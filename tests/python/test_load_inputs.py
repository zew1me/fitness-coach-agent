"""Exercise the real Supabase query serializer against controlled HTTP pages."""

from datetime import date
from uuid import UUID

import httpx
import pytest
from postgrest.exceptions import APIError as PostgRESTAPIError
from pydantic import ValidationError

from backend.repos.supabase_repo import SupabaseRepository
from supabase import ClientOptions, create_client

DAY = date(2026, 7, 3)


def _row(number: int, *, day: date = DAY, tss: float | None = 1.0) -> dict:
    return {"id": str(UUID(int=number)), "activity_date": day.isoformat(), "tss": tss}


def _repo(handler) -> SupabaseRepository:
    client = create_client(
        "https://local.test",
        "test-key",
        options=ClientOptions(httpx_client=httpx.Client(transport=httpx.MockTransport(handler))),
    )
    return SupabaseRepository(client=client)


@pytest.mark.parametrize("server_cap", [37, 500])
@pytest.mark.parametrize("sport", [None, "cycling"])
async def test_reads_all_pages_even_below_requested_size(server_cap, sport) -> None:
    # Same-day activities cross page boundaries; the oldest activity has the largest UUID.
    older = date(2026, 7, 2)
    rows = [_row(i) for i in range(1, 1102)] + [_row(1102, day=older, tss=84)]
    rows[0]["tss"] = None
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        params = request.url.params
        assert request.url.path == "/rest/v1/activities"
        assert params["select"] == "id,activity_date,tss"
        assert params["user_id"] == "eq.athlete-1"
        assert params.get_list("activity_date") == ["gte.2026-07-02", "lte.2026-07-03"]
        assert params["order"] == "id.asc"
        assert params["limit"] == "500"
        assert params.get("sport") == (f"eq.{sport}" if sport is not None else None)
        cursor = params.get("id", "gt." + str(UUID(int=0))).removeprefix("gt.")
        page = [row for row in rows if row["id"] > cursor][:server_cap]
        return httpx.Response(200, json=page)

    totals = await _repo(respond).get_daily_activity_tss(
        "athlete-1", since=older, through=DAY, sport=sport
    )

    assert totals == {DAY: 1100.0, older: 84.0}
    assert len(requests) == (len(rows) + server_cap - 1) // server_cap + 1
    assert requests[-1].url.params["id"] == "gt." + rows[-1]["id"]


async def test_empty_history() -> None:
    repo = _repo(lambda _: httpx.Response(200, json=[]))
    assert await repo.get_daily_activity_tss("athlete-1", since=DAY, through=DAY) == {}


async def test_later_page_failure_propagates_without_returning_partial_totals() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if "id" in request.url.params:
            return httpx.Response(503, json={"code": "PGRST000", "message": "unavailable"})
        return httpx.Response(200, json=[_row(1)])

    with pytest.raises(PostgRESTAPIError):
        await _repo(respond).get_daily_activity_tss("athlete-1", since=DAY, through=DAY)


@pytest.mark.parametrize("invalid", [{"id": "not-a-uuid"}, {"tss": "NaN"}])
async def test_invalid_inputs_fail_closed(invalid) -> None:
    repo = _repo(lambda _: httpx.Response(200, json=[{**_row(1), **invalid}]))
    with pytest.raises(ValidationError):
        await repo.get_daily_activity_tss("athlete-1", since=DAY, through=DAY)


async def test_nonadvancing_cursor_fails_instead_of_looping() -> None:
    repo = _repo(lambda _: httpx.Response(200, json=[_row(1)]))
    with pytest.raises(ValueError, match="cursor did not advance"):
        await repo.get_daily_activity_tss("athlete-1", since=DAY, through=DAY)


async def test_reversed_window_is_rejected_before_reading() -> None:
    def unexpected_request(_: httpx.Request) -> httpx.Response:
        raise AssertionError("A reversed window must not issue a query")

    with pytest.raises(ValueError, match="start must not be after"):
        await _repo(unexpected_request).get_daily_activity_tss(
            "athlete-1", since=DAY, through=date(2026, 7, 2)
        )
