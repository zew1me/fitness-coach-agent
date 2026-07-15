import base64
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any, cast

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

import api.index as api_index
from backend.models.auth import UserContext
from backend.models.intervals import IntervalsConnectionCreate, IntervalsConnectionRecord
from backend.models.training import Activity
from backend.services.intervals import (
    IntervalsAuthContext,
    IntervalsConfigurationError,
    IntervalsNotConnectedError,
    IntervalsOAuthService,
    IntervalsSyncError,
    TokenCipher,
    map_intervals_activity,
)

_DEPENDENCY_OVERRIDE_MISSING = object()


def _override_require_user_context(user_context: UserContext) -> Callable[[], None]:
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


class InMemoryIntervalsRepository:
    def __init__(self) -> None:
        self.rows: list[IntervalsConnectionRecord] = []

    def get_active_connection(self, user_id: str) -> IntervalsConnectionRecord | None:
        return next(
            (
                row
                for row in reversed(self.rows)
                if row.user_id == user_id and row.revoked_at is None
            ),
            None,
        )

    def replace_connection(
        self, connection: IntervalsConnectionCreate
    ) -> IntervalsConnectionRecord:
        now = datetime.now(UTC)
        row = IntervalsConnectionRecord(
            id=f"connection-{len(self.rows) + 1}",
            user_id=connection.user_id,
            intervals_athlete_id=connection.intervals_athlete_id,
            intervals_athlete_name=connection.intervals_athlete_name,
            scopes=connection.scopes,
            access_token_ciphertext=connection.access_token_ciphertext,
            token_type=connection.token_type,
            connected_at=now,
            updated_at=now,
            revoked_at=None,
        )
        self.rows.append(row)
        return row

    def revoke_active_connection(self, user_id: str) -> bool:
        row = self.get_active_connection(user_id)
        if row is None:
            return False
        row.revoked_at = datetime.now(UTC)
        return True


class InMemoryActivityRepository:
    def __init__(self, existing_keys: set[str] | None = None) -> None:
        self.existing_keys = existing_keys or set()
        self.created: list[Activity] = []

    async def list_synced_intervals_keys(self, _user_id: str) -> set[str]:
        return set(self.existing_keys)

    async def create_activity(self, activity: Activity) -> Activity:
        persisted = activity.model_copy(update={"id": f"activity-{len(self.created) + 1}"})
        self.created.append(persisted)
        return persisted

    async def list_plan_workouts_between(self, *_args: object, **_kwargs: object) -> list[object]:
        return []


async def _post_sync(
    monkeypatch: pytest.MonkeyPatch,
    *,
    service: IntervalsOAuthService,
    activity_repo: InMemoryActivityRepository,
    payload: dict[str, object],
) -> httpx.Response:
    monkeypatch.setattr(api_index, "intervals_service", service)
    monkeypatch.setattr(api_index, "repo", activity_repo)
    restore_override = _override_require_user_context(UserContext(user_id="user-1"))
    try:
        transport = ASGITransport(app=api_index.app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.post("/api/intervals/sync", json=payload)
    finally:
        restore_override()


@pytest.fixture(autouse=True)
def configured_intervals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backend.services.intervals.settings.intervals_token_encryption_secret",
        "encryption-secret-123",
    )
    monkeypatch.setattr("backend.services.intervals.settings.intervals_dev_api_key", "")
    monkeypatch.setattr("backend.services.intervals.settings.intervals_dev_athlete_id", "")
    monkeypatch.delenv("VERCEL_URL", raising=False)


def test_dev_bypass_resolves_basic_auth(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(
        "backend.services.intervals.settings.intervals_dev_api_key",
        "local-api-key",
    )
    monkeypatch.setattr(
        "backend.services.intervals.settings.intervals_dev_athlete_id",
        "i135168",
    )

    auth = IntervalsOAuthService(repository=InMemoryIntervalsRepository()).resolve_auth(
        "any-logged-in-user"
    )

    expected = base64.b64encode(b"API_KEY:local-api-key").decode()
    assert auth.athlete_id == "i135168"
    assert auth.auth_header == {"Authorization": f"Basic {expected}"}
    assert auth.mode == "dev_api_key"
    assert "using local Intervals API-key bypass athlete_id=i135168" in caplog.text


def test_dev_bypass_is_disabled_on_vercel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backend.services.intervals.settings.intervals_dev_api_key",
        "local-api-key",
    )
    monkeypatch.setattr(
        "backend.services.intervals.settings.intervals_dev_athlete_id",
        "i135168",
    )
    monkeypatch.setenv("VERCEL_URL", "coach-preview.vercel.app")

    with pytest.raises(IntervalsNotConnectedError):
        _ = IntervalsOAuthService(repository=InMemoryIntervalsRepository()).resolve_auth("user-1")


@pytest.mark.parametrize(
    ("api_key", "athlete_id"),
    [("local-api-key", ""), ("", "i135168")],
)
def test_half_configured_dev_bypass_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    api_key: str,
    athlete_id: str,
) -> None:
    monkeypatch.setattr(
        "backend.services.intervals.settings.intervals_dev_api_key",
        api_key,
    )
    monkeypatch.setattr(
        "backend.services.intervals.settings.intervals_dev_athlete_id",
        athlete_id,
    )

    with pytest.raises(IntervalsConfigurationError, match="must both be configured"):
        _ = IntervalsOAuthService(repository=InMemoryIntervalsRepository()).resolve_auth("user-1")


def test_oauth_auth_decrypts_stored_token() -> None:
    repo = InMemoryIntervalsRepository()
    _ = repo.replace_connection(
        IntervalsConnectionCreate(
            user_id="user-1",
            intervals_athlete_id="i135168",
            intervals_athlete_name="Nigel",
            scopes=["ACTIVITY:READ"],
            access_token_ciphertext=TokenCipher("encryption-secret-123").encrypt(
                "oauth-access-token"
            ),
            token_type="Bearer",
        )
    )

    auth = IntervalsOAuthService(repository=repo).resolve_auth("user-1")

    assert auth.athlete_id == "i135168"
    assert auth.auth_header == {"Authorization": "Bearer oauth-access-token"}
    assert auth.mode == "oauth"


def test_oauth_auth_requires_active_connection() -> None:
    with pytest.raises(IntervalsNotConnectedError, match="not connected"):
        _ = IntervalsOAuthService(repository=InMemoryIntervalsRepository()).resolve_auth("user-1")


def test_dev_bypass_returns_synthetic_connected_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.services.intervals.settings.intervals_dev_api_key",
        "local-api-key",
    )
    monkeypatch.setattr(
        "backend.services.intervals.settings.intervals_dev_athlete_id",
        "i135168",
    )

    status = IntervalsOAuthService(repository=InMemoryIntervalsRepository()).get_status("user-1")

    assert status.connected is True
    assert status.intervals_athlete_id == "i135168"
    assert status.scopes == ["ACTIVITY:READ"]


@pytest.mark.asyncio
async def test_fetch_recent_activities_uses_auth_and_date_window() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/athlete/i135168/activities"
        assert dict(request.url.params) == {
            "oldest": "2026-07-01",
            "newest": "2026-07-14",
        }
        assert request.headers["Authorization"] == "Bearer oauth-access-token"
        return httpx.Response(200, json=[{"id": "activity-1"}])

    service = IntervalsOAuthService(
        repository=InMemoryIntervalsRepository(),
        http_client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    activities = await service.fetch_recent_activities(
        IntervalsAuthContext(
            athlete_id="i135168",
            auth_header={"Authorization": "Bearer oauth-access-token"},
            mode="oauth",
        ),
        oldest=date(2026, 7, 1),
        newest=date(2026, 7, 14),
    )

    assert activities == [{"id": "activity-1"}]


@pytest.mark.asyncio
async def test_fetch_recent_activities_wraps_http_errors() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    service = IntervalsOAuthService(
        repository=InMemoryIntervalsRepository(),
        http_client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    auth = IntervalsAuthContext(
        athlete_id="i135168",
        auth_header={"Authorization": "Bearer oauth-access-token"},
        mode="oauth",
    )

    with pytest.raises(IntervalsSyncError, match="could not be fetched"):
        _ = await service.fetch_recent_activities(
            auth,
            oldest=date(2026, 7, 1),
            newest=date(2026, 7, 14),
        )


def test_map_intervals_activity_maps_summary_fields() -> None:
    item = {
        "id": "i987654321",
        "type": "VirtualRide",
        "start_date": "2026-07-14T15:30:00Z",
        "start_date_local": "2026-07-14T08:30:00",
        "moving_time": None,
        "elapsed_time": 3661,
        "distance": 40123.4,
        "total_elevation_gain": 512.6,
        "average_heartrate": 147.4,
        "max_heartrate": 176,
        "average_watts": 204.6,
        "icu_weighted_avg_watts": 221.2,
        "average_cadence": 88.6,
        "icu_training_load": 74.8,
        "icu_intensity": 86.04798,
        "perceived_exertion": 7,
    }

    activity = map_intervals_activity("user-1", item)

    assert activity is not None
    assert activity.sport == "cycling"
    assert activity.activity_date == date(2026, 7, 14)
    assert activity.started_at == datetime(2026, 7, 14, 15, 30, tzinfo=UTC)
    assert activity.duration_seconds == 3661
    assert activity.distance_meters == 40123.4
    assert activity.elevation_gain_meters == 512.6
    assert activity.avg_hr_bpm == 147
    assert activity.max_hr_bpm == 176
    assert activity.avg_power_watts == 205
    assert activity.normalized_power_watts == 221
    assert activity.avg_cadence_rpm == 89
    assert activity.tss == 74.8
    assert activity.intensity_factor == pytest.approx(0.8604798)
    assert activity.rpe == 7
    assert activity.source == "intervals_sync"
    assert activity.source_file_key == "intervals:i987654321"
    assert activity.raw_extraction == {"intervals_summary": item}


@pytest.mark.parametrize(
    ("intervals_type", "expected"),
    [
        ("Ride", "cycling"),
        ("Run", "running"),
        ("Swim", "swimming"),
        ("Rowing", "rowing"),
        ("Hike", "hiking"),
        ("Walk", "walking"),
        ("WeightTraining", "strength"),
        ("UnknownSport", "general"),
    ],
)
def test_map_intervals_activity_normalizes_sports(
    intervals_type: str,
    expected: str,
) -> None:
    activity = map_intervals_activity(
        "user-1",
        {
            "id": "activity-1",
            "type": intervals_type,
            "start_date_local": "2026-07-14T08:30:00",
        },
    )

    assert activity is not None
    assert activity.sport == expected


@pytest.mark.parametrize(
    "item",
    [
        {"start_date_local": "2026-07-14T08:30:00"},
        {"id": None, "start_date_local": "2026-07-14T08:30:00"},
        {"id": "activity-1"},
        {"id": "activity-1", "start_date_local": "not-a-date"},
    ],
)
def test_map_intervals_activity_skips_missing_identity_or_date(item: dict[str, object]) -> None:
    assert map_intervals_activity("user-1", item) is None


@pytest.mark.asyncio
async def test_sync_endpoint_persists_new_and_skips_existing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "id": "i100",
                    "type": "Ride",
                    "start_date_local": "2026-07-14T08:00:00",
                },
                {
                    "id": "i200",
                    "type": "Run",
                    "start_date_local": "2026-07-14T09:00:00",
                    "moving_time": 1800,
                },
            ],
        )

    monkeypatch.setattr(
        "backend.services.intervals.settings.intervals_dev_api_key",
        "local-api-key",
    )
    monkeypatch.setattr(
        "backend.services.intervals.settings.intervals_dev_athlete_id",
        "i135168",
    )
    service = IntervalsOAuthService(
        repository=InMemoryIntervalsRepository(),
        http_client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    activity_repo = InMemoryActivityRepository(existing_keys={"intervals:i100"})

    response = await _post_sync(
        monkeypatch,
        service=service,
        activity_repo=activity_repo,
        payload={"days": 14},
    )

    assert response.status_code == 200
    assert response.json()["synced"] == 1
    assert response.json()["skipped"] == 1
    assert len(response.json()["activities"]) == 1
    assert response.json()["activities"][0]["source_file_key"] == "intervals:i200"
    assert [activity.source_file_key for activity in activity_repo.created] == ["intervals:i200"]


@pytest.mark.asyncio
async def test_sync_endpoint_returns_409_without_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = await _post_sync(
        monkeypatch,
        service=IntervalsOAuthService(repository=InMemoryIntervalsRepository()),
        activity_repo=InMemoryActivityRepository(),
        payload={"days": 14},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Intervals.icu is not connected."


@pytest.mark.asyncio
async def test_sync_endpoint_returns_502_for_intervals_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    monkeypatch.setattr(
        "backend.services.intervals.settings.intervals_dev_api_key",
        "local-api-key",
    )
    monkeypatch.setattr(
        "backend.services.intervals.settings.intervals_dev_athlete_id",
        "i135168",
    )
    service = IntervalsOAuthService(
        repository=InMemoryIntervalsRepository(),
        http_client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    response = await _post_sync(
        monkeypatch,
        service=service,
        activity_repo=InMemoryActivityRepository(),
        payload={"days": 14},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "Intervals.icu activities could not be fetched."


@pytest.mark.asyncio
async def test_sync_endpoint_returns_503_for_half_configured_bypass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.services.intervals.settings.intervals_dev_api_key",
        "local-api-key",
    )

    response = await _post_sync(
        monkeypatch,
        service=IntervalsOAuthService(repository=InMemoryIntervalsRepository()),
        activity_repo=InMemoryActivityRepository(),
        payload={"days": 14},
    )

    assert response.status_code == 503
    assert "must both be configured" in response.json()["detail"]


@pytest.mark.asyncio
async def test_sync_endpoint_rejects_days_outside_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = await _post_sync(
        monkeypatch,
        service=IntervalsOAuthService(repository=InMemoryIntervalsRepository()),
        activity_repo=InMemoryActivityRepository(),
        payload={"days": 0},
    )

    assert response.status_code == 422
