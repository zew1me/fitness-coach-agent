from __future__ import annotations

import base64
import hashlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar
from urllib.parse import urlencode, urlparse

import httpx
import jwt

from backend.config import settings
from backend.models.auth import (
    BrowserSessionContext,
    BrowserTokenResponse,
    OAuthAuthorizeRequest,
    OAuthRevokeRequest,
    OAuthTokenRequest,
    TokenBundle,
    UserContext,
)
from backend.repos.oauth_repo import OAuthRepository, OAuthRepositoryNotConfiguredError

logger = logging.getLogger(__name__)


class OAuthError(ValueError):
    """Base class for OAuth validation errors."""


class OAuthLoginRequiredError(OAuthError):
    """Raised when the browser must log in before continuing."""


class OAuthConsentRequiredError(OAuthError):
    """Raised when the browser must review the consent screen."""


class OAuthInvalidGrantError(OAuthError):
    """Raised when a code or refresh token is invalid."""


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

    def __init__(self, oauth_repo: OAuthRepository | None = None) -> None:
        self._oauth_repo = oauth_repo or OAuthRepository()

    @property
    def browser_session_cookie_name(self) -> str:
        return self._browser_session_cookie_name

    def authorization_metadata(self) -> dict[str, object]:
        issuer = settings.base_url
        return {
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/api/oauth/authorize",
            "token_endpoint": f"{issuer}/api/oauth/token",
            "registration_endpoint": f"{issuer}/api/oauth/register",
            "revocation_endpoint": f"{issuer}/api/oauth/revoke",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "scopes_supported": sorted(self._supported_scopes),
        }

    def protected_resource_metadata(self) -> dict[str, object]:
        return {
            "resource": f"{settings.base_url}/api/mcp",
            "authorization_servers": [settings.base_url],
        }

    def parse_authorize_request(self, request: OAuthAuthorizeRequest) -> list[str]:
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

    def build_authorize_redirect(
        self, *, request: OAuthAuthorizeRequest, browser_session: BrowserSessionContext
    ) -> str:
        scopes = self.parse_authorize_request(request)
        grant = self._oauth_repo.get_active_grant(
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
        code = self._oauth_repo.create_authorization_code(
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

    def approve_consent(
        self, *, request: OAuthAuthorizeRequest, browser_session: BrowserSessionContext
    ) -> str:
        scopes = self.parse_scope_string(request.scope)
        self.parse_authorize_request(request)
        grant = self._oauth_repo.upsert_grant(
            user_id=browser_session.user_id,
            client_id=request.client_id,
            redirect_uri=request.redirect_uri,
            scopes=scopes,
        )
        code = self._oauth_repo.create_authorization_code(
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

    def exchange_token_request(self, request: OAuthTokenRequest) -> TokenBundle:
        if request.grant_type == "authorization_code":
            return self.exchange_code(request)
        if request.grant_type == "refresh_token":
            return self.refresh_access_token(request)
        raise OAuthError("Unsupported grant type.")

    def exchange_code(self, request: OAuthTokenRequest) -> TokenBundle:
        if request.code is None or request.redirect_uri is None:
            raise OAuthInvalidGrantError(
                "Authorization code requests require code and redirect_uri."
            )
        if request.code_verifier is None:
            raise OAuthInvalidGrantError("PKCE code_verifier is required.")
        code_record = self._oauth_repo.get_authorization_code(request.code)
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
            code_record = self._oauth_repo.consume_authorization_code(request.code)
        except ValueError as exc:
            raise OAuthInvalidGrantError(str(exc)) from exc
        grant = self._oauth_repo.get_grant_by_id(code_record.grant_id)
        if grant is None or grant.revoked_at is not None:
            raise OAuthInvalidGrantError("The associated OAuth grant is no longer active.")
        refresh_token = self._oauth_repo.create_refresh_token(
            grant_id=grant.id,
            user_id=grant.user_id,
            client_id=grant.client_id,
            scopes=code_record.scopes,
        )
        return TokenBundle(
            access_token=self._issue_access_token(
                user_id=grant.user_id,
                client_id=grant.client_id,
                scopes=code_record.scopes,
                grant_id=grant.id,
            ),
            refresh_token=refresh_token,
        )

    def refresh_access_token(self, request: OAuthTokenRequest) -> TokenBundle:
        if request.refresh_token is None:
            raise OAuthInvalidGrantError("Refresh requests require a refresh_token.")
        refresh_record = self._oauth_repo.get_refresh_token(request.refresh_token)
        if refresh_record is None:
            raise OAuthInvalidGrantError("Invalid refresh token.")
        if refresh_record.client_id != request.client_id:
            raise OAuthInvalidGrantError("Refresh token client mismatch.")
        try:
            refresh_record, replacement = self._oauth_repo.rotate_refresh_token(
                request.refresh_token
            )
        except ValueError as exc:
            raise OAuthInvalidGrantError(str(exc)) from exc
        grant = self._oauth_repo.get_grant_by_id(refresh_record.grant_id)
        if grant is None or grant.revoked_at is not None:
            raise OAuthInvalidGrantError("The associated OAuth grant is no longer active.")
        return TokenBundle(
            access_token=self._issue_access_token(
                user_id=refresh_record.user_id,
                client_id=refresh_record.client_id,
                scopes=refresh_record.scopes,
                grant_id=refresh_record.grant_id,
            ),
            refresh_token=replacement,
        )

    def revoke(self, request: OAuthRevokeRequest) -> bool:
        if request.token_type_hint == "refresh_token":
            revoked = self._oauth_repo.revoke_refresh_token(request.token)
            logger.info("oauth refresh_token revoked=%s", revoked)
            return revoked
        grant_id = self._decode_access_token_grant_id(request.token)
        if grant_id is not None:
            revoked = self._oauth_repo.revoke_grant(grant_id)
            logger.info("oauth grant revoked grant_id=%s revoked=%s", grant_id, revoked)
            return revoked
        return self._oauth_repo.revoke_refresh_token(request.token)

    def create_browser_session(self, supabase_access_token: str) -> BrowserSessionContext:
        user = self._fetch_supabase_user(supabase_access_token)
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
                "exp": datetime.now(UTC) + timedelta(hours=12),
            },
            settings.app_jwt_secret,
            algorithm="HS256",
        )

    def create_browser_token(self, session: BrowserSessionContext) -> BrowserTokenResponse:
        scopes = sorted(self._supported_scopes)
        grant = self._oauth_repo.upsert_grant(
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

    def get_user_context_from_bearer(self, token: str) -> UserContext:
        claims = jwt.decode(
            token,
            settings.app_jwt_secret,
            algorithms=["HS256"],
            audience=f"{settings.base_url}/api/mcp",
        )
        grant_id = str(claims.get("jti", "")) or None
        if grant_id is None:
            raise OAuthInvalidGrantError("Access token is missing a grant id.")
        grant = self._oauth_repo.get_grant_by_id(grant_id)
        if grant is None or grant.revoked_at is not None:
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

    def _fetch_supabase_user(self, access_token: str) -> dict[str, Any]:
        if not settings.supabase_url or not settings.supabase_service_role_key:
            raise OAuthRepositoryNotConfiguredError(
                "Supabase is not configured. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY."
            )
        url = f"{settings.supabase_url.rstrip('/')}/auth/v1/user"
        response = httpx.get(
            url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "apikey": settings.supabase_service_role_key,
            },
            timeout=10.0,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise OAuthLoginRequiredError("Supabase returned an unexpected user payload.")
        return payload

    def _issue_access_token(
        self, *, user_id: str, client_id: str, scopes: list[str], grant_id: str
    ) -> str:
        return jwt.encode(
            {
                "sub": user_id,
                "aud": f"{settings.base_url}/api/mcp",
                "azp": client_id,
                "scope": " ".join(scopes),
                "exp": datetime.now(UTC) + timedelta(minutes=15),
                "iat": datetime.now(UTC),
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
