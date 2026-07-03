"""Tests for the compliance engine endpoints:

- POST /api/engine/get-compliance-summary (read-time reconciliation + summary)
- POST /api/engine/resolve-plan-workout (athlete/coach confirm or skip)
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

import api.index as api_index
from backend.models.auth import UserContext
from backend.models.training import Activity, PlanWorkout, TrainingPlan
from backend.repos.supabase_repo import RecordNotFoundError

TODAY = datetime.now(UTC).date()


def _user_context() -> UserContext:
    return UserContext(
        user_id="athlete-1",
        scopes=["plans:write"],
        client_id="test-client",
        grant_id="grant-1",
    )


def _workout(**overrides: Any) -> PlanWorkout:
    fields: dict[str, Any] = {
        "id": "workout-1",
        "plan_id": "plan-1",
        "user_id": "athlete-1",
        "workout_date": TODAY - timedelta(days=2),
        "day_of_week": (TODAY - timedelta(days=2)).weekday(),
        "week_number": 1,
        "sport": "cycling",
        "title": "Endurance ride",
        "workout_type": "endurance",
        "target_duration_minutes": 60,
        "status": "scheduled",
    }
    fields.update(overrides)
    return PlanWorkout.model_validate(fields)


def _activity(**overrides: Any) -> Activity:
    fields: dict[str, Any] = {
        "id": "activity-1",
        "user_id": "athlete-1",
        "sport": "cycling",
        "activity_date": TODAY - timedelta(days=2),
        "duration_seconds": 3700,
    }
    fields.update(overrides)
    return Activity.model_validate(fields)


def _plan() -> TrainingPlan:
    return TrainingPlan(
        id="plan-1",
        user_id="athlete-1",
        title="Base build",
        plan_type="weekly",
        start_date=TODAY - timedelta(days=10),
        end_date=TODAY + timedelta(days=60),
    )


class ComplianceRepository:
    def __init__(
        self,
        *,
        plan: TrainingPlan | None,
        workouts: list[PlanWorkout] | None = None,
        activities: list[Activity] | None = None,
    ) -> None:
        self.plan = plan
        self.workouts = workouts or []
        self.activities = activities or []
        self.workout_updates: list[tuple[str, dict[str, Any]]] = []
        self.activity_updates: list[Activity] = []

    async def get_active_plan(self, user_id: str) -> TrainingPlan | None:
        return self.plan

    async def list_plan_workouts_between(self, user_id, *, start, end) -> list[PlanWorkout]:
        return [w for w in self.workouts if start <= w.workout_date <= end]

    async def list_activities_between(self, user_id, *, start, end) -> list[Activity]:
        return [a for a in self.activities if start <= a.activity_date <= end]

    async def get_plan_workout(self, user_id: str, workout_id: str) -> PlanWorkout:
        for workout in self.workouts:
            if workout.id == workout_id:
                return workout
        raise RecordNotFoundError(f"No plan workout '{workout_id}' for user '{user_id}'.")

    async def update_plan_workout_fields(
        self, user_id: str, workout_id: str, fields: dict[str, Any]
    ) -> PlanWorkout:
        self.workout_updates.append((workout_id, fields))
        workout = await self.get_plan_workout(user_id, workout_id)
        return workout.model_copy(update=fields)

    async def get_activity(self, user_id: str, activity_id: str) -> Activity:
        for activity in self.activities:
            if activity.id == activity_id:
                return activity
        raise RecordNotFoundError(f"Activity '{activity_id}' not found.")

    async def update_activity(self, activity: Activity) -> Activity:
        self.activity_updates.append(activity)
        return activity


async def _post(path: str, body: dict[str, Any]) -> Any:
    transport = ASGITransport(app=api_index.app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(path, json=body)


@pytest.fixture
def as_athlete():
    api_index.app.dependency_overrides[api_index.require_user_context] = _user_context
    yield
    api_index.app.dependency_overrides.clear()


@pytest.mark.usefixtures("as_athlete")
class TestGetComplianceSummary:
    async def test_no_active_plan(self, monkeypatch) -> None:
        monkeypatch.setattr(api_index, "repo", ComplianceRepository(plan=None))
        response = await _post("/api/engine/get-compliance-summary", {})
        assert response.status_code == 200
        assert response.json()["status"] == "no_active_plan"

    async def test_reconciles_matches_and_summarizes(self, monkeypatch) -> None:
        repo = ComplianceRepository(
            plan=_plan(),
            workouts=[
                _workout(),
                _workout(
                    id="workout-2",
                    workout_date=TODAY - timedelta(days=1),
                    sport="running",
                    title="Tempo run",
                ),
            ],
            activities=[_activity()],
        )
        monkeypatch.setattr(api_index, "repo", repo)

        response = await _post("/api/engine/get-compliance-summary", {})

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["totals"]["completed"] == 1
        assert body["totals"]["unconfirmed"] == 1
        assert body["compliance_pct"] == 50.0
        # The confident match was persisted on both sides.
        assert repo.workout_updates == [
            (
                "workout-1",
                {
                    "status": "completed",
                    "actual_activity_id": "activity-1",
                    "completion_source": "auto_matched",
                },
            )
        ]
        assert len(repo.activity_updates) == 1
        assert repo.activity_updates[0].planned_workout_id == "workout-1"
        # The unmatched running workout surfaces as a nudgeable session.
        assert [s["id"] for s in body["unconfirmed_sessions"]] == ["workout-2"]

    async def test_summary_is_idempotent_when_already_matched(self, monkeypatch) -> None:
        repo = ComplianceRepository(
            plan=_plan(),
            workouts=[_workout(status="completed", actual_activity_id="activity-1")],
            activities=[_activity(planned_workout_id="workout-1")],
        )
        monkeypatch.setattr(api_index, "repo", repo)

        response = await _post("/api/engine/get-compliance-summary", {})

        assert response.status_code == 200
        assert repo.workout_updates == []
        assert repo.activity_updates == []
        assert response.json()["compliance_pct"] == 100.0


@pytest.mark.usefixtures("as_athlete")
class TestUnplannedActivities:
    async def test_activity_without_plan_match_is_reported_unplanned(self, monkeypatch) -> None:
        repo = ComplianceRepository(
            plan=_plan(),
            workouts=[],
            activities=[_activity(sport="swimming")],
        )
        monkeypatch.setattr(api_index, "repo", repo)

        response = await _post("/api/engine/get-compliance-summary", {})

        assert response.status_code == 200
        body = response.json()
        assert body["totals"]["unplanned_activities"] == 1
        assert [a["id"] for a in body["unplanned_activities"]] == ["activity-1"]
        assert repo.workout_updates == []


@pytest.mark.usefixtures("as_athlete")
class TestResolvePlanWorkout:
    async def test_athlete_confirms_completed(self, monkeypatch) -> None:
        repo = ComplianceRepository(plan=_plan(), workouts=[_workout()])
        monkeypatch.setattr(api_index, "repo", repo)

        response = await _post(
            "/api/engine/resolve-plan-workout",
            {"plan_workout_id": "workout-1", "outcome": "completed", "source": "athlete"},
        )

        assert response.status_code == 200
        assert response.json()["workout"]["status"] == "completed"
        assert repo.workout_updates == [
            (
                "workout-1",
                {"status": "completed", "completion_source": "athlete_confirmed"},
            )
        ]

    async def test_coach_marks_skipped(self, monkeypatch) -> None:
        repo = ComplianceRepository(plan=_plan(), workouts=[_workout()])
        monkeypatch.setattr(api_index, "repo", repo)

        response = await _post(
            "/api/engine/resolve-plan-workout",
            {"plan_workout_id": "workout-1", "outcome": "skipped", "source": "coach"},
        )

        assert response.status_code == 200
        assert repo.workout_updates == [
            (
                "workout-1",
                {
                    "status": "skipped",
                    "completion_source": "coach_confirmed",
                    "actual_activity_id": None,
                },
            )
        ]

    async def test_completed_with_activity_links_both_sides(self, monkeypatch) -> None:
        repo = ComplianceRepository(plan=_plan(), workouts=[_workout()], activities=[_activity()])
        monkeypatch.setattr(api_index, "repo", repo)

        response = await _post(
            "/api/engine/resolve-plan-workout",
            {
                "plan_workout_id": "workout-1",
                "outcome": "completed",
                "activity_id": "activity-1",
                "source": "athlete",
            },
        )

        assert response.status_code == 200
        assert repo.workout_updates == [
            (
                "workout-1",
                {
                    "status": "completed",
                    "completion_source": "athlete_confirmed",
                    "actual_activity_id": "activity-1",
                },
            )
        ]
        assert len(repo.activity_updates) == 1
        assert repo.activity_updates[0].planned_workout_id == "workout-1"

    async def test_skipping_a_matched_workout_unlinks_the_activity(self, monkeypatch) -> None:
        repo = ComplianceRepository(
            plan=_plan(),
            workouts=[_workout(status="completed", actual_activity_id="activity-1")],
            activities=[_activity(planned_workout_id="workout-1")],
        )
        monkeypatch.setattr(api_index, "repo", repo)

        response = await _post(
            "/api/engine/resolve-plan-workout",
            {"plan_workout_id": "workout-1", "outcome": "skipped", "source": "athlete"},
        )

        assert response.status_code == 200
        assert repo.workout_updates == [
            (
                "workout-1",
                {
                    "status": "skipped",
                    "completion_source": "athlete_confirmed",
                    "actual_activity_id": None,
                },
            )
        ]
        assert len(repo.activity_updates) == 1
        assert repo.activity_updates[0].planned_workout_id is None

    async def test_completing_with_a_different_activity_unlinks_the_prior_one(
        self, monkeypatch
    ) -> None:
        repo = ComplianceRepository(
            plan=_plan(),
            workouts=[_workout(status="completed", actual_activity_id="activity-old")],
            activities=[
                _activity(id="activity-old", planned_workout_id="workout-1"),
                _activity(id="activity-new"),
            ],
        )
        monkeypatch.setattr(api_index, "repo", repo)

        response = await _post(
            "/api/engine/resolve-plan-workout",
            {
                "plan_workout_id": "workout-1",
                "outcome": "completed",
                "activity_id": "activity-new",
                "source": "athlete",
            },
        )

        assert response.status_code == 200
        linked_ids = [(a.id, a.planned_workout_id) for a in repo.activity_updates]
        assert ("activity-new", "workout-1") in linked_ids
        assert ("activity-old", None) in linked_ids

    async def test_unknown_workout_returns_404(self, monkeypatch) -> None:
        monkeypatch.setattr(api_index, "repo", ComplianceRepository(plan=_plan()))
        response = await _post(
            "/api/engine/resolve-plan-workout",
            {"plan_workout_id": "nope", "outcome": "completed", "source": "athlete"},
        )
        assert response.status_code == 404

    async def test_unknown_activity_returns_404(self, monkeypatch) -> None:
        repo = ComplianceRepository(plan=_plan(), workouts=[_workout()])
        monkeypatch.setattr(api_index, "repo", repo)
        response = await _post(
            "/api/engine/resolve-plan-workout",
            {
                "plan_workout_id": "workout-1",
                "outcome": "completed",
                "activity_id": "nope",
                "source": "athlete",
            },
        )
        assert response.status_code == 404
        assert repo.workout_updates == []

    async def test_invalid_outcome_rejected(self, monkeypatch) -> None:
        monkeypatch.setattr(api_index, "repo", ComplianceRepository(plan=_plan()))
        response = await _post(
            "/api/engine/resolve-plan-workout",
            {"plan_workout_id": "workout-1", "outcome": "missed", "source": "athlete"},
        )
        assert response.status_code == 422


@pytest.mark.usefixtures("as_athlete")
class TestWriteTimeMatchHook:
    async def test_saved_activity_auto_matches_plan_workout(self, monkeypatch) -> None:
        from types import SimpleNamespace

        from backend.services import activity_text

        saved_activity = _activity(id=None)

        class HookRepository(ComplianceRepository):
            async def get_athlete_profile(self, user_id: str):
                from backend.models.athlete import AthleteProfile

                return AthleteProfile(user_id=user_id)

            async def get_active_thresholds(self, user_id: str):
                return []

            async def create_activity(self, activity: Activity) -> Activity:
                created = activity.model_copy(update={"id": "activity-1"})
                self.activities.append(created)
                return created

        repo = HookRepository(plan=_plan(), workouts=[_workout()])
        monkeypatch.setattr(api_index, "repo", repo)

        async def fake_build_activity_from_text(_text, *, user_id, profile, thresholds):
            return SimpleNamespace(activity=saved_activity, missing=[], raw_extraction={})

        monkeypatch.setattr(
            activity_text, "build_activity_from_text", fake_build_activity_from_text
        )

        response = await _post(
            "/api/engine/save-activity-from-text",
            {"text": "Rode 62 minutes easy."},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "saved"
        assert body["matched_plan_workout"]["plan_workout_id"] == "workout-1"
        assert repo.workout_updates == [
            (
                "workout-1",
                {
                    "status": "completed",
                    "actual_activity_id": "activity-1",
                    "completion_source": "auto_matched",
                },
            )
        ]
        assert repo.activity_updates[-1].planned_workout_id == "workout-1"

    async def test_match_failure_does_not_fail_save(self, monkeypatch) -> None:
        from types import SimpleNamespace

        from backend.services import activity_text

        saved_activity = _activity(id=None)

        class BrokenMatchRepository(ComplianceRepository):
            async def get_athlete_profile(self, user_id: str):
                from backend.models.athlete import AthleteProfile

                return AthleteProfile(user_id=user_id)

            async def get_active_thresholds(self, user_id: str):
                return []

            async def create_activity(self, activity: Activity) -> Activity:
                return activity.model_copy(update={"id": "activity-1"})

            async def list_plan_workouts_between(self, user_id, *, start, end):
                raise RuntimeError("matching backend down")

        monkeypatch.setattr(api_index, "repo", BrokenMatchRepository(plan=_plan()))

        async def fake_build_activity_from_text(_text, *, user_id, profile, thresholds):
            return SimpleNamespace(activity=saved_activity, missing=[], raw_extraction={})

        monkeypatch.setattr(
            activity_text, "build_activity_from_text", fake_build_activity_from_text
        )

        response = await _post(
            "/api/engine/save-activity-from-text",
            {"text": "Rode 62 minutes easy."},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "saved"
        assert "matched_plan_workout" not in body
