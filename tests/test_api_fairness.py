"""Per-client share of the query slots: one client cannot fill all of them."""

import asyncio
import threading
import time
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import httpx2 as httpx
import pytest

from gridload.api import queries
from gridload.api.app import create_app
from gridload.api.limits import (
    HEAVY_CONCURRENCY,
    PER_CLIENT_QUERIES,
    QUERY_CONCURRENCY,
    RETRY_AFTER_SECONDS,
    ClientGate,
    Limits,
    ServerBusyError,
)
from gridload.config import Settings
from gridload.pipeline.db import migrate
from tests.test_api import add_run, publish
from tests.test_api_hardening import BUSY, DAY, HEAVY, ISSUED

# Requests for at least this many days block in their query until the test lets them go.
BLOCKING_LIMIT = 100
GREEDY = "192.0.2.1"
OTHER = "198.51.100.7"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    migrate(con)
    add_run(con, "r1", "lightgbm", DAY, ISSUED, 1.0)
    settings = Settings(data_dir=data_dir)
    publish(con, settings)
    con.close()
    return settings


@pytest.fixture
def blocking(monkeypatch: pytest.MonkeyPatch) -> Iterator[threading.Event]:
    """Hold every /forecasts/days query with a large limit until the event is set."""
    release = threading.Event()
    original = queries.forecast_days

    def held(cur: duckdb.DuckDBPyConnection, limit: int) -> list[date]:
        if limit >= BLOCKING_LIMIT:
            release.wait(10)
        return original(cur, limit)

    monkeypatch.setattr(queries, "forecast_days", held)
    yield release
    release.set()


def client_for(app: Any, host: str) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, client=(host, 40000))
    return httpx.AsyncClient(transport=transport, base_url="http://api")


async def settle(app: Any, used: int) -> None:
    """Wait until ``used`` query slots are taken."""
    gate = app.state.query_gate
    for _ in range(200):
        if gate._semaphore()._value == QUERY_CONCURRENCY - used:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"expected {used} used query slots")


def test_the_default_share_is_below_the_slot_count() -> None:
    assert 1 < PER_CLIENT_QUERIES < QUERY_CONCURRENCY
    assert Limits().per_client_queries == PER_CLIENT_QUERIES


def test_a_greedy_client_holds_only_its_share_and_others_are_served(
    settings: Settings, blocking: threading.Event
) -> None:
    app = create_app(settings, limits=Limits(query_wait=5.0))

    async def scenario() -> None:
        async with client_for(app, GREEDY) as greedy, client_for(app, OTHER) as other:
            # Ten slow requests from one address, within its in-flight limit of 16.
            flood = [
                asyncio.create_task(
                    greedy.get("/api/v1/forecasts/days", params={"limit": BLOCKING_LIMIT + i})
                )
                for i in range(10)
            ]
            await settle(app, PER_CLIENT_QUERIES)
            await asyncio.sleep(0.05)
            # Only its share runs; the rest waits outside the shared queue.
            assert app.state.client_queries.users(GREEDY) == 10
            assert app.state.query_gate.waiting == 0
            # Another client still gets a free slot at once.
            response = await asyncio.wait_for(
                other.get("/api/v1/forecasts/days", params={"limit": 1}), 2.0
            )
            assert response.status_code == 200
            assert response.json()["days"] == [DAY.isoformat()]
            assert app.state.client_queries.users(OTHER) == 0
            # The greedy client's own small request waits behind its share.
            own = asyncio.create_task(greedy.get("/api/v1/forecasts/days", params={"limit": 1}))
            await asyncio.sleep(0.1)
            assert not own.done()
            blocking.set()
            responses = await asyncio.gather(*flood, own)
            assert [r.status_code for r in responses] == [200] * 11
            assert app.state.client_queries.users(GREEDY) == 0

    asyncio.run(scenario())


def test_requests_beyond_the_share_get_a_busy_503_after_the_wait(
    settings: Settings, blocking: threading.Event
) -> None:
    app = create_app(settings, limits=Limits(query_wait=0.2, per_client_queries=2))

    async def scenario() -> None:
        async with client_for(app, GREEDY) as greedy:
            held = [
                asyncio.create_task(
                    greedy.get("/api/v1/forecasts/days", params={"limit": BLOCKING_LIMIT + i})
                )
                for i in range(2)
            ]
            await settle(app, 2)
            response = await greedy.get("/api/v1/forecasts")
            assert response.status_code == 503
            assert response.json() == BUSY
            assert response.headers["retry-after"] == str(RETRY_AFTER_SECONDS)
            # The refused request left no trace in the client's count.
            assert app.state.client_queries.users(GREEDY) == 2
            blocking.set()
            assert [r.status_code for r in await asyncio.gather(*held)] == [200, 200]
            assert app.state.client_queries.users(GREEDY) == 0

    asyncio.run(scenario())


def test_a_client_waiting_for_its_own_slots_holds_no_large_request_slot(
    settings: Settings, blocking: threading.Event
) -> None:
    app = create_app(settings, limits=Limits(heavy_wait=0.3, query_wait=5.0))

    async def scenario() -> None:
        async with client_for(app, GREEDY) as greedy, client_for(app, OTHER) as other:
            # The greedy client's query slots are busy with slow medium requests.
            medium = [
                asyncio.create_task(
                    greedy.get("/api/v1/forecasts/days", params={"limit": BLOCKING_LIMIT + i})
                )
                for i in range(PER_CLIENT_QUERIES)
            ]
            await settle(app, PER_CLIENT_QUERIES)
            # Its large requests wait for its own slots, not in the large-request slots.
            large = [
                asyncio.create_task(greedy.get("/api/v1/actuals", params=HEAVY))
                for _ in range(HEAVY_CONCURRENCY)
            ]
            await asyncio.sleep(0.1)
            assert app.state.client_queries.users(GREEDY) == PER_CLIENT_QUERIES + HEAVY_CONCURRENCY
            # Another client's large request still gets a large-request slot.
            response = await asyncio.wait_for(other.get("/api/v1/actuals", params=HEAVY), 2.0)
            assert response.status_code == 200, response.text
            blocking.set()
            responses = await asyncio.gather(*medium, *large)
            assert [r.status_code for r in responses] == [200] * (
                PER_CLIENT_QUERIES + HEAVY_CONCURRENCY
            )
            assert app.state.client_queries.users(GREEDY) == 0

    asyncio.run(scenario())


def test_all_waits_together_stay_within_the_query_wait(
    settings: Settings, blocking: threading.Event
) -> None:
    app = create_app(settings, limits=Limits(heavy_wait=5.0, query_wait=0.3))

    async def scenario() -> None:
        async with client_for(app, GREEDY) as greedy:
            held = [
                asyncio.create_task(
                    greedy.get("/api/v1/forecasts/days", params={"limit": BLOCKING_LIMIT + i})
                )
                for i in range(PER_CLIENT_QUERIES)
            ]
            await settle(app, PER_CLIENT_QUERIES)
            # All large-request slots are taken, so this request waits for its own
            # slot and then for a large-request slot: both within the query wait.
            for _ in range(HEAVY_CONCURRENCY):
                await app.state.heavy_gate.acquire()
            started = time.monotonic()
            response = await greedy.get("/api/v1/actuals", params=HEAVY)
            elapsed = time.monotonic() - started
            for _ in range(HEAVY_CONCURRENCY):
                app.state.heavy_gate.release()
            assert response.status_code == 503
            assert response.json() == BUSY
            assert elapsed < 1.0
            blocking.set()
            await asyncio.gather(*held)

    asyncio.run(scenario())


# The gate itself -------------------------------------------------------------------------


def test_client_gate_counts_per_key_and_forgets_idle_keys() -> None:
    gate = ClientGate(per_client=2)

    async def scenario() -> None:
        await gate.acquire("a", 1.0)
        await gate.acquire("a", 1.0)
        await gate.acquire("b", 1.0)
        assert (gate.users("a"), gate.users("b")) == (2, 1)
        with pytest.raises(ServerBusyError):
            await gate.acquire("a", 0.01)
        assert gate.users("a") == 2
        gate.release("a")
        gate.release("a")
        gate.release("b")
        assert gate._slots() == {}

    asyncio.run(scenario())


def test_client_gate_waiter_gets_the_next_slot_and_a_cancelled_one_leaves() -> None:
    gate = ClientGate(per_client=1)

    async def scenario() -> None:
        await gate.acquire("a", 1.0)
        waiter = asyncio.create_task(gate.acquire("a", 5.0))
        cancelled = asyncio.create_task(gate.acquire("a", 5.0))
        await asyncio.sleep(0.01)
        assert gate.users("a") == 3
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        assert gate.users("a") == 2
        gate.release("a")
        await asyncio.wait_for(waiter, 1.0)
        assert gate.users("a") == 1
        gate.release("a")
        assert gate._slots() == {}

    asyncio.run(scenario())


def test_client_gate_as_context_manager_releases_on_error() -> None:
    gate = ClientGate(per_client=1)

    async def failing() -> None:
        async with gate("a", 1.0):
            assert gate.users("a") == 1
            raise RuntimeError

    async def scenario() -> None:
        with pytest.raises(RuntimeError):
            await failing()
        assert gate.users("a") == 0

    asyncio.run(scenario())
