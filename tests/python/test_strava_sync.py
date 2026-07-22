"""Tests for Strava activity mapping, pagination, and rate-limit handling."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from backend.models.strava import StravaConnectionRecord
from backend.services.strava import (
    StravaAuthContext,
    StravaOAuthService,
    StravaRateLimitError,
    StravaSyncError,
    map_strava_activity,
)

_ATHLETE_ID = 135168


def _summary(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": 998877,
        "sport_type": "Ride",
        "type": "Workout",  # deprecated field; must lose to sport_type
        "start_date": "2026-07-20T14:00:00Z",
        "start_date_local": "2026-07-20T16:00:00Z",
        "moving_time": 3600,
        "elapsed_time": 3900,
        "distance": 40000.0,
        "total_elevation_gain": 350.0,
        "average_heartrate": 145.0,
        "max_heartrate": 172.0,
        "average_watts": 210.0,
        "weighted_average_watts": 235.0,
        "average_cadence": 88.0,
        "name": "Morning gravel",
        "map": {"polyline": "secret-gps-data"},
        "start_latlng": [51.5, -0.1],
    }
    base.update(overrides)
    return base


def test_mapping_prefers_sport_type_and_maps_metrics() -> None:
    activity = map_strava_activity("coach-user-1", _ATHLETE_ID, _summary())

    assert activity is not None
    assert activity.sport == "cycling"  # from sport_type "Ride", not type "Workout"
    assert activity.activity_date.isoformat() == "2026-07-20"
    assert activity.started_at is not None
    assert activity.duration_seconds == 3600  # moving_time preferred
    assert activity.distance_meters == 40000.0
    assert activity.avg_hr_bpm == 145
    assert activity.avg_power_watts == 210
    assert activity.normalized_power_watts == 235  # weighted_average_watts → NP
    assert activity.source == "strava_sync"
    assert activity.source_file_key == "strava:135168:998877"


def test_mapping_does_not_fabricate_tss_if_or_zones() -> None:
    activity = map_strava_activity("coach-user-1", _ATHLETE_ID, _summary())
    assert activity is not None
    assert activity.tss is None
    assert activity.intensity_factor is None
    assert activity.zone_distribution is None


def test_mapping_provenance_excludes_gps_and_map() -> None:
    activity = map_strava_activity("coach-user-1", _ATHLETE_ID, _summary())
    assert activity is not None
    assert activity.raw_extraction is not None
    provenance = activity.raw_extraction["strava_summary"]
    assert "map" not in provenance
    assert "start_latlng" not in provenance
    assert provenance["weighted_average_watts"] == 235.0


def test_mapping_falls_back_to_deprecated_type_when_sport_type_absent() -> None:
    item = _summary()
    del item["sport_type"]
    item["type"] = "Run"
    activity = map_strava_activity("coach-user-1", _ATHLETE_ID, item)
    assert activity is not None
    assert activity.sport == "running"


def test_mapping_skips_records_without_id_or_date() -> None:
    assert map_strava_activity("coach-user-1", _ATHLETE_ID, {"sport_type": "Ride"}) is None
    no_date = _summary()
    del no_date["start_date"]
    del no_date["start_date_local"]
    assert map_strava_activity("coach-user-1", _ATHLETE_ID, no_date) is None


def _auth() -> StravaAuthContext:
    now = datetime.now(UTC)
    record = StravaConnectionRecord(
        id="conn-1",
        user_id="coach-user-1",
        strava_athlete_id=_ATHLETE_ID,
        access_token_ciphertext="ct",
        refresh_token_ciphertext="ct",
        expires_at=now + timedelta(hours=5),
        connected_at=now,
        updated_at=now,
    )
    return StravaAuthContext(connection=record, access_token="access-1")


def _service_with(handler: object) -> StravaOAuthService:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return StravaOAuthService(
        repository=object(),  # type: ignore[arg-type]  # fetch never touches the repo
        http_client_factory=lambda: httpx.AsyncClient(transport=transport),
    )


@pytest.mark.asyncio
async def test_fetch_paginates_until_short_page() -> None:
    pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        pages.append(page)
        # Page 1 full (100), page 2 short (2) → stop after page 2.
        count = 100 if page == 1 else 2
        return httpx.Response(200, json=[{"id": page * 1000 + i} for i in range(count)])

    service = _service_with(handler)
    now = datetime.now(UTC)
    items = await service.fetch_activities(_auth(), after=now - timedelta(days=7), before=now)

    assert pages == [1, 2]
    assert len(items) == 102


@pytest.mark.asyncio
async def test_fetch_raises_rate_limit_with_retry_guidance() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"message": "Rate Limit Exceeded"})

    service = _service_with(handler)
    now = datetime.now(UTC)
    with pytest.raises(StravaRateLimitError) as excinfo:
        await service.fetch_activities(_auth(), after=now - timedelta(days=7), before=now)
    assert excinfo.value.retry_after_seconds is not None
    assert excinfo.value.retry_after_seconds > 0


@pytest.mark.asyncio
async def test_fetch_rejects_non_list_payload() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "object"})

    service = _service_with(handler)
    now = datetime.now(UTC)
    with pytest.raises(StravaSyncError):
        await service.fetch_activities(_auth(), after=now - timedelta(days=7), before=now)


@pytest.mark.asyncio
async def test_fetch_skips_non_dict_items_without_aborting() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"id": 1}, "garbage", {"id": 2}])

    service = _service_with(handler)
    now = datetime.now(UTC)
    items = await service.fetch_activities(_auth(), after=now - timedelta(days=7), before=now)
    assert [item["id"] for item in items] == [1, 2]
