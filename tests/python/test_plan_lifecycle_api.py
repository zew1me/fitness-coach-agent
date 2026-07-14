"""Tests for the plan lifecycle endpoints:

- POST /api/engine/generate-plan-structure regeneration cleanup (Task 2)
- POST /api/engine/adjust-plan future-in-place editing (Task 3)
- POST /api/engine/update-schedule typed validation (Task 7)

The regenerate/adjust tests drive the *real* SupabaseRepository against the
in-memory FakeSupabaseClient so the supersede + delete + recompose interaction is
exercised end-to-end, not stubbed.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from httpx import ASGITransport, AsyncClient

import api.index as api_index
from backend.models.auth import UserContext
from backend.repos.supabase_repo import SupabaseRepository
from tests.python.test_supabase_repo import FakeSupabaseClient

TODAY = datetime.now(UTC).date()


def _user_context() -> UserContext:
    return UserContext(
        user_id="athlete-1",
        scopes=["plans:write"],
        client_id="test-client",
        grant_id="grant-1",
    )


def _seeded_client() -> FakeSupabaseClient:
    """A fake DB with an athlete profile + one active event goal ~9 weeks out."""
    return FakeSupabaseClient(
        athlete_rows=[
            {
                "user_id": "athlete-1",
                "primary_sports": ["cycling"],
                "weekly_available_hours": 8.0,
                "coaching_state": "active",
            }
        ],
        goal_rows=[
            {
                "id": "goal-1",
                "user_id": "athlete-1",
                "goal_type": "event",
                "title": "Gran Fondo",
                "sport": "cycling",
                "target_date": (TODAY + timedelta(weeks=9)).isoformat(),
                "status": "active",
                "priority": 1,
            }
        ],
    )


async def _post(path: str, body: dict[str, Any]) -> Any:
    transport = ASGITransport(app=api_index.app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(path, json=body)


async def _get(path: str, params: dict[str, Any]) -> Any:
    transport = ASGITransport(app=api_index.app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get(path, params=params)


@pytest.fixture
def as_athlete():
    api_index.app.dependency_overrides[api_index.require_user_context] = _user_context
    yield
    api_index.app.dependency_overrides.clear()


def _future_planned_by_date(client: FakeSupabaseClient) -> dict[str, list[dict[str, Any]]]:
    """Group future scheduled plan_workout rows by date."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in client._tables["plan_workouts"]._rows:
        if str(row["workout_date"]) >= TODAY.isoformat() and row.get("status") == "scheduled":
            grouped.setdefault(str(row["workout_date"]), []).append(row)
    return grouped


@pytest.mark.usefixtures("as_athlete")
class TestRegenerateCleansCalendar:
    async def test_regenerate_calendar_single_timeline(self, monkeypatch) -> None:
        client = _seeded_client()
        monkeypatch.setattr(api_index, "repo", SupabaseRepository(client=client))

        first = await _post("/api/engine/generate-plan-structure", {"goal_id": "goal-1"})
        assert first.status_code == 200, first.text
        second = await _post("/api/engine/generate-plan-structure", {"goal_id": "goal-1"})
        assert second.status_code == 200, second.text
        assert first.json()["plan_id"] != second.json()["plan_id"]

        # The calendar must show exactly one scheduled workout per future date,
        # even though a superseded plan's rows still exist for past dates.
        end = TODAY + timedelta(weeks=8)
        calendar = await _get("/api/calendar", {"start": TODAY.isoformat(), "end": end.isoformat()})
        assert calendar.status_code == 200, calendar.text
        planned = calendar.json()["planned_workouts"]
        scheduled_dates = [w["workout_date"] for w in planned if w["status"] == "scheduled"]
        assert len(scheduled_dates) == len(set(scheduled_dates)), (
            "duplicate future dates on calendar"
        )
        # Every scheduled workout belongs to the new (active) plan.
        active_plan_id = second.json()["plan_id"]
        assert all(w["plan_id"] == active_plan_id for w in planned if w["status"] == "scheduled")

    async def test_regenerate_preserves_completed_history(self, monkeypatch) -> None:
        client = _seeded_client()
        monkeypatch.setattr(api_index, "repo", SupabaseRepository(client=client))

        first = await _post("/api/engine/generate-plan-structure", {"goal_id": "goal-1"})
        plan_a = first.json()["plan_id"]
        # Mark today's workout completed + matched on plan A (history).
        for row in client._tables["plan_workouts"]._rows:
            if row["plan_id"] == plan_a and str(row["workout_date"]) == TODAY.isoformat():
                row["status"] = "completed"
                row["actual_activity_id"] = "activity-x"
                completed_id = row["id"]
                break

        await _post("/api/engine/generate-plan-structure", {"goal_id": "goal-1"})

        # The completed workout from the superseded plan is untouched.
        surviving = [
            row for row in client._tables["plan_workouts"]._rows if row["id"] == completed_id
        ]
        assert len(surviving) == 1
        assert surviving[0]["status"] == "completed"
        assert surviving[0]["actual_activity_id"] == "activity-x"


@pytest.mark.usefixtures("as_athlete")
class TestAdjustPlan:
    async def test_adjust_plan_edits_future_in_place_and_keeps_history(self, monkeypatch) -> None:
        client = _seeded_client()
        monkeypatch.setattr(api_index, "repo", SupabaseRepository(client=client))

        generated = await _post("/api/engine/generate-plan-structure", {"goal_id": "goal-1"})
        plan_id = generated.json()["plan_id"]

        # Mark today's workout completed/matched — it is history and must survive.
        completed_id = None
        for row in client._tables["plan_workouts"]._rows:
            if str(row["workout_date"]) == TODAY.isoformat():
                row["status"] = "completed"
                row["actual_activity_id"] = "activity-x"
                completed_id = row["id"]
                break
        assert completed_id is not None

        future_ids_before = {
            row["id"]
            for row in client._tables["plan_workouts"]._rows
            if str(row["workout_date"]) > TODAY.isoformat()
        }

        response = await _post(
            "/api/engine/adjust-plan",
            {"plan_id": plan_id, "reason": "Feeling flat, ease off next week."},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "adjusted"
        assert body["plan_id"] == plan_id
        assert body["workouts_created"] > 0

        rows = client._tables["plan_workouts"]._rows
        # History preserved: the completed workout still exists, same id + status.
        completed = [r for r in rows if r["id"] == completed_id]
        assert len(completed) == 1
        assert completed[0]["status"] == "completed"

        # Future scheduled workouts were replaced (deleted + reinserted → new ids).
        future_ids_after = {r["id"] for r in rows if str(r["workout_date"]) > TODAY.isoformat()}
        assert future_ids_after.isdisjoint(future_ids_before)
        # Still one scheduled workout per future date (no double-show).
        for group in _future_planned_by_date(client).values():
            assert len(group) == 1

        # An audit entry was appended to the plan's generation_context.
        plan_rows = client._tables["training_plans"]._rows
        active = next(r for r in plan_rows if r["id"] == plan_id)
        context = cast(dict[str, Any], active["generation_context"])
        adjustments = cast(list[dict[str, Any]], context["adjustments"])
        assert adjustments[-1]["reason"] == "Feeling flat, ease off next week."

    async def test_adjust_plan_rejects_mismatched_plan_id(self, monkeypatch) -> None:
        client = _seeded_client()
        monkeypatch.setattr(api_index, "repo", SupabaseRepository(client=client))
        await _post("/api/engine/generate-plan-structure", {"goal_id": "goal-1"})

        response = await _post(
            "/api/engine/adjust-plan", {"plan_id": "not-the-active-plan", "reason": "x"}
        )
        assert response.status_code == 409

    async def test_adjust_plan_rejects_formula_recomposition_of_exact_schedule(
        self, monkeypatch
    ) -> None:
        client = _seeded_client()
        monkeypatch.setattr(api_index, "repo", SupabaseRepository(client=client))
        explicit_workouts = [
            {
                "description": "Run-first session.",
                "phase_name": "Build",
                "sport": "running",
                "target_distance_meters": None,
                "target_duration_minutes": 45,
                "target_tss": None,
                "title": "Easy trail run",
                "workout_date": TODAY.isoformat(),
                "workout_type": "endurance",
            },
            {
                "description": "Keep the athlete's planned ride.",
                "phase_name": "Build",
                "sport": "cycling",
                "target_distance_meters": None,
                "target_duration_minutes": 90,
                "target_tss": None,
                "title": "Group ride",
                "workout_date": (TODAY + timedelta(days=2)).isoformat(),
                "workout_type": "tempo",
            },
        ]
        generated = await _post(
            "/api/engine/generate-plan-structure",
            {
                "goal_id": "goal-1",
                "title": "Exact mixed-sport schedule",
                "workouts": explicit_workouts,
            },
        )
        assert generated.status_code == 200, generated.text
        plan_id = generated.json()["plan_id"]
        workout_ids_before = {
            row["id"] for row in client._tables["plan_workouts"]._rows if row["plan_id"] == plan_id
        }

        response = await _post(
            "/api/engine/adjust-plan",
            {"plan_id": plan_id, "reason": "Move the ride."},
        )

        assert response.status_code == 409
        assert response.json()["detail"].startswith("Exact-workout plans cannot")
        workout_ids_after = {
            row["id"] for row in client._tables["plan_workouts"]._rows if row["plan_id"] == plan_id
        }
        assert workout_ids_after == workout_ids_before

    async def test_adjust_plan_without_active_plan_returns_404(self, monkeypatch) -> None:
        monkeypatch.setattr(api_index, "repo", SupabaseRepository(client=FakeSupabaseClient()))
        response = await _post("/api/engine/adjust-plan", {"plan_id": "plan-1", "reason": "x"})
        assert response.status_code == 404


@pytest.mark.usefixtures("as_athlete")
class TestUpdateScheduleValidation:
    async def test_update_schedule_validation_rejects_out_of_range_hours(self, monkeypatch) -> None:
        monkeypatch.setattr(api_index, "repo", SupabaseRepository(client=FakeSupabaseClient()))

        too_high = await _post(
            "/api/engine/update-schedule",
            {"weekly_pattern": {"monday": {"available": True, "max_hours": 25}}},
        )
        assert too_high.status_code == 422

        negative = await _post(
            "/api/engine/update-schedule",
            {"weekly_pattern": {"monday": {"available": True, "max_hours": -1}}},
        )
        assert negative.status_code == 422

    async def test_update_schedule_validation_accepts_valid_pattern_and_override(
        self, monkeypatch
    ) -> None:
        monkeypatch.setattr(api_index, "repo", SupabaseRepository(client=FakeSupabaseClient()))

        response = await _post(
            "/api/engine/update-schedule",
            {
                "weekly_pattern": {
                    "monday": {"available": True, "max_hours": 1.5},
                    "sunday": {"available": False},
                },
                "overrides": [
                    {"override_date": "2026-12-20", "available": False, "reason": "travel"}
                ],
            },
        )
        assert response.status_code == 200, response.text
        assert set(response.json()["updated"]) == {"weekly_pattern", "overrides"}
