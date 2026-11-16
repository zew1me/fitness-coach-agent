from __future__ import annotations

import base64
import hashlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar, NoReturn
from urllib.parse import urlencode, urlparse

import httpx
import jwt

from backend.config import settings
from backend.models.auth import (
    ACCESS_TOKEN_TTL,
    DEVICE_USER_CODE_ALPHABET,
    DEVICE_USER_CODE_LENGTH,
    BrowserSessionContext,
    BrowserTokenResponse,
    CliTokenResponse,
    DeviceAuthorizationRequest,
    DeviceAuthorizationResponse,
    OAuthAuthorizeRequest,
    OAuthDeviceCodeRecord,
    OAuthGrantRecord,
    OAuthRevokeRequest,
    OAuthTokenRequest,
    TokenBundle,
    UserContext,
)
from backend.repos.oauth_repo import OAuthRepository, OAuthRepositoryNotConfiguredError

logger = logging.getLogger(__name__)

# First-party CLI client. Its grant row needs a redirect URI, but the CLI never takes part in
# a redirect flow: tokens come from server-side OTP verification. The URN is not routable, and
# parse_authorize_request reserves both values so the public authorize flow cannot mint codes
# or tokens that impersonate the CLI.
CLI_CLIENT_ID = "fitness-coach-cli"
CLI_REDIRECT_URI = "urn:fitness-coach:cli"

# RFC 8628 device authorization grant. Only first-party clients may start it: the athlete
# approves on this app's own /device page, so there is no third-party consent screen.
DEVICE_CODE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
DEVICE_FLOW_CLIENT_IDS = frozenset({CLI_CLIENT_ID})
DEVICE_CODE_EXPIRES_IN_SECONDS = 600
DEVICE_CODE_INTERVAL_SECONDS = 5
# RFC 8628 §3.5: every ``slow_down`` adds 5 seconds to the polling interval.
DEVICE_CODE_SLOW_DOWN_SECONDS = 5
# Compare-and-set retries for a poll stamp before conceding to concurrent polls.
DEVICE_POLL_STAMP_ATTEMPTS = 3


class OAuthError(ValueError):
    """Base class for OAuth validation errors."""


class OAuthLoginRequiredError(OAuthError):
    """Raised when the browser must log in before continuing."""


class OAuthConsentRequiredError(OAuthError):
    """Raised when the browser must review the consent screen."""


class OAuthInvalidGrantError(OAuthError):
    """Raised when a code or refresh token is invalid."""


class OAuthDeviceFlowError(OAuthError):
    """An RFC 8628 / RFC 6749 §5.2 error, reported as ``{"error", "error_description"}``.

    Messages never contain device codes, user codes or tokens, so they are safe to log.
    """

    def __init__(self, error: str, description: str) -> None:
        super().__init__(description)
        self.error = error
        self.description = description


class OAuthInvalidUserCodeError(OAuthError):
    """Raised when a user code is malformed, unknown, expired, or no longer pending."""


class OAuthDeviceConfirmationMismatchError(OAuthError):
    """Raised when an approval's displayed client or scopes differ from the stored request.

    The confirm page renders from query parameters, so a crafted link could describe fewer
    permissions than the pending request actually holds. Approval is bound to what was shown.
    """

    def __init__(self, record: OAuthDeviceCodeRecord) -> None:
        super().__init__("The confirmation does not match the pending request.")
        self.record = record


class AuthService:
    """Issue durable OAuth artifacts and verify browser-backed consent sessions."""

    _supported_scopes: ClassVar[set[str]] = {
        "profile:read",
        "profile:write",
        "plans:read",
        "plans:write",
        "metrics:write",
    }
    _browser_session_cookie_name: ClassVar[str] = "coach_browser_session"
    _browser_session_max_age_seconds: ClassVar[int] = 12 * 60 * 60

    def __init__(
        self,
        oauth_repo: OAuthRepository | None = None,
        *,
        http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self._oauth_repo = oauth_repo or OAuthRepository()
        self._http_client_factory = http_client_factory or (lambda: httpx.AsyncClient(timeout=10.0))

    async def aclose(self) -> None:
        """Release the OAuth repository's HTTP connections at application shutdown."""
        await self._oauth_repo.aclose()

    @property
    def browser_session_cookie_name(self) -> str:
        return self._browser_session_cookie_name

    @property
    def browser_session_max_age_seconds(self) -> int:
        return self._browser_session_max_age_seconds

    def authorization_metadata(self) -> dict[str, object]:
        issuer = settings.base_url
        return {
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/api/oauth/authorize",
            "token_endpoint": f"{issuer}/api/oauth/token",
            "registration_endpoint": f"{issuer}/api/oauth/register",
            "revocation_endpoint": f"{issuer}/api/oauth/revoke",
            "device_authorization_endpoint": f"{issuer}/api/oauth/device/code",
            "response_types_supported": ["code"],
            "grant_types_supported": [
                "authorization_code",
                "refresh_token",
                DEVICE_CODE_GRANT_TYPE,
            ],
            "code_challenge_methods_supported": ["S256"],
            "scopes_supported": sorted(self._supported_scopes),
        }

    def protected_resource_metadata(self) -> dict[str, object]:
        return {
            "resource": f"{settings.base_url}/api/mcp",
            "authorization_servers": [settings.base_url],
        }

    def parse_authorize_request(self, request: OAuthAuthorizeRequest) -> list[str]:
        if request.client_id == CLI_CLIENT_ID or request.redirect_uri == CLI_REDIRECT_URI:
            raise OAuthError("This client cannot use the authorization code flow.")
        if request.response_type != "code":
            raise OAuthError("Only authorization_code is supported.")
        if request.code_challenge is None or request.code_challenge_method is None:
            raise OAuthError("PKCE is required for OAuth authorization requests.")
        if request.code_challenge_method != "S256":
            raise OAuthError("Only S256 PKCE challenges are supported.")
        if not self._is_valid_redirect_uri(request.redirect_uri):
            raise OAuthError("The redirect URI is not allowed.")
        scopes = self._parse_scope_string(request.scope)
        if not scopes:
            raise OAuthError("At least one OAuth scope must be requested.")
        if not set(scopes).issubset(self._supported_scopes):
            raise OAuthError("One or more requested scopes are unsupported.")
        return scopes

    def build_login_redirect(self, authorize_url: str) -> str:
        return f"{settings.base_url}/login?{urlencode({'return_to': authorize_url})}"

    def build_consent_redirect(self, request: OAuthAuthorizeRequest) -> str:
        query = {
            "client_id": request.client_id,
            "redirect_uri": request.redirect_uri,
            "scope": request.scope,
            "state": request.state or "",
            "code_challenge": request.code_challenge or "",
            "code_challenge_method": request.code_challenge_method or "",
        }
        return f"{settings.base_url}/consent?{urlencode(query)}"

    async def build_authorize_redirect(
        self, *, request: OAuthAuthorizeRequest, browser_session: BrowserSessionContext
    ) -> str:
        scopes = self.parse_authorize_request(request)
        grant = await self._oauth_repo.get_active_grant(
            user_id=browser_session.user_id,
            client_id=request.client_id,
            redirect_uri=request.redirect_uri,
        )
        if (
            request.prompt == "consent"
            or grant is None
            or not set(scopes).issubset(set(grant.scopes))
        ):
            raise OAuthConsentRequiredError("Browser consent is required.")
        code = await self._oauth_repo.create_authorization_code(
            grant_id=grant.id,
            user_id=browser_session.user_id,
            client_id=request.client_id,
            redirect_uri=request.redirect_uri,
            scopes=scopes,
            code_challenge=request.code_challenge or "",
            code_challenge_method=request.code_challenge_method or "",
        )
        return self._append_redirect_query(
            request.redirect_uri, {"code": code, "state": request.state or ""}
        )

    async def approve_consent(
        self, *, request: OAuthAuthorizeRequest, browser_session: BrowserSessionContext
    ) -> str:
        scopes = self.parse_scope_string(request.scope)
        self.parse_authorize_request(request)
        grant = await self._oauth_repo.upsert_grant(
            user_id=browser_session.user_id,
            client_id=request.client_id,
            redirect_uri=request.redirect_uri,
            scopes=scopes,
        )
        code = await self._oauth_repo.create_authorization_code(
            grant_id=grant.id,
            user_id=browser_session.user_id,
            client_id=request.client_id,
            redirect_uri=request.redirect_uri,
            scopes=scopes,
            code_challenge=request.code_challenge or "",
            code_challenge_method=request.code_challenge_method or "",
        )
        logger.info(
            "oauth consent approved user_id=%s client_id=%s scopes=%s",
            browser_session.user_id,
            request.client_id,
            scopes,
        )
        return self._append_redirect_query(
            request.redirect_uri, {"code": code, "state": request.state or ""}
        )

    async def exchange_token_request(self, request: OAuthTokenRequest) -> TokenBundle:
        if request.grant_type == "authorization_code":
            return await self.exchange_code(request)
        if request.grant_type == "refresh_token":
            return await self.refresh_access_token(request)
        if request.grant_type == DEVICE_CODE_GRANT_TYPE:
            return await self.exchange_device_code(request)
        raise OAuthError("Unsupported grant type.")

    async def exchange_code(self, request: OAuthTokenRequest) -> TokenBundle:
        if request.code is None or request.redirect_uri is None:
            raise OAuthInvalidGrantError(
                "Authorization code requests require code and redirect_uri."
            )
        if request.code_verifier is None:
            raise OAuthInvalidGrantError("PKCE code_verifier is required.")
        code_record = await self._oauth_repo.get_authorization_code(request.code)
        if code_record is None:
            raise OAuthInvalidGrantError("Invalid authorization code.")
        if (
            code_record.client_id != request.client_id
            or code_record.redirect_uri != request.redirect_uri
        ):
            raise OAuthInvalidGrantError("Authorization code client or redirect mismatch.")
        if (
            code_record.code_challenge is None
            or code_record.code_challenge_method is None
            or code_record.code_challenge == ""
            or code_record.code_challenge_method == ""
        ):
            raise OAuthInvalidGrantError("Authorization code is missing PKCE challenge data.")
        self._verify_code_challenge(
            verifier=request.code_verifier,
            expected_challenge=code_record.code_challenge,
            method=code_record.code_challenge_method,
        )
        try:
            code_record = await self._oauth_repo.consume_authorization_code(request.code)
        except ValueError as exc:
            raise OAuthInvalidGrantError(str(exc)) from exc
        grant = await self._oauth_repo.get_grant_by_id(code_record.grant_id)
        if grant is None or grant.revoked_at is not None:
            raise OAuthInvalidGrantError("The associated OAuth grant is no longer active.")
        bundle = await self._issue_token_bundle(grant=grant, scopes=code_record.scopes)
        logger.info(
            "oauth code exchanged user_id=%s client_id=%s scopes=%s",
            grant.user_id,
            grant.client_id,
            code_record.scopes,
        )
        return bundle

    async def refresh_access_token(self, request: OAuthTokenRequest) -> TokenBundle:
        if request.refresh_token is None:
            raise OAuthInvalidGrantError("Refresh requests require a refresh_token.")
        refresh_record = await self._oauth_repo.get_refresh_token(request.refresh_token)
        if refresh_record is None:
            raise OAuthInvalidGrantError("Invalid refresh token.")
        if refresh_record.client_id != request.client_id:
            raise OAuthInvalidGrantError("Refresh token client mismatch.")
        try:
            refresh_record, replacement = await self._oauth_repo.rotate_refresh_token(
                request.refresh_token
            )
        except ValueError as exc:
            raise OAuthInvalidGrantError(str(exc)) from exc
        grant = await self._oauth_repo.get_grant_by_id(refresh_record.grant_id)
        if grant is None or grant.revoked_at is not None:
            raise OAuthInvalidGrantError("The associated OAuth grant is no longer active.")
        logger.info(
            "oauth token refreshed user_id=%s client_id=%s",
            refresh_record.user_id,
            refresh_record.client_id,
        )
        return self._build_token_bundle(
            user_id=refresh_record.user_id,
            client_id=refresh_record.client_id,
            scopes=refresh_record.scopes,
            grant_id=refresh_record.grant_id,
            refresh_token=replacement,
        )

    async def revoke(self, request: OAuthRevokeRequest) -> bool:
        if request.token_type_hint == "refresh_token":
            revoked = await self._oauth_repo.revoke_refresh_token(request.token)
            logger.info("oauth refresh_token revoked=%s", revoked)
            return revoked
        grant_id = self._decode_access_token_grant_id(request.token)
        if grant_id is not None:
            revoked = await self._oauth_repo.revoke_grant(grant_id)
            logger.info("oauth grant revoked grant_id=%s revoked=%s", grant_id, revoked)
            return revoked
        return await self._oauth_repo.revoke_refresh_token(request.token)

    # ── RFC 8628 device authorization grant ───────────────────

    async def start_device_authorization(
        self, request: DeviceAuthorizationRequest
    ) -> DeviceAuthorizationResponse:
        """Create a pending device authorization for a first-party client (RFC 8628 §3.1)."""
        if request.client_id not in DEVICE_FLOW_CLIENT_IDS:
            raise OAuthDeviceFlowError(
                "invalid_client", "This client may not use the device authorization grant."
            )
        scopes = self._parse_scope_string(request.scope or "")
        if not scopes:
            scopes = sorted(self._supported_scopes)
        elif not set(scopes).issubset(self._supported_scopes):
            raise OAuthDeviceFlowError(
                "invalid_scope", "One or more requested scopes are unsupported."
            )
        else:
            scopes = sorted(set(scopes))
        expires_at = datetime.now(UTC) + timedelta(seconds=DEVICE_CODE_EXPIRES_IN_SECONDS)
        device_code, record = await self._oauth_repo.create_device_code(
            client_id=request.client_id,
            scopes=scopes,
            expires_at=expires_at,
            interval_seconds=DEVICE_CODE_INTERVAL_SECONDS,
        )
        logger.info(
            "oauth device authorization started client_id=%s scopes=%s",
            request.client_id,
            scopes,
        )
        base = settings.base_url
        return DeviceAuthorizationResponse(
            device_code=device_code,
            user_code=self.format_user_code(record.user_code),
            verification_uri=f"{base}/device",
            verification_uri_complete=(
                f"{base}/api/oauth/device?{urlencode({'user_code': record.user_code})}"
            ),
            expires_in=DEVICE_CODE_EXPIRES_IN_SECONDS,
            interval=DEVICE_CODE_INTERVAL_SECONDS,
        )

    @staticmethod
    def build_device_page_redirect(params: dict[str, str] | None = None) -> str:
        """URL of the /device page, carrying the query-param state it renders."""
        query = f"?{urlencode(params)}" if params else ""
        return f"{settings.base_url}/device{query}"

    def build_device_login_redirect(self, user_code: str) -> str:
        """Send the browser to log in, then back to the device entry point for ``user_code``."""
        return self.build_login_redirect("/api/oauth/device?" + urlencode({"user_code": user_code}))

    @staticmethod
    def normalize_user_code(raw: str | None) -> str | None:
        """Uppercase and drop whitespace and dashes; ``None`` unless a well-formed code remains.

        Only well-formed codes reach the database, and a malformed one is reported exactly
        like an unknown one.
        """
        if raw is None:
            return None
        normalized = "".join(ch for ch in raw.upper() if ch != "-" and not ch.isspace())
        if len(normalized) != DEVICE_USER_CODE_LENGTH or any(
            ch not in DEVICE_USER_CODE_ALPHABET for ch in normalized
        ):
            return None
        return normalized

    @staticmethod
    def format_user_code(user_code: str) -> str:
        """Render a normalised user code for display as ``XXXX-XXXX``."""
        half = DEVICE_USER_CODE_LENGTH // 2
        return f"{user_code[:half]}-{user_code[half:]}"

    async def get_pending_device_code(self, user_code: str) -> OAuthDeviceCodeRecord:
        """Return the live pending authorization for a normalised user code.

        Unknown, expired and already-decided codes all raise the same error.
        """
        record = await self._oauth_repo.find_pending_device_code(user_code)
        if record is None or record.expires_at <= datetime.now(UTC):
            raise OAuthInvalidUserCodeError("The user code is invalid or has expired.")
        return record

    async def approve_device_code(
        self,
        *,
        user_code: str,
        browser_session: BrowserSessionContext,
        shown_client_id: str,
        shown_scope: str,
    ) -> None:
        """Approve a pending device authorization on the athlete's durable CLI grant.

        ``shown_client_id`` and ``shown_scope`` are what the confirm page displayed; approval
        only proceeds when they match the stored request exactly.
        """
        record = await self.get_pending_device_code(user_code)
        shown_scopes = sorted(set(self._parse_scope_string(shown_scope)))
        if shown_client_id != record.client_id or shown_scopes != sorted(record.scopes):
            logger.warning(
                "oauth device approval mismatch user_id=%s client_id=%s",
                browser_session.user_id,
                record.client_id,
            )
            raise OAuthDeviceConfirmationMismatchError(record)
        grant = await self._oauth_repo.upsert_grant(
            user_id=browser_session.user_id,
            client_id=CLI_CLIENT_ID,
            redirect_uri=CLI_REDIRECT_URI,
            scopes=record.scopes,
        )
        approved = await self._oauth_repo.approve_device_code(
            record.id, user_id=browser_session.user_id, grant_id=grant.id
        )
        if approved is None:
            raise OAuthInvalidUserCodeError("The user code is invalid or has expired.")
        logger.info(
            "oauth device authorization approved user_id=%s client_id=%s",
            browser_session.user_id,
            record.client_id,
        )

    async def deny_device_code(
        self, *, user_code: str, browser_session: BrowserSessionContext
    ) -> None:
        """Deny a pending device authorization so the polling client stops."""
        record = await self.get_pending_device_code(user_code)
        if not await self._oauth_repo.deny_device_code(record.id):
            raise OAuthInvalidUserCodeError("The user code is invalid or has expired.")
        logger.info(
            "oauth device authorization denied user_id=%s client_id=%s",
            browser_session.user_id,
            record.client_id,
        )

    async def exchange_device_code(self, request: OAuthTokenRequest) -> CliTokenResponse:
        """Answer a device-code token poll (RFC 8628 §3.4/§3.5).

        Unknown, expired, consumed and wrong-client codes all yield ``expired_token`` so a
        caller cannot tell whether a device code ever existed.
        """
        if not request.device_code:
            raise OAuthDeviceFlowError("invalid_request", "device_code is required.")
        record = await self._oauth_repo.get_device_code(request.device_code)
        now = datetime.now(UTC)
        if (
            record is None
            or record.client_id != request.client_id
            or record.expires_at <= now
            or record.status == "consumed"
        ):
            raise self._expired_device_code_error()
        if record.status == "denied":
            raise OAuthDeviceFlowError("access_denied", "The athlete denied the request.")
        if record.status == "pending":
            await self._reject_pending_device_poll(record, device_code=request.device_code, now=now)
        consumed = await self._oauth_repo.consume_device_code(record.id)
        if consumed is None or consumed.grant_id is None:
            raise self._expired_device_code_error()
        grant = await self._oauth_repo.get_grant_by_id(consumed.grant_id)
        if grant is None or grant.revoked_at is not None:
            raise self._expired_device_code_error()
        bundle = await self._issue_token_bundle(grant=grant, scopes=consumed.scopes)
        logger.info(
            "oauth device code exchanged user_id=%s client_id=%s", grant.user_id, grant.client_id
        )
        return CliTokenResponse(**bundle.model_dump(), user_id=grant.user_id)

    async def _reject_pending_device_poll(
        self, record: OAuthDeviceCodeRecord, *, device_code: str, now: datetime
    ) -> NoReturn:
        """Record a poll on a pending code and raise ``authorization_pending`` or ``slow_down``.

        The stamp is a compare-and-set on the ``last_polled_at`` this poll read. When a
        concurrent poll wins, re-read and retry against the fresh row: measured against the
        stamp that just landed, this poll is too fast, so the retry raises the stored interval
        rather than leaving the backoff unchanged (RFC 8628 §3.5).
        """
        for _ in range(DEVICE_POLL_STAMP_ATTEMPTS):
            too_fast, interval = self._device_poll_backoff(record, now=now)
            if await self._oauth_repo.record_device_code_poll(
                record.id,
                observed_polled_at=record.last_polled_at,
                polled_at=now,
                interval_seconds=interval,
            ):
                if too_fast:
                    raise OAuthDeviceFlowError(
                        "slow_down",
                        f"Polling too fast; wait at least {interval} seconds between polls.",
                    )
                raise OAuthDeviceFlowError(
                    "authorization_pending", "The athlete has not approved the request yet."
                )
            fresh = await self._oauth_repo.get_device_code(device_code)
            if fresh is None or fresh.status != "pending":
                break  # decided or gone meanwhile; the next poll sees the new state
            record = fresh
        raise OAuthDeviceFlowError("slow_down", "Polling too fast; wait before polling again.")

    @staticmethod
    def _device_poll_backoff(record: OAuthDeviceCodeRecord, *, now: datetime) -> tuple[bool, int]:
        """Whether this poll is too fast for ``record``, and the interval to store."""
        too_fast = record.last_polled_at is not None and now - record.last_polled_at < timedelta(
            seconds=record.interval_seconds
        )
        return too_fast, record.interval_seconds + (
            DEVICE_CODE_SLOW_DOWN_SECONDS if too_fast else 0
        )

    @staticmethod
    def _expired_device_code_error() -> OAuthDeviceFlowError:
        return OAuthDeviceFlowError(
            "expired_token", "The device code is invalid or has expired. Start a new login."
        )

    async def create_browser_session(self, supabase_access_token: str) -> BrowserSessionContext:
        user = await self._fetch_supabase_user(supabase_access_token)
        user_id = str(user.get("id", ""))
        if not user_id:
            raise OAuthLoginRequiredError("Supabase did not return a user id.")
        email = user.get("email")
        return BrowserSessionContext(
            user_id=user_id,
            email=str(email) if isinstance(email, str) else None,
        )

    def create_browser_session_token(self, session: BrowserSessionContext) -> str:
        return jwt.encode(
            {
                "sub": session.user_id,
                "email": session.email,
                "typ": "browser_session",
                "iat": datetime.now(UTC),
                "exp": datetime.now(UTC) + timedelta(seconds=self.browser_session_max_age_seconds),
            },
            settings.app_jwt_secret,
            algorithm="HS256",
        )

    async def create_browser_token(self, session: BrowserSessionContext) -> BrowserTokenResponse:
        scopes = sorted(self._supported_scopes)
        grant = await self._oauth_repo.upsert_grant(
            user_id=session.user_id,
            client_id=settings.base_url,
            redirect_uri=settings.base_url,
            scopes=scopes,
        )
        return BrowserTokenResponse(
            access_token=self._issue_access_token(
                user_id=session.user_id,
                client_id=settings.base_url,
                scopes=scopes,
                grant_id=grant.id,
            ),
            scopes=scopes,
            user_id=session.user_id,
        )

    def get_browser_session_from_cookie(self, token: str | None) -> BrowserSessionContext:
        if token is None:
            raise OAuthLoginRequiredError("No browser session cookie is present.")
        try:
            claims = jwt.decode(token, settings.app_jwt_secret, algorithms=["HS256"])
        except jwt.PyJWTError as exc:
            raise OAuthLoginRequiredError("Invalid browser session cookie.") from exc
        if claims.get("typ") != "browser_session":
            raise OAuthLoginRequiredError("Invalid browser session cookie.")
        return BrowserSessionContext(
            user_id=str(claims["sub"]),
            email=str(claims.get("email", "")) or None,
        )

    async def get_user_context_from_bearer(self, token: str) -> UserContext:
        claims = jwt.decode(
            token,
            settings.app_jwt_secret,
            algorithms=["HS256"],
            audience=f"{settings.base_url}/api/mcp",
        )
        grant_id = str(claims.get("jti", "")) or None
        if grant_id is None:
            raise OAuthInvalidGrantError("Access token is missing a grant id.")
        grant = await self._oauth_repo.get_grant_by_id(grant_id)
        if grant is None or grant.revoked_at is not None:
            logger.warning("bearer auth: grant revoked or missing grant_id=%s", grant_id)
            raise OAuthInvalidGrantError("The OAuth grant for this token has been revoked.")
        return UserContext(
            user_id=str(claims["sub"]),
            scopes=list(claims.get("scope", "").split()),
            client_id=str(claims.get("azp", "")) or None,
            grant_id=grant_id,
        )

    def parse_scope_string(self, scope: str) -> list[str]:
        return self._parse_scope_string(scope)

    def _decode_access_token_grant_id(self, token: str) -> str | None:
        try:
            claims = jwt.decode(
                token,
                settings.app_jwt_secret,
                algorithms=["HS256"],
                audience=f"{settings.base_url}/api/mcp",
            )
        except jwt.PyJWTError:
            return None
        return str(claims.get("jti", "")) or None

    @staticmethod
    def _require_supabase_auth_config() -> tuple[str, str]:
        if not settings.supabase_url or not settings.supabase_service_role_key:
            raise OAuthRepositoryNotConfiguredError(
                "Supabase is not configured. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY."
            )
        return settings.supabase_url.rstrip("/"), settings.supabase_service_role_key

    async def _fetch_supabase_user(self, access_token: str) -> dict[str, Any]:
        supabase_url, service_role_key = self._require_supabase_auth_config()
        url = f"{supabase_url}/auth/v1/user"
        logger.debug("supabase user fetch start")
        async with self._http_client_factory() as client:
            response = await client.get(
                url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "apikey": service_role_key,
                },
            )
        logger.debug("supabase user fetch status=%d", response.status_code)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise OAuthLoginRequiredError("Supabase returned an unexpected user payload.")
        return payload

    async def _issue_token_bundle(
        self, *, grant: OAuthGrantRecord, scopes: list[str]
    ) -> TokenBundle:
        """Mint a fresh refresh token on ``grant`` and pair it with an access token."""
        refresh_token = await self._oauth_repo.create_refresh_token(
            grant_id=grant.id,
            user_id=grant.user_id,
            client_id=grant.client_id,
            scopes=scopes,
        )
        return self._build_token_bundle(
            user_id=grant.user_id,
            client_id=grant.client_id,
            scopes=scopes,
            grant_id=grant.id,
            refresh_token=refresh_token,
        )

    def _build_token_bundle(
        self, *, user_id: str, client_id: str, scopes: list[str], grant_id: str, refresh_token: str
    ) -> TokenBundle:
        issued_at = datetime.now(UTC)
        return TokenBundle(
            access_token=self._issue_access_token(
                user_id=user_id,
                client_id=client_id,
                scopes=scopes,
                grant_id=grant_id,
                issued_at=issued_at,
            ),
            expires_at=issued_at + ACCESS_TOKEN_TTL,
            expires_in=int(ACCESS_TOKEN_TTL.total_seconds()),
            refresh_token=refresh_token,
            scope=" ".join(scopes),
        )

    def _issue_access_token(
        self,
        *,
        user_id: str,
        client_id: str,
        scopes: list[str],
        grant_id: str,
        issued_at: datetime | None = None,
    ) -> str:
        issued_at = issued_at or datetime.now(UTC)
        return jwt.encode(
            {
                "sub": user_id,
                "aud": f"{settings.base_url}/api/mcp",
                "azp": client_id,
                "scope": " ".join(scopes),
                "exp": issued_at + ACCESS_TOKEN_TTL,
                "iat": issued_at,
                "jti": grant_id,
            },
            settings.app_jwt_secret,
            algorithm="HS256",
        )

    def _is_valid_redirect_uri(self, redirect_uri: str) -> bool:
        parsed = urlparse(redirect_uri)
        if parsed.scheme == "https" and parsed.netloc:
            return True
        return parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}

    def _verify_code_challenge(
        self, *, verifier: str, expected_challenge: str, method: str
    ) -> None:
        if method != "S256":
            raise OAuthInvalidGrantError("Only S256 PKCE verification is supported.")
        digest = hashlib.sha256(verifier.encode("utf-8")).digest()
        challenge = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
        if challenge != expected_challenge:
            raise OAuthInvalidGrantError("The PKCE code_verifier did not match the challenge.")

    @staticmethod
    def _append_redirect_query(redirect_uri: str, params: dict[str, str]) -> str:
        query = urlencode({key: value for key, value in params.items() if value})
        separator = "&" if urlparse(redirect_uri).query else "?"
        if not query:
            return redirect_uri
        return f"{redirect_uri}{separator}{query}"

    @staticmethod
    def _parse_scope_string(scope: str) -> list[str]:
        return [entry for entry in scope.split() if entry]
