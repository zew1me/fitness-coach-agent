from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fitness_coach_cli import cli
from fitness_coach_cli.client import CoachClient
from fitness_coach_cli.credentials import CredentialStore, Tokens
from typer.testing import CliRunner

BASE = "https://preview.example.com"


@pytest.fixture
def cli_transport(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    requests: list[httpx.Request] = []
    path = tmp_path / "private/credentials.json"
    store = CredentialStore(path)
    with store.locked():
        store.save(
            BASE,
            Tokens.parse(
                {
                    "access_token": "private-access",
                    "refresh_token": "private-refresh",
                    "expires_at": "2099-01-01T00:00:00+00:00",
                }
            ),
        )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.host == "preview.example.com"
        assert request.headers["authorization"] == "Bearer private-access"
        if request.url.path.endswith("presign-upload"):
            return httpx.Response(
                200,
                json={
                    "object_key": "users/athlete/file.fit",
                    "upload_url": "https://never-fetch-r2.example/",
                },
            )
        if request.url.path.endswith("attachments/upload"):
            return httpx.Response(200, json={"public_url": "https://canonical-r2.example/file.fit"})
        if request.url.path == "/api/chat":
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text=(
                    'data: {"type":"text-delta","delta":"Hello"}\n\n'
                    'data: {"type":"finish"}\n\ndata: [DONE]\n\n'
                ),
            )
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(
        cli,
        "CoachClient",
        lambda base, store: CoachClient(base, store, transport=httpx.MockTransport(handler)),
    )
    return ["--base-url", BASE, "--credentials", str(path), "--json"], requests


def test_help_and_tools_list_need_no_credentials() -> None:
    runner = CliRunner()
    assert runner.invoke(cli.app, ["--help"]).exit_code == 0
    result = runner.invoke(cli.app, ["--json", "tools", "list"])
    assert result.exit_code == 0, result.output
    assert any(t["operation"] == "get-athlete-summary" for t in json.loads(result.output))


def test_target_is_always_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COACH_BASE_URL", raising=False)
    result = CliRunner().invoke(cli.app, ["whoami"])
    assert result.exit_code == 1
    assert "no deployment is implicit" in result.output


def test_generic_call_routes_to_same_engine_endpoint(cli_transport) -> None:
    options, requests = cli_transport
    result = CliRunner().invoke(
        cli.app, [*options, "call", "get-athlete-summary", "--data", "-"], input="{}"
    )
    assert result.exit_code == 0, result.output
    assert requests[-1].url.path == "/api/engine/get-athlete-summary"
    assert json.loads(requests[-1].content) == {}
    assert "private-access" not in result.output


@pytest.mark.parametrize(
    "operation", ["../oauth/token", "https://evil.example", "x?y=z", "%2Fsecret"]
)
def test_call_cannot_escape_engine_operation_path(cli_transport, operation: str) -> None:
    options, requests = cli_transport
    result = CliRunner().invoke(cli.app, [*options, "call", operation])
    assert result.exit_code == 1
    assert not requests


def test_wellness_pipe_preserves_garmin_provenance(cli_transport) -> None:
    options, requests = cli_transport
    entries = [{"log_date": "2026-11-10", "source": "garmin_sidecar", "resting_hr_bpm": 48}]
    result = CliRunner().invoke(
        cli.app, [*options, "wellness", "push", "-"], input=json.dumps(entries)
    )
    assert result.exit_code == 0, result.output
    assert requests[-1].url.path == "/api/engine/save-recovery-data"
    assert json.loads(requests[-1].content) == {"entries": entries}


def test_upload_uses_proxy_and_does_not_call_r2(cli_transport, tmp_path: Path) -> None:
    options, requests = cli_transport
    file = tmp_path / "ride.fit"
    file.write_bytes(b"fit-content")
    result = CliRunner().invoke(cli.app, [*options, "activities", "upload", str(file)])
    assert result.exit_code == 0, result.output
    assert [r.url.path for r in requests] == [
        "/api/files/presign-upload",
        "/api/chat/attachments/upload",
        "/api/engine/process-uploaded-file",
    ]
    assert b"fit-content" in requests[1].content
    assert json.loads(requests[-1].content)["public_url"] == "https://canonical-r2.example/file.fit"


def test_chat_jsonl_preserves_events_and_message_identity(cli_transport) -> None:
    options, requests = cli_transport
    result = CliRunner().invoke(
        cli.app, [*options, "chat", "send", "How should I train?", "--stream"]
    )
    assert result.exit_code == 0, result.output
    assert [json.loads(line)["type"] for line in result.output.splitlines()] == [
        "text-delta",
        "finish",
    ]
    payload = json.loads(requests[-1].content)["message"]
    assert payload["parts"] == [{"type": "text", "text": "How should I train?"}]
    assert payload["role"] == "user" and payload["id"]


def test_human_chat_stream(cli_transport) -> None:
    options, _ = cli_transport
    result = CliRunner().invoke(cli.app, [*options[:-1], "chat", "send", "Hi", "--stream"])
    assert result.exit_code == 0, result.output
    assert result.output == "Hello\n"


@pytest.mark.parametrize("no_browser", [False, True])
def test_login_forwards_custom_callback_and_browser_choice(
    cli_transport, monkeypatch: pytest.MonkeyPatch, no_browser: bool
) -> None:
    options, requests = cli_transport
    captured = []

    def approve(_client, **kwargs) -> None:
        captured.append(kwargs)

    monkeypatch.setattr(cli, "browser_login", approve)
    args = [*options, "login", "--yes", "--callback-url", "http://localhost:8765/approved"]
    if no_browser:
        args.append("--no-browser")
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    assert captured[0]["callback_url"] == "http://localhost:8765/approved"
    assert captured[0]["open_browser"] is (not no_browser)
    assert not requests


def test_remote_login_requires_confirmation(cli_transport, monkeypatch: pytest.MonkeyPatch) -> None:
    options, requests = cli_transport

    def unexpected_login(*_args, **_kwargs) -> None:
        raise AssertionError("login must not start")

    monkeypatch.setattr(cli, "browser_login", unexpected_login)
    result = CliRunner().invoke(cli.app, [*options, "login"], input="n\n")
    assert result.exit_code != 0
    assert not requests
