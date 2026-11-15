"""RFC 8628 device authorization grant for the coach CLI (issue #568).

Exercises FastAPI -> AuthService -> async OAuth repository against the in-memory PostgREST
fake: starting a flow, the browser entry point and decision form, and token polling.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

import api.index as api_index
from backend.config import settings
from backend.models.auth import BrowserSessionContext, OAuthDeviceCodeRecord
from backend.repos.async_oauth_repo import AsyncOAuthRepository
from backend.services.auth import (
    CLI_CLIENT_ID,
    CLI_REDIRECT_URI,
    DEVICE_CODE_GRANT_TYPE,
    AuthService,
    OAuthDeviceFlowError,
)
from tests.python.test_async_oauth_repo import (
    DEVICE_CODES_TABLE,
    GRANTS_TABLE,
    TOKENS_TABLE,
    AsyncFakeSupabaseClient,
    _api_error,
)

BASE_URL = "https://coach.test"
USER_ID = "00000000-0000-0000-0000-0000000000a1"
OTHER_USER_ID = "00000000-0000-0000-0000-0000000000b2"
FIXED_USER_CODE = "BCDFGHJK"
ALL_SCOPES = " ".join(sorted(AuthService._supported_scopes))
TOKEN_BUNDLE_KEYS = {
    "access_token",
    "expires_at",
    "expires_in",
    "refresh_token",
    "scope",
    "token_type",
    "user_id",
}


@dataclass
class DeviceHarness:
    client: httpx.AsyncClient
    database: AsyncFakeSupabaseClient
    service: AuthService

    def sign_in(self, user_id: str = USER_ID) -> None:
        cookie = self.service.create_browser_session_token(BrowserSessionContext(user_id=user_id))
        self.client.cookies.set("coach_browser_session", cookie)

    def sign_out(self) -> None:
        self.client.cookies.clear()

    def device_row(self) -> dict[str, object]:
        [row] = self.database.tables[DEVICE_CODES_TABLE]
        return row

    def age_last_poll(self, seconds: int = 60) -> None:
        """Pretend the previous poll happened ``seconds`` ago, so the next is not too fast."""
        for row in self.database.tables[DEVICE_CODES_TABLE]:
            if row["last_polled_at"] is not None:
                polled = datetime.fromisoformat(str(row["last_polled_at"]))
                row["last_polled_at"] = (polled - timedelta(seconds=seconds)).isoformat()

    def expire(self) -> None:
        for row in self.database.tables[DEVICE_CODES_TABLE]:
            row["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()


@pytest.fixture
async def harness(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[DeviceHarness]:
    monkeypatch.setattr(settings, "app_base_url", BASE_URL)
    database = AsyncFakeSupabaseClient()
    service = AuthService(oauth_repo=AsyncOAuthRepository(client=database))
    monkeypatch.setattr(api_index, "auth_service", service)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api_index.app), base_url="http://testserver"
    ) as client:
        yield DeviceHarness(client=client, database=database, service=service)
    await service.aclose()


@pytest.fixture
def fixed_user_code(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(AsyncOAuthRepository, "_issue_user_code", staticmethod(lambda: "BCDFGHJK"))
    return FIXED_USER_CODE


async def _start(harness: DeviceHarness, **body: object) -> dict[str, object]:
    response = await harness.client.post(
        "/api/oauth/device/code", json={"client_id": CLI_CLIENT_ID, **body}
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


async def _poll(
    harness: DeviceHarness, device_code: object, client_id: str = CLI_CLIENT_ID
) -> httpx.Response:
    return await harness.client.post(
        "/api/oauth/token",
        json={
            "grant_type": DEVICE_CODE_GRANT_TYPE,
            "device_code": device_code,
            "client_id": client_id,
        },
    )


def _assert_oauth_error(response: httpx.Response, error: str) -> None:
    assert response.status_code == 400, response.text
    body = response.json()
    assert set(body) == {"error", "error_description"}
    assert body["error"] == error


def _redirect(response: httpx.Response) -> tuple[str, dict[str, str]]:
    """Return a 302's target (scheme://host/path) and its single-valued query parameters."""
    assert response.status_code == 302, response.text
    parts = urlsplit(response.headers["location"])
    query = {key: values[0] for key, values in parse_qs(parts.query).items()}
    return f"{parts.scheme}://{parts.netloc}{parts.path}", query


async def _decide(
    harness: DeviceHarness,
    user_code: str,
    decision: str,
    *,
    client_id: str = CLI_CLIENT_ID,
    scope: str = ALL_SCOPES,
) -> httpx.Response:
    """Submit the confirm form; ``client_id``/``scope`` are what the page displayed."""
    return await harness.client.post(
        "/api/oauth/device/decision",
        data={"user_code": user_code, "decision": decision, "client_id": client_id, "scope": scope},
    )


def _bearer(token: object) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ── Happy path ────────────────────────────────────────────────


def _assert_device_authorization_response(started: dict[str, object]) -> None:
    assert set(started) == {
        "device_code",
        "user_code",
        "verification_uri",
        "verification_uri_complete",
        "expires_in",
        "interval",
    }
    user_code = str(started["user_code"])
    undashed = user_code.replace("-", "")
    assert len(user_code) == 9
    assert user_code[4] == "-"
    assert set(undashed) <= set("BCDFGHJKLMNPQRSTVWXZ")
    assert started["verification_uri"] == f"{BASE_URL}/device"
    assert started["verification_uri_complete"] == (
        f"{BASE_URL}/api/oauth/device?user_code={undashed}"
    )
    assert started["expires_in"] == 600
    assert started["interval"] == 5


async def _assert_cli_tokens_work(harness: DeviceHarness, tokens: dict[str, object]) -> None:
    """The access token authenticates as the CLI and the refresh token rotates normally."""
    assert set(tokens) == TOKEN_BUNDLE_KEYS
    assert tokens["user_id"] == USER_ID
    assert tokens["token_type"] == "Bearer"
    assert tokens["expires_in"] == 900
    assert tokens["scope"] == ALL_SCOPES

    whoami = await harness.client.get("/api/auth/whoami", headers=_bearer(tokens["access_token"]))
    assert whoami.status_code == 200
    assert whoami.json() == {
        "user_id": USER_ID,
        "client_id": CLI_CLIENT_ID,
        "scopes": ALL_SCOPES.split(),
    }
    user_context = await api_index.require_user_context(f"Bearer {tokens['access_token']}")
    assert user_context.client_id == CLI_CLIENT_ID
    assert user_context.user_id == USER_ID

    refreshed = await harness.client.post(
        "/api/oauth/token",
        json={
            "grant_type": "refresh_token",
            "client_id": CLI_CLIENT_ID,
            "refresh_token": tokens["refresh_token"],
        },
    )
    assert refreshed.status_code == 200


async def test_full_device_flow_issues_cli_tokens_exactly_once(harness: DeviceHarness) -> None:
    started = await _start(harness)
    _assert_device_authorization_response(started)
    user_code = str(started["user_code"])
    undashed = user_code.replace("-", "")

    # Only the device code's hash is stored.
    row = harness.device_row()
    device_code = str(started["device_code"])
    assert row["device_code_hash"] == sha256(device_code.encode()).hexdigest()
    assert device_code not in {str(value) for value in row.values()}
    assert row["status"] == "pending"
    assert row["scopes"] == ALL_SCOPES.split()

    _assert_oauth_error(await _poll(harness, device_code), "authorization_pending")

    # The browser opens the complete URL without a session: log in, then come back.
    entry = urlsplit(str(started["verification_uri_complete"]))
    target, query = _redirect(await harness.client.get(f"{entry.path}?{entry.query}"))
    assert target == f"{BASE_URL}/login"
    assert query == {"return_to": f"/api/oauth/device?user_code={undashed}"}

    harness.sign_in()
    target, query = _redirect(await harness.client.get(query["return_to"]))
    assert target == f"{BASE_URL}/device"
    assert query == {
        "step": "confirm",
        "user_code": user_code,
        "client_id": CLI_CLIENT_ID,
        "scope": ALL_SCOPES,
    }

    target, query = _redirect(await _decide(harness, user_code, "approve"))
    assert (target, query) == (f"{BASE_URL}/device", {"status": "approved"})
    [grant] = harness.database.tables[GRANTS_TABLE]
    assert (grant["user_id"], grant["client_id"], grant["redirect_uri"]) == (
        USER_ID,
        CLI_CLIENT_ID,
        CLI_REDIRECT_URI,
    )
    assert harness.device_row()["status"] == "approved"

    harness.age_last_poll()
    issued = await _poll(harness, device_code)
    assert issued.status_code == 200, issued.text
    assert harness.device_row()["status"] == "consumed"
    harness.sign_out()
    await _assert_cli_tokens_work(harness, issued.json())

    # A second poll with the consumed code is terminal and issues nothing new.
    harness.age_last_poll()
    _assert_oauth_error(await _poll(harness, device_code), "expired_token")
    assert len(harness.database.tables[TOKENS_TABLE]) == 2  # issued + rotated, nothing else


async def test_concurrent_polls_issue_the_bundle_once(harness: DeviceHarness) -> None:
    started = await _start(harness)
    harness.sign_in()
    await _decide(harness, str(started["user_code"]), "approve")

    first, second = await asyncio.gather(
        _poll(harness, started["device_code"]), _poll(harness, started["device_code"])
    )

    assert sorted([first.status_code, second.status_code]) == [200, 400]
    loser = first if first.status_code == 400 else second
    _assert_oauth_error(loser, "expired_token")
    assert len(harness.database.tables[TOKENS_TABLE]) == 1


async def test_a_stale_approved_read_cannot_redeem_twice(
    harness: DeviceHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = await _start(harness)
    harness.sign_in()
    await _decide(harness, str(started["user_code"]), "approve")
    repo = harness.service._oauth_repo
    stale = await repo.get_device_code(str(started["device_code"]))
    assert stale is not None
    assert stale.status == "approved"
    assert (await _poll(harness, started["device_code"])).status_code == 200

    # A racing poll that read the row before it was consumed loses the conditional update.
    async def stale_read(_raw: str) -> object:
        return stale

    monkeypatch.setattr(repo, "get_device_code", stale_read)
    _assert_oauth_error(await _poll(harness, started["device_code"]), "expired_token")
    assert len(harness.database.tables[TOKENS_TABLE]) == 1


async def test_device_approval_reuses_the_cli_grant(harness: DeviceHarness) -> None:
    harness.sign_in()
    for _ in range(2):
        started = await _start(harness)
        await _decide(harness, str(started["user_code"]), "approve")
        harness.database.tables[DEVICE_CODES_TABLE].clear()

    assert len(harness.database.tables[GRANTS_TABLE]) == 1


async def test_requested_scope_subset_is_honoured(harness: DeviceHarness) -> None:
    started = await _start(harness, scope="profile:read  plans:read profile:read")
    harness.sign_in()

    _, query = _redirect(
        await harness.client.get(
            "/api/oauth/device", params={"user_code": str(started["user_code"])}
        )
    )
    assert query["scope"] == "plans:read profile:read"
    await _decide(harness, str(started["user_code"]), "approve", scope=query["scope"])
    tokens = (await _poll(harness, started["device_code"])).json()
    assert tokens["scope"] == "plans:read profile:read"


# ── Polling errors ────────────────────────────────────────────


async def test_pending_poll_is_rfc_shaped_and_uncacheable(harness: DeviceHarness) -> None:
    started = await _start(harness)

    response = await _poll(harness, started["device_code"])

    _assert_oauth_error(response, "authorization_pending")
    assert response.headers["cache-control"] == "no-store"
    assert harness.device_row()["last_polled_at"] is not None


async def test_polling_too_fast_answers_slow_down_and_raises_the_interval(
    harness: DeviceHarness,
) -> None:
    started = await _start(harness)
    device_code = started["device_code"]

    _assert_oauth_error(await _poll(harness, device_code), "authorization_pending")
    slow = await _poll(harness, device_code)
    _assert_oauth_error(slow, "slow_down")
    assert harness.device_row()["interval_seconds"] == 10
    _assert_oauth_error(await _poll(harness, device_code), "slow_down")
    assert harness.device_row()["interval_seconds"] == 15

    # Waiting 11s is still inside the raised 15s interval.
    harness.age_last_poll(11)
    _assert_oauth_error(await _poll(harness, device_code), "slow_down")
    assert harness.device_row()["interval_seconds"] == 20

    harness.age_last_poll(21)
    _assert_oauth_error(await _poll(harness, device_code), "authorization_pending")
    assert harness.device_row()["interval_seconds"] == 20


async def test_denied_request_answers_access_denied(harness: DeviceHarness) -> None:
    started = await _start(harness)
    harness.sign_in()

    target, query = _redirect(await _decide(harness, str(started["user_code"]), "deny"))

    assert (target, query) == (f"{BASE_URL}/device", {"status": "denied"})
    assert harness.device_row()["status"] == "denied"
    assert harness.database.tables[GRANTS_TABLE] == []
    _assert_oauth_error(await _poll(harness, started["device_code"]), "access_denied")
    # A decided code is no longer pending, so it cannot be re-opened or re-decided.
    _, query = _redirect(
        await harness.client.get(
            "/api/oauth/device", params={"user_code": str(started["user_code"])}
        )
    )
    assert query == {"error": "invalid_code"}
    _, query = _redirect(await _decide(harness, str(started["user_code"]), "approve"))
    assert query == {"error": "invalid_code"}
    assert harness.database.tables[GRANTS_TABLE] == []


@pytest.mark.parametrize("decision", ["reject", ""])
async def test_any_decision_other_than_approve_denies(
    harness: DeviceHarness, decision: str
) -> None:
    started = await _start(harness)
    harness.sign_in()

    _, query = _redirect(await _decide(harness, str(started["user_code"]), decision))

    assert query == {"status": "denied"}
    assert harness.device_row()["status"] == "denied"


async def test_expired_codes_are_terminal_everywhere(harness: DeviceHarness) -> None:
    started = await _start(harness)
    harness.sign_in()
    harness.expire()

    _assert_oauth_error(await _poll(harness, started["device_code"]), "expired_token")
    _, query = _redirect(
        await harness.client.get(
            "/api/oauth/device", params={"user_code": str(started["user_code"])}
        )
    )
    assert query == {"error": "invalid_code"}
    _, query = _redirect(await _decide(harness, str(started["user_code"]), "approve"))
    assert query == {"error": "invalid_code"}
    assert harness.device_row()["status"] == "pending"
    assert harness.database.tables[GRANTS_TABLE] == []


async def test_approved_code_that_expires_before_the_poll_is_not_redeemed(
    harness: DeviceHarness,
) -> None:
    started = await _start(harness)
    harness.sign_in()
    await _decide(harness, str(started["user_code"]), "approve")
    harness.expire()

    _assert_oauth_error(await _poll(harness, started["device_code"]), "expired_token")
    assert harness.database.tables[TOKENS_TABLE] == []


async def test_unknown_wrong_client_and_expired_codes_are_indistinguishable(
    harness: DeviceHarness,
) -> None:
    started = await _start(harness)

    unknown = await _poll(harness, "not-a-real-device-code")
    wrong_client = await _poll(harness, started["device_code"], client_id="client-1")
    harness.expire()
    expired = await _poll(harness, started["device_code"])

    _assert_oauth_error(unknown, "expired_token")
    assert unknown.json() == wrong_client.json() == expired.json()
    assert harness.device_row()["last_polled_at"] is None


async def test_revoked_grant_before_the_poll_issues_nothing(harness: DeviceHarness) -> None:
    started = await _start(harness)
    harness.sign_in()
    await _decide(harness, str(started["user_code"]), "approve")
    harness.database.tables[GRANTS_TABLE][0]["revoked_at"] = datetime.now(UTC).isoformat()

    _assert_oauth_error(await _poll(harness, started["device_code"]), "expired_token")
    assert harness.database.tables[TOKENS_TABLE] == []


async def test_missing_device_code_is_an_invalid_request(harness: DeviceHarness) -> None:
    response = await harness.client.post(
        "/api/oauth/token", json={"grant_type": DEVICE_CODE_GRANT_TYPE, "client_id": CLI_CLIENT_ID}
    )

    _assert_oauth_error(response, "invalid_request")


async def test_other_grant_types_keep_the_detail_error_shape(harness: DeviceHarness) -> None:
    unsupported = await harness.client.post(
        "/api/oauth/token", json={"grant_type": "password", "client_id": CLI_CLIENT_ID}
    )
    refresh = await harness.client.post(
        "/api/oauth/token",
        json={"grant_type": "refresh_token", "client_id": CLI_CLIENT_ID, "refresh_token": "nope"},
    )

    assert unsupported.status_code == 400
    assert unsupported.json() == {"detail": "Unsupported grant type."}
    assert refresh.status_code == 400
    assert set(refresh.json()) == {"detail"}


# ── Starting a flow ───────────────────────────────────────────


@pytest.mark.parametrize("client_id", ["client-1", "", "https://chatgpt.com"])
async def test_only_the_cli_may_start_a_device_flow(harness: DeviceHarness, client_id: str) -> None:
    response = await harness.client.post("/api/oauth/device/code", json={"client_id": client_id})

    _assert_oauth_error(response, "invalid_client")
    assert harness.database.tables[DEVICE_CODES_TABLE] == []


@pytest.mark.parametrize("scope", ["admin", "profile:read admin"])
async def test_unsupported_scope_is_rejected(harness: DeviceHarness, scope: str) -> None:
    response = await harness.client.post(
        "/api/oauth/device/code", json={"client_id": CLI_CLIENT_ID, "scope": scope}
    )

    _assert_oauth_error(response, "invalid_scope")
    assert harness.database.tables[DEVICE_CODES_TABLE] == []


async def test_a_user_code_collision_retries_with_a_fresh_code(harness: DeviceHarness) -> None:
    harness.database.fail_next(DEVICE_CODES_TABLE, "insert", _api_error("23505"))

    started = await _start(harness)

    assert harness.database.operations[(DEVICE_CODES_TABLE, "insert")] == 2
    assert harness.device_row()["user_code"] == str(started["user_code"]).replace("-", "")


async def test_starting_a_flow_purges_codes_expired_over_a_day_ago(harness: DeviceHarness) -> None:
    await _start(harness)
    await _start(harness)
    stale, recent = harness.database.tables[DEVICE_CODES_TABLE]
    stale["expires_at"] = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    recent["expires_at"] = (datetime.now(UTC) - timedelta(hours=1)).isoformat()

    await _start(harness)

    remaining = harness.database.tables[DEVICE_CODES_TABLE]
    assert len(remaining) == 2
    assert stale not in remaining
    assert recent in remaining


async def test_a_failed_purge_does_not_fail_the_login(harness: DeviceHarness) -> None:
    harness.database.fail_next(DEVICE_CODES_TABLE, "delete", _api_error("57014"))

    await _start(harness)

    assert len(harness.database.tables[DEVICE_CODES_TABLE]) == 1


async def test_metadata_advertises_the_device_grant(harness: DeviceHarness) -> None:
    metadata = (await harness.client.get("/.well-known/oauth-authorization-server")).json()

    assert metadata["device_authorization_endpoint"] == f"{BASE_URL}/api/oauth/device/code"
    assert DEVICE_CODE_GRANT_TYPE in metadata["grant_types_supported"]


# ── Browser entry point and decision ──────────────────────────


async def test_entry_point_without_a_code_shows_the_form(harness: DeviceHarness) -> None:
    assert _redirect(await harness.client.get("/api/oauth/device")) == (f"{BASE_URL}/device", {})
    assert _redirect(await harness.client.get("/api/oauth/device?user_code=")) == (
        f"{BASE_URL}/device",
        {},
    )


@pytest.mark.parametrize(
    "user_code", ["BCDFGHJA", "BCDF-GHJ", "BCDFGHJKL", "<script>", "BCDF GHJ0"]
)
async def test_malformed_codes_are_rejected_without_a_lookup(
    harness: DeviceHarness, user_code: str
) -> None:
    for signed_in in (False, True):
        if signed_in:
            harness.sign_in()
        _, query = _redirect(
            await harness.client.get("/api/oauth/device", params={"user_code": user_code})
        )
        assert query == {"error": "invalid_code"}
        _, query = _redirect(await _decide(harness, user_code, "approve"))
        assert query == {"error": "invalid_code"}
    assert harness.database.operations[(DEVICE_CODES_TABLE, "select")] == 0


async def test_unknown_code_with_a_session_is_invalid(harness: DeviceHarness) -> None:
    harness.sign_in()

    _, query = _redirect(
        await harness.client.get("/api/oauth/device", params={"user_code": "BCDF-GHJK"})
    )

    assert query == {"error": "invalid_code"}


async def test_unknown_code_without_a_session_goes_to_login_first(harness: DeviceHarness) -> None:
    target, query = _redirect(
        await harness.client.get("/api/oauth/device", params={"user_code": "bcdf-ghjk"})
    )

    assert target == f"{BASE_URL}/login"
    assert query == {"return_to": "/api/oauth/device?user_code=BCDFGHJK"}
    assert harness.database.operations[(DEVICE_CODES_TABLE, "select")] == 0


@pytest.mark.parametrize("cookie", [None, "not-a-jwt"])
async def test_decision_without_a_session_redirects_to_login(
    harness: DeviceHarness, fixed_user_code: str, cookie: str | None
) -> None:
    await _start(harness)
    if cookie is not None:
        harness.client.cookies.set("coach_browser_session", cookie)

    target, query = _redirect(await _decide(harness, "bcdf-ghjk", "approve"))

    assert target == f"{BASE_URL}/login"
    assert query == {"return_to": f"/api/oauth/device?user_code={fixed_user_code}"}
    assert harness.device_row()["status"] == "pending"
    assert harness.database.tables[GRANTS_TABLE] == []


@pytest.mark.parametrize("typed", ["bcdf-ghjk", " BCDF GHJK ", "bcdfghjk", "BcDf-GhJk"])
async def test_user_code_input_is_normalised(
    harness: DeviceHarness, fixed_user_code: str, typed: str
) -> None:
    started = await _start(harness)
    assert started["user_code"] == "BCDF-GHJK"
    harness.sign_in()

    _, query = _redirect(await harness.client.get("/api/oauth/device", params={"user_code": typed}))
    assert query["step"] == "confirm"
    assert query["user_code"] == "BCDF-GHJK"

    _, query = _redirect(await _decide(harness, typed, "approve"))
    assert query == {"status": "approved"}
    assert harness.device_row()["user_id"] == USER_ID
    assert harness.device_row()["user_code"] == fixed_user_code


async def test_approval_binds_the_signed_in_athlete(harness: DeviceHarness) -> None:
    started = await _start(harness)
    harness.sign_in(OTHER_USER_ID)

    await _decide(harness, str(started["user_code"]), "approve")
    tokens = (await _poll(harness, started["device_code"])).json()

    assert tokens["user_id"] == OTHER_USER_ID
    assert harness.device_row()["user_id"] == OTHER_USER_ID


# ── Logging ───────────────────────────────────────────────────


async def test_device_flow_never_logs_codes_or_tokens(
    harness: DeviceHarness, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    started = await _start(harness)
    user_code = str(started["user_code"])
    await _poll(harness, started["device_code"])
    await _poll(harness, started["device_code"])
    await harness.client.get("/api/oauth/device", params={"user_code": user_code})
    harness.sign_in()
    await harness.client.get("/api/oauth/device", params={"user_code": user_code})
    await _decide(harness, user_code, "approve")
    harness.age_last_poll()
    tokens = (await _poll(harness, started["device_code"])).json()
    await _poll(harness, started["device_code"])
    await _poll(harness, "unknown-device-code")

    # The test's own HTTP client logs request URLs; only server-side records matter here.
    server_logs = "\n".join(
        record.getMessage() for record in caplog.records if not record.name.startswith("httpx")
    )
    secrets = (
        started["device_code"],
        "unknown-device-code",
        user_code,
        user_code.replace("-", ""),
        tokens["access_token"],
        tokens["refresh_token"],
    )
    for secret in secrets:
        assert str(secret) not in server_logs
    assert USER_ID in server_logs
    assert "authorization_pending" in server_logs


# ── Approval is bound to what the confirm page displayed ─────


@pytest.mark.parametrize(
    ("shown_client_id", "shown_scope"),
    [
        (CLI_CLIENT_ID, "profile:read"),  # crafted link understating the stored scopes
        (CLI_CLIENT_ID, ""),  # no scope echoed at all
        ("some-other-client", ALL_SCOPES),  # wrong client label
    ],
)
async def test_approval_that_does_not_match_the_stored_request_approves_nothing(
    harness: DeviceHarness, shown_client_id: str, shown_scope: str
) -> None:
    started = await _start(harness)
    harness.sign_in()

    response = await _decide(
        harness,
        str(started["user_code"]),
        "approve",
        client_id=shown_client_id,
        scope=shown_scope,
    )

    # Back to the confirm step, rendered from the stored request rather than the form.
    target, query = _redirect(response)
    assert target == f"{BASE_URL}/device"
    assert query["step"] == "confirm"
    assert query["client_id"] == CLI_CLIENT_ID
    assert query["scope"] == ALL_SCOPES
    assert harness.device_row()["status"] == "pending"
    assert harness.database.tables.get(GRANTS_TABLE, []) == []
    _assert_oauth_error(await _poll(harness, started["device_code"]), "authorization_pending")


async def test_matching_scope_in_a_different_order_is_accepted(harness: DeviceHarness) -> None:
    started = await _start(harness)
    harness.sign_in()

    shuffled = " ".join(reversed(ALL_SCOPES.split()))
    _, query = _redirect(
        await _decide(harness, str(started["user_code"]), "approve", scope=shuffled)
    )

    assert query == {"status": "approved"}


async def test_deny_needs_no_matching_confirmation(harness: DeviceHarness) -> None:
    started = await _start(harness)
    harness.sign_in()

    _, query = _redirect(
        await _decide(harness, str(started["user_code"]), "deny", scope="profile:read")
    )

    assert query == {"status": "denied"}


async def test_a_stale_concurrent_poll_cannot_lower_the_backoff(harness: DeviceHarness) -> None:
    started = await _start(harness)
    await _poll(harness, started["device_code"])  # first poll stamps last_polled_at
    stale_view = dict(harness.device_row())  # what a slow concurrent poll read

    # A fast poll raises the interval to 10s.
    _assert_oauth_error(await _poll(harness, started["device_code"]), "slow_down")
    assert harness.device_row()["interval_seconds"] == 10
    raised_row = dict(harness.device_row())

    # The stale poll now tries to write interval 5 based on its old read: the
    # compare-and-set misses, it answers slow_down, and the larger backoff survives.
    record = OAuthDeviceCodeRecord.model_validate(stale_view)
    with pytest.raises(OAuthDeviceFlowError) as excinfo:
        await harness.service._reject_pending_device_poll(record, now=datetime.now(UTC))
    assert excinfo.value.error == "slow_down"
    assert harness.device_row() == raised_row
