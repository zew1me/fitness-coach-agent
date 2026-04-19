import base64
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, TypedDict, cast

import pytest
from httpx import ASGITransport, AsyncClient

import api.index as api_index
from backend.models.athlete import (
    AthleteProfile,
    RecoveryLog,
    ScheduleAvailability,
    SportThreshold,
)
from backend.models.auth import (
    BrowserSessionContext,
    BrowserTokenResponse,
    OAuthRevokeRequest,
    OAuthTokenRequest,
    UserContext,
)
from backend.models.training import Activity, DailyLoadSnapshot, Goal
from backend.repos.oauth_repo import OAuthRepositoryNotConfiguredError
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


class EngineRepository:
    async def get_athlete_profile(self, user_id: str) -> AthleteProfile:
        return AthleteProfile(
            user_id=user_id,
            display_name="Athlete One",
            birth_date=datetime.fromisoformat("1990-04-01T00:00:00+00:00").date(),
            biological_sex="not_specified",
            primary_sports=["running", "cycling"],
            weekly_available_hours=7.5,
            coaching_state="active",
        )

    async def get_active_thresholds(self, user_id: str) -> list[SportThreshold]:
        return [
            SportThreshold(
                id="threshold-1",
                user_id=user_id,
                sport="cycling",
                lt2_power_watts=250,
                lt1_power_watts=188,
                confidence="medium",
            )
        ]

    async def list_active_goals(self, user_id: str) -> list[Goal]:
        return [
            Goal(
                id="goal-1",
                user_id=user_id,
                goal_type="event",
                sport="running",
                title="Hill climb race",
                target_date=datetime.fromisoformat("2026-07-01T00:00:00+00:00").date(),
                course_distance_meters=14_000,
                course_elevation_gain_meters=700,
                priority=1,
            )
        ]

    async def get_latest_load(
        self, user_id: str, sport: str | None = None
    ) -> DailyLoadSnapshot | None:
        return DailyLoadSnapshot(
            user_id=user_id,
            snapshot_date=datetime.fromisoformat("2026-04-01T00:00:00+00:00").date(),
            sport=sport,
            daily_tss=60,
            ctl=42,
            atl=50,
            tsb=-8,
        )

    async def list_recovery_logs(
        self, user_id: str, *, since=None, limit: int = 14
    ) -> list[RecoveryLog]:
        return [
            RecoveryLog(
                id="recovery-1",
                user_id=user_id,
                log_date=datetime.fromisoformat("2026-04-01T00:00:00+00:00").date(),
                sleep_score=82,
                hrv_ms=55,
            )
        ][:limit]

    async def get_schedule(self, user_id: str) -> ScheduleAvailability:
        return ScheduleAvailability(
            id="schedule-1",
            user_id=user_id,
            weekly_pattern={"monday": {"available": True, "max_hours": 1.0}},
        )

    async def get_active_plan(self, user_id: str):
        return None

    async def list_activities(self, user_id: str, *, sport=None, since=None, limit: int = 50):
        return []

    async def upsert_load_snapshots(self, user_id: str, snapshots: list[dict], sport=None) -> None:
        self.snapshots = snapshots


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

    def get_authorization_code(self, raw_code: str):
        code = self.codes.get(raw_code)
        if code is None:
            return None
        return type("AuthorizationCode", (), code)()

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

    def get_refresh_token(self, raw_token: str):
        token = self.refresh_tokens.get(raw_token)
        if token is None:
            return None
        return type("RefreshToken", (), token)()

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
        response = await client.post(
            "/api/engine/get-athlete-summary", json={"user_id": "athlete-1"}
        )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_chat_attachments_presign_requires_bearer_token() -> None:
    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/chat/attachments/presign",
            json={
                "filename": "garage-test.txt",
                "content_type": "text/plain",
                "content_length": 1,
                "purpose": "chat-attachment",
            },
        )

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing bearer token"


@pytest.mark.asyncio
async def test_chat_attachments_upload_requires_bearer_token() -> None:
    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/api/chat/attachments/upload")

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing bearer token"


@pytest.mark.asyncio
async def test_chat_attachments_upload_validates_object_key_scope(
    auth_service_fixture, monkeypatch
) -> None:
    # Mock R2 service to avoid actual S3 calls
    from backend.models.storage import PresignUploadResponse

    async def mock_upload_file(*args, **kwargs):
        return PresignUploadResponse(
            upload_url="",
            object_key="users/athlete-1/chat-attachment/2024/01/01/file.png",
            public_url="https://cdn.example.com/file.png",
            headers={"Content-Type": "image/png"},
            method="POST",
        )

    monkeypatch.setattr("api.index.r2_service.upload_file", mock_upload_file)

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # First set up browser session
        session_response = await client.post(
            "/api/oauth/browser-session",
            json={"access_token": "supabase-access-token"},
        )
        assert session_response.status_code == 200

        cookie_header = session_response.headers["set-cookie"]
        cookie_value = cookie_header.split("coach_browser_session=")[1].split(";")[0]

        # Then get a valid token
        token_response = await client.post(
            "/api/oauth/browser-token",
            cookies={"coach_browser_session": cookie_value},
        )
        token_body = token_response.json()

        # Try to upload with object_key that doesn't belong to the authenticated user
        response = await client.post(
            "/api/chat/attachments/upload",
            data={"object_key": "users/different-user/chat-attachment/2024/01/01/file.png"},
            files={"file": ("test.png", b"fake image data", "image/png")},
            headers={"Authorization": f"Bearer {token_body['access_token']}"},
        )

    assert response.status_code == 403
    assert "does not belong to authenticated user" in response.json()["detail"]


@pytest.mark.asyncio
async def test_chat_attachments_upload_success(auth_service_fixture, monkeypatch) -> None:
    from backend.models.storage import PresignUploadResponse

    object_key = "users/athlete-1/chat-attachment/2024/01/01/file.png"
    public_url = "https://cdn.example.com/file.png"

    async def mock_upload_file(*args, **kwargs):
        return PresignUploadResponse(
            upload_url="",
            object_key=object_key,
            public_url=public_url,
            headers={"Content-Type": "image/png"},
            method="POST",
        )

    monkeypatch.setattr("api.index.r2_service.upload_file", mock_upload_file)

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        session_response = await client.post(
            "/api/oauth/browser-session",
            json={"access_token": "supabase-access-token"},
        )
        assert session_response.status_code == 200

        cookie_header = session_response.headers["set-cookie"]
        cookie_value = cookie_header.split("coach_browser_session=")[1].split(";")[0]

        token_response = await client.post(
            "/api/oauth/browser-token",
            cookies={"coach_browser_session": cookie_value},
        )
        token_body = token_response.json()

        response = await client.post(
            "/api/chat/attachments/upload",
            data={"object_key": object_key},
            files={"file": ("file.png", b"fake image data", "image/png")},
            headers={"Authorization": f"Bearer {token_body['access_token']}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["object_key"] == object_key
    assert body["public_url"] == public_url


@pytest.mark.asyncio
async def test_process_uploaded_file_parses_gpx_from_authenticated_object(
    auth_service_fixture, monkeypatch
) -> None:
    object_key = "users/athlete-1/chat-attachment/2024/01/01/run.gpx"
    captured: dict[str, str] = {}

    async def mock_download_file_bytes(*, user_id: str, object_key: str) -> bytes:
        captured["user_id"] = user_id
        captured["object_key"] = object_key
        return b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><trkseg>
    <trkpt lat="37.0" lon="-122.0"><ele>10</ele><time>2026-04-19T10:00:00Z</time></trkpt>
    <trkpt lat="37.0" lon="-122.001"><ele>12</ele><time>2026-04-19T10:01:00Z</time></trkpt>
  </trkseg></trk>
</gpx>"""

    monkeypatch.setattr("api.index.r2_service.download_file_bytes", mock_download_file_bytes)

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        session_response = await client.post(
            "/api/oauth/browser-session",
            json={"access_token": "supabase-access-token"},
        )
        assert session_response.status_code == 200

        cookie_header = session_response.headers["set-cookie"]
        cookie_value = cookie_header.split("coach_browser_session=")[1].split(";")[0]

        token_response = await client.post(
            "/api/oauth/browser-token",
            cookies={"coach_browser_session": cookie_value},
        )
        token_body = token_response.json()

        response = await client.post(
            "/api/engine/process-uploaded-file",
            json={
                "content_type": "application/gpx+xml",
                "filename": "run.gpx",
                "object_key": object_key,
                "public_url": "https://cdn.example.com/run.gpx",
                "user_id": "payload-user-is-ignored",
            },
            headers={"Authorization": f"Bearer {token_body['access_token']}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["activity"]["sport"] == "running"
    assert body["activity"]["source_file_key"] == object_key
    assert body["activity"]["source"] == "gpx_upload"
    assert captured == {"user_id": "athlete-1", "object_key": object_key}


@pytest.mark.asyncio
async def test_process_uploaded_file_parses_tcx_with_hrv_metadata(
    auth_service_fixture, monkeypatch
) -> None:
    object_key = "users/athlete-1/chat-attachment/2024/01/01/run.tcx"

    async def mock_download_file_bytes(*, user_id: str, object_key: str) -> bytes:
        return b"""<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
  <Activities>
    <Activity Sport="Running">
      <Id>2026-04-19T10:00:00Z</Id>
      <Lap StartTime="2026-04-19T10:00:00Z">
        <TotalTimeSeconds>60</TotalTimeSeconds>
        <DistanceMeters>200</DistanceMeters>
        <Track>
          <Trackpoint>
            <Time>2026-04-19T10:00:00Z</Time>
            <DistanceMeters>0</DistanceMeters>
            <HeartRateBpm><Value>140</Value></HeartRateBpm>
            <Extensions><rr>820</rr><rr>830</rr></Extensions>
          </Trackpoint>
          <Trackpoint>
            <Time>2026-04-19T10:01:00Z</Time>
            <DistanceMeters>200</DistanceMeters>
            <HeartRateBpm><Value>145</Value></HeartRateBpm>
            <Extensions><rr>815</rr><rr>825</rr></Extensions>
          </Trackpoint>
        </Track>
      </Lap>
    </Activity>
  </Activities>
</TrainingCenterDatabase>"""

    monkeypatch.setattr("api.index.r2_service.download_file_bytes", mock_download_file_bytes)

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        session_response = await client.post(
            "/api/oauth/browser-session",
            json={"access_token": "supabase-access-token"},
        )
        cookie_header = session_response.headers["set-cookie"]
        cookie_value = cookie_header.split("coach_browser_session=")[1].split(";")[0]
        token_response = await client.post(
            "/api/oauth/browser-token",
            cookies={"coach_browser_session": cookie_value},
        )
        token_body = token_response.json()

        response = await client.post(
            "/api/engine/process-uploaded-file",
            json={
                "content_type": "application/vnd.garmin.tcx+xml",
                "filename": "run.tcx",
                "object_key": object_key,
                "public_url": "https://cdn.example.com/run.tcx",
                "user_id": "payload-user-is-ignored",
            },
            headers={"Authorization": f"Bearer {token_body['access_token']}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["activity"]["source"] == "tcx_upload"
    assert body["activity"]["avg_hr_bpm"] == 142
    assert body["activity"]["raw_extraction"]["rr_interval_count"] == 4
    assert body["activity"]["raw_extraction"]["hrv"]["quality"] == "insufficient_rr_intervals"


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
async def test_oauth_authorize_rejects_unsupported_scope(auth_service_fixture) -> None:
    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/oauth/authorize",
            params={
                "client_id": "https://chat.openai.com",
                "redirect_uri": "https://chat.openai.com/callback",
                "scope": "profile:read admin:root",
                "state": "state-1",
                "code_challenge": "challenge-1",
                "code_challenge_method": "S256",
            },
            follow_redirects=False,
        )

    assert response.status_code == 400
    assert "unsupported" in response.json()["detail"]


@pytest.mark.asyncio
async def test_oauth_authorize_rejects_invalid_redirect_uri(auth_service_fixture) -> None:
    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/oauth/authorize",
            params={
                "client_id": "https://chat.openai.com",
                "redirect_uri": "javascript:alert(1)",
                "scope": "profile:read plans:write",
                "state": "state-1",
                "code_challenge": "challenge-1",
                "code_challenge_method": "S256",
            },
            follow_redirects=False,
        )

    assert response.status_code == 400
    assert "redirect URI" in response.json()["detail"]


@pytest.mark.asyncio
async def test_oauth_authorize_invalid_browser_cookie_redirects_to_login(
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
            cookies={"coach_browser_session": "not-a-jwt"},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"].startswith("http://localhost:3000/login?")


@pytest.mark.asyncio
async def test_oauth_authorize_prompt_consent_forces_consent(
    auth_service_fixture,
) -> None:
    browser_cookie = auth_service_fixture.create_browser_session_token(
        BrowserSessionContext(user_id="athlete-1", email="athlete@example.com")
    )
    auth_service_fixture._oauth_repo.upsert_grant(
        user_id="athlete-1",
        client_id="https://chat.openai.com",
        redirect_uri="https://chat.openai.com/callback",
        scopes=["profile:read", "plans:write"],
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
                "prompt": "consent",
                "code_challenge": "challenge-1",
                "code_challenge_method": "S256",
            },
            cookies={"coach_browser_session": browser_cookie},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"].startswith("http://localhost:3000/consent?")


@pytest.mark.asyncio
async def test_oauth_authorize_decision_denial_redirects_back_with_error(
    auth_service_fixture,
) -> None:
    browser_cookie = auth_service_fixture.create_browser_session_token(
        BrowserSessionContext(user_id="athlete-1", email="athlete@example.com")
    )
    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/oauth/authorize/decision",
            data={
                "client_id": "https://chat.openai.com",
                "redirect_uri": "https://chat.openai.com/callback",
                "scope": "profile:read plans:write",
                "state": "state-1",
                "code_challenge": "challenge-1",
                "code_challenge_method": "S256",
                "decision": "deny",
            },
            cookies={"coach_browser_session": browser_cookie},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"] == (
        "https://chat.openai.com/callback?error=access_denied&state=state-1"
    )


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


@pytest.mark.asyncio
async def test_oauth_browser_token_issues_same_origin_bearer(auth_service_fixture) -> None:
    browser_cookie = auth_service_fixture.create_browser_session_token(
        BrowserSessionContext(user_id="athlete-1", email="athlete@example.com")
    )
    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/oauth/browser-token",
            cookies={"coach_browser_session": browser_cookie},
        )

    assert response.status_code == 200
    payload = BrowserTokenResponse.model_validate(response.json())
    assert payload.user_id == "athlete-1"
    assert "profile:write" in payload.scopes


@pytest.mark.asyncio
async def test_oauth_browser_token_requires_browser_cookie(auth_service_fixture) -> None:
    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/api/oauth/browser-token")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_oauth_token_rejects_bad_code_verifier(auth_service_fixture) -> None:
    browser_cookie = auth_service_fixture.create_browser_session_token(
        BrowserSessionContext(user_id="athlete-1", email="athlete@example.com")
    )
    verifier = "correct-verifier"
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
        code = decision_response.headers["location"].split("code=")[1].split("&", 1)[0]

        token_response = await client.post(
            "/api/oauth/token",
            json=OAuthTokenRequest(
                client_id="https://chat.openai.com",
                code=code,
                code_verifier="wrong-verifier",
                grant_type="authorization_code",
                redirect_uri="https://chat.openai.com/callback",
            ).model_dump(mode="json"),
        )

    assert token_response.status_code == 400
    assert "code_verifier" in token_response.json()["detail"]
    assert auth_service_fixture._oauth_repo.codes[code]["consumed_at"] is None


@pytest.mark.asyncio
async def test_oauth_token_rejects_client_mismatch_without_consuming_code(
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
        code = decision_response.headers["location"].split("code=")[1].split("&", 1)[0]

        response = await client.post(
            "/api/oauth/token",
            json=OAuthTokenRequest(
                client_id="https://example.com",
                code=code,
                code_verifier=verifier,
                grant_type="authorization_code",
                redirect_uri="https://chat.openai.com/callback",
            ).model_dump(mode="json"),
        )

    assert response.status_code == 400
    assert "client or redirect mismatch" in response.json()["detail"]
    assert auth_service_fixture._oauth_repo.codes[code]["consumed_at"] is None


@pytest.mark.asyncio
async def test_oauth_token_rejects_reused_authorization_code(auth_service_fixture) -> None:
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
        code = decision_response.headers["location"].split("code=")[1].split("&", 1)[0]

        first_response = await client.post(
            "/api/oauth/token",
            json=OAuthTokenRequest(
                client_id="https://chat.openai.com",
                code=code,
                code_verifier=verifier,
                grant_type="authorization_code",
                redirect_uri="https://chat.openai.com/callback",
            ).model_dump(mode="json"),
        )
        second_response = await client.post(
            "/api/oauth/token",
            json=OAuthTokenRequest(
                client_id="https://chat.openai.com",
                code=code,
                code_verifier=verifier,
                grant_type="authorization_code",
                redirect_uri="https://chat.openai.com/callback",
            ).model_dump(mode="json"),
        )

    assert first_response.status_code == 200
    assert second_response.status_code == 400
    assert "no longer valid" in second_response.json()["detail"]


@pytest.mark.asyncio
async def test_oauth_refresh_rejects_client_mismatch_without_revoking_token(
    auth_service_fixture,
) -> None:
    refresh_token = auth_service_fixture._oauth_repo.create_refresh_token(
        grant_id="grant-1",
        user_id="athlete-1",
        client_id="https://chat.openai.com",
        scopes=["profile:read"],
    )
    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/oauth/token",
            json=OAuthTokenRequest(
                client_id="https://example.com",
                grant_type="refresh_token",
                refresh_token=refresh_token,
            ).model_dump(mode="json"),
        )

    assert response.status_code == 400
    assert "client mismatch" in response.json()["detail"]
    assert auth_service_fixture._oauth_repo.refresh_tokens[refresh_token]["revoked_at"] is None


async def test_engine_endpoint_requires_bearer_token() -> None:
    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/api/engine/calculate-zones", json={"sport": "cycling"})

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_calculate_zones_returns_power_boundaries() -> None:
    api_index.app.dependency_overrides[api_index.require_user_context] = lambda: UserContext(
        user_id="athlete-1",
        scopes=["profile:read"],
        client_id="test-client",
        grant_id="grant-1",
    )

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/engine/calculate-zones",
            json={"sport": "cycling", "ftp_watts": 300},
        )

    api_index.app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["zones"][0]["name"] == "Recovery"
    assert body["zones"][1]["power_high"] == 225


@pytest.mark.asyncio
async def test_compute_tss_returns_power_based_score() -> None:
    api_index.app.dependency_overrides[api_index.require_user_context] = lambda: UserContext(
        user_id="athlete-1",
        scopes=["profile:read"],
        client_id="test-client",
        grant_id="grant-1",
    )

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/engine/compute-tss",
            json={
                "duration_seconds": 3600,
                "sport": "cycling",
                "normalized_power": 250,
                "ftp": 250,
            },
        )

    api_index.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["tss"] == 100


@pytest.mark.asyncio
async def test_estimate_thresholds_returns_running_paces() -> None:
    api_index.app.dependency_overrides[api_index.require_user_context] = lambda: UserContext(
        user_id="athlete-1",
        scopes=["profile:read"],
        client_id="test-client",
        grant_id="grant-1",
    )

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/engine/estimate-thresholds",
            json={
                "sport": "running",
                "race_time_seconds": 20 * 60,
                "race_distance_meters": 5000,
            },
        )

    api_index.app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["sport"] == "running"
    assert body["lt1_pace_sec_km"] > body["lt2_pace_sec_km"]


@pytest.mark.asyncio
async def test_get_athlete_summary_returns_context_bundle(monkeypatch) -> None:
    api_index.app.dependency_overrides[api_index.require_user_context] = lambda: UserContext(
        user_id="athlete-1",
        scopes=["profile:read"],
        client_id="test-client",
        grant_id="grant-1",
    )
    monkeypatch.setattr(api_index, "repo", EngineRepository())

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/engine/get-athlete-summary",
            json={"user_id": "athlete-1"},
        )

    api_index.app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["profile"]["primary_sports"] == ["running", "cycling"]
    assert body["current_load"]["ctl"] == 42
    assert body["goals"][0]["course_distance_meters"] == 14_000
    assert body["ctl_ceiling_guidance"]["committed_amateur_ctl"] > 0


@pytest.mark.asyncio
async def test_get_athlete_summary_rejects_cross_user_access(monkeypatch) -> None:
    api_index.app.dependency_overrides[api_index.require_user_context] = lambda: UserContext(
        user_id="athlete-1",
        scopes=["profile:read"],
        client_id="test-client",
        grant_id="grant-1",
    )
    monkeypatch.setattr(api_index, "repo", EngineRepository())

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/engine/get-athlete-summary",
            json={"user_id": "athlete-2"},
        )

    api_index.app.dependency_overrides.clear()

    assert response.status_code == 403
    assert "cannot access this resource" in response.json()["detail"]


@pytest.mark.asyncio
async def test_get_recent_activities_returns_normalized_activity_list(monkeypatch) -> None:
    class ActivityRepository(EngineRepository):
        async def list_activities(self, user_id: str, *, sport=None, since=None, limit: int = 50):
            assert user_id == "athlete-1"
            assert sport == "running"
            assert limit == 2
            return [
                Activity(
                    id="activity-1",
                    user_id=user_id,
                    sport="running",
                    activity_date=datetime.fromisoformat("2026-04-10T00:00:00+00:00").date(),
                    duration_seconds=2700,
                    distance_meters=8000,
                    tss=55,
                )
            ]

    api_index.app.dependency_overrides[api_index.require_user_context] = lambda: UserContext(
        user_id="athlete-1",
        scopes=["profile:read"],
        client_id="test-client",
        grant_id="grant-1",
    )
    monkeypatch.setattr(api_index, "repo", ActivityRepository())

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/engine/get-recent-activities",
            json={"limit": 2, "sport": "running", "user_id": "athlete-1"},
        )

    api_index.app.dependency_overrides.clear()

    assert response.status_code == 200
    activities = response.json()["activities"]
    assert activities[0]["id"] == "activity-1"
    assert activities[0]["sport"] == "running"
    assert activities[0]["activity_date"] == "2026-04-10"
    assert activities[0]["distance_meters"] == 8000
    assert activities[0]["tss"] == 55


@pytest.mark.asyncio
async def test_generate_plan_structure_uses_goal_and_load(monkeypatch) -> None:
    api_index.app.dependency_overrides[api_index.require_user_context] = lambda: UserContext(
        user_id="athlete-1",
        scopes=["plans:write"],
        client_id="test-client",
        grant_id="grant-1",
    )
    monkeypatch.setattr(api_index, "repo", EngineRepository())

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/engine/generate-plan-structure",
            json={"user_id": "athlete-1"},
        )

    api_index.app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["target_goal"]["title"] == "Hill climb race"
    assert body["starting_weekly_tss"] == 294
    assert body["phases"]


@pytest.mark.asyncio
async def test_get_athlete_summary_new_user_returns_onboarding_stub(monkeypatch) -> None:
    """New users without a profile row get a stub with coaching_state=onboarding."""
    from backend.repos.supabase_repo import RecordNotFoundError

    class NewUserRepository(EngineRepository):
        async def get_athlete_profile(self, user_id: str) -> AthleteProfile:
            raise RecordNotFoundError(f"No athlete profile found for user '{user_id}'.")

    api_index.app.dependency_overrides[api_index.require_user_context] = lambda: UserContext(
        user_id="athlete-new",
        scopes=["profile:read"],
        client_id="test-client",
        grant_id="grant-1",
    )
    monkeypatch.setattr(api_index, "repo", NewUserRepository())

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/engine/get-athlete-summary",
            json={"user_id": "athlete-new"},
        )

    api_index.app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["profile"]["user_id"] == "athlete-new"
    assert body["profile"]["coaching_state"] == "onboarding"
    assert body["ctl_ceiling_guidance"]["committed_amateur_ctl"] > 0


@pytest.mark.asyncio
async def test_update_athlete_profile_persists_fields(monkeypatch) -> None:
    """update-athlete-profile saves display_name, primary_sports, and weekly_available_hours."""

    class MutableRepository(EngineRepository):
        def __init__(self) -> None:
            self.saved: dict | None = None

        async def update_athlete_profile_fields(self, user_id: str, fields: dict) -> AthleteProfile:
            self.saved = {"user_id": user_id, **fields}
            return AthleteProfile(
                user_id=user_id,
                display_name=fields.get("display_name"),
                primary_sports=fields.get("primary_sports", []),
                coaching_state="onboarding",
            )

    repo = MutableRepository()
    api_index.app.dependency_overrides[api_index.require_user_context] = lambda: UserContext(
        user_id="athlete-1",
        scopes=["profile:write"],
        client_id="test-client",
        grant_id="grant-1",
    )
    monkeypatch.setattr(api_index, "repo", repo)

    transport = ASGITransport(app=api_index.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/engine/update-athlete-profile",
            json={
                "user_id": "athlete-1",
                "fields": {
                    "display_name": "Alex",
                    "primary_sports": ["running", "cycling"],
                    "weekly_available_hours": 8.0,
                },
            },
        )

    api_index.app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["display_name"] == "Alex"
    assert repo.saved is not None
    assert repo.saved["display_name"] == "Alex"
    assert repo.saved["primary_sports"] == ["running", "cycling"]
