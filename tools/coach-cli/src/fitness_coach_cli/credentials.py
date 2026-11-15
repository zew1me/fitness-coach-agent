"""Per-base-url token storage in a private, atomically written JSON file."""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

if sys.platform != "win32":
    import fcntl

from fitness_coach_cli.errors import CoachError

CREDENTIALS_ENV = "COACH_CREDENTIALS_FILE"
_FORMAT_VERSION = 1


@dataclass(frozen=True)
class Credentials:
    access_token: str
    refresh_token: str
    expires_at: float  # unix seconds
    user_id: str


def default_credentials_path() -> Path:
    override = os.environ.get(CREDENTIALS_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "fitness-coach" / "credentials.json"


def _mkdir_private(path: Path) -> None:
    """Create missing directories as 0700; existing ones (e.g. ``/tmp``) are left untouched."""
    for part in (*reversed(path.parents), path):
        try:
            part.mkdir(mode=0o700)
        except FileExistsError:
            continue
        part.chmod(0o700)  # mkdir's mode is masked by umask


def _write_private_atomic(path: Path, payload: bytes) -> None:
    _mkdir_private(path.parent)
    # mkstemp creates the file 0600 in the destination directory, so the rename is atomic and
    # the secret is never readable by others, even briefly.
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temp.chmod(0o600)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


class CredentialStore:
    """Credentials for several deployments, keyed by normalised base URL."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _read_all(self) -> dict[str, dict[str, Any]]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        try:
            servers = json.loads(raw)["servers"]
        except (ValueError, KeyError, TypeError) as exc:
            raise CoachError(
                "credentials_unreadable",
                f"{self.path} is not a valid credentials file; delete it and run `coach login`.",
            ) from exc
        return servers if isinstance(servers, dict) else {}

    def _write_all(self, servers: dict[str, dict[str, Any]]) -> None:
        document = {"version": _FORMAT_VERSION, "servers": servers}
        _write_private_atomic(self.path, json.dumps(document, indent=2).encode("utf-8"))

    @contextlib.contextmanager
    def refresh_lock(self) -> Iterator[None]:
        """Serialise token refreshes across processes sharing this credentials file.

        Refresh tokens rotate, so two processes presenting the same one would see the loser
        rejected. Uses ``flock`` on a sibling ``.lock`` file (0600); where ``fcntl`` is
        unavailable (Windows) it is a no-op and callers rely on their post-refresh reload check.
        """
        if sys.platform == "win32":
            yield
            return
        _mkdir_private(self.path.parent)
        lock_path = self.path.with_name(f"{self.path.name}.lock")
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)  # closing the descriptor releases the lock

    def load(self, base_url: str) -> Credentials | None:
        entry = self._read_all().get(base_url)
        if entry is None:
            return None
        try:
            return Credentials(
                access_token=str(entry["access_token"]),
                refresh_token=str(entry["refresh_token"]),
                expires_at=float(entry["expires_at"]),
                user_id=str(entry["user_id"]),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def save(self, base_url: str, credentials: Credentials) -> None:
        servers = self._read_all()
        servers[base_url] = asdict(credentials)
        self._write_all(servers)

    def delete(self, base_url: str) -> bool:
        servers = self._read_all()
        if base_url not in servers:
            return False
        del servers[base_url]
        if servers:
            self._write_all(servers)
        else:
            with contextlib.suppress(FileNotFoundError):
                self.path.unlink()
        return True
