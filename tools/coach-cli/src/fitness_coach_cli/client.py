"""HTTP client for the fitness-coach API. Talks only to the app base URL, never to Supabase/R2."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from fitness_coach_cli.credentials import Credentials, CredentialStore
from fitness_coach_cli.errors import CoachError
from fitness_coach_cli.tools import validate_operation

CLIENT_ID = "fitness-coach-cli"
DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
_SLOW_DOWN_STEP_SECONDS = 5.0
_DEFAULT_DEVICE_INTERVAL = 5.0
_HTTP_TOO_MANY_REQUESTS = 429
REFRESH_SKEW_SECONDS = 60
_DEFAULT_TTL_SECONDS = 900
_REQUEST_TIMEOUT = 30.0
_UPLOAD_TIMEOUT = 120.0
_HTTP_UNAUTHORIZED = 401
_HTTP_ERROR_FLOOR = 400
# The upload pipeline's allowlist (api/index.py _ALLOWED_UPLOAD_TYPES), keyed by extension.
ACTIVITY_CONTENT_TYPES = {
    ".fit": "application/vnd.garmin.fit",
    ".gpx": "application/gpx+xml",
    ".tcx": "application/vnd.garmin.tcx+xml",
}
# Presign `purpose` only shapes the object key prefix; reuse the value the web chat sends.
_UPLOAD_PURPOSE = "chat-attachment"


def _error_from_response(response: httpx.Response) -> CoachError:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    body = payload if isinstance(payload, dict) else {}
    code = body.get("error")
    detail = body.get("detail", body.get("message"))
    if detail is None:
        detail = response.text.strip()[:500] or response.reason_phrase
    error = code if isinstance(code, str) else f"http_{response.status_code}"
    return CoachError(error, detail, status=response.status_code)


def _expiry(bundle: dict[str, Any], now: float) -> float:
    expires_in = bundle.get("expires_in")
    if isinstance(expires_in, int | float) and not isinstance(expires_in, bool):
        return now + float(expires_in)
    expires_at = bundle.get("expires_at")
    if isinstance(expires_at, str):
        try:
            return datetime.fromisoformat(expires_at).timestamp()
        except ValueError:
            pass
    return now + _DEFAULT_TTL_SECONDS


def credentials_from_bundle(
    bundle: dict[str, Any], *, now: float, previous: Credentials | None = None
) -> Credentials:
    access = bundle.get("access_token")
    refresh = bundle.get("refresh_token") or (previous.refresh_token if previous else None)
    user_id = bundle.get("user_id") or (previous.user_id if previous else None)
    if not (isinstance(access, str) and isinstance(refresh, str) and isinstance(user_id, str)):
        raise CoachError("invalid_token_response", "Server returned an incomplete token bundle.")
    return Credentials(access, refresh, _expiry(bundle, now), user_id)


class CoachClient:
    def __init__(
        self,
        base_url: str,
        store: CredentialStore,
        *,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.base_url = base_url
        self.store = store
        self._clock = clock
        self._http = httpx.Client(base_url=base_url, transport=transport, timeout=_REQUEST_TIMEOUT)

    def close(self) -> None:
        self._http.close()

    # -- plumbing ---------------------------------------------------------------------------

    def _send(
        self, method: str, path: str, *, token: str | None = None, **kwargs: Any
    ) -> httpx.Response:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            return self._http.request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise CoachError("network_error", f"{type(exc).__name__}: {exc}") from exc

    def _json(self, response: httpx.Response) -> Any:
        if response.status_code >= _HTTP_ERROR_FLOOR:
            raise _error_from_response(response)
        try:
            return response.json()
        except ValueError as exc:
            raise CoachError("invalid_response", "Server did not return JSON.") from exc

    def _login_required(self) -> CoachError:
        return CoachError(
            "not_logged_in",
            f"Run `coach --base-url {self.base_url} login` first.",
            status=_HTTP_UNAUTHORIZED,
        )

    def _is_fresh(self, creds: Credentials) -> bool:
        return creds.expires_at - self._clock() > REFRESH_SKEW_SECONDS

    def _refresh(self, current: Credentials) -> Credentials:
        # Refresh tokens rotate, so concurrent CLI processes must not both present the same one.
        with self.store.refresh_lock():
            stored = self.store.load(self.base_url)
            if stored is not None and stored != current and self._is_fresh(stored):
                return stored  # another process already refreshed while we waited
            return self._refresh_locked(stored or current)

    def _refresh_locked(self, current: Credentials) -> Credentials:
        response = self._send(
            "POST",
            "/api/oauth/token",
            json={
                "client_id": CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": current.refresh_token,
            },
        )
        if response.status_code in {400, _HTTP_UNAUTHORIZED}:
            # Lock-less platforms (or a writer outside the lock) may have rotated the token
            # under us; valid, different stored credentials mean the session is still alive.
            reloaded = self.store.load(self.base_url)
            if (
                reloaded is not None
                and reloaded.refresh_token != current.refresh_token
                and self._is_fresh(reloaded)
            ):
                return reloaded
            raise CoachError(
                "session_expired",
                "Session can no longer be refreshed; run "
                f"`coach --base-url {self.base_url} login`.",
                status=response.status_code,
            )
        bundle = self._json(response)
        rotated = credentials_from_bundle(bundle, now=self._clock(), previous=current)
        self.store.save(self.base_url, rotated)
        return rotated

    def _authed(self, method: str, path: str, **kwargs: Any) -> Any:
        creds = self.store.load(self.base_url)
        if creds is None:
            raise self._login_required()
        if creds.expires_at - self._clock() <= REFRESH_SKEW_SECONDS:
            creds = self._refresh(creds)
        response = self._send(method, path, token=creds.access_token, **kwargs)
        if response.status_code == _HTTP_UNAUTHORIZED:
            creds = self._refresh(creds)
            response = self._send(method, path, token=creds.access_token, **kwargs)
        return self._json(response)

    # -- login / logout ---------------------------------------------------------------------

    def request_otp(self, email: str, invite_code: str | None = None) -> None:
        body: dict[str, str] = {"email": email}
        if invite_code:
            body["inviteCode"] = invite_code
        self._json(self._send("POST", "/api/auth/request-otp", json=body))

    def verify_otp(self, email: str, code: str) -> Credentials:
        response = self._send(
            "POST", "/api/auth/cli/verify-otp", json={"email": email, "token": code}
        )
        if response.status_code == _HTTP_TOO_MANY_REQUESTS:
            raise CoachError(
                "rate_limited", _error_from_response(response).detail, status=response.status_code
            )
        bundle = self._json(response)
        creds = credentials_from_bundle(bundle, now=self._clock())
        self.store.save(self.base_url, creds)
        return creds

    def start_device_flow(self) -> dict[str, Any]:
        """RFC 8628 step 1: returns the device authorization response."""
        body = self._json(
            self._send("POST", "/api/oauth/device/code", json={"client_id": CLIENT_ID})
        )
        required = ("device_code", "user_code", "verification_uri", "verification_uri_complete")
        if not isinstance(body, dict) or not all(isinstance(body.get(k), str) for k in required):
            raise CoachError("invalid_response", "Device authorization response was incomplete.")
        return body

    def poll_device_flow(
        self,
        grant: dict[str, Any],
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> Credentials:
        """RFC 8628 step 2: poll the token endpoint until approved, denied, or expired."""
        expires_in = grant.get("expires_in")
        lifetime = float(expires_in) if isinstance(expires_in, int | float) else 600.0
        interval = grant.get("interval")
        wait = float(interval) if isinstance(interval, int | float) else _DEFAULT_DEVICE_INTERVAL
        deadline = monotonic() + lifetime
        while True:
            sleep(wait)
            if monotonic() >= deadline:
                raise CoachError("expired", "The login code expired before it was approved.")
            response = self._send(
                "POST",
                "/api/oauth/token",
                json={
                    "grant_type": DEVICE_GRANT_TYPE,
                    "device_code": grant["device_code"],
                    "client_id": CLIENT_ID,
                },
            )
            if response.status_code < _HTTP_ERROR_FLOOR:
                creds = credentials_from_bundle(self._json(response), now=self._clock())
                self.store.save(self.base_url, creds)
                return creds
            error = _error_from_response(response)
            if error.error == "authorization_pending":
                continue
            if error.error == "slow_down":
                wait += _SLOW_DOWN_STEP_SECONDS
                continue
            if error.error == "access_denied":
                raise CoachError("access_denied", "The login request was denied.", status=400)
            if error.error in {"expired_token", "invalid_grant"}:
                raise CoachError(
                    "expired", "The login code expired or is no longer valid.", status=400
                )
            raise error

    def logout(self) -> bool:
        """Revoke server-side (best effort), then always delete the local credentials.

        Returns whether the server confirmed revocation.
        """
        creds = self.store.load(self.base_url)
        if creds is None:
            raise self._login_required()
        revoked = True
        # Refresh token first (kills the session); the access token revoke closes the grant.
        for token, hint in ((creds.refresh_token, "refresh_token"), (creds.access_token, None)):
            body: dict[str, str] = {"client_id": CLIENT_ID, "token": token}
            if hint:
                body["token_type_hint"] = hint
            try:
                response = self._send("POST", "/api/oauth/revoke", json=body)
            except CoachError:
                revoked = False
                continue
            revoked = revoked and response.status_code < _HTTP_ERROR_FLOOR
        self.store.delete(self.base_url)
        return revoked

    # -- API calls --------------------------------------------------------------------------

    def whoami(self) -> dict[str, Any]:
        return self._authed("GET", "/api/auth/whoami")

    def call(self, operation: str, body: dict[str, Any]) -> Any:
        return self._authed("POST", f"/api/engine/{validate_operation(operation)}", json=body)

    def save_recovery_data(self, entries: list[dict[str, Any]]) -> Any:
        return self.call("save-recovery-data", {"entries": entries})

    def upload_activity(self, path: Path) -> dict[str, Any]:
        """presign -> proxy upload -> process. Returns the process response (has ``kind``)."""
        content_type = ACTIVITY_CONTENT_TYPES.get(path.suffix.lower())
        if content_type is None:
            raise CoachError("unsupported_file", f"{path.name}: expected .fit, .gpx or .tcx")
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise CoachError("file_unreadable", f"{path}: {exc.strerror or exc}") from exc
        if not data:
            raise CoachError("empty_file", f"{path.name} is empty")

        presign = self._authed(
            "POST",
            "/api/chat/attachments/presign",
            json={
                "content_length": len(data),
                "content_type": content_type,
                "filename": path.name,
                "purpose": _UPLOAD_PURPOSE,
            },
        )
        # Only the object_key is used: the presigned storage URL is intentionally ignored so
        # the CLI never talks to R2 directly; the app proxies the bytes.
        object_key = presign.get("object_key") if isinstance(presign, dict) else None
        if not isinstance(object_key, str):
            raise CoachError("invalid_response", "Presign response had no object_key.")
        self._authed(
            "POST",
            "/api/chat/attachments/upload",
            data={"object_key": object_key},
            files={"file": (path.name, data, content_type)},
            timeout=_UPLOAD_TIMEOUT,
        )
        body: dict[str, Any] = {
            "content_type": content_type,
            "filename": path.name,
            "object_key": object_key,
        }
        if presign.get("public_url"):
            body["public_url"] = presign["public_url"]
        return self._authed(
            "POST", "/api/engine/process-uploaded-file", json=body, timeout=_UPLOAD_TIMEOUT
        )
