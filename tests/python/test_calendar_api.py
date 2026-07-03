"""Tests for GET /api/calendar — the agenda/calendar view data endpoint (issue #212)."""

from datetime import date, datetime

import pytest
from httpx import ASGITransport, AsyncClient

import api.index as api_index
from backend.models.auth import UserContext
from backend.models.training import Activity, PlanWorkout


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
