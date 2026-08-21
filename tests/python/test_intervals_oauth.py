import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

import api.index as api_index
from backend.models.auth import UserContext
from backend.models.intervals import IntervalsConnectionCreate, IntervalsConnectionRecord
from backend.services.intervals import (
    IntervalsOAuthExchangeError,
    IntervalsOAuthService,
    IntervalsStateError,
    TokenCipher,
)

_DEPENDENCY_OVERRIDE_MISSING = object()


def _override_require_user_context(user_context: UserContext) -> Callable[[], None]:
    previous = api_index.app.dependency_overrides.get(
        api_index.require_user_context,
        _DEPENDENCY_OVERRIDE_MISSING,
    )
    api_index.app.dependency_overrides[api_index.require_user_context] = lambda: user_context

    def restore() -> None:
        if previous is _DEPENDENCY_OVERRIDE_MISSING:
            api_index.app.dependency_overrides.pop(api_index.require_user_context, None)
        else:
            api_index.app.dependency_overrides[api_index.require_user_context] = cast(
                Callable[..., Any],
                previous,
            )

    return restore


class InMemoryIntervalsRepository:
    def __init__(self) -> None:
        self.rows: list[IntervalsConnectionRecord] = []

    def get_active_connection(self, user_id: str) -> IntervalsConnectionRecord | None:
        for row in reversed(self.rows):
            if row.user_id == user_id and row.revoked_at is None:
                return row
        return None

    def replace_connection(
        self, connection: IntervalsConnectionCreate
    ) -> IntervalsConnectionRecord:
        now = datetime.now(UTC)
        for row in self.rows:
            if row.user_id == connection.user_id and row.revoked_at is None:
                row.revoked_at = now
        row = IntervalsConnectionRecord(
            id=f"connection-{len(self.rows) + 1}",
            user_id=connection.user_id,
            intervals_athlete_id=connection.intervals_athlete_id,
            intervals_athlete_name=connection.intervals_athlete_name,
            scopes=connection.scopes,
            access_token_ciphertext=connection.access_token_ciphertext,
            token_type=connection.token_type,
            connected_at=now,
            updated_at=now,
            revoked_at=None,
        )
        self.rows.append(row)
        return row

    def revoke_active_connection(self, user_id: str) -> bool:
        row = self.get_active_connection(user_id)
        if row is None:
            return False
        row.revoked_at = datetime.now(UTC)
        return True


@pytest.fixture
def configured_intervals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backend.services.intervals.settings.app_base_url",
        "https://coach.nigels.dev",
    )
    monkeypatch.setattr(
        "backend.services.intervals.settings.app_jwt_secret",
        "test-jwt-secret-with-at-least-thirty-two-bytes",
    )
    monkeypatch.setattr("backend.services.intervals.settings.intervals_client_id", "client-123")
    monkeypatch.setattr(
        "backend.services.intervals.settings.intervals_client_secret",
        "client-secret-123",
    )
    monkeypatch.setattr(
        "backend.services.intervals.settings.intervals_token_encryption_secret",
        "encryption-secret-123",
    )
    monkeypatch.setattr("backend.services.intervals.settings.intervals_dev_api_key", "")
    monkeypatch.setattr("backend.services.intervals.settings.intervals_dev_athlete_id", "")
    monkeypatch.delenv("VERCEL_URL", raising=False)


pytestmark = pytest.mark.usefixtures("configured_intervals")


def _service(
    repo: InMemoryIntervalsRepository | None = None,
    *,
    transport: httpx.MockTransport | None = None,
) -> IntervalsOAuthService:
    return IntervalsOAuthService(
        repository=repo or InMemoryIntervalsRepository(),
        http_client_factory=(
            (lambda: httpx.AsyncClient(transport=transport)) if transport is not None else None
        ),
    )


def test_authorization_url_contains_redirect_scope_and_signed_state() -> None:
    service = _service()

    response = service.build_authorization_url(user_id="coach-user-1")
    url = urlparse(response.redirect_url)
    query = parse_qs(url.query)

    assert url.scheme == "https"
    assert url.netloc == "intervals.icu"
    assert url.path == "/oauth/authorize"
    assert query["client_id"] == ["client-123"]
    assert query["redirect_uri"] == ["https://coach.nigels.dev/api/intervals/callback"]
    assert query["scope"] == ["ACTIVITY:READ,WELLNESS:READ,CALENDAR:READ"]
    assert service.validate_state(query["state"][0], expected_user_id="coach-user-1").user_id == (
        "coach-user-1"
    )


def test_state_validation_rejects_expired_and_wrong_user() -> None:
    service = _service()

    expired = service.create_state(user_id="coach-user-1", ttl_seconds=-1)
    valid_for_other_user = service.create_state(user_id="coach-user-2")

    with pytest.raises(IntervalsStateError):
        service.validate_state(expired, expected_user_id="coach-user-1")
    with pytest.raises(IntervalsStateError):
        service.validate_state(valid_for_other_user, expected_user_id="coach-user-1")


def test_token_cipher_encrypts_without_plaintext_and_round_trips() -> None:
    cipher = TokenCipher("encryption-secret-123")

    ciphertext = cipher.encrypt("intervals-access-token")

    assert "intervals-access-token" not in ciphertext
    assert cipher.decrypt(ciphertext) == "intervals-access-token"


@pytest.mark.asyncio
async def test_exchange_code_stores_encrypted_token_and_returns_connection_status() -> None:
    repo = InMemoryIntervalsRepository()
    token_requests: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        token_requests.append(json.loads(request.content.decode()))
        return httpx.Response(
            200,
            json={
                "token_type": "Bearer",
                "access_token": "intervals-access-token",
                "scope": "ACTIVITY:READ,WELLNESS:READ,CALENDAR:READ",
                "athlete": {"id": "i135168", "name": "Nigel"},
            },
        )

    service = _service(repo, transport=httpx.MockTransport(handler))
    state = service.create_state(user_id="coach-user-1")

    status = await service.exchange_code_for_connection(code="intervals-code", state=state)

    assert token_requests == [
        {
            "grant_type": "authorization_code",
            "code": "intervals-code",
            "client_id": "client-123",
            "client_secret": "client-secret-123",
            "redirect_uri": "https://coach.nigels.dev/api/intervals/callback",
        }
    ]
    assert status.connected is True
    assert status.intervals_athlete_id == "i135168"
    assert status.intervals_athlete_name == "Nigel"
    assert repo.rows[0].access_token_ciphertext != "intervals-access-token"
    assert "intervals-access-token" not in repo.rows[0].model_dump_json()


@pytest.mark.asyncio
async def test_exchange_code_rejects_missing_athlete_id() -> None:
    repo = InMemoryIntervalsRepository()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "token_type": "Bearer",
                "access_token": "intervals-access-token",
                "scope": "ACTIVITY:READ",
                "athlete": {"id": "", "name": "Nigel"},
            },
        )

    service = _service(repo, transport=httpx.MockTransport(handler))
    state = service.create_state(user_id="coach-user-1")

    with pytest.raises(
        IntervalsOAuthExchangeError,
        match="Intervals.icu authorization could not be completed",
    ):
        await service.exchange_code_for_connection(code="intervals-code", state=state)
    assert repo.rows == []


@pytest.mark.asyncio
async def test_authorize_endpoint_returns_intervals_redirect() -> None:
    original_service = api_index.intervals_service
    api_index.intervals_service = _service()
    restore_override = _override_require_user_context(
        UserContext(user_id="coach-user-1", scopes=["profile:read"])
    )
    try:
        transport = ASGITransport(app=api_index.app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post("/api/intervals/authorize")
    finally:
        restore_override()
        api_index.intervals_service = original_service

    assert response.status_code == 200
    redirect_url = response.json()["redirect_url"]
    assert redirect_url.startswith("https://intervals.icu/oauth/authorize?")


@pytest.mark.asyncio
async def test_authorize_endpoint_returns_bounded_error_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("backend.services.intervals.settings.intervals_client_id", "")
    original_service = api_index.intervals_service
    api_index.intervals_service = _service()
    restore_override = _override_require_user_context(UserContext(user_id="coach-user-1"))
    try:
        transport = ASGITransport(app=api_index.app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post("/api/intervals/authorize")
    finally:
        restore_override()
        api_index.intervals_service = original_service

    assert response.status_code == 503
    assert response.json()["detail"] == "Intervals.icu integration is not configured yet."


@pytest.mark.asyncio
async def test_callback_denial_redirects_to_profile_error() -> None:
    original_service = api_index.intervals_service
    api_index.intervals_service = _service()
    try:
        transport = ASGITransport(app=api_index.app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get(
                "/api/intervals/callback?error=access_denied",
                follow_redirects=False,
            )
    finally:
        api_index.intervals_service = original_service

    assert response.status_code == 302
    assert response.headers["location"] == "https://coach.nigels.dev/profile?intervals=error"


@pytest.mark.asyncio
async def test_callback_redirects_normalize_base_url_trailing_slash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "api.index.settings.app_base_url",
        "https://coach.nigels.dev/",
    )
    original_service = api_index.intervals_service
    api_index.intervals_service = _service()
    try:
        transport = ASGITransport(app=api_index.app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get(
                "/api/intervals/callback?error=access_denied",
                follow_redirects=False,
            )
    finally:
        api_index.intervals_service = original_service

    assert response.status_code == 302
    assert response.headers["location"] == "https://coach.nigels.dev/profile?intervals=error"


@pytest.mark.asyncio
async def test_status_and_disconnect_are_user_scoped() -> None:
    repo = InMemoryIntervalsRepository()
    repo.replace_connection(
        IntervalsConnectionCreate(
            user_id="coach-user-1",
            intervals_athlete_id="i135168",
            intervals_athlete_name="Nigel",
            scopes=["ACTIVITY:READ"],
            access_token_ciphertext="ciphertext",
            token_type="Bearer",
        )
    )
    original_service = api_index.intervals_service
    api_index.intervals_service = _service(repo)
    restore_override = _override_require_user_context(UserContext(user_id="coach-user-1"))
    try:
        transport = ASGITransport(app=api_index.app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            status_response = await client.get("/api/intervals/status")
            disconnect_response = await client.delete("/api/intervals/connection")
            disconnected_status_response = await client.get("/api/intervals/status")
    finally:
        restore_override()
        api_index.intervals_service = original_service

    assert status_response.status_code == 200
    assert status_response.json()["connected"] is True
    assert status_response.json()["intervals_athlete_id"] == "i135168"
    assert "access_token" not in status_response.text
    assert disconnect_response.status_code == 200
    assert disconnect_response.json()["connected"] is False
    assert disconnected_status_response.json()["connected"] is False


@pytest.mark.asyncio
async def test_callback_success_exchanges_code_and_redirects_connected() -> None:
    repo = InMemoryIntervalsRepository()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "token_type": "Bearer",
                "access_token": "intervals-access-token",
                "scope": "ACTIVITY:READ",
                "athlete": {"id": "i135168", "name": "Nigel"},
            },
        )

    service = _service(repo, transport=httpx.MockTransport(handler))
    state = service.create_state(user_id="coach-user-1")
    original_service = api_index.intervals_service
    api_index.intervals_service = service
    try:
        transport = ASGITransport(app=api_index.app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get(
                f"/api/intervals/callback?code=intervals-code&state={state}",
                follow_redirects=False,
            )
    finally:
        api_index.intervals_service = original_service

    assert response.status_code == 302
    assert response.headers["location"] == "https://coach.nigels.dev/profile?intervals=connected"
    assert repo.get_active_connection("coach-user-1") is not None
