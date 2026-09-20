from collections import Counter
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from inspect import iscoroutinefunction, signature
from typing import Any

import pytest

from backend.config import settings
from backend.repos import async_oauth_repo
from backend.repos.async_oauth_repo import AsyncOAuthRepository
from backend.repos.oauth_repo import OAuthRepository, OAuthRepositoryNotConfiguredError

GRANTS_TABLE = "oauth_grants"
CODES_TABLE = "oauth_authorization_codes"
TOKENS_TABLE = "oauth_refresh_tokens"


class FakeResponse:
    def __init__(self, data: list[dict[str, object]]) -> None:
        self.data = data


class AsyncFakeTableQuery:
    def __init__(self, client: "AsyncFakeSupabaseClient", table_name: str) -> None:
        self._client = client
        self._table_name = table_name
        self._filters: dict[str, object] = {}
        self._is_null: set[str] = set()
        self._insert_payload: dict[str, object] | None = None
        self._update_payload: dict[str, object] | None = None
        self._limit: int | None = None

    def select(self, *_columns: str) -> "AsyncFakeTableQuery":
        return self

    def insert(self, payload: dict[str, object]) -> "AsyncFakeTableQuery":
        self._insert_payload = payload
        return self

    def update(self, payload: dict[str, object]) -> "AsyncFakeTableQuery":
        self._update_payload = payload
        return self

    def eq(self, column: str, value: object) -> "AsyncFakeTableQuery":
        self._filters[column] = value
        return self

    def is_(self, column: str, value: object) -> "AsyncFakeTableQuery":
        assert value == "null"
        self._is_null.add(column)
        return self

    def limit(self, count: int) -> "AsyncFakeTableQuery":
        self._limit = count
        return self

    async def execute(self) -> FakeResponse:
        operation = "select"
        if self._insert_payload is not None:
            operation = "insert"
        elif self._update_payload is not None:
            operation = "update"
        self._client.operations[(self._table_name, operation)] += 1

        if (self._table_name, operation) in self._client.empty_responses:
            return FakeResponse([])
        if self._insert_payload is not None:
            row = dict(self._insert_payload)
            self._client.tables[self._table_name].append(row)
            return FakeResponse([row])
        if self._update_payload is not None:
            updated: list[dict[str, object]] = []
            for row in self._matching_rows():
                row.update(self._update_payload)
                updated.append(row)
            return FakeResponse(updated)

        rows = self._matching_rows()
        if self._limit is not None:
            rows = rows[: self._limit]
        return FakeResponse(rows)

    def _matching_rows(self) -> list[dict[str, object]]:
        return [
            row
            for row in self._client.tables[self._table_name]
            if all(row.get(column) == value for column, value in self._filters.items())
            and all(row.get(column) is None for column in self._is_null)
        ]


class AsyncFakePostgRESTClient:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class AsyncFakeSupabaseClient:
    def __init__(
        self,
        *,
        grants: list[dict[str, object]] | None = None,
        authorization_codes: list[dict[str, object]] | None = None,
        refresh_tokens: list[dict[str, object]] | None = None,
        empty_responses: set[tuple[str, str]] | None = None,
    ) -> None:
        self.tables = {
            GRANTS_TABLE: grants or [],
            CODES_TABLE: authorization_codes or [],
            TOKENS_TABLE: refresh_tokens or [],
        }
        self.empty_responses = empty_responses or set()
        self.operations: Counter[tuple[str, str]] = Counter()
        self.postgrest = AsyncFakePostgRESTClient()

    def table(self, table_name: str) -> AsyncFakeTableQuery:
        return AsyncFakeTableQuery(self, table_name)


def _grant_row(
    *,
    grant_id: str = "grant-1",
    scopes: list[str] | None = None,
    revoked_at: str | None = None,
) -> dict[str, object]:
    now = datetime.now(UTC).isoformat()
    return {
        "id": grant_id,
        "user_id": "user-1",
        "client_id": "client-1",
        "redirect_uri": "https://example.com/callback",
        "scopes": scopes or ["profile:read"],
        "created_at": now,
        "updated_at": now,
        "revoked_at": revoked_at,
    }


def _authorization_code_row(
    raw_code: str,
    *,
    code_id: str = "code-1",
    consumed_at: str | None = None,
    expires_at: datetime | None = None,
) -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "id": code_id,
        "grant_id": "grant-1",
        "user_id": "user-1",
        "client_id": "client-1",
        "redirect_uri": "https://example.com/callback",
        "scopes": ["profile:read"],
        "code_challenge": "challenge",
        "code_challenge_method": "S256",
        "token_hash": sha256(raw_code.encode()).hexdigest(),
        "expires_at": (expires_at or now + timedelta(minutes=10)).isoformat(),
        "consumed_at": consumed_at,
        "created_at": now.isoformat(),
    }


def _refresh_token_row(
    raw_token: str,
    *,
    token_id: str = "token-1",
    revoked_at: str | None = None,
    expires_at: datetime | None = None,
) -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "id": token_id,
        "grant_id": "grant-1",
        "user_id": "user-1",
        "client_id": "client-1",
        "scopes": ["profile:read"],
        "token_hash": sha256(raw_token.encode()).hexdigest(),
        "expires_at": (expires_at or now + timedelta(days=30)).isoformat(),
        "revoked_at": revoked_at,
        "created_at": now.isoformat(),
        "rotated_from_id": None,
    }


async def test_aclose_closes_postgrest_connections() -> None:
    client = AsyncFakeSupabaseClient()
    repo = AsyncOAuthRepository(client=client)

    await repo.aclose()

    assert client.postgrest.closed is True


async def test_upsert_grant_inserts_when_active_grant_is_absent() -> None:
    client = AsyncFakeSupabaseClient()
    repo = AsyncOAuthRepository(client=client)

    grant = await repo.upsert_grant(
        user_id="user-1",
        client_id="client-1",
        redirect_uri="https://example.com/callback",
        scopes=["profile:read"],
    )

    assert grant.user_id == "user-1"
    assert grant.scopes == ["profile:read"]
    assert client.operations[(GRANTS_TABLE, "select")] == 1
    assert client.operations[(GRANTS_TABLE, "insert")] == 1
    assert client.operations[(GRANTS_TABLE, "update")] == 0


async def test_upsert_grant_updates_existing_grant_and_merges_scopes() -> None:
    client = AsyncFakeSupabaseClient(grants=[_grant_row(scopes=["profile:read"])])
    repo = AsyncOAuthRepository(client=client)

    grant = await repo.upsert_grant(
        user_id="user-1",
        client_id="client-1",
        redirect_uri="https://example.com/callback",
        scopes=["metrics:write", "profile:read"],
    )

    assert grant.id == "grant-1"
    assert grant.scopes == ["metrics:write", "profile:read"]
    assert client.operations[(GRANTS_TABLE, "select")] == 1
    assert client.operations[(GRANTS_TABLE, "update")] == 1
    assert client.operations[(GRANTS_TABLE, "insert")] == 0


async def test_upsert_grant_returns_existing_grant_when_scopes_are_unchanged() -> None:
    updated_at = "2026-01-01T00:00:00+00:00"
    row = _grant_row(scopes=["metrics:write", "profile:read"])
    row["updated_at"] = updated_at
    client = AsyncFakeSupabaseClient(grants=[row])
    repo = AsyncOAuthRepository(client=client)

    grant = await repo.upsert_grant(
        user_id="user-1",
        client_id="client-1",
        redirect_uri="https://example.com/callback",
        scopes=["profile:read"],
    )

    assert grant.scopes == ["metrics:write", "profile:read"]
    assert client.tables[GRANTS_TABLE][0]["updated_at"] == updated_at
    assert client.operations[(GRANTS_TABLE, "select")] == 1
    assert client.operations[(GRANTS_TABLE, "update")] == 0


async def test_upsert_grant_normalizes_existing_duplicate_scopes() -> None:
    client = AsyncFakeSupabaseClient(grants=[_grant_row(scopes=["profile:read", "profile:read"])])
    repo = AsyncOAuthRepository(client=client)

    grant = await repo.upsert_grant(
        user_id="user-1",
        client_id="client-1",
        redirect_uri="https://example.com/callback",
        scopes=["profile:read"],
    )

    assert grant.scopes == ["profile:read"]
    assert client.operations[(GRANTS_TABLE, "select")] == 1
    assert client.operations[(GRANTS_TABLE, "update")] == 1


async def test_upsert_grant_normalizes_scopes_before_insert() -> None:
    client = AsyncFakeSupabaseClient()
    repo = AsyncOAuthRepository(client=client)

    grant = await repo.upsert_grant(
        user_id="user-1",
        client_id="client-1",
        redirect_uri="https://example.com/callback",
        scopes=["profile:read", "metrics:write", "profile:read"],
    )

    assert grant.scopes == ["metrics:write", "profile:read"]
    assert client.operations[(GRANTS_TABLE, "select")] == 1
    assert client.operations[(GRANTS_TABLE, "insert")] == 1


async def test_get_active_grant_ignores_revoked_matching_grant() -> None:
    client = AsyncFakeSupabaseClient(grants=[_grant_row(revoked_at=datetime.now(UTC).isoformat())])
    repo = AsyncOAuthRepository(client=client)

    grant = await repo.get_active_grant(
        user_id="user-1",
        client_id="client-1",
        redirect_uri="https://example.com/callback",
    )

    assert grant is None
    assert client.operations[(GRANTS_TABLE, "select")] == 1


async def test_get_active_grant_and_get_grant_by_id_hit_and_miss() -> None:
    client = AsyncFakeSupabaseClient(grants=[_grant_row()])
    repo = AsyncOAuthRepository(client=client)

    active = await repo.get_active_grant(
        user_id="user-1",
        client_id="client-1",
        redirect_uri="https://example.com/callback",
    )
    missing_active = await repo.get_active_grant(
        user_id="other",
        client_id="client-1",
        redirect_uri="https://example.com/callback",
    )

    assert active is not None and active.id == "grant-1"
    assert missing_active is None
    assert (await repo.get_grant_by_id("grant-1")) == active
    assert await repo.get_grant_by_id("missing") is None
    assert client.operations[(GRANTS_TABLE, "select")] == 4


async def test_create_and_get_authorization_code_persist_only_hash() -> None:
    client = AsyncFakeSupabaseClient()
    repo = AsyncOAuthRepository(client=client)

    raw_code = await repo.create_authorization_code(
        grant_id="grant-1",
        user_id="user-1",
        client_id="client-1",
        redirect_uri="https://example.com/callback",
        scopes=["profile:read"],
        code_challenge="challenge",
        code_challenge_method="S256",
    )

    stored = client.tables[CODES_TABLE][0]
    assert stored["token_hash"] == sha256(raw_code.encode()).hexdigest()
    assert raw_code not in stored.values()
    record = await repo.get_authorization_code(raw_code)
    assert record is not None and record.id == stored["id"]
    assert await repo.get_authorization_code("missing") is None
    assert client.operations[(CODES_TABLE, "insert")] == 1
    assert client.operations[(CODES_TABLE, "select")] == 2


async def test_consume_authorization_code_marks_it_consumed() -> None:
    raw_code = "raw-code"
    client = AsyncFakeSupabaseClient(authorization_codes=[_authorization_code_row(raw_code)])
    repo = AsyncOAuthRepository(client=client)

    consumed = await repo.consume_authorization_code(raw_code)

    assert consumed.consumed_at is not None
    assert client.tables[CODES_TABLE][0]["consumed_at"] is not None
    assert client.operations[(CODES_TABLE, "select")] == 1
    assert client.operations[(CODES_TABLE, "update")] == 1


@pytest.mark.parametrize(
    ("rows", "raw_code", "message"),
    [
        ([], "missing", "Invalid authorization code."),
        (
            [_authorization_code_row("consumed", consumed_at=datetime.now(UTC).isoformat())],
            "consumed",
            "Authorization code is no longer valid.",
        ),
        (
            [
                _authorization_code_row(
                    "expired", expires_at=datetime.now(UTC) - timedelta(seconds=1)
                )
            ],
            "expired",
            "Authorization code is no longer valid.",
        ),
    ],
)
async def test_consume_authorization_code_rejects_invalid_states(
    rows: list[dict[str, object]], raw_code: str, message: str
) -> None:
    repo = AsyncOAuthRepository(client=AsyncFakeSupabaseClient(authorization_codes=rows))

    with pytest.raises(ValueError, match=message):
        await repo.consume_authorization_code(raw_code)


async def test_create_get_and_rotate_refresh_token() -> None:
    client = AsyncFakeSupabaseClient()
    repo = AsyncOAuthRepository(client=client)
    raw_token = await repo.create_refresh_token(
        grant_id="grant-1",
        user_id="user-1",
        client_id="client-1",
        scopes=["profile:read"],
    )

    stored = client.tables[TOKENS_TABLE][0]
    assert stored["token_hash"] == sha256(raw_token.encode()).hexdigest()
    assert raw_token not in stored.values()
    found = await repo.get_refresh_token(raw_token)
    assert found is not None and found.id == stored["id"]
    assert await repo.get_refresh_token("missing") is None

    revoked, replacement = await repo.rotate_refresh_token(raw_token)

    assert revoked.revoked_at is not None
    assert replacement != raw_token
    assert client.tables[TOKENS_TABLE][1]["rotated_from_id"] == revoked.id
    assert client.operations[(TOKENS_TABLE, "insert")] == 2
    assert client.operations[(TOKENS_TABLE, "update")] == 1
    assert client.operations[(TOKENS_TABLE, "select")] == 3


@pytest.mark.parametrize(
    ("rows", "raw_token", "message"),
    [
        ([], "missing", "Invalid refresh token."),
        (
            [_refresh_token_row("revoked", revoked_at=datetime.now(UTC).isoformat())],
            "revoked",
            "Refresh token is no longer valid.",
        ),
        (
            [_refresh_token_row("expired", expires_at=datetime.now(UTC) - timedelta(seconds=1))],
            "expired",
            "Refresh token is no longer valid.",
        ),
    ],
)
async def test_rotate_refresh_token_rejects_invalid_states(
    rows: list[dict[str, object]], raw_token: str, message: str
) -> None:
    repo = AsyncOAuthRepository(client=AsyncFakeSupabaseClient(refresh_tokens=rows))

    with pytest.raises(ValueError, match=message):
        await repo.rotate_refresh_token(raw_token)


async def test_revoke_refresh_token_returns_true_then_false() -> None:
    raw_token = "raw-token"
    client = AsyncFakeSupabaseClient(refresh_tokens=[_refresh_token_row(raw_token)])
    repo = AsyncOAuthRepository(client=client)

    assert await repo.revoke_refresh_token(raw_token) is True
    assert await repo.revoke_refresh_token(raw_token) is False
    assert await repo.revoke_refresh_token("missing") is False
    assert client.operations[(TOKENS_TABLE, "select")] == 3
    assert client.operations[(TOKENS_TABLE, "update")] == 1


async def test_revoke_grant_cascades_to_active_refresh_tokens() -> None:
    already_revoked_at = datetime.now(UTC).isoformat()
    client = AsyncFakeSupabaseClient(
        grants=[_grant_row()],
        refresh_tokens=[
            _refresh_token_row("active", token_id="active-token"),
            _refresh_token_row(
                "revoked",
                token_id="revoked-token",
                revoked_at=already_revoked_at,
            ),
        ],
    )
    repo = AsyncOAuthRepository(client=client)

    assert await repo.revoke_grant("grant-1") is True
    assert client.tables[GRANTS_TABLE][0]["revoked_at"] is not None
    assert client.tables[TOKENS_TABLE][0]["revoked_at"] is not None
    assert client.tables[TOKENS_TABLE][1]["revoked_at"] == already_revoked_at
    assert client.operations[(GRANTS_TABLE, "update")] == 1
    assert client.operations[(TOKENS_TABLE, "update")] == 1

    assert await repo.revoke_grant("missing") is False
    assert client.operations[(GRANTS_TABLE, "update")] == 2
    assert client.operations[(TOKENS_TABLE, "update")] == 2


async def test_upsert_grant_raises_when_update_returns_no_rows() -> None:
    client = AsyncFakeSupabaseClient(
        grants=[_grant_row()], empty_responses={(GRANTS_TABLE, "update")}
    )
    repo = AsyncOAuthRepository(client=client)

    with pytest.raises(RuntimeError, match="OAuth grant row"):
        await repo.upsert_grant(
            user_id="user-1",
            client_id="client-1",
            redirect_uri="https://example.com/callback",
            scopes=["metrics:write"],
        )


@pytest.mark.parametrize(
    ("empty_response", "operation", "expected_message"),
    [
        ((GRANTS_TABLE, "insert"), "upsert_grant", "OAuth grant row"),
        ((CODES_TABLE, "insert"), "create_authorization_code", "authorization code row"),
        ((CODES_TABLE, "update"), "consume_authorization_code", "consumed OAuth code row"),
        ((TOKENS_TABLE, "insert"), "create_refresh_token", "refresh token row"),
        ((TOKENS_TABLE, "update"), "rotate_refresh_token", "revoked OAuth refresh token row"),
    ],
)
async def test_write_methods_raise_when_supabase_returns_no_rows(
    empty_response: tuple[str, str], operation: str, expected_message: str
) -> None:
    raw_code = "raw-code"
    raw_token = "raw-token"
    client = AsyncFakeSupabaseClient(
        authorization_codes=[_authorization_code_row(raw_code)],
        refresh_tokens=[_refresh_token_row(raw_token)],
        empty_responses={empty_response},
    )
    repo = AsyncOAuthRepository(client=client)
    calls: dict[str, Any] = {
        "upsert_grant": lambda: repo.upsert_grant(
            user_id="user-1",
            client_id="client-1",
            redirect_uri="https://example.com/callback",
            scopes=["profile:read"],
        ),
        "create_authorization_code": lambda: repo.create_authorization_code(
            grant_id="grant-1",
            user_id="user-1",
            client_id="client-1",
            redirect_uri="https://example.com/callback",
            scopes=["profile:read"],
            code_challenge="challenge",
            code_challenge_method="S256",
        ),
        "consume_authorization_code": lambda: repo.consume_authorization_code(raw_code),
        "create_refresh_token": lambda: repo.create_refresh_token(
            grant_id="grant-1",
            user_id="user-1",
            client_id="client-1",
            scopes=["profile:read"],
        ),
        "rotate_refresh_token": lambda: repo.rotate_refresh_token(raw_token),
    }

    with pytest.raises(RuntimeError, match=expected_message):
        await calls[operation]()


async def test_unconfigured_repository_raises_shared_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "supabase_url", None)
    monkeypatch.setattr(settings, "supabase_service_role_key", None)
    repo = AsyncOAuthRepository()

    with pytest.raises(OAuthRepositoryNotConfiguredError, match="Supabase is not configured"):
        await repo.get_grant_by_id("grant-1")


def test_build_client_constructs_authenticated_async_client_synchronously(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = object()
    calls: list[tuple[str, str]] = []

    def fake_async_client(url: str, key: str) -> object:
        calls.append((url, key))
        return sentinel

    monkeypatch.setattr(settings, "supabase_url", "https://supabase.example")
    monkeypatch.setattr(settings, "supabase_service_role_key", "service-role-key")
    monkeypatch.setattr(async_oauth_repo, "SupabaseAsyncClient", fake_async_client)

    repo = AsyncOAuthRepository()

    assert repo._client is sentinel
    assert calls == [("https://supabase.example", "service-role-key")]


def test_async_repository_has_coroutine_signature_parity() -> None:
    assert signature(AsyncOAuthRepository) == signature(OAuthRepository)
    method_names = [
        "get_active_grant",
        "get_grant_by_id",
        "upsert_grant",
        "create_authorization_code",
        "get_authorization_code",
        "consume_authorization_code",
        "create_refresh_token",
        "get_refresh_token",
        "rotate_refresh_token",
        "revoke_refresh_token",
        "revoke_grant",
    ]

    for method_name in method_names:
        async_method = getattr(AsyncOAuthRepository, method_name)
        sync_method = getattr(OAuthRepository, method_name)
        assert iscoroutinefunction(async_method), method_name
        assert signature(async_method) == signature(sync_method), method_name
