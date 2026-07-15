import base64
from datetime import UTC, date, datetime

import httpx
import pytest

from backend.models.intervals import IntervalsConnectionCreate, IntervalsConnectionRecord
from backend.services.intervals import (
    IntervalsAuthContext,
    IntervalsConfigurationError,
    IntervalsNotConnectedError,
    IntervalsOAuthService,
    IntervalsSyncError,
    TokenCipher,
    map_intervals_activity,
)


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
