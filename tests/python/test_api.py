from datetime import datetime

import pytest
from httpx import ASGITransport, AsyncClient

import api.index as api_index
from backend.models.auth import UserContext
from backend.models.planning import AthleteProfile, CheckInInput, CheckInRecord
from backend.repos.supabase_repo import RecordNotFoundError, RepositoryNotConfiguredError


class FakeRepository:
    async def get_athlete_profile(self, user_id: str) -> AthleteProfile:
        return AthleteProfile(
            user_id=user_id,
            cycling_ftp_watts=238,
            goals=["Prepare for CX season"],
            constraints=["Friday childcare"],
        )

    async def create_check_in(self, check_in: CheckInInput) -> CheckInRecord:
        return CheckInRecord(
            id="check-in-1",
            user_id=check_in.user_id,
            raw_text=check_in.raw_text,
            image_count=check_in.image_count,
            effective_date=check_in.effective_date,
            created_at=datetime.fromisoformat("2026-03-21T10:00:00+00:00"),
        )

    async def upsert_athlete_profile(self, profile: AthleteProfile) -> AthleteProfile:
        return profile


class MissingProfileRepository:
    async def get_athlete_profile(self, user_id: str) -> AthleteProfile:
        raise RecordNotFoundError(f"No athlete profile found for user '{user_id}'.")


class UnconfiguredRepository:
    async def get_athlete_profile(self, user_id: str) -> AthleteProfile:
        raise RepositoryNotConfiguredError("Supabase is not configured.")

    async def upsert_athlete_profile(self, profile: AthleteProfile) -> AthleteProfile:
        raise RepositoryNotConfiguredError("Supabase is not configured.")

    async def create_check_in(self, check_in: CheckInInput) -> CheckInRecord:
        raise RepositoryNotConfiguredError("Supabase is not configured.")


async def test_protected_profile_requires_bearer_token() -> None:
    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/api/profile", json={"user_id": "athlete-1"})

    assert response.status_code == 401


async def test_create_check_in_returns_persisted_record(monkeypatch) -> None:
    api_index.app.dependency_overrides[api_index.require_user_context] = lambda: UserContext(
        user_id="athlete-1",
        scopes=["plans:write"],
        client_id="test-client",
        grant_id="grant-1",
    )
    monkeypatch.setattr(api_index, "repo", FakeRepository())

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/check-ins",
            json={
                "user_id": "athlete-1",
                "raw_text": "Travel week, low energy.",
                "image_count": 1,
                "effective_date": "2026-03-21",
            },
        )

    api_index.app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is True
    assert body["check_in"]["id"] == "check-in-1"
    assert body["check_in"]["effective_date"] == "2026-03-21"


@pytest.mark.asyncio
async def test_profile_returns_404_when_profile_is_missing(monkeypatch) -> None:
    api_index.app.dependency_overrides[api_index.require_user_context] = lambda: UserContext(
        user_id="athlete-1",
        scopes=["profile:read"],
        client_id="test-client",
        grant_id="grant-1",
    )
    monkeypatch.setattr(api_index, "repo", MissingProfileRepository())

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/api/profile", json={"user_id": "athlete-1"})

    api_index.app.dependency_overrides.clear()

    assert response.status_code == 404
    assert "No athlete profile found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_check_in_returns_503_when_supabase_is_unconfigured(monkeypatch) -> None:
    api_index.app.dependency_overrides[api_index.require_user_context] = lambda: UserContext(
        user_id="athlete-1",
        scopes=["plans:write"],
        client_id="test-client",
        grant_id="grant-1",
    )
    monkeypatch.setattr(api_index, "repo", UnconfiguredRepository())

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/check-ins",
            json={
                "user_id": "athlete-1",
                "raw_text": "Travel week, low energy.",
                "image_count": 1,
            },
        )

    api_index.app.dependency_overrides.clear()

    assert response.status_code == 503
    assert "Supabase is not configured" in response.json()["detail"]


@pytest.mark.asyncio
async def test_profile_rejects_cross_user_access(monkeypatch) -> None:
    api_index.app.dependency_overrides[api_index.require_user_context] = lambda: UserContext(
        user_id="athlete-1",
        scopes=["profile:read"],
        client_id="test-client",
        grant_id="grant-1",
    )
    monkeypatch.setattr(api_index, "repo", FakeRepository())

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/api/profile", json={"user_id": "athlete-2"})

    api_index.app.dependency_overrides.clear()

    assert response.status_code == 403
    assert "cannot access this resource" in response.json()["detail"]


@pytest.mark.asyncio
async def test_check_in_rejects_cross_user_access(monkeypatch) -> None:
    api_index.app.dependency_overrides[api_index.require_user_context] = lambda: UserContext(
        user_id="athlete-1",
        scopes=["plans:write"],
        client_id="test-client",
        grant_id="grant-1",
    )
    monkeypatch.setattr(api_index, "repo", FakeRepository())

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/check-ins",
            json={
                "user_id": "athlete-2",
                "raw_text": "Travel week, low energy.",
                "image_count": 1,
            },
        )

    api_index.app.dependency_overrides.clear()

    assert response.status_code == 403
    assert "cannot access this resource" in response.json()["detail"]


@pytest.mark.asyncio
async def test_profile_upsert_returns_saved_profile(monkeypatch) -> None:
    api_index.app.dependency_overrides[api_index.require_user_context] = lambda: UserContext(
        user_id="athlete-1",
        scopes=["profile:write"],
        client_id="test-client",
        grant_id="grant-1",
    )
    monkeypatch.setattr(api_index, "repo", FakeRepository())

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.put(
            "/api/profile",
            json={
                "user_id": "athlete-1",
                "cycling_ftp_watts": 245,
                "goals": ["Improve repeatability"],
                "constraints": ["Thursday travel"],
                "injuries_rehab": ["Achilles rehab"],
                "notes": "Prefers long endurance outdoors.",
                "age": 35,
                "weight_kg": 70.2,
            },
        )

    api_index.app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == "athlete-1"
    assert body["cycling_ftp_watts"] == 245


@pytest.mark.asyncio
async def test_profile_upsert_rejects_cross_user_access(monkeypatch) -> None:
    api_index.app.dependency_overrides[api_index.require_user_context] = lambda: UserContext(
        user_id="athlete-1",
        scopes=["profile:write"],
        client_id="test-client",
        grant_id="grant-1",
    )
    monkeypatch.setattr(api_index, "repo", FakeRepository())

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.put(
            "/api/profile",
            json={"user_id": "athlete-2", "goals": ["Not allowed"]},
        )

    api_index.app.dependency_overrides.clear()

    assert response.status_code == 403
    assert "cannot access this resource" in response.json()["detail"]
