"""Headless CLI sign-in (issue #568): server-side OTP verify, whoami, refresh and revoke.

Supabase Auth is replaced with an ``httpx.MockTransport``; the OAuth repository is the real
async repository over the in-memory PostgREST fake, so grant and refresh-token rows are real.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpx
import pytest

import api.index as api_index
from backend.models.athlete import AthleteProfile
from backend.repos.async_oauth_repo import AsyncOAuthRepository
from backend.services.auth import CLI_CLIENT_ID, AuthService
from tests.python.test_async_oauth_repo import GRANTS_TABLE, AsyncFakeSupabaseClient

SUPABASE_URL = "https://supabase.example.com"
VALID_CODE = "123456"
USERS = {"athlete-a@example.com": "user-a", "athlete-b@example.com": "user-b"}


@dataclass
class FakeGoTrue:
    """Supabase Auth stand-in: one valid code for every known email."""

    calls: list[httpx.Request] = field(default_factory=list)
    status_override: int | None = None
    raise_transport_error: bool = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.raise_transport_error:
            raise httpx.ConnectError("boom", request=request)
        if request.url.path == "/auth/v1/logout":
            return httpx.Response(204)
        if self.status_override is not None:
            return httpx.Response(self.status_override, json={"msg": "upstream"})
        body = json.loads(request.content)
        user_id = USERS.get(body["email"])
        if user_id is None or body["token"] != VALID_CODE:
            # GoTrue's real messages differ per cause; the endpoint must not echo them.
            message = "User not found" if user_id is None else "Token has expired or is invalid"
            return httpx.Response(403, json={"msg": message})
        return httpx.Response(
            200,
            json={
                "access_token": f"supabase-session-{user_id}",
                "refresh_token": "supabase-refresh",
                "user": {"id": user_id, "email": body["email"]},
            },
        )

    def verify_calls(self) -> list[httpx.Request]:
        return [call for call in self.calls if call.url.path == "/auth/v1/verify"]


@dataclass
class Harness:
    client: httpx.AsyncClient
    gotrue: FakeGoTrue
    database: AsyncFakeSupabaseClient


@pytest.fixture
async def harness(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Harness]:
    monkeypatch.setattr(api_index.settings, "supabase_url", SUPABASE_URL)
    monkeypatch.setattr(api_index.settings, "supabase_service_role_key", "service-key")
    database = AsyncFakeSupabaseClient()
    service = AuthService(oauth_repo=AsyncOAuthRepository(client=database))
    monkeypatch.setattr(api_index, "auth_service", service)
    gotrue = FakeGoTrue()
    real_async_client = httpx.AsyncClient

    def upstream_client(**kwargs: object) -> httpx.AsyncClient:
        timeout = kwargs.get("timeout")
        assert isinstance(timeout, float)
        return real_async_client(transport=httpx.MockTransport(gotrue.handle), timeout=timeout)

    async with real_async_client(
        transport=httpx.ASGITransport(app=api_index.app), base_url="http://testserver"
    ) as client:
        monkeypatch.setattr("backend.services.auth.httpx.AsyncClient", upstream_client)
        yield Harness(client=client, gotrue=gotrue, database=database)
    await service.aclose()


async def _login(client: httpx.AsyncClient, email: str = "athlete-a@example.com") -> dict:
    response = await client.post(
        "/api/auth/cli/verify-otp", json={"email": email, "token": VALID_CODE}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_verify_otp_mints_cli_grant_and_discards_supabase_session(harness: Harness) -> None:
    response = await harness.client.post(
        "/api/auth/cli/verify-otp",
        json={"email": "  Athlete-A@Example.com ", "token": VALID_CODE},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["user_id"] == "user-a"
    assert body["token_type"] == "Bearer"
    assert body["access_token"]
    assert body["refresh_token"]
    assert "expires_at" in body
    # The Supabase session never reaches the CLI.
    assert "supabase-session" not in response.text

    verify = harness.gotrue.verify_calls()[0]
    assert json.loads(verify.content) == {
        "type": "email",
        "email": "athlete-a@example.com",
        "token": VALID_CODE,
    }
    assert verify.headers["apikey"] == "service-key"
    logout = next(c for c in harness.gotrue.calls if c.url.path == "/auth/v1/logout")
    assert logout.headers["authorization"] == "Bearer supabase-session-user-a"

    [grant] = harness.database.tables[GRANTS_TABLE]
    assert grant["client_id"] == CLI_CLIENT_ID
    assert grant["user_id"] == "user-a"
    assert str(grant["redirect_uri"]).startswith("urn:fitness-coach-cli:login:")


async def test_whoami_refresh_and_revoke_round_trip(harness: Harness) -> None:
    tokens = await _login(harness.client)

    whoami = await harness.client.get("/api/auth/whoami", headers=_bearer(tokens["access_token"]))
    assert whoami.status_code == 200
    assert whoami.headers["cache-control"] == "no-store"
    identity = whoami.json()
    assert identity["user_id"] == "user-a"
    assert identity["client_id"] == CLI_CLIENT_ID
    assert "profile:read" in identity["scopes"]

    refreshed = await harness.client.post(
        "/api/oauth/token",
        json={
            "grant_type": "refresh_token",
            "client_id": CLI_CLIENT_ID,
            "refresh_token": tokens["refresh_token"],
        },
    )
    assert refreshed.status_code == 200
    new_access = refreshed.json()["access_token"]

    revoked = await harness.client.post("/api/oauth/revoke", json={"token": new_access})
    assert revoked.json() == {"revoked": True}
    # Revoking the grant invalidates every access token minted from it, old and new.
    for token in (tokens["access_token"], new_access):
        after = await harness.client.get("/api/auth/whoami", headers=_bearer(token))
        assert after.status_code == 401


async def test_each_login_is_an_independent_grant(harness: Harness) -> None:
    laptop = await _login(harness.client)
    server = await _login(harness.client)
    assert len(harness.database.tables[GRANTS_TABLE]) == 2

    await harness.client.post("/api/oauth/revoke", json={"token": laptop["access_token"]})

    still_valid = await harness.client.get(
        "/api/auth/whoami", headers=_bearer(server["access_token"])
    )
    assert still_valid.status_code == 200


async def test_rejections_are_indistinguishable(harness: Harness) -> None:
    wrong_code = await harness.client.post(
        "/api/auth/cli/verify-otp", json={"email": "athlete-a@example.com", "token": "999999"}
    )
    unknown_email = await harness.client.post(
        "/api/auth/cli/verify-otp", json={"email": "nobody@example.com", "token": VALID_CODE}
    )

    assert wrong_code.status_code == unknown_email.status_code == 401
    assert wrong_code.json() == unknown_email.json() == {"detail": "Invalid or expired code."}
    assert harness.database.tables.get(GRANTS_TABLE, []) == []


@pytest.mark.parametrize("email", ["athlete-a@example.com", "nobody@example.com"])
async def test_failed_attempts_are_rate_limited_per_email(harness: Harness, email: str) -> None:
    for _ in range(5):
        response = await harness.client.post(
            "/api/auth/cli/verify-otp", json={"email": email, "token": "000000"}
        )
        assert response.status_code == 401

    limited = await harness.client.post(
        "/api/auth/cli/verify-otp", json={"email": email.upper(), "token": VALID_CODE}
    )

    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) > 0
    # The sixth attempt never reaches Supabase, even with the right code.
    assert len(harness.gotrue.verify_calls()) == 5
    other = await harness.client.post(
        "/api/auth/cli/verify-otp", json={"email": "athlete-b@example.com", "token": VALID_CODE}
    )
    assert other.status_code == 200


async def test_success_clears_the_attempt_window(harness: Harness) -> None:
    for _ in range(4):
        await harness.client.post(
            "/api/auth/cli/verify-otp", json={"email": "athlete-a@example.com", "token": "000000"}
        )
    await _login(harness.client)
    for _ in range(4):
        response = await harness.client.post(
            "/api/auth/cli/verify-otp", json={"email": "athlete-a@example.com", "token": "000000"}
        )
        assert response.status_code == 401


@pytest.mark.parametrize(
    ("status_override", "transport_error", "expected"),
    [(500, False, 503), (None, True, 503), (429, False, 429)],
)
async def test_upstream_failures_map_to_retryable_errors(
    harness: Harness, status_override: int | None, transport_error: bool, expected: int
) -> None:
    harness.gotrue.status_override = status_override
    harness.gotrue.raise_transport_error = transport_error

    response = await harness.client.post(
        "/api/auth/cli/verify-otp", json={"email": "athlete-a@example.com", "token": VALID_CODE}
    )

    assert response.status_code == expected
    assert "upstream" not in response.text


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "athlete-a@example.com", "token": "12345"},
        {"email": "athlete-a@example.com", "token": "abcdef"},
        {"email": "not-an-email", "token": VALID_CODE},
        {"email": "athlete-a@example.com"},
    ],
)
async def test_malformed_requests_never_reach_supabase(harness: Harness, payload: dict) -> None:
    response = await harness.client.post("/api/auth/cli/verify-otp", json=payload)

    assert response.status_code == 422
    assert harness.gotrue.calls == []


async def test_unconfigured_supabase_is_unavailable(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api_index.settings, "supabase_url", "")

    response = await harness.client.post(
        "/api/auth/cli/verify-otp", json={"email": "athlete-a@example.com", "token": VALID_CODE}
    )

    assert response.status_code == 503


class RecordingSummaryRepo:
    """Records which athlete each read in get-athlete-summary targets."""

    def __init__(self) -> None:
        self.user_ids: set[str] = set()

    def _seen(self, user_id: str) -> None:
        self.user_ids.add(user_id)

    async def get_athlete_profile(self, user_id: str) -> AthleteProfile:
        self._seen(user_id)
        return AthleteProfile(user_id=user_id, coaching_state="onboarding")

    async def get_active_thresholds(self, user_id: str) -> list[object]:
        self._seen(user_id)
        return []

    async def list_active_goals(self, user_id: str) -> list[object]:
        self._seen(user_id)
        return []

    async def get_latest_load(self, user_id: str) -> None:
        self._seen(user_id)

    async def list_recovery_logs(self, user_id: str, *, limit: int) -> list[object]:
        assert limit > 0
        self._seen(user_id)
        return []

    async def get_schedule(self, user_id: str) -> None:
        self._seen(user_id)

    async def get_active_plan(self, user_id: str) -> None:
        self._seen(user_id)


async def test_cli_token_only_reaches_its_own_athlete(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    athlete_a = await _login(harness.client, "athlete-a@example.com")
    athlete_b = await _login(harness.client, "athlete-b@example.com")

    for tokens, expected in ((athlete_a, "user-a"), (athlete_b, "user-b")):
        summary_repo = RecordingSummaryRepo()
        monkeypatch.setattr(api_index, "repo", summary_repo)
        response = await harness.client.post(
            "/api/engine/get-athlete-summary", headers=_bearer(tokens["access_token"])
        )
        assert response.status_code == 200
        assert response.json()["profile"]["user_id"] == expected
        assert summary_repo.user_ids == {expected}
