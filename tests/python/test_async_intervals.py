"""Async Intervals persistence contracts and real service/repository route integration."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from postgrest.exceptions import APIError as PostgRESTAPIError

import api.index as api_index
from backend.config import settings
from backend.models.auth import UserContext
from backend.models.intervals import IntervalsConnectionCreate
from backend.repos.intervals_repo import IntervalsRepository, IntervalsRepositoryNotConfiguredError
from backend.services.intervals import IntervalsOAuthService, TokenCipher
from tests.python.test_intervals_sync import InMemoryActivityRepository


def _database(data: object) -> tuple[Mock, Mock]:
    query = Mock()
    for operation in ("select", "eq", "is_", "order", "limit", "update"):
        getattr(query, operation).return_value = query
    query.execute = AsyncMock(return_value=SimpleNamespace(data=data))
    database = Mock()
    database.table.return_value = query
    database.rpc.return_value = query
    database.postgrest.aclose = AsyncMock()
    return database, query


def _row() -> dict[str, object]:
    now = datetime.now(UTC).isoformat()
    return {
        "id": "connection-1",
        "user_id": "user-1",
        "intervals_athlete_id": "i123",
        "intervals_athlete_name": "Athlete",
        "scopes": ["ACTIVITY:READ"],
        "access_token_ciphertext": TokenCipher("test-encryption-key").encrypt("upstream-token"),
        "token_type": "Bearer",
        "connected_at": now,
        "updated_at": now,
        "revoked_at": None,
    }


@pytest.mark.parametrize("found", [True, False])
async def test_connection_read_awaits_user_scoped_query(found: bool) -> None:
    database, query = _database([_row()] if found else [])
    repo = IntervalsRepository(database)
    record = await repo.get_active_connection("user-1")
    assert (record is not None) == found
    database.table.assert_called_once_with("intervals_connections")
    query.eq.assert_called_once_with("user_id", "user-1")
    query.is_.assert_called_once_with("revoked_at", "null")
    query.order.assert_called_once_with("connected_at", desc=True)
    query.limit.assert_called_once_with(1)
    query.execute.assert_awaited_once()
    await repo.aclose()


@pytest.mark.parametrize("found", [True, False])
async def test_connection_revoke_awaits_user_scoped_query(found: bool) -> None:
    database, query = _database([_row()] if found else [])
    repo = IntervalsRepository(database)
    assert await repo.revoke_active_connection("user-1") is found
    query.eq.assert_called_once_with("user_id", "user-1")
    query.is_.assert_called_once_with("revoked_at", "null")
    payload = query.update.call_args.args[0]
    assert payload["revoked_at"] == payload["updated_at"]
    query.execute.assert_awaited_once()
    await repo.aclose()


async def test_unconfigured_queries_fail_but_cleanup_is_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "supabase_url", "")
    monkeypatch.setattr(settings, "supabase_service_role_key", "")
    repo = IntervalsRepository()
    with pytest.raises(IntervalsRepositoryNotConfiguredError):
        await repo.get_active_connection("user-1")
    with pytest.raises(IntervalsRepositoryNotConfiguredError):
        await repo.revoke_active_connection("user-1")
    with pytest.raises(IntervalsRepositoryNotConfiguredError):
        await repo.replace_connection(
            IntervalsConnectionCreate(
                user_id="user-1", intervals_athlete_id="i123", access_token_ciphertext="encrypted"
            )
        )
    await repo.aclose()


def test_repository_constructs_async_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "supabase_url", "https://supabase.example.com")
    monkeypatch.setattr(settings, "supabase_service_role_key", "test-service-key")
    database, _query = _database([])
    factory = Mock(return_value=database)
    monkeypatch.setattr("backend.repos.intervals_repo.SupabaseAsyncClient", factory)
    repo = IntervalsRepository()
    assert repo._require_client() is database
    factory.assert_called_once_with("https://supabase.example.com", "test-service-key")


@pytest.fixture
def configured_async_intervals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "intervals_client_id", "test-client")
    monkeypatch.setattr(settings, "intervals_client_secret", "test-client-secret")
    monkeypatch.setattr(settings, "intervals_token_encryption_secret", "test-encryption-key")
    monkeypatch.setattr(settings, "intervals_dev_api_key", "")
    monkeypatch.setattr(settings, "intervals_dev_athlete_id", "")
    monkeypatch.delenv("VERCEL_URL", raising=False)
    monkeypatch.setitem(
        api_index.app.dependency_overrides,
        api_index.require_user_context,
        lambda: UserContext(user_id="user-1"),
    )


def _service(database: Mock) -> IntervalsOAuthService:
    def upstream(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "token_type": "Bearer",
                    "access_token": "upstream-token",
                    "scope": "ACTIVITY:READ",
                    "athlete": {"id": "i123", "name": "Athlete"},
                },
            )
        return httpx.Response(200, json=[])

    return IntervalsOAuthService(
        repository=IntervalsRepository(database),
        http_client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(upstream)),
    )


@pytest.mark.usefixtures("configured_async_intervals")
@pytest.mark.parametrize("flow", ["status", "disconnect", "sync", "callback"])
async def test_routes_await_postgrest_without_stalling_other_requests(
    monkeypatch: pytest.MonkeyPatch,
    flow: str,
) -> None:
    database, query = _database(_row() if flow == "callback" else [_row()])
    service = _service(database)
    monkeypatch.setattr(api_index, "intervals_service", service)
    monkeypatch.setattr(api_index, "repo", InMemoryActivityRepository())
    entered = asyncio.Event()
    release = asyncio.Event()
    response_data = query.execute.return_value

    async def pending_io() -> Any:
        entered.set()
        await release.wait()
        return response_data

    query.execute.side_effect = pending_io
    endpoints = {
        "status": ("GET", "/api/intervals/status"),
        "disconnect": ("DELETE", "/api/intervals/connection"),
        "sync": ("POST", "/api/intervals/sync"),
        "callback": ("GET", "/api/intervals/callback"),
    }
    method, path = endpoints[flow]
    kwargs: dict[str, Any] = {}
    if flow == "callback":
        kwargs["params"] = {"code": "test-code", "state": service.create_state(user_id="user-1")}
    if flow == "sync":
        kwargs["json"] = {"days": 14}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api_index.app), base_url="http://testserver"
    ) as client:
        pending = asyncio.create_task(client.request(method, path, **kwargs))
        try:
            await asyncio.wait_for(entered.wait(), timeout=1)
            assert (await asyncio.wait_for(client.get("/health"), timeout=1)).status_code == 200
            assert not pending.done()
        finally:
            release.set()
            response = await pending
    assert response.status_code == (302 if flow == "callback" else 200)
    query.execute.assert_awaited_once()
    if flow == "callback":
        assert response.headers["location"].endswith("?intervals=connected")
        rpc_name, params = database.rpc.call_args.args
        assert rpc_name == "replace_intervals_connection"
        assert params["p_user_id"] == "user-1"
        assert "upstream-token" not in params["p_access_token_ciphertext"]
    else:
        query.eq.assert_called_once_with("user_id", "user-1")
    await service.aclose()
    database.postgrest.aclose.assert_awaited_once()


@pytest.mark.usefixtures("configured_async_intervals")
@pytest.mark.parametrize("flow", ["status", "disconnect", "callback"])
@pytest.mark.parametrize("code,expected", [("23505", 409), ("23503", 422), ("504", 503)])
async def test_routes_preserve_centralized_postgrest_error_mapping(
    monkeypatch: pytest.MonkeyPatch,
    flow: str,
    code: str,
    expected: int,
) -> None:
    database, query = _database([])
    query.execute.side_effect = PostgRESTAPIError({"message": "database rejected", "code": code})
    service = _service(database)
    monkeypatch.setattr(api_index, "intervals_service", service)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api_index.app), base_url="http://testserver"
    ) as client:
        if flow == "callback":
            response = await client.get(
                "/api/intervals/callback",
                params={"code": "test-code", "state": service.create_state(user_id="user-1")},
            )
        elif flow == "disconnect":
            response = await client.delete("/api/intervals/connection")
        else:
            response = await client.get("/api/intervals/status")
    assert response.status_code == expected
    query.execute.assert_awaited_once()
    await service.aclose()


@pytest.mark.parametrize("failing_service", ["auth_service", "intervals_service"])
async def test_lifespan_attempts_both_pool_cleanups_when_one_fails(
    monkeypatch: pytest.MonkeyPatch, failing_service: str
) -> None:
    auth = Mock(aclose=AsyncMock())
    intervals = Mock(aclose=AsyncMock())
    services = {"auth_service": auth, "intervals_service": intervals}
    services[failing_service].aclose.side_effect = RuntimeError("pool cleanup failed")
    for name, service in services.items():
        monkeypatch.setattr(api_index, name, service)

    with pytest.raises(RuntimeError, match="pool cleanup failed"):
        async with api_index.lifespan(api_index.app):
            pass

    auth.aclose.assert_awaited_once()
    intervals.aclose.assert_awaited_once()


async def test_lifespan_closes_intervals_pool_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    database, _query = _database([])
    monkeypatch.setattr(api_index, "intervals_service", _service(database))
    with pytest.raises(RuntimeError, match="application failed"):
        async with api_index.lifespan(api_index.app):
            raise RuntimeError("application failed")
    database.postgrest.aclose.assert_awaited_once()
