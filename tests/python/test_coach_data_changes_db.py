"""Transactional notice/receipt regressions (#591); local-only and always rolled back.

RUN_DB_TESTS=1 uv run pytest -m db tests/python/test_coach_data_changes_db.py
The fixture can exercise the additive migration on an existing local stack without resetting it.
"""

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any, LiteralString, cast
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from tests.python.local_db import direct_session

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(os.environ.get("RUN_DB_TESTS") != "1", reason="Local DB tests are opt-in"),
]
MIGRATION = Path(__file__).parents[2] / "supabase/migrations/20261118061831_coach_data_changes.sql"


@pytest.fixture()
def db() -> Iterator[psycopg.Cursor]:
    with direct_session() as session:
        cursor = session.cursor
        cursor.execute("select to_regclass('public.coach_data_changes')")
        if cursor.fetchone() == (None,):
            # Reviewed, checked-in SQL only; never an external or athlete-supplied string.
            cursor.execute(
                cast(
                    LiteralString,
                    (
                        MIGRATION.parent / "20261101120000_recovery_logs_source_free_form.sql"
                    ).read_text(),
                )
            )
            cursor.execute(cast(LiteralString, MIGRATION.read_text()))
        yield cursor


def _athlete(db: psycopg.Cursor) -> str:
    user = str(uuid4())
    db.execute("insert into public.athlete_profiles (user_id) values (%s)", (user,))
    return user


def _thread(db: psycopg.Cursor, user: str) -> str:
    db.execute(
        "insert into public.chat_threads (id, user_id) values (%s, %s) returning id",
        (str(uuid4()), user),
    )
    row = db.fetchone()
    assert row is not None
    return str(row[0])


def _ride(db: psycopg.Cursor, user: str) -> str:
    db.execute(
        "insert into public.activities (user_id, sport, activity_date, source)"
        " values (%s, 'cycling', '2026-11-09', 'fit_upload') returning id",
        (user,),
    )
    row = db.fetchone()
    assert row is not None
    return str(row[0])


def _snapshot(db: psycopg.Cursor, user: str, thread: str | None = None) -> dict[str, Any]:
    db.execute("select public.get_coach_data_changes(%s, %s)", (user, thread))
    row = db.fetchone()
    assert row is not None
    return row[0]


def _reply(
    db: psycopg.Cursor,
    user: str,
    thread: str,
    events: list[str],
    *,
    role: str = "assistant",
    finish: str = "stop",
) -> None:
    db.execute(
        "insert into public.chat_messages (id, user_id, thread_id, role, parts, metadata)"
        " values (%s, %s, %s, %s, '[]', %s)",
        (
            str(uuid4()),
            user,
            thread,
            role,
            Jsonb({"finish_reason": finish, "data_change_event_ids": events}),
        ),
    )


def test_committed_records_are_coalesced_without_chat_messages(db: psycopg.Cursor) -> None:
    user = _athlete(db)
    for _ in range(3):
        _ride(db, user)
    snapshot = _snapshot(db, user)
    assert len(snapshot["event_ids"]) == 3
    assert snapshot["summaries"] == [
        {
            "data_type": "activity",
            "source": "fit_upload",
            "start_date": "2026-11-09",
            "end_date": "2026-11-09",
            "created_records": 3,
            "updated_records": 0,
        }
    ]
    db.execute("select count(*) from public.chat_messages where user_id = %s", (user,))
    assert db.fetchone() == (0,)


def test_only_successful_same_thread_assistant_replies_acknowledge(db: psycopg.Cursor) -> None:
    user, other = _athlete(db), _athlete(db)
    thread, other_thread = _thread(db, user), _thread(db, other)
    _ride(db, user)
    events = _snapshot(db, user, thread)["event_ids"]
    _reply(db, user, thread, events, role="user")
    _reply(db, user, thread, events, finish="error")
    _reply(db, other, other_thread, events)
    assert _snapshot(db, user, thread)["event_ids"] == events
    assert _snapshot(db, other, other_thread)["event_ids"] == []
    _reply(db, user, thread, events)
    _reply(db, user, thread, events)  # replaying a receipt is harmless
    assert _snapshot(db, user, thread)["event_ids"] == []
    # No thread or a different thread must not inherit this conversation's receipt.
    assert _snapshot(db, user)["event_ids"] == events
    assert _snapshot(db, user, other_thread)["event_ids"] == events


def test_import_during_turn_stays_pending_and_compaction_cannot_erase_receipt(
    db: psycopg.Cursor,
) -> None:
    user = _athlete(db)
    thread = _thread(db, user)
    _ride(db, user)
    before = _snapshot(db, user, thread)
    _ride(db, user)
    _reply(db, user, thread, before["event_ids"])
    db.execute(
        "insert into public.chat_model_states (thread_id, user_id, items) values (%s,%s,'[]')",
        (thread, user),
    )
    after = _snapshot(db, user, thread)
    assert len(after["event_ids"]) == 1
    assert set(after["event_ids"]).isdisjoint(before["event_ids"])
    db.execute("update public.chat_model_states set items = '[]' where user_id = %s", (user,))
    assert _snapshot(db, user, thread) == after


def test_rollback_or_failed_member_cannot_publish_success(db: psycopg.Cursor) -> None:
    user = _athlete(db)
    _ride(db, user)  # successful member of a partial batch
    before = _snapshot(db, user)
    db.execute("savepoint rejected_member")
    _ride(db, user)
    db.execute("rollback to savepoint rejected_member")
    assert _snapshot(db, user) == before
    with pytest.raises(psycopg.errors.CheckViolation), db.connection.transaction():
        db.execute(
            "insert into public.activities (user_id, sport, activity_date, source)"
            " values (%s, 'cycling', '2026-11-10', 'invalid_source')",
            (user,),
        )
    assert _snapshot(db, user) == before


def test_wellness_upserts_ignore_legacy_id_churn_but_capture_changed_values(
    db: psycopg.Cursor,
) -> None:
    user = _athlete(db)
    thread = _thread(db, user)
    db.execute(
        "insert into public.recovery_logs (user_id, log_date, source, sleep_score)"
        " values (%s, '2026-11-09', 'unknown_device', 80)",
        (user,),
    )
    before = _snapshot(db, user, thread)
    assert before["summaries"][0]["source"] == "other"
    _reply(db, user, thread, before["event_ids"])
    db.execute(
        "update public.recovery_logs set id = gen_random_uuid(), sleep_score = 80"
        " where user_id = %s",
        (user,),
    )
    assert _snapshot(db, user, thread)["event_ids"] == []
    db.execute("update public.recovery_logs set sleep_score = 82 where user_id = %s", (user,))
    after = _snapshot(db, user, thread)
    assert len(after["event_ids"]) == 1
    assert after["summaries"][0]["created_records"] == 0
    assert after["summaries"][0]["updated_records"] == 1
    assert "sleep_score" not in str(after)


def test_activity_updates_are_not_counted_as_new_workouts(db: psycopg.Cursor) -> None:
    user = _athlete(db)
    thread = _thread(db, user)
    ride = _ride(db, user)
    db.execute("update public.activities set distance_meters = 1000 where id = %s", (ride,))
    before = _snapshot(db, user, thread)
    assert len(before["event_ids"]) == 2
    assert before["summaries"][0]["created_records"] == 1
    assert before["summaries"][0]["updated_records"] == 0
    _reply(db, user, thread, before["event_ids"])
    db.execute("update public.activities set distance_meters = 2000 where id = %s", (ride,))
    after = _snapshot(db, user, thread)
    assert after["summaries"][0]["created_records"] == 0
    assert after["summaries"][0]["updated_records"] == 1


def test_paging_does_not_acknowledge_unseen_events(db: psycopg.Cursor) -> None:
    user = _athlete(db)
    thread = _thread(db, user)
    db.execute(
        "insert into public.activities (user_id, sport, activity_date, source)"
        " select %s, 'cycling', '2026-11-09', 'manual' from generate_series(1,257)",
        (user,),
    )
    first = _snapshot(db, user, thread)
    assert len(first["event_ids"]) == 256
    assert first["has_more"] is True
    _reply(db, user, thread, first["event_ids"])
    second = _snapshot(db, user, thread)
    assert len(second["event_ids"]) == 1
    assert second["has_more"] is False


def test_matching_updates_do_not_create_another_import_notice(db: psycopg.Cursor) -> None:
    user = _athlete(db)
    ride = _ride(db, user)
    before = _snapshot(db, user)
    db.execute(
        "update public.activities set activity_summary = '{\"updated\":true}',"
        " source_count = source_count + 1 where id = %s",
        (ride,),
    )
    assert _snapshot(db, user) == before


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_change_log_and_reader_are_server_only(db: psycopg.Cursor, role: str) -> None:
    db.execute(
        "select has_table_privilege(%s, 'public.coach_data_changes', 'SELECT'),"
        " has_function_privilege(%s, 'public.get_coach_data_changes(text,uuid)', 'EXECUTE')",
        (role, role),
    )
    assert db.fetchone() == (False, False)
