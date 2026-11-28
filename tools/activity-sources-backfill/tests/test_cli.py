"""No live requests: exercise the operator flow through a mocked HTTP transport."""

import json
from collections import deque

import httpx
import pytest
from fitness_coach_activity_sources_backfill import cli
from fitness_coach_activity_sources_backfill.backfill import Settings
from typer.testing import CliRunner

URL = "https://preview.example.test"
SECRET = "private-service-role-key"
ARGS = ["--url", URL, "--confirm-target", "preview.example.test"]
PREFLIGHT = {"ok": True, "source_counts": {"manual": 3}}
DRAINED = {"processed": 0, "drained": True}
FIRST_PASS = {
    "holes": 0,
    "live_source_errors": 0,
    "capture_errors": 0,
    "drained": True,
    "clean_passes": 1,
    "complete": False,
}
COMPLETE = {**FIRST_PASS, "clean_passes": 2, "complete": True}
RUNNER = CliRunner()


@pytest.fixture()
def mocked_rpc(monkeypatch):
    pending = deque()
    requests = []
    original = httpx.Client

    def handle(request):
        requests.append(request)
        assert request.headers["apikey"] == SECRET
        assert request.headers["authorization"] == f"Bearer {SECRET}"
        response = pending.popleft()
        if isinstance(response, Exception):
            raise response
        status, body = response
        return httpx.Response(status, json=body)

    def client(**kwargs):
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        return original(**kwargs, transport=httpx.MockTransport(handle))

    monkeypatch.setenv("BACKFILL_SERVICE_ROLE_KEY", SECRET)
    monkeypatch.setattr(cli.httpx, "Client", client)
    return pending, requests


def _enqueue(pending, *responses):
    pending.extend((200, response) for response in responses)


def test_run_preflights_drains_and_requires_two_passes(mocked_rpc):
    pending, requests = mocked_rpc
    _enqueue(pending, PREFLIGHT, {"processed": 3, "drained": False}, DRAINED, FIRST_PASS, COMPLETE)
    result = RUNNER.invoke(cli.app, [*ARGS, "run", "--batch-size", "3"])
    assert result.exit_code == 0, result.output
    assert [request.url.path.rsplit("_", 1)[1] for request in requests] == [
        "preflight",
        "batch",
        "batch",
        "verify",
        "verify",
    ]
    assert json.loads(requests[1].content) == {"p_batch_size": 3}
    assert json.loads(result.stdout.splitlines()[-1])["complete"] is True
    assert SECRET not in result.output


def test_holes_trigger_resumed_batches_not_a_false_failure(mocked_rpc):
    pending, requests = mocked_rpc
    hole = {**FIRST_PASS, "holes": 1, "live_source_errors": 1, "drained": False, "clean_passes": 0}
    _enqueue(
        pending,
        PREFLIGHT,
        DRAINED,
        hole,
        {"processed": 1, "drained": False},
        DRAINED,
        FIRST_PASS,
        COMPLETE,
    )
    result = RUNNER.invoke(cli.app, [*ARGS, "run"])
    assert result.exit_code == 0, result.output
    assert len(requests) == 7


def test_preflight_failure_never_starts_batch_or_logs_private_details(mocked_rpc):
    pending, requests = mocked_rpc
    pending.append((400, {"message": "athlete-private-row", "details": SECRET}))
    result = RUNNER.invoke(cli.app, [*ARGS, "run"])
    assert result.exit_code == 1
    assert len(requests) == 1
    assert "athlete-private-row" not in result.output
    assert SECRET not in result.output


def test_preflight_false_is_not_success(mocked_rpc):
    pending, requests = mocked_rpc
    _enqueue(pending, {**PREFLIGHT, "ok": False})
    result = RUNNER.invoke(cli.app, [*ARGS, "run"])
    assert result.exit_code == 1
    assert len(requests) == 1


@pytest.mark.parametrize("failure", ["capture_errors", "live_source_errors"])
def test_conflicting_evidence_stops_without_repair(mocked_rpc, failure):
    pending, requests = mocked_rpc
    _enqueue(pending, PREFLIGHT, DRAINED, {**FIRST_PASS, failure: 1, "clean_passes": 0})
    result = RUNNER.invoke(cli.app, [*ARGS, "run"])
    assert result.exit_code == 1
    assert "conflicting evidence" in result.output
    assert len(requests) == 3


def test_lost_response_stops_and_new_invocation_resumes(mocked_rpc):
    pending, requests = mocked_rpc
    _enqueue(pending, PREFLIGHT)
    pending.append(httpx.ReadTimeout(f"private {SECRET}"))
    failed = RUNNER.invoke(cli.app, [*ARGS, "run"])
    assert failed.exit_code == 1
    assert "commit status is unknown" in failed.output
    assert SECRET not in failed.output
    assert len(requests) == 2  # no blind resend
    _enqueue(pending, PREFLIGHT, DRAINED, FIRST_PASS, COMPLETE)
    resumed = RUNNER.invoke(cli.app, [*ARGS, "run"])
    assert resumed.exit_code == 0
    assert len(requests) == 6


def test_batch_budget_bounds_run_without_erasing_checkpoint(mocked_rpc):
    pending, requests = mocked_rpc
    _enqueue(pending, PREFLIGHT, {"processed": 1, "drained": False})
    result = RUNNER.invoke(cli.app, [*ARGS, "run", "--max-batches", "1"])
    assert result.exit_code == 1
    assert "Batch limit reached" in result.output
    assert len(requests) == 2


@pytest.mark.parametrize("value", [0, 10001])
def test_invalid_batch_size_makes_no_requests(mocked_rpc, value):
    _, requests = mocked_rpc
    result = RUNNER.invoke(cli.app, [*ARGS, "run", "--batch-size", str(value)])
    assert result.exit_code == 2
    assert not requests


@pytest.mark.parametrize(
    "body",
    [
        None,
        {},
        {"processed": "0", "drained": True},
        {"processed": 501, "drained": False},
        {"processed": 0, "drained": False},
    ],
)
def test_bad_batch_response_fails_closed(mocked_rpc, body):
    pending, requests = mocked_rpc
    _enqueue(pending, PREFLIGHT, body)
    result = RUNNER.invoke(cli.app, [*ARGS, "run"])
    assert result.exit_code == 1
    assert len(requests) == 2


@pytest.mark.parametrize(
    "body",
    [
        FIRST_PASS,
        {**COMPLETE, "holes": 1},
        {**COMPLETE, "clean_passes": 1},
        {**COMPLETE, "drained": False},
    ],
)
def test_verify_does_not_report_incomplete_or_inconsistent_as_success(mocked_rpc, body):
    pending, _ = mocked_rpc
    _enqueue(pending, body)
    result = RUNNER.invoke(cli.app, [*ARGS, "verify"])
    assert result.exit_code == 1


def test_verify_complete_and_preflight_commands(mocked_rpc):
    pending, _ = mocked_rpc
    _enqueue(pending, COMPLETE, PREFLIGHT)
    for command in ("verify", "preflight"):
        result = RUNNER.invoke(cli.app, [*ARGS, command])
        assert result.exit_code == 0
        assert json.loads(result.stdout)["operation"] in {"verification", "preflight"}


def test_redirect_is_not_followed(mocked_rpc):
    pending, requests = mocked_rpc
    pending.append((307, {"location": "https://untrusted.test"}))
    result = RUNNER.invoke(cli.app, [*ARGS, "preflight"])
    assert result.exit_code == 1
    assert len(requests) == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://remote.test",
        "https://user:password@remote.test",
        "https://remote.test/path",
        "https://remote.test?key=secret",
        "https://remote.test#fragment",
        "not-a-url",
        "https://[",
    ],
)
def test_unsafe_or_invalid_origin_rejected_before_request(mocked_rpc, url):
    _, requests = mocked_rpc
    result = RUNNER.invoke(cli.app, ["--url", url, "--confirm-target", "remote.test", "run"])
    assert result.exit_code == 2
    assert not requests
    assert "password" not in result.output
    assert "key=secret" not in result.output


def test_explicit_target_and_private_env_credentials_required(mocked_rpc, monkeypatch):
    _, requests = mocked_rpc
    mismatch = RUNNER.invoke(cli.app, ["--url", URL, "--confirm-target", "production", "run"])
    assert mismatch.exit_code == 2
    monkeypatch.delenv("BACKFILL_SERVICE_ROLE_KEY")
    missing = RUNNER.invoke(cli.app, [*ARGS, "run"])
    assert missing.exit_code == 2
    assert "BACKFILL_SERVICE_ROLE_KEY" in missing.output
    assert not requests


def test_env_url_and_loopback_supported_without_secret_in_repr(mocked_rpc, monkeypatch):
    pending, _ = mocked_rpc
    monkeypatch.setenv("BACKFILL_SUPABASE_URL", "http://127.0.0.1:54321")
    _enqueue(pending, PREFLIGHT)
    result = RUNNER.invoke(cli.app, ["--confirm-target", "127.0.0.1:54321", "preflight"])
    assert result.exit_code == 0
    assert SECRET not in repr(Settings(URL, SECRET))
