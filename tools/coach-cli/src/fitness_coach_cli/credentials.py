"""Private, atomic credential storage keyed by an explicit deployment URL."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


class CoachError(ValueError):
    """A bounded, user-facing client failure without credential-bearing diagnostics."""

    def __init__(self, detail: str, *, status: int | None = None) -> None:
        super().__init__(detail)
        self.status = status


def normalize_base_url(value: str) -> str:
    """Accept HTTPS deployments or explicit HTTP loopback development targets."""
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise CoachError("--base-url must be a deployment origin without credentials or a path.")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise CoachError("Plain HTTP is allowed only for a loopback development deployment.")
    try:
        _ = parsed.port
    except ValueError as error:
        raise CoachError("Invalid deployment port.") from error
    return f"{parsed.scheme}://{parsed.netloc.lower()}"


@dataclass(frozen=True)
class Tokens:
    access_token: str
    refresh_token: str
    expires_at: datetime

    @classmethod
    def parse(cls, payload: Any) -> Tokens:  # HTTP and file data are untrusted until validated.
        if not isinstance(payload, dict):
            raise CoachError("Invalid token response or credential file; log in again.")
        access, refresh, expiry = (
            payload.get(k) for k in ("access_token", "refresh_token", "expires_at")
        )
        if not all(isinstance(v, str) and v for v in (access, refresh, expiry)):
            raise CoachError("Invalid token response or credential file; log in again.")
        try:
            expires_at = datetime.fromisoformat(expiry)
        except ValueError as error:
            raise CoachError("Invalid token expiry; log in again.") from error
        if expires_at.tzinfo is None or payload.get("token_type", "Bearer") != "Bearer":
            raise CoachError("Invalid token response or credential file; log in again.")
        return cls(access, refresh, expires_at)

    def payload(self) -> dict[str, str]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at.isoformat(),
        }

    def needs_refresh(self) -> bool:
        return self.expires_at <= datetime.now(UTC) + timedelta(seconds=30)


def default_store_path() -> Path:
    return (
        Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser()
        / "fitness-coach/credentials.json"
    )


class CredentialStore:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser()

    @contextmanager
    def locked(self):
        """Serialize refresh rotation across local CLI processes; never lock the renamed file."""
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.path.parent.is_symlink():
            raise CoachError("The credential directory must not be a symlink.")
        self.path.parent.chmod(0o700)
        lock = self.path.with_suffix(".lock")
        descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "r+") as handle:
            os.fchmod(handle.fileno(), 0o600)
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield self
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _read(self) -> dict[str, Any]:
        if self.path.is_symlink():
            raise CoachError("The credential file must not be a symlink.")
        if not self.path.exists():
            return {}
        self.path.chmod(0o600)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError) as error:
            raise CoachError("Invalid credential file; move it aside and log in again.") from error
        if not isinstance(data, dict):
            raise CoachError("Invalid credential file; move it aside and log in again.")
        return data

    def get(self, base_url: str) -> Tokens:
        data = self._read()
        if base_url not in data:
            raise CoachError("Not logged in to this deployment. Run coach login first.", status=401)
        return Tokens.parse(data[base_url])

    def save(self, base_url: str, tokens: Tokens | None) -> None:
        """Call under locked(); replace atomically without exposing tokens in a partial write."""
        data = self._read()
        if tokens is None:
            data.pop(base_url, None)
        else:
            data[base_url] = tokens.payload()
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=self.path.parent, delete=False) as out:
                temporary = Path(out.name)
                os.fchmod(out.fileno(), 0o600)
                json.dump(data, out)
                out.flush()
                os.fsync(out.fileno())
            temporary.replace(self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
