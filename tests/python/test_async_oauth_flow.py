"""Exercise FastAPI -> AuthService -> async PostgREST without replacing the service."""

import asyncio
import base64
from collections.abc import AsyncIterator
from hashlib import sha256
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from postgrest.exceptions import APIError as PostgRESTAPIError

import api.index as api_index
from backend.models.auth import BrowserSessionContext
from backend.repos.async_oauth_repo import AsyncOAuthRepository
from backend.services.auth import AuthService
from tests.python.test_async_oauth_repo import (
    CODES_TABLE,
    GRANTS_TABLE,
    TOKENS_TABLE,
    AsyncFakeSupabaseClient,
)


@pytest.fixture
async def async_oauth_client(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[httpx.AsyncClient, AsyncFakeSupabaseClient]]:
    database = AsyncFakeSupabaseClient()
    service = AuthService(oauth_repo=AsyncOAuthRepository(client=database))
    monkeypatch.setattr(api_index, "auth_service", service)
    cookie = service.create_browser_session_token(BrowserSessionContext(user_id="user-1"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api_index.app),
        base_url="http://testserver",
        cookies={"coach_browser_session": cookie},
    ) as client:
        yield client, database
    await service.aclose()


async def test_consent_code_refresh_bearer_and_revoke_use_async_repository(
    async_oauth_client: tuple[httpx.AsyncClient, AsyncFakeSupabaseClient],
) -> None:
    client, database = async_oauth_client
    verifier = "verifier"
    challenge = base64.urlsafe_b64encode(sha256(verifier.encode()).digest()).decode().rstrip("=")
    authorize = {
        "client_id": "client-1",
        "redirect_uri": "https://example.com/callback",
        "scope": "profile:read",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "state-1",
    }
    missing = await client.get("/api/oauth/authorize", params=authorize)
    assert missing.status_code == 302
    assert "/consent?" in missing.headers["location"]

    approved = await client.post(
        "/api/oauth/authorize/decision", data={**authorize, "decision": "approve"}
    )
    assert approved.status_code == 302
    assert len(database.tables[GRANTS_TABLE]) == 1

    # Reauthorization must read the persisted grant and issue a code without consent.
    authorized = await client.get("/api/oauth/authorize", params=authorize)
    assert authorized.status_code == 302
    redirect = parse_qs(urlparse(authorized.headers["location"]).query)
    assert redirect["state"] == ["state-1"]
    exchange = {
        "grant_type": "authorization_code",
        "client_id": "client-1",
        "redirect_uri": authorize["redirect_uri"],
        "code": redirect["code"][0],
        "code_verifier": verifier,
    }
    token = await client.post("/api/oauth/token", json=exchange)
    assert token.status_code == 200
    assert database.tables[CODES_TABLE][-1]["consumed_at"] is not None
    assert (await client.post("/api/oauth/token", json=exchange)).status_code == 400
    headers = {"Authorization": f"Bearer {token.json()['access_token']}"}
    assert (await client.post("/api/mcp", headers=headers)).status_code == 200

    refresh = {
        "grant_type": "refresh_token",
        "client_id": "client-1",
        "refresh_token": token.json()["refresh_token"],
    }
    rotated = await client.post("/api/oauth/token", json=refresh)
    assert rotated.status_code == 200
    assert rotated.json()["refresh_token"] != refresh["refresh_token"]
    assert (await client.post("/api/oauth/token", json=refresh)).status_code == 400

    revoked = await client.post("/api/oauth/revoke", json={"token": rotated.json()["access_token"]})
    assert revoked.json() == {"revoked": True}
    assert all(row["revoked_at"] is not None for row in database.tables[TOKENS_TABLE])
    assert (await client.post("/api/mcp", headers=headers)).status_code == 401
    assert (
        await client.post(
            "/api/oauth/token", json={**refresh, "refresh_token": rotated.json()["refresh_token"]}
        )
    ).status_code == 400


@pytest.mark.parametrize("code,expected_status", [("23505", 409), ("23503", 422), ("504", 503)])
async def test_browser_token_preserves_centralized_database_error_contract(
    async_oauth_client: tuple[httpx.AsyncClient, AsyncFakeSupabaseClient],
    code: str,
    expected_status: int,
) -> None:
    client, database = async_oauth_client
    error = PostgRESTAPIError({"message": "unavailable", "code": code})
    for _ in range(2):
        database.fail_next(GRANTS_TABLE, "select", error)
    response = await client.post("/api/oauth/browser-token")
    assert response.status_code == expected_status
    assert "set-cookie" not in response.headers
    assert database.operations[(GRANTS_TABLE, "select")] == (2 if code == "504" else 1)


async def test_ambiguous_browser_grant_commit_retries_without_blocking(
    async_oauth_client: tuple[httpx.AsyncClient, AsyncFakeSupabaseClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, database = async_oauth_client
    database.fail_next(
        GRANTS_TABLE,
        "insert",
        PostgRESTAPIError({"message": "gateway timeout", "code": "504"}),
        after_operation=True,
    )
    retrying = asyncio.Event()
    release = asyncio.Event()

    async def pause_retry(_delay: float) -> None:
        retrying.set()
        await release.wait()

    sleep = AsyncMock(side_effect=pause_retry)
    monkeypatch.setattr("backend.repos.async_oauth_repo.asyncio.sleep", sleep)
    pending = asyncio.create_task(client.post("/api/oauth/browser-token"))
    try:
        await asyncio.wait_for(retrying.wait(), timeout=1)
        assert (await asyncio.wait_for(client.get("/health"), timeout=1)).status_code == 200
        assert not pending.done()
    finally:
        release.set()
        response = await pending
    assert response.status_code == 200
    assert "set-cookie" in response.headers
    sleep.assert_awaited_once_with(0.25)
    assert len(database.tables[GRANTS_TABLE]) == 1
    assert database.operations[(GRANTS_TABLE, "insert")] == 1
    assert database.operations[(GRANTS_TABLE, "select")] == 2


async def test_browser_session_verification_uses_async_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api_index.settings, "supabase_url", "https://supabase.example.com")
    monkeypatch.setattr(api_index.settings, "supabase_service_role_key", "test-service-key")
    database = AsyncFakeSupabaseClient()
    service = AuthService(oauth_repo=AsyncOAuthRepository(client=database))
    monkeypatch.setattr(api_index, "auth_service", service)
    # Keep the ASGI client independent from the patched upstream HTTP client factory.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api_index.app), base_url="http://testserver"
    ) as client:
        upstream = AsyncMock()
        upstream.__aenter__.return_value = upstream
        upstream.get.return_value = httpx.Response(
            200,
            json={"id": "user-1", "email": "athlete@example.com"},
            request=httpx.Request("GET", "https://supabase.example.com/auth/v1/user"),
        )
        # AsyncClient construction is synchronous; only its context/I/O is awaited.
        factory = Mock(return_value=upstream)
        monkeypatch.setattr("backend.services.auth.httpx.AsyncClient", factory)
        response = await client.post(
            "/api/oauth/browser-session", json={"access_token": "supabase-access-token"}
        )
        assert response.status_code == 200
        factory.assert_called_once_with(timeout=10.0)
        upstream.get.assert_awaited_once_with(
            "https://supabase.example.com/auth/v1/user",
            headers={"Authorization": "Bearer supabase-access-token", "apikey": "test-service-key"},
        )
        upstream.__aexit__.assert_awaited_once()
    await service.aclose()


async def test_lifespan_closes_oauth_connections_even_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = AsyncFakeSupabaseClient()
    monkeypatch.setattr(
        api_index, "auth_service", AuthService(AsyncOAuthRepository(client=database))
    )
    with pytest.raises(RuntimeError, match="application failed"):
        async with api_index.lifespan(api_index.app):
            raise RuntimeError("application failed")
    assert database.postgrest.closed


async def test_unconfigured_oauth_repository_can_close(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_index.settings, "supabase_url", "")
    monkeypatch.setattr(api_index.settings, "supabase_service_role_key", "")
    await AuthService().aclose()
