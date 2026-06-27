import re
from datetime import date

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

    def select(self, *_columns: str) -> "FakeTableQuery":
        return self

    def eq(self, column: str, value: object) -> "FakeTableQuery":
        self._filters[column] = value
        return self

    def in_(self, column: str, values: list[object]) -> "FakeTableQuery":
        self._in_filters[column] = set(values)
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

    def upsert(self, payload: dict[str, object], on_conflict: str) -> "FakeTableQuery":
        assert on_conflict
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
    ) -> None:
        self._tables = {
            "athlete_profiles": FakeTableQuery(athlete_rows or []),
            "sport_thresholds": FakeTableQuery(threshold_rows or []),
            "activities": FakeTableQuery(activity_rows or []),
            "chat_threads": FakeTableQuery(chat_thread_rows or []),
            "chat_messages": FakeTableQuery(chat_message_rows or []),
            "chat_attachments": FakeTableQuery(chat_attachment_rows or []),
        }

    def table(self, table_name: str) -> FakeTableQuery:
        return self._tables[table_name]


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


def test_athlete_profile_specialization_pct_defaults_to_none() -> None:
    """Regression guard: default must be None, not 80 (issue #254).

    If this fails someone reverted int|None=None back to int=80 in athlete.py.
    """
    profile = AthleteProfile(user_id="x")
    assert profile.specialization_pct is None


def test_onboarding_collected_null_values_coerced_to_false() -> None:
    """Legacy DB rows with null section flags must not raise ValidationError."""
    profile = AthleteProfile.model_validate(
        {
            "user_id": "u1",
            "onboarding_collected": {"nutrition": None, "goals": True},
        }
    )
    assert profile.onboarding_collected == {"nutrition": False, "goals": True}


def test_onboarding_collected_missing_or_non_dict_coerced_to_empty() -> None:
    """A missing or non-dict value must fall back to an empty dict, not crash."""
    for bad_value in (None, "yes", [], 42):
        profile = AthleteProfile.model_validate(
            {"user_id": "u1", "onboarding_collected": bad_value}
        )
        assert profile.onboarding_collected == {}, f"expected {{}} for {bad_value!r}"


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

    table = client.table("athlete_profiles")
    assert isinstance(table, FakeTableQuery)
    upserted = table._upserted_payload
    assert upserted is not None
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
    table = client.table("athlete_profiles")
    assert isinstance(table, FakeTableQuery)
    payload = table._upserted_payload
    assert payload is not None
    assert "specialization_pct" in payload, (
        "upsert_athlete_profile must send specialization_pct explicitly (as null), "
        "not omit it — omission would be semantically ambiguous in a full upsert"
    )
    assert payload["specialization_pct"] is None
