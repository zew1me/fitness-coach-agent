from datetime import UTC, datetime

import pytest

from backend.models.planning import AthleteProfile, CheckInInput
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
        self.inserted_payload: dict[str, object] | None = None
        self.upserted_payload: dict[str, object] | None = None

    def select(self, *_columns: str) -> "FakeTableQuery":
        return self

    def eq(self, column: str, value: object) -> "FakeTableQuery":
        self._filters[column] = value
        return self

    def insert(self, payload: dict[str, object]) -> "FakeTableQuery":
        self.inserted_payload = payload
        return self

    def upsert(self, payload: dict[str, object], on_conflict: str) -> "FakeTableQuery":
        assert on_conflict == "user_id"
        self.upserted_payload = payload
        return self

    def execute(self) -> FakeResponse:
        if self.inserted_payload is not None:
            return FakeResponse([self.inserted_payload])
        if self.upserted_payload is not None:
            return FakeResponse([self.upserted_payload])
        filtered_rows = [
            row
            for row in self._rows
            if all(row.get(column) == value for column, value in self._filters.items())
        ]
        return FakeResponse(filtered_rows)


class FakeSupabaseClient:
    def __init__(
        self,
        *,
        athlete_rows: list[dict[str, object]] | None = None,
        check_in_rows: list[dict[str, object]] | None = None,
    ) -> None:
        self._tables = {
            "athlete_profiles": FakeTableQuery(athlete_rows or []),
            "check_ins": FakeTableQuery(check_in_rows or []),
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
                    "cycling_ftp_watts": 250,
                    "goals": ["Raise threshold"],
                    "constraints": ["Tuesday travel"],
                    "injuries_rehab": ["Low-back rehab"],
                    "notes": "Build toward fall CX block.",
                    "age": 37,
                    "weight_kg": 72.5,
                }
            ]
        )
    )

    profile = await repo.get_athlete_profile("athlete-1")

    assert profile.user_id == "athlete-1"
    assert profile.cycling_ftp_watts == 250
    assert profile.constraints == ["Tuesday travel"]


@pytest.mark.asyncio
async def test_get_athlete_profile_raises_for_missing_row() -> None:
    repo = SupabaseRepository(client=FakeSupabaseClient())

    with pytest.raises(RecordNotFoundError):
        await repo.get_athlete_profile("missing-user")


@pytest.mark.asyncio
async def test_create_check_in_persists_payload() -> None:
    client = FakeSupabaseClient()
    repo = SupabaseRepository(client=client)
    check_in = CheckInInput(user_id="athlete-1", raw_text="Fatigued after travel.", image_count=2)

    record = await repo.create_check_in(check_in)

    assert record.user_id == "athlete-1"
    assert record.raw_text == "Fatigued after travel."
    assert record.image_count == 2
    assert record.id
    assert record.created_at <= datetime.now(UTC)


@pytest.mark.asyncio
async def test_upsert_athlete_profile_persists_profile_payload() -> None:
    client = FakeSupabaseClient()
    repo = SupabaseRepository(client=client)

    profile = await repo.upsert_athlete_profile(
        AthleteProfile(
            user_id="athlete-1",
            cycling_ftp_watts=245,
            goals=["Improve repeatability"],
            constraints=["Thursday travel"],
            injuries_rehab=["Achilles rehab"],
            notes="Prefers long endurance outdoors.",
            age=35,
            weight_kg=70.2,
        )
    )

    assert profile.user_id == "athlete-1"
    assert profile.cycling_ftp_watts == 245
    assert profile.injuries_rehab == ["Achilles rehab"]


@pytest.mark.asyncio
async def test_repository_requires_supabase_configuration() -> None:
    repo = SupabaseRepository(client=None)
    repo._client = None

    with pytest.raises(RepositoryNotConfiguredError):
        await repo.get_athlete_profile("athlete-1")
