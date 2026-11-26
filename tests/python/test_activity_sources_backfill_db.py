"""Live-database regressions for activity_sources resumable backfill (issue #533, PR B)."""

import os
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, LiteralString, cast

import psycopg
import pytest
from psycopg import errors as pg_errors

from tests.python.local_db import direct_session, local_db_url

_RUN_DB_TESTS = os.environ.get("RUN_DB_TESTS") == "1"

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(
        not _RUN_DB_TESTS,
        reason="RUN_DB_TESTS=1 not provided",
    ),
]

_BASE_CREATED_AT = datetime(2026, 7, 4, 12, 0, tzinfo=UTC)


def _reset_progress(cursor: Any) -> None:
    cursor.execute(
        """
        update public.activity_sources_backfill_progress
        set
          last_created_at = null,
          last_id = null,
          preflight_at = null,
          processed_rows = 0,
          drained_at = null,
          clean_passes = 0,
          completed_at = null
        where singleton
        """
    )


def _fetchone_required(cursor: Any) -> tuple[Any, ...]:
    row = cursor.fetchone()
    assert row is not None
    return tuple(row)


def _progress(cursor: Any) -> dict[str, Any]:
    cursor.execute(
        """
        select to_jsonb(p)
        from public.activity_sources_backfill_progress p
        where singleton
        """
    )
    return _fetchone_required(cursor)[0]


def _preflight(cursor: Any) -> dict[str, Any]:
    cursor.execute("select public.activity_sources_backfill_preflight()")
    return _fetchone_required(cursor)[0]


def _batch(cursor: Any, batch_size: int) -> dict[str, Any]:
    cursor.execute("select public.activity_sources_backfill_batch(%s)", (batch_size,))
    return _fetchone_required(cursor)[0]


def _verify(cursor: Any) -> dict[str, Any]:
    cursor.execute("select public.activity_sources_backfill_verify()")
    return _fetchone_required(cursor)[0]


def _insert_athlete(cursor: Any, user_id: str) -> None:
    cursor.execute("insert into public.athlete_profiles (user_id) values (%s)", (user_id,))


def _insert_activity(
    cursor: Any,
    *,
    user_id: str,
    activity_id: str,
    source: str = "fit_upload",
    source_file_key: str | None = None,
    sport: str = "cycling",
    created_at: datetime,
    started_at: datetime | None = _BASE_CREATED_AT,
    duration_seconds: int | None = 3600,
    distance_meters: float | None = 30000,
    athlete_notes: str | None = None,
    with_bootstrap_marker: bool = True,
) -> None:
    if with_bootstrap_marker:
        cursor.execute("select set_config('app.activity_source_bootstrap', 'on', true)")
    cursor.execute(
        """
        insert into public.activities (
          id,
          user_id,
          sport,
          activity_date,
          started_at,
          duration_seconds,
          distance_meters,
          athlete_notes,
          source,
          source_file_key,
          created_at
        )
        values (%s, %s, %s, '2026-07-04', %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            activity_id,
            user_id,
            sport,
            started_at,
            duration_seconds,
            distance_meters,
            athlete_notes,
            source,
            source_file_key,
            created_at,
        ),
    )
    if with_bootstrap_marker:
        cursor.execute("select set_config('app.activity_source_bootstrap', 'off', true)")


def _insert_bootstrap_source(cursor: Any, activity_id: str) -> None:
    cursor.execute(
        """
        insert into public.activity_sources
        select s.*
        from public.activities a
        cross join lateral public.activity_bootstrap_source(a) s
        where a.id = %s
        """,
        (activity_id,),
    )


def _source_row(cursor: Any, activity_id: str) -> dict[str, Any] | None:
    cursor.execute(
        "select to_jsonb(s) from public.activity_sources s where s.id = %s",
        (activity_id,),
    )
    row = cursor.fetchone()
    return None if row is None else row[0]


def _source_count(cursor: Any, user_id: str) -> int:
    cursor.execute("select count(*) from public.activity_sources where user_id = %s", (user_id,))
    return int(_fetchone_required(cursor)[0])


def _snapshot_progress_state(cursor: Any) -> tuple[Any, ...]:
    cursor.execute(
        """
        select
          last_created_at,
          last_id,
          preflight_at,
          processed_rows,
          drained_at,
          clean_passes,
          completed_at
        from public.activity_sources_backfill_progress
        where singleton
        """
    )
    return _fetchone_required(cursor)


def _restore_progress_state(cursor: Any, snapshot: tuple[Any, ...]) -> None:
    cursor.execute(
        """
        update public.activity_sources_backfill_progress
        set
          last_created_at = %s,
          last_id = %s,
          preflight_at = %s,
          processed_rows = %s,
          drained_at = %s,
          clean_passes = %s,
          completed_at = %s
        where singleton
        """,
        snapshot,
    )


def _cleanup_committed_fixture(*, user_id: str, progress_snapshot: tuple[Any, ...]) -> None:
    with psycopg.connect(local_db_url(), autocommit=False) as cleanup:
        cursor = cleanup.cursor()
        _restore_progress_state(cursor, progress_snapshot)
        cursor.execute("delete from public.athlete_profiles where user_id = %s", (user_id,))
        cleanup.commit()


def test_backfill_batches_resume_after_committed_interruption() -> None:
    user_id = str(uuid.uuid4())
    ids = [str(uuid.uuid4()) for _ in range(3)]
    progress_snapshot: tuple[Any, ...] | None = None

    try:
        with psycopg.connect(local_db_url(), autocommit=False) as first:
            cursor = first.cursor()
            progress_snapshot = _snapshot_progress_state(cursor)
            _reset_progress(cursor)
            _insert_athlete(cursor, user_id)

            for idx, activity_id in enumerate(ids):
                _insert_activity(
                    cursor,
                    user_id=user_id,
                    activity_id=activity_id,
                    source_file_key=f"uploads/{user_id}/{idx}.fit",
                    created_at=_BASE_CREATED_AT + timedelta(seconds=idx),
                )

            _preflight(cursor)
            first_batch = _batch(cursor, 2)
            assert first_batch["processed"] == 2
            assert first_batch["drained"] is False
            assert first_batch["last_id"] == ids[1]
            first.commit()

        with psycopg.connect(local_db_url(), autocommit=False) as resumed:
            cursor = resumed.cursor()
            # Fresh connection sees the committed checkpoint and resumes strictly after it.
            assert _source_count(cursor, user_id) == 2

            resumed_batch = _batch(cursor, 50)
            assert resumed_batch["processed"] == 1
            assert resumed_batch["drained"] is False
            assert resumed_batch["last_id"] == ids[2]

            drained_batch = _batch(cursor, 50)
            assert drained_batch["processed"] == 0
            assert drained_batch["drained"] is True
            assert drained_batch["last_id"] == ids[2]

            progress = _progress(cursor)
            assert progress["processed_rows"] == 3
            assert progress["drained_at"] is not None
            resumed.commit()
    finally:
        if progress_snapshot is not None:
            _cleanup_committed_fixture(user_id=user_id, progress_snapshot=progress_snapshot)


def test_backfill_rolls_back_sources_when_checkpoint_update_fails() -> None:
    user_id = str(uuid.uuid4())
    activity_id = str(uuid.uuid4())

    with direct_session() as session:
        cursor = session.cursor
        _reset_progress(cursor)
        _insert_athlete(cursor, user_id)
        _insert_activity(
            cursor,
            user_id=user_id,
            activity_id=activity_id,
            source_file_key=f"uploads/{user_id}/rollback.fit",
            created_at=_BASE_CREATED_AT,
        )
        _preflight(cursor)

        cursor.execute(
            """
            create function public.__test_backfill_progress_fail()
            returns trigger
            language plpgsql
            set search_path = ''
            as $$
            begin
              raise exception 'forced checkpoint failure';
            end;
            $$
            """
        )
        cursor.execute(
            """
            create trigger __test_backfill_progress_fail
            before update on public.activity_sources_backfill_progress
            for each row execute function public.__test_backfill_progress_fail()
            """
        )

        cursor.execute("savepoint before_batch")
        with pytest.raises(pg_errors.RaiseException, match="forced checkpoint failure"):
            _batch(cursor, 10)
        cursor.execute("rollback to savepoint before_batch")

        cursor.execute(
            "drop trigger __test_backfill_progress_fail"
            " on public.activity_sources_backfill_progress"
        )
        cursor.execute("drop function public.__test_backfill_progress_fail()")

        assert _source_row(cursor, activity_id) is None
        progress = _progress(cursor)
        assert progress["last_id"] is None
        assert progress["processed_rows"] == 0


def test_verify_rewinds_to_earliest_hole_and_requires_two_clean_passes() -> None:
    user_id = str(uuid.uuid4())
    ids = [str(uuid.uuid4()) for _ in range(3)]

    with direct_session() as session:
        cursor = session.cursor
        _reset_progress(cursor)
        _insert_athlete(cursor, user_id)

        for idx, activity_id in enumerate(ids):
            _insert_activity(
                cursor,
                user_id=user_id,
                activity_id=activity_id,
                source_file_key=f"uploads/{user_id}/hole-{idx}.fit",
                created_at=_BASE_CREATED_AT + timedelta(seconds=idx),
            )

        # Create a single hole at ids[1].
        _insert_bootstrap_source(cursor, ids[0])
        _insert_bootstrap_source(cursor, ids[2])

        cursor.execute(
            """
            update public.activity_sources_backfill_progress
            set
              preflight_at = clock_timestamp(),
              last_created_at = %s,
              last_id = %s,
              processed_rows = 3,
              drained_at = clock_timestamp(),
              clean_passes = 1,
              completed_at = clock_timestamp()
            where singleton
            """,
            (_BASE_CREATED_AT + timedelta(seconds=2), ids[2]),
        )

        rewind = _verify(cursor)
        assert rewind["holes"] == 1
        assert rewind["drained"] is False
        assert rewind["clean_passes"] == 0
        assert rewind["complete"] is False

        rewound_progress = _progress(cursor)
        assert rewound_progress["last_id"] == ids[0]

        replay = _batch(cursor, 10)
        assert replay["processed"] == 2
        drained = _batch(cursor, 10)
        assert drained["processed"] == 0
        assert drained["drained"] is True

        first_clean_pass = _verify(cursor)
        assert first_clean_pass["holes"] == 0
        assert first_clean_pass["live_source_errors"] == 0
        assert first_clean_pass["capture_errors"] == 0
        assert first_clean_pass["clean_passes"] == 1
        assert first_clean_pass["complete"] is False

        second_clean_pass = _verify(cursor)
        assert second_clean_pass["clean_passes"] == 2
        assert second_clean_pass["complete"] is True


def test_preflight_rejects_unknown_source_when_source_check_was_bypassed() -> None:
    user_id = str(uuid.uuid4())
    activity_id = str(uuid.uuid4())

    with direct_session() as session:
        cursor = session.cursor
        _reset_progress(cursor)
        _insert_athlete(cursor, user_id)
        cursor.execute("alter table public.activities drop constraint activities_source_check")

        _insert_activity(
            cursor,
            user_id=user_id,
            activity_id=activity_id,
            source="file_upload",
            source_file_key=f"uploads/{user_id}/legacy.bin",
            created_at=_BASE_CREATED_AT,
        )

        cursor.execute("savepoint before_preflight")
        with pytest.raises(pg_errors.InvalidParameterValue, match="No activity source mapping"):
            _preflight(cursor)
        cursor.execute("rollback to savepoint before_preflight")

        assert _progress(cursor)["preflight_at"] is None


def test_preflight_rejects_malformed_intervals_source_key() -> None:
    user_id = str(uuid.uuid4())

    with direct_session() as session:
        cursor = session.cursor
        _reset_progress(cursor)
        _insert_athlete(cursor, user_id)
        _insert_activity(
            cursor,
            user_id=user_id,
            activity_id=str(uuid.uuid4()),
            source="intervals_sync",
            source_file_key="not-an-intervals-key",
            created_at=_BASE_CREATED_AT,
        )

        with pytest.raises(pg_errors.InvalidParameterValue, match="Malformed intervals"):
            _preflight(cursor)


def test_preflight_rejects_duplicate_intervals_external_ids_per_user() -> None:
    user_id = str(uuid.uuid4())

    with direct_session() as session:
        cursor = session.cursor
        _reset_progress(cursor)
        _insert_athlete(cursor, user_id)
        cursor.execute(
            """
            alter table public.activities
            drop constraint activities_intervals_source_file_key_unique
            """
        )

        for idx in range(2):
            _insert_activity(
                cursor,
                user_id=user_id,
                activity_id=str(uuid.uuid4()),
                source="intervals_sync",
                source_file_key="intervals:dup",
                created_at=_BASE_CREATED_AT + timedelta(seconds=idx),
            )

        with pytest.raises(pg_errors.InvalidParameterValue, match="Duplicate Intervals"):
            _preflight(cursor)


def test_backfill_keeps_sparse_rows_with_null_payload_fingerprint() -> None:
    user_id = str(uuid.uuid4())
    without_start = str(uuid.uuid4())
    without_positive_metric = str(uuid.uuid4())
    positive = str(uuid.uuid4())

    with direct_session() as session:
        cursor = session.cursor
        _reset_progress(cursor)
        _insert_athlete(cursor, user_id)

        _insert_activity(
            cursor,
            user_id=user_id,
            activity_id=without_start,
            source_file_key=f"uploads/{user_id}/nostart.fit",
            started_at=None,
            created_at=_BASE_CREATED_AT,
        )
        _insert_activity(
            cursor,
            user_id=user_id,
            activity_id=without_positive_metric,
            source_file_key=f"uploads/{user_id}/nopositive.fit",
            duration_seconds=0,
            distance_meters=0,
            created_at=_BASE_CREATED_AT + timedelta(seconds=1),
        )
        _insert_activity(
            cursor,
            user_id=user_id,
            activity_id=positive,
            source_file_key=f"uploads/{user_id}/positive.fit",
            duration_seconds=120,
            distance_meters=500,
            created_at=_BASE_CREATED_AT + timedelta(seconds=2),
        )

        _preflight(cursor)
        _batch(cursor, 10)

        first = _source_row(cursor, without_start)
        second = _source_row(cursor, without_positive_metric)
        third = _source_row(cursor, positive)
        assert first is not None and second is not None and third is not None
        assert first["payload_fingerprint"] is None
        assert second["payload_fingerprint"] is None
        assert third["payload_fingerprint"] is not None


def test_backfill_aborts_on_mismatched_existing_deterministic_source() -> None:
    user_id = str(uuid.uuid4())
    activity_id = str(uuid.uuid4())

    with direct_session() as session:
        cursor = session.cursor
        _reset_progress(cursor)
        _insert_athlete(cursor, user_id)
        _insert_activity(
            cursor,
            user_id=user_id,
            activity_id=activity_id,
            source_file_key=f"uploads/{user_id}/conflict.fit",
            created_at=_BASE_CREATED_AT,
        )

        cursor.execute(
            """
            insert into public.activity_sources (
              id,
              user_id,
              activity_id,
              origin_activity_id,
              provider,
              ingest_format,
              object_key,
              fields,
              recorded_at
            )
            values (%s, %s, %s, %s, 'garmin', 'fit', %s, '{}'::jsonb, null)
            """,
            (
                activity_id,
                user_id,
                activity_id,
                activity_id,
                f"uploads/{user_id}/conflict.fit",
            ),
        )

        _preflight(cursor)

        cursor.execute("savepoint before_conflict")
        with pytest.raises(pg_errors.InvalidParameterValue, match="Backfill source conflict"):
            _batch(cursor, 10)
        cursor.execute("rollback to savepoint before_conflict")

        progress = _progress(cursor)
        assert progress["processed_rows"] == 0
        assert progress["last_id"] is None


def test_backfill_rejects_shadow_captured_row_if_projection_changed_before_scan() -> None:
    user_id = str(uuid.uuid4())
    activity_id = str(uuid.uuid4())

    with direct_session() as session:
        cursor = session.cursor
        _reset_progress(cursor)
        _insert_athlete(cursor, user_id)
        _insert_activity(
            cursor,
            user_id=user_id,
            activity_id=activity_id,
            source_file_key=f"uploads/{user_id}/shadow.fit",
            created_at=_BASE_CREATED_AT,
            with_bootstrap_marker=False,
        )

        source = _source_row(cursor, activity_id)
        assert source is not None
        assert source["fields"]["athlete_notes"] is None

        cursor.execute(
            """
            update public.activities
            set athlete_notes = 'edited before backfill scan'
            where id = %s
            """,
            (activity_id,),
        )

        _preflight(cursor)

        # PR B explicitly requires conflicting with already-captured deterministic rows when
        # a later projection edit is observed before this batch scans the row.
        cursor.execute("savepoint before_shadow_conflict")
        with pytest.raises(pg_errors.InvalidParameterValue, match="Backfill source conflict"):
            _batch(cursor, 10)
        cursor.execute("rollback to savepoint before_shadow_conflict")

        progress = _progress(cursor)
        assert progress["processed_rows"] == 0
        assert progress["last_id"] is None


def test_verify_uses_captured_source_version_under_projection_drift() -> None:
    user_id = str(uuid.uuid4())
    activity_id = str(uuid.uuid4())

    with direct_session() as session:
        cursor = session.cursor
        _reset_progress(cursor)
        _insert_athlete(cursor, user_id)
        _insert_activity(
            cursor,
            user_id=user_id,
            activity_id=activity_id,
            source_file_key=f"uploads/{user_id}/drift.fit",
            created_at=_BASE_CREATED_AT,
        )
        _insert_bootstrap_source(cursor, activity_id)

        cursor.execute(
            """
            update public.activities
            set
              athlete_notes = 'edited after source capture',
              duration_seconds = 999
            where id = %s
            """,
            (activity_id,),
        )
        cursor.execute(
            """
            update public.activity_sources_backfill_progress
            set
              preflight_at = clock_timestamp(),
              drained_at = clock_timestamp(),
              clean_passes = 0,
              completed_at = null
            where singleton
            """
        )

        verify = _verify(cursor)
        assert verify["holes"] == 0
        assert verify["live_source_errors"] == 0
        assert verify["capture_errors"] == 0

        source = _source_row(cursor, activity_id)
        assert source is not None
        assert source["fields"]["duration_seconds"] == 3600
        assert source["fields"]["athlete_notes"] is None


@pytest.mark.parametrize(
    "call",
    [
        "select public.activity_sources_backfill_preflight()",
        "select public.activity_sources_backfill_batch(1)",
        "select public.activity_sources_backfill_verify()",
    ],
)
@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_backfill_rpcs_are_not_executable_by_browser_roles(role: str, call: str) -> None:
    with direct_session(role=role) as session, pytest.raises(pg_errors.InsufficientPrivilege):
        # `call` comes from the literal parametrize list above, never from input.
        session.cursor.execute(cast(LiteralString, call))


def test_service_role_can_execute_backfill_rpcs() -> None:
    with direct_session(role="service_role") as session:
        preflight = _preflight(session.cursor)
        batch = _batch(session.cursor, 1)
        verify = _verify(session.cursor)

    assert preflight["ok"] is True
    assert "processed" in batch
    assert "holes" in verify


def _create_committed_activity_fixture(
    *,
    user_id: str,
    activity_id: str,
    source_file_key: str,
) -> tuple[Any, ...]:
    with psycopg.connect(local_db_url(), autocommit=False) as setup:
        cursor = setup.cursor()
        progress_snapshot = _snapshot_progress_state(cursor)
        _reset_progress(cursor)
        _insert_athlete(cursor, user_id)
        _insert_activity(
            cursor,
            user_id=user_id,
            activity_id=activity_id,
            source_file_key=source_file_key,
            created_at=_BASE_CREATED_AT,
        )
        setup.commit()
    return progress_snapshot


def _run_batch_once(
    *,
    state: dict[str, Any],
    pid_ready: threading.Event,
    batch_size: int,
) -> None:
    try:
        with psycopg.connect(local_db_url(), autocommit=False) as conn:
            cursor = conn.cursor()
            cursor.execute("select pg_backend_pid()")
            state["pid"] = int(_fetchone_required(cursor)[0])
            pid_ready.set()
            _preflight(cursor)
            state["result"] = _batch(cursor, batch_size)
            conn.commit()
    except BaseException as exc:  # pragma: no cover - surfaced by assertion below
        state["error"] = exc
        pid_ready.set()


def _run_batch_with_insert_gate(
    *,
    activity_id: str,
    lock_key: int,
    function_name: str,
    trigger_name: str,
    state: dict[str, Any],
    pid_ready: threading.Event,
    timing: Literal["before", "after"] = "before",
) -> None:
    # "before" parks the batch after its row lock but before the source row is written;
    # "after" parks it once the source row exists but before the checkpoint update.
    try:
        with psycopg.connect(local_db_url(), autocommit=False) as conn:
            cursor = conn.cursor()
            cursor.execute("select pg_backend_pid()")
            state["pid"] = int(_fetchone_required(cursor)[0])
            pid_ready.set()
            _preflight(cursor)

            create_function_sql = cast(
                LiteralString,
                f"""
                create function public.{function_name}()
                returns trigger
                language plpgsql
                set search_path = ''
                as $$
                begin
                  perform pg_advisory_lock({lock_key});
                  perform pg_advisory_unlock({lock_key});
                  return new;
                end;
                $$
                """,
            )
            create_trigger_sql = cast(
                LiteralString,
                f"""
                create trigger {trigger_name}
                {timing} insert on public.activity_sources
                for each row
                when (new.id = '{activity_id}'::uuid)
                execute function public.{function_name}()
                """,
            )
            drop_trigger_sql = cast(
                LiteralString,
                f"drop trigger {trigger_name} on public.activity_sources",
            )
            drop_function_sql = cast(LiteralString, f"drop function public.{function_name}()")

            cursor.execute(create_function_sql)
            cursor.execute(create_trigger_sql)
            state["result"] = _batch(cursor, 1)
            cursor.execute(drop_trigger_sql)
            cursor.execute(drop_function_sql)
            conn.commit()
    except BaseException as exc:  # pragma: no cover - surfaced by assertion below
        state["error"] = exc
        pid_ready.set()


def _run_update_and_commit(
    *,
    activity_id: str,
    athlete_notes: str,
    state: dict[str, Any],
    pid_ready: threading.Event,
    finished: threading.Event,
) -> None:
    try:
        with psycopg.connect(local_db_url(), autocommit=False) as conn:
            cursor = conn.cursor()
            cursor.execute("select pg_backend_pid()")
            state["pid"] = int(_fetchone_required(cursor)[0])
            pid_ready.set()
            cursor.execute(
                "update public.activities set athlete_notes = %s where id = %s",
                (athlete_notes, activity_id),
            )
            conn.commit()
    except BaseException as exc:  # pragma: no cover - surfaced by assertion below
        state["error"] = exc
        pid_ready.set()
    finally:
        finished.set()


def _wait_for_blocker(*, blocked_pid: int, blocker_pid: int, timeout_seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    with psycopg.connect(local_db_url(), autocommit=True) as conn:
        cursor = conn.cursor()
        while time.monotonic() < deadline:
            cursor.execute("select pg_blocking_pids(%s)", (blocked_pid,))
            blockers = _fetchone_required(cursor)[0] or []
            if blocker_pid in blockers:
                return True
            time.sleep(0.05)
    return False


def _batch_holds_activity_row_lock(activity_id: str, *, timeout_seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        with direct_session() as probe:
            probe.cursor.execute("set local lock_timeout = '120ms'")
            try:
                probe.cursor.execute(
                    "update public.activities set athlete_notes = athlete_notes where id = %s",
                    (activity_id,),
                )
            except pg_errors.LockNotAvailable:
                return True
        time.sleep(0.05)
    return False


def test_legacy_update_committed_before_batch_lock_is_captured() -> None:
    user_id = str(uuid.uuid4())
    activity_id = str(uuid.uuid4())
    progress_snapshot = _create_committed_activity_fixture(
        user_id=user_id,
        activity_id=activity_id,
        source_file_key=f"uploads/{user_id}/ordering.fit",
    )

    updater_conn = psycopg.connect(local_db_url(), autocommit=False)
    updater_pid: int | None = None
    batch_state: dict[str, Any] = {}
    batch_pid_ready = threading.Event()

    try:
        with updater_conn.cursor() as updater_cursor:
            updater_cursor.execute("select pg_backend_pid()")
            updater_pid = int(_fetchone_required(updater_cursor)[0])
            updater_cursor.execute(
                "update public.activities set athlete_notes = 'updated before lock' where id = %s",
                (activity_id,),
            )

        batch_thread = threading.Thread(
            target=_run_batch_once,
            kwargs={"state": batch_state, "pid_ready": batch_pid_ready, "batch_size": 1},
            daemon=True,
        )
        batch_thread.start()
        assert batch_pid_ready.wait(timeout=5), "batch backend pid was never published"
        assert batch_state.get("error") is None, batch_state.get("error")

        batch_pid_value = batch_state.get("pid")
        assert isinstance(batch_pid_value, int)
        batch_pid = batch_pid_value
        assert updater_pid is not None
        assert _wait_for_blocker(
            blocked_pid=batch_pid,
            blocker_pid=updater_pid,
        ), "batch never blocked on updater row lock"

        updater_conn.commit()
        batch_thread.join(timeout=5)

        assert batch_state.get("error") is None, batch_state.get("error")
        source = None
        with psycopg.connect(local_db_url(), autocommit=False) as check:
            source = _source_row(check.cursor(), activity_id)
        assert source is not None
        assert source["fields"]["athlete_notes"] == "updated before lock"
    finally:
        updater_conn.close()
        _cleanup_committed_fixture(user_id=user_id, progress_snapshot=progress_snapshot)


def _coordinate_after_lock_race(
    *,
    activity_id: str,
    lock_key: int,
    function_name: str,
    trigger_name: str,
) -> None:
    batch_state: dict[str, Any] = {}
    batch_pid_ready = threading.Event()
    update_state: dict[str, Any] = {}
    update_pid_ready = threading.Event()
    update_finished = threading.Event()

    with psycopg.connect(local_db_url(), autocommit=False) as coordinator:
        with coordinator.cursor() as cursor:
            cursor.execute("select pg_advisory_lock(%s)", (lock_key,))

        batch_thread = threading.Thread(
            target=_run_batch_with_insert_gate,
            kwargs={
                "activity_id": activity_id,
                "lock_key": lock_key,
                "function_name": function_name,
                "trigger_name": trigger_name,
                "state": batch_state,
                "pid_ready": batch_pid_ready,
            },
            daemon=True,
        )
        batch_thread.start()
        assert batch_pid_ready.wait(timeout=5), "batch backend pid was never published"
        assert _batch_holds_activity_row_lock(activity_id), "batch never held activity row lock"

        update_thread = threading.Thread(
            target=_run_update_and_commit,
            kwargs={
                "activity_id": activity_id,
                "athlete_notes": "updated after lock",
                "state": update_state,
                "pid_ready": update_pid_ready,
                "finished": update_finished,
            },
            daemon=True,
        )
        update_thread.start()
        assert update_pid_ready.wait(timeout=5), "update backend pid was never published"

        assert batch_state.get("error") is None, batch_state.get("error")
        assert update_state.get("error") is None, update_state.get("error")

        batch_pid_value = batch_state.get("pid")
        updater_pid_value = update_state.get("pid")
        assert isinstance(batch_pid_value, int)
        assert isinstance(updater_pid_value, int)
        assert _wait_for_blocker(
            blocked_pid=updater_pid_value,
            blocker_pid=batch_pid_value,
        ), "updater never blocked on batch row lock"

        with coordinator.cursor() as cursor:
            cursor.execute("select pg_advisory_unlock(%s)", (lock_key,))
        coordinator.commit()

    batch_thread.join(timeout=5)
    update_thread.join(timeout=5)

    assert batch_state.get("error") is None, batch_state.get("error")
    assert update_state.get("error") is None, update_state.get("error")
    assert update_finished.is_set(), "legacy update did not complete"


def test_legacy_update_waits_for_batch_row_lock_and_lands_after_capture() -> None:
    user_id = str(uuid.uuid4())
    activity_id = str(uuid.uuid4())
    lock_key = int(uuid.uuid4().int % 1_000_000_000)
    suffix = uuid.uuid4().hex[:8]
    function_name = f"__test_backfill_wait_{suffix}"
    trigger_name = f"__test_backfill_wait_{suffix}"

    progress_snapshot = _create_committed_activity_fixture(
        user_id=user_id,
        activity_id=activity_id,
        source_file_key=f"uploads/{user_id}/race.fit",
    )

    try:
        _coordinate_after_lock_race(
            activity_id=activity_id,
            lock_key=lock_key,
            function_name=function_name,
            trigger_name=trigger_name,
        )

        with psycopg.connect(local_db_url(), autocommit=False) as check:
            cursor = check.cursor()
            source = _source_row(cursor, activity_id)
            assert source is not None
            assert source["fields"]["athlete_notes"] is None
            cursor.execute(
                "select athlete_notes from public.activities where id = %s",
                (activity_id,),
            )
            assert cursor.fetchone() == ("updated after lock",)
            verify = _verify(cursor)
            assert verify["capture_errors"] == 0
    finally:
        _cleanup_committed_fixture(user_id=user_id, progress_snapshot=progress_snapshot)


@pytest.mark.parametrize("source_file_key", [None, "intervals:"])
def test_preflight_rejects_missing_or_empty_intervals_identity(
    source_file_key: str | None,
) -> None:
    user_id = str(uuid.uuid4())

    with direct_session() as session:
        cursor = session.cursor
        _reset_progress(cursor)
        _insert_athlete(cursor, user_id)
        _insert_activity(
            cursor,
            user_id=user_id,
            activity_id=str(uuid.uuid4()),
            source="intervals_sync",
            source_file_key=source_file_key,
            created_at=_BASE_CREATED_AT,
        )

        cursor.execute("savepoint before_preflight")
        with pytest.raises(pg_errors.InvalidParameterValue, match="Malformed intervals"):
            _preflight(cursor)
        cursor.execute("rollback to savepoint before_preflight")
        assert _progress(cursor)["preflight_at"] is None


def test_backfill_captures_payload_fingerprint_collisions_without_rejecting() -> None:
    # A fingerprint is a candidate-matching hint, never a rejection key: two distinct
    # activities with the same sport/start/duration/distance must both get a live source.
    user_id = str(uuid.uuid4())
    ids = [str(uuid.uuid4()) for _ in range(2)]

    with direct_session() as session:
        cursor = session.cursor
        _reset_progress(cursor)
        _insert_athlete(cursor, user_id)
        for idx, activity_id in enumerate(ids):
            _insert_activity(
                cursor,
                user_id=user_id,
                activity_id=activity_id,
                source_file_key=f"uploads/{user_id}/same-ride-{idx}.fit",
                created_at=_BASE_CREATED_AT + timedelta(seconds=idx),
            )

        _preflight(cursor)
        assert _batch(cursor, 10)["processed"] == 2

        sources = [_source_row(cursor, activity_id) for activity_id in ids]
        assert all(source is not None for source in sources)
        fingerprints = {source["payload_fingerprint"] for source in sources if source}
        assert len(fingerprints) == 1 and None not in fingerprints


def test_same_intervals_id_for_different_users_is_not_a_collision() -> None:
    # Known-provider external ids are unique per user, not globally.
    user_ids = [str(uuid.uuid4()) for _ in range(2)]
    ids = [str(uuid.uuid4()) for _ in range(2)]

    with direct_session() as session:
        cursor = session.cursor
        _reset_progress(cursor)
        for idx, (user_id, activity_id) in enumerate(zip(user_ids, ids, strict=True)):
            _insert_athlete(cursor, user_id)
            _insert_activity(
                cursor,
                user_id=user_id,
                activity_id=activity_id,
                source="intervals_sync",
                source_file_key="intervals:i42",
                created_at=_BASE_CREATED_AT + timedelta(seconds=idx),
            )

        _preflight(cursor)
        assert _batch(cursor, 10)["processed"] == 2
        for user_id, activity_id in zip(user_ids, ids, strict=True):
            source = _source_row(cursor, activity_id)
            assert source is not None
            assert source["user_id"] == user_id
            assert source["external_id"] == "i42"


def test_backend_killed_after_insert_before_checkpoint_rolls_back_and_resumes() -> None:
    # A real interruption, not a simulated error: the batch backend is terminated while it
    # is parked inside the source INSERT (after the row lock, before the checkpoint update).
    user_id = str(uuid.uuid4())
    activity_id = str(uuid.uuid4())
    lock_key = int(uuid.uuid4().int % 1_000_000_000)
    suffix = uuid.uuid4().hex[:8]
    function_name = f"__test_backfill_kill_{suffix}"
    trigger_name = f"__test_backfill_kill_{suffix}"

    progress_snapshot = _create_committed_activity_fixture(
        user_id=user_id,
        activity_id=activity_id,
        source_file_key=f"uploads/{user_id}/killed.fit",
    )
    batch_state: dict[str, Any] = {}
    batch_pid_ready = threading.Event()

    try:
        with psycopg.connect(local_db_url(), autocommit=False) as coordinator:
            with coordinator.cursor() as cursor:
                cursor.execute("select pg_advisory_lock(%s)", (lock_key,))

            batch_thread = threading.Thread(
                target=_run_batch_with_insert_gate,
                kwargs={
                    "activity_id": activity_id,
                    "lock_key": lock_key,
                    "function_name": function_name,
                    "trigger_name": trigger_name,
                    "state": batch_state,
                    "pid_ready": batch_pid_ready,
                    "timing": "after",
                },
                daemon=True,
            )
            batch_thread.start()
            assert batch_pid_ready.wait(timeout=5), "batch backend pid was never published"
            assert _batch_holds_activity_row_lock(activity_id), "batch never held row lock"

            batch_pid = batch_state.get("pid")
            assert isinstance(batch_pid, int)
            with coordinator.cursor() as cursor:
                cursor.execute("select pg_backend_pid()")
                coordinator_pid = int(_fetchone_required(cursor)[0])
            # The row-lock probe alone does not prove the gate was reached: require the batch
            # to be parked on the coordinator's advisory lock inside the AFTER INSERT trigger,
            # i.e. with its source row written and the checkpoint not yet updated.
            assert _wait_for_blocker(blocked_pid=batch_pid, blocker_pid=coordinator_pid), (
                "batch never reached the post-insert gate"
            )
            with coordinator.cursor() as cursor:
                cursor.execute("select pg_terminate_backend(%s)", (batch_pid,))
                assert _fetchone_required(cursor)[0] is True
                cursor.execute("select pg_advisory_unlock(%s)", (lock_key,))
            coordinator.commit()

        batch_thread.join(timeout=5)
        assert isinstance(batch_state.get("error"), psycopg.OperationalError)

        with psycopg.connect(local_db_url(), autocommit=False) as resumed:
            cursor = resumed.cursor()
            # The killed transaction took its source, its gate trigger, and its checkpoint
            # with it: nothing partial is left behind.
            assert _source_row(cursor, activity_id) is None
            progress = _progress(cursor)
            assert progress["last_id"] is None
            assert progress["processed_rows"] == 0

            _preflight(cursor)
            assert _batch(cursor, 10)["processed"] >= 1
            while not _batch(cursor, 10)["drained"]:
                pass
            assert _source_row(cursor, activity_id) is not None
            first = _verify(cursor)
            second = _verify(cursor)
            assert first["holes"] == 0 and first["live_source_errors"] == 0
            assert second["complete"] is True
            resumed.commit()
    finally:
        _cleanup_committed_fixture(user_id=user_id, progress_snapshot=progress_snapshot)


def test_rerun_after_completion_aborts_atomically_on_edited_shadow_row() -> None:
    # Both hosted environments completed with rows created after the checkpoint that the
    # shadow writer captured. Re-running `run` re-scans them; if one has since had a
    # projection edit, the batch must fail closed without touching completion or cursor.
    user_id = str(uuid.uuid4())
    completed_id = str(uuid.uuid4())
    shadow_id = str(uuid.uuid4())

    with direct_session() as session:
        cursor = session.cursor
        _reset_progress(cursor)
        _insert_athlete(cursor, user_id)
        _insert_activity(
            cursor,
            user_id=user_id,
            activity_id=completed_id,
            source_file_key=f"uploads/{user_id}/before.fit",
            created_at=_BASE_CREATED_AT,
        )
        _preflight(cursor)
        _batch(cursor, 10)
        assert _batch(cursor, 10)["drained"] is True
        _verify(cursor)
        assert _verify(cursor)["complete"] is True
        completed = _progress(cursor)

        # Shadow-captured after completion (no marker: the legacy trigger captures it), then
        # edited, so its captured fields legitimately differ from today's projection.
        _insert_activity(
            cursor,
            user_id=user_id,
            activity_id=shadow_id,
            source_file_key=f"uploads/{user_id}/after.fit",
            created_at=_BASE_CREATED_AT + timedelta(seconds=1),
            with_bootstrap_marker=False,
        )
        cursor.execute(
            "update public.activities set rpe = 7 where id = %s",
            (shadow_id,),
        )

        cursor.execute("savepoint before_rerun")
        with pytest.raises(pg_errors.InvalidParameterValue, match="Backfill source conflict"):
            _batch(cursor, 10)
        cursor.execute("rollback to savepoint before_rerun")
        assert _progress(cursor) == completed

        # The read-only status the runbook prescribes for completed environments sees the
        # same row as healthy: captured fields round-trip, so drift is not a capture error.
        cursor.execute(
            """
            select count(*) from public.activities a
            left join public.activity_sources s on s.id = a.id where s.id is null
            """
        )
        assert _fetchone_required(cursor)[0] == 0
        assert _verify(cursor)["capture_errors"] == 0
