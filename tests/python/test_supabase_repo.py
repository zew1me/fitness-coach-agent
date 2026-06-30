import re
from datetime import UTC, date, datetime, timedelta

import pytest

from backend.models.athlete import AthleteProfile, SportThreshold
from backend.models.chat import ChatModelStateReplaceRequest
from backend.models.training import Activity
from backend.repos.supabase_repo import (
    RecordNotFoundError,
    RepositoryNotConfiguredError,
    SupabaseRepository,
)


class FakeResponse:
    def __init__(self, data: list[dict[str, object]]) -> None:
        self.data = data


class FakeTableQuery:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows
        self._filters: dict[str, object] = {}
        self._is_null: set[str] = set()
        self._inserted_payload: dict[str, object] | None = None
        self._inserted_payloads: list[dict[str, object]] | None = None
        self._upserted_payloads: list[dict[str, object]] | None = None
        self._upsert_conflict: str | None = None
        self._ignore_duplicates = False
        self._update_payload: dict[str, object] | None = None
        self._limit: int | None = None
        self._in_filters: dict[str, set[object]] = {}
        self._gt_filters: dict[str, object] = {}
        self._cursor_before: tuple[str, str] | None = None
        self._orders: list[tuple[str, bool]] = []

    def select(self, *_columns: str) -> "FakeTableQuery":
        return self

    def eq(self, column: str, value: object) -> "FakeTableQuery":
        self._filters[column] = value
        return self

    def in_(self, column: str, values: list[object]) -> "FakeTableQuery":
        self._in_filters[column] = set(values)
        return self

    def gt(self, column: str, value: object) -> "FakeTableQuery":
        self._gt_filters[column] = value
        return self

    def is_(self, column: str, value: object) -> "FakeTableQuery":
        assert value == "null"
        self._is_null.add(column)
        return self

    def or_(self, expression: str) -> "FakeTableQuery":
        match = re.fullmatch(
            r"created_at\.lt\.(.+),and\(created_at\.eq\.(.+),id\.lt\.(.+)\)", expression
        )
        assert match is not None and match.group(1) == match.group(2)
        self._cursor_before = (match.group(1), match.group(3))
        return self

    def order(self, column: str, *, desc: bool = False) -> "FakeTableQuery":
        self._orders.append((column, desc))
        return self

    def limit(self, count: int) -> "FakeTableQuery":
        self._limit = count
        return self

    def insert(self, payload: dict[str, object] | list[dict[str, object]]) -> "FakeTableQuery":
        if isinstance(payload, list):
            self._inserted_payloads = payload
        else:
            self._inserted_payload = payload
        return self

    def upsert(
        self,
        payload: dict[str, object] | list[dict[str, object]],
        on_conflict: str,
        *,
        ignore_duplicates: bool = False,
    ) -> "FakeTableQuery":
        assert on_conflict
        self._upserted_payloads = payload if isinstance(payload, list) else [payload]
        self._upsert_conflict = on_conflict
        self._ignore_duplicates = ignore_duplicates
        return self

    def update(self, payload: dict[str, object]) -> "FakeTableQuery":
        self._update_payload = payload
        return self

    def execute(self) -> FakeResponse:  # noqa: C901
        if self._inserted_payload is not None:
            self._rows.append(self._inserted_payload)
            return FakeResponse([self._inserted_payload])
        if self._inserted_payloads is not None:
            self._rows.extend(self._inserted_payloads)
            return FakeResponse(self._inserted_payloads)
        if self._upserted_payloads is not None:
            conflict_columns = [
                column.strip()
                for column in (self._upsert_conflict or "").split(",")
                if column.strip()
            ]
            assert conflict_columns
            upserted_rows: list[dict[str, object]] = []
            for payload in self._upserted_payloads:
                for index, row in enumerate(self._rows):
                    if any(row.get(column) != payload.get(column) for column in conflict_columns):
                        continue
                    if self._ignore_duplicates:
                        break
                    merged = {**row, **payload}
                    self._rows[index] = merged
                    upserted_rows.append(merged)
                    break
                else:
                    self._rows.append(payload)
                    upserted_rows.append(payload)
            return FakeResponse(upserted_rows)
        if self._update_payload is not None:
            updated = []
            for row in self._matching_rows():
                row.update(self._update_payload)
                updated.append(row)
            return FakeResponse(updated)

        rows = self._matching_rows()
        for column, desc in reversed(self._orders):
            rows.sort(key=lambda row: row.get(column), reverse=desc)
        if self._limit is not None:
            rows = rows[: self._limit]
        return FakeResponse(rows)

    def _matching_rows(self) -> list[dict[str, object]]:
        return [
            row
            for row in self._rows
            if all(row.get(column) == value for column, value in self._filters.items())
            and all(row.get(column) in values for column, values in self._in_filters.items())
            and all(row.get(column) is None for column in self._is_null)
            and all(
                row.get(column) is not None and str(row[column]) > str(value)
                for column, value in self._gt_filters.items()
            )
            and (
                self._cursor_before is None
                or str(row.get("created_at")) < self._cursor_before[0]
                or (
                    str(row.get("created_at")) == self._cursor_before[0]
                    and str(row.get("id")) < self._cursor_before[1]
                )
            )
        ]


class FakeSupabaseClient:
    def __init__(
        self,
        *,
        athlete_rows: list[dict[str, object]] | None = None,
        threshold_rows: list[dict[str, object]] | None = None,
        activity_rows: list[dict[str, object]] | None = None,
        chat_thread_rows: list[dict[str, object]] | None = None,
        chat_message_rows: list[dict[str, object]] | None = None,
        chat_attachment_rows: list[dict[str, object]] | None = None,
        chat_model_state_rows: list[dict[str, object]] | None = None,
        daily_load_snapshot_rows: list[dict[str, object]] | None = None,
        goal_rows: list[dict[str, object]] | None = None,
    ) -> None:
        self._tables = {
            "athlete_profiles": FakeTableQuery(athlete_rows or []),
            "sport_thresholds": FakeTableQuery(threshold_rows or []),
            "activities": FakeTableQuery(activity_rows or []),
            "daily_load_snapshots": FakeTableQuery(daily_load_snapshot_rows or []),
            "chat_threads": FakeTableQuery(chat_thread_rows or []),
            "chat_messages": FakeTableQuery(chat_message_rows or []),
            "chat_attachments": FakeTableQuery(chat_attachment_rows or []),
            "chat_model_states": FakeTableQuery(chat_model_state_rows or []),
            "goals": FakeTableQuery(goal_rows or []),
        }

    def table(self, table_name: str) -> FakeTableQuery:
        # The real Supabase client returns a fresh query builder for each call.
        return FakeTableQuery(self._tables[table_name]._rows)


def test_fake_table_upsert_replaces_existing_conflict_row() -> None:
    rows: list[dict[str, object]] = [
        {"external_id": "activity-1", "name": "Old"},
        {"external_id": "activity-2", "name": "Keep"},
    ]

    response = (
        FakeTableQuery(rows)
        .upsert(
            {"external_id": "activity-1", "name": "New"},
            on_conflict="external_id",
        )
        .execute()
    )

    assert response.data == [{"external_id": "activity-1", "name": "New"}]
    assert rows == [
        {"external_id": "activity-1", "name": "New"},
        {"external_id": "activity-2", "name": "Keep"},
    ]


def test_fake_table_upsert_matches_composite_conflict_key() -> None:
    rows: list[dict[str, object]] = [
        {"user_id": "athlete-1", "snapshot_date": "2026-06-28", "sport": "run", "ctl": 10},
        {"user_id": "athlete-1", "snapshot_date": "2026-06-28", "sport": "bike", "ctl": 20},
    ]

    response = (
        FakeTableQuery(rows)
        .upsert(
            {
                "user_id": "athlete-1",
                "snapshot_date": "2026-06-28",
                "sport": "run",
                "ctl": 11,
            },
            on_conflict="user_id,snapshot_date,sport",
        )
        .execute()
    )

    assert response.data == [
        {"user_id": "athlete-1", "snapshot_date": "2026-06-28", "sport": "run", "ctl": 11}
    ]
    assert rows == [
        {"user_id": "athlete-1", "snapshot_date": "2026-06-28", "sport": "run", "ctl": 11},
        {"user_id": "athlete-1", "snapshot_date": "2026-06-28", "sport": "bike", "ctl": 20},
    ]


def test_fake_table_upsert_handles_batch_payloads() -> None:
    rows: list[dict[str, object]] = [
        {"user_id": "athlete-1", "snapshot_date": "2026-06-28", "sport": "run", "ctl": 10},
    ]

    response = (
        FakeTableQuery(rows)
        .upsert(
            [
                {
                    "user_id": "athlete-1",
                    "snapshot_date": "2026-06-28",
                    "sport": "run",
                    "ctl": 11,
                },
                {
                    "user_id": "athlete-1",
                    "snapshot_date": "2026-06-29",
                    "sport": "run",
                    "ctl": 12,
                },
            ],
            on_conflict="user_id,snapshot_date,sport",
        )
        .execute()
    )

    assert response.data == [
        {"user_id": "athlete-1", "snapshot_date": "2026-06-28", "sport": "run", "ctl": 11},
        {"user_id": "athlete-1", "snapshot_date": "2026-06-29", "sport": "run", "ctl": 12},
    ]
    assert rows == response.data


@pytest.mark.asyncio
async def test_upsert_load_snapshots_handles_batch_payloads() -> None:
    client = FakeSupabaseClient()
    repo = SupabaseRepository(client=client)

    await repo.upsert_load_snapshots(
        "athlete-1",
        [
            {"snapshot_date": date(2026, 6, 28), "daily_tss": 50, "ctl": 10, "atl": 12, "tsb": -2},
            {"snapshot_date": date(2026, 6, 29), "daily_tss": 60, "ctl": 11, "atl": 13, "tsb": -2},
        ],
        sport="cycling",
    )

    rows = client._tables["daily_load_snapshots"]._rows
    assert len(rows) == 2
    assert [row["snapshot_date"] for row in rows] == ["2026-06-28", "2026-06-29"]
    assert all(row["user_id"] == "athlete-1" for row in rows)
    assert all(row["sport"] == "cycling" for row in rows)


@pytest.mark.asyncio
async def test_get_athlete_profile_reads_supabase_row() -> None:
    repo = SupabaseRepository(
        client=FakeSupabaseClient(
            athlete_rows=[
                {
                    "user_id": "athlete-1",
                    "display_name": "Athlete One",
                    "primary_sports": ["running"],
                    "weekly_available_hours": 6.5,
                    "coaching_state": "active",
                }
            ]
        )
    )

    profile = await repo.get_athlete_profile("athlete-1")

    assert profile.user_id == "athlete-1"
    assert profile.primary_sports == ["running"]
    assert profile.weekly_available_hours == 6.5


@pytest.mark.asyncio
async def test_get_athlete_profile_allows_null_onboarding_collected_values() -> None:
    repo = SupabaseRepository(
        client=FakeSupabaseClient(
            athlete_rows=[
                {
                    "user_id": "athlete-1",
                    "onboarding_collected": {"nutrition": None},
                    "coaching_state": "onboarding",
                }
            ]
        )
    )

    profile = await repo.get_athlete_profile("athlete-1")

    assert profile.onboarding_collected == {"nutrition": None}


@pytest.mark.asyncio
async def test_get_athlete_profile_raises_for_missing_row() -> None:
    repo = SupabaseRepository(client=FakeSupabaseClient())

    with pytest.raises(RecordNotFoundError):
        await repo.get_athlete_profile("missing-user")


@pytest.mark.asyncio
async def test_upsert_athlete_profile_persists_new_profile_shape() -> None:
    repo = SupabaseRepository(client=FakeSupabaseClient())

    profile = await repo.upsert_athlete_profile(
        AthleteProfile(
            user_id="athlete-1",
            display_name="Athlete One",
            birth_date=date(1990, 4, 1),
            primary_sports=["cycling", "running"],
            constraints=["No Wednesdays"],
        )
    )

    assert profile.user_id == "athlete-1"
    assert profile.birth_date == date(1990, 4, 1)
    assert profile.primary_sports == ["cycling", "running"]


@pytest.mark.asyncio
async def test_upsert_sport_threshold_supersedes_active_threshold() -> None:
    client = FakeSupabaseClient(
        threshold_rows=[
            {
                "id": "old-threshold",
                "user_id": "athlete-1",
                "sport": "cycling",
                "lt2_power_watts": 240,
                "zones": [],
                "estimation_method": "manual",
                "confidence": "medium",
                "effective_from": "2026-01-01",
                "superseded_at": None,
            }
        ]
    )
    repo = SupabaseRepository(client=client)

    threshold = await repo.upsert_sport_threshold(
        SportThreshold(
            user_id="athlete-1",
            sport="cycling",
            lt2_power_watts=260,
            zones=[{"zone": 4, "power_low": 237}],
            confidence="high",
        )
    )

    assert threshold.user_id == "athlete-1"
    assert threshold.lt2_power_watts == 260
    assert threshold.id is not None


@pytest.mark.asyncio
async def test_create_activity_persists_structured_activity() -> None:
    repo = SupabaseRepository(client=FakeSupabaseClient())

    activity = await repo.create_activity(
        Activity(
            user_id="athlete-1",
            sport="running",
            activity_date=date(2026, 4, 1),
            duration_seconds=3600,
            distance_meters=10_000,
            tss=75.5,
            fueling_notes="Took one gel at 30 minutes",
            source="manual",
            activity_summary={
                "schema": "activity_summary_v1",
                "load": {"primary_load": 75.5},
            },
        )
    )

    assert activity.user_id == "athlete-1"
    assert activity.sport == "running"
    assert activity.tss == 75.5
    assert activity.fueling_notes == "Took one gel at 30 minutes"
    assert activity.activity_summary["load"]["primary_load"] == 75.5
    assert activity.id is not None


@pytest.mark.asyncio
async def test_create_activity_builds_summary_when_activity_has_default_summary() -> None:
    repo = SupabaseRepository(client=FakeSupabaseClient())

    activity = await repo.create_activity(
        Activity(
            user_id="athlete-1",
            sport="running",
            activity_date=date(2026, 4, 1),
            duration_seconds=3600,
            distance_meters=10_000,
            avg_hr_bpm=145,
            source="gpx_upload",
            raw_extraction={"rr_interval_count": 12},
        )
    )

    assert activity.summary_schema_version == 1
    assert activity.activity_summary["schema"] == "activity_summary_v1"
    assert activity.activity_summary["session"]["sport"] == "running"
    assert activity.activity_summary["session"]["duration_moving_s"] == 3600
    assert activity.activity_summary["heart_rate"]["avg_bpm"] == 145
    assert activity.activity_summary["data_quality"]["has_gps"] is True
    assert activity.activity_summary["data_quality"]["has_rr_intervals"] is True


@pytest.mark.asyncio
async def test_upsert_athlete_profile_persists_dietary_restrictions() -> None:
    repo = SupabaseRepository(client=FakeSupabaseClient())

    profile = await repo.upsert_athlete_profile(
        AthleteProfile(
            user_id="athlete-2",
            dietary_restrictions=["vegetarian", "lactose intolerant"],
            nutrition_notes="Prefers gels over real food during races",
        )
    )

    assert profile.dietary_restrictions == ["vegetarian", "lactose intolerant"]
    assert profile.nutrition_notes == "Prefers gels over real food during races"


@pytest.mark.asyncio
async def test_update_athlete_profile_fields_allows_nutrition_fields() -> None:
    repo = SupabaseRepository(client=FakeSupabaseClient())

    profile = await repo.update_athlete_profile_fields(
        "athlete-3",
        {
            "dietary_restrictions": ["vegan"],
            "nutrition_notes": "Whole food plant-based",
        },
    )

    assert profile.dietary_restrictions == ["vegan"]
    assert profile.nutrition_notes == "Whole food plant-based"


@pytest.mark.asyncio
async def test_update_athlete_profile_fields_normalizes_not_provided_hormone_status() -> None:
    repo = SupabaseRepository(client=FakeSupabaseClient())

    profile = await repo.update_athlete_profile_fields(
        "athlete-4",
        {
            "dietary_restrictions": ["mostly vegetarian with some seafood"],
            "hormone_status": "not_provided",
            "nutrition_notes": "mostly vegetarian with some seafood",
        },
    )

    assert profile.hormone_status == "not_specified"
    assert profile.dietary_restrictions == ["mostly vegetarian with some seafood"]
    assert profile.nutrition_notes == "mostly vegetarian with some seafood"


@pytest.mark.asyncio
async def test_update_athlete_profile_fields_drops_unknown_optional_profile_enums() -> None:
    repo = SupabaseRepository(client=FakeSupabaseClient())

    profile = await repo.update_athlete_profile_fields(
        "athlete-5",
        {
            "hormone_status": "irrelevant",
            "nutrition_notes": "Still save the valid sibling field",
        },
    )

    assert profile.hormone_status is None
    assert profile.nutrition_notes == "Still save the valid sibling field"


@pytest.mark.asyncio
async def test_update_athlete_profile_fields_drops_unknown_threshold_sources() -> None:
    repo = SupabaseRepository(client=FakeSupabaseClient())

    profile = await repo.update_athlete_profile_fields(
        "athlete-6",
        {
            "max_hr_source": "watch_guess",
            "weight_source": "scale",
            "nutrition_notes": "Still save the valid sibling field",
        },
    )

    assert profile.max_hr_source is None
    assert profile.weight_source is None
    assert profile.nutrition_notes == "Still save the valid sibling field"


@pytest.mark.asyncio
async def test_create_goal_persists_row_with_generated_id() -> None:
    from backend.models.training import Goal

    client = FakeSupabaseClient()
    repo = SupabaseRepository(client=client)

    created = await repo.create_goal(
        Goal(user_id="athlete-1", goal_type="event", title="A race", sport="cycling")
    )

    assert created.id is not None
    assert created.user_id == "athlete-1"
    assert created.title == "A race"


@pytest.mark.asyncio
async def test_get_goal_is_scoped_to_owning_user() -> None:
    client = FakeSupabaseClient(
        goal_rows=[
            {
                "id": "goal-1",
                "user_id": "athlete-1",
                "goal_type": "event",
                "title": "Other athlete goal",
            },
            {
                "id": "goal-1",
                "user_id": "athlete-2",
                "goal_type": "event",
                "title": "Own goal",
                "course_profile": {"terrain": "trail"},
            },
        ]
    )
    repo = SupabaseRepository(client=client)

    goal = await repo.get_goal("goal-1", "athlete-2")

    assert goal.user_id == "athlete-2"
    assert goal.title == "Own goal"
    assert goal.course_profile == {"terrain": "trail"}


@pytest.mark.asyncio
async def test_update_goal_is_scoped_to_owning_user() -> None:
    """A caller cannot mutate another athlete's goal by passing its id."""
    from backend.models.training import Goal

    client = FakeSupabaseClient(
        goal_rows=[
            {
                "id": "goal-1",
                "user_id": "athlete-2",
                "goal_type": "event",
                "title": "Someone else's race",
                "status": "active",
            }
        ]
    )
    repo = SupabaseRepository(client=client)

    with pytest.raises(RecordNotFoundError):
        await repo.update_goal("goal-1", "athlete-1", {"status": "abandoned"})

    # The owner can update it, and only the provided field changes.
    updated = await repo.update_goal("goal-1", "athlete-2", {"status": "completed"})
    assert isinstance(updated, Goal)
    assert updated.status == "completed"
    assert updated.title == "Someone else's race"


@pytest.mark.asyncio
async def test_repository_requires_supabase_configuration() -> None:
    repo = SupabaseRepository(client=None)
    repo._client = None

    with pytest.raises(RepositoryNotConfiguredError):
        await repo.get_athlete_profile("athlete-1")


@pytest.mark.asyncio
async def test_create_chat_message_honors_caller_message_id() -> None:
    client = FakeSupabaseClient()
    repo = SupabaseRepository(client=client)
    message_id = "63ff9606-9158-43d7-a82b-d31ef9788b7d"

    message = await repo.create_chat_message(
        thread_id="thread-1",
        user_id="athlete-1",
        role="user",
        parts=[{"type": "text", "text": "I train ~8 hours/week"}],
        message_id=message_id,
    )

    assert message.id == message_id


@pytest.mark.asyncio
async def test_create_chat_message_generates_uuid_when_message_id_omitted() -> None:
    client = FakeSupabaseClient()
    repo = SupabaseRepository(client=client)

    message = await repo.create_chat_message(
        thread_id="thread-1",
        user_id="athlete-1",
        role="assistant",
        parts=[{"type": "text", "text": "Welcome."}],
    )

    assert re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        message.id,
    )


@pytest.mark.asyncio
async def test_create_chat_message_persists_json_attachments_with_honored_message_id() -> None:
    client = FakeSupabaseClient()
    repo = SupabaseRepository(client=client)
    message_id = "63ff9606-9158-43d7-a82b-d31ef9788b7d"

    message = await repo.create_chat_message(
        thread_id="thread-1",
        user_id="athlete-1",
        role="user",
        parts=[{"type": "text", "text": "Here's my workout chart"}],
        message_id=message_id,
        attachments=[
            {
                "type": "file",
                "mediaType": "image/png",
                "filename": "chart.png",
                "url": "https://example.com/chart.png",
            }
        ],
    )

    assert message.id == message_id
    assert message.attachments == [
        {
            "type": "file",
            "mediaType": "image/png",
            "filename": "chart.png",
            "url": "https://example.com/chart.png",
        }
    ]


@pytest.mark.asyncio
async def test_create_chat_message_is_idempotent_for_caller_message_id() -> None:
    message_id = "63ff9606-9158-43d7-a82b-d31ef9788b7d"
    existing: dict[str, object] = {
        "id": message_id,
        "thread_id": "thread-1",
        "user_id": "athlete-1",
        "role": "user",
        "content": "Existing",
        "parts": [{"type": "text", "text": "Existing"}],
        "attachments": [],
        "metadata": {},
        "created_at": "2026-06-20T12:00:00+00:00",
    }
    client = FakeSupabaseClient(chat_message_rows=[existing])
    repo = SupabaseRepository(client=client)

    message = await repo.create_chat_message(
        thread_id="thread-1",
        user_id="athlete-1",
        role="user",
        parts=[{"type": "text", "text": "Retry"}],
        message_id=message_id,
    )

    assert message.content == "Existing"
    assert len(client._tables["chat_messages"]._rows) == 1


@pytest.mark.asyncio
async def test_chat_message_pagination_is_stable_for_equal_timestamps() -> None:
    created_at = "2026-06-20T12:00:00+00:00"
    message_ids = (
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
        "00000000-0000-0000-0000-000000000003",
    )
    rows: list[dict[str, object]] = [
        {
            "id": message_id,
            "thread_id": "thread-1",
            "user_id": "athlete-1",
            "role": "user",
            "content": message_id,
            "parts": [{"type": "text", "text": message_id}],
            "attachments": [],
            "metadata": {},
            "created_at": created_at,
        }
        for message_id in message_ids
    ]
    repo = SupabaseRepository(client=FakeSupabaseClient(chat_message_rows=rows))

    newest = await repo.list_chat_messages("thread-1", limit=2)
    older = await repo.list_chat_messages(
        "thread-1",
        limit=2,
        before=(newest[0].created_at, newest[0].id),
    )

    assert [message.id for message in newest] == [message_ids[1], message_ids[2]]
    assert [message.id for message in older] == [message_ids[0]]


@pytest.mark.asyncio
async def test_chat_message_pagination_rejects_non_uuid_cursor_id() -> None:
    repo = SupabaseRepository(client=FakeSupabaseClient())

    with pytest.raises(ValueError, match="Invalid chat message cursor"):
        await repo.list_chat_messages(
            "thread-1",
            before=(datetime(2026, 6, 20, 12, tzinfo=UTC), "not,a,uuid"),
        )


@pytest.mark.asyncio
async def test_chat_model_state_compare_and_swap_preserves_transcript() -> None:
    now = datetime.now(UTC)
    state_row: dict[str, object] = {
        "thread_id": "thread-1",
        "user_id": "athlete-1",
        "items": [{"role": "user", "content": "old"}],
        "coaching_memory": [],
        "compaction_metadata": {},
        "schema_version": 1,
        "version": 3,
        "lease_id": "lease-1",
        "lease_expires_at": (now + timedelta(minutes=5)).isoformat(),
        "created_at": "2026-06-20T12:00:00+00:00",
        "updated_at": "2026-06-20T12:00:00+00:00",
    }
    transcript: list[dict[str, object]] = [{"id": "message-1", "thread_id": "thread-1"}]
    client = FakeSupabaseClient(
        chat_model_state_rows=[state_row],
        chat_message_rows=transcript,
    )
    repo = SupabaseRepository(client=client)

    updated = await repo.replace_chat_model_state(
        thread_id="thread-1",
        user_id="athlete-1",
        replacement=ChatModelStateReplaceRequest(
            expected_version=3,
            lease_id="lease-1",
            items=[{"role": "user", "content": "compacted"}],
            coaching_memory=[],
            compaction_metadata={"trigger": "token_threshold"},
        ),
    )

    assert updated.version == 4
    assert updated.items == [{"role": "user", "content": "compacted"}]
    assert client._tables["chat_messages"]._rows == transcript


@pytest.mark.asyncio
async def test_chat_model_state_initialization_recovers_from_concurrent_insert() -> None:
    now = datetime.now(UTC).isoformat()
    concurrent_row: dict[str, object] = {
        "thread_id": "thread-1",
        "user_id": "athlete-1",
        "items": [{"role": "user", "content": "concurrent"}],
        "coaching_memory": [],
        "compaction_metadata": {},
        "schema_version": 1,
        "version": 1,
        "lease_id": None,
        "lease_expires_at": None,
        "created_at": now,
        "updated_at": now,
    }

    class ConcurrentQuery:
        def __init__(self, client: "ConcurrentClient") -> None:
            self.client = client
            self.operation = "select"

        def select(self, *_columns: str) -> "ConcurrentQuery":
            return self

        def eq(self, *_args: object) -> "ConcurrentQuery":
            return self

        def upsert(self, *_args: object, **_kwargs: object) -> "ConcurrentQuery":
            self.operation = "upsert"
            return self

        def execute(self) -> FakeResponse:
            if self.operation == "upsert":
                self.client.inserted = True
                return FakeResponse([])
            return FakeResponse([concurrent_row] if self.client.inserted else [])

    class ConcurrentClient:
        inserted = False

        def table(self, table_name: str) -> ConcurrentQuery:
            assert table_name == "chat_model_states"
            return ConcurrentQuery(self)

    state = await SupabaseRepository(client=ConcurrentClient()).get_or_create_chat_model_state(
        thread_id="thread-1", user_id="athlete-1"
    )

    assert state.version == 1
    assert state.items == [{"role": "user", "content": "concurrent"}]


@pytest.mark.asyncio
async def test_chat_model_state_replace_rejects_non_owner_lease() -> None:
    now = datetime.now(UTC)
    original_items = [{"role": "user", "content": "owned"}]
    client = FakeSupabaseClient(
        chat_model_state_rows=[
            {
                "thread_id": "thread-1",
                "user_id": "athlete-1",
                "items": original_items,
                "coaching_memory": [],
                "compaction_metadata": {},
                "schema_version": 1,
                "version": 3,
                "lease_id": "lease-owner",
                "lease_expires_at": (now + timedelta(minutes=5)).isoformat(),
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }
        ]
    )
    repo = SupabaseRepository(client=client)

    with pytest.raises(ValueError, match="lease or version conflict"):
        await repo.replace_chat_model_state(
            thread_id="thread-1",
            user_id="athlete-1",
            replacement=ChatModelStateReplaceRequest(
                expected_version=3,
                lease_id="other-lease",
                items=[{"role": "user", "content": "intruder"}],
                coaching_memory=[],
                compaction_metadata={},
            ),
        )

    assert client._tables["chat_model_states"]._rows[0]["items"] == original_items


@pytest.mark.asyncio
async def test_chat_model_state_rejects_stale_version() -> None:
    now = datetime.now(UTC)
    client = FakeSupabaseClient(
        chat_model_state_rows=[
            {
                "thread_id": "thread-1",
                "user_id": "athlete-1",
                "items": [],
                "coaching_memory": [],
                "compaction_metadata": {},
                "schema_version": 1,
                "version": 4,
                "lease_id": "lease-1",
                "lease_expires_at": (now + timedelta(minutes=5)).isoformat(),
                "created_at": "2026-06-20T12:00:00+00:00",
                "updated_at": "2026-06-20T12:00:00+00:00",
            }
        ]
    )
    repo = SupabaseRepository(client=client)

    with pytest.raises(ValueError, match="lease or version conflict"):
        await repo.replace_chat_model_state(
            thread_id="thread-1",
            user_id="athlete-1",
            replacement=ChatModelStateReplaceRequest(
                expected_version=3,
                lease_id="lease-1",
                items=[],
                coaching_memory=[],
                compaction_metadata={},
            ),
        )


@pytest.mark.asyncio
async def test_chat_turn_lease_rejects_active_owner_and_allows_expired_lease() -> None:
    now = datetime.now(UTC)
    client = FakeSupabaseClient(
        chat_model_state_rows=[
            {
                "thread_id": "thread-1",
                "user_id": "athlete-1",
                "items": [],
                "coaching_memory": [],
                "compaction_metadata": {},
                "schema_version": 1,
                "version": 1,
                "lease_id": "old-lease",
                "lease_expires_at": (now - timedelta(seconds=1)).isoformat(),
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }
        ]
    )
    repo = SupabaseRepository(client=client)

    leased = await repo.acquire_chat_turn_lease(
        thread_id="thread-1",
        user_id="athlete-1",
        lease_id="new-lease",
        ttl_seconds=60,
    )
    retried = await repo.acquire_chat_turn_lease(
        thread_id="thread-1",
        user_id="athlete-1",
        lease_id="new-lease",
        ttl_seconds=60,
    )

    assert leased.lease_id == "new-lease"
    assert retried.version == leased.version
    with pytest.raises(ValueError, match="already in progress"):
        await repo.acquire_chat_turn_lease(
            thread_id="thread-1",
            user_id="athlete-1",
            lease_id="other-lease",
            ttl_seconds=60,
        )


def test_athlete_profile_specialization_pct_defaults_to_none() -> None:
    """Regression guard: default must be None, not 80 (issue #254).

    If this fails someone reverted int|None=None back to int=80 in athlete.py.
    """
    profile = AthleteProfile(user_id="x")
    assert profile.specialization_pct is None


def test_onboarding_collected_null_values_are_preserved() -> None:
    """Legacy DB rows with null section flags must not raise ValidationError."""
    profile = AthleteProfile.model_validate(
        {
            "user_id": "u1",
            "onboarding_collected": {"nutrition": None, "goals": True},
        }
    )
    assert profile.onboarding_collected == {"nutrition": None, "goals": True}


def test_onboarding_collected_missing_or_non_dict_coerced_to_empty() -> None:
    """A missing or non-dict value must fall back to an empty dict, not crash."""
    for bad_value in (None, "yes", [], 42):
        profile = AthleteProfile.model_validate(
            {"user_id": "u1", "onboarding_collected": bad_value}
        )
        assert profile.onboarding_collected == {}, f"expected {{}} for {bad_value!r}"


def test_onboarding_collected_string_false_coerced_to_bool_false() -> None:
    """Legacy DB rows with string 'false' or '0' must not be flipped to True by bool()."""
    profile = AthleteProfile.model_validate(
        {
            "user_id": "u1",
            "onboarding_collected": {
                "nutrition": "false",
                "goals": "0",
                "training": "true",
                "metrics": "1",
                "completed": True,
            },
        }
    )
    assert profile.onboarding_collected == {
        "nutrition": False,
        "goals": False,
        "training": True,
        "metrics": True,
        "completed": True,
    }


@pytest.mark.asyncio
async def test_update_athlete_profile_fields_drops_null_specialization_pct() -> None:
    """None specialization_pct must be excluded from the upsert payload.

    A multi-sport athlete may have no single-sport specialization. The AI sends
    specialization_pct=None; the repo filter must drop it so Postgres is never asked
    to store an explicit NULL on a partial update (preserving whatever was stored).
    """
    client = FakeSupabaseClient()
    repo = SupabaseRepository(client=client)

    await repo.update_athlete_profile_fields(
        "athlete-multi",
        {
            "primary_sports": ["cycling", "running"],
            "specialization_pct": None,
            "weekly_available_hours": 8,
        },
    )

    # FakeSupabaseClient.table() returns a fresh query builder each call, so _upserted_payload
    # lives only on the ephemeral instance. execute() appends to the shared _rows list though,
    # so inspect the row that landed in the backing store.
    rows = client._tables["athlete_profiles"]._rows
    assert len(rows) == 1, f"expected 1 row after upsert, got {len(rows)}"
    upserted = rows[0]
    assert "specialization_pct" not in upserted, (
        "specialization_pct=None must be filtered out before reaching the DB"
    )
    assert upserted.get("primary_sports") == ["cycling", "running"]
    assert upserted.get("weekly_available_hours") == 8


@pytest.mark.asyncio
async def test_upsert_athlete_profile_allows_null_specialization_pct() -> None:
    """upsert_athlete_profile must tolerate specialization_pct=None on the model.

    Multi-sport athletes have no single-sport specialization. The column is nullable
    after migration 20260624055541, so the model must accept None and the upsert must succeed.

    Unlike update_athlete_profile_fields (which filters out None values), upsert sends
    the full model_dump payload — specialization_pct is present as explicit null.
    After migration 20260624055541 the column accepts null, so this reaches the DB correctly.
    """
    client = FakeSupabaseClient()
    repo = SupabaseRepository(client=client)

    profile = AthleteProfile(
        user_id="athlete-multi2",
        primary_sports=["cycling", "running"],
        specialization_pct=None,
    )
    assert profile.specialization_pct is None

    saved = await repo.upsert_athlete_profile(profile)
    assert saved.user_id == "athlete-multi2"
    assert saved.specialization_pct is None

    # Verify the upsert payload explicitly contains specialization_pct=None (not omitted).
    # upsert_athlete_profile uses model_dump and sends all fields; it is the nullable
    # column (migration 20260624055541) that makes this safe.  Contrast with
    # update_athlete_profile_fields which drops None values via _safe_athlete_profile_fields.
    # FakeSupabaseClient.table() returns a fresh query builder each call; use _rows instead
    # (execute() appends to the shared backing list).
    rows = client._tables["athlete_profiles"]._rows
    assert len(rows) == 1, f"expected 1 row after upsert, got {len(rows)}"
    payload = rows[0]
    assert "specialization_pct" in payload, (
        "upsert_athlete_profile must send specialization_pct explicitly (as null), "
        "not omit it — omission would be semantically ambiguous in a full upsert"
    )
    assert payload["specialization_pct"] is None
