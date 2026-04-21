"""
Auth flow contract tests — browser-session + browser-token endpoints.

Run this suite specifically:
    uv run pytest tests/python/test_auth_flow.py -v
"""

from collections.abc import AsyncGenerator
from typing import Any, cast

import pytest
from httpx import ASGITransport, AsyncClient

import api.index as api_index
from backend.models.auth import BrowserSessionContext, BrowserTokenResponse
from tests.python.test_api import FakeAuthService, InMemoryOAuthRepository


class AuthFlowFakeService(FakeAuthService):
    """Raises ValueError for bad tokens, matching what the real Supabase HTTP call raises."""

    def create_browser_session(self, supabase_access_token: str) -> BrowserSessionContext:
        if supabase_access_token != "supabase-access-token":
            raise ValueError("Supabase rejected the access token.")
        return BrowserSessionContext(user_id="athlete-1", email="athlete@example.com")


@pytest.fixture
async def auth_client() -> AsyncGenerator[tuple[AsyncClient, AuthFlowFakeService], None]:
    fake_service = AuthFlowFakeService(oauth_repo=cast(Any, InMemoryOAuthRepository()))
    original = api_index.auth_service
    api_index.auth_service = fake_service
    transport = ASGITransport(app=api_index.app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client, fake_service
    finally:
        api_index.auth_service = original


@pytest.mark.asyncio
async def test_browser_session_sets_httponly_cookie(
    auth_client: tuple[AsyncClient, AuthFlowFakeService],
) -> None:
    client, _ = auth_client
    response = await client.post(
        "/api/oauth/browser-session",
        json={"access_token": "supabase-access-token"},
    )
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]


@pytest.mark.asyncio
async def test_browser_session_cookie_has_correct_name(
    auth_client: tuple[AsyncClient, AuthFlowFakeService],
) -> None:
    client, _ = auth_client
    response = await client.post(
        "/api/oauth/browser-session",
        json={"access_token": "supabase-access-token"},
    )
    assert response.status_code == 200
    assert "coach_browser_session=" in response.headers["set-cookie"]


@pytest.mark.asyncio
async def test_browser_session_invalid_token_returns_401(
    auth_client: tuple[AsyncClient, AuthFlowFakeService],
) -> None:
    client, _ = auth_client
    response = await client.post(
        "/api/oauth/browser-session",
        json={"access_token": "bad-token"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_browser_session_missing_body_returns_422(
    auth_client: tuple[AsyncClient, AuthFlowFakeService],
) -> None:
    client, _ = auth_client
    response = await client.post("/api/oauth/browser-session", json={})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_otp_login_round_trip(
    auth_client: tuple[AsyncClient, AuthFlowFakeService],
) -> None:
    """Full contract: browser-session sets cookie → browser-token reads it → user confirmed."""
    client, _ = auth_client

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
    assert token_response.status_code == 200
    payload = BrowserTokenResponse.model_validate(token_response.json())
    assert payload.user_id == "athlete-1"
    assert len(payload.scopes) > 0


@pytest.mark.asyncio
async def test_browser_token_with_garbage_cookie_returns_401(
    auth_client: tuple[AsyncClient, AuthFlowFakeService],
) -> None:
    client, _ = auth_client
    response = await client.post(
        "/api/oauth/browser-token",
        cookies={"coach_browser_session": "not-a-valid-jwt"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_browser_session_logout_clears_cookie(
    auth_client: tuple[AsyncClient, AuthFlowFakeService],
) -> None:
    client, _ = auth_client
    response = await client.post("/api/oauth/browser-session/logout", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login?return_to=/"
    cookie_header = response.headers["set-cookie"]
    assert "coach_browser_session=" in cookie_header
    assert "Max-Age=0" in cookie_header
