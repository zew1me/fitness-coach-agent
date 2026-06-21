import re
from datetime import UTC, date, datetime, timedelta

import pytest

from backend.models.athlete import AthleteProfile, SportThreshold
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
        self._upserted_payload: dict[str, object] | None = None
        self._update_payload: dict[str, object] | None = None
        self._limit: int | None = None
        self._in_filters: dict[str, set[object]] = {}
        self._gt_filters: dict[str, object] = {}

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

    def order(self, *_args: object, **_kwargs: object) -> "FakeTableQuery":
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
        payload: dict[str, object],
        on_conflict: str,
        *,
        ignore_duplicates: bool = False,
    ) -> "FakeTableQuery":
        assert on_conflict
        del ignore_duplicates
        self._upserted_payload = payload
        return self

    def update(self, payload: dict[str, object]) -> "FakeTableQuery":
        self._update_payload = payload
        return self

    def execute(self) -> FakeResponse:
        if self._inserted_payload is not None:
            self._rows.append(self._inserted_payload)
            return FakeResponse([self._inserted_payload])
        if self._inserted_payloads is not None:
            self._rows.extend(self._inserted_payloads)
            return FakeResponse(self._inserted_payloads)
        if self._upserted_payload is not None:
            self._rows.append(self._upserted_payload)
            return FakeResponse([self._upserted_payload])
        if self._update_payload is not None:
            updated = []
            for row in self._matching_rows():
                row.update(self._update_payload)
                updated.append(row)
            return FakeResponse(updated)

        rows = self._matching_rows()
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
                row.get(column) is not None and row[column] > value
                for column, value in self._gt_filters.items()
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
    ) -> None:
        self._tables = {
            "athlete_profiles": FakeTableQuery(athlete_rows or []),
            "sport_thresholds": FakeTableQuery(threshold_rows or []),
            "activities": FakeTableQuery(activity_rows or []),
            "chat_threads": FakeTableQuery(chat_thread_rows or []),
            "chat_messages": FakeTableQuery(chat_message_rows or []),
            "chat_attachments": FakeTableQuery(chat_attachment_rows or []),
            "chat_model_states": FakeTableQuery(chat_model_state_rows or []),
        }

    def table(self, table_name: str) -> FakeTableQuery:
        # The real Supabase client returns a fresh query builder for each call.
        return FakeTableQuery(self._tables[table_name]._rows)


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
        )
    )

    assert activity.user_id == "athlete-1"
    assert activity.sport == "running"
    assert activity.tss == 75.5
    assert activity.fueling_notes == "Took one gel at 30 minutes"
    assert activity.id is not None


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
        expected_version=3,
        lease_id="lease-1",
        items=[{"role": "user", "content": "compacted"}],
        coaching_memory=[],
        compaction_metadata={"trigger": "token_threshold"},
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
            expected_version=3,
            lease_id="other-lease",
            items=[{"role": "user", "content": "intruder"}],
            coaching_memory=[],
            compaction_metadata={},
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
            expected_version=3,
            lease_id="lease-1",
            items=[],
            coaching_memory=[],
            compaction_metadata={},
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

    assert leased.lease_id == "new-lease"
    with pytest.raises(ValueError, match="already in progress"):
        await repo.acquire_chat_turn_lease(
            thread_id="thread-1",
            user_id="athlete-1",
            lease_id="other-lease",
            ttl_seconds=60,
        )
