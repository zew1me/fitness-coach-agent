"""Tests for POST /api/engine/recalibrate-thresholds."""

from datetime import date
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

import api.index as api_index
from backend.models.athlete import SportThreshold
from backend.models.auth import UserContext
from backend.models.training import Activity

TODAY = date.today()


def _user_context() -> UserContext:
    return UserContext(
        user_id="athlete-1",
        scopes=["plans:write"],
        client_id="test-client",
        grant_id="grant-1",
    )


def _activity(**overrides: Any) -> Activity:
    fields: dict[str, Any] = {
        "id": "activity-1",
        "user_id": "athlete-1",
        "sport": "running",
        "activity_date": TODAY,
        "distance_meters": 5000,
        "duration_seconds": 1080,
        "rpe": 9,
    }
    fields.update(overrides)
    return Activity.model_validate(fields)


def _threshold(**overrides: Any) -> SportThreshold:
    fields: dict[str, Any] = {
        "id": "threshold-1",
        "user_id": "athlete-1",
        "sport": "running",
        "source": "estimated",
    }
    fields.update(overrides)
    return SportThreshold.model_validate(fields)


class RecalibrationRepository:
    def __init__(
        self,
        *,
        thresholds: list[SportThreshold] | None = None,
        activities_by_sport: dict[str, list[Activity]] | None = None,
    ) -> None:
        self.thresholds = thresholds or []
        self.activities_by_sport = activities_by_sport or {}
        self.upserted: list[SportThreshold] = []

    async def get_active_thresholds(self, user_id: str) -> list[SportThreshold]:
        return self.thresholds

    async def list_activities(
        self, user_id: str, *, sport: str | None = None, since=None, limit: int = 50
    ) -> list[Activity]:
        return self.activities_by_sport.get(sport or "", [])

    async def upsert_sport_threshold(self, threshold: SportThreshold) -> SportThreshold:
        self.upserted.append(threshold)
        return threshold.model_copy(update={"id": threshold.id or "new-threshold"})


async def _post(body: dict[str, Any]) -> Any:
    transport = ASGITransport(app=api_index.app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post("/api/engine/recalibrate-thresholds", json=body)


@pytest.fixture
def as_athlete():
    api_index.app.dependency_overrides[api_index.require_user_context] = _user_context
    yield
    api_index.app.dependency_overrides.clear()


@pytest.mark.usefixtures("as_athlete")
class TestRecalibrateThresholdsEndpoint:
    async def test_eligible_evidence_persists_and_returns_explanation(self, monkeypatch) -> None:
        repo = RecalibrationRepository(
            thresholds=[],
            activities_by_sport={"running": [_activity()], "cycling": []},
        )
        monkeypatch.setattr(api_index, "repo", repo)

        response = await _post({})

        assert response.status_code == 200
        body = response.json()
        running_result = next(r for r in body["results"] if r["sport"] == "running")
        assert running_result["status"] == "recalibrated"
        assert running_result["explanation"]
        assert len(repo.upserted) == 1
        assert repo.upserted[0].sport == "running"
        assert repo.upserted[0].source == "estimated"

    async def test_insufficient_evidence_does_not_persist(self, monkeypatch) -> None:
        easy_run = _activity(rpe=None, duration_seconds=1800)  # slow, no rpe, no current to compare
        repo = RecalibrationRepository(
            thresholds=[],
            activities_by_sport={"running": [easy_run], "cycling": []},
        )
        monkeypatch.setattr(api_index, "repo", repo)

        response = await _post({})

        assert response.status_code == 200
        body = response.json()
        running_result = next(r for r in body["results"] if r["sport"] == "running")
        assert running_result["status"] == "insufficient_evidence"
        assert repo.upserted == []

    async def test_user_confirmed_threshold_is_not_overridden(self, monkeypatch) -> None:
        current = _threshold(
            source="user",
            lt2_pace_sec_per_km=300,
            lt1_pace_sec_per_km=330,
        )
        repo = RecalibrationRepository(
            thresholds=[current],
            activities_by_sport={"running": [_activity()], "cycling": []},
        )
        monkeypatch.setattr(api_index, "repo", repo)

        response = await _post({})

        assert response.status_code == 200
        body = response.json()
        running_result = next(r for r in body["results"] if r["sport"] == "running")
        assert running_result["status"] == "already_user_confirmed"
        assert repo.upserted == []

    async def test_never_returns_pending_implementation(self, monkeypatch) -> None:
        repo = RecalibrationRepository()
        monkeypatch.setattr(api_index, "repo", repo)

        response = await _post({})

        assert response.status_code == 200
        assert response.json() != {"status": "pending_implementation"}


async def test_recalibrate_thresholds_requires_auth() -> None:
    response = await _post({})
    assert response.status_code == 401
