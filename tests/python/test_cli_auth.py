"""First-party CLI tokens: whoami, refresh rotation, revocation and the reserved client id.

The coach CLI only logs in through the RFC 8628 device flow (see test_device_flow.py). These
tests mint the same CLI grant + token bundle directly and exercise what happens afterwards,
through FastAPI -> AuthService -> the async OAuth repository on the in-memory PostgREST fake.
"""

import base64
from collections.abc import AsyncIterator
from dataclasses import dataclass
from hashlib import sha256

import httpx
import pytest

import api.index as api_index
from backend.models.auth import BrowserSessionContext
from backend.repos.async_oauth_repo import AsyncOAuthRepository
from backend.services.auth import CLI_CLIENT_ID, CLI_REDIRECT_URI, AuthService
from tests.python.test_async_oauth_repo import GRANTS_TABLE, AsyncFakeSupabaseClient

USER_ID = "00000000-0000-0000-0000-0000000000a1"


@dataclass
class CliAuthHarness:
    client: httpx.AsyncClient
    database: AsyncFakeSupabaseClient
    service: AuthService


@pytest.fixture
async def harness(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[CliAuthHarness]:
    database = AsyncFakeSupabaseClient()
    service = AuthService(oauth_repo=AsyncOAuthRepository(client=database))
    monkeypatch.setattr(api_index, "auth_service", service)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api_index.app), base_url="http://testserver"
    ) as client:
        yield CliAuthHarness(client=client, database=database, service=service)
    await service.aclose()


async def _login(harness: CliAuthHarness) -> dict[str, object]:
    """Mint the bundle an approved device-flow login yields: the durable CLI grant's tokens."""
    scopes = sorted(AuthService._supported_scopes)
    grant = await harness.service._oauth_repo.upsert_grant(
        user_id=USER_ID, client_id=CLI_CLIENT_ID, redirect_uri=CLI_REDIRECT_URI, scopes=scopes
    )
    bundle = await harness.service._issue_token_bundle(grant=grant, scopes=scopes)
    return bundle.model_dump(mode="json")


async def test_cli_tokens_authenticate_as_the_cli_client(harness: CliAuthHarness) -> None:
    tokens = await _login(harness)

    whoami = await harness.client.get("/api/auth/whoami", headers=_bearer(tokens["access_token"]))

    assert whoami.status_code == 200
    assert whoami.json() == {
        "user_id": USER_ID,
        "client_id": CLI_CLIENT_ID,
        "scopes": sorted(AuthService._supported_scopes),
    }


def _bearer(token: object) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_cli_refresh_token_rotates_through_the_oauth_token_endpoint(
    harness: CliAuthHarness,
) -> None:
    tokens = await _login(harness)
    refresh = {
        "grant_type": "refresh_token",
        "client_id": CLI_CLIENT_ID,
        "refresh_token": tokens["refresh_token"],
    }

    wrong_client = await harness.client.post(
        "/api/oauth/token", json={**refresh, "client_id": "client-1"}
    )
    assert wrong_client.status_code == 400

    rotated = await harness.client.post("/api/oauth/token", json=refresh)
    assert rotated.status_code == 200
    # Token responses must never be cached (RFC 6749 §5.1).
    assert rotated.headers["cache-control"] == "no-store"
    assert rotated.headers["pragma"] == "no-cache"
    body = rotated.json()
    assert body["refresh_token"] != tokens["refresh_token"]
    assert body["expires_in"] == 900
    assert body["scope"] == tokens["scope"]
    whoami = await harness.client.get("/api/auth/whoami", headers=_bearer(body["access_token"]))
    assert whoami.json()["client_id"] == CLI_CLIENT_ID

    # Rotation consumed the original refresh token.
    assert (await harness.client.post("/api/oauth/token", json=refresh)).status_code == 400


async def test_revoking_the_cli_refresh_token_blocks_refresh(harness: CliAuthHarness) -> None:
    tokens = await _login(harness)

    revoked = await harness.client.post(
        "/api/oauth/revoke",
        json={
            "token": tokens["refresh_token"],
            "token_type_hint": "refresh_token",
            "client_id": CLI_CLIENT_ID,
        },
    )

    assert revoked.json() == {"revoked": True}
    refresh = await harness.client.post(
        "/api/oauth/token",
        json={
            "grant_type": "refresh_token",
            "client_id": CLI_CLIENT_ID,
            "refresh_token": tokens["refresh_token"],
        },
    )
    assert refresh.status_code == 400


async def test_revoking_the_cli_access_token_kills_the_whole_grant(
    harness: CliAuthHarness,
) -> None:
    tokens = await _login(harness)

    revoked = await harness.client.post("/api/oauth/revoke", json={"token": tokens["access_token"]})

    assert revoked.json() == {"revoked": True}
    whoami = await harness.client.get("/api/auth/whoami", headers=_bearer(tokens["access_token"]))
    assert whoami.status_code == 401
    refresh = await harness.client.post(
        "/api/oauth/token",
        json={
            "grant_type": "refresh_token",
            "client_id": CLI_CLIENT_ID,
            "refresh_token": tokens["refresh_token"],
        },
    )
    assert refresh.status_code == 400


async def test_whoami_requires_a_bearer_token(harness: CliAuthHarness) -> None:
    assert (await harness.client.get("/api/auth/whoami")).status_code == 401
    invalid = await harness.client.get("/api/auth/whoami", headers=_bearer("not-a-jwt"))
    assert invalid.status_code == 401


def _pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(sha256(verifier.encode()).digest()).decode().rstrip("=")


@pytest.mark.parametrize(
    ("client_id", "redirect_uri"),
    [
        (CLI_CLIENT_ID, "https://example.com/callback"),
        ("client-1", CLI_REDIRECT_URI),
        (CLI_CLIENT_ID, CLI_REDIRECT_URI),
    ],
)
async def test_authorize_flow_rejects_the_reserved_cli_client(
    harness: CliAuthHarness, client_id: str, redirect_uri: str
) -> None:
    authorize = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "profile:read",
        "code_challenge": _pkce_challenge("verifier"),
        "code_challenge_method": "S256",
        "state": "state-1",
    }
    cookie = api_index.auth_service.create_browser_session_token(
        BrowserSessionContext(user_id=USER_ID)
    )

    # Rejected before the login redirect, so an anonymous browser cannot be bounced either.
    anonymous = await harness.client.get("/api/oauth/authorize", params=authorize)
    assert anonymous.status_code == 400

    harness.client.cookies.set("coach_browser_session", cookie)
    authorized = await harness.client.get("/api/oauth/authorize", params=authorize)
    assert authorized.status_code == 400
    decision = await harness.client.post(
        "/api/oauth/authorize/decision", data={**authorize, "decision": "approve"}
    )
    assert decision.status_code == 400
    assert harness.database.tables[GRANTS_TABLE] == []
