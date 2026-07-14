"""Tests for GET /api/calendar — the agenda/calendar view data endpoint (issue #212)."""

from datetime import UTC, date, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

import api.index as api_index
from backend.models.auth import UserContext
from backend.models.training import Activity, PlanWorkout, TrainingPlan


def _user_context() -> UserContext:
    return UserContext(
        user_id="athlete-1",
        scopes=["profile:read"],
        client_id="test-client",
        grant_id="grant-1",
    )


class CalendarRepository:
    """Fake repo capturing the range the endpoint requests."""

    def __init__(self) -> None:
        self.activity_calls: list[tuple[str, date, date]] = []
        self.workout_calls: list[tuple[str, date, date]] = []

    async def get_active_plan(self, user_id: str) -> TrainingPlan | None:
        return TrainingPlan(
            id="plan-1",
            user_id=user_id,
            title="Active plan",
            plan_type="full_cycle",
            status="active",
            start_date=date(2026, 5, 1),
            end_date=date(2026, 8, 31),
        )

    async def list_activities_between(
        self, user_id: str, *, start: date, end: date
    ) -> list[Activity]:
        self.activity_calls.append((user_id, start, end))
        return [
            Activity(
                id="activity-1",
                user_id=user_id,
                sport="running",
                activity_date=date(2026, 6, 20),
                duration_seconds=3600,
                distance_meters=12_000,
                tss=68,
                rpe=6,
                athlete_notes="Felt strong on the hills.",
            )
        ]

    async def list_plan_workouts_between(
        self, user_id: str, *, start: date, end: date
    ) -> list[PlanWorkout]:
        self.workout_calls.append((user_id, start, end))
        return [
            PlanWorkout(
                id="workout-1",
                plan_id="plan-1",
                user_id=user_id,
                workout_date=date(2026, 7, 4),
                day_of_week=5,
                week_number=2,
                sport="cycling",
                title="Sweet spot 3x12",
                workout_type="sweet_spot",
                target_duration_minutes=75,
                target_tss=80,
                status="scheduled",
            )
        ]


async def _get_calendar(query: str) -> tuple[int, dict]:
    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(f"/api/calendar?{query}")
    return response.status_code, (
        response.json()
        if response.headers.get("content-type", "").startswith("application/json")
        else {}
    )


@pytest.mark.asyncio
async def test_calendar_returns_planned_and_recorded_in_range(monkeypatch) -> None:
    repo = CalendarRepository()
    monkeypatch.setattr(api_index, "repo", repo)
    api_index.app.dependency_overrides[api_index.require_user_context] = _user_context

    try:
        status, body = await _get_calendar("start=2026-05-22&end=2026-08-28")
    finally:
        api_index.app.dependency_overrides.clear()

    assert status == 200
    assert body["start"] == "2026-05-22"
    assert body["end"] == "2026-08-28"

    assert repo.activity_calls == [("athlete-1", date(2026, 5, 22), date(2026, 8, 28))]
    assert repo.workout_calls == [("athlete-1", date(2026, 5, 22), date(2026, 8, 28))]

    activity = body["activities"][0]
    assert activity["id"] == "activity-1"
    assert activity["activity_date"] == "2026-06-20"
    assert activity["tss"] == 68

    workout = body["planned_workouts"][0]
    assert workout["id"] == "workout-1"
    assert workout["workout_date"] == "2026-07-04"
    assert workout["workout_type"] == "sweet_spot"
    assert workout["status"] == "scheduled"


@pytest.mark.asyncio
async def test_calendar_rejects_inverted_range(monkeypatch) -> None:
    monkeypatch.setattr(api_index, "repo", CalendarRepository())
    api_index.app.dependency_overrides[api_index.require_user_context] = _user_context

    try:
        status, body = await _get_calendar("start=2026-08-28&end=2026-05-22")
    finally:
        api_index.app.dependency_overrides.clear()

    assert status == 400
    assert "start" in body["detail"]


@pytest.mark.asyncio
async def test_calendar_rejects_oversized_range(monkeypatch) -> None:
    monkeypatch.setattr(api_index, "repo", CalendarRepository())
    api_index.app.dependency_overrides[api_index.require_user_context] = _user_context

    try:
        status, body = await _get_calendar("start=2025-01-01&end=2026-08-28")
    finally:
        api_index.app.dependency_overrides.clear()

    assert status == 400
    assert "range" in body["detail"].lower()


@pytest.mark.asyncio
async def test_calendar_rejects_malformed_dates(monkeypatch) -> None:
    monkeypatch.setattr(api_index, "repo", CalendarRepository())
    api_index.app.dependency_overrides[api_index.require_user_context] = _user_context

    try:
        status, _body = await _get_calendar("start=not-a-date&end=2026-08-28")
    finally:
        api_index.app.dependency_overrides.clear()

    assert status == 422


@pytest.mark.asyncio
async def test_calendar_requires_auth() -> None:
    status, _body = await _get_calendar("start=2026-05-22&end=2026-08-28")
    assert status == 401


def _plan(plan_id: str, status: str, user_id: str = "athlete-1") -> TrainingPlan:
    return TrainingPlan(
        id=plan_id,
        user_id=user_id,
        title=f"Plan {plan_id}",
        plan_type="full_cycle",
        status=status,
        start_date=date(2026, 5, 1),
        end_date=date(2026, 9, 30),
    )


def _workout(
    workout_id: str,
    plan_id: str,
    workout_date: date,
    *,
    status: str = "scheduled",
    actual_activity_id: str | None = None,
    user_id: str = "athlete-1",
) -> PlanWorkout:
    return PlanWorkout(
        id=workout_id,
        plan_id=plan_id,
        user_id=user_id,
        workout_date=workout_date,
        day_of_week=workout_date.weekday(),
        week_number=1,
        sport="cycling",
        title=workout_id,
        workout_type="endurance",
        status=status,
        actual_activity_id=actual_activity_id,
    )


@pytest.mark.asyncio
async def test_calendar_scopes_future_scheduled_to_active_plan(monkeypatch) -> None:
    """Superseded plan future scheduled rows are hidden; history is preserved."""
    today = datetime.now(UTC).date()

    future = today + timedelta(days=7)
    past = today - timedelta(days=7)
    older_past = today - timedelta(days=10)

    class TwoPlanRepository(CalendarRepository):
        async def get_active_plan(self, user_id: str) -> TrainingPlan | None:
            return _plan("active-plan", "active", user_id)

        async def list_activities_between(
            self, user_id: str, *, start: date, end: date
        ) -> list[Activity]:
            return []

        async def list_plan_workouts_between(
            self, user_id: str, *, start: date, end: date
        ) -> list[PlanWorkout]:
            self.workout_calls.append((user_id, start, end))
            return [
                # Active plan: future scheduled → kept.
                _workout("active-future", "active-plan", future),
                # Superseded plan: future scheduled unmatched → dropped.
                _workout("superseded-future", "superseded-plan", future),
                # Superseded plan: past completed/matched → kept (history).
                _workout(
                    "superseded-done",
                    "superseded-plan",
                    past,
                    status="completed",
                    actual_activity_id="activity-9",
                ),
                # Superseded plan: past scheduled (unconfirmed) → kept.
                _workout("superseded-unconfirmed", "superseded-plan", older_past),
            ]

    monkeypatch.setattr(api_index, "repo", TwoPlanRepository())
    api_index.app.dependency_overrides[api_index.require_user_context] = _user_context

    try:
        span_start = (past - timedelta(days=30)).isoformat()
        span_end = (future + timedelta(days=30)).isoformat()
        status, body = await _get_calendar(f"start={span_start}&end={span_end}")
    finally:
        api_index.app.dependency_overrides.clear()

    assert status == 200
    ids = [w["id"] for w in body["planned_workouts"]]
    # Superseded plan's future scheduled row is gone.
    assert "superseded-future" not in ids
    # Active plan future scheduled is present; no duplicate workout_date.
    assert "active-future" in ids
    dates = [w["workout_date"] for w in body["planned_workouts"]]
    assert len(dates) == len(set(dates))
    # History from the superseded plan survives.
    assert "superseded-done" in ids
    assert "superseded-unconfirmed" in ids


@pytest.mark.asyncio
async def test_calendar_no_active_plan_returns_all(monkeypatch) -> None:
    """With no active plan, no exclusion is applied (legitimate rows kept)."""
    today = datetime.now(UTC).date()

    future = today + timedelta(days=7)

    class NoActivePlanRepository(CalendarRepository):
        async def get_active_plan(self, user_id: str) -> TrainingPlan | None:
            return None

        async def list_activities_between(
            self, user_id: str, *, start: date, end: date
        ) -> list[Activity]:
            return []

        async def list_plan_workouts_between(
            self, user_id: str, *, start: date, end: date
        ) -> list[PlanWorkout]:
            return [_workout("orphan-future", "old-plan", future)]

    monkeypatch.setattr(api_index, "repo", NoActivePlanRepository())
    api_index.app.dependency_overrides[api_index.require_user_context] = _user_context

    try:
        span_start = (future - timedelta(days=30)).isoformat()
        span_end = (future + timedelta(days=30)).isoformat()
        status, body = await _get_calendar(f"start={span_start}&end={span_end}")
    finally:
        api_index.app.dependency_overrides.clear()

    assert status == 200
    assert [w["id"] for w in body["planned_workouts"]] == ["orphan-future"]


def test_scope_helper_predicate_cases() -> None:
    """Unit-test the exclusion predicate in isolation."""

    today = date(2026, 7, 14)
    future = today + timedelta(days=3)
    past = today - timedelta(days=3)

    active_future = _workout("a-fut", "active", future)
    other_future = _workout("o-fut", "other", future)
    other_past = _workout("o-past", "other", past)
    other_done = _workout("o-done", "other", future, status="completed", actual_activity_id="act-1")
    other_matched = _workout("o-match", "other", future, actual_activity_id="act-2")

    planned = [active_future, other_future, other_past, other_done, other_matched]

    kept = api_index._scope_planned_workouts_to_active_plan(planned, "active", today)
    kept_ids = {w.id for w in kept}
    assert kept_ids == {"a-fut", "o-past", "o-done", "o-match"}
    assert "o-fut" not in kept_ids

    # No active plan → passthrough.
    passthrough = api_index._scope_planned_workouts_to_active_plan(planned, None, today)
    assert passthrough == planned


@pytest.mark.asyncio
async def test_calendar_serializes_timestamps_as_json(monkeypatch) -> None:
    class TimestampRepository(CalendarRepository):
        async def list_activities_between(
            self, user_id: str, *, start: date, end: date
        ) -> list[Activity]:
            return [
                Activity(
                    id="activity-2",
                    user_id=user_id,
                    sport="running",
                    activity_date=date(2026, 6, 21),
                    started_at=datetime.fromisoformat("2026-06-21T06:30:00+00:00"),
                )
            ]

    monkeypatch.setattr(api_index, "repo", TimestampRepository())
    api_index.app.dependency_overrides[api_index.require_user_context] = _user_context

    try:
        status, body = await _get_calendar("start=2026-05-22&end=2026-08-28")
    finally:
        api_index.app.dependency_overrides.clear()

    assert status == 200
    assert body["activities"][0]["started_at"] == "2026-06-21T06:30:00Z"
