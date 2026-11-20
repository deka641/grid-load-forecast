"""The in-process cache of large response bodies."""

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import duckdb
import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient

from gridload.api import queries
from gridload.api.app import create_app
from gridload.api.cache import ResponseCache
from gridload.api.limits import CACHE_BYTES, CACHE_MIN_BYTES, HEAVY_CONCURRENCY, Limits
from gridload.config import Settings
from gridload.pipeline import snapshot
from gridload.pipeline.db import migrate
from gridload.series import CATALOG

ALL_KEYS = ",".join(s.key for s in CATALOG)
# 15 series x 139 days x 24 hours = 50,040 points, about 2.5 MB: heavy and cached.
HEAVY = {"series": ALL_KEYS, "start": "2025-01-01", "end": "2025-05-19"}
OTHER_HEAVY = {"series": ALL_KEYS, "start": "2025-01-02", "end": "2025-05-20"}
BUSY = {"detail": "the server is busy; try again shortly"}
SNAP = (1, 1)


# The cache itself ------------------------------------------------------------------------


def test_documented_sizes() -> None:
    assert CACHE_BYTES == 32 * 2**20
    assert CACHE_MIN_BYTES == 2**20


def test_small_bodies_are_not_stored_and_large_ones_are() -> None:
    cache = ResponseCache(capacity=100, min_size=10)
    cache.select(SNAP)
    cache.put(SNAP, "small", b"x" * 9)
    cache.put(SNAP, "large", b"x" * 10)
    assert cache.get(SNAP, "small") is None
    assert cache.get(SNAP, "large") == b"x" * 10
    assert (len(cache), cache.size) == (1, 10)


def test_a_body_larger_than_the_capacity_is_not_stored() -> None:
    cache = ResponseCache(capacity=100, min_size=10)
    cache.select(SNAP)
    cache.put(SNAP, "a", b"a" * 60)
    cache.put(SNAP, "huge", b"h" * 101)
    assert cache.get(SNAP, "huge") is None
    # Nothing was evicted for it.
    assert cache.get(SNAP, "a") == b"a" * 60


def test_least_recently_used_bodies_are_evicted_first() -> None:
    cache = ResponseCache(capacity=100, min_size=10)
    cache.select(SNAP)
    cache.put(SNAP, "a", b"a" * 40)
    cache.put(SNAP, "b", b"b" * 40)
    assert cache.get(SNAP, "a") is not None  # a is now more recent than b
    cache.put(SNAP, "c", b"c" * 40)
    assert cache.get(SNAP, "b") is None
    assert cache.get(SNAP, "a") == b"a" * 40
    assert cache.get(SNAP, "c") == b"c" * 40
    assert cache.size == 80
    cache.put(SNAP, "d", b"d" * 100)
    assert (len(cache), cache.size) == (1, 100)


def test_storing_a_tag_twice_counts_it_once() -> None:
    cache = ResponseCache(capacity=100, min_size=10)
    cache.select(SNAP)
    cache.put(SNAP, "a", b"a" * 40)
    cache.put(SNAP, "a", b"a" * 40)
    assert (len(cache), cache.size) == (1, 40)


def test_a_new_snapshot_empties_the_cache_and_old_requests_do_not_refill_it() -> None:
    cache = ResponseCache(capacity=100, min_size=10)
    cache.select(SNAP)
    cache.put(SNAP, "a", b"a" * 40)
    cache.select(SNAP)  # the same snapshot keeps the entries
    assert cache.get(SNAP, "a") is not None
    new = (1, 2)
    cache.select(new)
    assert (len(cache), cache.size) == (0, 0)
    # A request still running on the old snapshot neither reads nor stores.
    cache.put(SNAP, "b", b"b" * 40)
    assert cache.get(SNAP, "b") is None
    assert len(cache) == 0
    cache.put(new, "c", b"c" * 40)
    assert cache.get(new, "c") == b"c" * 40


def test_nothing_is_stored_before_a_snapshot_is_selected() -> None:
    cache = ResponseCache(capacity=100, min_size=10)
    cache.put(SNAP, "a", b"a" * 40)
    assert len(cache) == 0


# The cache in the API ----------------------------------------------------------------------


def fill(con: duckdb.DuckDBPyConnection, value: str = "1000 + (epoch(g.ts) % 7919) / 7.0") -> None:
    """Quarter hours for every series from 2025-01-01 to 2025-06-01 local."""
    con.execute(
        "INSERT INTO observations SELECT s.series_key, 'quarterhour', g.ts, "  # noqa: S608
        f"{value}, now(), 0 FROM series s, generate_series("
        "TIMESTAMPTZ '2024-12-31 23:00:00+00', TIMESTAMPTZ '2025-05-31 21:45:00+00', "
        "INTERVAL 15 MINUTE) g(ts)"
    )


@pytest.fixture
def warehouse() -> Iterator[duckdb.DuckDBPyConnection]:
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    migrate(con)
    fill(con)
    yield con
    con.close()


@pytest.fixture
def settings(tmp_path: Path, warehouse: duckdb.DuckDBPyConnection) -> Settings:
    settings = Settings(data_dir=tmp_path)
    snapshot.publish(warehouse, settings.serving_path)
    return settings


def _counting(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Count the actuals bodies built from the snapshot."""
    calls: list[int] = []
    original = queries.actuals_json

    def counted(*args: Any, **kwargs: Any) -> bytes:
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(queries, "actuals_json", counted)
    return calls


def _hold(client: TestClient, name: str, count: int, *, release: bool = False) -> None:
    gate = getattr(client.app.state, name)  # type: ignore[attr-defined]
    for _ in range(count):
        client.portal.call(gate.release if release else gate.acquire)  # type: ignore[union-attr]


def test_a_repeated_heavy_request_needs_no_gate_and_no_query(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _counting(monkeypatch)
    limits = Limits(heavy_wait=0.01, query_wait=0.01)
    with TestClient(create_app(settings, limits=limits)) as client:
        first = client.get("/api/v1/actuals", params=HEAVY)
        assert first.status_code == 200
        assert len(first.content) > CACHE_MIN_BYTES
        _hold(client, "heavy_gate", HEAVY_CONCURRENCY)
        _hold(client, "query_gate", 8)
        try:
            again = client.get("/api/v1/actuals", params=HEAVY)
            # Another heavy request still needs the slots that are all taken.
            other = client.get("/api/v1/actuals", params=OTHER_HEAVY)
        finally:
            _hold(client, "heavy_gate", HEAVY_CONCURRENCY, release=True)
            _hold(client, "query_gate", 8, release=True)
        assert again.status_code == 200
        assert again.content == first.content
        assert again.headers["etag"] == first.headers["etag"]
        assert other.status_code == 503
        assert other.json() == BUSY
        assert client.app.state.budget.used == 0  # type: ignore[attr-defined]
    assert len(calls) == 1


def test_small_responses_are_not_cached(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _counting(monkeypatch)
    small = {"series": "load", "start": "2025-01-01", "end": "2025-01-07"}
    with TestClient(create_app(settings)) as client:
        for _ in range(2):
            assert client.get("/api/v1/actuals", params=small).status_code == 200
            assert client.get("/api/v1/forecasts/days").status_code == 200
    assert len(calls) == 2


def test_a_cached_body_still_needs_room_in_the_byte_budget(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        body = client.get("/api/v1/actuals", params=HEAVY).content
        budget = client.app.state.budget  # type: ignore[attr-defined]
        budget.reserve(budget.limit - len(body) + 1)
        try:
            refused = client.get("/api/v1/actuals", params=HEAVY)
        finally:
            budget.release(budget.limit - len(body) + 1)
        assert refused.status_code == 503
        assert refused.json() == BUSY
        assert client.get("/api/v1/actuals", params=HEAVY).content == body
        assert budget.used == 0


def test_a_new_snapshot_is_served_fresh_and_empties_the_cache(
    warehouse: duckdb.DuckDBPyConnection,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _counting(monkeypatch)
    with TestClient(create_app(settings)) as client:
        old = client.get("/api/v1/actuals", params=HEAVY)
        cache = client.app.state.cache  # type: ignore[attr-defined]
        assert len(cache) == 1
        warehouse.execute("DELETE FROM observations")
        fill(warehouse, "2000.0")
        snapshot.publish(warehouse, settings.serving_path)
        new = client.get("/api/v1/actuals", params=HEAVY)
        assert new.headers["etag"] != old.headers["etag"]
        assert new.json()["series"][0]["points"][0]["value"] == 8000.0
        assert len(cache) == 1
        assert cache.size == len(new.content)
    assert len(calls) == 2


def test_identical_requests_waiting_for_a_slot_use_the_first_result(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _counting(monkeypatch)
    app = create_app(settings)
    clients = 6

    async def scenario() -> list[httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
            return await asyncio.gather(
                *(client.get("/api/v1/actuals", params=HEAVY) for _ in range(clients))
            )

    responses = asyncio.run(scenario())
    assert [r.status_code for r in responses] == [200] * clients
    assert len({r.content for r in responses}) == 1
    # Only the requests that got a heavy slot at once built the body.
    assert len(calls) <= HEAVY_CONCURRENCY
