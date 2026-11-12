from __future__ import annotations

import base64
import hashlib
from contextlib import nullcontext
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
from fitness_coach_cli import approval as module
from fitness_coach_cli.approval import Approval, browser_login, callback_address
from fitness_coach_cli.client import CoachClient
from fitness_coach_cli.credentials import CoachError, CredentialStore


@pytest.mark.parametrize(
    "path",
    [
        "/callback?code=x&state=wrong",
        "/callback?code=x&state=%C3%A9",
        "/other?code=x&state=state",
        "/callback?code=x&state=state&state=state",
        "/callback?code=x&code=y&state=state",
        "/callback?code=x&error=denied&state=state",
    ],
)
def test_forged_or_ambiguous_callback_does_not_complete_login(path: str) -> None:
    result = Approval("state", "/callback")
    assert not result.capture(path)
    assert result.code is None and not result.denied


def test_approval_code_and_denial() -> None:
    result = Approval("state", "/callback")
    assert result.capture("/callback?state=state&code=auth-code")
    assert result.code == "auth-code"
    result = Approval("state", "/callback")
    assert result.capture("/callback?state=state&error=access_denied")
    assert result.denied


@pytest.mark.parametrize(
    "uri",
    [
        "https://127.0.0.1/callback",
        "http://evil.example/callback",
        "http://127.0.0.1.evil.example/callback",
        "http://user@localhost/callback",
        "http://localhost:bad/callback",
        "http://localhost/callback?x=y",
        "http://localhost/callback#fragment",
    ],
)
def test_callback_must_be_loopback_without_embedded_parameters(uri: str) -> None:
    with pytest.raises(CoachError):
        callback_address(uri)


def test_custom_callback_accepts_localhost_port_and_path() -> None:
    assert callback_address("http://localhost:8765/approved") == ("localhost", 8765, "/approved")


def test_login_timeout_saves_no_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from contextlib import contextmanager
    from types import SimpleNamespace

    @contextmanager
    def server(_address, _callback):
        yield SimpleNamespace(server_port=8765, timeout=1)

    clock = iter([0, 1])
    monkeypatch.setattr(module, "HTTPServer", server)
    monkeypatch.setattr(module.time, "monotonic", lambda: next(clock))
    store = CredentialStore(tmp_path / "private/credentials.json")
    with (
        CoachClient("http://localhost:3000", store) as client,
        pytest.raises(CoachError, match="timed out"),
    ):
        browser_login(
            client,
            callback_url="http://localhost:0/callback",
            open_browser=False,
            timeout=1,
            announce=lambda _text: None,
        )
    assert not store.path.exists()


def _unexpected_browser(_url: str) -> None:
    raise AssertionError("no-browser opened a browser")


@pytest.mark.parametrize("denied", [False, True])
def test_browser_login_checks_state_and_exchanges_exact_pkce_callback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, denied: bool
) -> None:
    captured: dict = {}
    real_handler = module._handler

    def handler(approval: Approval):
        captured["approval"] = approval
        return real_handler(approval)

    class Server:
        server_port = 45678
        timeout = 1

        def __init__(self, address, _handler):
            assert address == ("127.0.0.1", 0)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def handle_request(self):
            query = captured["query"]
            approval = captured["approval"]
            assert not approval.capture("/approved?code=forged&state=wrong")
            params = {
                "state": query["state"][0],
                "error" if denied else "code": "denied" if denied else "auth-code",
            }
            assert approval.capture("/approved?" + urlencode(params))

    def announce(text: str) -> None:
        captured["query"] = parse_qs(urlsplit(text.splitlines()[-1]).query)

    def exchange(request: httpx.Request) -> httpx.Response:
        import json

        payload = json.loads(request.content)
        query = captured["query"]
        assert payload["code"] == "auth-code"
        assert (
            payload["redirect_uri"] == query["redirect_uri"][0] == "http://localhost:45678/approved"
        )
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(payload["code_verifier"].encode()).digest())
            .decode()
            .rstrip("=")
        )
        assert challenge == query["code_challenge"][0]
        assert query["code_challenge_method"] == ["S256"]
        assert query["prompt"] == ["consent"]
        return httpx.Response(
            200,
            json={
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_at": "2099-01-01T00:00:00+00:00",
            },
        )

    monkeypatch.setattr(module, "_handler", handler)
    monkeypatch.setattr(module, "HTTPServer", Server)

    monkeypatch.setattr(module.webbrowser, "open", _unexpected_browser)
    store = CredentialStore(tmp_path / "private/credentials.json")
    expected = pytest.raises(CoachError, match="denied") if denied else nullcontext()
    with (
        expected,
        CoachClient(
            "https://preview.example.com", store, transport=httpx.MockTransport(exchange)
        ) as client,
    ):
        browser_login(
            client,
            callback_url="http://localhost:0/approved",
            open_browser=False,
            timeout=5,
            announce=announce,
        )
    assert store.path.exists() is (not denied)
