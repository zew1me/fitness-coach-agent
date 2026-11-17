from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fitness_coach_cli import cli
from fitness_coach_cli.credentials import Credentials, CredentialStore
from typer.testing import Result

Run = Callable[..., Result]


def _login(api: Any, run: Run, h: Any, creds_path: Path) -> None:
    """Seed stored credentials (far-future expiry so the real clock never triggers a refresh)."""
    del api, run
    CredentialStore(creds_path).save(
        h.BASE, Credentials("acc-1", "ref-1", time.time() + 10 * 365 * 86400, "user-1")
    )


# -- login ---------------------------------------------------------------------------------


def test_base_url_is_required(run: Run) -> None:
    result = run("whoami", base=None)
    assert result.exit_code == 1
    assert "COACH_BASE_URL" in result.stderr


def _queue_device_login(api: Any, h: Any) -> None:
    api.on(
        "POST",
        "/api/oauth/device/code",
        json={
            "device_code": "dev",
            "user_code": "BCDF-GHJK",
            "verification_uri": f"{h.BASE}/device",
            "verification_uri_complete": f"{h.BASE}/device?c=1",
            "expires_in": 600,
            "interval": 1,
        },
    )
    api.on("POST", "/api/oauth/token", json=h.bundle())


def test_production_login_requires_yes(
    api: Any, run: Run, h: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_sleep", lambda _s: None)
    _queue_device_login(api, h)
    refused = run("login", base="https://coach.example.com")
    assert refused.exit_code == 1
    assert "--yes" in refused.stderr
    assert not api.requests
    allowed = run("login", "--yes", "--no-browser", base="https://coach.example.com")
    assert allowed.exit_code == 0


def test_preview_login_requires_yes(api: Any, run: Run) -> None:
    refused = run("login", auto_yes=False)
    assert refused.exit_code == 1
    assert "--yes" in refused.stderr
    assert not api.requests


def test_login_has_no_email_code_options(api: Any, run: Run) -> None:
    for flag in ("--email", "--code", "--invite-code"):
        result = run("login", flag, "x")
        assert result.exit_code != 0
        assert not api.requests


def test_malformed_base_url_yields_structured_json_error(run: Run) -> None:
    result = run("--json", "whoami", base="https://example.com:bad")
    assert result.exit_code == 1
    assert '"error":"invalid_base_url"' in result.stderr


def test_localhost_login_needs_no_yes(
    api: Any, run: Run, h: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_sleep", lambda _s: None)
    _queue_device_login(api, h)
    assert run("login", "--no-browser", base="http://localhost:3000").exit_code == 0


def test_remote_http_is_refused(api: Any, run: Run) -> None:
    result = run("whoami", base="http://coach.example.com")
    assert result.exit_code == 1
    assert "https" in result.stderr
    assert not api.requests


# -- whoami / logout / tools ---------------------------------------------------------------


def test_whoami_human_and_json(api: Any, run: Run, h: Any, creds_path: Path) -> None:
    _login(api, run, h, creds_path)
    api.on(
        "GET",
        "/api/auth/whoami",
        json={"user_id": "user-1", "client_id": "fitness-coach-cli", "scopes": ["a", "b"]},
    )
    assert "user_id=user-1" in run("whoami").output
    assert json.loads(run("--json", "whoami").output)["scopes"] == ["a", "b"]


def test_whoami_without_login_fails(run: Run) -> None:
    result = run("whoami")
    assert result.exit_code == 1
    assert "coach --base-url" in result.stderr and "login" in result.stderr


def test_logout_revokes_and_deletes(api: Any, run: Run, creds_path: Path, h: Any) -> None:
    _login(api, run, h, creds_path)
    api.on("POST", "/api/oauth/revoke", json={"revoked": True})
    result = run("logout")
    assert result.exit_code == 0
    assert len(api.to("/api/oauth/revoke")) == 2
    assert not creds_path.exists()


def test_tools_list(run: Run) -> None:
    result = run("tools", "list")
    assert result.exit_code == 0
    assert "save-recovery-data" in result.output
    names = [item["operation"] for item in json.loads(run("--json", "tools", "list").output)]
    assert "get-athlete-summary" in names


# -- call ----------------------------------------------------------------------------------


def test_call_default_body_is_empty_object(api: Any, run: Run, h: Any, creds_path: Path) -> None:
    _login(api, run, h, creds_path)
    api.on("POST", "/api/engine/get-athlete-summary", json={"ok": True})
    result = run("--json", "call", "get-athlete-summary")
    assert result.exit_code == 0
    assert result.output.strip() == '{"ok":true}'
    request = api.to("/api/engine/get-athlete-summary")[0]
    assert h.body(request) == {}
    assert request.headers["authorization"] == "Bearer acc-1"


def test_call_inline_file_and_stdin_data(
    api: Any, run: Run, tmp_path: Path, h: Any, creds_path: Path
) -> None:
    _login(api, run, h, creds_path)
    api.on("POST", "/api/engine/get-recent-activities", json={"activities": []})
    (tmp_path / "body.json").write_text('{"limit": 5}')
    assert run("call", "get-recent-activities", "--data", '{"limit": 3}').exit_code == 0
    assert (
        run("call", "get-recent-activities", "--data", f"@{tmp_path / 'body.json'}").exit_code == 0
    )
    assert run("call", "get-recent-activities", "--data", "-", input='{"limit": 9}').exit_code == 0
    sent = [h.body(r) for r in api.to("/api/engine/get-recent-activities")]
    assert sent == [{"limit": 3}, {"limit": 5}, {"limit": 9}]


@pytest.mark.parametrize("data", ["{nope", "[1]", "@/does/not/exist.json"])
def test_call_bad_data(api: Any, run: Run, h: Any, data: str, creds_path: Path) -> None:
    _login(api, run, h, creds_path)
    result = run("--json", "call", "x-op", "--data", data)
    assert result.exit_code == 1
    assert json.loads(result.stderr)["error"] == "invalid_data"


def test_call_server_error_json(api: Any, run: Run, h: Any, creds_path: Path) -> None:
    _login(api, run, h, creds_path)
    api.on("POST", "/api/engine/adjust-plan", status=422, json={"detail": [{"msg": "bad"}]})
    result = run("--json", "call", "adjust-plan")
    assert result.exit_code == 1
    assert json.loads(result.stderr) == {
        "error": "http_422",
        "status": 422,
        "detail": [{"msg": "bad"}],
    }


# -- wellness ------------------------------------------------------------------------------

SIDECAR = [
    {"log_date": "2026-08-15", "sleep_score": 82, "hrv_ms": 44, "body_battery": 78},
    {"log_date": "2026-08-14", "resting_hr_bpm": 54},
]


def test_wellness_push_from_stdin(api: Any, run: Run, h: Any, creds_path: Path) -> None:
    _login(api, run, h, creds_path)
    api.on("POST", "/api/engine/save-recovery-data", json={"saved": [], "count": 2})
    result = run("wellness", "push", "-", input=json.dumps(SIDECAR))
    assert result.exit_code == 0
    assert "Saved 2" in result.output
    entries = h.body(api.to("/api/engine/save-recovery-data")[0])["entries"]
    assert [e["source"] for e in entries] == ["garmin", "garmin"]
    assert entries[0]["sleep_score"] == 82


def test_wellness_push_from_file_and_default_stdin(
    api: Any, run: Run, tmp_path: Path, h: Any, creds_path: Path
) -> None:
    _login(api, run, h, creds_path)
    api.on("POST", "/api/engine/save-recovery-data", json={"count": 2})
    export = tmp_path / "w.json"
    export.write_text(json.dumps(SIDECAR))
    assert run("wellness", "push", str(export)).exit_code == 0
    assert run("wellness", "push", input=json.dumps(SIDECAR)).exit_code == 0


def test_wellness_push_nothing_to_push(api: Any, run: Run, h: Any, creds_path: Path) -> None:
    _login(api, run, h, creds_path)
    result = run("--json", "wellness", "push", "-", input='[{"log_date": "2026-08-15"}]')
    assert result.exit_code == 1
    assert json.loads(result.stderr)["error"] == "nothing_to_push"
    assert not api.to("/api/engine/save-recovery-data")


def test_wellness_push_warns_about_dropped_fields(
    api: Any, run: Run, h: Any, creds_path: Path
) -> None:
    _login(api, run, h, creds_path)
    api.on("POST", "/api/engine/save-recovery-data", json={"count": 1})
    payload = json.dumps([{"log_date": "2026-08-15", "hrv_ms": 44, "respiration": 14}])
    result = run("wellness", "push", "-", input=payload)
    assert result.exit_code == 0
    assert "respiration" in result.stderr


# -- activities ----------------------------------------------------------------------------


def _upload_routes(api: Any, user: str = "user-1") -> None:
    api.on(
        "POST",
        "/api/chat/attachments/presign",
        json={
            "object_key": f"users/{user}/chat-attachment/2026/ride.fit",
            "public_url": "https://files.example/ride.fit",
            "upload_url": "",
            "headers": {},
        },
    )
    api.on("POST", "/api/chat/attachments/upload", json={"object_key": "k"})


def test_upload_three_call_sequence(
    api: Any, run: Run, tmp_path: Path, h: Any, creds_path: Path
) -> None:
    _login(api, run, h, creds_path)
    _upload_routes(api)
    api.on("POST", "/api/engine/process-uploaded-file", json={"kind": "activity", "id": "a1"})
    ride = tmp_path / "ride.fit"
    ride.write_bytes(b"FITDATA")
    result = run("activities", "upload", str(ride))
    assert result.exit_code == 0
    assert "saved activity" in result.output
    assert [r.url.path for r in api.requests] == [
        "/api/chat/attachments/presign",
        "/api/chat/attachments/upload",
        "/api/engine/process-uploaded-file",
    ]
    presign, upload, process = api.requests
    assert h.body(presign) == {
        "content_length": 7,
        "content_type": "application/vnd.garmin.fit",
        "filename": "ride.fit",
        "purpose": "chat-attachment",
    }
    assert upload.headers["content-type"].startswith("multipart/form-data")
    assert b"users/user-1/chat-attachment/2026/ride.fit" in upload.content
    assert b"FITDATA" in upload.content
    assert h.body(process) == {
        "content_type": "application/vnd.garmin.fit",
        "filename": "ride.fit",
        "object_key": "users/user-1/chat-attachment/2026/ride.fit",
        "public_url": "https://files.example/ride.fit",
    }


def test_upload_partial_failure_continues_and_exits_nonzero(
    api: Any, run: Run, tmp_path: Path, h: Any, creds_path: Path
) -> None:
    _login(api, run, h, creds_path)
    _upload_routes(api)
    api.on("POST", "/api/engine/process-uploaded-file", json={"kind": "course"})
    course = tmp_path / "route.gpx"
    course.write_text("<gpx/>")
    notes = tmp_path / "notes.txt"
    notes.write_text("hi")
    missing = tmp_path / "gone.tcx"
    result = run("--json", "activities", "upload", str(notes), str(missing), str(course))
    assert result.exit_code == 1
    outcomes = json.loads(result.output)
    assert [o["outcome"] for o in outcomes] == ["failed", "failed", "course"]
    assert outcomes[0]["error"] == "unsupported_file"
    assert outcomes[1]["error"] == "file_unreadable"


def test_upload_server_failure_is_per_file(
    api: Any, run: Run, tmp_path: Path, h: Any, creds_path: Path
) -> None:
    _login(api, run, h, creds_path)
    api.on("POST", "/api/chat/attachments/presign", status=400, json={"detail": "not supported"})
    ride = tmp_path / "ride.fit"
    ride.write_bytes(b"x")
    result = run("activities", "upload", str(ride))
    assert result.exit_code == 1
    assert "failed" in result.output
    assert "not supported" in result.output
