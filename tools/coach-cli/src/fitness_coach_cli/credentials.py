"""Private on-disk token store, keyed by deployment base URL."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from fitness_coach_cli.errors import CoachError

_STORE_VERSION = 1
# Refresh slightly early so a token cannot expire between the check and the server's check.
_EXPIRY_SKEW = timedelta(seconds=60)


def default_credentials_path() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(config_home) / "fitness-coach" / "credentials.json"


@dataclass(frozen=True)
class Credentials:
    access_token: str
    refresh_token: str
    expires_at: datetime
    user_id: str

    def is_fresh(self, now: datetime | None = None) -> bool:
        return self.expires_at - _EXPIRY_SKEW > (now or datetime.now(UTC))

    def to_json(self) -> dict[str, str]:
        payload = asdict(self)
        payload["expires_at"] = self.expires_at.isoformat()
        return payload

    @classmethod
    def from_json(cls, payload: object) -> Credentials:
        if not isinstance(payload, dict):
            raise TypeError("credential entry is not an object")
        entry = cast(dict[str, Any], payload)
        expires_at = datetime.fromisoformat(str(entry["expires_at"]))
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return cls(
            access_token=str(entry["access_token"]),
            refresh_token=str(entry["refresh_token"]),
            expires_at=expires_at,
            user_id=str(entry["user_id"]),
        )


class CredentialStore:
    """JSON file of ``{base_url: Credentials}``; directory 0700, file 0600.

    Writers hold an exclusive ``flock`` on a sibling lock file. Refresh tokens rotate on
    every use, so two ``coach`` processes refreshing at once must serialize, or the loser
    presents an already-rotated token and is signed out.
    """

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser()

    def load(self, base_url: str) -> Credentials | None:
        entry = self._read().get(base_url)
        if entry is None:
            return None
        try:
            return Credentials.from_json(entry)
        except (KeyError, TypeError, ValueError) as exc:
            raise CoachError(
                "corrupt_credentials",
                f"Stored credentials for {base_url} are unreadable; run `coach logout` and "
                "sign in again.",
            ) from exc

    def save(self, base_url: str, credentials: Credentials) -> None:
        deployments = self._read()
        deployments[base_url] = credentials.to_json()
        self._write(deployments)

    def delete(self, base_url: str) -> bool:
        deployments = self._read()
        if deployments.pop(base_url, None) is None:
            return False
        self._write(deployments)
        return True

    @contextlib.contextmanager
    def locked(self) -> Iterator[None]:
        _mkdir_private(self.path.parent)
        lock_path = self.path.with_name(f".{self.path.name}.lock")
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)

    def _read(self) -> dict[str, Any]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CoachError(
                "corrupt_credentials", f"Credential file {self.path} is not valid JSON."
            ) from exc
        deployments = payload.get("deployments") if isinstance(payload, dict) else None
        return dict(deployments) if isinstance(deployments, dict) else {}

    def _write(self, deployments: dict[str, Any]) -> None:
        _mkdir_private(self.path.parent)
        document = {"version": _STORE_VERSION, "deployments": deployments}
        _write_private_atomic(self.path, json.dumps(document, indent=2).encode("utf-8"))


def _mkdir_private(path: Path) -> None:
    """Create path and missing parents, securing each newly created directory to 0700."""
    for part in (*reversed(path.parents), path):
        with contextlib.suppress(FileExistsError):
            part.mkdir(mode=0o700)


def _write_private_atomic(path: Path, payload: bytes) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{path.name}.", dir=path.parent, delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary_path.chmod(0o600)
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
