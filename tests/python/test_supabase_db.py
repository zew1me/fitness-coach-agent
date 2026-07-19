"""Integration tests requiring a live Supabase connection.

Run with:
    bun run db:start          # start local Supabase (once)
    bun run test:db           # uv run pytest -m db tests/python/

These tests are excluded from the default gate (uv run pytest) via
addopts = "-m 'not db'" in pyproject.toml.  They are not run in CI
and must be run manually against a local or preview Supabase project.

Red/green context for migration 20260624055541
--------------------------------------
Before applying 20260624055541_specialization_pct_nullable.sql:
  bun run db:reset            # replay migrations 0001-0004
  bun run test:db             # test_specialization_pct_* RED -> APIError NOT NULL
After applying 20260624055541:
  bun run db:reset            # replay all migrations
  bun run test:db             # all db tests GREEN
"""

import asyncio
import os
import threading
import uuid
from collections.abc import Callable
from datetime import date
from typing import Any, cast

import pytest
from httpx import ASGITransport, AsyncClient

import api.index as api_index
from backend.models.athlete import AthleteProfile, SportThreshold, ThresholdRecalibrationCandidate
from backend.models.auth import UserContext
from backend.models.intervals import IntervalsConnectionCreate
from backend.models.training import TrainingPlan
from backend.repos.intervals_repo import IntervalsRepository
from backend.repos.supabase_repo import RecordNotFoundError, SupabaseRepository

_SUPABASE_CONFIGURED = bool(
    os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
)
# Explicit opt-in prevents accidental live writes when credentials happen to be present
# in the environment (e.g. via .env.local / direnv pointing at a hosted project).
# Set via: RUN_DB_TESTS=1 uv run pytest -m db   (or bun run test:db which sets it).
_RUN_DB_TESTS = os.environ.get("RUN_DB_TESTS") == "1"
_RUN_OAI_TESTS = os.environ.get("RUN_OAI_TESTS") == "1"
_OPENAI_CONFIGURED = bool(os.environ.get("OPENAI_API_KEY"))

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(
        not (_SUPABASE_CONFIGURED and _RUN_DB_TESTS),
        reason=("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set, or RUN_DB_TESTS=1 not provided"),
    ),
]


@pytest.fixture()
def repo() -> SupabaseRepository:
    return SupabaseRepository()


@pytest.fixture()
def unique_user() -> str:
    return f"test-{uuid.uuid4()}"


@pytest.mark.asyncio
async def test_upsert_profile_with_null_specialization_pct(
    repo: SupabaseRepository, unique_user: str
) -> None:
    """A multi-sport athlete profile with specialization_pct=None must persist without error.

    This is the canonical regression test for issue #254.  Before migration 20260624055541 this
    raises APIError because the column was NOT NULL.  After 20260624055541 it stores NULL.
    """
    profile = AthleteProfile(
        user_id=unique_user,
        primary_sports=["cycling", "running"],
        coaching_state="onboarding",
        specialization_pct=None,
    )

    saved = await repo.upsert_athlete_profile(profile)

    assert saved.user_id == unique_user
    assert saved.specialization_pct is None
    assert saved.primary_sports == ["cycling", "running"]


@pytest.mark.asyncio
async def test_update_profile_fields_with_null_specialization_pct_preserves_existing(
    repo: SupabaseRepository, unique_user: str
) -> None:
    """Sending specialization_pct=None in a partial update must leave the existing value alone.

    The repo filter drops None values so Postgres is never asked to NULL the column.
    This test proves the filter holds end-to-end: first store a known value, then send
    a partial update with specialization_pct=None, then read back — old value must be
    intact.
    """
    await repo.upsert_athlete_profile(
        AthleteProfile(
            user_id=unique_user,
            specialization_pct=70,
            coaching_state="onboarding",
        )
    )

    await repo.update_athlete_profile_fields(
        unique_user,
        {
            "primary_sports": ["duathlon"],
            "specialization_pct": None,
        },
    )

    refreshed = await repo.get_athlete_profile(unique_user)
    assert refreshed.specialization_pct == 70, (
        "specialization_pct=None in a partial update must be filtered out, "
        "not overwrite the stored value"
    )
    assert refreshed.primary_sports == ["duathlon"]


@pytest.mark.asyncio
async def test_update_profile_fields_can_set_specialization_pct_to_value(
    repo: SupabaseRepository, unique_user: str
) -> None:
    """A non-None specialization_pct value must be written through to the DB."""
    await repo.upsert_athlete_profile(
        AthleteProfile(user_id=unique_user, coaching_state="onboarding")
    )

    updated = await repo.update_athlete_profile_fields(unique_user, {"specialization_pct": 65})

    assert updated.specialization_pct == 65
    refreshed = await repo.get_athlete_profile(unique_user)
    assert refreshed.specialization_pct == 65


@pytest.mark.asyncio
async def test_new_profile_row_has_null_specialization_pct_not_default_80(
    repo: SupabaseRepository, unique_user: str
) -> None:
    """A brand-new profile row must store NULL for specialization_pct, not the old DEFAULT 80.

    This tests the DROP DEFAULT path of migration 20260624055541.  Before that
    migration, omitting the field from the INSERT would fall back to DEFAULT 80.
    After it, the field stores NULL.
    The canonical failure for issue #254 came from the column having no DEFAULT on a
    drifted DB — this test verifies we converge on NULL everywhere.
    """
    await repo.update_athlete_profile_fields(unique_user, {"coaching_state": "onboarding"})

    profile = await repo.get_athlete_profile(unique_user)
    assert profile.specialization_pct is None, (
        "New rows must have NULL specialization_pct, not the old DEFAULT 80"
    )


@pytest.mark.asyncio
async def test_create_training_plan_atomic_rpc_returns_persisted_plan(
    repo: SupabaseRepository, unique_user: str
) -> None:
    """`create_training_plan` must round-trip the atomic RPC against a real DB.

    Regression for the `KeyError: 0` 500 in `/api/engine/generate-plan-structure`
    (Sentry PYTHON-FASTAPI-T). `create_training_plan_atomic` is declared
    `returns public.training_plans` (a single composite row), so PostgREST returns
    the row as a JSON object, not an array. The repo previously indexed it as a list
    (`rows[0]`), which raises `KeyError: 0` on the dict. Unit tests missed this because
    their fake RPC client returned a list — only a live PostgREST call exercises the
    real object shape. The RPC also `for update`s athlete_profiles, so a profile row
    must exist first or it raises P0002 (`Athlete profile not found`) instead.
    """
    await repo.upsert_athlete_profile(
        AthleteProfile(user_id=unique_user, primary_sports=["running"], coaching_state="active")
    )
    plan = TrainingPlan(
        user_id=unique_user,
        title="Half-marathon build",
        plan_type="full_cycle",
        start_date=date(2026, 7, 14),
        end_date=date(2026, 8, 29),
        phases=[{"name": "base", "start_week": 1, "end_week": 4}],
        generation_context={"training_model": "performance"},
        weekly_tss_target=380.0,
        weekly_hours_target=8.0,
    )

    created = await repo.create_training_plan(plan)

    assert created.id is not None
    assert created.user_id == unique_user
    assert created.status == "active"
    assert created.title == "Half-marathon build"


@pytest.mark.asyncio
async def test_recalibration_candidate_decision_claim_and_threshold_write_are_atomic(
    repo: SupabaseRepository, unique_user: str
) -> None:
    """Concurrent accepts produce one decision and one active threshold."""
    await repo.upsert_athlete_profile(
        AthleteProfile(user_id=unique_user, primary_sports=["running"], coaching_state="active")
    )
    candidate = await repo.create_recalibration_candidate(
        ThresholdRecalibrationCandidate(
            user_id=unique_user,
            sport="running",
            confidence="high",
            evidence_activity_id="activity-1",
            explanation="Faster 5K.",
            candidate_threshold=SportThreshold(
                user_id=unique_user,
                sport="running",
                lt2_pace_sec_per_km=250,
                source="file",
            ),
        )
    )
    threshold = candidate.candidate_threshold.model_copy(update={"id": None})
    barrier = threading.Barrier(2)

    def decide() -> object:
        local_repo = SupabaseRepository()
        barrier.wait(timeout=5)
        return asyncio.run(
            local_repo.decide_recalibration_candidate(
                user_id=unique_user,
                candidate_id=candidate.id or "",
                status="accepted",
                threshold=threshold,
            )
        )

    outcomes = await asyncio.gather(
        asyncio.to_thread(decide),
        asyncio.to_thread(decide),
        return_exceptions=True,
    )

    assert sum(not isinstance(outcome, BaseException) for outcome in outcomes) == 1
    assert sum(isinstance(outcome, RecordNotFoundError) for outcome in outcomes) == 1
    active_thresholds = await repo.get_active_thresholds(unique_user)
    assert len(active_thresholds) == 1
    assert active_thresholds[0].lt2_pace_sec_per_km == 250
    history_response = (
        repo._require_client()
        .table("sport_thresholds")
        .select("id")
        .eq("user_id", unique_user)
        .eq("sport", "running")
        .execute()
    )
    assert len(history_response.data or []) == 1


@pytest.mark.asyncio
async def test_replace_intervals_connection_serializes_concurrent_replaces(
    repo: SupabaseRepository, unique_user: str
) -> None:
    """Concurrent replaces both succeed and leave exactly one active connection.

    Before migration 20260719000000 the repo revoked and inserted in two
    independent PostgREST calls, so two interleaved replaces raced the partial
    unique index (intervals_connections_user_active_idx) and the loser failed
    with a unique violation. The atomic RPC serializes per user via an advisory
    lock: the later committer revokes the earlier row and becomes active.
    """
    await repo.upsert_athlete_profile(
        AthleteProfile(user_id=unique_user, primary_sports=["cycling"], coaching_state="active")
    )
    barrier = threading.Barrier(2)

    def replace(athlete_id: str) -> object:
        local_repo = IntervalsRepository()
        barrier.wait(timeout=5)
        return local_repo.replace_connection(
            IntervalsConnectionCreate(
                user_id=unique_user,
                intervals_athlete_id=athlete_id,
                intervals_athlete_name="Nigel",
                scopes=["ACTIVITY:READ"],
                access_token_ciphertext=f"ciphertext-{athlete_id}",
                token_type="Bearer",
            )
        )

    outcomes = await asyncio.gather(
        asyncio.to_thread(replace, "i111"),
        asyncio.to_thread(replace, "i222"),
        return_exceptions=True,
    )

    assert not any(isinstance(outcome, BaseException) for outcome in outcomes), outcomes
    rows_response = (
        repo._require_client()
        .table("intervals_connections")
        .select("id, revoked_at")
        .eq("user_id", unique_user)
        .execute()
    )
    rows = rows_response.data or []
    assert len(rows) == 2
    assert sum(1 for row in rows if row["revoked_at"] is None) == 1
    active = IntervalsRepository().get_active_connection(unique_user)
    assert active is not None


@pytest.mark.asyncio
async def test_create_training_plan_supersedes_prior_active_plan(
    repo: SupabaseRepository, unique_user: str
) -> None:
    """A second `create_training_plan` must flip the prior active plan to superseded.

    Exercises the atomic supersede-then-insert path of `create_training_plan_atomic`
    end-to-end against a live DB, guarding the single-active-plan invariant.
    """
    await repo.upsert_athlete_profile(
        AthleteProfile(user_id=unique_user, primary_sports=["running"], coaching_state="active")
    )
    first = await repo.create_training_plan(
        TrainingPlan(
            user_id=unique_user,
            title="First",
            plan_type="weekly",
            start_date=date(2026, 7, 14),
            end_date=date(2026, 8, 14),
        )
    )
    second = await repo.create_training_plan(
        TrainingPlan(
            user_id=unique_user,
            title="Second",
            plan_type="weekly",
            start_date=date(2026, 7, 21),
            end_date=date(2026, 8, 21),
        )
    )

    active = await repo.get_active_plan(unique_user)
    assert active is not None
    assert active.id == second.id
    assert active.id != first.id


@pytest.mark.skipif(
    not (_RUN_OAI_TESTS and _OPENAI_CONFIGURED),
    reason="RUN_OAI_TESTS=1 and OPENAI_API_KEY are required for the activity text DB test.",
)
@pytest.mark.asyncio
async def test_save_activity_from_text_endpoint_persists_real_activity_summary(
    monkeypatch: pytest.MonkeyPatch,
    repo: SupabaseRepository,
    unique_user: str,
) -> None:
    await repo.upsert_athlete_profile(
        AthleteProfile(
            user_id=unique_user,
            coaching_state="active",
            max_hr_bpm=195,
            resting_hr_bpm=52,
        )
    )
    await repo.upsert_sport_threshold(
        SportThreshold(
            user_id=unique_user,
            sport="cycling",
            lt1_power_watts=180,
            lt2_power_watts=250,
            lt1_hr_bpm=145,
            lt2_hr_bpm=174,
        )
    )

    previous_override = api_index.app.dependency_overrides.get(
        api_index.require_user_context,
        None,
    )
    had_previous_override = api_index.require_user_context in api_index.app.dependency_overrides
    api_index.app.dependency_overrides[api_index.require_user_context] = lambda: UserContext(
        user_id=unique_user,
        scopes=["activities:write"],
        client_id="test-client",
        grant_id="grant-1",
    )
    monkeypatch.setattr(api_index, "repo", repo)

    try:
        transport = ASGITransport(app=api_index.app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/engine/save-activity-from-text",
                json={
                    "text": (
                        "Volunteer Park crit, Sat 13 Jun 2026 — 45 min race start at "
                        "~12:56-13:00. Report: in race ~19 minutes then blew up; avg HR "
                        "183 bpm, max 193 bpm; avg power 198 W, NP 243 W; I ate one "
                        "Maurten Gel 100 and drank some Skratch; short high-power surges "
                        "up to ~450 W for 8-15s."
                    )
                },
            )
    finally:
        if had_previous_override:
            api_index.app.dependency_overrides[api_index.require_user_context] = cast(
                Callable[..., Any],
                previous_override,
            )
        else:
            api_index.app.dependency_overrides.pop(api_index.require_user_context, None)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "saved"
    saved_activity = await repo.get_activity(unique_user, body["activity"]["id"])
    assert saved_activity.source == "text_extract"
    assert saved_activity.activity_summary["schema"] == "activity_summary_v1"
    assert saved_activity.activity_summary["fueling"]["carbs_g"] > 0
    assert saved_activity.activity_summary["fueling"]["calories_kcal"] > 0
    assert saved_activity.activity_summary["food_items"]
    assert saved_activity.activity_summary["thresholds_used"]["ftp_w"] == 250
