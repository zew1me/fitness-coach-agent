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
        self._upserted_payload: dict[str, object] | None = None
        self._update_payload: dict[str, object] | None = None
        self._limit: int | None = None

    def select(self, *_columns: str) -> "FakeTableQuery":
        return self

    def eq(self, column: str, value: object) -> "FakeTableQuery":
        self._filters[column] = value
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

    def insert(self, payload: dict[str, object]) -> "FakeTableQuery":
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
            and all(row.get(column) is None for column in self._is_null)
        ]


class FakeSupabaseClient:
    def __init__(
        self,
        *,
        athlete_rows: list[dict[str, object]] | None = None,
        threshold_rows: list[dict[str, object]] | None = None,
        activity_rows: list[dict[str, object]] | None = None,
    ) -> None:
        self._tables = {
            "athlete_profiles": FakeTableQuery(athlete_rows or []),
            "sport_thresholds": FakeTableQuery(threshold_rows or []),
            "activities": FakeTableQuery(activity_rows or []),
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
async def test_repository_requires_supabase_configuration() -> None:
    repo = SupabaseRepository(client=None)
    repo._client = None

    with pytest.raises(RepositoryNotConfiguredError):
        await repo.get_athlete_profile("athlete-1")
