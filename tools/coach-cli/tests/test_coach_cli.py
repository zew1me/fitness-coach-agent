from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import Result

Run = Callable[..., Result]


def _login(api: Any, run: Run, h: Any) -> None:
    api.on("POST", "/api/auth/cli/verify-otp", json=h.bundle())
    assert run("login", "--email", "A@B.c", "--code", "123456").exit_code == 0


# -- login ---------------------------------------------------------------------------------


def test_login_step_one_requests_otp_only(api: Any, run: Run, creds_path: Path, h: Any) -> None:
    api.on("POST", "/api/auth/request-otp", json={"status": "otp_sent"})
    result = run("login", "--email", "A@B.c", "--invite-code", "inv")
    assert result.exit_code == 0
    assert "emailed" in result.output
    assert h.body(api.requests[0]) == {"email": "a@b.c", "inviteCode": "inv"}
    assert len(api.requests) == 1
    assert not creds_path.exists()


def test_login_step_two_stores_credentials_without_printing_tokens(
    api: Any, run: Run, creds_path: Path, h: Any
) -> None:
    api.on("POST", "/api/auth/cli/verify-otp", json=h.bundle())
    result = run("login", "--email", "a@b.c", "--code", "123456")
    assert result.exit_code == 0
    assert "acc-1" not in result.output
    assert "ref-1" not in result.output
    assert oct(creds_path.stat().st_mode & 0o777) == "0o600"
    assert h.BASE in json.loads(creds_path.read_text())["servers"]


def test_login_invite_required(api: Any, run: Run) -> None:
    api.on(
        "POST",
        "/api/auth/request-otp",
        status=409,
        json={"error": "invite_required", "message": "Invite code required."},
    )
    result = run("--json", "login", "--email", "a@b.c")
    assert result.exit_code == 1
    assert json.loads(result.stderr) == {
        "error": "invite_required",
        "status": 409,
        "detail": "Invite code required.",
    }


def test_login_invite_required_human_hint(api: Any, run: Run) -> None:
    api.on("POST", "/api/auth/request-otp", status=409, json={"error": "invite_required"})
    result = run("login", "--email", "a@b.c")
    assert result.exit_code == 1
    assert "--invite-code" in result.stderr


def test_login_bad_code(api: Any, run: Run, creds_path: Path) -> None:
    api.on(
        "POST", "/api/auth/cli/verify-otp", status=401, json={"detail": "Invalid or expired code."}
    )
    result = run("login", "--email", "a@b.c", "--code", "000000")
    assert result.exit_code == 1
    assert "Invalid or expired code." in result.stderr
    assert not creds_path.exists()


def test_base_url_is_required(run: Run) -> None:
    result = run("whoami", base=None)
    assert result.exit_code == 1
    assert "COACH_BASE_URL" in result.stderr


def test_production_login_requires_yes(api: Any, run: Run, h: Any) -> None:
    api.on("POST", "/api/auth/request-otp", json={"status": "otp_sent"})
    refused = run("login", "--email", "a@b.c", base="https://coach.example.com")
    assert refused.exit_code == 1
    assert "--yes" in refused.stderr
    assert not api.requests
    allowed = run("login", "--email", "a@b.c", "--yes", base="https://coach.example.com")
    assert allowed.exit_code == 0


def test_preview_login_requires_yes(api: Any, run: Run) -> None:
    refused = run("login", "--email", "a@b.c", auto_yes=False)
    assert refused.exit_code == 1
    assert "--yes" in refused.stderr
    assert not api.requests


def test_otp_continuation_hint_keeps_root_options_before_login(
    api: Any, run: Run, creds_path: Path, h: Any
) -> None:
    api.on("POST", "/api/auth/request-otp", json={"status": "otp_sent"})
    result = run("login", "--email", "a@b.c")
    assert result.exit_code == 0
    expected = (
        f"coach --base-url {h.BASE} --credentials-file {creds_path} "
        "login --email a@b.c --code <code> --yes"
    )
    assert expected in result.output


def test_otp_continuation_hint_json_flag_and_local_target(api: Any, run: Run) -> None:
    api.on("POST", "/api/auth/request-otp", json={"status": "otp_sent"})
    result = run("--json", "login", "--email", "a@b.c", base="http://localhost:3000")
    assert result.exit_code == 0
    assert "otp_sent" in result.output
    human = run("login", "--email", "a@b.c", base="http://localhost:3000")
    assert "--base-url http://localhost:3000" in human.output
    assert "--json" not in human.output
    assert "--yes" not in human.output


def test_malformed_base_url_yields_structured_json_error(run: Run) -> None:
    result = run("--json", "whoami", base="https://example.com:bad")
    assert result.exit_code == 1
    assert '"error":"invalid_base_url"' in result.stderr


def test_localhost_login_needs_no_yes(api: Any, run: Run) -> None:
    api.on("POST", "/api/auth/request-otp", json={"status": "otp_sent"})
    assert run("login", "--email", "a@b.c", base="http://localhost:3000").exit_code == 0


def test_remote_http_is_refused(api: Any, run: Run) -> None:
    result = run("whoami", base="http://coach.example.com")
    assert result.exit_code == 1
    assert "https" in result.stderr
    assert not api.requests


# -- whoami / logout / tools ---------------------------------------------------------------


def test_whoami_human_and_json(api: Any, run: Run, h: Any) -> None:
    _login(api, run, h)
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
    _login(api, run, h)
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


def test_call_default_body_is_empty_object(api: Any, run: Run, h: Any) -> None:
    _login(api, run, h)
    api.on("POST", "/api/engine/get-athlete-summary", json={"ok": True})
    result = run("--json", "call", "get-athlete-summary")
    assert result.exit_code == 0
    assert result.output.strip() == '{"ok":true}'
    request = api.to("/api/engine/get-athlete-summary")[0]
    assert h.body(request) == {}
    assert request.headers["authorization"] == "Bearer acc-1"


def test_call_inline_file_and_stdin_data(api: Any, run: Run, tmp_path: Path, h: Any) -> None:
    _login(api, run, h)
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
def test_call_bad_data(api: Any, run: Run, h: Any, data: str) -> None:
    _login(api, run, h)
    result = run("--json", "call", "x-op", "--data", data)
    assert result.exit_code == 1
    assert json.loads(result.stderr)["error"] == "invalid_data"


def test_call_server_error_json(api: Any, run: Run, h: Any) -> None:
    _login(api, run, h)
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


def test_wellness_push_from_stdin(api: Any, run: Run, h: Any) -> None:
    _login(api, run, h)
    api.on("POST", "/api/engine/save-recovery-data", json={"saved": [], "count": 2})
    result = run("wellness", "push", "-", input=json.dumps(SIDECAR))
    assert result.exit_code == 0
    assert "Saved 2" in result.output
    entries = h.body(api.to("/api/engine/save-recovery-data")[0])["entries"]
    assert [e["source"] for e in entries] == ["garmin", "garmin"]
    assert entries[0]["sleep_score"] == 82


def test_wellness_push_from_file_and_default_stdin(
    api: Any, run: Run, tmp_path: Path, h: Any
) -> None:
    _login(api, run, h)
    api.on("POST", "/api/engine/save-recovery-data", json={"count": 2})
    export = tmp_path / "w.json"
    export.write_text(json.dumps(SIDECAR))
    assert run("wellness", "push", str(export)).exit_code == 0
    assert run("wellness", "push", input=json.dumps(SIDECAR)).exit_code == 0


def test_wellness_push_nothing_to_push(api: Any, run: Run, h: Any) -> None:
    _login(api, run, h)
    result = run("--json", "wellness", "push", "-", input='[{"log_date": "2026-08-15"}]')
    assert result.exit_code == 1
    assert json.loads(result.stderr)["error"] == "nothing_to_push"
    assert not api.to("/api/engine/save-recovery-data")


def test_wellness_push_warns_about_dropped_fields(api: Any, run: Run, h: Any) -> None:
    _login(api, run, h)
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


def test_upload_three_call_sequence(api: Any, run: Run, tmp_path: Path, h: Any) -> None:
    _login(api, run, h)
    _upload_routes(api)
    api.on("POST", "/api/engine/process-uploaded-file", json={"kind": "activity", "id": "a1"})
    ride = tmp_path / "ride.fit"
    ride.write_bytes(b"FITDATA")
    result = run("activities", "upload", str(ride))
    assert result.exit_code == 0
    assert "saved activity" in result.output
    assert [r.url.path for r in api.requests[1:]] == [
        "/api/chat/attachments/presign",
        "/api/chat/attachments/upload",
        "/api/engine/process-uploaded-file",
    ]
    presign, upload, process = api.requests[1:]
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
    api: Any, run: Run, tmp_path: Path, h: Any
) -> None:
    _login(api, run, h)
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


def test_upload_server_failure_is_per_file(api: Any, run: Run, tmp_path: Path, h: Any) -> None:
    _login(api, run, h)
    api.on("POST", "/api/chat/attachments/presign", status=400, json={"detail": "not supported"})
    ride = tmp_path / "ride.fit"
    ride.write_bytes(b"x")
    result = run("activities", "upload", str(ride))
    assert result.exit_code == 1
    assert "failed" in result.output
    assert "not supported" in result.output
