"""Deployment base URLs: validation, canonical form, and environment classification."""

from __future__ import annotations

from urllib.parse import urlsplit

from fitness_coach_cli.errors import CoachError

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_VERCEL_PREVIEW_SUFFIX = ".vercel.app"


def normalize_base_url(raw: str) -> str:
    """Return ``scheme://host[:port]`` so one deployment always maps to one credential key.

    Plain HTTP is accepted only for a local server: sign-in codes and bearer tokens must
    never cross a network unencrypted.
    """
    parts = urlsplit(raw.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme not in {"http", "https"} or not host:
        raise CoachError("invalid_base_url", f"--base-url must be an http(s) URL, got {raw!r}.")
    if parts.path.strip("/") or parts.query or parts.fragment or parts.username:
        raise CoachError(
            "invalid_base_url",
            "--base-url must be the deployment origin only, e.g. https://coach.example.com.",
        )
    if parts.scheme == "http" and host not in _LOCAL_HOSTS:
        raise CoachError("invalid_base_url", "Only a local --base-url may use plain http.")
    netloc = f"[{host}]" if ":" in host else host
    if parts.port is not None:
        netloc = f"{netloc}:{parts.port}"
    return f"{parts.scheme}://{netloc}"


def is_local(base_url: str) -> bool:
    return (urlsplit(base_url).hostname or "") in _LOCAL_HOSTS


def is_vercel_host(base_url: str) -> bool:
    return (urlsplit(base_url).hostname or "").endswith(_VERCEL_PREVIEW_SUFFIX)
