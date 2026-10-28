"""Live-database tests for activity_sources and the shadow writer (issue #533, PR A).

Run with ``bun run db:reset && bun run test:db`` against the local Supabase stack. The
role and grant assertions additionally open a direct, rolled-back Postgres connection and
refuse any non-loopback host (see ``tests/python/local_db.py``).
"""

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from typing import Any, LiteralString, cast

import pytest
from postgrest.exceptions import APIError as PostgRESTAPIError
from psycopg import errors as pg_errors

from backend.models.athlete import AthleteProfile
from backend.models.training import Activity
from backend.repos.supabase_repo import SupabaseRepository
from tests.python.local_db import direct_session

_RUN_DB_TESTS = os.environ.get("RUN_DB_TESTS") == "1"
_SUPABASE_CONFIGURED = bool(
    os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
)

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(
        not (_SUPABASE_CONFIGURED and _RUN_DB_TESTS),
        reason=("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set, or RUN_DB_TESTS=1 not provided"),
    ),
]

LEGACY_LOG_LINE = "activity_sources: legacy activities insert captured by bootstrap trigger"
STARTED_AT = datetime(2026, 7, 4, 9, 30, 15, tzinfo=UTC)


@pytest.fixture()
def repo() -> SupabaseRepository:
    return SupabaseRepository()


@pytest.fixture()
async def athletes(repo: SupabaseRepository) -> AsyncIterator[tuple[str, str]]:
    """Two athletes with UUID-shaped ids so ``auth.uid()::text`` RLS checks can match."""
    owner, other = str(uuid.uuid4()), str(uuid.uuid4())
    for user_id in (owner, other):
        await repo.upsert_athlete_profile(AthleteProfile(user_id=user_id))
    try:
        yield owner, other
    finally:
        repo._require_client().table("athlete_profiles").delete().in_(
            "user_id", [owner, other]
        ).execute()


def _ride(user_id: str, **overrides: Any) -> Activity:
    fields: dict[str, Any] = {
        "user_id": user_id,
        "sport": "cycling",
        "activity_date": date(2026, 7, 4),
        "started_at": STARTED_AT,
        "duration_seconds": 3600,
        "distance_meters": 30_004.0,
        "avg_power_watts": 210,
        "source": "fit_upload",
        "source_file_key": f"uploads/{user_id}/{uuid.uuid4()}.fit",
    }
    fields.update(overrides)
    return Activity(**fields)


def _sources(repo: SupabaseRepository, activity_id: str) -> list[dict[str, Any]]:
    return (
        repo._require_client()
        .table("activity_sources")
        .select("*")
        .eq("activity_id", activity_id)
        .execute()
        .data
    )


def _error_code(error: pytest.ExceptionInfo[PostgRESTAPIError]) -> str | None:
    return error.value.code


# ── Shadow writer ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_shadow_write_yields_exactly_one_live_source(
    repo: SupabaseRepository, athletes: tuple[str, str]
) -> None:
    owner, _ = athletes
    source_key = f"uploads/{owner}/ride.fit"

    created = await repo.create_activity(_ride(owner, source_file_key=source_key))

    assert created.id is not None
    sources = await repo.list_live_activity_sources(owner, created.id)
    assert len(sources) == 1
    [source] = sources
    # Deterministic bootstrap identity: the source id is the activity id.
    assert source.id == created.id
    assert source.activity_id == source.origin_activity_id == created.id
    assert (source.provider, source.ingest_format) == ("unknown", "fit")
    assert source.object_key == source_key
    assert source.external_id is None
    assert source.content_hash is None
    assert source.payload_fingerprint is not None
    assert source.retired_at is None
    assert source.recorded_at == STARTED_AT
    assert source.fields["duration_seconds"] == 3600
    assert source.fields["avg_power_watts"] == 210
    assert source.fields["sport"] == "cycling"


@pytest.mark.asyncio
async def test_manual_activity_without_a_start_has_a_null_fingerprint(
    repo: SupabaseRepository, athletes: tuple[str, str]
) -> None:
    owner, _ = athletes

    created = await repo.create_activity(
        Activity(
            user_id=owner,
            sport="running",
            activity_date=date(2026, 7, 4),
            duration_seconds=1800,
            source="manual",
        )
    )

    assert created.id is not None
    [source] = await repo.list_live_activity_sources(owner, created.id)
    assert (source.provider, source.ingest_format) == ("athlete", "manual")
    assert source.payload_fingerprint is None
    assert source.object_key is None


@pytest.mark.asyncio
async def test_intervals_write_records_external_id_and_duplicate_returns_none(
    repo: SupabaseRepository, athletes: tuple[str, str]
) -> None:
    owner, _ = athletes
    activity = _ride(owner, source="intervals_sync", source_file_key="intervals:i4242")

    created = await repo.create_intervals_activity(activity)
    duplicate = await repo.create_intervals_activity(activity.model_copy(update={"id": None}))

    assert created is not None and created.id is not None
    assert duplicate is None
    [source] = await repo.list_live_activity_sources(owner, created.id)
    assert (source.provider, source.ingest_format) == ("intervals", "intervals_api")
    assert source.external_id == "i4242"
    assert source.object_key == "intervals:i4242"
    activities = (
        repo._require_client().table("activities").select("id").eq("user_id", owner).execute().data
    )
    all_sources = (
        repo._require_client()
        .table("activity_sources")
        .select("id")
        .eq("user_id", owner)
        .execute()
        .data
    )
    assert len(activities) == len(all_sources) == 1


@pytest.mark.asyncio
async def test_same_intervals_key_for_a_different_athlete_is_not_a_duplicate(
    repo: SupabaseRepository, athletes: tuple[str, str]
) -> None:
    owner, other = athletes

    first = await repo.create_intervals_activity(
        _ride(owner, source="intervals_sync", source_file_key="intervals:shared")
    )
    second = await repo.create_intervals_activity(
        _ride(other, source="intervals_sync", source_file_key="intervals:shared")
    )

    assert first is not None and second is not None


@pytest.mark.asyncio
async def test_rpc_rejects_disagreeing_unknown_or_foreign_input_atomically(
    repo: SupabaseRepository, athletes: tuple[str, str]
) -> None:
    owner, other = athletes
    client = repo._require_client()
    activity_id = str(uuid.uuid4())
    payload = {
        "id": activity_id,
        "user_id": owner,
        "sport": "cycling",
        "activity_date": "2026-07-04",
        "source": "intervals_sync",
        "source_file_key": "intervals:i1",
    }
    bad_calls = [
        {"p_user_id": owner, "p_activity": payload, "p_source": {"external_id": "wrong"}},
        {"p_user_id": owner, "p_activity": payload, "p_source": {"object_key": "x"}},
        {"p_user_id": owner, "p_activity": payload, "p_source": {"content_hash": "abc"}},
        {"p_user_id": other, "p_activity": payload, "p_source": {}},
        {
            "p_user_id": owner,
            "p_activity": {k: v for k, v in payload.items() if k != "source"},
            "p_source": {},
        },
        # Keys the insert would silently drop are rejected instead.
        {"p_user_id": owner, "p_activity": {**payload, "future_column": 1}, "p_source": {}},
        {"p_user_id": owner, "p_activity": {**payload, "presentation_state": "x"}, "p_source": {}},
        {"p_user_id": owner, "p_activity": {**payload, "source_count": 2}, "p_source": {}},
        {
            "p_user_id": owner,
            "p_activity": {**payload, "source_file_key": "not-an-intervals-key"},
            "p_source": {},
        },
    ]

    for params in bad_calls:
        with pytest.raises(PostgRESTAPIError) as error:
            client.rpc("create_activity_with_source", params).execute()
        assert _error_code(error) == "22023"

    # A rejected call leaves neither an activity nor a source behind.
    assert client.table("activities").select("id").eq("id", activity_id).execute().data == []
    assert _sources(repo, activity_id) == []


@pytest.mark.asyncio
async def test_unmapped_legacy_source_raises_instead_of_defaulting(
    repo: SupabaseRepository,
) -> None:
    client = repo._require_client()

    for unmapped in ("file_upload", "strava_sync", ""):
        with pytest.raises(PostgRESTAPIError) as error:
            client.rpc("activity_legacy_source_mapping", {"p_source": unmapped}).execute()
        assert _error_code(error) == "22023"

    rows = client.rpc("activity_legacy_source_mapping", {"p_source": "tcx_upload"}).execute().data
    assert rows == [{"provider": "unknown", "ingest_format": "tcx"}]


# ── Legacy trigger and marker ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_direct_legacy_insert_is_captured_by_the_trigger(
    repo: SupabaseRepository, athletes: tuple[str, str]
) -> None:
    owner, _ = athletes
    activity_id = str(uuid.uuid4())

    repo._require_client().table("activities").insert(
        {
            "id": activity_id,
            "user_id": owner,
            "sport": "running",
            "activity_date": "2026-07-04",
            "started_at": STARTED_AT.isoformat(),
            "duration_seconds": 2400,
            "source": "gpx_upload",
            "source_file_key": "uploads/legacy.gpx",
        }
    ).execute()

    [source] = _sources(repo, activity_id)
    assert source["id"] == activity_id
    assert (source["provider"], source["ingest_format"]) == ("unknown", "gpx")
    assert source["object_key"] == "uploads/legacy.gpx"
    assert source["content_hash"] is None


def test_trigger_logs_legacy_inserts_and_the_marker_prevents_double_capture(
    athletes: tuple[str, str],
) -> None:
    owner, _ = athletes
    legacy_id, shadow_id = str(uuid.uuid4()), str(uuid.uuid4())
    with direct_session() as session:
        cursor = session.cursor
        cursor.execute(
            "insert into public.activities (id, user_id, sport, activity_date)"
            " values (%s, %s, 'running', '2026-07-04')",
            (legacy_id, owner),
        )
        assert session.notices.count(LEGACY_LOG_LINE) == 1

        session.notices.clear()
        cursor.execute(
            "select public.create_activity_with_source(%s, %s::jsonb, '{}'::jsonb)",
            (
                owner,
                f'{{"id": "{shadow_id}", "user_id": "{owner}", "sport": "running",'
                ' "activity_date": "2026-07-04", "source": "manual"}',
            ),
        )
        assert LEGACY_LOG_LINE not in session.notices

        # The duplicate (NULL) path also clears the marker.
        intervals = (
            f'{{"user_id": "{owner}", "sport": "running", "activity_date": "2026-07-04",'
            ' "source": "intervals_sync", "source_file_key": "intervals:dup"}'
        )
        for _ in range(2):
            cursor.execute(
                "select public.create_activity_with_source(%s, %s::jsonb, '{}'::jsonb)",
                (owner, intervals),
            )
        session.notices.clear()
        cursor.execute(
            "insert into public.activities (user_id, sport, activity_date)"
            " values (%s, 'running', '2026-07-06')",
            (owner,),
        )
        assert session.notices.count(LEGACY_LOG_LINE) == 1
        session.notices.clear()

        # The marker is transaction-local and cleared by the RPC, so a later legacy insert
        # in the same transaction is captured again.
        cursor.execute(
            "insert into public.activities (user_id, sport, activity_date)"
            " values (%s, 'running', '2026-07-05')",
            (owner,),
        )
        assert session.notices.count(LEGACY_LOG_LINE) == 1

        cursor.execute(
            "select count(*) from public.activity_sources where activity_id in (%s, %s)",
            (legacy_id, shadow_id),
        )
        assert cursor.fetchone() == (2,)
        cursor.execute(
            "select count(*) from public.activities a where a.user_id = %s and not exists"
            " (select 1 from public.activity_sources s where s.activity_id = a.id)",
            (owner,),
        )
        assert cursor.fetchone() == (0,)


def test_browser_role_direct_insert_is_still_captured_by_the_definer_trigger(
    athletes: tuple[str, str],
) -> None:
    owner, _ = athletes
    with direct_session(jwt_sub=owner) as session:
        cursor = session.cursor
        # Hosted Supabase projects grant browser roles DML on public tables by default, so
        # this is the exposure the trigger must cover. A fresh local bootstrap does not, so
        # grant it inside this rolled-back transaction before dropping to the role.
        cursor.execute("grant select, insert on public.activities to authenticated")
        cursor.execute("set local role authenticated")
        cursor.execute(
            "insert into public.activities (user_id, sport, activity_date)"
            " values (%s, 'running', '2026-07-04') returning id",
            (owner,),
        )
        row = cursor.fetchone()
        assert row is not None
        assert session.notices.count(LEGACY_LOG_LINE) == 1
        # authenticated has no INSERT on activity_sources, so only the definer trigger
        # could have written this row; RLS lets the owner read it back.
        cursor.execute("select id from public.activity_sources where activity_id = %s", (row[0],))
        assert cursor.fetchall() == [(row[0],)]


# ── Evidence immutability, composite FKs, identity indexes ──────────────────


@pytest.mark.asyncio
async def test_evidence_update_raises_but_lifecycle_update_is_allowed(
    repo: SupabaseRepository, athletes: tuple[str, str]
) -> None:
    owner, _ = athletes
    created = await repo.create_activity(_ride(owner))
    assert created.id is not None
    table = repo._require_client().table("activity_sources")

    for evidence in (
        {"provider": "garmin"},
        {"ingest_format": "gpx"},
        {"external_id": "x"},
        {"object_key": "other"},
        {"content_hash": "h"},
        {"payload_fingerprint": "f"},
        {"fields": {"duration_seconds": 1}},
        {"raw_extraction": {"a": 1}},
        {"recorded_at": "2020-01-01T00:00:00Z"},
        {"created_at": "2020-01-01T00:00:00Z"},
    ):
        with pytest.raises(PostgRESTAPIError) as error:
            table.update(evidence).eq("id", created.id).execute()
        assert _error_code(error) == "22023", evidence

    retired = (
        table.update({"retired_at": "2026-07-05T00:00:00Z"}).eq("id", created.id).execute().data
    )
    assert retired[0]["retired_at"] is not None
    assert await repo.list_live_activity_sources(owner, created.id) == []


@pytest.mark.asyncio
async def test_cross_user_source_reference_fails_the_composite_foreign_key(
    repo: SupabaseRepository, athletes: tuple[str, str]
) -> None:
    owner, other = athletes
    created = await repo.create_activity(_ride(owner))
    assert created.id is not None

    with pytest.raises(PostgRESTAPIError) as error:
        repo._require_client().table("activity_sources").insert(
            {
                "user_id": other,
                "activity_id": created.id,
                "origin_activity_id": created.id,
                "provider": "garmin",
                "ingest_format": "fit",
            }
        ).execute()

    assert _error_code(error) == "23503"


@pytest.mark.asyncio
async def test_superseded_by_pointer_is_same_user_and_set_null_keeps_the_row(
    repo: SupabaseRepository, athletes: tuple[str, str]
) -> None:
    owner, other = athletes
    client = repo._require_client()
    older = await repo.create_activity(_ride(owner))
    newer = await repo.create_activity(_ride(owner))
    foreign = await repo.create_activity(_ride(other))
    assert older.id and newer.id and foreign.id

    with pytest.raises(PostgRESTAPIError) as error:
        client.table("activities").update(
            {"presentation_state": "superseded", "superseded_by_activity_id": foreign.id}
        ).eq("id", older.id).execute()
    assert _error_code(error) == "23503"

    client.table("activities").update(
        {"presentation_state": "superseded", "superseded_by_activity_id": newer.id}
    ).eq("id", older.id).execute()
    client.table("activities").delete().eq("id", newer.id).execute()

    [survivor] = client.table("activities").select("*").eq("id", older.id).execute().data
    assert survivor["superseded_by_activity_id"] is None
    assert survivor["user_id"] == owner
    assert survivor["presentation_state"] == "superseded"

    with pytest.raises(PostgRESTAPIError) as invalid_state:
        client.table("activities").update({"presentation_state": "bogus"}).eq(
            "id", older.id
        ).execute()
    assert _error_code(invalid_state) == "23514"


@pytest.mark.asyncio
async def test_update_activity_does_not_reset_rpc_owned_projection_columns(
    repo: SupabaseRepository, athletes: tuple[str, str]
) -> None:
    owner, _ = athletes
    created = await repo.create_activity(_ride(owner))
    assert created.id is not None
    repo._require_client().table("activities").update(
        {"presentation_state": "superseded", "source_count": 3}
    ).eq("id", created.id).execute()

    await repo.update_activity(created.model_copy(update={"athlete_notes": "felt good"}))

    reloaded = await repo.get_activity(owner, created.id)
    assert reloaded.athlete_notes == "felt good"
    assert reloaded.presentation_state == "superseded"
    assert reloaded.source_count == 3


@pytest.mark.asyncio
async def test_fingerprint_collisions_do_not_reject(
    repo: SupabaseRepository, athletes: tuple[str, str]
) -> None:
    owner, _ = athletes

    first = await repo.create_activity(_ride(owner))
    # Same sport, start, duration, and a distance inside the same 10 m bucket.
    second = await repo.create_activity(_ride(owner, distance_meters=30_001.0))

    assert first.id and second.id and first.id != second.id
    [first_source] = await repo.list_live_activity_sources(owner, first.id)
    [second_source] = await repo.list_live_activity_sources(owner, second.id)
    assert first_source.payload_fingerprint is not None
    assert first_source.payload_fingerprint == second_source.payload_fingerprint


@pytest.mark.asyncio
async def test_content_hash_and_known_provider_external_id_collisions_reject(
    repo: SupabaseRepository, athletes: tuple[str, str]
) -> None:
    owner, other = athletes
    client = repo._require_client()
    first = await repo.create_activity(_ride(owner))
    second = await repo.create_activity(_ride(owner))
    foreign = await repo.create_activity(_ride(other))
    assert first.id and second.id and foreign.id

    def extra_source(activity: Activity, activity_id: str, **fields: Any) -> dict[str, Any]:
        return {
            "id": str(uuid.uuid4()),
            "user_id": activity.user_id,
            "activity_id": activity_id,
            "origin_activity_id": activity_id,
            "ingest_format": "fit",
            "provider": "unknown",
            **fields,
        }

    table = client.table("activity_sources")
    table.insert(extra_source(first, first.id, content_hash="h1")).execute()
    with pytest.raises(PostgRESTAPIError) as same_hash:
        table.insert(extra_source(second, second.id, content_hash="h1")).execute()
    assert _error_code(same_hash) == "23505"
    # Byte identity is per athlete, never global.
    table.insert(extra_source(foreign, foreign.id, content_hash="h1")).execute()

    table.insert(extra_source(first, first.id, provider="garmin", external_id="g1")).execute()
    with pytest.raises(PostgRESTAPIError) as same_external_id:
        table.insert(extra_source(second, second.id, provider="garmin", external_id="g1")).execute()
    assert _error_code(same_external_id) == "23505"
    # 'unknown' is not a namespace, so identical ids from unknown providers coexist.
    table.insert(extra_source(first, first.id, external_id="u1")).execute()
    table.insert(extra_source(second, second.id, external_id="u1")).execute()

    # A retired source no longer holds its identity.
    table.update({"retired_at": "2026-07-05T00:00:00Z"}).eq("activity_id", first.id).eq(
        "content_hash", "h1"
    ).execute()
    table.insert(extra_source(second, second.id, content_hash="h1")).execute()


@pytest.mark.asyncio
async def test_one_live_athlete_override_per_activity(
    repo: SupabaseRepository, athletes: tuple[str, str]
) -> None:
    owner, _ = athletes
    created = await repo.create_activity(_ride(owner))
    assert created.id is not None
    table = repo._require_client().table("activity_sources")

    def override() -> dict[str, Any]:
        return {
            "user_id": owner,
            "activity_id": created.id,
            "origin_activity_id": created.id,
            "provider": "athlete",
            "ingest_format": "athlete_override",
        }

    table.insert(override()).execute()
    with pytest.raises(PostgRESTAPIError) as error:
        table.insert(override()).execute()
    assert _error_code(error) == "23505"


@pytest.mark.asyncio
async def test_deleting_the_athlete_cascades_through_activities_and_sources(
    repo: SupabaseRepository,
) -> None:
    owner = str(uuid.uuid4())
    client = repo._require_client()
    await repo.upsert_athlete_profile(AthleteProfile(user_id=owner))
    older = await repo.create_activity(_ride(owner))
    newer = await repo.create_activity(_ride(owner))
    assert older.id and newer.id
    client.table("activities").update(
        {"presentation_state": "superseded", "superseded_by_activity_id": newer.id}
    ).eq("id", older.id).execute()

    client.table("athlete_profiles").delete().eq("user_id", owner).execute()

    assert client.table("activity_sources").select("id").eq("user_id", owner).execute().data == []
    assert client.table("activities").select("id").eq("user_id", owner).execute().data == []


# ── Reference data, grants, and RLS (direct Postgres, local only) ───────────


@pytest.mark.asyncio
async def test_ingest_formats_are_seeded_with_unique_ranks(repo: SupabaseRepository) -> None:
    rows = repo._require_client().table("activity_ingest_formats").select("*").execute().data

    assert {row["ingest_format"]: row["fidelity_rank"] for row in rows} == {
        "athlete_override": 0,
        "fit": 1,
        "tcx": 2,
        "gpx": 3,
        "intervals_api": 4,
        "text": 5,
        "screenshot": 6,
        "manual": 7,
    }


@pytest.mark.parametrize("role", ["anon", "authenticated", "service_role"])
def test_ingest_formats_are_read_only_for_every_application_role(role: str) -> None:
    for statement in (
        "insert into public.activity_ingest_formats values ('x', 99)",
        "update public.activity_ingest_formats set fidelity_rank = 99",
        "delete from public.activity_ingest_formats",
    ):
        with (
            direct_session(role=role) as session,
            pytest.raises(pg_errors.InsufficientPrivilege),
        ):
            session.cursor.execute(statement)


def test_ingest_formats_are_selectable_by_signed_in_roles_but_not_anon() -> None:
    for role in ("authenticated", "service_role"):
        with direct_session(role=role) as session:
            session.cursor.execute("select count(*) from public.activity_ingest_formats")
            assert session.cursor.fetchone() == (8,)
    with direct_session(role="anon") as session, pytest.raises(pg_errors.InsufficientPrivilege):
        session.cursor.execute("select 1 from public.activity_ingest_formats")


@pytest.mark.parametrize(
    "call",
    [
        "select public.create_activity_with_source('u', '{}'::jsonb, '{}'::jsonb)",
        "select * from public.activity_legacy_source_mapping('manual')",
        "select public.activity_intervals_external_id('intervals:1')",
        "select public.activity_payload_fingerprint('running', now(), 1, 1.0)",
    ],
)
@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_shadow_writer_and_helpers_are_not_executable_by_browser_roles(
    role: str, call: str
) -> None:
    with direct_session(role=role) as session, pytest.raises(pg_errors.InsufficientPrivilege):
        # `call` comes from the literal parametrize list above, never from input.
        session.cursor.execute(cast(LiteralString, call))


def test_service_role_can_execute_the_shadow_writer() -> None:
    # Reaching the function body (and its 22023 validation) proves EXECUTE was granted.
    with (
        direct_session(role="service_role") as session,
        pytest.raises(pg_errors.InvalidParameterValue),
    ):
        session.cursor.execute(
            "select public.create_activity_with_source('u', '{}'::jsonb, '{}'::jsonb)"
        )


def test_authenticated_can_read_only_its_own_sources_and_cannot_write_them(
    athletes: tuple[str, str],
) -> None:
    owner, other = athletes
    with direct_session() as session:
        cursor = session.cursor
        for user_id in (owner, other):
            cursor.execute(
                "insert into public.activities (user_id, sport, activity_date)"
                " values (%s, 'running', '2026-07-04')",
                (user_id,),
            )
        cursor.connection.commit()
    with direct_session(role="authenticated", jwt_sub=owner) as session:
        session.cursor.execute("select user_id from public.activity_sources")
        assert {row[0] for row in session.cursor.fetchall()} == {owner}
    with (
        direct_session(role="authenticated", jwt_sub=owner) as session,
        pytest.raises(pg_errors.InsufficientPrivilege),
    ):
        session.cursor.execute(
            "insert into public.activity_sources"
            " (user_id, activity_id, origin_activity_id, provider, ingest_format)"
            " select user_id, id, id, 'garmin', 'fit' from public.activities limit 1"
        )
    with (
        direct_session(role="authenticated", jwt_sub=owner) as session,
        pytest.raises(pg_errors.InsufficientPrivilege),
    ):
        session.cursor.execute("update public.activity_sources set retired_at = now()")
    with (
        direct_session(role="anon") as session,
        pytest.raises(pg_errors.InsufficientPrivilege),
    ):
        session.cursor.execute("select 1 from public.activity_sources")


def test_load_rebuild_pending_is_not_writable_or_readable_by_browser_roles() -> None:
    with (
        direct_session(role="authenticated") as session,
        pytest.raises(pg_errors.InsufficientPrivilege),
    ):
        session.cursor.execute(
            "insert into public.load_rebuild_pending (user_id, pending_from)"
            " values ('u', '2026-07-01')"
        )
    with direct_session(role="anon") as session, pytest.raises(pg_errors.InsufficientPrivilege):
        session.cursor.execute("select 1 from public.load_rebuild_pending")


@pytest.mark.asyncio
async def test_load_rebuild_pending_treats_null_sport_as_one_key(
    repo: SupabaseRepository, athletes: tuple[str, str]
) -> None:
    owner, _ = athletes
    table = repo._require_client().table("load_rebuild_pending")
    table.insert({"user_id": owner, "sport": None, "pending_from": "2026-07-01"}).execute()
    table.insert({"user_id": owner, "sport": "cycling", "pending_from": "2026-07-01"}).execute()

    with pytest.raises(PostgRESTAPIError) as error:
        table.insert({"user_id": owner, "sport": None, "pending_from": "2026-07-02"}).execute()

    assert _error_code(error) == "23505"
