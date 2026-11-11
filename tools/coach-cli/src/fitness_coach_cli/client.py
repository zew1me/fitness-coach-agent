"""HTTP client for one deployment: sign-in, transparent token refresh, and coach operations.

The CLI only ever talks to the deployment's own API. It never holds a Supabase URL or key.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from typing import Any

import httpx

from fitness_coach_cli.credentials import Credentials, CredentialStore
from fitness_coach_cli.errors import CoachError
from fitness_coach_cli.target import is_vercel_host

# Must match backend.services.auth.CLI_CLIENT_ID (a root test pins the two together).
CLI_CLIENT_ID = "fitness-coach-cli"
_OPERATION_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_BYPASS_HEADER = "x-vercel-protection-bypass"


def _user_agent() -> str:
    try:
        return f"fitness-coach-cli/{version('fitness-coach-cli')}"
    except PackageNotFoundError:
        return "fitness-coach-cli"


def _detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:500] or response.reason_phrase
    if isinstance(payload, dict):
        for key in ("detail", "message", "error"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        if "detail" in payload:
            return str(payload["detail"])
    return str(payload)[:500]


class CoachClient:
    def __init__(
        self,
        base_url: str,
        store: CredentialStore,
        *,
        timeout: float = 120.0,
        bypass_secret: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url
        self._store = store
        headers = {"User-Agent": _user_agent()}
        # Deployment Protection bypass for Vercel previews; never sent to any other host.
        if bypass_secret and is_vercel_host(base_url):
            headers[_BYPASS_HEADER] = bypass_secret
        self._http = httpx.Client(
            base_url=base_url, timeout=timeout, headers=headers, transport=transport
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> CoachClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ── unauthenticated ──────────────────────────────────────────

    def environment(self) -> str | None:
        """Return the deployment's APP_ENV from /health, or None if it cannot be determined."""
        try:
            response = self._http.get("/health")
        except httpx.HTTPError:
            return None
        if not response.is_success:
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        environment = payload.get("environment") if isinstance(payload, dict) else None
        return environment if isinstance(environment, str) else None

    def request_code(self, email: str, invite_code: str | None = None) -> None:
        body: dict[str, str] = {"email": email}
        if invite_code:
            body["inviteCode"] = invite_code
        response = self._send("POST", "/api/auth/request-otp", json=body)
        if response.status_code == httpx.codes.CONFLICT:
            raise CoachError(
                "invite_required",
                "This email has no account yet; an invite code is required.",
                status=response.status_code,
                hint="Re-run with --invite-code <code>.",
            )
        self._raise_for_status(response)

    def verify_code(self, email: str, code: str) -> Credentials:
        response = self._send(
            "POST", "/api/auth/cli/verify-otp", json={"email": email, "token": code}
        )
        self._raise_for_status(response)
        credentials = self._credentials_from(response, user_id=None)
        with self._store.locked():
            self._store.save(self.base_url, credentials)
        return credentials

    # ── authenticated ────────────────────────────────────────────

    def whoami(self) -> dict[str, Any]:
        return self._json(self._authorized("GET", "/api/auth/whoami"))

    def call(self, operation: str, payload: dict[str, Any]) -> Any:
        if not _OPERATION_RE.fullmatch(operation):
            raise CoachError(
                "invalid_operation",
                f"{operation!r} is not an operation name (lowercase words joined by '-').",
            )
        return self._json(self._authorized("POST", f"/api/engine/{operation}", json=payload))

    def logout(self) -> str:
        """Revoke this login's grant server-side, then always forget it locally.

        Revoking only the refresh token would leave the current access token usable until
        it expires, so the grant is revoked through a (refreshed if necessary) access token.
        Returns ``"revoked"``, ``"already_invalid"`` or ``"not_logged_in"``.
        """
        credentials = self._store.load(self.base_url)
        if credentials is None:
            return "not_logged_in"
        try:
            if not credentials.is_fresh():
                credentials = self._refresh(credentials)
            self._revoke(credentials.access_token, hint=None)
            self._revoke(credentials.refresh_token, hint="refresh_token")
        except CoachError as exc:
            if exc.error != "session_expired":
                exc.hint = (
                    "Local credentials were removed, but the server was not told. Its tokens "
                    "stay valid until they expire (access 15 minutes, refresh 30 days); no "
                    "copy of either is kept on this machine."
                )
                raise
            return "already_invalid"
        finally:
            with self._store.locked():
                self._store.delete(self.base_url)
        return "revoked"

    # ── internals ────────────────────────────────────────────────

    def _authorized(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        credentials = self._current_credentials()
        if not credentials.is_fresh():
            credentials = self._refresh(credentials)
        response = self._send(method, path, credentials=credentials, **kwargs)
        if response.status_code == httpx.codes.UNAUTHORIZED:
            # The access token may have been rejected early (clock skew, key rotation);
            # one refresh distinguishes that from a revoked grant, which refresh rejects.
            credentials = self._refresh(credentials)
            response = self._send(method, path, credentials=credentials, **kwargs)
            if response.status_code == httpx.codes.UNAUTHORIZED:
                raise self._session_expired(response.status_code)
        self._raise_for_status(response)
        return response

    def _current_credentials(self) -> Credentials:
        credentials = self._store.load(self.base_url)
        if credentials is None:
            raise CoachError(
                "not_logged_in",
                f"Not signed in to {self.base_url}.",
                hint=f"Run `coach --base-url {self.base_url} login --email <you>`.",
            )
        return credentials

    def _refresh(self, stale: Credentials) -> Credentials:
        with self._store.locked():
            current = self._store.load(self.base_url)
            if current is None:
                raise self._session_expired(None)
            if current.refresh_token != stale.refresh_token and current.is_fresh():
                return current  # another coach process refreshed while we waited
            response = self._send(
                "POST",
                "/api/oauth/token",
                json={
                    "grant_type": "refresh_token",
                    "client_id": CLI_CLIENT_ID,
                    "refresh_token": current.refresh_token,
                },
            )
            if response.status_code in {httpx.codes.BAD_REQUEST, httpx.codes.UNAUTHORIZED}:
                raise self._session_expired(response.status_code)
            self._raise_for_status(response)
            refreshed = self._credentials_from(response, user_id=current.user_id)
            self._store.save(self.base_url, refreshed)
            return refreshed

    def _revoke(self, token: str, *, hint: str | None) -> None:
        body: dict[str, str] = {"token": token, "client_id": CLI_CLIENT_ID}
        if hint is not None:
            body["token_type_hint"] = hint
        self._raise_for_status(self._send("POST", "/api/oauth/revoke", json=body))

    def _session_expired(self, status: int | None) -> CoachError:
        return CoachError(
            "session_expired",
            f"The sign-in for {self.base_url} is no longer valid (expired or revoked).",
            status=status,
            hint=f"Run `coach --base-url {self.base_url} login --email <you>` again.",
        )

    def _send(
        self,
        method: str,
        path: str,
        *,
        credentials: Credentials | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        headers = {"Accept": "application/json"}
        if credentials is not None:
            headers["Authorization"] = f"Bearer {credentials.access_token}"
        try:
            return self._http.request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise CoachError(
                "network_error", f"Could not reach {self.base_url}: {type(exc).__name__}: {exc}"
            ) from exc

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.is_success:
            return
        status = response.status_code
        if status == httpx.codes.TOO_MANY_REQUESTS:
            retry_after = response.headers.get("retry-after")
            raise CoachError(
                "rate_limited",
                _detail(response),
                status=status,
                hint=f"Retry after {retry_after} seconds." if retry_after else None,
            )
        raise CoachError("http_error", _detail(response), status=status)

    @staticmethod
    def _json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise CoachError(
                "invalid_response",
                "The deployment returned a non-JSON response.",
                status=response.status_code,
            ) from exc

    def _credentials_from(self, response: httpx.Response, *, user_id: str | None) -> Credentials:
        payload = self._json(response)
        try:
            expires_at = datetime.fromisoformat(str(payload["expires_at"]))
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            return Credentials(
                access_token=str(payload["access_token"]),
                refresh_token=str(payload["refresh_token"]),
                expires_at=expires_at,
                user_id=str(payload.get("user_id") or user_id or ""),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CoachError(
                "invalid_response", "The deployment returned an incomplete token response."
            ) from exc
