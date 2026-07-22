"""Unit tests for the Supabase-backed IntervalsRepository RPC contract.

`replace_connection` must delegate the revoke-and-insert swap to the
`replace_intervals_connection` RPC in a single call (issue #345). The fake
client below refuses table-level access so any regression back to the old
two-step UPDATE + INSERT fails loudly.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.models.intervals import IntervalsConnectionCreate
from backend.repos.intervals_repo import IntervalsRepository


class FakeRpcQuery:
    def __init__(self, data: object) -> None:
        self._data = data

    def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._data)


class FakeAtomicIntervalsClient:
    """Emulates the `replace_intervals_connection` RPC over in-memory rows."""

    def __init__(self, *, rpc_data_override: object = None) -> None:
        self.rows: list[dict[str, object]] = []
        self.rpc_calls: list[tuple[str, dict[str, object]]] = []
        self._rpc_data_override = rpc_data_override

    def table(self, table_name: str) -> None:
        raise AssertionError(
            "replace_connection must not issue table-level calls; the atomic RPC "
            f"owns the revoke-and-insert swap (got table({table_name!r}))."
        )

    def rpc(self, function_name: str, params: dict[str, object]) -> FakeRpcQuery:
        assert function_name == "replace_intervals_connection"
        self.rpc_calls.append((function_name, params))
        if self._rpc_data_override is not None:
            return FakeRpcQuery(self._rpc_data_override)

        now = datetime.now(UTC).isoformat()
        for row in self.rows:
            if row["user_id"] == params["p_user_id"] and row["revoked_at"] is None:
                row["revoked_at"] = now
                row["updated_at"] = now
        row = {
            "id": str(uuid4()),
            "user_id": params["p_user_id"],
            "intervals_athlete_id": params["p_intervals_athlete_id"],
            "intervals_athlete_name": params["p_intervals_athlete_name"],
            "scopes": params["p_scopes"],
            "access_token_ciphertext": params["p_access_token_ciphertext"],
            "token_type": params["p_token_type"],
            "connected_at": now,
            "updated_at": now,
            "revoked_at": None,
        }
        self.rows.append(row)
        # `returns public.intervals_connections` is a single composite row, so
        # PostgREST hands back a JSON object — not a list. Returning a dict here
        # keeps the fake honest about the shape the repo must parse.
        return FakeRpcQuery(dict(row))


def _connection(athlete_id: str = "i135168") -> IntervalsConnectionCreate:
    return IntervalsConnectionCreate(
        user_id="coach-user-1",
        intervals_athlete_id=athlete_id,
        intervals_athlete_name="Nigel",
        scopes=["ACTIVITY:READ"],
        access_token_ciphertext="ciphertext",
        token_type="Bearer",
    )


def test_replace_connection_swaps_via_single_atomic_rpc() -> None:
    client = FakeAtomicIntervalsClient()
    repo = IntervalsRepository(client)

    record = repo.replace_connection(_connection())

    assert client.rpc_calls == [
        (
            "replace_intervals_connection",
            {
                "p_user_id": "coach-user-1",
                "p_intervals_athlete_id": "i135168",
                "p_intervals_athlete_name": "Nigel",
                "p_scopes": ["ACTIVITY:READ"],
                "p_access_token_ciphertext": "ciphertext",
                "p_token_type": "Bearer",
            },
        )
    ]
    assert record.user_id == "coach-user-1"
    assert record.intervals_athlete_id == "i135168"
    assert record.revoked_at is None


def test_replace_connection_revokes_prior_active_row_in_the_same_call() -> None:
    client = FakeAtomicIntervalsClient()
    repo = IntervalsRepository(client)

    first = repo.replace_connection(_connection("i111"))
    second = repo.replace_connection(_connection("i222"))

    assert first.id != second.id
    active_rows = [row for row in client.rows if row["revoked_at"] is None]
    assert [row["id"] for row in active_rows] == [second.id]
    revoked_rows = [row for row in client.rows if row["revoked_at"] is not None]
    assert [row["id"] for row in revoked_rows] == [first.id]


def test_replace_connection_raises_when_rpc_returns_no_row() -> None:
    repo = IntervalsRepository(FakeAtomicIntervalsClient(rpc_data_override={}))

    with pytest.raises(RuntimeError, match="did not return the Intervals connection row"):
        repo.replace_connection(_connection())


def test_replace_connection_rejects_non_object_rpc_payload() -> None:
    repo = IntervalsRepository(
        FakeAtomicIntervalsClient(rpc_data_override=["unexpected-array-shape"])
    )

    with pytest.raises(TypeError, match="rows must be objects"):
        repo.replace_connection(_connection())


class _StaticRpcClient:
    """Returns a fixed `response.data` regardless of RPC params.

    `FakeAtomicIntervalsClient` treats `rpc_data_override=None` as "no override"
    (it still synthesizes a row), so it cannot represent a literal `data=None`
    PostgREST response. This minimal client can.
    """

    def __init__(self, data: object) -> None:
        self._data = data

    def table(self, table_name: str) -> None:
        raise AssertionError(f"unexpected table({table_name!r}) access")

    def rpc(self, function_name: str, params: dict[str, object]) -> FakeRpcQuery:
        assert function_name == "replace_intervals_connection"
        return FakeRpcQuery(self._data)


def test_replace_connection_raises_when_rpc_returns_none() -> None:
    """A literal `response.data is None` (distinct from `{}`) must also surface as
    the same RuntimeError rather than falling through to `_parse_connection(None)`."""
    repo = IntervalsRepository(_StaticRpcClient(None))

    with pytest.raises(RuntimeError, match="did not return the Intervals connection row"):
        repo.replace_connection(_connection())


def test_replace_connection_rejects_scalar_rpc_payload() -> None:
    """A truthy but non-dict, non-list payload (e.g. a bare string) must also be
    rejected — the guard is about the row shape, not just "not a list"."""
    repo = IntervalsRepository(FakeAtomicIntervalsClient(rpc_data_override="not-a-row"))

    with pytest.raises(TypeError, match="rows must be objects"):
        repo.replace_connection(_connection())


def test_replace_connection_parses_every_field_from_the_composite_row() -> None:
    """The composite-row response must round-trip every `IntervalsConnectionRecord`
    field, not just the few spot-checked in the happy-path test above."""
    client = FakeAtomicIntervalsClient()
    repo = IntervalsRepository(client)

    record = repo.replace_connection(_connection())

    row = client.rows[0]
    assert record.id == row["id"]
    assert record.user_id == "coach-user-1"
    assert record.intervals_athlete_id == "i135168"
    assert record.intervals_athlete_name == "Nigel"
    assert record.scopes == ["ACTIVITY:READ"]
    assert record.access_token_ciphertext == "ciphertext"
    assert record.token_type == "Bearer"
    assert record.connected_at.isoformat() == row["connected_at"]
    assert record.updated_at.isoformat() == row["updated_at"]
    assert record.revoked_at is None


def test_replace_connection_forwards_model_defaults_for_scopes_and_token_type() -> None:
    """`IntervalsConnectionCreate` defaults (empty scopes, `token_type="Bearer"`) must
    be forwarded to the RPC verbatim rather than silently dropped or re-defaulted
    client-side — the migration's own `coalesce()` fallbacks only apply server-side."""
    client = FakeAtomicIntervalsClient()
    repo = IntervalsRepository(client)
    connection = IntervalsConnectionCreate(
        user_id="coach-user-2",
        intervals_athlete_id="i999",
        access_token_ciphertext="ciphertext-2",
    )

    record = repo.replace_connection(connection)

    _, params = client.rpc_calls[0]
    assert params["p_scopes"] == []
    assert params["p_token_type"] == "Bearer"
    assert params["p_intervals_athlete_name"] is None
    assert record.scopes == []
    assert record.token_type == "Bearer"
