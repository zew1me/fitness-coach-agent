"""Base-URL normalisation and the production-login guard."""

from __future__ import annotations

from urllib.parse import urlsplit

from fitness_coach_cli.errors import CoachError

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_PREVIEW_SUFFIX = ".vercel.app"


def normalize_base_url(raw: str) -> str:
    """Return ``scheme://host[:port][/path]`` lower-cased, without query or trailing slash.

    The result keys the credentials file, so equivalent spellings must collapse to one key.
    """
    parts = urlsplit(raw.strip())
    host = parts.hostname
    if parts.scheme not in {"http", "https"} or not host:
        raise CoachError("invalid_base_url", f"Not an http(s) URL: {raw!r}")
    shown_host = f"[{host}]" if ":" in host else host
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{shown_host}{port}{parts.path.rstrip('/')}"


def _host(base_url: str) -> str:
    return urlsplit(base_url).hostname or ""


def is_local(base_url: str) -> bool:
    return _host(base_url) in _LOCAL_HOSTS


def requires_confirmation(base_url: str) -> bool:
    """True for anything that is not localhost or a ``*.vercel.app`` preview deployment.

    Logging in stores a long-lived refresh token and lets an agent act on the athlete's real
    data, so a non-preview target (production) needs an explicit ``--yes``.
    """
    host = _host(base_url)
    return host not in _LOCAL_HOSTS and not host.endswith(_PREVIEW_SUFFIX)


def require_secure_transport(base_url: str) -> None:
    """Refuse to send bearer tokens or login codes over plain http to a remote host."""
    if base_url.startswith("http://") and not is_local(base_url):
        raise CoachError("insecure_base_url", "Remote base URLs must use https.")
