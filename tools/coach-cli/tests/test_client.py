"""Credential store and client internals: key normalization, refresh races, bypass header."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fitness_coach_cli.client import CoachClient
from fitness_coach_cli.credentials import Credentials, CredentialStore
from fitness_coach_cli.errors import CoachError
from fitness_coach_cli.target import normalize_base_url


def _credentials(refresh: str, *, fresh: bool) -> Credentials:
    offset = timedelta(minutes=15) if fresh else timedelta(minutes=-1)
    return Credentials(f"access-for-{refresh}", refresh, datetime.now(UTC) + offset, "user-1")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://Coach.Example.com/", "https://coach.example.com"),
        ("  https://coach.example.com  ", "https://coach.example.com"),
        ("http://LOCALHOST:3000", "http://localhost:3000"),
        ("http://127.0.0.1:8000/", "http://127.0.0.1:8000"),
        ("http://[::1]:3000", "http://[::1]:3000"),
    ],
)
def test_base_url_normalization(raw: str, expected: str) -> None:
    assert normalize_base_url(raw) == expected


def test_store_keeps_deployments_separate(tmp_path: Path) -> None:
    store = CredentialStore(tmp_path / "credentials.json")
    store.save("https://a.example.com", _credentials("a", fresh=True))
    store.save("http://localhost:3000", _credentials("b", fresh=True))

    assert store.delete("https://a.example.com")
    assert store.load("https://a.example.com") is None
    loaded = store.load("http://localhost:3000")
    assert loaded is not None
    assert loaded.refresh_token == "b"
    assert not store.delete("https://a.example.com")


def test_corrupt_store_is_reported_not_silently_reset(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    path.write_text("{not json")

    with pytest.raises(CoachError) as caught:
        CredentialStore(path).load("https://a.example.com")

    assert caught.value.error == "corrupt_credentials"
    assert path.read_text() == "{not json"


def test_refresh_reuses_tokens_rotated_by_a_concurrent_process(tmp_path: Path) -> None:
    store = CredentialStore(tmp_path / "credentials.json")
    base = "https://coach.example.com"
    stale = _credentials("old", fresh=False)
    # Another `coach` process already rotated the refresh token and saved the result.
    store.save(base, _credentials("new", fresh=True))
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"user_id": "user-1"})

    with CoachClient(base, store, transport=httpx.MockTransport(handler)) as client:
        refreshed = client._refresh(stale)

    assert refreshed.refresh_token == "new"
    assert seen == []


def test_bypass_header_is_only_sent_to_vercel_hosts(tmp_path: Path) -> None:
    headers: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        headers[request.url.host] = request.headers.get("x-vercel-protection-bypass")
        return httpx.Response(200, json={"status": "ok", "environment": "preview"})

    store = CredentialStore(tmp_path / "credentials.json")
    for base in ("https://my-app-git-branch.vercel.app", "https://coach.example.com"):
        with CoachClient(
            base, store, bypass_secret="s3cret", transport=httpx.MockTransport(handler)
        ) as client:
            client.environment()

    assert headers == {"my-app-git-branch.vercel.app": "s3cret", "coach.example.com": None}


def test_environment_is_none_when_health_is_unreadable(tmp_path: Path) -> None:
    store = CredentialStore(tmp_path / "credentials.json")
    responses = [
        httpx.Response(404),
        httpx.Response(200, text="ok"),
        httpx.Response(200, json={"status": "ok"}),
    ]
    for response in responses:
        transport = httpx.MockTransport(lambda _request, r=response: r)
        with CoachClient("https://c.example.com", store, transport=transport) as client:
            assert client.environment() is None


def test_store_file_is_versioned_json(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    CredentialStore(path).save("https://a.example.com", _credentials("a", fresh=True))

    document = json.loads(path.read_text())

    assert document["version"] == 1
    assert document["deployments"]["https://a.example.com"]["refresh_token"] == "a"
