"""Per-phase fueling focus (issue #53).

Covers the pure derivation (`derive_nutrition_focus`) and its end-to-end wiring:
generate stamps every phase, the calendar surfaces it per week, and an adjust
preserves it untouched.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

import api.index as api_index
from backend.models.auth import UserContext
from backend.repos.supabase_repo import SupabaseRepository
from backend.services.nutrition_focus import derive_nutrition_focus
from tests.python.test_supabase_repo import FakeSupabaseClient

TODAY = datetime.now(UTC).date()


# ── Pure derivation ───────────────────────────────────────────


def test_derive_nutrition_focus_differs_by_phase() -> None:
    focuses = {
        phase: derive_nutrition_focus(phase)
        for phase in ("base", "build", "peak", "taper", "recovery")
    }
    # Every phase yields a non-empty, distinct emphasis.
    assert all(text.strip() for text in focuses.values())
    assert len(set(focuses.values())) == len(focuses)


def test_derive_nutrition_focus_unknown_phase_falls_back_to_base() -> None:
    assert derive_nutrition_focus("mystery") == derive_nutrition_focus("base")


def test_derive_nutrition_focus_is_case_insensitive() -> None:
    assert derive_nutrition_focus("BASE") == derive_nutrition_focus("base")


def test_derive_nutrition_focus_folds_in_dietary_restrictions() -> None:
    text = derive_nutrition_focus("build", ["vegetarian", "gluten-free"])
    assert "vegetarian" in text
    assert "gluten-free" in text
    # The restriction clause is appended to the phase emphasis, not a replacement.
    assert derive_nutrition_focus("build") in text


def test_derive_nutrition_focus_ignores_blank_restrictions() -> None:
    assert derive_nutrition_focus("base", ["  ", ""]) == derive_nutrition_focus("base")


def test_derive_nutrition_focus_is_deterministic() -> None:
    args = ("peak", ["vegan"])
    assert derive_nutrition_focus(*args) == derive_nutrition_focus(*args)


# ── End-to-end wiring ─────────────────────────────────────────


def _user_context() -> UserContext:
    return UserContext(
        user_id="athlete-1",
        scopes=["plans:write"],
        client_id="test-client",
        grant_id="grant-1",
    )


def _seeded_client() -> FakeSupabaseClient:
    return FakeSupabaseClient(
        athlete_rows=[
            {
                "user_id": "athlete-1",
                "primary_sports": ["cycling"],
                "weekly_available_hours": 8.0,
                "coaching_state": "active",
                "dietary_restrictions": ["vegetarian", "gluten-free"],
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


@pytest.mark.usefixtures("as_athlete")
class TestNutritionFocusWiring:
    async def test_generate_stamps_every_phase_with_athlete_tailored_focus(
        self, monkeypatch
    ) -> None:
        monkeypatch.setattr(api_index, "repo", SupabaseRepository(client=_seeded_client()))

        generated = await _post("/api/engine/generate-plan-structure", {"goal_id": "goal-1"})
        assert generated.status_code == 200, generated.text

        phases = generated.json()["phases"]
        assert phases, "expected a periodized plan with phases"
        for phase in phases:
            focus = phase["nutrition_focus"]
            assert focus.strip(), f"phase {phase['name']} missing nutrition_focus"
            # Dietary restrictions from the profile are folded into every phase.
            assert "vegetarian" in focus
            assert "gluten-free" in focus

    async def test_calendar_surfaces_per_week_nutrition_focus(self, monkeypatch) -> None:
        monkeypatch.setattr(api_index, "repo", SupabaseRepository(client=_seeded_client()))

        generated = await _post("/api/engine/generate-plan-structure", {"goal_id": "goal-1"})
        assert generated.status_code == 200, generated.text

        end = TODAY + timedelta(weeks=8)
        calendar = await _get("/api/calendar", {"start": TODAY.isoformat(), "end": end.isoformat()})
        assert calendar.status_code == 200, calendar.text
        planned = calendar.json()["planned_workouts"]
        assert planned, "expected scheduled workouts on the calendar"
        # Every active-plan workout carries its week's fueling focus.
        assert all(w.get("nutrition_focus", "").strip() for w in planned)

    async def test_adjust_preserves_nutrition_focus(self, monkeypatch) -> None:
        repo = SupabaseRepository(client=_seeded_client())
        monkeypatch.setattr(api_index, "repo", repo)

        generated = await _post("/api/engine/generate-plan-structure", {"goal_id": "goal-1"})
        plan_id = generated.json()["plan_id"]
        before = [p["nutrition_focus"] for p in generated.json()["phases"]]
        assert before and all(f.strip() for f in before)  # sanity: focus was set

        adjusted = await _post(
            "/api/engine/adjust-plan", {"plan_id": plan_id, "reason": "life got busy"}
        )
        assert adjusted.status_code == 200, adjusted.text

        # Adjust never rewrites phases, so the fueling focus survives byte-for-byte.
        plan_after = await repo.get_active_plan("athlete-1")
        assert plan_after is not None
        assert [p["nutrition_focus"] for p in plan_after.phases] == before
