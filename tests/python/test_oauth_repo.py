from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import pytest
from postgrest.exceptions import APIError as PostgRESTAPIError

import backend.repos.oauth_repo as oauth_repo_module
from backend.repos.oauth_repo import OAuthRepository


@dataclass
class _Response:
    data: list[dict[str, Any]]


class _FakeQuery:
    def __init__(self, client: _FakeClient) -> None:
        self._client = client
        self._operation = ""
        self._payload: dict[str, Any] | None = None
        self._filters: dict[str, Any] = {}

    def select(self, _columns: str) -> _FakeQuery:
        self._operation = "select"
        return self

    def insert(self, payload: dict[str, Any]) -> _FakeQuery:
        self._operation = "insert"
        self._payload = payload
        return self

    def update(self, payload: dict[str, Any]) -> _FakeQuery:
        self._operation = "update"
        self._payload = payload
        return self

    def eq(self, field: str, value: Any) -> _FakeQuery:
        self._filters[field] = value
        return self

    def is_(self, field: str, value: Any) -> _FakeQuery:
        self._filters[field] = None if value == "null" else value
        return self

    def limit(self, _count: int) -> _FakeQuery:
        return self

    def execute(self) -> _Response:
        return self._client.execute(self)


class _FakeClient:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = deepcopy(rows)
        self.calls = {"select": 0, "insert": 0, "update": 0}
        self.failures: dict[str, list[tuple[PostgRESTAPIError, bool]]] = {}

    def table(self, _name: str) -> _FakeQuery:
        return _FakeQuery(self)

    def fail_next(
        self, operation: str, error: PostgRESTAPIError, *, after_operation: bool = False
    ) -> None:
        self.failures.setdefault(operation, []).append((error, after_operation))

    def execute(self, query: _FakeQuery) -> _Response:
        operation = query._operation
        self.calls[operation] += 1
        failure = self.failures.get(operation, [])
        pending = failure.pop(0) if failure else None
        if pending is not None and not pending[1]:
            raise pending[0]

        if operation == "select":
            data = [
                deepcopy(row)
                for row in self.rows
                if all(row.get(field) == value for field, value in query._filters.items())
            ][:1]
        elif operation == "insert":
            assert query._payload is not None
            self.rows.append(deepcopy(query._payload))
            data = [deepcopy(query._payload)]
        elif operation == "update":
            assert query._payload is not None
            data = []
            for row in self.rows:
                if all(row.get(field) == value for field, value in query._filters.items()):
                    row.update(deepcopy(query._payload))
                    data.append(deepcopy(row))
        else:
            raise AssertionError(f"Unexpected fake query operation: {operation}")

        if pending is not None:
            raise pending[0]
        return _Response(data=data)


def _grant(*, scopes: list[str] | None = None) -> dict[str, Any]:
    return {
        "id": "grant-1",
        "user_id": "athlete-1",
        "client_id": "https://coach.example",
        "redirect_uri": "https://coach.example",
        "scopes": scopes or ["profile:read"],
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "revoked_at": None,
    }


def _api_error(code: str | int) -> PostgRESTAPIError:
    return PostgRESTAPIError(
        {
            "message": "JSON could not be generated",
            "code": code,
            "hint": None,
            "details": None,
        }
    )


def _upsert(repository: OAuthRepository, scopes: list[str]) -> Any:
    return repository.upsert_grant(
        user_id="athlete-1",
        client_id="https://coach.example",
        redirect_uri="https://coach.example",
        scopes=scopes,
    )


def test_upsert_grant_returns_existing_grant_without_rewriting_unchanged_scopes() -> None:
    client = _FakeClient([_grant(scopes=["metrics:write", "profile:read"])])
    repository = OAuthRepository(client=client)

    result = _upsert(repository, ["profile:read"])

    assert result.id == "grant-1"
    assert client.calls == {"select": 1, "insert": 0, "update": 0}
    assert client.rows[0]["updated_at"] == "2026-01-01T00:00:00+00:00"


def test_upsert_grant_updates_when_requested_scope_is_missing() -> None:
    client = _FakeClient([_grant()])
    repository = OAuthRepository(client=client)

    result = _upsert(repository, ["metrics:write"])

    assert result.scopes == ["metrics:write", "profile:read"]
    assert client.calls == {"select": 1, "insert": 0, "update": 1}


@pytest.mark.parametrize("code", ["502", "503", "504", 502, 503, 504])
def test_upsert_grant_retries_transient_gateway_errors(
    code: str | int, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _FakeClient([_grant()])
    client.fail_next("select", _api_error(code))
    repository = OAuthRepository(client=client)
    sleeps: list[float] = []
    monkeypatch.setattr(oauth_repo_module.time, "sleep", sleeps.append)

    result = _upsert(repository, ["profile:read"])

    assert result.id == "grant-1"
    assert client.calls == {"select": 2, "insert": 0, "update": 0}
    assert sleeps == [0.25]


def test_upsert_grant_retry_observes_ambiguous_insert_without_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient([])
    client.fail_next("insert", _api_error("504"), after_operation=True)
    repository = OAuthRepository(client=client)
    monkeypatch.setattr(oauth_repo_module.time, "sleep", lambda _delay: None)

    result = _upsert(repository, ["profile:read"])

    assert result.scopes == ["profile:read"]
    assert client.calls == {"select": 2, "insert": 1, "update": 0}
    assert len(client.rows) == 1


def test_upsert_grant_retry_observes_ambiguous_update_without_rewriting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient([_grant()])
    client.fail_next("update", _api_error("504"), after_operation=True)
    repository = OAuthRepository(client=client)
    monkeypatch.setattr(oauth_repo_module.time, "sleep", lambda _delay: None)

    result = _upsert(repository, ["metrics:write"])

    assert result.scopes == ["metrics:write", "profile:read"]
    assert client.calls == {"select": 2, "insert": 0, "update": 1}


def test_upsert_grant_stops_after_one_gateway_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient([_grant()])
    first_error = _api_error("504")
    final_error = _api_error("504")
    client.fail_next("select", first_error)
    client.fail_next("select", final_error)
    repository = OAuthRepository(client=client)
    sleeps: list[float] = []
    monkeypatch.setattr(oauth_repo_module.time, "sleep", sleeps.append)

    with pytest.raises(PostgRESTAPIError) as excinfo:
        _upsert(repository, ["profile:read"])

    assert excinfo.value is final_error
    assert client.calls == {"select": 2, "insert": 0, "update": 0}
    assert sleeps == [0.25]


def test_upsert_grant_does_not_retry_unique_violation(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient([])
    error = _api_error("23505")
    client.fail_next("insert", error)
    repository = OAuthRepository(client=client)
    sleeps: list[float] = []
    monkeypatch.setattr(oauth_repo_module.time, "sleep", sleeps.append)

    with pytest.raises(PostgRESTAPIError) as excinfo:
        _upsert(repository, ["profile:read"])

    assert excinfo.value is error
    assert client.calls == {"select": 1, "insert": 1, "update": 0}
    assert sleeps == []
