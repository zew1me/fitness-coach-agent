"""First-party CLI auth: server-side email OTP verification, whoami, refresh and revoke.

Exercises FastAPI -> AuthService -> async OAuth repository against the in-memory
PostgREST fake, with Supabase Auth (GoTrue) mocked at the HTTP transport.
"""

import base64
import json
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from hashlib import sha256

import httpx
import pytest

import api.index as api_index
from backend.models.auth import BrowserSessionContext
from backend.repos.async_oauth_repo import AsyncOAuthRepository
from backend.services.auth import CLI_CLIENT_ID, CLI_REDIRECT_URI, AuthService
from tests.python.test_async_oauth_repo import GRANTS_TABLE, TOKENS_TABLE, AsyncFakeSupabaseClient

SUPABASE_URL = "https://supabase.test"
SERVICE_ROLE_KEY = "service-role-key"
USER_ID = "00000000-0000-0000-0000-0000000000a1"
EMAIL = "athlete@example.com"
CODE = "123456"
INVALID_CODE_DETAIL = "Invalid or expired code."

GoTrueHandler = Callable[[httpx.Request], httpx.Response]


def _verified(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "access_token": "supabase-access-token",
            "refresh_token": "supabase-refresh-token",
            "token_type": "bearer",
            "expires_in": 3600,
            "user": {"id": USER_ID, "email": EMAIL},
        },
    )


@dataclass
class GoTrue:
    """Records Supabase Auth requests and answers them with the configured handler."""

    handler: GoTrueHandler = _verified
    requests: list[httpx.Request] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)


@dataclass
class CliAuthHarness:
    client: httpx.AsyncClient
    database: AsyncFakeSupabaseClient
    gotrue: GoTrue


@pytest.fixture
async def harness(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[CliAuthHarness]:
    monkeypatch.setattr("backend.services.auth.settings.supabase_url", SUPABASE_URL)
    monkeypatch.setattr(
        "backend.services.auth.settings.supabase_service_role_key", SERVICE_ROLE_KEY
    )
    gotrue = GoTrue()
    database = AsyncFakeSupabaseClient()
    service = AuthService(
        oauth_repo=AsyncOAuthRepository(client=database),
        http_client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(gotrue)),
    )
    monkeypatch.setattr(api_index, "auth_service", service)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api_index.app), base_url="http://testserver"
    ) as client:
        yield CliAuthHarness(client=client, database=database, gotrue=gotrue)
    await service.aclose()


async def _login(harness: CliAuthHarness) -> dict[str, object]:
    response = await harness.client.post(
        "/api/auth/cli/verify-otp", json={"email": EMAIL, "token": CODE}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _bearer(token: object) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_verify_otp_mints_cli_tokens_that_authenticate(harness: CliAuthHarness) -> None:
    response = await harness.client.post(
        "/api/auth/cli/verify-otp",
        json={"email": "  Athlete@Example.COM ", "token": f" {CODE} "},
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "access_token",
        "expires_at",
        "expires_in",
        "refresh_token",
        "scope",
        "token_type",
        "user_id",
    }
    assert body["user_id"] == USER_ID
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 900
    assert body["scope"] == " ".join(sorted(AuthService._supported_scopes))

    # The server, not the CLI, verifies with GoTrue, using the normalized email.
    [verify] = harness.gotrue.requests
    assert verify.method == "POST"
    assert str(verify.url) == f"{SUPABASE_URL}/auth/v1/verify"
    assert verify.headers["apikey"] == SERVICE_ROLE_KEY
    assert json.loads(verify.content) == {"type": "email", "email": EMAIL, "token": CODE}

    [grant] = harness.database.tables[GRANTS_TABLE]
    assert grant["client_id"] == CLI_CLIENT_ID
    assert grant["redirect_uri"] == CLI_REDIRECT_URI
    assert grant["user_id"] == USER_ID

    whoami = await harness.client.get("/api/auth/whoami", headers=_bearer(body["access_token"]))
    assert whoami.status_code == 200
    assert whoami.json() == {
        "user_id": USER_ID,
        "client_id": CLI_CLIENT_ID,
        "scopes": body["scope"].split(),
    }


async def test_repeat_login_reuses_the_cli_grant(harness: CliAuthHarness) -> None:
    first = await _login(harness)
    second = await _login(harness)

    assert first["refresh_token"] != second["refresh_token"]
    assert len(harness.database.tables[GRANTS_TABLE]) == 1
    assert len(harness.database.tables[TOKENS_TABLE]) == 2


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


@pytest.mark.parametrize(
    ("status", "payload"),
    [
        (403, {"code": 403, "error_code": "otp_expired", "msg": "Token has expired or is invalid"}),
        (400, {"code": 400, "error_code": "validation_failed", "msg": "bad"}),
        (401, {"code": 401, "msg": "unauthorized"}),
        (404, {"code": 404, "error_code": "user_not_found", "msg": "User not found"}),
        (422, {"code": 422, "msg": "unprocessable"}),
    ],
)
async def test_rejected_codes_share_one_uniform_401(
    harness: CliAuthHarness, status: int, payload: dict[str, object]
) -> None:
    harness.gotrue.handler = lambda _request: httpx.Response(status, json=payload)

    response = await harness.client.post(
        "/api/auth/cli/verify-otp", json={"email": EMAIL, "token": CODE}
    )

    assert response.status_code == 401
    assert response.json() == {"detail": INVALID_CODE_DETAIL}
    assert harness.database.tables[GRANTS_TABLE] == []
    assert harness.database.tables[TOKENS_TABLE] == []


@pytest.mark.parametrize(
    "body",
    [
        {"email": EMAIL, "token": "12345"},
        {"email": EMAIL, "token": "12345678901"},
        {"email": EMAIL, "token": "abcdef"},
        {"email": EMAIL, "token": "123 456"},
        {"email": EMAIL, "token": "١٢٣٤٥٦"},  # non-ASCII digits
        {"email": EMAIL, "token": ""},
        {"email": "   ", "token": CODE},
        {"email": "a" * 321, "token": CODE},
        {"email": EMAIL},
        {"token": CODE},
    ],
)
async def test_malformed_requests_are_rejected_without_calling_supabase(
    harness: CliAuthHarness, body: dict[str, str]
) -> None:
    response = await harness.client.post("/api/auth/cli/verify-otp", json=body)

    assert response.status_code == 422
    assert harness.gotrue.requests == []


def _connect_error(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


@pytest.mark.parametrize(
    "handler",
    [
        _connect_error,
        lambda _request: httpx.Response(500, json={"msg": "internal"}),
        lambda _request: httpx.Response(502, text="bad gateway"),
        lambda _request: httpx.Response(200, text="not json"),
        lambda _request: httpx.Response(200, json={"user": {}}),
    ],
    ids=["transport", "500", "502", "non-json", "missing-user-id"],
)
async def test_supabase_outage_returns_503(harness: CliAuthHarness, handler: GoTrueHandler) -> None:
    harness.gotrue.handler = handler

    response = await harness.client.post(
        "/api/auth/cli/verify-otp", json={"email": EMAIL, "token": CODE}
    )

    assert response.status_code == 503
    assert harness.database.tables[GRANTS_TABLE] == []


async def test_supabase_rate_limit_is_reported_as_429(harness: CliAuthHarness) -> None:
    harness.gotrue.handler = lambda _request: httpx.Response(
        429, json={"error_code": "over_request_rate_limit"}
    )

    response = await harness.client.post(
        "/api/auth/cli/verify-otp", json={"email": EMAIL, "token": CODE}
    )

    assert response.status_code == 429
    assert harness.database.tables[GRANTS_TABLE] == []


async def test_unconfigured_supabase_returns_503(
    harness: CliAuthHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("backend.services.auth.settings.supabase_url", None)

    response = await harness.client.post(
        "/api/auth/cli/verify-otp", json={"email": EMAIL, "token": CODE}
    )

    assert response.status_code == 503
    assert harness.gotrue.requests == []


async def test_verify_otp_never_logs_the_email_code_or_tokens(
    harness: CliAuthHarness, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    tokens = await _login(harness)
    harness.gotrue.handler = lambda _request: httpx.Response(403, json={})
    await harness.client.post("/api/auth/cli/verify-otp", json={"email": EMAIL, "token": "654321"})

    logged = caplog.text
    for secret in (EMAIL, CODE, "654321", tokens["access_token"], tokens["refresh_token"]):
        assert str(secret) not in logged
    assert USER_ID in logged


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
