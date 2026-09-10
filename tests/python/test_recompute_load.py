from copy import deepcopy
from datetime import date, timedelta

import pytest
from httpx import ASGITransport, AsyncClient, Response

import api.index as api_index
from backend.engine.training_load import compute_next_load
from backend.models.auth import UserContext
from backend.repos.supabase_repo import SupabaseRepository
from tests.python.test_supabase_repo import FakeSupabaseClient

USER_ID = "athlete-1"


def _user_context() -> UserContext:
    return UserContext(
        user_id=USER_ID,
        scopes=["plans:write"],
        client_id="test-client",
        grant_id="grant-1",
    )


@pytest.fixture
def as_athlete():
    api_index.app.dependency_overrides[api_index.require_user_context] = _user_context
    yield
    api_index.app.dependency_overrides.clear()


async def _post_recompute_load(body: dict[str, object]) -> Response:
    transport = ASGITransport(app=api_index.app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post("/api/engine/recompute-load", json=body)


def _load_rows(client: FakeSupabaseClient) -> list[dict[str, object]]:
    return client._tables["daily_load_snapshots"]._rows


def _text(value: object) -> str:
    assert isinstance(value, str)
    return value


def _number(value: object) -> float:
    assert isinstance(value, int | float)
    return float(value)


def _load_values(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    keys = ("snapshot_date", "sport", "daily_tss", "ctl", "atl", "tsb")
    return [{key: row.get(key) for key in keys} for row in rows]


def _activity_row(activity_date: date, tss: float) -> dict[str, object]:
    return {
        "id": f"activity-{activity_date.isoformat()}",
        "user_id": USER_ID,
        "sport": "cycling",
        "activity_date": activity_date.isoformat(),
        "tss": tss,
    }


def _snapshot_row(
    snapshot_date: date,
    *,
    ctl: float,
    atl: float,
    daily_tss: float = 0.0,
) -> dict[str, object]:
    return {
        "id": f"load-{snapshot_date.isoformat()}",
        "user_id": USER_ID,
        "snapshot_date": snapshot_date.isoformat(),
        "sport": None,
        "daily_tss": daily_tss,
        "ctl": ctl,
        "atl": atl,
        "tsb": ctl - atl,
    }


@pytest.mark.usefixtures("as_athlete")
async def test_recompute_load_with_default_since_is_idempotent(monkeypatch) -> None:
    today = date.today()
    client = FakeSupabaseClient(
        activity_rows=[_activity_row(today, 84.0)],
        daily_load_snapshot_rows=[_snapshot_row(today, ctl=60.0, atl=70.0, daily_tss=84.0)],
    )
    monkeypatch.setattr(api_index, "repo", SupabaseRepository(client=client))

    first_response = await _post_recompute_load({})
    assert first_response.status_code == 200, first_response.text
    first_rows = deepcopy(_load_values(client._tables["daily_load_snapshots"]._rows))

    second_response = await _post_recompute_load({})
    assert second_response.status_code == 200, second_response.text
    second_rows = _load_values(client._tables["daily_load_snapshots"]._rows)

    assert second_rows == first_rows


@pytest.mark.usefixtures("as_athlete")
async def test_backward_recompute_seeds_from_day_before_window(monkeypatch) -> None:
    today = date.today()
    since = today - timedelta(days=2)
    seed_date = since - timedelta(days=1)
    seed_ctl = 12.0
    seed_atl = 18.0
    first_day_tss = 84.0
    client = FakeSupabaseClient(
        activity_rows=[
            _activity_row(since, first_day_tss),
            _activity_row(today, 45.0),
        ],
        daily_load_snapshot_rows=[
            _snapshot_row(seed_date, ctl=seed_ctl, atl=seed_atl),
            _snapshot_row(today, ctl=70.0, atl=80.0, daily_tss=45.0),
        ],
    )
    monkeypatch.setattr(api_index, "repo", SupabaseRepository(client=client))

    response = await _post_recompute_load({"since": since.isoformat()})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["seed_date"] == seed_date.isoformat()
    assert payload["seed_ctl"] == seed_ctl
    assert payload["seed_atl"] == seed_atl

    first_snapshot = next(
        row
        for row in client._tables["daily_load_snapshots"]._rows
        if row["snapshot_date"] == since.isoformat()
    )
    expected_ctl, expected_atl, expected_tsb = compute_next_load(seed_ctl, seed_atl, first_day_tss)
    assert first_snapshot["ctl"] == round(expected_ctl, 1)
    assert first_snapshot["atl"] == round(expected_atl, 1)
    assert first_snapshot["tsb"] == round(expected_tsb, 1)


@pytest.mark.usefixtures("as_athlete")
async def test_recompute_load_without_seed_reports_degraded_fallback(monkeypatch) -> None:
    today = date.today()
    client = FakeSupabaseClient(activity_rows=[_activity_row(today, 42.0)])
    monkeypatch.setattr(api_index, "repo", SupabaseRepository(client=client))

    response = await _post_recompute_load({})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["seed_date"] is None
    assert payload["seed_ctl"] == 0.0
    assert payload["seed_atl"] == 0.0

    snapshot = client._tables["daily_load_snapshots"]._rows[0]
    expected_ctl, expected_atl, expected_tsb = compute_next_load(0.0, 0.0, 42.0)
    assert snapshot["ctl"] == round(expected_ctl, 1)
    assert snapshot["atl"] == round(expected_atl, 1)
    assert snapshot["tsb"] == round(expected_tsb, 1)


@pytest.mark.usefixtures("as_athlete")
async def test_stale_seed_rebuilds_the_gap_before_the_window(monkeypatch) -> None:
    today = date.today()
    since = today - timedelta(days=1)
    seed_date = since - timedelta(days=4)
    gap_activity_date = seed_date + timedelta(days=2)
    seed_ctl = 30.0
    seed_atl = 40.0
    gap_tss = 90.0
    since_tss = 60.0
    client = FakeSupabaseClient(
        activity_rows=[
            _activity_row(gap_activity_date, gap_tss),
            _activity_row(since, since_tss),
        ],
        daily_load_snapshot_rows=[_snapshot_row(seed_date, ctl=seed_ctl, atl=seed_atl)],
    )
    monkeypatch.setattr(api_index, "repo", SupabaseRepository(client=client))

    response = await _post_recompute_load({"since": since.isoformat()})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["seed_date"] == seed_date.isoformat()
    assert payload["rebuild_start"] == (seed_date + timedelta(days=1)).isoformat()

    # Walking the gap day by day is what a stale seed applied verbatim at `since` would skip.
    ctl, atl = seed_ctl, seed_atl
    expected: dict[date, tuple[float, float, float]] = {}
    day = seed_date + timedelta(days=1)
    while day <= today:
        tss = {gap_activity_date: gap_tss, since: since_tss}.get(day, 0.0)
        ctl, atl, tsb = compute_next_load(ctl, atl, tss)
        expected[day] = (round(ctl, 1), round(atl, 1), round(tsb, 1))
        day += timedelta(days=1)

    written = {
        date.fromisoformat(_text(row["snapshot_date"])): (
            _number(row["ctl"]),
            _number(row["atl"]),
            _number(row["tsb"]),
        )
        for row in client._tables["daily_load_snapshots"]._rows
        if row["snapshot_date"] != seed_date.isoformat()
    }
    assert written == expected


@pytest.mark.usefixtures("as_athlete")
async def test_stale_seed_rebuild_is_stable_across_runs(monkeypatch) -> None:
    today = date.today()
    since = today - timedelta(days=1)
    seed_date = since - timedelta(days=3)
    client = FakeSupabaseClient(
        activity_rows=[_activity_row(seed_date + timedelta(days=1), 75.0)],
        daily_load_snapshot_rows=[_snapshot_row(seed_date, ctl=25.0, atl=35.0)],
    )
    monkeypatch.setattr(api_index, "repo", SupabaseRepository(client=client))

    assert (await _post_recompute_load({"since": since.isoformat()})).status_code == 200
    first_rows = {row["snapshot_date"]: deepcopy(row) for row in _load_rows(client)}

    assert (await _post_recompute_load({"since": since.isoformat()})).status_code == 200
    second_rows = {row["snapshot_date"]: row for row in _load_rows(client)}

    assert second_rows.keys() == first_rows.keys()
    for snapshot_date, second in second_rows.items():
        first = first_rows[snapshot_date]
        for field in ("daily_tss", "ctl", "atl", "tsb"):
            # Snapshots are persisted rounded to 0.1, so the second run reseeds from a rounded
            # row rather than the first run's full-precision state. Values must stay put within
            # that rounding, but they are not bit-identical.
            drift = round(abs(_number(second[field]) - _number(first[field])), 6)
            assert drift <= 0.1, (snapshot_date, field, drift)
