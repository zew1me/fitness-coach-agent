"""Integration tests requiring a live Supabase connection.

Run with:
    bun run db:start          # start local Supabase (once)
    bun run test:db           # uv run pytest -m db tests/python/

These tests are excluded from the default gate (uv run pytest) via
addopts = "-m 'not db'" in pyproject.toml.  They are not run in CI
and must be run manually against a local or preview Supabase project.

Red/green context for migration 0005
--------------------------------------
Before applying 0005_specialization_pct_nullable.sql:
  bun run db:reset            # replay migrations 0001-0004
  bun run test:db             # test_specialization_pct_* RED -> APIError NOT NULL
After applying 0005:
  bun run db:reset            # replay 0001-0005
  bun run test:db             # all db tests GREEN
"""

import os
import uuid

import pytest

from backend.models.athlete import AthleteProfile
from backend.repos.supabase_repo import SupabaseRepository

_SUPABASE_CONFIGURED = bool(
    os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
)

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(
        not _SUPABASE_CONFIGURED,
        reason="SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set",
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

    This is the canonical regression test for issue #254.  Before migration 0005 this
    raises APIError because the column was NOT NULL.  After 0005 it stores NULL.
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
