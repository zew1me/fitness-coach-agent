"""Tests for the centralized PostgREST error handling.

`_postgrest_http_status` classifies a Postgres SQLSTATE (surfaced on
``APIError.code``) into the HTTP status a client should see, and the global
``@app.exception_handler(PostgRESTAPIError)`` applies it to *every* endpoint —
including ones with no local try/except (which previously surfaced a raw 500).
"""

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from postgrest.exceptions import APIError as PostgRESTAPIError

import api.index as api_index
from backend.models.auth import UserContext


def _api_error(code: str | None) -> PostgRESTAPIError:
    return PostgRESTAPIError({"message": "boom", "code": code, "hint": None, "details": None})


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("22P02", 422),  # invalid_text_representation (bad uuid) → client fault
        ("22007", 422),  # invalid_datetime_format → client fault
        ("23505", 409),  # unique_violation → conflict
        ("23502", 503),  # not_null_violation → server omitted a column, not client input
        ("23503", 422),  # foreign_key_violation → other class 23 → client fault
        ("23514", 422),  # check_violation → other class 23 → client fault
        ("PGRST205", 503),  # schema-cache miss → outage
        ("42501", 503),  # insufficient_privilege → not a client-input fault
        (None, 503),  # missing code → treat as outage
    ],
)
def test_postgrest_http_status_mapping(code: str | None, expected: int) -> None:
    assert api_index._postgrest_http_status(_api_error(code)) == expected


def test_postgrest_http_status_non_string_code_is_503() -> None:
    exc = _api_error("22P02")
    # Defensive: a non-str code must not blow up on code[:2]. object.__setattr__
    # bypasses the str-typed attribute so we can exercise the guard.
    object.__setattr__(exc, "code", 22)
    assert api_index._postgrest_http_status(exc) == 503


class _RaisingQuery:
    """Fluent PostgREST query stub whose terminal .execute() raises."""

    def __init__(self, exc: PostgRESTAPIError) -> None:
        self._exc = exc

    def __getattr__(self, _name: str) -> Any:
        # .table(...).update(...).eq(...).is_(...) all chain back to self.
        return lambda *_a, **_k: self

    def execute(self) -> Any:
        raise self._exc


class _RaisingClient:
    def __init__(self, exc: PostgRESTAPIError) -> None:
        self._query = _RaisingQuery(exc)

    def table(self, _name: str) -> _RaisingQuery:
        return self._query


@pytest.fixture
def as_user():
    api_index.app.dependency_overrides[api_index.require_user_context] = lambda: UserContext(
        user_id="athlete-1",
        scopes=["plans:write"],
        client_id="test-client",
        grant_id="grant-1",
    )
    yield
    api_index.app.dependency_overrides.clear()


async def _post_confirm_threshold() -> Any:
    transport = ASGITransport(app=api_index.app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post("/api/engine/confirm-threshold", json={"sport": "cycling"})


@pytest.mark.usefixtures("as_user")
class TestUnwrappedEndpointCoveredByHandler:
    """confirm_threshold has no local try/except; the global handler now covers it."""

    async def test_client_fault_maps_to_422_not_500(self, monkeypatch) -> None:
        monkeypatch.setattr(
            api_index.repo, "_require_client", lambda: _RaisingClient(_api_error("22P02"))
        )
        response = await _post_confirm_threshold()
        assert response.status_code == 422
        assert response.json() == {"detail": "Invalid request."}

    async def test_unique_violation_maps_to_409(self, monkeypatch) -> None:
        monkeypatch.setattr(
            api_index.repo, "_require_client", lambda: _RaisingClient(_api_error("23505"))
        )
        response = await _post_confirm_threshold()
        assert response.status_code == 409

    async def test_outage_maps_to_503(self, monkeypatch) -> None:
        monkeypatch.setattr(
            api_index.repo, "_require_client", lambda: _RaisingClient(_api_error("PGRST205"))
        )
        response = await _post_confirm_threshold()
        assert response.status_code == 503
