"""End-to-end CLI behaviour against an in-process fake deployment (no network)."""

from __future__ import annotations

import json
import stat
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fitness_coach_cli import cli
from fitness_coach_cli.client import CLI_CLIENT_ID
from fitness_coach_cli.credentials import Credentials, CredentialStore
from typer.testing import CliRunner

BASE = "https://coach.example.com"
EMAIL = "athlete@example.com"
CODE = "123456"


@dataclass
class FakeDeployment:
    """Just enough of the deployment API for the CLI: auth, whoami and engine calls."""

    environment: str | None = "preview"
    access_ttl: timedelta = timedelta(minutes=15)
    issued: int = 0
    live_access: set[str] = field(default_factory=set)
    live_refresh: set[str] = field(default_factory=set)
    revoked_grant: bool = False
    requests: list[httpx.Request] = field(default_factory=list)
    reject_next_access: bool = False

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def _tokens(self) -> dict[str, Any]:
        self.issued += 1
        access, refresh = f"access-{self.issued}", f"refresh-{self.issued}"
        self.live_access.add(access)
        self.live_refresh.add(refresh)
        return {
            "access_token": access,
            "refresh_token": refresh,
            "expires_at": (datetime.now(UTC) + self.access_ttl).isoformat(),
            "token_type": "Bearer",
        }

    def _authorized(self, request: httpx.Request) -> bool:
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        if self.reject_next_access:
            self.reject_next_access = False
            return False
        return not self.revoked_grant and token in self.live_access

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        public = {
            "/health": self._health,
            "/api/auth/request-otp": self._request_otp,
            "/api/auth/cli/verify-otp": self._verify_otp,
            "/api/oauth/token": self._refresh,
            "/api/oauth/revoke": self._revoke,
        }
        if path in public:
            return public[path](body)
        if not self._authorized(request):
            return httpx.Response(401, json={"detail": "Invalid bearer token"})
        if path == "/api/auth/whoami":
            return httpx.Response(
                200,
                json={"user_id": "user-1", "client_id": CLI_CLIENT_ID, "scopes": ["profile:read"]},
            )
        if path.startswith("/api/engine/"):
            return httpx.Response(200, json={"operation": path.rsplit("/", 1)[1], "body": body})
        return httpx.Response(404, json={"detail": "Not Found"})

    def _health(self, _body: dict[str, Any]) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok", "environment": self.environment})

    def _request_otp(self, body: dict[str, Any]) -> httpx.Response:
        if body["email"] != EMAIL and "inviteCode" not in body:
            return httpx.Response(409, json={"error": "invite_required"})
        return httpx.Response(200, json={"status": "otp_sent"})

    def _verify_otp(self, body: dict[str, Any]) -> httpx.Response:
        if body != {"email": EMAIL, "token": CODE}:
            return httpx.Response(401, json={"detail": "Invalid or expired code."})
        return httpx.Response(200, json={**self._tokens(), "user_id": "user-1"})

    def _refresh(self, body: dict[str, Any]) -> httpx.Response:
        assert body["client_id"] == CLI_CLIENT_ID
        if self.revoked_grant or body["refresh_token"] not in self.live_refresh:
            return httpx.Response(400, json={"detail": "Invalid refresh token."})
        self.live_refresh.discard(body["refresh_token"])
        return httpx.Response(200, json=self._tokens())

    def _revoke(self, body: dict[str, Any]) -> httpx.Response:
        token = body["token"]
        if body.get("token_type_hint") == "refresh_token":
            revoked = token in self.live_refresh
            self.live_refresh.discard(token)
        else:
            revoked = token in self.live_access and not self.revoked_grant
            self.revoked_grant = self.revoked_grant or revoked
        return httpx.Response(200, json={"revoked": revoked})

    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]


@pytest.fixture
def deployment() -> FakeDeployment:
    return FakeDeployment()


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    return tmp_path / "config" / "fitness-coach" / "credentials.json"


@pytest.fixture
def run(deployment: FakeDeployment, store_path: Path):
    runner = CliRunner()

    def invoke(*args: str, base_url: str | None = BASE, stdin: str | None = None) -> Any:
        prefix = ["--credentials-file", str(store_path)]
        if base_url is not None:
            prefix += ["--base-url", base_url]
        return runner.invoke(
            cli.app,
            [*prefix, *args],
            obj={"transport": deployment.transport()},
            input=stdin,
        )

    return invoke


def _login(run: Any) -> None:
    result = run("login", "--email", EMAIL, "--code", CODE)
    assert result.exit_code == 0, result.output


def test_two_step_login_stores_private_credentials(
    run: Any, deployment: FakeDeployment, store_path: Path
) -> None:
    first = run("--json", "login", "--email", " Athlete@Example.com ")
    assert first.exit_code == 0, first.output
    assert json.loads(first.stdout) == {"status": "code_sent", "email": EMAIL, "base_url": BASE}
    assert not store_path.exists()

    second = run("login", "--email", EMAIL, "--code", CODE)

    assert second.exit_code == 0, second.output
    assert "Signed in to https://coach.example.com as user-1" in second.stdout
    assert stat.S_IMODE(store_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store_path.parent.stat().st_mode) == 0o700
    document = json.loads(store_path.read_text())
    assert list(document["deployments"]) == [BASE]
    assert "/api/auth/cli/verify-otp" in deployment.paths()


def test_interactive_login_prompts_for_the_code(
    run: Any, monkeypatch: pytest.MonkeyPatch, store_path: Path
) -> None:
    monkeypatch.setattr(cli, "_interactive", lambda: True)

    result = run("login", "--email", EMAIL, stdin=f"{CODE}\n")

    assert result.exit_code == 0, result.output
    assert CredentialStore(store_path).load(BASE) is not None


def test_wrong_code_reports_machine_readable_error(run: Any) -> None:
    result = run("--json", "login", "--email", EMAIL, "--code", "000000")

    assert result.exit_code == 1
    assert json.loads(result.stderr) == {
        "error": "http_error",
        "status": 401,
        "detail": "Invalid or expired code.",
    }


def test_new_email_needs_an_invite_code(run: Any) -> None:
    missing = run("login", "--email", "new@example.com")
    assert missing.exit_code == 1
    assert "--invite-code" in missing.stderr

    invited = run("login", "--email", "new@example.com", "--invite-code", "invite")
    assert invited.exit_code == 0, invited.output


@pytest.mark.parametrize("environment", ["production", None])
def test_production_or_unknown_target_requires_confirmation(
    run: Any, deployment: FakeDeployment, environment: str | None
) -> None:
    deployment.environment = environment

    refused = run("--json", "login", "--email", EMAIL)

    assert refused.exit_code == 1
    assert json.loads(refused.stderr)["error"] == "confirmation_required"
    assert "/api/auth/request-otp" not in deployment.paths()
    confirmed = run("login", "--email", EMAIL, "--yes")
    assert confirmed.exit_code == 0, confirmed.output


def test_local_target_skips_the_environment_check(run: Any, deployment: FakeDeployment) -> None:
    result = run("login", "--email", EMAIL, base_url="http://localhost:3000/")

    assert result.exit_code == 0, result.output
    assert "/health" not in deployment.paths()


@pytest.mark.parametrize(
    "base_url",
    ["http://coach.example.com", "https://coach.example.com/api", "ftp://x", "not a url"],
)
def test_rejects_unsafe_or_non_origin_base_urls(run: Any, base_url: str) -> None:
    result = run("--json", "whoami", base_url=base_url)

    assert result.exit_code == 1
    assert json.loads(result.stderr)["error"] == "invalid_base_url"


def test_base_url_is_required(run: Any) -> None:
    result = run("whoami", base_url=None)

    assert result.exit_code == 1
    assert "--base-url" in result.stderr


def test_whoami_and_call_use_the_stored_token(run: Any, deployment: FakeDeployment) -> None:
    _login(run)

    who = run("--json", "whoami")
    assert who.exit_code == 0, who.output
    assert json.loads(who.stdout)["user_id"] == "user-1"

    called = run("--json", "call", "get-athlete-summary")
    assert called.exit_code == 0, called.output
    assert json.loads(called.stdout) == {"operation": "get-athlete-summary", "body": {}}
    engine = deployment.requests[-1]
    assert engine.headers["authorization"] == "Bearer access-1"


def test_call_reads_payload_from_inline_file_and_stdin(run: Any, tmp_path: Path) -> None:
    _login(run)
    payload_file = tmp_path / "body.json"
    payload_file.write_text('{"days": 7}')

    inline = run("--json", "call", "get-recent-activities", "--data", '{"days": 3}')
    from_file = run("--json", "call", "get-recent-activities", "--data", f"@{payload_file}")
    from_stdin = run("--json", "call", "get-recent-activities", "--data", "-", stdin='{"days":1}')

    assert [json.loads(r.stdout)["body"] for r in (inline, from_file, from_stdin)] == [
        {"days": 3},
        {"days": 7},
        {"days": 1},
    ]


@pytest.mark.parametrize(
    ("args", "error"),
    [
        (["call", "get-athlete-summary", "--data", "[1]"], "invalid_input"),
        (["call", "get-athlete-summary", "--data", "{oops"], "invalid_input"),
        (["call", "get-athlete-summary", "--data", "@/no/such/file"], "invalid_input"),
        (["call", "../oauth/revoke"], "invalid_operation"),
        (["call", "Get-Athlete-Summary"], "invalid_operation"),
    ],
)
def test_call_rejects_bad_input_before_any_request(
    run: Any, deployment: FakeDeployment, args: list[str], error: str
) -> None:
    _login(run)
    sent = len(deployment.requests)

    result = run("--json", *args)

    assert result.exit_code == 1
    assert json.loads(result.stderr)["error"] == error
    assert len(deployment.requests) == sent


def test_expired_access_token_is_refreshed_before_the_call(
    run: Any, deployment: FakeDeployment, store_path: Path
) -> None:
    deployment.access_ttl = timedelta(seconds=30)  # inside the refresh skew
    _login(run)

    result = run("--json", "whoami")

    assert result.exit_code == 0, result.output
    assert deployment.paths()[-2:] == ["/api/oauth/token", "/api/auth/whoami"]
    stored = CredentialStore(store_path).load(BASE)
    assert stored is not None
    assert stored.refresh_token == "refresh-2"
    assert stored.user_id == "user-1"


def test_a_401_triggers_one_refresh_and_retry(run: Any, deployment: FakeDeployment) -> None:
    _login(run)
    deployment.reject_next_access = True

    result = run("--json", "whoami")

    assert result.exit_code == 0, result.output
    assert deployment.paths()[-3:] == ["/api/auth/whoami", "/api/oauth/token", "/api/auth/whoami"]


def test_revoked_grant_fails_with_a_relogin_hint(run: Any, deployment: FakeDeployment) -> None:
    _login(run)
    deployment.revoked_grant = True

    result = run("--json", "call", "get-athlete-summary")

    assert result.exit_code == 1
    error = json.loads(result.stderr)
    assert error["error"] == "session_expired"
    assert "login" in error["hint"]


def test_not_logged_in_has_a_login_hint(run: Any) -> None:
    result = run("whoami")

    assert result.exit_code == 1
    assert "Not signed in" in result.stderr
    assert "login --email" in result.stderr


def test_logout_revokes_the_grant_and_forgets_tokens(
    run: Any, deployment: FakeDeployment, store_path: Path
) -> None:
    _login(run)

    result = run("--json", "logout")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["status"] == "revoked"
    assert deployment.revoked_grant
    assert deployment.live_refresh == set()
    assert CredentialStore(store_path).load(BASE) is None
    after = run("--json", "whoami")
    assert json.loads(after.stderr)["error"] == "not_logged_in"


def test_logout_with_expired_access_token_refreshes_then_revokes_grant(
    run: Any, deployment: FakeDeployment
) -> None:
    deployment.access_ttl = timedelta(seconds=0)
    _login(run)

    result = run("--json", "logout")

    assert json.loads(result.stdout)["status"] == "revoked"
    assert deployment.revoked_grant


def test_logout_after_server_side_revocation_still_clears_local_state(
    run: Any, deployment: FakeDeployment, store_path: Path
) -> None:
    deployment.access_ttl = timedelta(seconds=0)
    _login(run)
    deployment.revoked_grant = True

    result = run("--json", "logout")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["status"] == "already_invalid"
    assert CredentialStore(store_path).load(BASE) is None


def test_logout_reports_a_grant_revoked_elsewhere(
    run: Any, deployment: FakeDeployment, store_path: Path
) -> None:
    _login(run)
    deployment.revoked_grant = True  # e.g. revoked from another device; access token still fresh

    result = run("--json", "logout")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["status"] == "already_invalid"
    assert CredentialStore(store_path).load(BASE) is None


@pytest.mark.parametrize(
    ("base_url", "hint_fragment"),
    [
        ("https://app-git-branch.vercel.app", "VERCEL_AUTOMATION_BYPASS_SECRET"),
        ("https://coach.example.com", "in front of the deployment"),
    ],
)
def test_html_401_from_an_edge_proxy_is_not_a_session_problem(
    store_path: Path, base_url: str, hint_fragment: str
) -> None:
    CredentialStore(store_path).save(
        base_url, Credentials("a", "r", datetime.now(UTC) + timedelta(hours=1), "user-1")
    )
    protection_page = httpx.MockTransport(
        lambda _request: httpx.Response(
            401, text="<html>Authentication Required</html>", headers={"content-type": "text/html"}
        )
    )

    result = CliRunner().invoke(
        cli.app,
        ["--credentials-file", str(store_path), "--base-url", base_url, "--json", "whoami"],
        obj={"transport": protection_page},
    )

    assert result.exit_code == 1
    error = json.loads(result.stderr)
    assert error["error"] == "deployment_protected"
    assert hint_fragment in error["hint"]
    # No refresh was attempted, so the stored sign-in is untouched.
    stored = CredentialStore(store_path).load(base_url)
    assert stored is not None
    assert stored.refresh_token == "r"


def test_logout_network_failure_still_clears_local_state(store_path: Path) -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    CredentialStore(store_path).save(
        BASE,
        Credentials("a", "r", datetime.now(UTC) + timedelta(hours=1), "user-1"),
    )
    result = CliRunner().invoke(
        cli.app,
        ["--credentials-file", str(store_path), "--base-url", BASE, "--json", "logout"],
        obj={"transport": httpx.MockTransport(broken)},
    )

    assert result.exit_code == 1
    assert json.loads(result.stderr)["error"] == "network_error"
    assert CredentialStore(store_path).load(BASE) is None


def test_tools_list_needs_no_deployment(run: Any) -> None:
    text = run("tools", "list", base_url=None)
    as_json = run("--json", "tools", "list", base_url=None)

    assert text.exit_code == 0
    assert "get-athlete-summary" in text.stdout
    entries = json.loads(as_json.stdout)
    assert {"name", "description"} == set(entries[0])
    assert [entry["name"] for entry in entries] == sorted(entry["name"] for entry in entries)
