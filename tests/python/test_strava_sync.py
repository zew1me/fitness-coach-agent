"""Tests for Strava activity mapping, pagination, and rate-limit handling."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from backend.models.athlete import AthleteProfile, SportThreshold
from backend.models.strava import StravaConnectionRecord
from backend.services.strava import (
    StravaAuthContext,
    StravaOAuthService,
    StravaRateLimitError,
    StravaStreams,
    StravaSyncError,
    estimate_strava_training_load,
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


def test_mapping_leaves_training_load_for_athlete_context_enrichment() -> None:
    activity = map_strava_activity("coach-user-1", _ATHLETE_ID, _summary())
    assert activity is not None
    assert activity.tss is None
    assert activity.intensity_factor is None
    assert activity.zone_distribution is None


def test_training_load_uses_athlete_power_thresholds() -> None:
    activity = map_strava_activity("coach-user-1", _ATHLETE_ID, _summary())
    assert activity is not None

    enriched = estimate_strava_training_load(
        activity,
        profile=AthleteProfile(user_id="coach-user-1"),
        threshold=SportThreshold(
            user_id="coach-user-1",
            sport="cycling",
            lt2_power_watts=250,
        ),
    )

    assert enriched.intensity_factor == 0.94
    assert enriched.tss == pytest.approx(88.4)
    assert enriched.zone_distribution is None  # Summary data has no time-in-zone stream.
    assert enriched.raw_extraction is not None
    assert enriched.raw_extraction["training_load_estimate"] == {
        "method": "athlete_threshold_power"
    }


def test_training_load_uses_streams_without_storing_raw_samples() -> None:
    activity = map_strava_activity("coach-user-1", _ATHLETE_ID, _summary())
    assert activity is not None
    streams: StravaStreams = {
        "time": [0, 1, 2, 3, 4, 5],
        "watts": [200, 200, 200, 200, 200, 200],
        "moving": [True, True, True, True, True, True],
    }

    enriched = estimate_strava_training_load(
        activity,
        profile=AthleteProfile(user_id="coach-user-1"),
        threshold=SportThreshold(
            user_id="coach-user-1",
            sport="cycling",
            lt1_power_watts=188,
            lt2_power_watts=250,
        ),
        streams=streams,
    )

    assert enriched.normalized_power_watts == 200
    assert enriched.intensity_factor == 0.8
    assert enriched.tss == pytest.approx(64.0)
    assert enriched.zone_distribution == {
        "zone_1": 0.0,
        "zone_2": 0.0,
        "zone_3": 100.0,
        "zone_4": 0.0,
        "zone_5": 0.0,
        "zone_6": 0.0,
    }
    assert enriched.raw_extraction is not None
    assert enriched.raw_extraction["strava_stream_derivation"] == {
        "sample_count": 6,
        "streams_used": ["moving", "time", "watts"],
        "raw_samples_stored": False,
    }


def test_all_zero_power_stream_does_not_replace_summary_power() -> None:
    activity = map_strava_activity("coach-user-1", _ATHLETE_ID, _summary())
    assert activity is not None

    enriched = estimate_strava_training_load(
        activity,
        profile=AthleteProfile(user_id="coach-user-1"),
        threshold=SportThreshold(
            user_id="coach-user-1",
            sport="cycling",
            lt2_power_watts=250,
        ),
        streams={"time": [0, 1, 2], "watts": [0, 0, 0]},
    )

    assert enriched.normalized_power_watts == 235
    assert enriched.intensity_factor == 0.94
    assert enriched.tss == pytest.approx(88.4)


def test_training_load_falls_back_to_athlete_heart_rate_profile() -> None:
    activity = map_strava_activity(
        "coach-user-1",
        _ATHLETE_ID,
        _summary(weighted_average_watts=None),
    )
    assert activity is not None

    enriched = estimate_strava_training_load(
        activity,
        profile=AthleteProfile(
            user_id="coach-user-1",
            biological_sex="female",
            resting_hr_bpm=50,
            max_hr_bpm=190,
        ),
        threshold=None,
    )

    assert enriched.tss is not None
    assert enriched.tss > 0
    assert enriched.intensity_factor is None


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
async def test_fetch_activity_streams_requests_only_allowlisted_non_gps_data() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/api/v3/activities/{_ATHLETE_ID}/streams"
        keys = set(request.url.params["keys"].split(","))
        assert keys == {
            "time",
            "heartrate",
            "cadence",
            "watts",
            "velocity_smooth",
            "moving",
        }
        assert "latlng" not in keys
        assert request.url.params["key_by_type"] == "true"
        return httpx.Response(
            200,
            json={
                "time": {"data": [0, 1, 2]},
                "watts": {"data": [190, 210, 220]},
                "moving": {"data": [True, True, False]},
                "latlng": {"data": [[51.5, -0.1]]},
            },
        )

    streams = await _service_with(handler).fetch_activity_streams(_auth(), str(_ATHLETE_ID))

    assert streams == {
        "time": [0, 1, 2],
        "watts": [190, 210, 220],
        "moving": [True, True, False],
    }


@pytest.mark.asyncio
async def test_fetch_activity_streams_tolerates_unavailable_streams() -> None:
    service = _service_with(lambda _request: httpx.Response(404))

    assert await service.fetch_activity_streams(_auth(), str(_ATHLETE_ID)) == {}


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
