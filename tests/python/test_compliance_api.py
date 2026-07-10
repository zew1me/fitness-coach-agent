"""Tests for the compliance engine endpoints:

- POST /api/engine/get-compliance-summary (read-time reconciliation + summary)
- POST /api/engine/resolve-plan-workout (athlete/coach confirm or skip)
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from postgrest.exceptions import APIError as PostgRESTAPIError

import api.index as api_index
from backend.models.auth import UserContext
from backend.models.training import Activity, PlanWorkout, TrainingPlan
from backend.repos.supabase_repo import RecordNotFoundError

TODAY = datetime.now(UTC).date()
# resolve_plan_workout now enforces a real UUID at the boundary, so tests use a
# canonical uuid rather than the "workout-1" style sentinel used elsewhere.
WORKOUT_ID = "00000000-0000-0000-0000-000000000011"


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

    async def match_plan_workout_to_activity(
        self,
        *,
        user_id: str,
        workout_id: str,
        activity_id: str,
        completion_source: str,
    ) -> PlanWorkout:
        workout = await self.update_plan_workout_fields(
            user_id,
            workout_id,
            {
                "status": "completed",
                "actual_activity_id": activity_id,
                "completion_source": completion_source,
            },
        )
        activity = await self.get_activity(user_id, activity_id)
        await self.update_activity(activity.model_copy(update={"planned_workout_id": workout_id}))
        return workout

    async def resolve_plan_workout_atomic(
        self,
        *,
        user_id: str,
        workout_id: str,
        outcome: str,
        activity_id: str | None,
        source: str,
    ) -> PlanWorkout:
        workout = await self.get_plan_workout(user_id, workout_id)
        activity: Activity | None = None
        if activity_id is not None:
            activity = await self.get_activity(user_id, activity_id)
        fields: dict[str, Any] = {
            "status": outcome,
            "completion_source": f"{source}_confirmed",
        }
        if activity_id is not None:
            fields["actual_activity_id"] = activity_id
        if outcome == "skipped":
            fields["actual_activity_id"] = None
        updated = await self.update_plan_workout_fields(user_id, workout_id, fields)
        if activity is not None:
            await self.update_activity(
                activity.model_copy(update={"planned_workout_id": workout_id})
            )
        replaced = activity_id is not None and workout.actual_activity_id not in (None, activity_id)
        if workout.actual_activity_id is not None and (outcome == "skipped" or replaced):
            prior = await self.get_activity(user_id, workout.actual_activity_id)
            await self.update_activity(prior.model_copy(update={"planned_workout_id": None}))
        return updated


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

    async def test_stale_match_is_skipped_without_failing_the_summary(self, monkeypatch) -> None:
        class StaleMatchRepository(ComplianceRepository):
            async def match_plan_workout_to_activity(self, **kwargs: Any) -> PlanWorkout:
                raise RecordNotFoundError("Plan workout was deleted since matching.")

        repo = StaleMatchRepository(
            plan=_plan(),
            workouts=[_workout()],
            activities=[_activity()],
        )
        monkeypatch.setattr(api_index, "repo", repo)

        response = await _post("/api/engine/get-compliance-summary", {})

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        # The stale match must not be reflected as completed in the summary,
        # and must not have persisted any workout/activity update.
        assert repo.workout_updates == []
        assert repo.activity_updates == []
        assert body["totals"]["unconfirmed"] == 1
        assert body["totals"]["completed"] == 0


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
        repo = ComplianceRepository(plan=_plan(), workouts=[_workout(id=WORKOUT_ID)])
        monkeypatch.setattr(api_index, "repo", repo)

        response = await _post(
            "/api/engine/resolve-plan-workout",
            {"plan_workout_id": WORKOUT_ID, "outcome": "completed", "source": "athlete"},
        )

        assert response.status_code == 200
        assert response.json()["workout"]["status"] == "completed"
        assert repo.workout_updates == [
            (
                WORKOUT_ID,
                {"status": "completed", "completion_source": "athlete_confirmed"},
            )
        ]

    async def test_coach_marks_skipped(self, monkeypatch) -> None:
        repo = ComplianceRepository(plan=_plan(), workouts=[_workout(id=WORKOUT_ID)])
        monkeypatch.setattr(api_index, "repo", repo)

        response = await _post(
            "/api/engine/resolve-plan-workout",
            {"plan_workout_id": WORKOUT_ID, "outcome": "skipped", "source": "coach"},
        )

        assert response.status_code == 200
        assert repo.workout_updates == [
            (
                WORKOUT_ID,
                {
                    "status": "skipped",
                    "completion_source": "coach_confirmed",
                    "actual_activity_id": None,
                },
            )
        ]

    async def test_completed_with_activity_links_both_sides(self, monkeypatch) -> None:
        repo = ComplianceRepository(
            plan=_plan(), workouts=[_workout(id=WORKOUT_ID)], activities=[_activity()]
        )
        monkeypatch.setattr(api_index, "repo", repo)

        response = await _post(
            "/api/engine/resolve-plan-workout",
            {
                "plan_workout_id": WORKOUT_ID,
                "outcome": "completed",
                "activity_id": "activity-1",
                "source": "athlete",
            },
        )

        assert response.status_code == 200
        assert repo.workout_updates == [
            (
                WORKOUT_ID,
                {
                    "status": "completed",
                    "completion_source": "athlete_confirmed",
                    "actual_activity_id": "activity-1",
                },
            )
        ]
        assert len(repo.activity_updates) == 1
        assert repo.activity_updates[0].planned_workout_id == WORKOUT_ID

    async def test_skipping_a_matched_workout_unlinks_the_activity(self, monkeypatch) -> None:
        repo = ComplianceRepository(
            plan=_plan(),
            workouts=[_workout(id=WORKOUT_ID, status="completed", actual_activity_id="activity-1")],
            activities=[_activity(planned_workout_id=WORKOUT_ID)],
        )
        monkeypatch.setattr(api_index, "repo", repo)

        response = await _post(
            "/api/engine/resolve-plan-workout",
            {"plan_workout_id": WORKOUT_ID, "outcome": "skipped", "source": "athlete"},
        )

        assert response.status_code == 200
        assert repo.workout_updates == [
            (
                WORKOUT_ID,
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
            workouts=[
                _workout(id=WORKOUT_ID, status="completed", actual_activity_id="activity-old")
            ],
            activities=[
                _activity(id="activity-old", planned_workout_id=WORKOUT_ID),
                _activity(id="activity-new"),
            ],
        )
        monkeypatch.setattr(api_index, "repo", repo)

        response = await _post(
            "/api/engine/resolve-plan-workout",
            {
                "plan_workout_id": WORKOUT_ID,
                "outcome": "completed",
                "activity_id": "activity-new",
                "source": "athlete",
            },
        )

        assert response.status_code == 200
        linked_ids = [(a.id, a.planned_workout_id) for a in repo.activity_updates]
        assert ("activity-new", WORKOUT_ID) in linked_ids
        assert ("activity-old", None) in linked_ids

    async def test_unknown_workout_returns_404(self, monkeypatch) -> None:
        # A well-formed but absent id resolves past the boundary and 404s at lookup.
        monkeypatch.setattr(api_index, "repo", ComplianceRepository(plan=_plan()))
        response = await _post(
            "/api/engine/resolve-plan-workout",
            {"plan_workout_id": WORKOUT_ID, "outcome": "completed", "source": "athlete"},
        )
        assert response.status_code == 404

    async def test_unknown_activity_returns_404(self, monkeypatch) -> None:
        repo = ComplianceRepository(plan=_plan(), workouts=[_workout(id=WORKOUT_ID)])
        monkeypatch.setattr(api_index, "repo", repo)
        response = await _post(
            "/api/engine/resolve-plan-workout",
            {
                "plan_workout_id": WORKOUT_ID,
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
            {"plan_workout_id": WORKOUT_ID, "outcome": "missed", "source": "athlete"},
        )
        assert response.status_code == 422

    async def test_non_uuid_plan_workout_id_rejected_at_boundary(self, monkeypatch) -> None:
        # Tier 2: the coach fabricating "placeholder"/"<unknown>" (the original 503
        # incident) is rejected at request validation with a 422 before any DB call.
        monkeypatch.setattr(api_index, "repo", ComplianceRepository(plan=_plan()))
        response = await _post(
            "/api/engine/resolve-plan-workout",
            {"plan_workout_id": "placeholder", "outcome": "completed", "source": "coach"},
        )
        assert response.status_code == 422

    async def test_postgrest_client_fault_returns_422_not_503(self, monkeypatch) -> None:
        # Tier 1: a PostgREST client-class SQLSTATE (e.g. 22P02) reaching the handler
        # is bad input, not an outage, so it maps to 422 rather than 503.
        repo = ComplianceRepository(plan=_plan())

        async def _raise_invalid_uuid(*_args, **_kwargs) -> PlanWorkout:
            raise PostgRESTAPIError(
                {
                    "message": 'invalid input syntax for type uuid: "…"',
                    "code": "22P02",
                    "hint": None,
                    "details": None,
                }
            )

        monkeypatch.setattr(repo, "get_plan_workout", _raise_invalid_uuid)
        monkeypatch.setattr(api_index, "repo", repo)

        response = await _post(
            "/api/engine/resolve-plan-workout",
            {"plan_workout_id": WORKOUT_ID, "outcome": "completed", "source": "coach"},
        )
        assert response.status_code == 422

    async def test_postgrest_outage_still_returns_503(self, monkeypatch) -> None:
        # A genuine service fault (schema-cache miss / connectivity) is not client
        # input and must remain a 503.
        repo = ComplianceRepository(plan=_plan())

        async def _raise_schema_cache_miss(*_args, **_kwargs) -> PlanWorkout:
            raise PostgRESTAPIError(
                {
                    "message": "Could not find the table in the schema cache",
                    "code": "PGRST205",
                    "hint": None,
                    "details": None,
                }
            )

        monkeypatch.setattr(repo, "get_plan_workout", _raise_schema_cache_miss)
        monkeypatch.setattr(api_index, "repo", repo)

        response = await _post(
            "/api/engine/resolve-plan-workout",
            {"plan_workout_id": WORKOUT_ID, "outcome": "completed", "source": "coach"},
        )
        assert response.status_code == 503


@pytest.mark.usefixtures("as_athlete")
class TestFindPlanWorkout:
    async def test_returns_candidate_id_for_date_and_sport(self, monkeypatch) -> None:
        target = TODAY - timedelta(days=1)
        repo = ComplianceRepository(
            plan=_plan(),
            workouts=[_workout(id=WORKOUT_ID, workout_date=target, sport="cycling")],
        )
        monkeypatch.setattr(api_index, "repo", repo)

        response = await _post(
            "/api/engine/find-plan-workout",
            {"workout_date": target.isoformat(), "sport": "cycling"},
        )

        assert response.status_code == 200
        candidates = response.json()["candidates"]
        assert [c["plan_workout_id"] for c in candidates] == [WORKOUT_ID]

    async def test_filters_by_sport(self, monkeypatch) -> None:
        target = TODAY - timedelta(days=1)
        repo = ComplianceRepository(
            plan=_plan(),
            workouts=[
                _workout(id=WORKOUT_ID, workout_date=target, sport="cycling"),
                _workout(
                    id="00000000-0000-0000-0000-000000000022", workout_date=target, sport="running"
                ),
            ],
        )
        monkeypatch.setattr(api_index, "repo", repo)

        response = await _post(
            "/api/engine/find-plan-workout",
            {"workout_date": target.isoformat(), "sport": "running"},
        )

        assert response.status_code == 200
        candidates = response.json()["candidates"]
        assert [c["sport"] for c in candidates] == ["running"]

    async def test_no_match_returns_empty(self, monkeypatch) -> None:
        monkeypatch.setattr(api_index, "repo", ComplianceRepository(plan=_plan()))
        response = await _post(
            "/api/engine/find-plan-workout",
            {"workout_date": TODAY.isoformat(), "sport": "cycling"},
        )
        assert response.status_code == 200
        assert response.json()["candidates"] == []


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
