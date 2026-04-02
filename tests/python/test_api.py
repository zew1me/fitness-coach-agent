import base64
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, TypedDict, cast

import pytest
from httpx import ASGITransport, AsyncClient

import api.index as api_index
from backend.models.auth import (
    BrowserSessionContext,
    OAuthRevokeRequest,
    OAuthTokenRequest,
    UserContext,
)
from backend.models.planning import AthleteProfile, CheckInInput, CheckInRecord
from backend.repos.oauth_repo import OAuthRepositoryNotConfiguredError
from backend.repos.supabase_repo import RecordNotFoundError, RepositoryNotConfiguredError
from backend.services.auth import AuthService


class GrantRecord(TypedDict):
    id: str
    user_id: str
    client_id: str
    redirect_uri: str
    scopes: list[str]
    created_at: datetime
    updated_at: datetime
    revoked_at: datetime | None


class AuthorizationCodeRecord(TypedDict):
    id: str
    grant_id: str
    user_id: str
    client_id: str
    redirect_uri: str
    scopes: list[str]
    code_challenge: str
    code_challenge_method: str
    expires_at: datetime
    consumed_at: datetime | None
    created_at: datetime


class RefreshTokenRecord(TypedDict):
    id: str
    grant_id: str
    user_id: str
    client_id: str
    scopes: list[str]
    expires_at: datetime
    revoked_at: datetime | None
    created_at: datetime
    rotated_from_id: str | None


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


class PlannerRepository(FakeRepository):
    async def get_athlete_profile(self, user_id: str) -> AthleteProfile:
        return AthleteProfile(
            user_id=user_id,
            cycling_ftp_watts=238,
            goals=["Raise FTP for cyclocross"],
            constraints=["Wednesday travel"],
        )


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


class InMemoryOAuthRepository:
    def __init__(self) -> None:
        self.grants: dict[str, GrantRecord] = {}
        self.codes: dict[str, AuthorizationCodeRecord] = {}
        self.refresh_tokens: dict[str, RefreshTokenRecord] = {}

    def get_active_grant(self, *, user_id: str, client_id: str, redirect_uri: str):
        for grant in self.grants.values():
            if (
                grant["user_id"] == user_id
                and grant["client_id"] == client_id
                and grant["redirect_uri"] == redirect_uri
                and grant["revoked_at"] is None
            ):
                return type("Grant", (), grant)()
        return None

    def get_grant_by_id(self, grant_id: str):
        grant = self.grants.get(grant_id)
        if grant is None:
            return None
        return type("Grant", (), grant)()

    def upsert_grant(self, *, user_id: str, client_id: str, redirect_uri: str, scopes: list[str]):
        existing = self.get_active_grant(
            user_id=user_id, client_id=client_id, redirect_uri=redirect_uri
        )
        now = datetime.now().astimezone()
        if existing is None:
            grant_id = f"grant-{len(self.grants) + 1}"
            self.grants[grant_id] = {
                "id": grant_id,
                "user_id": user_id,
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "scopes": scopes,
                "created_at": now,
                "updated_at": now,
                "revoked_at": None,
            }
            return type("Grant", (), self.grants[grant_id])()

        current = self.grants[existing.id]
        current["scopes"] = sorted(set(current["scopes"]).union(scopes))
        current["updated_at"] = now
        return type("Grant", (), current)()

    def create_authorization_code(
        self,
        *,
        grant_id: str,
        user_id: str,
        client_id: str,
        redirect_uri: str,
        scopes: list[str],
        code_challenge: str,
        code_challenge_method: str,
    ) -> str:
        code = f"code-{len(self.codes) + 1}"
        self.codes[code] = {
            "id": code,
            "grant_id": grant_id,
            "user_id": user_id,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scopes": scopes,
            "code_challenge": code_challenge,
            "code_challenge_method": code_challenge_method,
            "expires_at": datetime.max.replace(tzinfo=UTC),
            "consumed_at": None,
            "created_at": datetime.now(UTC),
        }
        return code

    def consume_authorization_code(self, raw_code: str):
        code = self.codes.get(raw_code)
        if code is None:
            raise ValueError("Invalid authorization code.")
        if code["consumed_at"] is not None:
            raise ValueError("Authorization code is no longer valid.")
        code["consumed_at"] = datetime.now().astimezone()
        return type("AuthorizationCode", (), code)()

    def create_refresh_token(
        self,
        *,
        grant_id: str,
        user_id: str,
        client_id: str,
        scopes: list[str],
        rotated_from_id: str | None = None,
    ) -> str:
        token = f"refresh-{len(self.refresh_tokens) + 1}"
        self.refresh_tokens[token] = {
            "id": token,
            "grant_id": grant_id,
            "user_id": user_id,
            "client_id": client_id,
            "scopes": scopes,
            "expires_at": datetime.max.replace(tzinfo=UTC),
            "revoked_at": None,
            "created_at": datetime.now(UTC),
            "rotated_from_id": rotated_from_id,
        }
        return token

    def rotate_refresh_token(self, raw_token: str):
        current = self.refresh_tokens.get(raw_token)
        if current is None:
            raise ValueError("Invalid refresh token.")
        current["revoked_at"] = datetime.now().astimezone()
        replacement = self.create_refresh_token(
            grant_id=current["grant_id"],
            user_id=current["user_id"],
            client_id=current["client_id"],
            scopes=current["scopes"],
            rotated_from_id=current["id"],
        )
        return type("RefreshToken", (), current)(), replacement

    def revoke_refresh_token(self, raw_token: str) -> bool:
        current = self.refresh_tokens.get(raw_token)
        if current is None or current["revoked_at"] is not None:
            return False
        current["revoked_at"] = datetime.now().astimezone()
        return True

    def revoke_grant(self, grant_id: str) -> bool:
        grant = self.grants.get(grant_id)
        if grant is None:
            return False
        grant["revoked_at"] = datetime.now().astimezone()
        return True


class FakeAuthService(AuthService):
    def create_browser_session(self, supabase_access_token: str) -> BrowserSessionContext:
        if supabase_access_token != "supabase-access-token":
            raise OAuthRepositoryNotConfiguredError("Unable to verify browser session.")
        return BrowserSessionContext(user_id="athlete-1", email="athlete@example.com")


async def test_protected_profile_requires_bearer_token() -> None:
    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/api/profile", json={"user_id": "athlete-1"})

    assert response.status_code == 401


@pytest.fixture
def auth_service_fixture():
    original = api_index.auth_service
    api_index.auth_service = FakeAuthService(oauth_repo=cast(Any, InMemoryOAuthRepository()))
    try:
        yield api_index.auth_service
    finally:
        api_index.auth_service = original


@pytest.mark.asyncio
async def test_oauth_authorize_redirects_to_login_without_browser_session(
    auth_service_fixture,
) -> None:
    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/oauth/authorize",
            params={
                "client_id": "https://chat.openai.com",
                "redirect_uri": "https://chat.openai.com/callback",
                "scope": "profile:read plans:write",
                "state": "state-1",
                "code_challenge": "challenge-1",
                "code_challenge_method": "S256",
            },
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"].startswith("http://localhost:3000/login?")


@pytest.mark.asyncio
async def test_oauth_authorize_redirects_to_consent_when_grant_missing(
    auth_service_fixture,
) -> None:
    browser_cookie = auth_service_fixture.create_browser_session_token(
        BrowserSessionContext(user_id="athlete-1", email="athlete@example.com")
    )
    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/oauth/authorize",
            params={
                "client_id": "https://chat.openai.com",
                "redirect_uri": "https://chat.openai.com/callback",
                "scope": "profile:read plans:write",
                "state": "state-1",
                "code_challenge": "challenge-1",
                "code_challenge_method": "S256",
            },
            cookies={"coach_browser_session": browser_cookie},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"].startswith("http://localhost:3000/consent?")


@pytest.mark.asyncio
async def test_oauth_authorize_decision_exchanges_code_refresh_and_revokes(
    auth_service_fixture,
) -> None:
    browser_cookie = auth_service_fixture.create_browser_session_token(
        BrowserSessionContext(user_id="athlete-1", email="athlete@example.com")
    )
    verifier = "verifier"
    challenge = (
        base64.urlsafe_b64encode(sha256(verifier.encode("utf-8")).digest())
        .decode("utf-8")
        .rstrip("=")
    )
    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        decision_response = await client.post(
            "/api/oauth/authorize/decision",
            data={
                "client_id": "https://chat.openai.com",
                "redirect_uri": "https://chat.openai.com/callback",
                "scope": "profile:read plans:write",
                "state": "state-1",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "decision": "approve",
            },
            cookies={"coach_browser_session": browser_cookie},
            follow_redirects=False,
        )

        assert decision_response.status_code == 302
        redirected = decision_response.headers["location"]
        assert redirected.startswith("https://chat.openai.com/callback?code=")
        code = redirected.split("code=")[1].split("&", 1)[0]

        token_response = await client.post(
            "/api/oauth/token",
            json=OAuthTokenRequest(
                client_id="https://chat.openai.com",
                code=code,
                code_verifier=verifier,
                grant_type="authorization_code",
                redirect_uri="https://chat.openai.com/callback",
            ).model_dump(mode="json"),
        )

        assert token_response.status_code == 200
        token_body = token_response.json()
        assert "access_token" in token_body
        assert token_body["refresh_token"].startswith("refresh-")

        protected_response = await client.post(
            "/api/mcp",
            headers={"Authorization": f"Bearer {token_body['access_token']}"},
        )
        assert protected_response.status_code == 200

        refresh_response = await client.post(
            "/api/oauth/token",
            json=OAuthTokenRequest(
                client_id="https://chat.openai.com",
                grant_type="refresh_token",
                refresh_token=token_body["refresh_token"],
            ).model_dump(mode="json"),
        )

        assert refresh_response.status_code == 200
        refreshed_body = refresh_response.json()
        assert refreshed_body["refresh_token"] != token_body["refresh_token"]

        revoke_response = await client.post(
            "/api/oauth/revoke",
            json=OAuthRevokeRequest(token=refreshed_body["access_token"]).model_dump(mode="json"),
        )

        assert revoke_response.status_code == 200
        assert revoke_response.json()["revoked"] is True

        revoked_access_response = await client.post(
            "/api/mcp",
            headers={"Authorization": f"Bearer {refreshed_body['access_token']}"},
        )
        assert revoked_access_response.status_code == 401


@pytest.mark.asyncio
async def test_oauth_browser_session_endpoint_sets_cookie(auth_service_fixture) -> None:
    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/oauth/browser-session",
            json={"access_token": "supabase-access-token"},
        )

    assert response.status_code == 200
    assert "coach_browser_session=" in response.headers["set-cookie"]


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
async def test_generate_plan_returns_adaptive_plan(monkeypatch) -> None:
    api_index.app.dependency_overrides[api_index.require_user_context] = lambda: UserContext(
        user_id="athlete-1",
        scopes=["plans:read"],
        client_id="test-client",
        grant_id="grant-1",
    )
    monkeypatch.setattr(api_index, "repo", PlannerRepository())

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/plans/generate",
            json={
                "user_id": "athlete-1",
                "raw_text": "Feeling fatigued after travel with heavy legs.",
                "image_count": 1,
            },
        )

    api_index.app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["plan"]["hours"] == 4.8
    assert body["plan"]["days"][1]["focus"] == "Image-informed recovery day"
    assert body["plan"]["days"][4]["focus"] == "Portable tempo session"
    assert body["plan"]["days"][12]["focus"] == "Tempo run substitution"
    assert body["prompt_preview"].startswith("You are a fitness expert")


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
