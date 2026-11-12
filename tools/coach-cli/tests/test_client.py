from __future__ import annotations

import json
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fitness_coach_cli.client import CoachClient, stream_events
from fitness_coach_cli.credentials import CoachError, CredentialStore, Tokens, normalize_base_url

BASE = "https://preview.example.com"


def token_payload(access: str = "access", *, expired: bool = False) -> dict[str, str]:
    return {
        "access_token": access,
        "refresh_token": f"refresh-{access}",
        "expires_at": (datetime.now(UTC) + timedelta(minutes=-1 if expired else 15)).isoformat(),
    }


@pytest.fixture
def store(tmp_path: Path) -> CredentialStore:
    result = CredentialStore(tmp_path / "private/credentials.json")
    with result.locked():
        result.save(BASE, Tokens.parse(token_payload()))
    return result


def test_private_store_preserves_other_deployments(store: CredentialStore) -> None:
    with store.locked():
        store.save("http://localhost:3000", Tokens.parse(token_payload("local")))
        store.save(BASE, None)
        assert store.get("http://localhost:3000").access_token == "local"
        with pytest.raises(CoachError, match="Not logged in"):
            store.get(BASE)
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(store.path.with_suffix(".lock").stat().st_mode) == 0o600


def test_store_rejects_symlink_and_invalid_json(store: CredentialStore, tmp_path: Path) -> None:
    store.path.write_text("[]")
    with pytest.raises(CoachError, match="Invalid credential"):
        store.get(BASE)
    store.path.unlink()
    store.path.symlink_to(tmp_path / "other.json")
    with pytest.raises(CoachError, match="symlink"):
        store.get(BASE)


@pytest.mark.parametrize(
    "value",
    [
        "http://remote.example",
        "https://user:pass@example.com",
        "https://example.com/path",
        "https://example.com/?token=x",
        "https://example.com/#x",
        "//example.com",
        "https://example.com:bad",
    ],
)
def test_invalid_targets_are_rejected(value: str) -> None:
    with pytest.raises(CoachError):
        normalize_base_url(value)


def test_loopback_and_https_targets() -> None:
    assert normalize_base_url("http://127.0.0.1:3000/") == "http://127.0.0.1:3000"
    assert normalize_base_url("https://PREVIEW.example.com/") == BASE


@pytest.mark.parametrize("expired", [False, True])
def test_refresh_on_401_or_expiry(store: CredentialStore, expired: bool) -> None:
    with store.locked():
        store.save(BASE, Tokens.parse(token_payload(expired=expired)))
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "preview.example.com"
        calls.append(request.url.path)
        if request.url.path == "/api/oauth/token":
            assert json.loads(request.content)["refresh_token"] == "refresh-access"
            return httpx.Response(200, json=token_payload("new"))
        if request.headers["Authorization"] == "Bearer access":
            return httpx.Response(401)
        assert request.headers["Authorization"] == "Bearer new"
        return httpx.Response(200, json={"user_id": "athlete-a"})

    with CoachClient(BASE, store, transport=httpx.MockTransport(handler)) as client:
        assert client.request("GET", "/api/auth/whoami") == {"user_id": "athlete-a"}
    assert calls.count("/api/oauth/token") == 1
    assert store.get(BASE).refresh_token == "refresh-new"


def test_revoked_refresh_fails_without_retry_storm(store: CredentialStore) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(
            400 if request.url.path == "/api/oauth/token" else 401,
            json={"detail": "secret-refresh-value"},
        )

    with (
        CoachClient(BASE, store, transport=httpx.MockTransport(handler)) as client,
        pytest.raises(CoachError, match="Log in again") as error,
    ):
        client.request("GET", "/api/auth/whoami")
    assert "secret-refresh-value" not in str(error.value)
    assert calls == ["/api/auth/whoami", "/api/oauth/token"]


def test_transport_failure_does_not_replay_write(store: CredentialStore) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        raise httpx.ReadTimeout("uncertain write outcome")

    with (
        CoachClient(BASE, store, transport=httpx.MockTransport(handler)) as client,
        pytest.raises(httpx.ReadTimeout),
    ):
        client.request("POST", "/api/engine/save-recovery-data", json={"entries": []})
    assert calls == ["/api/engine/save-recovery-data"]


def test_redirect_does_not_forward_bearer_to_another_origin(store: CredentialStore) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(307, headers={"location": "https://attacker.example/"})

    with (
        CoachClient(BASE, store, transport=httpx.MockTransport(handler)) as client,
        pytest.raises(CoachError),
    ):
        client.request("GET", "/api/auth/whoami")
    assert len(calls) == 1


def test_logout_revokes_grant_and_removes_only_target_credentials(store: CredentialStore) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/oauth/revoke"
        assert json.loads(request.content) == {"client_id": "fitness-coach-cli", "token": "access"}
        return httpx.Response(200, json={"revoked": True})

    with CoachClient(BASE, store, transport=httpx.MockTransport(handler)) as client:
        client.logout()
    with pytest.raises(CoachError, match="Not logged in"):
        store.get(BASE)


def test_stream_refreshes_before_yielding_and_keeps_all_events(store: CredentialStore) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/oauth/token":
            return httpx.Response(200, json=token_payload("new"))
        if request.headers["Authorization"] == "Bearer access":
            return httpx.Response(401)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=(
                ': heartbeat\n\ndata: {"type":"text-delta","delta":"Hi"}\n\n'
                'data: {"type":"tool-input-available","toolName":"get_athlete_summary"}\n\n'
                'data: {"type":"finish"}\n\ndata: [DONE]\n\n'
            ),
        )

    with (
        CoachClient(BASE, store, transport=httpx.MockTransport(handler)) as client,
        client.stream({"message": {}}) as response,
    ):
        events = list(stream_events(response))
    assert [event["type"] for event in events] == ["text-delta", "tool-input-available", "finish"]


@pytest.mark.parametrize(
    "content",
    [
        'data: {"type":"text-delta","delta":"partial"}\n\n',
        'data: {"type":"error","errorText":"secret"}\n\n',
        "data: []\n\n",
        'data: {"type":"abort"}\n\ndata: [DONE]\n\n',
        "data: [DONE]\n\n",
    ],
)
def test_broken_stream_is_not_a_success(content: str) -> None:
    response = httpx.Response(200, text=content)
    with pytest.raises(CoachError):
        list(stream_events(response))
