"""Unit tests for the Supabase-backed StravaRepository RPC contract.

`replace_connection` delegates the revoke-and-insert swap to a single atomic
RPC, and `rotate_tokens` uses a compare-and-swap on the previously observed
expiry so a stale refresh response can never overwrite a newer rotation. The
fake client below models both RPCs over in-memory rows and refuses table-level
access for the swap so a regression to two-step writes fails loudly.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.models.strava import (
    StravaConnectionCreate,
    StravaTokenRotation,
)
from backend.repos.strava_repo import StravaRepository

_EXPIRES = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)


class FakeRpcQuery:
    def __init__(self, data: object) -> None:
        self._data = data

    def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._data)


class FakeStravaClient:
    """Emulates replace_strava_connection + rotate_strava_tokens over rows."""

    def __init__(self, *, rpc_data_override: object | None = None) -> None:
        self.rows: list[dict[str, object]] = []
        self.rpc_calls: list[tuple[str, dict[str, object]]] = []
        self._rpc_data_override = rpc_data_override

    def table(self, table_name: str) -> None:
        raise AssertionError(
            f"connection swap/rotation must go through the atomic RPCs, not table({table_name!r})."
        )

    def rpc(self, function_name: str, params: dict[str, object]) -> FakeRpcQuery:
        self.rpc_calls.append((function_name, params))
        if self._rpc_data_override is not None:
            return FakeRpcQuery(self._rpc_data_override)
        if function_name == "replace_strava_connection":
            return FakeRpcQuery(self._replace(params))
        if function_name == "rotate_strava_tokens":
            return FakeRpcQuery(self._rotate(params))
        raise AssertionError(f"unexpected rpc({function_name!r})")

    def _replace(self, params: dict[str, object]) -> dict[str, object]:
        now = datetime.now(UTC).isoformat()
        for row in self.rows:
            if row["user_id"] == params["p_user_id"] and row["revoked_at"] is None:
                row["revoked_at"] = now
        row = {
            "id": str(uuid4()),
            "user_id": params["p_user_id"],
            "strava_athlete_id": params["p_strava_athlete_id"],
            "strava_athlete_name": params["p_strava_athlete_name"],
            "scopes": params["p_scopes"],
            "access_token_ciphertext": params["p_access_token_ciphertext"],
            "refresh_token_ciphertext": params["p_refresh_token_ciphertext"],
            "token_type": params["p_token_type"],
            "expires_at": params["p_expires_at"],
            "authorization_version": params["p_authorization_version"],
            "consented_at": now,
            "connected_at": now,
            "updated_at": now,
            "last_sync_at": None,
            "revoked_at": None,
        }
        self.rows.append(row)
        return dict(row)

    def _rotate(self, params: dict[str, object]) -> dict[str, object] | None:
        for row in self.rows:
            if (
                row["id"] == params["p_connection_id"]
                and row["revoked_at"] is None
                and row["expires_at"] == params["p_expected_expires_at"]
            ):
                row["access_token_ciphertext"] = params["p_access_token_ciphertext"]
                row["refresh_token_ciphertext"] = params["p_refresh_token_ciphertext"]
                row["token_type"] = params["p_token_type"]
                row["expires_at"] = params["p_expires_at"]
                row["updated_at"] = datetime.now(UTC).isoformat()
                return dict(row)
        return None


def _connection(athlete_id: int = 135168) -> StravaConnectionCreate:
    return StravaConnectionCreate(
        user_id="coach-user-1",
        strava_athlete_id=athlete_id,
        strava_athlete_name="Nigel",
        scopes=["read", "activity:read_all"],
        access_token_ciphertext="access-ct",
        refresh_token_ciphertext="refresh-ct",
        token_type="Bearer",
        expires_at=_EXPIRES,
        authorization_version="2026-07-21",
    )


def test_replace_connection_swaps_via_single_atomic_rpc() -> None:
    client = FakeStravaClient()
    repo = StravaRepository(client)

    record = repo.replace_connection(_connection())

    assert client.rpc_calls[0][0] == "replace_strava_connection"
    params = client.rpc_calls[0][1]
    assert params["p_user_id"] == "coach-user-1"
    assert params["p_strava_athlete_id"] == 135168
    assert params["p_refresh_token_ciphertext"] == "refresh-ct"
    assert params["p_expires_at"] == _EXPIRES.isoformat()
    assert record.strava_athlete_id == 135168
    assert record.revoked_at is None


def test_replace_connection_revokes_prior_active_row_in_same_call() -> None:
    client = FakeStravaClient()
    repo = StravaRepository(client)

    first = repo.replace_connection(_connection(111))
    second = repo.replace_connection(_connection(222))

    active = [row for row in client.rows if row["revoked_at"] is None]
    assert [row["id"] for row in active] == [second.id]
    assert first.id != second.id


def test_rotate_tokens_persists_new_pair_on_matching_expiry() -> None:
    client = FakeStravaClient()
    repo = StravaRepository(client)
    record = repo.replace_connection(_connection())

    new_expiry = _EXPIRES + timedelta(hours=6)
    rotated = repo.rotate_tokens(
        connection_id=record.id,
        expected_expires_at=_EXPIRES,
        rotation=StravaTokenRotation(
            access_token_ciphertext="new-access",
            refresh_token_ciphertext="new-refresh",
            token_type="Bearer",
            expires_at=new_expiry,
        ),
    )

    assert rotated is not None
    assert rotated.access_token_ciphertext == "new-access"
    assert rotated.refresh_token_ciphertext == "new-refresh"
    assert rotated.expires_at == new_expiry


def test_rotate_tokens_returns_none_when_cas_lost() -> None:
    """A concurrent rotation already advanced expires_at, so this stale write
    must no-op rather than overwrite the newer refresh token."""
    client = FakeStravaClient()
    repo = StravaRepository(client)
    record = repo.replace_connection(_connection())

    stale = repo.rotate_tokens(
        connection_id=record.id,
        expected_expires_at=_EXPIRES - timedelta(hours=1),  # never matched
        rotation=StravaTokenRotation(
            access_token_ciphertext="loser",
            refresh_token_ciphertext="loser-refresh",
            expires_at=_EXPIRES + timedelta(hours=6),
        ),
    )

    assert stale is None
    assert client.rows[0]["access_token_ciphertext"] == "access-ct"


def test_replace_connection_raises_when_rpc_returns_no_row() -> None:
    repo = StravaRepository(FakeStravaClient(rpc_data_override={}))
    with pytest.raises(RuntimeError, match="did not return the Strava connection row"):
        repo.replace_connection(_connection())


def test_replace_connection_rejects_non_object_rpc_payload() -> None:
    repo = StravaRepository(FakeStravaClient(rpc_data_override=["unexpected-array"]))
    with pytest.raises(TypeError, match="rows must be objects"):
        repo.replace_connection(_connection())
