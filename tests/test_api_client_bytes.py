"""The share of the byte budget that one client address may hold."""

import asyncio
import threading
from pathlib import Path
from typing import Any

import duckdb
import httpx2 as httpx
import pytest
from fastapi import FastAPI

from gridload.api import app as app_module
from gridload.api import queries
from gridload.api.app import create_app
from gridload.api.limits import (
    BYTES_PER_POINT,
    PER_CLIENT_INFLIGHT,
    QUERY_CONCURRENCY,
    ByteBudget,
    Limits,
    ServerBusyError,
)
from gridload.config import Settings
from gridload.pipeline.db import migrate
from tests.test_api import publish
from tests.test_api_hardening import ALL_KEYS, BUSY, RawClient, fill_year

GREEDY = "10.0.0.1"
OTHER = "10.0.0.2"
ACTUALS = "/api/v1/actuals"
# One year of all series hourly, the largest body the API serves.
YEAR = f"series={ALL_KEYS}&start=2025-01-01&end=2026-01-01"
MONTH = f"series={ALL_KEYS}&start=2025-03-01&end=2025-03-31"
# The dashboard's largest request: seven days of all series hourly.
WEEK = f"series={ALL_KEYS}&start=2025-12-26&end=2026-01-01"
WEEK_ESTIMATE = 15 * 7 * 24 * BYTES_PER_POINT


@pytest.fixture(scope="module")
def settings(tmp_path_factory: pytest.TempPathFactory) -> Settings:
    data_dir = Path(tmp_path_factory.mktemp("year")) / "data"
    data_dir.mkdir()
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    migrate(con)
    fill_year(con)
    settings = Settings(data_dir=data_dir)
    publish(con, settings)
    con.close()
    return settings


def http_client(app: FastAPI, host: str) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, client=(host, 1))
    return httpx.AsyncClient(transport=transport, base_url="http://api")


async def fetch(app: FastAPI, query: str, host: str) -> bytes:
    """The body of one fully read request; it also fills the cache."""
    client = RawClient(asyncio.Event())
    await client.call(app, ACTUALS, query, host)
    assert client.status == 200
    return client.body


class Holds:
    """Requests whose clients take the status line and then read nothing."""

    def __init__(self, app: FastAPI) -> None:
        self.app = app
        self.readable = asyncio.Event()
        self.clients: list[RawClient] = []
        self.tasks: list[asyncio.Task[None]] = []

    async def open(self, query: str, host: str) -> RawClient:
        client = RawClient(asyncio.Event(), self.readable)
        self.clients.append(client)
        self.tasks.append(asyncio.create_task(client.call(self.app, ACTUALS, query, host)))
        async with asyncio.timeout(10):
            while not client.messages:
                await asyncio.sleep(0.005)
        return client

    async def finish(self) -> None:
        self.readable.set()
        await asyncio.gather(*self.tasks)


def assert_released(app: FastAPI) -> None:
    budget = app.state.budget
    assert budget.used == 0
    assert budget.by_client == {}


# The budget itself -----------------------------------------------------------------------


def test_budget_counts_each_client_and_forgets_it_at_zero() -> None:
    budget = ByteBudget(limit=100, per_client=40)
    first = budget.reserve(30, "a")
    budget.reserve(10, "a")
    with pytest.raises(ServerBusyError):
        budget.reserve(1, "a")
    other = budget.reserve(40, "b")
    assert budget.by_client == {"a": 40, "b": 40}
    assert budget.used == 80
    first.release()
    first.release()  # a second release returns nothing
    assert budget.by_client == {"a": 10, "b": 40}
    other.release()
    assert budget.by_client == {"a": 10}
    budget.release(10, "a")
    assert budget.by_client == {}
    assert budget.used == 0


def test_budget_lets_a_client_holding_nothing_take_one_larger_body() -> None:
    budget = ByteBudget(limit=100, per_client=40)
    large = budget.reserve(60, "a")
    with pytest.raises(ServerBusyError):
        budget.reserve(1, "a")
    with pytest.raises(ServerBusyError):
        budget.reserve(41, "b")  # the total still applies
    budget.reserve(40, "b")
    large.release()
    assert budget.by_client == {"b": 40}


def test_bytes_without_a_client_count_only_in_total() -> None:
    budget = ByteBudget(limit=100, per_client=40)
    budget.reserve(90)
    assert budget.by_client == {}
    with pytest.raises(ServerBusyError):
        budget.reserve(11, "a")
    budget.reserve(10, "a")
    budget.release(90)
    assert budget.used == 10


# One client holding bodies it does not read ----------------------------------------------


def test_a_client_holding_unread_bodies_leaves_room_for_others(settings: Settings) -> None:
    app = create_app(settings, limits=Limits())
    budget = app.state.budget

    async def scenario() -> tuple[int, list[int], list[httpx.Response]]:
        size = len(await fetch(app, YEAR, GREEDY))
        # Sixteen such bodies, the most one client may have in flight, exceed the
        # budget: without a share per client, fifteen leave no room for another.
        assert PER_CLIENT_INFLIGHT * size > budget.limit
        holds = Holds(app)
        for _ in range(PER_CLIENT_INFLIGHT):
            await holds.open(YEAR, GREEDY)
        statuses = [c.status for c in holds.clients]
        async with http_client(app, OTHER) as http:
            others = [
                await http.get(f"{ACTUALS}?{YEAR}"),
                await http.get(f"{ACTUALS}?{WEEK}"),
                await http.get("/api/v1/forecasts/days"),
            ]
        await holds.finish()
        return size, statuses, others

    size, statuses, others = asyncio.run(scenario())
    assert [r.status_code for r in others] == [200, 200, 200]
    held = Limits().per_client_bytes // size
    assert held == 7
    assert statuses == [200] * held + [503] * (PER_CLIENT_INFLIGHT - held)
    assert_released(app)


def test_a_refused_body_keeps_the_busy_response(settings: Settings) -> None:
    app = create_app(settings, limits=Limits(per_client_bytes=1))

    async def scenario() -> RawClient:
        await fetch(app, MONTH, GREEDY)
        holds = Holds(app)
        await holds.open(MONTH, GREEDY)
        refused = await holds.open(MONTH, GREEDY)
        await holds.finish()
        return refused

    refused = asyncio.run(scenario())
    assert refused.status == 503
    headers = dict(refused.messages[0]["headers"])
    assert headers[b"retry-after"] == b"1"
    assert headers[b"content-type"] == b"application/json"
    assert refused.body == b'{"detail":"the server is busy; try again shortly"}'
    assert_released(app)


def test_the_estimate_of_a_query_counts_for_its_client(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    proceed = threading.Event()
    calls: list[int] = []
    original = queries.actuals_json

    def blocking_first(*args: Any) -> bytes:
        calls.append(1)
        if len(calls) == 1:
            assert proceed.wait(10)
        return original(*args)

    monkeypatch.setattr(queries, "actuals_json", blocking_first)
    # Room for one estimate of a week, not for two.
    app = create_app(settings, limits=Limits(per_client_bytes=WEEK_ESTIMATE * 3 // 2))
    budget = app.state.budget
    other_week = f"series={ALL_KEYS}&start=2025-12-19&end=2025-12-25"

    async def scenario() -> tuple[httpx.Response, httpx.Response, httpx.Response, list[int]]:
        async with http_client(app, GREEDY) as greedy, http_client(app, OTHER) as other:
            running = asyncio.create_task(greedy.get(f"{ACTUALS}?{WEEK}"))
            async with asyncio.timeout(10):
                while not calls:
                    await asyncio.sleep(0.005)
            held = dict(budget.by_client)
            refused = await greedy.get(f"{ACTUALS}?{other_week}")
            served = await other.get(f"{ACTUALS}?{other_week}")
            proceed.set()
            return await running, refused, served, [held[GREEDY], len(calls)]

    running, refused, served, (held, queried) = asyncio.run(scenario())
    assert held == WEEK_ESTIMATE
    assert running.status_code == 200
    assert refused.status_code == 503
    assert refused.json() == BUSY
    assert served.status_code == 200
    # The refused request ran no query: the first and the other client's did.
    assert queried == 2
    assert_released(app)


def test_a_body_larger_than_its_estimate_must_fit_the_client_share(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app(settings, limits=Limits())

    async def sizes() -> tuple[int, int]:
        return len(await fetch(app, MONTH, GREEDY)), len(await fetch(app, WEEK, GREEDY))

    month, week = asyncio.run(sizes())
    monkeypatch.setattr(app_module, "BYTES_PER_POINT", 1)
    # The month is held; the week's estimate (2,520 bytes) fits next to it, its
    # body does not.
    app = create_app(settings, limits=Limits(per_client_bytes=month + week - 1))

    async def scenario() -> tuple[RawClient, httpx.Response, dict[str, int]]:
        holds = Holds(app)
        await holds.open(MONTH, GREEDY)
        async with http_client(app, GREEDY) as http:
            refused = await http.get(f"{ACTUALS}?{WEEK}")
        held = dict(app.state.budget.by_client)
        await holds.finish()
        return holds.clients[0], refused, held

    hold, refused, held = asyncio.run(scenario())
    assert hold.status == 200
    assert refused.status_code == 503
    assert refused.json() == BUSY
    assert held == {GREEDY: month}
    assert_released(app)


def test_a_body_larger_than_the_share_is_sent_to_a_client_holding_nothing(
    settings: Settings,
) -> None:
    app = create_app(settings, limits=Limits(per_client_bytes=1000))

    async def scenario() -> tuple[RawClient, RawClient, httpx.Response]:
        holds = Holds(app)
        first = await holds.open(MONTH, GREEDY)  # not cached yet: runs the query
        second = await holds.open(MONTH, GREEDY)
        async with http_client(app, OTHER) as http:
            other = await http.get(f"{ACTUALS}?{MONTH}")
        await holds.finish()
        return first, second, other

    first, second, other = asyncio.run(scenario())
    assert first.status == 200
    assert len(first.body) > 1000
    assert second.status == 503
    assert other.status_code == 200
    assert_released(app)


def test_several_clients_each_hold_their_own_share(settings: Settings) -> None:
    probe = create_app(settings)
    size = len(asyncio.run(fetch(probe, MONTH, GREEDY)))
    app = create_app(settings, limits=Limits(per_client_bytes=2 * size + size // 2))
    hosts = ["10.0.1.1", "10.0.1.2", "10.0.1.3", "2001:db8::1"]

    async def scenario() -> tuple[dict[str, list[int]], dict[str, int], int]:
        await fetch(app, MONTH, OTHER)
        holds = Holds(app)
        opened = await asyncio.gather(*(holds.open(MONTH, h) for h in hosts for _ in range(3)))
        statuses = {h: [c.status for c in opened[i * 3 : i * 3 + 3]] for i, h in enumerate(hosts)}
        held = dict(app.state.budget.by_client)
        used = app.state.budget.used
        await holds.finish()
        return statuses, held, used

    statuses, held, used = asyncio.run(scenario())
    assert {h: sorted(s) for h, s in statuses.items()} == {h: [200, 200, 503] for h in hosts}
    assert held == {
        "10.0.1.1": 2 * size,
        "10.0.1.2": 2 * size,
        "10.0.1.3": 2 * size,
        "2001:db8::/64": 2 * size,
    }
    assert used == 8 * size
    assert_released(app)


# Every way a request ends returns its bytes ----------------------------------------------


def test_a_client_gone_before_its_query_returns_its_bytes(settings: Settings) -> None:
    app = create_app(settings)
    gone = asyncio.Event()
    gone.set()
    client = RawClient(gone)
    asyncio.run(client.call(app, ACTUALS, WEEK, GREEDY))
    assert client.status == 499
    assert_released(app)


def test_a_dropped_slow_reader_returns_its_bytes(settings: Settings) -> None:
    app = create_app(settings, limits=Limits(send_timeout=0.05))
    client = RawClient(asyncio.Event(), asyncio.Event())  # never readable
    asyncio.run(client.call(app, ACTUALS, MONTH, GREEDY))
    assert client.status == 200
    assert client.body == b""
    assert_released(app)


def test_a_request_cancelled_while_sending_returns_its_bytes(settings: Settings) -> None:
    app = create_app(settings)

    async def scenario() -> int:
        holds = Holds(app)
        await holds.open(MONTH, GREEDY)
        held = app.state.budget.by_client[GREEDY]
        holds.tasks[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await holds.tasks[0]
        return int(held)

    assert asyncio.run(scenario()) > 0
    assert_released(app)


def test_a_failing_query_returns_its_bytes(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_args: Any) -> bytes:
        raise duckdb.IOException("disk gone")

    monkeypatch.setattr(queries, "actuals_json", broken)
    app = create_app(settings)
    client = RawClient(asyncio.Event())
    asyncio.run(client.call(app, ACTUALS, WEEK, GREEDY))
    assert client.status == 503
    assert_released(app)


def test_a_request_refused_by_a_gate_returns_its_bytes(settings: Settings) -> None:
    app = create_app(settings, limits=Limits(query_wait=0.05))
    gate = app.state.query_gate

    async def scenario() -> httpx.Response:
        for _ in range(QUERY_CONCURRENCY):
            await gate.acquire()
        try:
            async with http_client(app, GREEDY) as http:
                return await http.get(f"{ACTUALS}?{WEEK}")
        finally:
            for _ in range(QUERY_CONCURRENCY):
                gate.release()

    response = asyncio.run(scenario())
    assert response.status_code == 503
    assert response.json() == BUSY
    assert_released(app)
