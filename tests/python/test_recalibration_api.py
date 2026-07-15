"""Tests for the threshold recalibration candidate workflow."""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

import api.index as api_index
from backend.models.athlete import SportThreshold, ThresholdRecalibrationCandidate
from backend.models.auth import UserContext
from backend.models.training import Activity
from backend.repos.supabase_repo import RecordNotFoundError

TODAY = date.today()
NOW = datetime.now(UTC)


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
        self.created_candidates: list[ThresholdRecalibrationCandidate] = []
        self.latest_candidates: dict[str, ThresholdRecalibrationCandidate | None] = {}
        self.candidates_by_id: dict[str, ThresholdRecalibrationCandidate] = {}
        self.decisions: list[dict[str, Any]] = []
        self.active_threshold_user_ids: list[str] = []
        self.activity_calls: list[dict[str, Any]] = []
        self.raise_not_found_on_decide = False

    async def get_active_thresholds(self, user_id: str) -> list[SportThreshold]:
        self.active_threshold_user_ids.append(user_id)
        return self.thresholds

    async def list_activities(
        self, user_id: str, *, sport: str | None = None, since=None, limit: int = 50
    ) -> list[Activity]:
        self.activity_calls.append(
            {"limit": limit, "since": since, "sport": sport, "user_id": user_id}
        )
        return self.activities_by_sport.get(sport or "", [])

    async def upsert_sport_threshold(self, threshold: SportThreshold) -> SportThreshold:
        self.upserted.append(threshold)
        return threshold.model_copy(update={"id": threshold.id or "new-threshold"})

    async def get_latest_recalibration_candidate(
        self, user_id: str, sport: str
    ) -> ThresholdRecalibrationCandidate | None:
        return self.latest_candidates.get(f"{user_id}:{sport}")

    async def create_recalibration_candidate(
        self, candidate: ThresholdRecalibrationCandidate
    ) -> ThresholdRecalibrationCandidate:
        saved = candidate.model_copy(update={"id": candidate.id or "candidate-1"})
        self.created_candidates.append(saved)
        self.candidates_by_id[saved.id or ""] = saved
        return saved

    async def get_recalibration_candidate(
        self, user_id: str, candidate_id: str
    ) -> ThresholdRecalibrationCandidate | None:
        candidate = self.candidates_by_id.get(candidate_id)
        if candidate is None or candidate.user_id != user_id:
            return None
        return candidate

    async def decide_recalibration_candidate(
        self,
        *,
        user_id: str,
        candidate_id: str,
        status: str,
        threshold: SportThreshold | None = None,
    ) -> tuple[ThresholdRecalibrationCandidate, SportThreshold | None]:
        if self.raise_not_found_on_decide:
            raise RecordNotFoundError("Recalibration candidate not found.")
        candidate = self.candidates_by_id[candidate_id]
        saved_threshold = None
        if threshold is not None:
            saved_threshold = threshold.model_copy(update={"id": threshold.id or "new-threshold"})
            self.upserted.append(saved_threshold)
        decided = candidate.model_copy(
            update={
                "decided_at": NOW,
                "manual_threshold": saved_threshold if status == "manual_entered" else None,
                "status": status,
            }
        )
        self.candidates_by_id[candidate_id] = decided
        self.decisions.append(
            {
                "candidate_id": candidate_id,
                "threshold": threshold,
                "status": status,
                "user_id": user_id,
            }
        )
        return decided, saved_threshold


async def _post(body: dict[str, Any]) -> Any:
    transport = ASGITransport(app=api_index.app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post("/api/engine/recalibrate-thresholds", json=body)


async def _post_decision(body: dict[str, Any]) -> Any:
    transport = ASGITransport(app=api_index.app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post("/api/engine/recalibration-candidate-decision", json=body)


@pytest.fixture
def as_athlete():
    api_index.app.dependency_overrides[api_index.require_user_context] = _user_context
    yield
    api_index.app.dependency_overrides.clear()


@pytest.mark.usefixtures("as_athlete")
class TestRecalibrateThresholdsEndpoint:
    async def test_eligible_evidence_queues_candidate_without_persisting_threshold(
        self, monkeypatch
    ) -> None:
        repo = RecalibrationRepository(
            thresholds=[],
            activities_by_sport={"running": [_activity()], "cycling": []},
        )
        monkeypatch.setattr(api_index, "repo", repo)

        response = await _post({})

        assert response.status_code == 200
        body = response.json()
        running_result = next(r for r in body["results"] if r["sport"] == "running")
        assert running_result["status"] == "candidate_queued"
        assert running_result["candidate_id"] == "candidate-1"
        assert running_result["explanation"]
        assert repo.upserted == []
        assert len(repo.created_candidates) == 1
        assert repo.created_candidates[0].sport == "running"
        assert repo.created_candidates[0].candidate_threshold.source == "file"
        assert repo.created_candidates[0].user_id == "athlete-1"
        assert repo.active_threshold_user_ids == ["athlete-1"]
        assert sorted(repo.activity_calls, key=lambda call: call["sport"] or "") == [
            {
                "limit": 200,
                "since": TODAY - timedelta(days=90),
                "sport": "cycling",
                "user_id": "athlete-1",
            },
            {
                "limit": 200,
                "since": TODAY - timedelta(days=90),
                "sport": "running",
                "user_id": "athlete-1",
            },
        ]

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
        assert repo.created_candidates == []

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
        assert repo.created_candidates == []

    async def test_duplicate_active_thresholds_keep_first_current_threshold(
        self, monkeypatch
    ) -> None:
        user_confirmed = _threshold(
            id="newer-threshold",
            source="user",
            lt2_pace_sec_per_km=300,
            lt1_pace_sec_per_km=330,
        )
        older_estimated = _threshold(
            id="older-threshold",
            source="estimated",
            lt2_pace_sec_per_km=360,
            lt1_pace_sec_per_km=390,
        )
        repo = RecalibrationRepository(
            thresholds=[user_confirmed, older_estimated],
            activities_by_sport={"running": [_activity()], "cycling": []},
        )
        monkeypatch.setattr(api_index, "repo", repo)

        response = await _post({})

        assert response.status_code == 200
        body = response.json()
        running_result = next(r for r in body["results"] if r["sport"] == "running")
        assert running_result["status"] == "already_user_confirmed"
        assert repo.upserted == []

    async def test_medium_and_high_confidence_candidates_wait_at_least_28_days(
        self, monkeypatch
    ) -> None:
        recent_candidate = ThresholdRecalibrationCandidate(
            user_id="athlete-1",
            sport="running",
            status="kept_current",
            confidence="high",
            evidence_activity_id="previous",
            explanation="Previous proposal",
            candidate_threshold=_threshold(),
            generated_at=NOW - timedelta(days=27),
        )
        repo = RecalibrationRepository(
            thresholds=[],
            activities_by_sport={"running": [_activity(rpe=9)], "cycling": []},
        )
        repo.latest_candidates["athlete-1:running"] = recent_candidate
        monkeypatch.setattr(api_index, "repo", repo)

        response = await _post({})

        assert response.status_code == 200
        running_result = next(r for r in response.json()["results"] if r["sport"] == "running")
        assert running_result["status"] == "cadence_gated"
        assert (
            running_result["next_eligible_date"]
            == (recent_candidate.generated_at.date() + timedelta(days=28)).isoformat()
        )
        assert repo.created_candidates == []

    async def test_never_returns_pending_implementation(self, monkeypatch) -> None:
        repo = RecalibrationRepository()
        monkeypatch.setattr(api_index, "repo", repo)

        response = await _post({})

        assert response.status_code == 200
        assert response.json() != {"status": "pending_implementation"}


async def test_recalibrate_thresholds_requires_auth() -> None:
    response = await _post({})
    assert response.status_code == 401


@pytest.mark.usefixtures("as_athlete")
class TestRecalibrationCandidateDecisionEndpoint:
    async def test_accept_candidate_persists_candidate_threshold(self, monkeypatch) -> None:
        candidate_threshold = _threshold(
            id=None,
            source="file",
            lt2_pace_sec_per_km=250,
            lt1_pace_sec_per_km=285,
            confidence="high",
            estimation_method="race_time",
        )
        candidate = ThresholdRecalibrationCandidate(
            id="candidate-1",
            user_id="athlete-1",
            sport="running",
            status="pending",
            confidence="high",
            evidence_activity_id="activity-1",
            explanation="Faster 5K.",
            candidate_threshold=candidate_threshold,
        )
        repo = RecalibrationRepository()
        repo.candidates_by_id["candidate-1"] = candidate
        monkeypatch.setattr(api_index, "repo", repo)

        response = await _post_decision(
            {"candidate_id": "candidate-1", "decision": "accept_candidate"}
        )

        assert response.status_code == 200
        assert response.json()["candidate"]["status"] == "accepted"
        assert len(repo.upserted) == 1
        assert repo.upserted[0].source == "file"
        assert repo.decisions[0]["status"] == "accepted"

    async def test_keep_current_records_decision_without_persisting_threshold(
        self, monkeypatch
    ) -> None:
        candidate = ThresholdRecalibrationCandidate(
            id="candidate-1",
            user_id="athlete-1",
            sport="running",
            status="pending",
            confidence="high",
            evidence_activity_id="activity-1",
            explanation="Faster 5K.",
            candidate_threshold=_threshold(source="file"),
        )
        repo = RecalibrationRepository()
        repo.candidates_by_id["candidate-1"] = candidate
        monkeypatch.setattr(api_index, "repo", repo)

        response = await _post_decision({"candidate_id": "candidate-1", "decision": "keep_current"})

        assert response.status_code == 200
        assert response.json()["candidate"]["status"] == "kept_current"
        assert repo.upserted == []
        assert repo.decisions[0]["status"] == "kept_current"

    async def test_manual_threshold_persists_user_confirmed_threshold(self, monkeypatch) -> None:
        candidate = ThresholdRecalibrationCandidate(
            id="candidate-1",
            user_id="athlete-1",
            sport="running",
            status="pending",
            confidence="medium",
            evidence_activity_id="activity-1",
            explanation="Faster 5K.",
            candidate_threshold=_threshold(source="file"),
        )
        repo = RecalibrationRepository()
        repo.candidates_by_id["candidate-1"] = candidate
        monkeypatch.setattr(api_index, "repo", repo)

        response = await _post_decision(
            {
                "candidate_id": "candidate-1",
                "decision": "manual_threshold",
                "manual_threshold": {
                    "lt2_pace_sec_per_km": 260,
                    "lt1_pace_sec_per_km": 300,
                },
            }
        )

        assert response.status_code == 200
        assert response.json()["candidate"]["status"] == "manual_entered"
        assert len(repo.upserted) == 1
        assert repo.upserted[0].source == "user"
        assert repo.upserted[0].confidence == "high"
        assert repo.upserted[0].estimation_method == "manual"
        assert repo.upserted[0].lt2_pace_sec_per_km == 260
        assert repo.decisions[0]["status"] == "manual_entered"

    async def test_concurrent_decision_returns_conflict(self, monkeypatch) -> None:
        """A second request racing the first to decide the same candidate gets a 409.

        The endpoint's own pending check (`candidate.status != "pending"`) can't catch
        this: both requests read the row while it's still pending. The repo's atomic
        `.eq("status", "pending")` update guard is what actually loses the race, raising
        RecordNotFoundError for the loser.
        """
        candidate = ThresholdRecalibrationCandidate(
            id="candidate-1",
            user_id="athlete-1",
            sport="running",
            status="pending",
            confidence="high",
            evidence_activity_id="activity-1",
            explanation="Faster 5K.",
            candidate_threshold=_threshold(source="file"),
        )
        repo = RecalibrationRepository()
        repo.candidates_by_id["candidate-1"] = candidate
        repo.raise_not_found_on_decide = True
        monkeypatch.setattr(api_index, "repo", repo)

        response = await _post_decision({"candidate_id": "candidate-1", "decision": "keep_current"})

        assert response.status_code == 409
