import base64
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Literal, TypedDict, cast

import pytest
from httpx import ASGITransport, AsyncClient, HTTPError
from postgrest.exceptions import APIError as PostgRESTAPIError

import api.index as api_index
from backend.models.athlete import (
    AthleteProfile,
    RecoveryLog,
    ScheduleAvailability,
    ScheduleOverride,
    SportThreshold,
)
from backend.models.auth import (
    BrowserSessionContext,
    BrowserTokenResponse,
    OAuthRevokeRequest,
    OAuthTokenRequest,
    UserContext,
)
from backend.models.chat import ChatModelState, ChatModelStateReplaceRequest
from backend.models.training import Activity, DailyLoadSnapshot, Goal, PlanWorkout, TrainingPlan
from backend.repos.oauth_repo import OAuthRepositoryNotConfiguredError
from backend.repos.supabase_repo import RecordNotFoundError, RepositoryNotConfiguredError
from backend.services.auth import AuthService


class GrantRecord(TypedDict):
    id: str
    user_id: str
    client_id: str
    redirect_uri: str
    scopes: list[str]
    created_at: datetime
    updated_at: datetime
    revoked_at: datetime | None


class AuthorizationCodeRecord(TypedDict):
    id: str
    grant_id: str
    user_id: str
    client_id: str
    redirect_uri: str
    scopes: list[str]
    code_challenge: str
    code_challenge_method: str
    expires_at: datetime
    consumed_at: datetime | None
    created_at: datetime


class RefreshTokenRecord(TypedDict):
    id: str
    grant_id: str
    user_id: str
    client_id: str
    scopes: list[str]
    expires_at: datetime
    revoked_at: datetime | None
    created_at: datetime
    rotated_from_id: str | None


class EngineRepository:
    async def get_athlete_profile(self, user_id: str) -> AthleteProfile:
        return AthleteProfile(
            user_id=user_id,
            display_name="Athlete One",
            birth_date=datetime.fromisoformat("1990-04-01T00:00:00+00:00").date(),
            biological_sex="not_specified",
            primary_sports=["running", "cycling"],
            weekly_available_hours=7.5,
            coaching_state="active",
        )

    async def get_active_thresholds(self, user_id: str) -> list[SportThreshold]:
        return [
            SportThreshold(
                id="threshold-1",
                user_id=user_id,
                sport="cycling",
                lt2_power_watts=250,
                lt1_power_watts=188,
                confidence="medium",
            )
        ]

    async def list_active_goals(self, user_id: str) -> list[Goal]:
        return [
            Goal(
                id="goal-1",
                user_id=user_id,
                goal_type="event",
                sport="running",
                title="Hill climb race",
                target_date=datetime.fromisoformat("2026-07-01T00:00:00+00:00").date(),
                course_distance_meters=14_000,
                course_elevation_gain_meters=700,
                priority=1,
            )
        ]

    async def get_latest_load(
        self, user_id: str, sport: str | None = None
    ) -> DailyLoadSnapshot | None:
        return DailyLoadSnapshot(
            user_id=user_id,
            snapshot_date=datetime.fromisoformat("2026-04-01T00:00:00+00:00").date(),
            sport=sport,
            daily_tss=60,
            ctl=42,
            atl=50,
            tsb=-8,
        )

    async def list_recovery_logs(self, user_id: str, *, limit: int = 14) -> list[RecoveryLog]:
        return [
            RecoveryLog(
                id="recovery-1",
                user_id=user_id,
                log_date=datetime.fromisoformat("2026-04-01T00:00:00+00:00").date(),
                sleep_score=82,
                hrv_ms=55,
            )
        ][:limit]

    async def get_schedule(self, user_id: str) -> ScheduleAvailability:
        return ScheduleAvailability(
            id="schedule-1",
            user_id=user_id,
            weekly_pattern={"monday": {"available": True, "max_hours": 1.0}},
        )

    async def get_active_plan(self, user_id: str):
        return None

    async def list_activities(self, user_id: str, *, sport=None, limit: int = 50):
        return []

    async def create_activity(self, activity: Activity) -> Activity:
        return activity.model_copy(update={"id": "activity-1"})

    async def list_plan_workouts_between(self, user_id: str, *, start, end) -> list[PlanWorkout]:
        return []

    async def list_schedule_overrides_between(self, user_id: str, *, start, end):
        return []

    async def delete_future_scheduled_workouts(self, user_id: str, plan_id: str, from_date) -> int:
        return 0

    async def create_training_plan(self, plan: TrainingPlan) -> TrainingPlan:
        return plan.model_copy(update={"id": "plan-1"})

    async def create_plan_workouts(self, workouts: list[PlanWorkout]) -> list[PlanWorkout]:
        return [
            w.model_copy(update={"id": f"workout-{i}"}) for i, w in enumerate(workouts, start=1)
        ]

    async def get_activity(self, user_id: str, activity_id: str) -> Activity:
        return Activity(
            id=activity_id,
            user_id=user_id,
            sport="cycling",
            activity_date=datetime.fromisoformat("2026-06-13T00:00:00+00:00").date(),
            source="fit_upload",
            activity_summary={
                "schema": "activity_summary_v1",
                "session": {"sport": "cycling"},
                "fueling": {},
                "subjective": {},
                "data_quality": {"source": "fit_upload"},
            },
            raw_extraction={"filename": "race.fit"},
        )

    async def update_activity(self, activity: Activity) -> Activity:
        return activity

    async def match_plan_workout_to_activity(
        self,
        *,
        user_id: str,
        workout_id: str,
        activity_id: str,
        completion_source: Literal["auto_matched", "athlete_confirmed", "coach_confirmed"],
    ) -> PlanWorkout:
        return PlanWorkout(
            id=workout_id,
            plan_id="plan-1",
            user_id=user_id,
            workout_date=datetime.fromisoformat("2026-06-13T00:00:00+00:00").date(),
            day_of_week=5,
            week_number=1,
            sport="cycling",
            title="Matched ride",
            workout_type="endurance",
            status="completed",
            actual_activity_id=activity_id,
            completion_source=completion_source,
        )

    async def resolve_plan_workout_atomic(
        self,
        *,
        user_id: str,
        workout_id: str,
        outcome: str,
        activity_id: str | None,
        source: Literal["athlete", "coach"],
    ) -> PlanWorkout:
        return PlanWorkout(
            id=workout_id,
            plan_id="plan-1",
            user_id=user_id,
            workout_date=datetime.fromisoformat("2026-06-13T00:00:00+00:00").date(),
            day_of_week=5,
            week_number=1,
            sport="cycling",
            title="Resolved ride",
            workout_type="endurance",
            status=outcome,
            actual_activity_id=activity_id,
            completion_source=cast(
                Literal["athlete_confirmed", "coach_confirmed"], f"{source}_confirmed"
            ),
        )

    async def upsert_load_snapshots(self, user_id: str, snapshots: list[dict], sport=None) -> None:
        self.snapshots = snapshots


_DEPENDENCY_OVERRIDE_MISSING = object()


def _override_require_user_context(user_context: UserContext):
    previous = api_index.app.dependency_overrides.get(
        api_index.require_user_context,
        _DEPENDENCY_OVERRIDE_MISSING,
    )
    api_index.app.dependency_overrides[api_index.require_user_context] = lambda: user_context

    def restore() -> None:
        if previous is _DEPENDENCY_OVERRIDE_MISSING:
            api_index.app.dependency_overrides.pop(api_index.require_user_context, None)
        else:
            api_index.app.dependency_overrides[api_index.require_user_context] = cast(
                Callable[..., Any],
                previous,
            )

    return restore


class InMemoryOAuthRepository:
    def __init__(self) -> None:
        self.grants: dict[str, GrantRecord] = {}
        self.codes: dict[str, AuthorizationCodeRecord] = {}
        self.refresh_tokens: dict[str, RefreshTokenRecord] = {}

    def get_active_grant(self, *, user_id: str, client_id: str, redirect_uri: str):
        for grant in self.grants.values():
            if (
                grant["user_id"] == user_id
                and grant["client_id"] == client_id
                and grant["redirect_uri"] == redirect_uri
                and grant["revoked_at"] is None
            ):
                return type("Grant", (), grant)()
        return None

    def get_grant_by_id(self, grant_id: str):
        grant = self.grants.get(grant_id)
        if grant is None:
            return None
        return type("Grant", (), grant)()

    def upsert_grant(self, *, user_id: str, client_id: str, redirect_uri: str, scopes: list[str]):
        existing = self.get_active_grant(
            user_id=user_id, client_id=client_id, redirect_uri=redirect_uri
        )
        now = datetime.now().astimezone()
        if existing is None:
            grant_id = f"grant-{len(self.grants) + 1}"
            self.grants[grant_id] = {
                "id": grant_id,
                "user_id": user_id,
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "scopes": scopes,
                "created_at": now,
                "updated_at": now,
                "revoked_at": None,
            }
            return type("Grant", (), self.grants[grant_id])()

        current = self.grants[existing.id]
        current["scopes"] = sorted(set(current["scopes"]).union(scopes))
        current["updated_at"] = now
        return type("Grant", (), current)()

    def create_authorization_code(
        self,
        *,
        grant_id: str,
        user_id: str,
        client_id: str,
        redirect_uri: str,
        scopes: list[str],
        code_challenge: str,
        code_challenge_method: str,
    ) -> str:
        code = f"code-{len(self.codes) + 1}"
        self.codes[code] = {
            "id": code,
            "grant_id": grant_id,
            "user_id": user_id,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scopes": scopes,
            "code_challenge": code_challenge,
            "code_challenge_method": code_challenge_method,
            "expires_at": datetime.max.replace(tzinfo=UTC),
            "consumed_at": None,
            "created_at": datetime.now(UTC),
        }
        return code

    def get_authorization_code(self, raw_code: str):
        code = self.codes.get(raw_code)
        if code is None:
            return None
        return type("AuthorizationCode", (), code)()

    def consume_authorization_code(self, raw_code: str):
        code = self.codes.get(raw_code)
        if code is None:
            raise ValueError("Invalid authorization code.")
        if code["consumed_at"] is not None:
            raise ValueError("Authorization code is no longer valid.")
        code["consumed_at"] = datetime.now().astimezone()
        return type("AuthorizationCode", (), code)()

    def create_refresh_token(
        self,
        *,
        grant_id: str,
        user_id: str,
        client_id: str,
        scopes: list[str],
        rotated_from_id: str | None = None,
    ) -> str:
        token = f"refresh-{len(self.refresh_tokens) + 1}"
        self.refresh_tokens[token] = {
            "id": token,
            "grant_id": grant_id,
            "user_id": user_id,
            "client_id": client_id,
            "scopes": scopes,
            "expires_at": datetime.max.replace(tzinfo=UTC),
            "revoked_at": None,
            "created_at": datetime.now(UTC),
            "rotated_from_id": rotated_from_id,
        }
        return token

    def get_refresh_token(self, raw_token: str):
        token = self.refresh_tokens.get(raw_token)
        if token is None:
            return None
        return type("RefreshToken", (), token)()

    def rotate_refresh_token(self, raw_token: str):
        current = self.refresh_tokens.get(raw_token)
        if current is None:
            raise ValueError("Invalid refresh token.")
        current["revoked_at"] = datetime.now().astimezone()
        replacement = self.create_refresh_token(
            grant_id=current["grant_id"],
            user_id=current["user_id"],
            client_id=current["client_id"],
            scopes=current["scopes"],
            rotated_from_id=current["id"],
        )
        return type("RefreshToken", (), current)(), replacement

    def revoke_refresh_token(self, raw_token: str) -> bool:
        current = self.refresh_tokens.get(raw_token)
        if current is None or current["revoked_at"] is not None:
            return False
        current["revoked_at"] = datetime.now().astimezone()
        return True

    def revoke_grant(self, grant_id: str) -> bool:
        grant = self.grants.get(grant_id)
        if grant is None:
            return False
        grant["revoked_at"] = datetime.now().astimezone()
        return True


class ModelStateChatService:
    def __init__(self) -> None:
        now = datetime(2026, 6, 20, tzinfo=UTC)
        self.state = ChatModelState(
            created_at=now,
            thread_id="thread-1",
            updated_at=now,
            user_id="athlete-1",
            version=2,
        )

    async def get_model_state(self, user_id: str) -> ChatModelState:
        assert user_id == "athlete-1"
        return self.state

    async def replace_model_state(
        self, user_id: str, replacement: ChatModelStateReplaceRequest
    ) -> ChatModelState:
        assert user_id == "athlete-1"
        if replacement.lease_id != self.state.lease_id:
            raise ValueError("Chat turn lease is no longer owned by this request.")
        if replacement.expected_version != self.state.version:
            raise ValueError("Chat model state version conflict.")
        self.state = self.state.model_copy(
            update={
                "items": replacement.items,
                "coaching_memory": replacement.coaching_memory,
                "compaction_metadata": replacement.compaction_metadata,
                "version": self.state.version + 1,
            }
        )
        return self.state

    async def acquire_turn_lease(
        self, user_id: str, lease_id: str, *, ttl_seconds: int
    ) -> ChatModelState:
        assert user_id == "athlete-1"
        assert ttl_seconds == 60
        self.state = self.state.model_copy(
            update={"lease_id": lease_id, "version": self.state.version + 1}
        )
        return self.state

    async def release_turn_lease(self, user_id: str, lease_id: str) -> ChatModelState:
        assert user_id == "athlete-1"
        assert lease_id == self.state.lease_id
        self.state = self.state.model_copy(update={"lease_id": None})
        return self.state


class FakeAuthService(AuthService):
    def create_browser_session(self, supabase_access_token: str) -> BrowserSessionContext:
        if supabase_access_token != "supabase-access-token":
            raise OAuthRepositoryNotConfiguredError("Unable to verify browser session.")
        return BrowserSessionContext(user_id="athlete-1", email="athlete@example.com")


async def test_protected_profile_requires_bearer_token() -> None:
    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/api/engine/get-athlete-summary")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_chat_attachments_presign_requires_bearer_token() -> None:
    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/chat/attachments/presign",
            json={
                "filename": "garage-test.txt",
                "content_type": "text/plain",
                "content_length": 1,
                "purpose": "chat-attachment",
            },
        )

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing bearer token"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content_type",
    [
        "application/pdf",
        "text/plain",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ],
)
async def test_chat_attachments_presign_rejects_unsupported_type(content_type: str) -> None:
    restore_override = _override_require_user_context(
        UserContext(
            user_id="athlete-1",
            scopes=["profile:read"],
            client_id="test-client",
            grant_id="grant-1",
        )
    )
    try:
        transport = ASGITransport(app=api_index.app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/chat/attachments/presign",
                json={
                    "filename": "file",
                    "content_type": content_type,
                    "content_length": 1024,
                    "purpose": "chat-attachment",
                },
            )
    finally:
        restore_override()

    assert response.status_code == 400
    assert content_type in response.json()["detail"]


_ZIP_TEST_USER = UserContext(
    user_id="athlete-1",
    scopes=["profile:read"],
    client_id="test-client",
    grant_id="grant-1",
)

_SAMPLE_GPX = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><trkseg>
    <trkpt lat="37.0" lon="-122.0"><ele>10</ele><time>2026-04-19T10:00:00Z</time></trkpt>
    <trkpt lat="37.0" lon="-122.001"><ele>12</ele><time>2026-04-19T10:01:00Z</time></trkpt>
  </trkseg></trk>
</gpx>"""

_SAMPLE_TCX = b"""<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
  <Activities>
    <Activity Sport="Running">
      <Id>2026-04-19T10:00:00Z</Id>
      <Lap StartTime="2026-04-19T10:00:00Z">
        <TotalTimeSeconds>60</TotalTimeSeconds>
        <DistanceMeters>200</DistanceMeters>
      </Lap>
    </Activity>
  </Activities>
</TrainingCenterDatabase>"""


def _make_zip(members: dict[str, bytes]) -> bytes:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


async def _post_process_zip(zip_bytes: bytes, monkeypatch) -> dict[str, Any]:
    async def mock_download_file_bytes(*, user_id: str, object_key: str) -> bytes:
        return zip_bytes

    monkeypatch.setattr("api.index.r2_service.download_file_bytes", mock_download_file_bytes)
    # Zip activities persist like the single-file path, so create_activity must resolve;
    # EngineRepository stubs it (id "activity-1") and returns no plannable matches.
    monkeypatch.setattr(api_index, "repo", EngineRepository())
    restore_override = _override_require_user_context(_ZIP_TEST_USER)
    try:
        transport = ASGITransport(app=api_index.app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/engine/process-uploaded-zip",
                json={
                    "content_type": "application/zip",
                    "filename": "export.zip",
                    "object_key": "users/athlete-1/chat-attachment/2024/01/01/export.zip",
                    "public_url": "https://cdn.example.com/export.zip",
                },
            )
    finally:
        restore_override()
    assert response.status_code == 200
    return response.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("content_type", ["application/zip", "application/x-zip-compressed"])
async def test_chat_attachments_presign_accepts_zip(content_type: str, monkeypatch) -> None:
    from backend.models.storage import PresignUploadResponse

    def mock_create_presigned_upload(*, user_id: str, request) -> PresignUploadResponse:
        return PresignUploadResponse(
            upload_url="https://r2.example.com/upload",
            object_key="users/athlete-1/chat-attachment/2024/01/01/export.zip",
            public_url="https://cdn.example.com/export.zip",
            headers={"Content-Type": content_type},
        )

    monkeypatch.setattr(
        "api.index.r2_service.create_presigned_upload", mock_create_presigned_upload
    )
    restore_override = _override_require_user_context(_ZIP_TEST_USER)
    try:
        transport = ASGITransport(app=api_index.app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/chat/attachments/presign",
                json={
                    "filename": "export.zip",
                    "content_type": content_type,
                    "content_length": 1024,
                    "purpose": "chat-attachment",
                },
            )
    finally:
        restore_override()

    assert response.status_code == 200
    assert response.json()["object_key"].endswith("export.zip")


@pytest.mark.asyncio
async def test_process_uploaded_zip_parses_single_gpx_ignoring_junk(monkeypatch) -> None:
    zip_bytes = _make_zip(
        {
            "activities/run.gpx": _SAMPLE_GPX,
            "__MACOSX/activities/._run.gpx": b"apple double junk",
            ".DS_Store": b"finder junk",
            "notes.txt": b"discard me",
        }
    )

    body = await _post_process_zip(zip_bytes, monkeypatch)

    assert body["status"] == "ok"
    assert len(body["processed"]) == 1
    entry = body["processed"][0]
    assert entry["kind"] == "activity"
    # Persisted like the single-file path: saved to the log, not merely surfaced.
    assert entry["status"] == "saved"
    assert entry["activity"]["id"] == "activity-1"
    assert entry["activity"]["sport"] == "running"
    assert entry["activity"]["source"] == "gpx_upload"
    # __MACOSX junk, .DS_Store, and notes.txt are all discarded.
    assert body["skipped_count"] == 3


@pytest.mark.asyncio
async def test_process_uploaded_zip_processes_multiple_activities(monkeypatch) -> None:
    zip_bytes = _make_zip({"run1.gpx": _SAMPLE_GPX, "run2.gpx": _SAMPLE_GPX})

    body = await _post_process_zip(zip_bytes, monkeypatch)

    assert body["status"] == "ok"
    assert len(body["processed"]) == 2
    assert all(entry["kind"] == "activity" for entry in body["processed"])
    assert body["skipped_count"] == 0


@pytest.mark.asyncio
async def test_process_uploaded_zip_persists_each_activity(monkeypatch) -> None:
    created: list[Activity] = []

    class RecordingRepo(EngineRepository):
        async def create_activity(self, activity: Activity) -> Activity:
            created.append(activity)
            return activity.model_copy(update={"id": f"activity-{len(created)}"})

    async def mock_download_file_bytes(*, user_id: str, object_key: str) -> bytes:
        return _make_zip({"run1.gpx": _SAMPLE_GPX, "run2.gpx": _SAMPLE_GPX})

    monkeypatch.setattr("api.index.r2_service.download_file_bytes", mock_download_file_bytes)
    monkeypatch.setattr(api_index, "repo", RecordingRepo())
    restore_override = _override_require_user_context(_ZIP_TEST_USER)
    try:
        transport = ASGITransport(app=api_index.app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/engine/process-uploaded-zip",
                json={
                    "content_type": "application/zip",
                    "filename": "export.zip",
                    "object_key": "users/athlete-1/chat-attachment/2024/01/01/export.zip",
                    "public_url": "https://cdn.example.com/export.zip",
                },
            )
    finally:
        restore_override()

    assert response.status_code == 200
    body = response.json()
    assert len(created) == 2
    assert {e["activity"]["id"] for e in body["processed"]} == {"activity-1", "activity-2"}
    assert all(e["status"] == "saved" for e in body["processed"])


@pytest.mark.asyncio
async def test_process_uploaded_zip_isolates_persist_failure_per_member(monkeypatch) -> None:
    # A create_activity failure for one member must not abort a valid sibling: the
    # failing member is skipped best-effort while the other still saves.
    class FlakyRepo(EngineRepository):
        def __init__(self) -> None:
            self.calls = 0

        async def create_activity(self, activity: Activity) -> Activity:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("write conflict")
            return activity.model_copy(update={"id": "activity-2"})

    async def mock_download_file_bytes(*, user_id: str, object_key: str) -> bytes:
        return _make_zip({"run1.gpx": _SAMPLE_GPX, "run2.gpx": _SAMPLE_GPX})

    monkeypatch.setattr("api.index.r2_service.download_file_bytes", mock_download_file_bytes)
    monkeypatch.setattr(api_index, "repo", FlakyRepo())
    restore_override = _override_require_user_context(_ZIP_TEST_USER)
    try:
        transport = ASGITransport(app=api_index.app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/engine/process-uploaded-zip",
                json={
                    "content_type": "application/zip",
                    "filename": "export.zip",
                    "object_key": "users/athlete-1/chat-attachment/2024/01/01/export.zip",
                    "public_url": "https://cdn.example.com/export.zip",
                },
            )
    finally:
        restore_override()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert len(body["processed"]) == 1
    assert body["processed"][0]["activity"]["id"] == "activity-2"
    assert body["skipped_count"] == 1


@pytest.mark.asyncio
async def test_process_uploaded_zip_isolates_postgrest_persist_failure(monkeypatch) -> None:
    # A raw PostgRESTAPIError from persistence must not abort the archive. It is not an
    # HTTPException, so it would otherwise propagate to the global handler and 500 the
    # whole zip; the per-member catch must swallow it and skip only that member.
    class PostgrestFailingRepo(EngineRepository):
        def __init__(self) -> None:
            self.calls = 0

        async def create_activity(self, activity: Activity) -> Activity:
            self.calls += 1
            if self.calls == 1:
                raise PostgRESTAPIError(
                    {
                        "message": "deadlock detected",
                        "code": "40P01",
                        "hint": None,
                        "details": None,
                    }
                )
            return activity.model_copy(update={"id": "activity-2"})

    async def mock_download_file_bytes(*, user_id: str, object_key: str) -> bytes:
        return _make_zip({"run1.gpx": _SAMPLE_GPX, "run2.gpx": _SAMPLE_GPX})

    monkeypatch.setattr("api.index.r2_service.download_file_bytes", mock_download_file_bytes)
    monkeypatch.setattr(api_index, "repo", PostgrestFailingRepo())
    restore_override = _override_require_user_context(_ZIP_TEST_USER)
    try:
        transport = ASGITransport(app=api_index.app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/engine/process-uploaded-zip",
                json={
                    "content_type": "application/zip",
                    "filename": "export.zip",
                    "object_key": "users/athlete-1/chat-attachment/2024/01/01/export.zip",
                    "public_url": "https://cdn.example.com/export.zip",
                },
            )
    finally:
        restore_override()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert len(body["processed"]) == 1
    assert body["processed"][0]["activity"]["id"] == "activity-2"
    assert body["skipped_count"] == 1


@pytest.mark.asyncio
async def test_process_uploaded_zip_processes_activity_and_image(monkeypatch) -> None:
    from backend.models.screenshot import ExtractionResult
    from backend.models.storage import PresignUploadResponse

    async def mock_upload_file(**kwargs) -> PresignUploadResponse:
        return PresignUploadResponse(
            upload_url="",
            object_key="users/athlete-1/chat-attachment/2024/01/01/shot.png",
            public_url="https://cdn.example.com/shot.png",
            headers={"Content-Type": "image/png"},
            method="POST",
        )

    async def mock_analyze_screenshot(image_url: str) -> ExtractionResult:
        return ExtractionResult(
            screenshot_type="activity_single",
            data={"distance_km": 10},
            raw_response="{}",
        )

    monkeypatch.setattr("api.index.r2_service.upload_file", mock_upload_file)
    monkeypatch.setattr("backend.services.screenshot.analyze_screenshot", mock_analyze_screenshot)

    zip_bytes = _make_zip(
        {"run.gpx": _SAMPLE_GPX, "shot.png": b"fake png bytes", "readme.md": b"discard"}
    )

    body = await _post_process_zip(zip_bytes, monkeypatch)

    assert body["status"] == "ok"
    kinds = sorted(entry["kind"] for entry in body["processed"])
    assert kinds == ["activity", "image_analysis"]
    image_entry = next(e for e in body["processed"] if e["kind"] == "image_analysis")
    assert image_entry["screenshot_type"] == "activity_single"
    assert image_entry["public_url"] == "https://cdn.example.com/shot.png"
    assert body["skipped_count"] == 1


@pytest.mark.asyncio
async def test_process_uploaded_zip_no_processable_when_only_unknown(monkeypatch) -> None:
    zip_bytes = _make_zip({"notes.txt": b"nothing here", "data.csv": b"a,b,c"})

    body = await _post_process_zip(zip_bytes, monkeypatch)

    assert body["status"] == "no_processable_files"
    assert body["processed"] == []
    assert body["skipped_count"] == 2
    assert "zip" in body["detail"].lower()


@pytest.mark.asyncio
async def test_process_uploaded_zip_no_processable_when_empty(monkeypatch) -> None:
    body = await _post_process_zip(_make_zip({}), monkeypatch)

    assert body["status"] == "no_processable_files"
    assert body["processed"] == []
    assert body["skipped_count"] == 0


@pytest.mark.asyncio
async def test_process_uploaded_zip_skips_oversized_member(monkeypatch) -> None:
    monkeypatch.setattr("api.index._ZIP_MEMBER_MAX_BYTES", 8)
    zip_bytes = _make_zip({"run.gpx": _SAMPLE_GPX})

    body = await _post_process_zip(zip_bytes, monkeypatch)

    assert body["status"] == "no_processable_files"
    assert body["skipped_count"] == 1


@pytest.mark.asyncio
async def test_process_uploaded_zip_resolves_mangled_object_key(monkeypatch) -> None:
    # The coach can corrupt the opaque object_key while transcribing public_url
    # correctly; the zip endpoint must resolve from public_url like the single-file
    # path (else a valid upload 500s on the scope/download check). The resolved key
    # is also what gets stamped as each activity's source_file_key.
    correct_key = "users/athlete-1/chat-attachment/2026/07/08/export.zip"
    mangled_key = "users/athlete-1/chat-attachment/deadbeef.zip"
    monkeypatch.setattr("backend.services.r2.settings.r2_public_base_url", "https://pub-abc.r2.dev")

    downloaded_keys: list[str] = []

    async def mock_download_file_bytes(*, user_id: str, object_key: str) -> bytes:
        downloaded_keys.append(object_key)
        return _make_zip({"run.gpx": _SAMPLE_GPX})

    created: list[Activity] = []

    class RecordingRepo(EngineRepository):
        async def create_activity(self, activity: Activity) -> Activity:
            created.append(activity)
            return activity.model_copy(update={"id": "activity-1"})

    monkeypatch.setattr("api.index.r2_service.download_file_bytes", mock_download_file_bytes)
    monkeypatch.setattr(api_index, "repo", RecordingRepo())
    restore_override = _override_require_user_context(_ZIP_TEST_USER)
    try:
        transport = ASGITransport(app=api_index.app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/engine/process-uploaded-zip",
                json={
                    "content_type": "application/zip",
                    "filename": "export.zip",
                    "object_key": mangled_key,
                    "public_url": f"https://pub-abc.r2.dev/{correct_key}",
                },
            )
    finally:
        restore_override()

    assert response.status_code == 200
    assert downloaded_keys == [correct_key]
    assert len(created) == 1
    assert created[0].source_file_key == correct_key


@pytest.mark.asyncio
async def test_process_uploaded_zip_handles_corrupt_archive(monkeypatch) -> None:
    body = await _post_process_zip(b"this is definitely not a zip file", monkeypatch)

    assert body["status"] == "no_processable_files"
    assert body["processed"] == []


@pytest.mark.asyncio
async def test_process_uploaded_zip_parses_tcx_member(monkeypatch) -> None:
    body = await _post_process_zip(_make_zip({"ride.tcx": _SAMPLE_TCX}), monkeypatch)

    assert body["status"] == "ok"
    assert len(body["processed"]) == 1
    entry = body["processed"][0]
    assert entry["kind"] == "activity"
    assert entry["activity"]["source"] == "tcx_upload"


@pytest.mark.asyncio
async def test_process_uploaded_zip_skips_unparseable_activity_without_aborting(
    monkeypatch,
) -> None:
    # A member with an activity suffix but garbage bytes must be skipped best-effort,
    # while a valid sibling activity still processes.
    zip_bytes = _make_zip({"good.gpx": _SAMPLE_GPX, "broken.gpx": b"not valid gpx at all"})

    body = await _post_process_zip(zip_bytes, monkeypatch)

    assert body["status"] == "ok"
    assert len(body["processed"]) == 1
    assert body["skipped_count"] == 1


@pytest.mark.asyncio
async def test_process_uploaded_zip_respects_processed_member_cap(monkeypatch) -> None:
    monkeypatch.setattr("api.index._ZIP_MAX_PROCESSED_MEMBERS", 1)
    zip_bytes = _make_zip({"run1.gpx": _SAMPLE_GPX, "run2.gpx": _SAMPLE_GPX})

    body = await _post_process_zip(zip_bytes, monkeypatch)

    assert body["status"] == "ok"
    assert len(body["processed"]) == 1
    assert body["skipped_count"] == 1


@pytest.mark.asyncio
async def test_process_uploaded_zip_cap_counts_failed_member_attempts(monkeypatch) -> None:
    # The work cap counts *attempts*, not successes: an archive of corrupt members
    # (which never land in `processed`) must not make us read/parse every one, or the
    # cap could be bypassed by uploading many unparseable files.
    monkeypatch.setattr("api.index._ZIP_MAX_PROCESSED_MEMBERS", 2)

    attempts = 0

    async def counting_entry(**_kwargs) -> None:
        nonlocal attempts
        attempts += 1

    monkeypatch.setattr(api_index, "_zip_activity_entry", counting_entry)

    zip_bytes = _make_zip({f"broken{i}.gpx": b"not valid gpx at all" for i in range(5)})

    body = await _post_process_zip(zip_bytes, monkeypatch)

    assert body["status"] == "no_processable_files"
    assert body["processed"] == []
    # Only the first two members were read/attempted; the cap declined the remaining
    # three before touching them, even though none succeeded.
    assert attempts == 2
    assert body["skipped_count"] == 5


@pytest.mark.asyncio
async def test_process_uploaded_zip_skips_directory_entries_without_counting(
    monkeypatch,
) -> None:
    # Explicit directory entries (as real macOS/Windows archives carry) are skipped
    # and must not inflate skipped_count.
    zip_bytes = _make_zip({"activities/": b"", "activities/run.gpx": _SAMPLE_GPX})

    body = await _post_process_zip(zip_bytes, monkeypatch)

    assert body["status"] == "ok"
    assert len(body["processed"]) == 1
    assert body["skipped_count"] == 0


@pytest.mark.asyncio
async def test_process_zip_member_counts_read_failure_as_skipped(monkeypatch) -> None:
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(_make_zip({"run.gpx": _SAMPLE_GPX}))) as archive:
        member = archive.infolist()[0]

        def fail_to_read_member(_member: zipfile.ZipInfo) -> bytes:
            raise zipfile.BadZipFile("corrupt member")

        monkeypatch.setattr(archive, "read", fail_to_read_member)
        result = await api_index._process_zip_member(
            archive=archive,
            member=member,
            user_id=_ZIP_TEST_USER.user_id,
            zip_object_key="users/athlete-1/chat-attachment/2024/01/01/export.zip",
            attempted_count=0,
        )

    assert result.entry is None
    assert result.counts_as_skipped is True
    # A read failure is still a processable candidate we attempted, so it must
    # consume the work budget — otherwise a corrupt-heavy archive bypasses the cap.
    assert result.counts_as_attempt is True


@pytest.mark.asyncio
async def test_process_uploaded_zip_skips_image_when_reupload_has_no_public_url(
    monkeypatch,
) -> None:
    from backend.models.storage import PresignUploadResponse

    async def mock_upload_file(**kwargs) -> PresignUploadResponse:
        # R2 public base URL unset → no fetchable URL for the vision model.
        return PresignUploadResponse(
            upload_url="",
            object_key="users/athlete-1/chat-attachment/2024/01/01/shot.png",
            public_url=None,
            headers={"Content-Type": "image/png"},
            method="POST",
        )

    monkeypatch.setattr("api.index.r2_service.upload_file", mock_upload_file)

    body = await _post_process_zip(_make_zip({"shot.png": b"fake png"}), monkeypatch)

    assert body["status"] == "no_processable_files"
    assert body["skipped_count"] == 1


@pytest.mark.asyncio
async def test_process_uploaded_zip_skips_image_when_reupload_raises(monkeypatch) -> None:
    async def mock_upload_file(**kwargs):
        raise RuntimeError("R2 unavailable")

    monkeypatch.setattr("api.index.r2_service.upload_file", mock_upload_file)

    # A failed image upload must not abort a valid sibling activity.
    zip_bytes = _make_zip({"run.gpx": _SAMPLE_GPX, "shot.png": b"fake png"})
    body = await _post_process_zip(zip_bytes, monkeypatch)

    assert body["status"] == "ok"
    assert len(body["processed"]) == 1
    assert body["processed"][0]["kind"] == "activity"
    assert body["skipped_count"] == 1


@pytest.mark.asyncio
async def test_process_uploaded_zip_skips_image_when_analysis_raises(monkeypatch) -> None:
    from backend.models.storage import PresignUploadResponse

    async def mock_upload_file(**kwargs) -> PresignUploadResponse:
        return PresignUploadResponse(
            upload_url="",
            object_key="users/athlete-1/chat-attachment/2024/01/01/shot.png",
            public_url="https://cdn.example.com/shot.png",
            headers={"Content-Type": "image/png"},
            method="POST",
        )

    async def mock_analyze_screenshot(image_url: str):
        raise RuntimeError("vision model rate limited")

    monkeypatch.setattr("api.index.r2_service.upload_file", mock_upload_file)
    monkeypatch.setattr("backend.services.screenshot.analyze_screenshot", mock_analyze_screenshot)

    # A flaky vision call on one image must not abort a valid sibling activity.
    zip_bytes = _make_zip({"run.gpx": _SAMPLE_GPX, "shot.png": b"fake png"})
    body = await _post_process_zip(zip_bytes, monkeypatch)

    assert body["status"] == "ok"
    assert len(body["processed"]) == 1
    assert body["processed"][0]["kind"] == "activity"
    assert body["skipped_count"] == 1


@pytest.mark.asyncio
async def test_chat_attachments_upload_requires_bearer_token() -> None:
    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/api/chat/attachments/upload")

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing bearer token"


@pytest.mark.asyncio
async def test_chat_attachments_upload_validates_object_key_scope(
    auth_service_fixture, monkeypatch
) -> None:
    # Mock R2 service to avoid actual S3 calls
    from backend.models.storage import PresignUploadResponse

    async def mock_upload_file(**kwargs):
        return PresignUploadResponse(
            upload_url="",
            object_key="users/athlete-1/chat-attachment/2024/01/01/file.png",
            public_url="https://cdn.example.com/file.png",
            headers={"Content-Type": "image/png"},
            method="POST",
        )

    monkeypatch.setattr("api.index.r2_service.upload_file", mock_upload_file)

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # First set up browser session
        session_response = await client.post(
            "/api/oauth/browser-session",
            json={"access_token": "supabase-access-token"},
        )
        assert session_response.status_code == 200

        cookie_header = session_response.headers["set-cookie"]
        cookie_value = cookie_header.split("coach_browser_session=")[1].split(";")[0]

        # Then get a valid token
        token_response = await client.post(
            "/api/oauth/browser-token",
            cookies={"coach_browser_session": cookie_value},
        )
        token_body = token_response.json()

        # Try to upload with object_key that doesn't belong to the authenticated user
        response = await client.post(
            "/api/chat/attachments/upload",
            data={"object_key": "users/different-user/chat-attachment/2024/01/01/file.png"},
            files={"file": ("test.png", b"fake image data", "image/png")},
            headers={"Authorization": f"Bearer {token_body['access_token']}"},
        )

    assert response.status_code == 403
    assert "does not belong to authenticated user" in response.json()["detail"]


@pytest.mark.asyncio
async def test_chat_attachments_upload_success(auth_service_fixture, monkeypatch) -> None:
    from backend.models.storage import PresignUploadResponse

    object_key = "users/athlete-1/chat-attachment/2024/01/01/file.png"
    public_url = "https://cdn.example.com/file.png"

    async def mock_upload_file(**kwargs):
        return PresignUploadResponse(
            upload_url="",
            object_key=object_key,
            public_url=public_url,
            headers={"Content-Type": "image/png"},
            method="POST",
        )

    monkeypatch.setattr("api.index.r2_service.upload_file", mock_upload_file)

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        session_response = await client.post(
            "/api/oauth/browser-session",
            json={"access_token": "supabase-access-token"},
        )
        assert session_response.status_code == 200

        cookie_header = session_response.headers["set-cookie"]
        cookie_value = cookie_header.split("coach_browser_session=")[1].split(";")[0]

        token_response = await client.post(
            "/api/oauth/browser-token",
            cookies={"coach_browser_session": cookie_value},
        )
        token_body = token_response.json()

        response = await client.post(
            "/api/chat/attachments/upload",
            data={"object_key": object_key},
            files={"file": ("file.png", b"fake image data", "image/png")},
            headers={"Authorization": f"Bearer {token_body['access_token']}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["object_key"] == object_key
    assert body["public_url"] == public_url


@pytest.mark.asyncio
async def test_process_uploaded_file_parses_gpx_from_authenticated_object(
    auth_service_fixture, monkeypatch, caplog
) -> None:
    object_key = "users/athlete-1/chat-attachment/2024/01/01/run.gpx"
    sensitive_filename = "Secret Race Notes\nInjected.gpx"
    captured: dict[str, str] = {}

    monkeypatch.setattr(api_index, "repo", EngineRepository())

    async def mock_download_file_bytes(*, user_id: str, object_key: str) -> bytes:
        captured["user_id"] = user_id
        captured["object_key"] = object_key
        return b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><trkseg>
    <trkpt lat="37.0" lon="-122.0"><ele>10</ele><time>2026-04-19T10:00:00Z</time></trkpt>
    <trkpt lat="37.0" lon="-122.001"><ele>12</ele><time>2026-04-19T10:01:00Z</time></trkpt>
  </trkseg></trk>
</gpx>"""

    monkeypatch.setattr("api.index.r2_service.download_file_bytes", mock_download_file_bytes)
    caplog.set_level(logging.INFO, logger="api.index")

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        session_response = await client.post(
            "/api/oauth/browser-session",
            json={"access_token": "supabase-access-token"},
        )
        assert session_response.status_code == 200

        cookie_header = session_response.headers["set-cookie"]
        cookie_value = cookie_header.split("coach_browser_session=")[1].split(";")[0]

        token_response = await client.post(
            "/api/oauth/browser-token",
            cookies={"coach_browser_session": cookie_value},
        )
        token_body = token_response.json()

        response = await client.post(
            "/api/engine/process-uploaded-file",
            json={
                "content_type": "application/gpx+xml",
                "filename": sensitive_filename,
                "object_key": object_key,
                "public_url": "https://cdn.example.com/run.gpx",
                "user_id": "payload-user-is-ignored",
            },
            headers={"Authorization": f"Bearer {token_body['access_token']}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["activity"]["sport"] == "running"
    assert body["activity"]["source_file_key"] == object_key
    assert body["activity"]["source"] == "gpx_upload"
    assert body["activity"]["summary_schema_version"] == 1
    assert body["activity"]["activity_summary"]["schema"] == "activity_summary_v1"
    assert body["activity"]["activity_summary"]["session"]["sport"] == "running"
    assert body["activity"]["activity_summary"]["data_quality"]["has_gps"] is True
    assert captured == {"user_id": "athlete-1", "object_key": object_key}
    assert sensitive_filename not in caplog.text
    assert "filename_suffix=.gpx" in caplog.text


@pytest.mark.asyncio
async def test_process_uploaded_file_recovers_key_from_public_url(
    auth_service_fixture, monkeypatch
) -> None:
    # Regression for issue #325: the coach reliably transcribes the distinctive
    # public_url but corrupts the long opaque object_key (splicing the user-UUID
    # head onto the file-UUID tail). The endpoint must derive the authoritative
    # key from public_url so both the R2 download and stored source_file_key are
    # correct — otherwise every future re-read of the activity 403s.
    correct_key = "users/athlete-1/chat-attachment/2024/01/01/run.gpx"
    mangled_key = "users/athlete-1/6679c232edad.gpx"
    captured: dict[str, str] = {}

    monkeypatch.setattr(api_index, "repo", EngineRepository())
    monkeypatch.setattr("backend.services.r2.settings.r2_public_base_url", "https://pub-abc.r2.dev")

    async def mock_download_file_bytes(*, user_id: str, object_key: str) -> bytes:
        captured["object_key"] = object_key
        return b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><trkseg>
    <trkpt lat="37.0" lon="-122.0"><ele>10</ele><time>2026-04-19T10:00:00Z</time></trkpt>
    <trkpt lat="37.0" lon="-122.001"><ele>12</ele><time>2026-04-19T10:01:00Z</time></trkpt>
  </trkseg></trk>
</gpx>"""

    monkeypatch.setattr("api.index.r2_service.download_file_bytes", mock_download_file_bytes)

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        session_response = await client.post(
            "/api/oauth/browser-session",
            json={"access_token": "supabase-access-token"},
        )
        cookie_header = session_response.headers["set-cookie"]
        cookie_value = cookie_header.split("coach_browser_session=")[1].split(";")[0]
        token_response = await client.post(
            "/api/oauth/browser-token",
            cookies={"coach_browser_session": cookie_value},
        )
        token_body = token_response.json()

        response = await client.post(
            "/api/engine/process-uploaded-file",
            json={
                "content_type": "application/gpx+xml",
                "filename": "run.gpx",
                "object_key": mangled_key,
                "public_url": f"https://pub-abc.r2.dev/{correct_key}",
            },
            headers={"Authorization": f"Bearer {token_body['access_token']}"},
        )

    assert response.status_code == 200
    body = response.json()
    # The download used the recovered key, not the mangled one the model sent.
    assert captured["object_key"] == correct_key
    assert body["activity"]["source_file_key"] == correct_key


@pytest.mark.asyncio
async def test_process_uploaded_file_persists_activity(auth_service_fixture, monkeypatch) -> None:
    object_key = "users/athlete-1/chat-attachment/2024/01/01/run.gpx"

    class ActivityRepository(EngineRepository):
        def __init__(self) -> None:
            self.created_activity: Activity | None = None

        async def create_activity(self, activity: Activity) -> Activity:
            self.created_activity = activity
            return activity.model_copy(update={"id": "activity-1"})

    activity_repo = ActivityRepository()
    monkeypatch.setattr(api_index, "repo", activity_repo)

    async def mock_download_file_bytes(*, user_id: str, object_key: str) -> bytes:
        return b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><trkseg>
    <trkpt lat="37.0" lon="-122.0"><ele>10</ele><time>2026-04-19T10:00:00Z</time></trkpt>
    <trkpt lat="37.0" lon="-122.001"><ele>12</ele><time>2026-04-19T10:01:00Z</time></trkpt>
  </trkseg></trk>
</gpx>"""

    monkeypatch.setattr("api.index.r2_service.download_file_bytes", mock_download_file_bytes)

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        session_response = await client.post(
            "/api/oauth/browser-session",
            json={"access_token": "supabase-access-token"},
        )
        cookie_header = session_response.headers["set-cookie"]
        cookie_value = cookie_header.split("coach_browser_session=")[1].split(";")[0]
        token_response = await client.post(
            "/api/oauth/browser-token",
            cookies={"coach_browser_session": cookie_value},
        )
        token_body = token_response.json()

        response = await client.post(
            "/api/engine/process-uploaded-file",
            json={
                "content_type": "application/gpx+xml",
                "filename": "run.gpx",
                "object_key": object_key,
                "public_url": "https://cdn.example.com/run.gpx",
            },
            headers={"Authorization": f"Bearer {token_body['access_token']}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "saved"
    assert body["activity"]["id"] == "activity-1"
    assert activity_repo.created_activity is not None
    assert activity_repo.created_activity.user_id == "athlete-1"
    assert activity_repo.created_activity.source_file_key == object_key


@pytest.mark.asyncio
async def test_process_uploaded_file_parses_tcx_with_hrv_metadata(
    auth_service_fixture, monkeypatch
) -> None:
    object_key = "users/athlete-1/chat-attachment/2024/01/01/run.tcx"

    monkeypatch.setattr(api_index, "repo", EngineRepository())

    async def mock_download_file_bytes(*, user_id: str, object_key: str) -> bytes:
        return b"""<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
  <Activities>
    <Activity Sport="Running">
      <Id>2026-04-19T10:00:00Z</Id>
      <Lap StartTime="2026-04-19T10:00:00Z">
        <TotalTimeSeconds>60</TotalTimeSeconds>
        <DistanceMeters>200</DistanceMeters>
        <Track>
          <Trackpoint>
            <Time>2026-04-19T10:00:00Z</Time>
            <DistanceMeters>0</DistanceMeters>
            <HeartRateBpm><Value>140</Value></HeartRateBpm>
            <Extensions><rr>820</rr><rr>830</rr></Extensions>
          </Trackpoint>
          <Trackpoint>
            <Time>2026-04-19T10:01:00Z</Time>
            <DistanceMeters>200</DistanceMeters>
            <HeartRateBpm><Value>145</Value></HeartRateBpm>
            <Extensions><rr>815</rr><rr>825</rr></Extensions>
          </Trackpoint>
        </Track>
      </Lap>
    </Activity>
  </Activities>
</TrainingCenterDatabase>"""

    monkeypatch.setattr("api.index.r2_service.download_file_bytes", mock_download_file_bytes)

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        session_response = await client.post(
            "/api/oauth/browser-session",
            json={"access_token": "supabase-access-token"},
        )
        cookie_header = session_response.headers["set-cookie"]
        cookie_value = cookie_header.split("coach_browser_session=")[1].split(";")[0]
        token_response = await client.post(
            "/api/oauth/browser-token",
            cookies={"coach_browser_session": cookie_value},
        )
        token_body = token_response.json()

        response = await client.post(
            "/api/engine/process-uploaded-file",
            json={
                "content_type": "application/vnd.garmin.tcx+xml",
                "filename": "run.tcx",
                "object_key": object_key,
                "public_url": "https://cdn.example.com/run.tcx",
                "user_id": "payload-user-is-ignored",
            },
            headers={"Authorization": f"Bearer {token_body['access_token']}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["activity"]["source"] == "tcx_upload"
    assert body["activity"]["avg_hr_bpm"] == 142
    assert body["activity"]["activity_summary"]["schema"] == "activity_summary_v1"
    assert body["activity"]["activity_summary"]["heart_rate"]["avg_bpm"] == 142
    assert body["activity"]["activity_summary"]["data_quality"]["has_rr_intervals"] is True
    assert body["activity"]["raw_extraction"]["rr_interval_count"] == 4
    assert body["activity"]["raw_extraction"]["hrv"]["quality"] == "insufficient_rr_intervals"


@pytest.fixture
def auth_service_fixture():
    original = api_index.auth_service
    api_index.auth_service = FakeAuthService(oauth_repo=cast(Any, InMemoryOAuthRepository()))
    try:
        yield api_index.auth_service
    finally:
        api_index.auth_service = original


@pytest.mark.asyncio
async def test_oauth_authorize_redirects_to_login_without_browser_session(
    auth_service_fixture,
) -> None:
    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/oauth/authorize",
            params={
                "client_id": "https://chat.openai.com",
                "redirect_uri": "https://chat.openai.com/callback",
                "scope": "profile:read plans:write",
                "state": "state-1",
                "code_challenge": "challenge-1",
                "code_challenge_method": "S256",
            },
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"].startswith("http://localhost:3000/login?")


@pytest.mark.asyncio
async def test_oauth_authorize_redirects_to_consent_when_grant_missing(
    auth_service_fixture,
) -> None:
    browser_cookie = auth_service_fixture.create_browser_session_token(
        BrowserSessionContext(user_id="athlete-1", email="athlete@example.com")
    )
    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/oauth/authorize",
            params={
                "client_id": "https://chat.openai.com",
                "redirect_uri": "https://chat.openai.com/callback",
                "scope": "profile:read plans:write",
                "state": "state-1",
                "code_challenge": "challenge-1",
                "code_challenge_method": "S256",
            },
            cookies={"coach_browser_session": browser_cookie},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"].startswith("http://localhost:3000/consent?")


@pytest.mark.asyncio
async def test_oauth_authorize_rejects_unsupported_scope(auth_service_fixture) -> None:
    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/oauth/authorize",
            params={
                "client_id": "https://chat.openai.com",
                "redirect_uri": "https://chat.openai.com/callback",
                "scope": "profile:read admin:root",
                "state": "state-1",
                "code_challenge": "challenge-1",
                "code_challenge_method": "S256",
            },
            follow_redirects=False,
        )

    assert response.status_code == 400
    assert "unsupported" in response.json()["detail"]


@pytest.mark.asyncio
async def test_oauth_authorize_rejects_invalid_redirect_uri(auth_service_fixture) -> None:
    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/oauth/authorize",
            params={
                "client_id": "https://chat.openai.com",
                "redirect_uri": "javascript:alert(1)",
                "scope": "profile:read plans:write",
                "state": "state-1",
                "code_challenge": "challenge-1",
                "code_challenge_method": "S256",
            },
            follow_redirects=False,
        )

    assert response.status_code == 400
    assert "redirect URI" in response.json()["detail"]


@pytest.mark.asyncio
async def test_oauth_authorize_invalid_browser_cookie_redirects_to_login(
    auth_service_fixture,
) -> None:
    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/oauth/authorize",
            params={
                "client_id": "https://chat.openai.com",
                "redirect_uri": "https://chat.openai.com/callback",
                "scope": "profile:read plans:write",
                "state": "state-1",
                "code_challenge": "challenge-1",
                "code_challenge_method": "S256",
            },
            cookies={"coach_browser_session": "not-a-jwt"},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"].startswith("http://localhost:3000/login?")


@pytest.mark.asyncio
async def test_oauth_authorize_prompt_consent_forces_consent(
    auth_service_fixture,
) -> None:
    browser_cookie = auth_service_fixture.create_browser_session_token(
        BrowserSessionContext(user_id="athlete-1", email="athlete@example.com")
    )
    auth_service_fixture._oauth_repo.upsert_grant(
        user_id="athlete-1",
        client_id="https://chat.openai.com",
        redirect_uri="https://chat.openai.com/callback",
        scopes=["profile:read", "plans:write"],
    )
    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/oauth/authorize",
            params={
                "client_id": "https://chat.openai.com",
                "redirect_uri": "https://chat.openai.com/callback",
                "scope": "profile:read plans:write",
                "state": "state-1",
                "prompt": "consent",
                "code_challenge": "challenge-1",
                "code_challenge_method": "S256",
            },
            cookies={"coach_browser_session": browser_cookie},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"].startswith("http://localhost:3000/consent?")


@pytest.mark.asyncio
async def test_oauth_authorize_decision_denial_redirects_back_with_error(
    auth_service_fixture,
) -> None:
    browser_cookie = auth_service_fixture.create_browser_session_token(
        BrowserSessionContext(user_id="athlete-1", email="athlete@example.com")
    )
    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/oauth/authorize/decision",
            data={
                "client_id": "https://chat.openai.com",
                "redirect_uri": "https://chat.openai.com/callback",
                "scope": "profile:read plans:write",
                "state": "state-1",
                "code_challenge": "challenge-1",
                "code_challenge_method": "S256",
                "decision": "deny",
            },
            cookies={"coach_browser_session": browser_cookie},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"] == (
        "https://chat.openai.com/callback?error=access_denied&state=state-1"
    )


@pytest.mark.asyncio
async def test_oauth_authorize_decision_exchanges_code_refresh_and_revokes(
    auth_service_fixture,
) -> None:
    browser_cookie = auth_service_fixture.create_browser_session_token(
        BrowserSessionContext(user_id="athlete-1", email="athlete@example.com")
    )
    verifier = "verifier"
    challenge = (
        base64.urlsafe_b64encode(sha256(verifier.encode("utf-8")).digest())
        .decode("utf-8")
        .rstrip("=")
    )
    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        decision_response = await client.post(
            "/api/oauth/authorize/decision",
            data={
                "client_id": "https://chat.openai.com",
                "redirect_uri": "https://chat.openai.com/callback",
                "scope": "profile:read plans:write",
                "state": "state-1",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "decision": "approve",
            },
            cookies={"coach_browser_session": browser_cookie},
            follow_redirects=False,
        )

        assert decision_response.status_code == 302
        redirected = decision_response.headers["location"]
        assert redirected.startswith("https://chat.openai.com/callback?code=")
        code = redirected.split("code=")[1].split("&", 1)[0]

        token_response = await client.post(
            "/api/oauth/token",
            json=OAuthTokenRequest(
                client_id="https://chat.openai.com",
                code=code,
                code_verifier=verifier,
                grant_type="authorization_code",
                redirect_uri="https://chat.openai.com/callback",
            ).model_dump(mode="json"),
        )

        assert token_response.status_code == 200
        token_body = token_response.json()
        assert "access_token" in token_body
        assert token_body["refresh_token"].startswith("refresh-")

        protected_response = await client.post(
            "/api/mcp",
            headers={"Authorization": f"Bearer {token_body['access_token']}"},
        )
        assert protected_response.status_code == 200

        refresh_response = await client.post(
            "/api/oauth/token",
            json=OAuthTokenRequest(
                client_id="https://chat.openai.com",
                grant_type="refresh_token",
                refresh_token=token_body["refresh_token"],
            ).model_dump(mode="json"),
        )

        assert refresh_response.status_code == 200
        refreshed_body = refresh_response.json()
        assert refreshed_body["refresh_token"] != token_body["refresh_token"]

        revoke_response = await client.post(
            "/api/oauth/revoke",
            json=OAuthRevokeRequest(token=refreshed_body["access_token"]).model_dump(mode="json"),
        )

        assert revoke_response.status_code == 200
        assert revoke_response.json()["revoked"] is True

        revoked_access_response = await client.post(
            "/api/mcp",
            headers={"Authorization": f"Bearer {refreshed_body['access_token']}"},
        )
        assert revoked_access_response.status_code == 401


@pytest.mark.asyncio
async def test_oauth_browser_session_endpoint_sets_cookie(auth_service_fixture) -> None:
    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/oauth/browser-session",
            json={"access_token": "supabase-access-token"},
        )

    assert response.status_code == 200
    assert "coach_browser_session=" in response.headers["set-cookie"]


@pytest.mark.asyncio
async def test_oauth_browser_session_failure_does_not_log_token(
    auth_service_fixture, monkeypatch, caplog
) -> None:
    sensitive_token = "supabase-sensitive-token"

    def fail_browser_session(supabase_access_token: str) -> BrowserSessionContext:
        raise RuntimeError(f"bad token {supabase_access_token}")

    monkeypatch.setattr(auth_service_fixture, "create_browser_session", fail_browser_session)
    caplog.set_level(logging.WARNING, logger="api.index")

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/oauth/browser-session",
            json={"access_token": sensitive_token},
        )

    assert response.status_code == 401
    assert sensitive_token not in caplog.text
    assert "bad token" not in caplog.text
    assert "error_type=RuntimeError" in caplog.text


@pytest.mark.asyncio
async def test_oauth_browser_token_issues_same_origin_bearer(auth_service_fixture) -> None:
    browser_cookie = auth_service_fixture.create_browser_session_token(
        BrowserSessionContext(user_id="athlete-1", email="athlete@example.com")
    )
    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/oauth/browser-token",
            cookies={"coach_browser_session": browser_cookie},
        )

    assert response.status_code == 200
    payload = BrowserTokenResponse.model_validate(response.json())
    assert payload.user_id == "athlete-1"
    assert "profile:write" in payload.scopes


@pytest.mark.asyncio
async def test_oauth_browser_token_requires_browser_cookie(auth_service_fixture) -> None:
    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/api/oauth/browser-token")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_oauth_token_rejects_bad_code_verifier(auth_service_fixture) -> None:
    browser_cookie = auth_service_fixture.create_browser_session_token(
        BrowserSessionContext(user_id="athlete-1", email="athlete@example.com")
    )
    verifier = "correct-verifier"
    challenge = (
        base64.urlsafe_b64encode(sha256(verifier.encode("utf-8")).digest())
        .decode("utf-8")
        .rstrip("=")
    )
    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        decision_response = await client.post(
            "/api/oauth/authorize/decision",
            data={
                "client_id": "https://chat.openai.com",
                "redirect_uri": "https://chat.openai.com/callback",
                "scope": "profile:read plans:write",
                "state": "state-1",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "decision": "approve",
            },
            cookies={"coach_browser_session": browser_cookie},
            follow_redirects=False,
        )
        code = decision_response.headers["location"].split("code=")[1].split("&", 1)[0]

        token_response = await client.post(
            "/api/oauth/token",
            json=OAuthTokenRequest(
                client_id="https://chat.openai.com",
                code=code,
                code_verifier="wrong-verifier",
                grant_type="authorization_code",
                redirect_uri="https://chat.openai.com/callback",
            ).model_dump(mode="json"),
        )

    assert token_response.status_code == 400
    assert "code_verifier" in token_response.json()["detail"]
    assert auth_service_fixture._oauth_repo.codes[code]["consumed_at"] is None


@pytest.mark.asyncio
async def test_oauth_token_rejects_client_mismatch_without_consuming_code(
    auth_service_fixture,
) -> None:
    browser_cookie = auth_service_fixture.create_browser_session_token(
        BrowserSessionContext(user_id="athlete-1", email="athlete@example.com")
    )
    verifier = "verifier"
    challenge = (
        base64.urlsafe_b64encode(sha256(verifier.encode("utf-8")).digest())
        .decode("utf-8")
        .rstrip("=")
    )
    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        decision_response = await client.post(
            "/api/oauth/authorize/decision",
            data={
                "client_id": "https://chat.openai.com",
                "redirect_uri": "https://chat.openai.com/callback",
                "scope": "profile:read plans:write",
                "state": "state-1",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "decision": "approve",
            },
            cookies={"coach_browser_session": browser_cookie},
            follow_redirects=False,
        )
        code = decision_response.headers["location"].split("code=")[1].split("&", 1)[0]

        response = await client.post(
            "/api/oauth/token",
            json=OAuthTokenRequest(
                client_id="https://example.com",
                code=code,
                code_verifier=verifier,
                grant_type="authorization_code",
                redirect_uri="https://chat.openai.com/callback",
            ).model_dump(mode="json"),
        )

    assert response.status_code == 400
    assert "client or redirect mismatch" in response.json()["detail"]
    assert auth_service_fixture._oauth_repo.codes[code]["consumed_at"] is None


@pytest.mark.asyncio
async def test_oauth_token_rejects_reused_authorization_code(auth_service_fixture) -> None:
    browser_cookie = auth_service_fixture.create_browser_session_token(
        BrowserSessionContext(user_id="athlete-1", email="athlete@example.com")
    )
    verifier = "verifier"
    challenge = (
        base64.urlsafe_b64encode(sha256(verifier.encode("utf-8")).digest())
        .decode("utf-8")
        .rstrip("=")
    )
    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        decision_response = await client.post(
            "/api/oauth/authorize/decision",
            data={
                "client_id": "https://chat.openai.com",
                "redirect_uri": "https://chat.openai.com/callback",
                "scope": "profile:read plans:write",
                "state": "state-1",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "decision": "approve",
            },
            cookies={"coach_browser_session": browser_cookie},
            follow_redirects=False,
        )
        code = decision_response.headers["location"].split("code=")[1].split("&", 1)[0]

        first_response = await client.post(
            "/api/oauth/token",
            json=OAuthTokenRequest(
                client_id="https://chat.openai.com",
                code=code,
                code_verifier=verifier,
                grant_type="authorization_code",
                redirect_uri="https://chat.openai.com/callback",
            ).model_dump(mode="json"),
        )
        second_response = await client.post(
            "/api/oauth/token",
            json=OAuthTokenRequest(
                client_id="https://chat.openai.com",
                code=code,
                code_verifier=verifier,
                grant_type="authorization_code",
                redirect_uri="https://chat.openai.com/callback",
            ).model_dump(mode="json"),
        )

    assert first_response.status_code == 200
    assert second_response.status_code == 400
    assert "no longer valid" in second_response.json()["detail"]


@pytest.mark.asyncio
async def test_oauth_refresh_rejects_client_mismatch_without_revoking_token(
    auth_service_fixture,
) -> None:
    refresh_token = auth_service_fixture._oauth_repo.create_refresh_token(
        grant_id="grant-1",
        user_id="athlete-1",
        client_id="https://chat.openai.com",
        scopes=["profile:read"],
    )
    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/oauth/token",
            json=OAuthTokenRequest(
                client_id="https://example.com",
                grant_type="refresh_token",
                refresh_token=refresh_token,
            ).model_dump(mode="json"),
        )

    assert response.status_code == 400
    assert "client mismatch" in response.json()["detail"]
    assert auth_service_fixture._oauth_repo.refresh_tokens[refresh_token]["revoked_at"] is None


async def test_engine_endpoint_requires_bearer_token() -> None:
    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/api/engine/calculate-zones", json={"sport": "cycling"})

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_calculate_zones_returns_power_boundaries() -> None:
    api_index.app.dependency_overrides[api_index.require_user_context] = lambda: UserContext(
        user_id="athlete-1",
        scopes=["profile:read"],
        client_id="test-client",
        grant_id="grant-1",
    )

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/engine/calculate-zones",
            json={"sport": "cycling", "ftp_watts": 300},
        )

    api_index.app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["zones"][0]["name"] == "Recovery"
    assert body["zones"][1]["power_high"] == 225


@pytest.mark.asyncio
async def test_compute_tss_returns_power_based_score() -> None:
    api_index.app.dependency_overrides[api_index.require_user_context] = lambda: UserContext(
        user_id="athlete-1",
        scopes=["profile:read"],
        client_id="test-client",
        grant_id="grant-1",
    )

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/engine/compute-tss",
            json={
                "duration_seconds": 3600,
                "sport": "cycling",
                "normalized_power": 250,
                "ftp": 250,
            },
        )

    api_index.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["tss"] == 100


@pytest.mark.asyncio
async def test_estimate_thresholds_returns_running_paces() -> None:
    api_index.app.dependency_overrides[api_index.require_user_context] = lambda: UserContext(
        user_id="athlete-1",
        scopes=["profile:read"],
        client_id="test-client",
        grant_id="grant-1",
    )

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/engine/estimate-thresholds",
            json={
                "sport": "running",
                "race_time_seconds": 20 * 60,
                "race_distance_meters": 5000,
            },
        )

    api_index.app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["sport"] == "running"
    assert body["lt1_pace_sec_km"] > body["lt2_pace_sec_km"]


@pytest.mark.asyncio
async def test_get_athlete_summary_returns_context_bundle(monkeypatch) -> None:
    api_index.app.dependency_overrides[api_index.require_user_context] = lambda: UserContext(
        user_id="athlete-1",
        scopes=["profile:read"],
        client_id="test-client",
        grant_id="grant-1",
    )
    monkeypatch.setattr(api_index, "repo", EngineRepository())

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/engine/get-athlete-summary",
        )

    api_index.app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["profile"]["primary_sports"] == ["running", "cycling"]
    assert body["current_load"]["ctl"] == 42
    assert body["goals"][0]["course_distance_meters"] == 14_000
    assert body["ctl_ceiling_guidance"]["committed_amateur_ctl"] > 0


@pytest.mark.asyncio
async def test_get_recent_activities_returns_normalized_activity_list(monkeypatch) -> None:
    class ActivityRepository(EngineRepository):
        async def list_activities(self, user_id: str, *, sport=None, limit: int = 50):
            assert user_id == "athlete-1"
            assert sport == "running"
            assert limit == 2
            return [
                Activity(
                    id="activity-1",
                    user_id=user_id,
                    sport="running",
                    activity_date=datetime.fromisoformat("2026-04-10T00:00:00+00:00").date(),
                    duration_seconds=2700,
                    distance_meters=8000,
                    tss=55,
                )
            ]

    api_index.app.dependency_overrides[api_index.require_user_context] = lambda: UserContext(
        user_id="athlete-1",
        scopes=["profile:read"],
        client_id="test-client",
        grant_id="grant-1",
    )
    monkeypatch.setattr(api_index, "repo", ActivityRepository())

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/engine/get-recent-activities",
            json={"limit": 2, "sport": "running"},
        )

    api_index.app.dependency_overrides.clear()

    assert response.status_code == 200
    activities = response.json()["activities"]
    assert activities[0]["id"] == "activity-1"
    assert activities[0]["sport"] == "running"
    assert activities[0]["activity_date"] == "2026-04-10"
    assert activities[0]["distance_meters"] == 8000
    assert activities[0]["tss"] == 55


@pytest.mark.asyncio
async def test_update_goals_validation_errors_return_422(monkeypatch) -> None:
    class GoalRepository(EngineRepository):
        async def create_goal(self, goal: Goal) -> Goal:
            return goal

    restore_override = _override_require_user_context(
        UserContext(
            user_id="athlete-1",
            scopes=["goals:write"],
            client_id="test-client",
            grant_id="grant-1",
        )
    )
    monkeypatch.setattr(api_index, "repo", GoalRepository())

    try:
        transport = ASGITransport(app=api_index.app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/engine/update-goals",
                json={"action": "create", "goal": {"goal_type": "event"}},
            )
    finally:
        restore_override()

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_update_goals_create_normalizes_race_goal_type_to_event(monkeypatch) -> None:
    class GoalRepository(EngineRepository):
        def __init__(self) -> None:
            self.created_goal: Goal | None = None

        async def create_goal(self, goal: Goal) -> Goal:
            self.created_goal = goal
            return goal

    repository = GoalRepository()
    restore_override = _override_require_user_context(
        UserContext(
            user_id="athlete-1",
            scopes=["goals:write"],
            client_id="test-client",
            grant_id="grant-1",
        )
    )
    monkeypatch.setattr(api_index, "repo", repository)

    try:
        transport = ASGITransport(app=api_index.app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/engine/update-goals",
                json={
                    "action": "create",
                    "goal": {
                        "goal_type": "race",
                        "sport": "running",
                        "target_date": "2026-08-29",
                        "title": "Aug 29 Half Marathon",
                    },
                },
            )
    finally:
        restore_override()

    assert response.status_code == 200
    assert response.json()["goal_type"] == "event"
    assert repository.created_goal is not None
    assert repository.created_goal.goal_type == "event"


@pytest.mark.asyncio
async def test_update_goals_contract_errors_return_400(monkeypatch) -> None:
    class GoalRepository(EngineRepository):
        def __init__(self) -> None:
            self.update_call: tuple[str, str, dict[str, object]] | None = None

        async def update_goal(self, goal_id: str, user_id: str, updates: dict) -> Goal:
            self.update_call = (goal_id, user_id, updates)
            return Goal(id=goal_id, user_id=user_id, goal_type="event", title="Race")

    repository = GoalRepository()
    restore_override = _override_require_user_context(
        UserContext(
            user_id="athlete-1",
            scopes=["goals:write"],
            client_id="test-client",
            grant_id="grant-1",
        )
    )
    monkeypatch.setattr(api_index, "repo", repository)

    try:
        transport = ASGITransport(app=api_index.app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            missing_goal_id = await client.post(
                "/api/engine/update-goals",
                json={"action": "complete"},
            )
            unknown_action = await client.post(
                "/api/engine/update-goals",
                json={"action": "pause", "goal_id": "goal-1"},
            )
    finally:
        restore_override()

    assert missing_goal_id.status_code == 400
    assert unknown_action.status_code == 400
    assert repository.update_call is None


@pytest.mark.asyncio
async def test_update_goals_update_is_scoped_to_authenticated_user_and_sanitized(
    monkeypatch,
) -> None:
    class GoalRepository(EngineRepository):
        def __init__(self) -> None:
            self.update_call: tuple[str, str, dict[str, object]] | None = None

        async def update_goal(self, goal_id: str, user_id: str, updates: dict) -> Goal:
            self.update_call = (goal_id, user_id, updates)
            return Goal(
                id=goal_id,
                user_id=user_id,
                goal_type="event",
                title=str(updates.get("title", "Updated goal")),
            )

    repository = GoalRepository()
    restore_override = _override_require_user_context(
        UserContext(
            user_id="athlete-1",
            scopes=["goals:write"],
            client_id="test-client",
            grant_id="grant-1",
        )
    )
    monkeypatch.setattr(api_index, "repo", repository)

    try:
        transport = ASGITransport(app=api_index.app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/engine/update-goals",
                json={
                    "action": "complete",
                    "goal_id": "goal-1",
                    "goal": {
                        "id": "other-goal",
                        "user_id": "other-user",
                        "created_at": "2026-01-01T00:00:00Z",
                        "updated_at": "2026-01-02T00:00:00Z",
                        "title": "Updated goal",
                    },
                },
            )
    finally:
        restore_override()

    assert response.status_code == 200
    assert repository.update_call == ("goal-1", "athlete-1", {"status": "completed"})


@pytest.mark.asyncio
async def test_update_goals_complete_allows_omitted_goal(monkeypatch) -> None:
    class GoalRepository(EngineRepository):
        def __init__(self) -> None:
            self.update_call: tuple[str, str, dict[str, object]] | None = None

        async def update_goal(self, goal_id: str, user_id: str, updates: dict) -> Goal:
            self.update_call = (goal_id, user_id, updates)
            return Goal(id=goal_id, user_id=user_id, goal_type="event", title="Updated goal")

    repository = GoalRepository()
    restore_override = _override_require_user_context(
        UserContext(
            user_id="athlete-1",
            scopes=["goals:write"],
            client_id="test-client",
            grant_id="grant-1",
        )
    )
    monkeypatch.setattr(api_index, "repo", repository)

    try:
        transport = ASGITransport(app=api_index.app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/engine/update-goals",
                json={"action": "complete", "goal_id": "goal-1"},
            )
    finally:
        restore_override()

    assert response.status_code == 200
    assert repository.update_call == ("goal-1", "athlete-1", {"status": "completed"})


@pytest.mark.asyncio
async def test_update_goals_create_rejects_malformed_target_date(monkeypatch) -> None:
    class GoalRepository(EngineRepository):
        def __init__(self) -> None:
            self.created: Goal | None = None

        async def create_goal(self, goal: Goal) -> Goal:
            self.created = goal
            return goal

    repository = GoalRepository()
    restore_override = _override_require_user_context(
        UserContext(
            user_id="athlete-1",
            scopes=["goals:write"],
            client_id="test-client",
            grant_id="grant-1",
        )
    )
    monkeypatch.setattr(api_index, "repo", repository)

    try:
        transport = ASGITransport(app=api_index.app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/engine/update-goals",
                json={
                    "action": "create",
                    "goal": {
                        "goal_type": "event",
                        "title": "Race",
                        "target_date": "summer 2026",
                    },
                },
            )
    finally:
        restore_override()

    assert response.status_code == 422
    assert repository.created is None


@pytest.mark.asyncio
async def test_update_goals_update_rejects_malformed_target_date(monkeypatch) -> None:
    class GoalRepository(EngineRepository):
        def __init__(self) -> None:
            self.update_call: tuple[str, str, dict[str, object]] | None = None

        async def update_goal(self, goal_id: str, user_id: str, updates: dict) -> Goal:
            self.update_call = (goal_id, user_id, updates)
            return Goal(id=goal_id, user_id=user_id, goal_type="event", title="Race")

    repository = GoalRepository()
    restore_override = _override_require_user_context(
        UserContext(
            user_id="athlete-1",
            scopes=["goals:write"],
            client_id="test-client",
            grant_id="grant-1",
        )
    )
    monkeypatch.setattr(api_index, "repo", repository)

    try:
        transport = ASGITransport(app=api_index.app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/engine/update-goals",
                json={
                    "action": "update",
                    "goal_id": "goal-1",
                    "goal": {"target_date": "2026-13-99"},
                },
            )
    finally:
        restore_override()

    assert response.status_code == 422
    assert repository.update_call is None


@pytest.mark.asyncio
async def test_update_goals_update_rejects_unsupported_goal_type(monkeypatch) -> None:
    class GoalRepository(EngineRepository):
        def __init__(self) -> None:
            self.update_call: tuple[str, str, dict[str, object]] | None = None

        async def update_goal(self, goal_id: str, user_id: str, updates: dict) -> Goal:
            self.update_call = (goal_id, user_id, updates)
            return Goal(id=goal_id, user_id=user_id, goal_type="event", title="Race")

    repository = GoalRepository()
    restore_override = _override_require_user_context(
        UserContext(
            user_id="athlete-1",
            scopes=["goals:write"],
            client_id="test-client",
            grant_id="grant-1",
        )
    )
    monkeypatch.setattr(api_index, "repo", repository)

    try:
        transport = ASGITransport(app=api_index.app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/engine/update-goals",
                json={
                    "action": "update",
                    "goal_id": "goal-1",
                    "goal": {"goal_type": "triathlon"},
                },
            )
    finally:
        restore_override()

    assert response.status_code == 422
    assert repository.update_call is None


@pytest.mark.asyncio
async def test_update_goals_update_requires_non_empty_fields(monkeypatch) -> None:
    class GoalRepository(EngineRepository):
        def __init__(self) -> None:
            self.update_call: tuple[str, str, dict[str, object]] | None = None

        async def update_goal(self, goal_id: str, user_id: str, updates: dict) -> Goal:
            self.update_call = (goal_id, user_id, updates)
            return Goal(id=goal_id, user_id=user_id, goal_type="event", title="Race")

    repository = GoalRepository()
    restore_override = _override_require_user_context(
        UserContext(
            user_id="athlete-1",
            scopes=["goals:write"],
            client_id="test-client",
            grant_id="grant-1",
        )
    )
    monkeypatch.setattr(api_index, "repo", repository)

    try:
        transport = ASGITransport(app=api_index.app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/engine/update-goals",
                json={"action": "update", "goal_id": "goal-1", "goal": {}},
            )
    finally:
        restore_override()

    assert response.status_code == 422
    assert repository.update_call is None


@pytest.mark.asyncio
async def test_update_goals_update_omits_null_fields(monkeypatch) -> None:
    class GoalRepository(EngineRepository):
        def __init__(self) -> None:
            self.update_call: tuple[str, str, dict[str, object]] | None = None

        async def update_goal(self, goal_id: str, user_id: str, updates: dict) -> Goal:
            self.update_call = (goal_id, user_id, updates)
            return Goal(id=goal_id, user_id=user_id, goal_type="event", title="Race")

    repository = GoalRepository()
    restore_override = _override_require_user_context(
        UserContext(
            user_id="athlete-1",
            scopes=["goals:write"],
            client_id="test-client",
            grant_id="grant-1",
        )
    )
    monkeypatch.setattr(api_index, "repo", repository)

    try:
        transport = ASGITransport(app=api_index.app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/engine/update-goals",
                json={
                    "action": "update",
                    "goal_id": "goal-1",
                    "goal": {
                        "goal_type": None,
                        "sport": None,
                        "target_date": None,
                        "title": "Updated race",
                    },
                },
            )
    finally:
        restore_override()

    assert response.status_code == 200
    assert repository.update_call == ("goal-1", "athlete-1", {"title": "Updated race"})


@pytest.mark.asyncio
async def test_update_goals_update_merges_course_profile_notes(monkeypatch) -> None:
    class GoalRepository(EngineRepository):
        def __init__(self) -> None:
            self.get_call: tuple[str, str] | None = None
            self.update_call: tuple[str, str, dict[str, object]] | None = None

        async def get_goal(self, goal_id: str, user_id: str) -> Goal:
            self.get_call = (goal_id, user_id)
            return Goal(
                id=goal_id,
                user_id=user_id,
                goal_type="event",
                title="Hill climb race",
                course_profile={"terrain": "trail", "aid_stations": 3},
            )

        async def update_goal(self, goal_id: str, user_id: str, updates: dict) -> Goal:
            self.update_call = (goal_id, user_id, updates)
            return Goal(
                id=goal_id,
                user_id=user_id,
                goal_type="event",
                title="Hill climb race",
            )

    repository = GoalRepository()
    restore_override = _override_require_user_context(
        UserContext(
            user_id="athlete-1",
            scopes=["goals:write"],
            client_id="test-client",
            grant_id="grant-1",
        )
    )
    monkeypatch.setattr(api_index, "repo", repository)

    try:
        transport = ASGITransport(app=api_index.app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/engine/update-goals",
                json={
                    "action": "update",
                    "goal_id": "goal-1",
                    "goal": {"course_profile_notes": "Steep final mile."},
                },
            )
    finally:
        restore_override()

    assert response.status_code == 200
    assert repository.get_call == ("goal-1", "athlete-1")
    assert repository.update_call == (
        "goal-1",
        "athlete-1",
        {
            "course_profile": {
                "aid_stations": 3,
                "notes": "Steep final mile.",
                "terrain": "trail",
            }
        },
    )


@pytest.mark.asyncio
async def test_update_goals_returns_503_when_repository_unconfigured(monkeypatch) -> None:
    class UnconfiguredRepository(EngineRepository):
        async def create_goal(self, goal: Goal) -> Goal:
            raise RepositoryNotConfiguredError("Supabase is not configured.")

    restore_override = _override_require_user_context(
        UserContext(
            user_id="athlete-1",
            scopes=["goals:write"],
            client_id="test-client",
            grant_id="grant-1",
        )
    )
    monkeypatch.setattr(api_index, "repo", UnconfiguredRepository())

    try:
        transport = ASGITransport(app=api_index.app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/engine/update-goals",
                json={"action": "create", "goal": {"goal_type": "event", "title": "Race"}},
            )
    finally:
        restore_override()

    assert response.status_code == 503
    assert response.json() == {"detail": "Supabase is not configured."}


@pytest.mark.asyncio
async def test_update_schedule_validation_errors_return_422(monkeypatch) -> None:
    class ScheduleRepository(EngineRepository):
        async def upsert_schedule(self, schedule: ScheduleAvailability) -> ScheduleAvailability:
            return schedule

        async def upsert_schedule_override(self, override: ScheduleOverride) -> ScheduleOverride:
            return override

    restore_override = _override_require_user_context(
        UserContext(
            user_id="athlete-1",
            scopes=["schedule:write"],
            client_id="test-client",
            grant_id="grant-1",
        )
    )
    monkeypatch.setattr(api_index, "repo", ScheduleRepository())

    try:
        transport = ASGITransport(app=api_index.app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/engine/update-schedule",
                json={"overrides": [{"override_date": "not-a-date", "available": True}]},
            )
    finally:
        restore_override()

    assert response.status_code == 422


@pytest.fixture
def recovery_user_context():
    restore_override = _override_require_user_context(
        UserContext(
            user_id="athlete-1",
            scopes=["recovery:write"],
            client_id="test-client",
            grant_id="grant-1",
        )
    )
    try:
        yield
    finally:
        restore_override()


class RecoveryRepository(EngineRepository):
    def __init__(self) -> None:
        self.saved: list[RecoveryLog] = []

    async def upsert_recovery_log(self, log: RecoveryLog) -> RecoveryLog:
        self.saved.append(log)
        return log.model_copy(update={"id": f"recovery-{len(self.saved)}"})


@pytest.mark.asyncio
@pytest.mark.usefixtures("recovery_user_context")
async def test_save_recovery_data_persists_entries(monkeypatch) -> None:
    repository = RecoveryRepository()
    monkeypatch.setattr(api_index, "repo", repository)

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/engine/save-recovery-data",
            json={
                "entries": [
                    {
                        "log_date": "2026-05-30",
                        "hrv_ms": 48,
                        "sleep_duration_hours": 7.5,
                        "subjective_energy": 4,
                        "notes": None,
                        "user_id": "ignored-client-user",
                    }
                ]
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["saved"][0]["id"] == "recovery-1"
    assert len(repository.saved) == 1
    saved = repository.saved[0]
    # user_id is always derived from the bearer token, never the client payload.
    assert saved.user_id == "athlete-1"
    assert saved.log_date.isoformat() == "2026-05-30"
    assert saved.hrv_ms == 48
    assert saved.sleep_duration_hours == 7.5


@pytest.mark.asyncio
@pytest.mark.usefixtures("recovery_user_context")
async def test_save_recovery_data_defaults_missing_log_date_to_today(monkeypatch) -> None:
    repository = RecoveryRepository()
    monkeypatch.setattr(api_index, "repo", repository)

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/engine/save-recovery-data",
            json={"entries": [{"hrv_ms": 51}]},
        )

    assert response.status_code == 200
    assert repository.saved[0].log_date == datetime.now(UTC).date()


@pytest.mark.asyncio
@pytest.mark.usefixtures("recovery_user_context")
async def test_save_recovery_data_repo_not_configured_returns_503(monkeypatch) -> None:
    class UnconfiguredRepository(EngineRepository):
        async def upsert_recovery_log(self, log: RecoveryLog) -> RecoveryLog:
            raise RepositoryNotConfiguredError("Supabase is not configured.")

    monkeypatch.setattr(api_index, "repo", UnconfiguredRepository())

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/engine/save-recovery-data",
            json={"entries": [{"hrv_ms": 51, "log_date": "2026-05-30"}]},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "Supabase is not configured."}


@pytest.mark.asyncio
@pytest.mark.usefixtures("recovery_user_context")
async def test_save_recovery_data_rejects_malformed_entry_without_partial_write(
    monkeypatch,
) -> None:
    repository = RecoveryRepository()
    monkeypatch.setattr(api_index, "repo", repository)

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/engine/save-recovery-data",
            json={
                "entries": [
                    {"log_date": "2026-05-30", "hrv_ms": 48},
                    {"log_date": "not-a-date", "hrv_ms": 51},
                ]
            },
        )

    assert response.status_code == 422
    # The valid first entry must not be persisted when a later entry is malformed.
    assert repository.saved == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("recovery_user_context")
async def test_save_recovery_data_requires_at_least_one_entry(monkeypatch) -> None:
    monkeypatch.setattr(api_index, "repo", EngineRepository())

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/engine/save-recovery-data",
            json={"entries": []},
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_save_activity_from_text_persists_summary_and_estimates(monkeypatch) -> None:
    from backend.services import activity_text
    from backend.services.activity_text import (
        ActivityTextExtraction,
        AdditionalImportantData,
        NutritionEstimate,
    )

    class ActivityRepository(EngineRepository):
        def __init__(self) -> None:
            self.created_activity: Activity | None = None

        async def create_activity(self, activity: Activity) -> Activity:
            self.created_activity = activity
            return activity.model_copy(update={"id": "activity-1"})

    async def fake_extract_activity_text(_text: str) -> ActivityTextExtraction:
        return ActivityTextExtraction(
            activity_date="2026-06-13",
            activity_date_confidence=0.9,
            additional_important_data=[
                AdditionalImportantData(key="race_context", value="blew up", confidence=0.8)
            ],
            avg_hr_bpm=183,
            avg_hr_bpm_confidence=0.95,
            avg_power_watts=198,
            avg_power_watts_confidence=0.95,
            elapsed_duration_seconds=2700,
            elapsed_duration_seconds_confidence=0.8,
            food_items=[],
            max_hr_bpm=193,
            max_hr_bpm_confidence=0.95,
            moving_duration_seconds=1140,
            moving_duration_seconds_confidence=0.86,
            normalized_power_watts=243,
            normalized_power_watts_confidence=0.95,
            nutrition_estimates=[
                NutritionEstimate(
                    calories_kcal=412,
                    calories_kcal_confidence=0.9,
                    carbs_g=103,
                    carbs_g_confidence=0.95,
                    item_name="reported CHO",
                    source_title=None,
                    source_url=None,
                )
            ],
            sport="cycling",
            sport_confidence=0.86,
            sub_sport="criterium",
            sub_sport_confidence=0.84,
        )

    repository = ActivityRepository()
    restore_override = _override_require_user_context(
        UserContext(
            user_id="athlete-1",
            scopes=["activities:write"],
            client_id="test-client",
            grant_id="grant-1",
        )
    )
    monkeypatch.setattr(api_index, "repo", repository)
    monkeypatch.setattr(activity_text, "extract_activity_text", fake_extract_activity_text)

    try:
        transport = ASGITransport(app=api_index.app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/engine/save-activity-from-text",
                json={
                    "text": (
                        "Volunteer Park crit, Sat 13 Jun 2026 — 45 min race start at "
                        "~12:56-13:00. Report: in race ~19 minutes then blew up; "
                        "avg HR 183 bpm, max 193 bpm; avg power 198 W, NP 243 W; "
                        "CHO used ~103 g; short high-power surges up to ~450 W for "
                        "8-15s; felt competitive for first 19 minutes."
                    )
                },
            )
    finally:
        restore_override()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "saved"
    assert body["activity"]["id"] == "activity-1"
    assert body["activity"]["source"] == "text_extract"
    assert body["activity"]["activity_summary"]["estimates"]["estimated_duration_moving_s"] == 1140
    assert body["activity"]["activity_summary"]["thresholds_used"]["ftp_w"] == 250
    assert body["activity"]["activity_summary"]["fueling"]["carbs_g"] == 103
    assert repository.created_activity is not None
    assert repository.created_activity.tss == 29.9


@pytest.mark.asyncio
async def test_save_activity_from_text_fails_when_openai_extraction_unavailable(
    monkeypatch,
) -> None:
    from backend.services import activity_text
    from backend.services.activity_text import ActivityTextExtractionUnavailable

    class ActivityRepository(EngineRepository):
        def __init__(self) -> None:
            self.create_called = False

        async def create_activity(self, activity: Activity) -> Activity:
            self.create_called = True
            return activity

    async def failing_extract_activity_text(_text: str):
        raise ActivityTextExtractionUnavailable("OpenAI activity text extraction unavailable.")

    repository = ActivityRepository()
    restore_override = _override_require_user_context(
        UserContext(
            user_id="athlete-1",
            scopes=["activities:write"],
            client_id="test-client",
            grant_id="grant-1",
        )
    )
    monkeypatch.setattr(api_index, "repo", repository)
    monkeypatch.setattr(activity_text, "extract_activity_text", failing_extract_activity_text)

    try:
        transport = ASGITransport(app=api_index.app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/engine/save-activity-from-text",
                json={"text": "Ran yesterday and ate a gel."},
            )
    finally:
        restore_override()

    assert response.status_code == 503
    assert response.json()["detail"] == "OpenAI activity text extraction unavailable."
    assert repository.create_called is False


@pytest.mark.asyncio
async def test_save_activity_from_text_updates_existing_activity(monkeypatch) -> None:
    from backend.services import activity_text
    from backend.services.activity_text import ActivityTextExtraction, NutritionEstimate

    class ActivityRepository(EngineRepository):
        def __init__(self) -> None:
            self.updated_activity: Activity | None = None

        async def update_activity(self, activity: Activity) -> Activity:
            self.updated_activity = activity
            return activity

    async def fake_extract_activity_text(_text: str) -> ActivityTextExtraction:
        return ActivityTextExtraction(
            food_items=[],
            gut_comfort_1_10=8,
            gut_comfort_1_10_confidence=0.8,
            nutrition_estimates=[
                NutritionEstimate(
                    calories_kcal=200,
                    calories_kcal_confidence=0.5,
                    carbs_g=50,
                    carbs_g_confidence=0.5,
                    item_name="2 generic energy gels",
                    source_title=None,
                    source_url=None,
                )
            ],
            overdid_it_flag=True,
            overdid_it_flag_confidence=0.9,
            rpe=9,
            rpe_confidence=0.8,
        )

    repository = ActivityRepository()
    restore_override = _override_require_user_context(
        UserContext(
            user_id="athlete-1",
            scopes=["activities:write"],
            client_id="test-client",
            grant_id="grant-1",
        )
    )
    monkeypatch.setattr(api_index, "repo", repository)
    monkeypatch.setattr(activity_text, "extract_activity_text", fake_extract_activity_text)

    try:
        transport = ASGITransport(app=api_index.app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/engine/save-activity-from-text",
                json={
                    "activity_id": "activity-1",
                    "text": "Add that I took 2 gels, gut felt 8/10, RPE 9, and I overdid it.",
                },
            )
    finally:
        restore_override()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "updated"
    assert body["activity"]["source"] == "fit_upload"
    assert body["activity"]["rpe"] == 9
    assert body["activity"]["activity_summary"]["subjective"]["overdid_it_flag"] is True
    assert repository.updated_activity is not None
    assert repository.updated_activity.source == "fit_upload"


@pytest.mark.asyncio
async def test_save_activity_from_text_rejects_blank_activity_id(monkeypatch) -> None:
    class ActivityRepository(EngineRepository):
        def __init__(self) -> None:
            self.create_called = False
            self.update_called = False

        async def create_activity(self, activity: Activity) -> Activity:
            self.create_called = True
            return activity

        async def update_activity(self, activity: Activity) -> Activity:
            self.update_called = True
            return activity

    repository = ActivityRepository()
    restore_override = _override_require_user_context(
        UserContext(
            user_id="athlete-1",
            scopes=["activities:write"],
            client_id="test-client",
            grant_id="grant-1",
        )
    )
    monkeypatch.setattr(api_index, "repo", repository)

    try:
        transport = ASGITransport(app=api_index.app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/engine/save-activity-from-text",
                json={"activity_id": "   ", "text": "Add RPE 9."},
            )
    finally:
        restore_override()

    assert response.status_code == 422
    assert repository.create_called is False
    assert repository.update_called is False


@pytest.mark.asyncio
async def test_save_activity_from_text_update_missing_activity_returns_404(monkeypatch) -> None:
    class ActivityRepository(EngineRepository):
        def __init__(self) -> None:
            self.update_called = False

        async def get_activity(self, user_id: str, activity_id: str) -> Activity:
            raise RecordNotFoundError(
                f"No activity found for user '{user_id}' and id '{activity_id}'."
            )

        async def update_activity(self, activity: Activity) -> Activity:
            self.update_called = True
            return activity

    repository = ActivityRepository()
    restore_override = _override_require_user_context(
        UserContext(
            user_id="athlete-1",
            scopes=["activities:write"],
            client_id="test-client",
            grant_id="grant-1",
        )
    )
    monkeypatch.setattr(api_index, "repo", repository)

    try:
        transport = ASGITransport(app=api_index.app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/engine/save-activity-from-text",
                json={"activity_id": "missing-activity", "text": "Add RPE 9."},
            )
    finally:
        restore_override()

    assert response.status_code == 404
    assert response.json()["detail"] == "Activity not found."
    assert repository.update_called is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failing_method", "expected_detail"),
    [
        ("get_athlete_profile", "Failed to load athlete profile."),
        ("get_active_thresholds", "Failed to load athlete thresholds."),
        ("create_activity", "Failed to save activity."),
    ],
)
async def test_save_activity_from_text_create_maps_repository_failures_to_503(
    failing_method: str,
    expected_detail: str,
    monkeypatch,
) -> None:
    from backend.services import activity_text
    from backend.services.activity_text import ActivityTextBuildResult

    class ActivityRepository(EngineRepository):
        async def get_athlete_profile(self, user_id: str) -> AthleteProfile:
            if failing_method == "get_athlete_profile":
                raise HTTPError("profile unavailable")
            return await super().get_athlete_profile(user_id)

        async def get_active_thresholds(self, user_id: str) -> list[SportThreshold]:
            if failing_method == "get_active_thresholds":
                raise HTTPError("thresholds unavailable")
            return await super().get_active_thresholds(user_id)

        async def create_activity(self, activity: Activity) -> Activity:
            if failing_method == "create_activity":
                raise HTTPError("insert unavailable")
            return activity

    async def fake_build_activity_from_text(*_args, **_kwargs) -> ActivityTextBuildResult:
        return ActivityTextBuildResult(
            activity=Activity(
                user_id="athlete-1",
                sport="cycling",
                activity_date=datetime.fromisoformat("2026-06-13T00:00:00+00:00").date(),
                source="text_extract",
            ),
            missing=[],
            raw_extraction={},
        )

    restore_override = _override_require_user_context(
        UserContext(
            user_id="athlete-1",
            scopes=["activities:write"],
            client_id="test-client",
            grant_id="grant-1",
        )
    )
    monkeypatch.setattr(api_index, "repo", ActivityRepository())
    monkeypatch.setattr(activity_text, "build_activity_from_text", fake_build_activity_from_text)

    try:
        transport = ASGITransport(app=api_index.app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/engine/save-activity-from-text",
                json={"text": "Rode hard yesterday."},
            )
    finally:
        restore_override()

    assert response.status_code == 503
    assert response.json()["detail"] == expected_detail


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failing_method", "expected_detail"),
    [
        ("get_activity", "Failed to load activity."),
        ("update_activity", "Failed to update activity."),
    ],
)
async def test_save_activity_from_text_update_maps_repository_failures_to_503(
    failing_method: str,
    expected_detail: str,
    monkeypatch,
) -> None:
    from backend.services import activity_text

    class ActivityRepository(EngineRepository):
        async def get_activity(self, user_id: str, activity_id: str) -> Activity:
            if failing_method == "get_activity":
                raise HTTPError("activity load unavailable")
            return await super().get_activity(user_id, activity_id)

        async def update_activity(self, activity: Activity) -> Activity:
            if failing_method == "update_activity":
                raise HTTPError("activity update unavailable")
            return activity

    async def fake_merge_activity_text_update(existing: Activity, _text: str) -> Activity:
        return existing.model_copy(update={"rpe": 9})

    restore_override = _override_require_user_context(
        UserContext(
            user_id="athlete-1",
            scopes=["activities:write"],
            client_id="test-client",
            grant_id="grant-1",
        )
    )
    monkeypatch.setattr(api_index, "repo", ActivityRepository())
    monkeypatch.setattr(
        activity_text, "merge_activity_text_update", fake_merge_activity_text_update
    )

    try:
        transport = ASGITransport(app=api_index.app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/engine/save-activity-from-text",
                json={"activity_id": "activity-1", "text": "Add RPE 9."},
            )
    finally:
        restore_override()

    assert response.status_code == 503
    assert response.json()["detail"] == expected_detail


@pytest.mark.asyncio
async def test_generate_plan_structure_uses_goal_and_load(monkeypatch) -> None:
    api_index.app.dependency_overrides[api_index.require_user_context] = lambda: UserContext(
        user_id="athlete-1",
        scopes=["plans:write"],
        client_id="test-client",
        grant_id="grant-1",
    )
    monkeypatch.setattr(api_index, "repo", EngineRepository())

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/engine/generate-plan-structure",
            json={},
        )

    api_index.app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["target_goal"]["title"] == "Hill climb race"
    assert body["starting_weekly_tss"] == 294
    assert body["phases"]
    assert body["plan_id"] == "plan-1"
    assert body["sport"] == "running"
    assert body["workouts_created"] == body["total_weeks"] * 7


@pytest.mark.asyncio
async def test_generate_plan_structure_persists_plan_and_workouts(monkeypatch) -> None:
    class RecordingRepository(EngineRepository):
        def __init__(self) -> None:
            self.created_plan: TrainingPlan | None = None
            self.created_workouts: list[PlanWorkout] = []

        async def get_athlete_profile(self, user_id: str) -> AthleteProfile:
            profile = await super().get_athlete_profile(user_id)
            # Put a non-goal sport first so the assertion below proves the
            # goal sport (running) wins over profile ordering.
            return profile.model_copy(update={"primary_sports": ["cycling", "running"]})

        async def create_training_plan(self, plan: TrainingPlan) -> TrainingPlan:
            self.created_plan = plan
            return plan.model_copy(update={"id": "plan-1"})

        async def create_plan_workouts(self, workouts: list[PlanWorkout]) -> list[PlanWorkout]:
            self.created_workouts = workouts
            return workouts

    recording_repo = RecordingRepository()
    api_index.app.dependency_overrides[api_index.require_user_context] = lambda: UserContext(
        user_id="athlete-1",
        scopes=["plans:write"],
        client_id="test-client",
        grant_id="grant-1",
    )
    monkeypatch.setattr(api_index, "repo", recording_repo)

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/api/engine/generate-plan-structure", json={})

    api_index.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert recording_repo.created_plan is not None
    assert recording_repo.created_plan.status == "active"
    assert recording_repo.created_plan.target_goal_id == "goal-1"
    workouts = recording_repo.created_workouts
    assert workouts
    assert all(w.plan_id == "plan-1" for w in workouts)
    assert all(w.user_id == "athlete-1" for w in workouts)
    # Goal sport (running) wins over profile ordering.
    assert {w.sport for w in workouts} == {"running"}
    assert all(w.status == "scheduled" for w in workouts)


@pytest.mark.asyncio
async def test_generate_plan_structure_accepts_training_model_policy(monkeypatch) -> None:
    class RecordingRepository(EngineRepository):
        def __init__(self) -> None:
            self.created_plan: TrainingPlan | None = None
            self.created_workouts: list[PlanWorkout] = []

        async def create_training_plan(self, plan: TrainingPlan) -> TrainingPlan:
            self.created_plan = plan
            return plan.model_copy(update={"id": "plan-1"})

        async def create_plan_workouts(self, workouts: list[PlanWorkout]) -> list[PlanWorkout]:
            self.created_workouts = workouts
            return workouts

    recording_repo = RecordingRepository()
    api_index.app.dependency_overrides[api_index.require_user_context] = lambda: UserContext(
        user_id="athlete-1",
        scopes=["plans:write"],
        client_id="test-client",
        grant_id="grant-1",
    )
    monkeypatch.setattr(api_index, "repo", recording_repo)

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/engine/generate-plan-structure",
            json={"training_model": "longevity"},
        )

    api_index.app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["training_model"] == "longevity"
    assert recording_repo.created_plan is not None
    assert recording_repo.created_plan.generation_context == {
        "training_model": "longevity",
        "training_model_source": "explicit",
    }
    first_week = [w for w in recording_repo.created_workouts if w.week_number == 1]
    quality = [w for w in first_week if w.workout_type in {"tempo", "threshold", "vo2max"}]
    assert len(quality) == 1


@pytest.mark.asyncio
async def test_generate_plan_structure_supersedes_partial_plan_on_workout_failure(
    monkeypatch,
) -> None:
    class FailingWorkoutRepository(EngineRepository):
        def __init__(self) -> None:
            self.status_updates: list[tuple[str, str]] = []

        async def create_training_plan(self, plan: TrainingPlan) -> TrainingPlan:
            return plan.model_copy(update={"id": "plan-1"})

        async def create_plan_workouts(self, workouts: list[PlanWorkout]) -> list[PlanWorkout]:
            raise RuntimeError("insert failed")

        async def update_training_plan_status(
            self, user_id: str, plan_id: str, status: str
        ) -> None:
            self.status_updates.append((plan_id, status))

    failing_repo = FailingWorkoutRepository()
    api_index.app.dependency_overrides[api_index.require_user_context] = lambda: UserContext(
        user_id="athlete-1",
        scopes=["plans:write"],
        client_id="test-client",
        grant_id="grant-1",
    )
    monkeypatch.setattr(api_index, "repo", failing_repo)

    transport = ASGITransport(app=api_index.app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/api/engine/generate-plan-structure", json={})

    api_index.app.dependency_overrides.clear()

    assert response.status_code == 503
    assert failing_repo.status_updates == [("plan-1", "superseded")]


@pytest.mark.asyncio
async def test_generate_plan_structure_maps_composer_valueerror_to_503(monkeypatch) -> None:
    from backend.services import plan_composer

    class RecordingCleanupRepository(EngineRepository):
        def __init__(self) -> None:
            self.status_updates: list[tuple[str, str]] = []

        async def create_training_plan(self, plan: TrainingPlan) -> TrainingPlan:
            return plan.model_copy(update={"id": "plan-1"})

        async def update_training_plan_status(
            self, user_id: str, plan_id: str, status: str
        ) -> None:
            self.status_updates.append((plan_id, status))

    def broken_compose(*args, **kwargs):
        raise ValueError("Plan skeleton has no phase covering week 3")

    cleanup_repo = RecordingCleanupRepository()
    api_index.app.dependency_overrides[api_index.require_user_context] = lambda: UserContext(
        user_id="athlete-1",
        scopes=["plans:write"],
        client_id="test-client",
        grant_id="grant-1",
    )
    monkeypatch.setattr(api_index, "repo", cleanup_repo)
    monkeypatch.setattr(plan_composer, "compose_plan_workouts", broken_compose)

    transport = ASGITransport(app=api_index.app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/api/engine/generate-plan-structure", json={})

    api_index.app.dependency_overrides.clear()

    assert response.status_code == 503
    assert cleanup_repo.status_updates == [("plan-1", "superseded")]


@pytest.mark.asyncio
async def test_get_athlete_summary_new_user_returns_onboarding_stub(monkeypatch) -> None:
    """New users without a profile row get a stub with coaching_state=onboarding."""
    from backend.repos.supabase_repo import RecordNotFoundError

    class NewUserRepository(EngineRepository):
        async def get_athlete_profile(self, user_id: str) -> AthleteProfile:
            raise RecordNotFoundError(f"No athlete profile found for user '{user_id}'.")

    api_index.app.dependency_overrides[api_index.require_user_context] = lambda: UserContext(
        user_id="athlete-new",
        scopes=["profile:read"],
        client_id="test-client",
        grant_id="grant-1",
    )
    monkeypatch.setattr(api_index, "repo", NewUserRepository())

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/engine/get-athlete-summary",
        )

    api_index.app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["profile"]["user_id"] == "athlete-new"
    assert body["profile"]["coaching_state"] == "onboarding"
    assert body["ctl_ceiling_guidance"]["committed_amateur_ctl"] > 0


@pytest.mark.asyncio
async def test_update_athlete_profile_persists_fields(monkeypatch) -> None:
    """update-athlete-profile saves display_name, primary_sports, and weekly_available_hours."""

    class MutableRepository(EngineRepository):
        def __init__(self) -> None:
            self.saved: dict | None = None

        async def update_athlete_profile_fields(self, user_id: str, fields: dict) -> AthleteProfile:
            self.saved = {"user_id": user_id, **fields}
            return AthleteProfile(
                user_id=user_id,
                display_name=fields.get("display_name"),
                primary_sports=fields.get("primary_sports", []),
                coaching_state="onboarding",
            )

    repo = MutableRepository()
    api_index.app.dependency_overrides[api_index.require_user_context] = lambda: UserContext(
        user_id="athlete-1",
        scopes=["profile:write"],
        client_id="test-client",
        grant_id="grant-1",
    )
    monkeypatch.setattr(api_index, "repo", repo)

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/engine/update-athlete-profile",
            json={
                "user_id": "athlete-1",
                "fields": {
                    "display_name": "Alex",
                    "primary_sports": ["running", "cycling"],
                    "weekly_available_hours": 8.0,
                },
            },
        )

    api_index.app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["display_name"] == "Alex"
    assert repo.saved is not None
    assert repo.saved["display_name"] == "Alex"
    assert repo.saved["primary_sports"] == ["running", "cycling"]


@pytest.mark.asyncio
async def test_update_athlete_profile_returns_bounded_error(monkeypatch) -> None:
    """update-athlete-profile returns a bounded 503 when persistence fails."""

    class FailingRepository(EngineRepository):
        async def update_athlete_profile_fields(self, user_id: str, fields: dict) -> AthleteProfile:
            raise RuntimeError("database rejected profile update")

    api_index.app.dependency_overrides[api_index.require_user_context] = lambda: UserContext(
        user_id="athlete-1",
        scopes=["profile:write"],
        client_id="test-client",
        grant_id="grant-1",
    )
    monkeypatch.setattr(api_index, "repo", FailingRepository())

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/engine/update-athlete-profile",
            json={"fields": {"hormone_status": "not_provided"}},
        )

    api_index.app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json() == {"detail": "Unable to update athlete profile."}


@pytest.mark.asyncio
async def test_get_athlete_summary_includes_nutrition_fields(monkeypatch) -> None:
    """Athlete summary returns dietary_restrictions and nutrition_notes."""

    class NutritionRepository(EngineRepository):
        async def get_athlete_profile(self, user_id: str) -> AthleteProfile:
            return AthleteProfile(
                user_id=user_id,
                coaching_state="active",
                dietary_restrictions=["vegetarian"],
                nutrition_notes="Avoid dairy on race morning",
            )

    api_index.app.dependency_overrides[api_index.require_user_context] = lambda: UserContext(
        user_id="athlete-1",
        scopes=["profile:read"],
        client_id="test-client",
        grant_id="grant-1",
    )
    monkeypatch.setattr(api_index, "repo", NutritionRepository())

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/engine/get-athlete-summary",
        )

    api_index.app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["profile"]["dietary_restrictions"] == ["vegetarian"]
    assert body["profile"]["nutrition_notes"] == "Avoid dairy on race morning"


@pytest.fixture
def model_state_chat_service_fixture():
    original = api_index.chat_service
    service = ModelStateChatService()
    api_index.chat_service = cast(Any, service)
    api_index.app.dependency_overrides[api_index.require_user_context] = lambda: UserContext(
        user_id="athlete-1", scopes=[]
    )
    try:
        yield service
    finally:
        api_index.chat_service = original
        api_index.app.dependency_overrides.pop(api_index.require_user_context, None)


@pytest.mark.asyncio
async def test_chat_model_state_get_and_replace_are_authenticated_private_endpoints(
    model_state_chat_service_fixture,
) -> None:
    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        initial = await client.get("/api/chat/model-state")
        await client.post(
            "/api/chat/model-state/lease", json={"lease_id": "lease-1", "ttl_seconds": 60}
        )
        replaced = await client.put(
            "/api/chat/model-state",
            json={
                "expected_version": 3,
                "lease_id": "lease-1",
                "items": [{"role": "user", "content": "hello"}],
                "coaching_memory": [],
                "compaction_metadata": {"reason": "seed"},
            },
        )

    assert initial.status_code == 200
    assert initial.json()["version"] == 2
    assert replaced.status_code == 200
    assert replaced.json()["items"] == [{"role": "user", "content": "hello"}]


@pytest.mark.asyncio
async def test_chat_model_state_replace_rejects_stale_version(
    model_state_chat_service_fixture,
) -> None:
    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.post(
            "/api/chat/model-state/lease", json={"lease_id": "lease-1", "ttl_seconds": 60}
        )
        response = await client.put(
            "/api/chat/model-state",
            json={
                "expected_version": 1,
                "lease_id": "lease-1",
                "items": [],
                "coaching_memory": [],
                "compaction_metadata": {},
            },
        )

    assert response.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "json"),
    [
        ("GET", "/api/chat/model-state", None),
        (
            "PUT",
            "/api/chat/model-state",
            {"expected_version": 0, "lease_id": "lease-1"},
        ),
        ("POST", "/api/chat/model-state/lease", {"lease_id": "lease-1"}),
        ("DELETE", "/api/chat/model-state/lease", {"lease_id": "lease-1"}),
    ],
)
async def test_chat_model_state_endpoints_require_authentication(
    method: str, path: str, json: dict[str, object] | None
) -> None:
    api_index.app.dependency_overrides.pop(api_index.require_user_context, None)
    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.request(method, path, json=json)

    assert response.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "json"),
    [
        ("GET", "/api/chat/messages", None),
        ("GET", "/api/chat/model-state", None),
        (
            "PUT",
            "/api/chat/model-state",
            {
                "expected_version": 0,
                "lease_id": "lease-1",
                "items": [],
                "coaching_memory": [],
                "compaction_metadata": {},
            },
        ),
        ("POST", "/api/chat/model-state/lease", {"lease_id": "lease-1"}),
        ("DELETE", "/api/chat/model-state/lease", {"lease_id": "lease-1"}),
    ],
)
async def test_private_chat_state_endpoints_map_repository_configuration_errors_to_503(
    method: str,
    path: str,
    json: dict[str, object] | None,
    model_state_chat_service_fixture,
) -> None:
    service = model_state_chat_service_fixture

    async def unavailable(*_args, **_kwargs):
        raise RepositoryNotConfiguredError("Supabase unavailable")

    service.list_messages = unavailable
    service.get_model_state = unavailable
    service.replace_model_state = unavailable
    service.acquire_turn_lease = unavailable
    service.release_turn_lease = unavailable
    transport = ASGITransport(app=api_index.app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.request(method, path, json=json)

    assert response.status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "json", "service_method", "exception", "expected_detail"),
    [
        # httpx transport errors are still handled locally by the endpoint, which keeps
        # its own message.
        (
            "GET",
            "/api/chat/messages",
            None,
            "list_messages",
            HTTPError("connection reset"),
            "Chat session service unavailable",
        ),
        (
            "GET",
            "/api/chat/model-state",
            None,
            "get_model_state",
            HTTPError("timeout"),
            "Chat session service unavailable",
        ),
        # A PostgREST schema-cache miss now flows to the centralized handler, which maps
        # it to 503 with the shared generic detail.
        (
            "PUT",
            "/api/chat/model-state",
            {
                "expected_version": 0,
                "lease_id": "lease-1",
                "items": [],
                "coaching_memory": [],
                "compaction_metadata": {},
            },
            "replace_model_state",
            PostgRESTAPIError(
                {
                    "message": "schema cache unavailable",
                    "code": "PGRST205",
                    "hint": None,
                    "details": None,
                }
            ),
            "Service temporarily unavailable.",
        ),
    ],
)
async def test_private_chat_state_endpoints_map_transient_storage_errors_to_503(
    method: str,
    path: str,
    json: dict[str, object] | None,
    service_method: str,
    exception: Exception,
    expected_detail: str,
    model_state_chat_service_fixture,
) -> None:
    service = model_state_chat_service_fixture

    async def unavailable(*_args, **_kwargs):
        raise exception

    setattr(service, service_method, unavailable)
    transport = ASGITransport(app=api_index.app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.request(method, path, json=json)

    assert response.status_code == 503
    assert response.json()["detail"] == expected_detail


@pytest.mark.asyncio
async def test_private_chat_state_endpoints_do_not_mask_programming_errors(
    model_state_chat_service_fixture,
) -> None:
    service = model_state_chat_service_fixture

    async def broken_replace(*_args, **_kwargs):
        raise TypeError("programming error")

    service.replace_model_state = broken_replace
    transport = ASGITransport(app=api_index.app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.put(
            "/api/chat/model-state",
            json={
                "chat_id": "thread-1",
                "expected_version": 0,
                "lease_id": "lease-1",
                "items": [],
                "coaching_memory": [],
                "compaction_metadata": {},
            },
        )

    assert response.status_code == 500


@pytest.mark.asyncio
async def test_chat_turn_lease_acquire_and_release(model_state_chat_service_fixture) -> None:
    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        acquired = await client.post(
            "/api/chat/model-state/lease",
            json={"lease_id": "lease-1", "ttl_seconds": 60},
        )
        released = await client.request(
            "DELETE",
            "/api/chat/model-state/lease",
            json={"lease_id": "lease-1"},
        )

    assert acquired.status_code == 200
    assert acquired.json()["lease_id"] == "lease-1"
    assert released.status_code == 200
    assert released.json()["lease_id"] is None
