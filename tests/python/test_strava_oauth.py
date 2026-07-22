"""Service-level tests for Strava OAuth, rotating-token refresh, and revocation."""

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import httpx
import pytest

from backend.models.strava import (
    StravaConnectionCreate,
    StravaConnectionRecord,
    StravaTokenRotation,
)
from backend.services.strava import (
    StravaConfigurationError,
    StravaOAuthService,
    StravaReconnectRequiredError,
    StravaScopeError,
    StravaStateError,
    TokenCipher,
)


class InMemoryStravaRepository:
    def __init__(self) -> None:
        self.rows: list[StravaConnectionRecord] = []

    def get_active_connection(self, user_id: str) -> StravaConnectionRecord | None:
        for row in reversed(self.rows):
            if row.user_id == user_id and row.revoked_at is None:
                return row
        return None

    def replace_connection(self, connection: StravaConnectionCreate) -> StravaConnectionRecord:
        now = datetime.now(UTC)
        for row in self.rows:
            if row.user_id == connection.user_id and row.revoked_at is None:
                row.revoked_at = now
        row = StravaConnectionRecord(
            id=str(uuid4()),
            user_id=connection.user_id,
            strava_athlete_id=connection.strava_athlete_id,
            strava_athlete_name=connection.strava_athlete_name,
            scopes=connection.scopes,
            access_token_ciphertext=connection.access_token_ciphertext,
            refresh_token_ciphertext=connection.refresh_token_ciphertext,
            token_type=connection.token_type,
            expires_at=connection.expires_at,
            authorization_version=connection.authorization_version,
            connected_at=now,
            updated_at=now,
        )
        self.rows.append(row)
        return row

    def rotate_tokens(
        self, *, connection_id: str, expected_expires_at: datetime, rotation: StravaTokenRotation
    ) -> StravaConnectionRecord | None:
        for row in self.rows:
            if (
                row.id == connection_id
                and row.revoked_at is None
                and row.expires_at == expected_expires_at
            ):
                row.access_token_ciphertext = rotation.access_token_ciphertext
                row.refresh_token_ciphertext = rotation.refresh_token_ciphertext
                row.token_type = rotation.token_type
                row.expires_at = rotation.expires_at
                return row
        return None

    def touch_last_sync(self, user_id: str) -> None:
        row = self.get_active_connection(user_id)
        if row is not None:
            row.last_sync_at = datetime.now(UTC)

    def revoke_active_connection(self, user_id: str) -> bool:
        row = self.get_active_connection(user_id)
        if row is None:
            return False
        row.revoked_at = datetime.now(UTC)
        return True


@pytest.fixture
def configured_strava(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.services.strava.settings.app_base_url", "https://coach.nigels.dev")
    monkeypatch.setattr(
        "backend.services.strava.settings.app_jwt_secret",
        "test-jwt-secret-with-at-least-thirty-two-bytes",
    )
    monkeypatch.setattr("backend.services.strava.settings.strava_integration_enabled", True)
    monkeypatch.setattr("backend.services.strava.settings.strava_client_id", "strava-123")
    monkeypatch.setattr("backend.services.strava.settings.strava_client_secret", "strava-secret")
    monkeypatch.setattr(
        "backend.services.strava.settings.strava_token_encryption_secret", "strava-enc-secret"
    )
    monkeypatch.setattr(
        "backend.services.strava.settings.strava_authorization_version", "2026-07-21"
    )
    monkeypatch.delenv("VERCEL_URL", raising=False)


pytestmark = pytest.mark.usefixtures("configured_strava")


def _service(
    repo: InMemoryStravaRepository | None = None,
    *,
    transport: httpx.MockTransport | None = None,
) -> StravaOAuthService:
    return StravaOAuthService(
        repository=repo or InMemoryStravaRepository(),
        http_client_factory=(
            (lambda: httpx.AsyncClient(transport=transport)) if transport is not None else None
        ),
    )


def _seed_connection(
    repo: InMemoryStravaRepository, *, expires_at: datetime, refresh: str = "refresh-1"
) -> StravaConnectionRecord:
    cipher = TokenCipher("strava-enc-secret")
    return repo.replace_connection(
        StravaConnectionCreate(
            user_id="coach-user-1",
            strava_athlete_id=135168,
            strava_athlete_name="Nigel",
            scopes=["read", "activity:read"],
            access_token_ciphertext=cipher.encrypt("access-1"),
            refresh_token_ciphertext=cipher.encrypt(refresh),
            token_type="Bearer",
            expires_at=expires_at,
        )
    )


def test_authorization_url_has_form_params_and_signed_state() -> None:
    service = _service()

    response = service.build_authorization_url(user_id="coach-user-1")
    url = urlparse(response.redirect_url)
    query = parse_qs(url.query)

    assert url.netloc == "www.strava.com"
    assert url.path == "/oauth/authorize"
    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["strava-123"]
    assert query["redirect_uri"] == ["https://coach.nigels.dev/api/strava/callback"]
    assert query["scope"] == ["read,activity:read"]
    assert service.validate_state(query["state"][0]).user_id == "coach-user-1"


def test_authorization_url_raises_when_integration_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("backend.services.strava.settings.strava_integration_enabled", False)
    with pytest.raises(StravaConfigurationError):
        _service().build_authorization_url(user_id="coach-user-1")


def test_status_reports_disconnected_when_integration_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Flag-off is the landing posture: status must read as a clean "not
    # connected" (no repo call, no error) rather than raising a 503.
    monkeypatch.setattr("backend.services.strava.settings.strava_integration_enabled", False)

    class ExplodingRepo:
        def get_active_connection(self, user_id: str) -> None:
            raise AssertionError("must not touch the repo while disabled")

    service = StravaOAuthService(repository=ExplodingRepo())  # type: ignore[arg-type]
    status = service.get_status("coach-user-1")
    assert status.connected is False


def test_state_validation_rejects_expired() -> None:
    service = _service()
    expired = service.create_state(user_id="coach-user-1", ttl_seconds=-1)
    with pytest.raises(StravaStateError):
        service.validate_state(expired)


def test_token_cipher_uses_strava_secret_and_round_trips() -> None:
    cipher = TokenCipher("strava-enc-secret")
    ciphertext = cipher.encrypt("strava-access-token")
    assert "strava-access-token" not in ciphertext
    assert cipher.decrypt(ciphertext) == "strava-access-token"


@pytest.mark.asyncio
async def test_exchange_stores_encrypted_access_and_refresh() -> None:
    repo = InMemoryStravaRepository()
    token_bodies: list[dict[str, list[str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        token_bodies.append(parse_qs(request.content.decode()))
        return httpx.Response(
            200,
            json={
                "token_type": "Bearer",
                "access_token": "strava-access",
                "refresh_token": "strava-refresh",
                "expires_at": int((datetime.now(UTC) + timedelta(hours=6)).timestamp()),
                "athlete": {"id": 135168, "firstname": "Nigel", "lastname": "S"},
            },
        )

    service = _service(repo, transport=httpx.MockTransport(handler))
    state = service.create_state(user_id="coach-user-1")

    status = await service.exchange_code_for_connection(
        code="strava-code", scope="read,activity:read", state=state
    )

    assert token_bodies[0]["grant_type"] == ["authorization_code"]
    assert token_bodies[0]["code"] == ["strava-code"]
    assert status.connected is True
    assert status.strava_athlete_id == 135168
    assert status.strava_athlete_name == "Nigel S"
    assert status.authorization_version == "2026-07-21"
    dumped = repo.rows[0].model_dump_json()
    assert "strava-access" not in dumped
    assert "strava-refresh" not in dumped


@pytest.mark.asyncio
async def test_exchange_rejects_missing_activity_scope() -> None:
    repo = InMemoryStravaRepository()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "token_type": "Bearer",
                "access_token": "strava-access",
                "refresh_token": "strava-refresh",
                "expires_at": int((datetime.now(UTC) + timedelta(hours=6)).timestamp()),
                "athlete": {"id": 135168},
            },
        )

    service = _service(repo, transport=httpx.MockTransport(handler))
    state = service.create_state(user_id="coach-user-1")

    with pytest.raises(StravaScopeError):
        await service.exchange_code_for_connection(code="c", scope="read", state=state)
    assert repo.rows == []


@pytest.mark.asyncio
async def test_resolve_auth_refreshes_when_token_near_expiry() -> None:
    repo = InMemoryStravaRepository()
    seeded = _seed_connection(repo, expires_at=datetime.now(UTC) + timedelta(minutes=30))
    new_expiry = int((datetime.now(UTC) + timedelta(hours=6)).timestamp())
    refresh_bodies: list[dict[str, list[str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        refresh_bodies.append(parse_qs(request.content.decode()))
        return httpx.Response(
            200,
            json={
                "token_type": "Bearer",
                "access_token": "rotated-access",
                "refresh_token": "rotated-refresh",
                "expires_at": new_expiry,
            },
        )

    service = _service(repo, transport=httpx.MockTransport(handler))
    auth = await service.resolve_auth("coach-user-1")

    assert refresh_bodies[0]["grant_type"] == ["refresh_token"]
    assert auth.access_token == "rotated-access"
    # The rotated refresh token is authoritative and persisted encrypted.
    cipher = TokenCipher("strava-enc-secret")
    assert cipher.decrypt(repo.rows[0].refresh_token_ciphertext) == "rotated-refresh"
    assert repo.rows[0].id == seeded.id


@pytest.mark.asyncio
async def test_resolve_auth_uses_stored_token_when_fresh() -> None:
    repo = InMemoryStravaRepository()
    _seed_connection(repo, expires_at=datetime.now(UTC) + timedelta(hours=5))

    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not refresh a still-fresh token")

    service = _service(repo, transport=httpx.MockTransport(handler))
    auth = await service.resolve_auth("coach-user-1")
    assert auth.access_token == "access-1"


@pytest.mark.asyncio
async def test_refresh_rejection_marks_reconnect_required() -> None:
    repo = InMemoryStravaRepository()
    _seed_connection(repo, expires_at=datetime.now(UTC) - timedelta(minutes=5))

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"message": "Bad Request", "errors": []})

    service = _service(repo, transport=httpx.MockTransport(handler))
    with pytest.raises(StravaReconnectRequiredError):
        await service.resolve_auth("coach-user-1")


@pytest.mark.asyncio
async def test_disconnect_revokes_remote_and_local() -> None:
    repo = InMemoryStravaRepository()
    _seed_connection(repo, expires_at=datetime.now(UTC) + timedelta(hours=5))
    deauth_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        deauth_calls.append(str(request.url))
        return httpx.Response(200, json={"access_token": "access-1"})

    service = _service(repo, transport=httpx.MockTransport(handler))
    result = await service.disconnect("coach-user-1")

    assert result.remote_revoked is True
    assert result.status.connected is False
    assert deauth_calls and deauth_calls[0].endswith("/oauth/deauthorize")
    assert repo.get_active_connection("coach-user-1") is None


@pytest.mark.asyncio
async def test_disconnect_pending_when_remote_revocation_fails() -> None:
    repo = InMemoryStravaRepository()
    _seed_connection(repo, expires_at=datetime.now(UTC) + timedelta(hours=5))

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    service = _service(repo, transport=httpx.MockTransport(handler))
    result = await service.disconnect("coach-user-1")

    assert result.remote_revoked is False
    assert result.status.disconnect_pending is True
    # Credentials retained so a retry can still revoke.
    assert repo.get_active_connection("coach-user-1") is not None
