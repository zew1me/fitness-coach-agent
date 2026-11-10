"""RLS role matrix: anon, two authenticated athletes, and service_role (issue #453).

Row-level security is a database boundary, so mocked clients and ordinary repository tests
cannot see a missing policy or an accidentally broadened one. These tests open a direct,
always-rolled-back Postgres connection to the local Supabase database (see
``tests/python/local_db.py``, which refuses any non-loopback host), seed two athletes as the
table owner, and then drop to each application role with real JWT claims so ``auth.uid()``
resolves exactly as it does behind PostgREST.

Run with::

    bun run db:reset
    bun run test:db   # or: RUN_DB_TESTS=1 uv run pytest -m db tests/python/test_rls_matrix_db.py

Why this is a gated job rather than part of CI: it needs a Postgres with every migration
applied in canonical order, and CI does not start a Supabase stack. It runs alongside the
other ``db``-marked suites.

Table privileges differ between environments: hosted projects grant browser roles DML on
public tables by default, while a fresh local bootstrap does not. Each test therefore grants
DML to ``anon`` and ``authenticated`` inside its rolled-back transaction, so RLS, not a
missing grant, is the barrier under test and the matrix means the same thing everywhere.
"""

import json
import os
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from psycopg import Cursor, sql
from psycopg import errors as pg_errors

from tests.python.local_db import direct_session

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(
        os.environ.get("RUN_DB_TESTS") != "1",
        reason="RUN_DB_TESTS=1 not provided (needs the local Supabase Postgres)",
    ),
]

# Tables an athlete reads and writes directly, scoped by their own ``user_id``.
OWNER_MANAGED_TABLES = (
    "athlete_profiles",
    "sport_thresholds",
    "goals",
    "training_plans",
    "plan_workouts",
    "activities",
    "daily_load_snapshots",
    "recovery_logs",
    "schedule_availability",
    "schedule_overrides",
    "chat_threads",
    "chat_messages",
)

# Tables an athlete may read (own rows only) but never write directly.
OWNER_READ_ONLY_TABLES = ("oauth_grants", "activity_sources", "load_rebuild_pending")

# Server-only state: RLS enabled with no client policies at all.
SERVER_ONLY_TABLES = (
    "oauth_authorization_codes",
    "oauth_refresh_tokens",
    "threshold_recalibration_candidates",
    "chat_model_states",
    "intervals_connections",
    "agent_emails",
    "activity_sources_backfill_progress",
)

# Shared reference data every signed-in athlete may read.
REFERENCE_TABLES = ("activity_ingest_formats",)

# Server-only tables that carry a ``user_id`` and so can be seeded per athlete.
SERVER_ONLY_USER_TABLES = (
    "oauth_authorization_codes",
    "oauth_refresh_tokens",
    "threshold_recalibration_candidates",
    "chat_model_states",
    "intervals_connections",
)


@dataclass(frozen=True)
class Athlete:
    """One seeded athlete and the ids of the parent rows they own."""

    user_id: str
    goal_id: str
    plan_id: str
    thread_id: str
    grant_id: str


def _new_athlete() -> Athlete:
    return Athlete(
        user_id=str(uuid.uuid4()),
        goal_id=str(uuid.uuid4()),
        plan_id=str(uuid.uuid4()),
        thread_id=str(uuid.uuid4()),
        grant_id=str(uuid.uuid4()),
    )


# One fresh, non-conflicting row per owner-managed table, stamped with ``user_id`` and pointing
# at ``parent``'s rows (normally the same athlete). ``day`` keeps dated unique keys distinct.
Statement = sql.SQL | sql.Composed
InsertBuilder = Callable[[str, Athlete, int], tuple[Statement, tuple[Any, ...]]]

_INSERTS: dict[str, InsertBuilder] = {
    "athlete_profiles": lambda user, _p, _d: (
        sql.SQL("insert into public.athlete_profiles (user_id) values (%s)"),
        (user,),
    ),
    "sport_thresholds": lambda user, _p, _d: (
        sql.SQL("insert into public.sport_thresholds (user_id, sport) values (%s, 'running')"),
        (user,),
    ),
    "goals": lambda user, _p, _d: (
        sql.SQL("insert into public.goals (user_id, goal_type, title) values (%s, 'event', 'RLS')"),
        (user,),
    ),
    "training_plans": lambda user, parent, _d: (
        sql.SQL(
            "insert into public.training_plans"
            " (user_id, title, plan_type, start_date, end_date, target_goal_id)"
            " values (%s, 'RLS', 'weekly', '2026-07-01', '2026-07-07', %s)"
        ),
        (user, parent.goal_id),
    ),
    "plan_workouts": lambda user, parent, day: (
        sql.SQL(
            "insert into public.plan_workouts"
            " (plan_id, user_id, workout_date, day_of_week, week_number, sport, title,"
            " workout_type)"
            " values (%s, %s, make_date(2026, 7, %s), 0, 1, 'running', 'RLS', 'endurance')"
        ),
        (parent.plan_id, user, day),
    ),
    "activities": lambda user, _p, day: (
        sql.SQL(
            "insert into public.activities (user_id, sport, activity_date)"
            " values (%s, 'running', make_date(2026, 7, %s))"
        ),
        (user, day),
    ),
    "daily_load_snapshots": lambda user, _p, day: (
        sql.SQL(
            "insert into public.daily_load_snapshots (user_id, snapshot_date)"
            " values (%s, make_date(2026, 7, %s))"
        ),
        (user, day),
    ),
    "recovery_logs": lambda user, _p, day: (
        sql.SQL(
            "insert into public.recovery_logs (user_id, log_date)"
            " values (%s, make_date(2026, 7, %s))"
        ),
        (user, day),
    ),
    "schedule_availability": lambda user, _p, _d: (
        sql.SQL("insert into public.schedule_availability (user_id) values (%s)"),
        (user,),
    ),
    "schedule_overrides": lambda user, _p, day: (
        sql.SQL(
            "insert into public.schedule_overrides (user_id, override_date)"
            " values (%s, make_date(2026, 7, %s))"
        ),
        (user, day),
    ),
    "chat_threads": lambda user, _p, _d: (
        sql.SQL("insert into public.chat_threads (id, user_id) values (gen_random_uuid(), %s)"),
        (user,),
    ),
    "chat_messages": lambda user, parent, _d: (
        sql.SQL(
            "insert into public.chat_messages (id, thread_id, user_id, role)"
            " values (gen_random_uuid(), %s, %s, 'user')"
        ),
        (parent.thread_id, user),
    ),
}

# Tables whose seeded row is already the athlete's only permitted row (a unique ``user_id``),
# so the "athlete can insert their own row" check has nothing fresh to insert.
_ONE_ROW_PER_ATHLETE = frozenset({"athlete_profiles", "schedule_availability", "chat_threads"})


def _seed(cursor: Cursor[Any], athlete: Athlete) -> None:
    """Create one row per user-scoped table for ``athlete``, as the owner (RLS bypassed)."""
    user = athlete.user_id
    cursor.execute("insert into public.athlete_profiles (user_id) values (%s)", (user,))
    cursor.execute(
        "insert into public.goals (id, user_id, goal_type, title) values (%s, %s, 'event', 'g')",
        (athlete.goal_id, user),
    )
    cursor.execute(
        "insert into public.training_plans"
        " (id, user_id, title, plan_type, start_date, end_date, target_goal_id)"
        " values (%s, %s, 'p', 'weekly', '2026-07-01', '2026-07-07', %s)",
        (athlete.plan_id, user, athlete.goal_id),
    )
    cursor.execute(
        "insert into public.chat_threads (id, user_id) values (%s, %s)",
        (athlete.thread_id, user),
    )
    cursor.execute(
        "insert into public.oauth_grants (id, user_id, client_id, redirect_uri)"
        " values (%s, %s, 'chatgpt', 'https://example.test/cb')",
        (athlete.grant_id, user),
    )
    seeded_with_known_ids = {"athlete_profiles", "goals", "training_plans", "chat_threads"}
    for table in OWNER_MANAGED_TABLES:
        if table in seeded_with_known_ids:
            continue  # seeded above with the ids the relationship checks need
        statement, params = _INSERTS[table](user, athlete, 1)
        cursor.execute(statement, params)
    # The activities insert trigger already wrote this athlete's activity_sources row.
    cursor.execute(
        "insert into public.load_rebuild_pending (user_id, pending_from)"
        " values (%s, '2026-07-01') on conflict do nothing",
        (user,),
    )
    cursor.execute(
        "insert into public.chat_model_states (thread_id, user_id) values (%s, %s)",
        (athlete.thread_id, user),
    )
    cursor.execute(
        "insert into public.oauth_authorization_codes"
        " (id, grant_id, user_id, client_id, redirect_uri, code_challenge,"
        " code_challenge_method, token_hash, expires_at)"
        " values (gen_random_uuid(), %s, %s, 'chatgpt', 'https://example.test/cb', 'c',"
        " 'S256', %s, now() + interval '5 minutes')",
        (athlete.grant_id, user, f"code-{user}"),
    )
    cursor.execute(
        "insert into public.oauth_refresh_tokens"
        " (id, grant_id, user_id, client_id, token_hash, expires_at)"
        " values (gen_random_uuid(), %s, %s, 'chatgpt', %s, now() + interval '30 days')",
        (athlete.grant_id, user, f"refresh-{user}"),
    )
    cursor.execute(
        "insert into public.threshold_recalibration_candidates"
        " (user_id, sport, confidence, explanation, candidate_threshold)"
        " values (%s, 'cycling', 'low', 'rls', '{}'::jsonb)",
        (user,),
    )
    cursor.execute(
        "insert into public.intervals_connections"
        " (user_id, intervals_athlete_id, access_token_ciphertext) values (%s, 'i0', 'x')",
        (user,),
    )


class Matrix:
    """A seeded, rolled-back transaction that can switch between application roles."""

    def __init__(self, cursor: Cursor[Any], athlete: Athlete, other: Athlete) -> None:
        self.cursor = cursor
        self.athlete = athlete
        self.other = other

    def become(self, role: str | None, user: Athlete | None = None) -> None:
        """Switch to ``role`` (``None`` = table owner) with JWT claims for ``user``."""
        self.cursor.execute("reset role")
        claims: dict[str, str] = {"role": role or "postgres"}
        if user is not None:
            claims["sub"] = user.user_id
        self.cursor.execute(
            "select set_config('request.jwt.claims', %s, true)", (json.dumps(claims),)
        )
        if role is not None:
            self.cursor.execute(sql.SQL("set local role {}").format(sql.Identifier(role)))

    def attempt(self, statement: Statement, params: tuple[Any, ...] = ()) -> pg_errors.Error | int:
        """Run ``statement`` in a savepoint; return its rowcount, or the error it raised."""
        self.cursor.execute("savepoint rls_attempt")
        try:
            self.cursor.execute(statement, params)
        except pg_errors.Error as error:
            self.cursor.execute("rollback to savepoint rls_attempt")
            return error
        affected = self.cursor.rowcount  # read before RELEASE replaces it
        self.cursor.execute("release savepoint rls_attempt")
        return affected

    def owners(self, table: str) -> set[str]:
        """The ``user_id`` values visible in ``table`` to the current role."""
        self.cursor.execute(
            sql.SQL("select distinct user_id from public.{}").format(sql.Identifier(table))
        )
        return {row[0] for row in self.cursor.fetchall()}

    def count(self, table: str) -> int:
        self.cursor.execute(sql.SQL("select count(*) from public.{}").format(sql.Identifier(table)))
        row = self.cursor.fetchone()
        assert row is not None
        return int(row[0])

    def rows_for(self, table: str, user: Athlete) -> int:
        """How many of ``user``'s rows exist, counted as the owner so RLS cannot hide any."""
        self.become(None)
        self.cursor.execute(
            sql.SQL("select count(*) from public.{} where user_id = %s").format(
                sql.Identifier(table)
            ),
            (user.user_id,),
        )
        row = self.cursor.fetchone()
        assert row is not None
        return int(row[0])


@pytest.fixture()
def matrix() -> Iterator[Matrix]:
    athlete, other = _new_athlete(), _new_athlete()
    with direct_session() as session:
        cursor = session.cursor
        # See the module docstring: emulate hosted default grants so RLS is the only barrier.
        cursor.execute(
            "grant select, insert, update, delete on all tables in schema public"
            " to anon, authenticated"
        )
        cursor.execute(
            "grant usage, select on all sequences in schema public to anon, authenticated"
        )
        _seed(cursor, athlete)
        _seed(cursor, other)
        yield Matrix(cursor, athlete, other)


def _table(name: str) -> sql.Identifier:
    return sql.Identifier(name)


def _is_rls_denial(outcome: pg_errors.Error | int) -> bool:
    """A ``WITH CHECK`` violation surfaces as SQLSTATE 42501 (insufficient_privilege)."""
    return isinstance(outcome, pg_errors.InsufficientPrivilege)


# ── Catalog: every table is classified and protected ─────────────────────────


def test_every_public_table_has_rls_enabled_and_is_classified(matrix: Matrix) -> None:
    matrix.cursor.execute(
        "select c.relname, c.relrowsecurity from pg_class c"
        " join pg_namespace n on n.oid = c.relnamespace"
        " where n.nspname = 'public' and c.relkind in ('r', 'p')"
    )
    tables = dict(matrix.cursor.fetchall())

    assert [name for name, enabled in tables.items() if not enabled] == []
    classified = (
        set(OWNER_MANAGED_TABLES)
        | set(OWNER_READ_ONLY_TABLES)
        | set(SERVER_ONLY_TABLES)
        | set(REFERENCE_TABLES)
    )
    # A new table must be added to one access class (and so to this matrix) deliberately.
    assert set(tables) == classified


def test_client_policies_never_target_anon_or_public(matrix: Matrix) -> None:
    matrix.cursor.execute(
        "select tablename, policyname, roles::text[] from pg_policies where schemaname = 'public'"
    )
    broad = [
        (table, policy)
        for table, policy, roles in matrix.cursor.fetchall()
        if {"anon", "public"} & set(roles)
    ]
    assert broad == []


def test_server_only_tables_have_no_client_policies(matrix: Matrix) -> None:
    matrix.cursor.execute(
        "select distinct tablename from pg_policies where schemaname = 'public'"
        " and tablename = any(%s)",
        (list(SERVER_ONLY_TABLES),),
    )
    assert matrix.cursor.fetchall() == []


def test_read_only_tables_expose_only_select_policies(matrix: Matrix) -> None:
    matrix.cursor.execute(
        "select tablename, cmd from pg_policies where schemaname = 'public'"
        " and tablename = any(%s) and cmd <> 'SELECT'",
        (list(OWNER_READ_ONLY_TABLES + REFERENCE_TABLES),),
    )
    assert matrix.cursor.fetchall() == []


# ── Owner-managed tables: own rows allowed, other athlete's rows denied ──────


@pytest.mark.parametrize("table", OWNER_MANAGED_TABLES)
def test_athlete_sees_only_their_own_rows(matrix: Matrix, table: str) -> None:
    matrix.become("authenticated", matrix.athlete)
    assert matrix.owners(table) == {matrix.athlete.user_id}


@pytest.mark.parametrize("table", OWNER_MANAGED_TABLES)
def test_athlete_can_update_and_delete_own_rows_only(matrix: Matrix, table: str) -> None:
    other_rows = matrix.rows_for(table, matrix.other)
    own_rows = matrix.rows_for(table, matrix.athlete)
    touch = sql.SQL("update public.{} set user_id = user_id where user_id = %s").format(
        _table(table)
    )
    remove = sql.SQL("delete from public.{} where user_id = %s").format(_table(table))

    matrix.become("authenticated", matrix.athlete)
    assert matrix.attempt(touch, (matrix.other.user_id,)) == 0
    assert matrix.attempt(remove, (matrix.other.user_id,)) == 0
    assert matrix.attempt(touch, (matrix.athlete.user_id,)) == own_rows
    # Last, because it is destructive. FK cascades may remove the athlete's rows in other
    # tables, so assert the outcome rather than tying the rowcount to the seed.
    outcome = matrix.attempt(remove, (matrix.athlete.user_id,))
    assert not isinstance(outcome, pg_errors.Error), outcome

    assert matrix.rows_for(table, matrix.athlete) == 0
    assert matrix.rows_for(table, matrix.other) == other_rows


@pytest.mark.parametrize("table", OWNER_MANAGED_TABLES)
def test_athlete_cannot_reassign_a_row_to_another_athlete(matrix: Matrix, table: str) -> None:
    reassign = sql.SQL("update public.{} set user_id = %s where user_id = %s").format(_table(table))
    matrix.become("authenticated", matrix.athlete)
    outcome = matrix.attempt(reassign, (matrix.other.user_id, matrix.athlete.user_id))
    assert _is_rls_denial(outcome), outcome


@pytest.mark.parametrize("table", OWNER_MANAGED_TABLES)
def test_athlete_cannot_insert_a_row_for_another_athlete(matrix: Matrix, table: str) -> None:
    statement, params = _INSERTS[table](matrix.other.user_id, matrix.other, 2)
    matrix.become("authenticated", matrix.athlete)
    outcome = matrix.attempt(statement, params)
    assert _is_rls_denial(outcome), outcome


@pytest.mark.parametrize(
    "table", [table for table in OWNER_MANAGED_TABLES if table not in _ONE_ROW_PER_ATHLETE]
)
def test_athlete_can_insert_their_own_row(matrix: Matrix, table: str) -> None:
    statement, params = _INSERTS[table](matrix.athlete.user_id, matrix.athlete, 2)
    matrix.become("authenticated", matrix.athlete)
    assert matrix.attempt(statement, params) == 1


# ── Relationship-bearing tables: parent ownership ────────────────────────────

# Child tables whose rows reference a parent row (thread, plan, goal) by id.
_RELATIONSHIPS = ("chat_messages", "plan_workouts", "training_plans")


@pytest.mark.parametrize("table", _RELATIONSHIPS)
def test_athlete_can_attach_a_child_to_their_own_parent(matrix: Matrix, table: str) -> None:
    statement, params = _INSERTS[table](matrix.athlete.user_id, matrix.athlete, 3)
    matrix.become("authenticated", matrix.athlete)
    assert matrix.attempt(statement, params) == 1


class CrossAthleteParentAcceptedError(AssertionError):
    """The known #452 gap: a child row referencing another athlete's parent was inserted."""


@pytest.mark.xfail(
    strict=True,
    # Only the known gap is expected; any other error (a broken seed, a renamed column) must
    # still fail the run instead of hiding behind the expected failure.
    raises=CrossAthleteParentAcceptedError,
    reason=(
        "#452: policies check only the child's own user_id, not the referenced parent's owner."
        " Remove this marker when #452 lands."
    ),
)
@pytest.mark.parametrize("table", _RELATIONSHIPS)
def test_athlete_cannot_attach_a_child_to_another_athletes_parent(
    matrix: Matrix, table: str
) -> None:
    # The child carries the caller's own user_id, so only a parent-ownership check can stop it.
    statement, params = _INSERTS[table](matrix.athlete.user_id, matrix.other, 4)
    matrix.become("authenticated", matrix.athlete)
    outcome = matrix.attempt(statement, params)
    if outcome == 1:
        raise CrossAthleteParentAcceptedError(table)
    assert _is_rls_denial(outcome), outcome


# ── Read-only and reference tables ───────────────────────────────────────────


@pytest.mark.parametrize("table", OWNER_READ_ONLY_TABLES)
def test_athlete_reads_but_cannot_write_own_read_only_rows(matrix: Matrix, table: str) -> None:
    own_rows = matrix.rows_for(table, matrix.athlete)
    assert own_rows > 0

    matrix.become("authenticated", matrix.athlete)
    assert matrix.owners(table) == {matrix.athlete.user_id}
    touch = sql.SQL("update public.{} set user_id = user_id where user_id = %s").format(
        _table(table)
    )
    remove = sql.SQL("delete from public.{} where user_id = %s").format(_table(table))
    assert matrix.attempt(touch, (matrix.athlete.user_id,)) == 0
    assert matrix.attempt(remove, (matrix.athlete.user_id,)) == 0

    assert matrix.rows_for(table, matrix.athlete) == own_rows


def test_athlete_cannot_insert_an_oauth_grant(matrix: Matrix) -> None:
    matrix.become("authenticated", matrix.athlete)
    outcome = matrix.attempt(
        sql.SQL(
            "insert into public.oauth_grants (id, user_id, client_id, redirect_uri)"
            " values (gen_random_uuid(), %s, 'evil', 'https://evil.test/cb')"
        ),
        (matrix.athlete.user_id,),
    )
    assert _is_rls_denial(outcome), outcome


def test_reference_formats_are_readable_but_not_writable(matrix: Matrix) -> None:
    owner_count = matrix.count("activity_ingest_formats")
    assert owner_count > 0

    matrix.become("authenticated", matrix.athlete)
    assert matrix.count("activity_ingest_formats") == owner_count
    outcome = matrix.attempt(
        sql.SQL(
            "insert into public.activity_ingest_formats (ingest_format, fidelity_rank)"
            " values ('rls_probe', 999)"
        )
    )
    assert _is_rls_denial(outcome), outcome


# ── Server-only tables: invisible and immutable to browser roles ─────────────


@pytest.mark.parametrize("role", ["anon", "authenticated"])
@pytest.mark.parametrize("table", SERVER_ONLY_TABLES)
def test_server_only_tables_are_invisible_to_browser_roles(
    matrix: Matrix, table: str, role: str
) -> None:
    matrix.become(None)
    assert matrix.count(table) > 0 or table == "agent_emails"

    matrix.become(role, matrix.athlete if role == "authenticated" else None)
    assert matrix.count(table) == 0


@pytest.mark.parametrize("table", SERVER_ONLY_USER_TABLES)
def test_athlete_cannot_modify_their_own_server_only_rows(matrix: Matrix, table: str) -> None:
    own_rows = matrix.rows_for(table, matrix.athlete)
    assert own_rows > 0

    matrix.become("authenticated", matrix.athlete)
    touch = sql.SQL("update public.{} set user_id = user_id where user_id = %s").format(
        _table(table)
    )
    remove = sql.SQL("delete from public.{} where user_id = %s").format(_table(table))
    assert matrix.attempt(touch, (matrix.athlete.user_id,)) == 0
    assert matrix.attempt(remove, (matrix.athlete.user_id,)) == 0

    assert matrix.rows_for(table, matrix.athlete) == own_rows


def test_athlete_cannot_insert_server_only_state(matrix: Matrix) -> None:
    matrix.become("authenticated", matrix.athlete)
    outcome = matrix.attempt(
        sql.SQL(
            "insert into public.threshold_recalibration_candidates"
            " (user_id, sport, confidence, explanation, candidate_threshold)"
            " values (%s, 'running', 'high', 'forged', '{}'::jsonb)"
        ),
        (matrix.athlete.user_id,),
    )
    assert _is_rls_denial(outcome), outcome


# ── Anonymous role: no application rows, no mutation ─────────────────────────


ALL_USER_TABLES = OWNER_MANAGED_TABLES + OWNER_READ_ONLY_TABLES + SERVER_ONLY_USER_TABLES


@pytest.mark.parametrize("table", ALL_USER_TABLES + REFERENCE_TABLES)
def test_anon_sees_no_rows(matrix: Matrix, table: str) -> None:
    matrix.become("anon")
    assert matrix.count(table) == 0


@pytest.mark.parametrize("table", ALL_USER_TABLES)
def test_anon_cannot_update_or_delete(matrix: Matrix, table: str) -> None:
    rows = matrix.rows_for(table, matrix.athlete)
    matrix.become("anon")
    touch = sql.SQL("update public.{} set user_id = user_id where user_id = %s").format(
        _table(table)
    )
    remove = sql.SQL("delete from public.{} where user_id = %s").format(_table(table))
    assert matrix.attempt(touch, (matrix.athlete.user_id,)) == 0
    assert matrix.attempt(remove, (matrix.athlete.user_id,)) == 0

    assert matrix.rows_for(table, matrix.athlete) == rows


@pytest.mark.parametrize("table", OWNER_MANAGED_TABLES)
def test_anon_cannot_insert(matrix: Matrix, table: str) -> None:
    statement, params = _INSERTS[table](matrix.athlete.user_id, matrix.athlete, 5)
    matrix.become("anon")
    outcome = matrix.attempt(statement, params)
    assert _is_rls_denial(outcome), outcome


# ── Service role: bypasses RLS for backend CRUD ──────────────────────────────


@pytest.mark.parametrize("table", ALL_USER_TABLES)
def test_service_role_reads_every_athletes_rows(matrix: Matrix, table: str) -> None:
    matrix.become("service_role")
    assert {matrix.athlete.user_id, matrix.other.user_id} <= matrix.owners(table)


@pytest.mark.parametrize("table", OWNER_MANAGED_TABLES)
def test_service_role_writes_any_athletes_rows(matrix: Matrix, table: str) -> None:
    statement, params = _INSERTS[table](matrix.other.user_id, matrix.other, 6)
    touch = sql.SQL("update public.{} set user_id = user_id where user_id = %s").format(
        _table(table)
    )
    other_rows = matrix.rows_for(table, matrix.other)

    matrix.become("service_role")
    assert matrix.attempt(touch, (matrix.other.user_id,)) == other_rows
    if table not in _ONE_ROW_PER_ATHLETE:
        assert matrix.attempt(statement, params) == 1
