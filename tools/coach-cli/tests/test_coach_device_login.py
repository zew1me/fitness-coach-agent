from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fitness_coach_cli import cli
from typer.testing import Result

Run = Callable[..., Result]

CODE_PATH = "/api/oauth/device/code"
TOKEN_PATH = "/api/oauth/token"
GRANT: dict[str, Any] = {
    "device_code": "dev-secret",
    "user_code": "BCDF-GHJK",
    "verification_uri": "https://preview-abc.vercel.app/device",
    "verification_uri_complete": "https://preview-abc.vercel.app/api/oauth/device?user_code=BCDFGHJK",
    "expires_in": 600,
    "interval": 5,
}


class FakeTime:
    """Virtual clock: sleep() advances it, so polling loops finish instantly."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def monotonic(self) -> float:
        return self.now


@pytest.fixture
def fake_time(monkeypatch: pytest.MonkeyPatch) -> FakeTime:
    fake = FakeTime()
    monkeypatch.setattr(cli, "_sleep", fake.sleep)
    monkeypatch.setattr(cli, "_monotonic", fake.monotonic)
    return fake


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    urls: list[str] = []
    monkeypatch.setattr(cli, "_open_url", lambda url: urls.append(url) or True)
    monkeypatch.setattr(cli, "_browser_plausible", lambda: True)
    return urls


def err(code: str) -> dict[str, str]:
    return {"error": code, "error_description": code}


def queue_polls(api: Any, *polls: tuple[int, dict[str, Any]]) -> None:
    api.on("POST", CODE_PATH, json=GRANT)
    for status, body in polls:
        api.on("POST", TOKEN_PATH, status=status, json=body)


def test_happy_path_stores_credentials(
    api: Any, run: Run, fake_time: FakeTime, opened: list[str], creds_path: Path, h: Any
) -> None:
    queue_polls(
        api,
        (400, err("authorization_pending")),
        (400, err("authorization_pending")),
        (200, h.bundle()),
    )
    result = run("login")
    assert result.exit_code == 0, result.output
    assert "BCDF-GHJK" in result.output
    assert GRANT["verification_uri"] in result.output
    assert "acc-1" not in result.output
    assert fake_time.sleeps == [5, 5, 5]
    assert h.body(api.requests[0]) == {"client_id": "fitness-coach-cli"}
    poll = h.body(api.to(TOKEN_PATH)[0])
    assert poll == {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "device_code": "dev-secret",
        "client_id": "fitness-coach-cli",
    }
    assert oct(creds_path.stat().st_mode & 0o777) == "0o600"
    assert h.BASE in json.loads(creds_path.read_text())["servers"]
    assert opened == [GRANT["verification_uri_complete"]]


def test_slow_down_adds_five_seconds(
    api: Any, run: Run, fake_time: FakeTime, opened: list[str], h: Any
) -> None:
    queue_polls(
        api, (400, err("slow_down")), (400, err("authorization_pending")), (200, h.bundle())
    )
    assert run("login").exit_code == 0
    assert fake_time.sleeps == [5, 10, 10]


def test_access_denied(api: Any, run: Run, fake_time: FakeTime, creds_path: Path) -> None:
    queue_polls(api, (400, err("access_denied")))
    result = run("--json", "login")
    assert result.exit_code == 1
    assert json.loads(result.stderr)["error"] == "access_denied"
    assert not creds_path.exists()


@pytest.mark.parametrize("code", ["expired_token", "invalid_grant"])
def test_expired_and_invalid_grant_are_expired(
    api: Any, run: Run, fake_time: FakeTime, code: str
) -> None:
    queue_polls(api, (400, err(code)))
    result = run("--json", "login")
    assert result.exit_code == 1
    assert json.loads(result.stderr)["error"] == "expired"


def test_local_deadline(api: Any, run: Run, fake_time: FakeTime, creds_path: Path) -> None:
    queue_polls(api, (400, err("authorization_pending")))
    result = run("--json", "login")
    assert result.exit_code == 1
    assert json.loads(result.stderr)["error"] == "expired"
    assert fake_time.now >= 600
    assert len(api.to(TOKEN_PATH)) == 119  # 600s / 5s, the last tick hits the deadline
    assert not creds_path.exists()


def test_json_first_line_shape(
    api: Any, run: Run, fake_time: FakeTime, opened: list[str], h: Any
) -> None:
    queue_polls(api, (400, err("authorization_pending")), (200, h.bundle()))
    result = run("--json", "login", "--no-browser")
    assert result.exit_code == 0
    first, last = result.stdout.strip().splitlines()
    assert json.loads(first) == {
        "verification_uri": GRANT["verification_uri"],
        "verification_uri_complete": GRANT["verification_uri_complete"],
        "user_code": "BCDF-GHJK",
        "expires_in": 600,
    }
    assert json.loads(last) == {"status": "logged_in", "user_id": "user-1", "base_url": h.BASE}
    assert "Waiting" not in result.stdout


def test_no_browser_skips_opener(
    api: Any, run: Run, fake_time: FakeTime, opened: list[str], h: Any
) -> None:
    queue_polls(api, (200, h.bundle()))
    assert run("login", "--no-browser").exit_code == 0
    assert opened == []


def test_opener_exception_is_ignored(
    api: Any, run: Run, fake_time: FakeTime, monkeypatch: pytest.MonkeyPatch, h: Any
) -> None:
    def boom(_url: str) -> bool:
        raise RuntimeError("no browser")

    monkeypatch.setattr(cli, "_open_url", boom)
    monkeypatch.setattr(cli, "_browser_plausible", lambda: True)
    queue_polls(api, (200, h.bundle()))
    assert run("login").exit_code == 0


def test_browser_not_opened_without_display(
    api: Any,
    run: Run,
    fake_time: FakeTime,
    monkeypatch: pytest.MonkeyPatch,
    h: Any,
) -> None:
    opened: list[str] = []
    monkeypatch.setattr(cli, "_open_url", lambda url: opened.append(url) or True)
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    queue_polls(api, (200, h.bundle()))
    assert run("login").exit_code == 0
    assert opened == []


def test_production_requires_yes_before_any_request(
    api: Any, run: Run, fake_time: FakeTime, h: Any
) -> None:
    queue_polls(api, (200, h.bundle()))
    refused = run("login", base="https://coach.example.com")
    assert refused.exit_code == 1
    assert "--yes" in refused.stderr
    assert not api.requests
    assert run("login", "--yes", "--no-browser", base="https://coach.example.com").exit_code == 0


def test_device_code_failure_surfaces(api: Any, run: Run, fake_time: FakeTime) -> None:
    api.on("POST", CODE_PATH, status=400, json={"error": "invalid_client"})
    result = run("--json", "login")
    assert result.exit_code == 1
    assert json.loads(result.stderr)["error"] == "invalid_client"
