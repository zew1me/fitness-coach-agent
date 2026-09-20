"""Opt-in integration coverage for the async OAuth repository.

Run against an isolated local Supabase stack with::

    bun run db:start
    RUN_DB_TESTS=1 SUPABASE_URL=http://127.0.0.1:54321 \
      SUPABASE_SERVICE_ROLE_KEY=<local-service-role-key> \
      uv run pytest -m db tests/python/test_async_oauth_repo_db.py

The explicit opt-in prevents accidental writes when hosted credentials are present.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from hashlib import sha256
from typing import Any, cast

import pytest

from backend.config import settings
from backend.repos.async_oauth_repo import AsyncOAuthRepository
from supabase import AsyncClient as SupabaseAsyncClient

_GRANTS_TABLE = "oauth_grants"
_AUTHORIZATION_CODES_TABLE = "oauth_authorization_codes"
_REFRESH_TOKENS_TABLE = "oauth_refresh_tokens"
_SUPABASE_CONFIGURED = bool(
    os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
)
_RUN_DB_TESTS = os.environ.get("RUN_DB_TESTS") == "1"

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(
        not (_SUPABASE_CONFIGURED and _RUN_DB_TESTS),
        reason=("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set, or RUN_DB_TESTS=1 not provided"),
    ),
]


def _single_row(data: object) -> dict[str, Any]:
    """Narrow an untyped PostgREST response to its single expected row."""
    assert isinstance(data, list)
    assert len(data) == 1
    row = data[0]
    assert isinstance(row, dict)
    return cast(dict[str, Any], row)


async def _close_repository(repo: AsyncOAuthRepository) -> None:
    """Close the lazy PostgREST HTTP client opened by a live repository."""
    await repo._require_client().postgrest.aclose()


@pytest.fixture()
async def live_repository() -> AsyncIterator[tuple[AsyncOAuthRepository, str]]:
    """Provide a directly constructed repository and clean up its isolated grant tree."""
    repo = AsyncOAuthRepository()
    user_id = f"async-oauth-db-{uuid.uuid4()}"
    try:
        yield repo, user_id
    finally:
        client = repo._require_client()
        await client.table(_GRANTS_TABLE).delete().eq("user_id", user_id).execute()
        await _close_repository(repo)


async def test_direct_async_client_construction_reaches_postgrest() -> None:
    """Direct ``AsyncClient`` construction must authenticate without async bootstrap."""
    repo = AsyncOAuthRepository()
    try:
        client = repo._require_client()
        assert isinstance(client, SupabaseAsyncClient)
        assert client.supabase_url == settings.supabase_url

        response = await client.table(_GRANTS_TABLE).select("id").limit(1).execute()

        assert isinstance(response.data, list)
    finally:
        await _close_repository(repo)


async def test_full_async_oauth_lifecycle_persists_hashes_and_revocations(
    live_repository: tuple[AsyncOAuthRepository, str],
) -> None:
    """Round-trip grants, codes, rotation, and cascading revocation through PostgREST."""
    repo, user_id = live_repository
    client_id = f"db-client-{uuid.uuid4()}"
    redirect_uri = f"https://example.com/oauth/{uuid.uuid4()}"

    grant = await repo.upsert_grant(
        user_id=user_id,
        client_id=client_id,
        redirect_uri=redirect_uri,
        scopes=["profile:read"],
    )
    updated_grant = await repo.upsert_grant(
        user_id=user_id,
        client_id=client_id,
        redirect_uri=redirect_uri,
        scopes=["metrics:write", "profile:read"],
    )

    assert updated_grant.id == grant.id
    assert updated_grant.scopes == ["metrics:write", "profile:read"]
    assert await repo.get_grant_by_id(grant.id) == updated_grant
    assert (
        await repo.get_active_grant(
            user_id=user_id,
            client_id=client_id,
            redirect_uri=redirect_uri,
        )
        == updated_grant
    )

    raw_code = await repo.create_authorization_code(
        grant_id=grant.id,
        user_id=user_id,
        client_id=client_id,
        redirect_uri=redirect_uri,
        scopes=updated_grant.scopes,
        code_challenge="integration-test-challenge",
        code_challenge_method="S256",
    )
    authorization_code = await repo.get_authorization_code(raw_code)
    assert authorization_code is not None
    code_response = await (
        repo._require_client()
        .table(_AUTHORIZATION_CODES_TABLE)
        .select("*")
        .eq("id", authorization_code.id)
        .execute()
    )
    code_row = _single_row(code_response.data)
    assert code_row["token_hash"] == sha256(raw_code.encode()).hexdigest()
    assert raw_code not in code_row.values()

    consumed_code = await repo.consume_authorization_code(raw_code)
    assert consumed_code.consumed_at is not None

    raw_refresh_token = await repo.create_refresh_token(
        grant_id=grant.id,
        user_id=user_id,
        client_id=client_id,
        scopes=updated_grant.scopes,
    )
    refresh_token = await repo.get_refresh_token(raw_refresh_token)
    assert refresh_token is not None
    refresh_response = await (
        repo._require_client()
        .table(_REFRESH_TOKENS_TABLE)
        .select("*")
        .eq("id", refresh_token.id)
        .execute()
    )
    refresh_row = _single_row(refresh_response.data)
    assert refresh_row["token_hash"] == sha256(raw_refresh_token.encode()).hexdigest()
    assert raw_refresh_token not in refresh_row.values()

    revoked_token, replacement_raw_token = await repo.rotate_refresh_token(raw_refresh_token)
    replacement_token = await repo.get_refresh_token(replacement_raw_token)
    assert revoked_token.revoked_at is not None
    assert replacement_token is not None
    assert replacement_token.rotated_from_id == revoked_token.id
    assert replacement_raw_token != raw_refresh_token

    assert await repo.revoke_grant(grant.id) is True
    revoked_grant = await repo.get_grant_by_id(grant.id)
    cascaded_token = await repo.get_refresh_token(replacement_raw_token)
    assert revoked_grant is not None and revoked_grant.revoked_at is not None
    assert cascaded_token is not None and cascaded_token.revoked_at is not None
