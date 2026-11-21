"""Per-client in-flight limit of the API and the client address it counts."""

import asyncio
from pathlib import Path
from typing import Any, cast

import duckdb
import httpx2 as httpx
import pytest
from starlette.types import Receive, Scope, Send
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from gridload.api.app import Limits, create_app
from gridload.api.limits import RETRY_AFTER_SECONDS, InflightLimit, client_key
from gridload.config import Settings
from gridload.pipeline.db import migrate
from tests.test_api import add_run, publish
from tests.test_api_hardening import BUSY, DAY, HEAVY, ISSUED

EXEMPT = frozenset({"/api/v1/health", "/api/v1/status"})
# The web container's address on the internal network, as in compose.yaml.
PROXY = "10.46.60.2"


class Held:
    """An ASGI app that answers 200 with the client host once ``release`` is set."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.seen: list[str] = []

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        client = scope.get("client")
        host = client[0] if client else "-"
        self.seen.append(host)
        await self.release.wait()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": host.encode()})


def client_for(app: Any, host: str | None, port: int = 40000) -> httpx.AsyncClient:
    """A client with source address ``host``; ``None`` sends requests without a client."""
    if host is None:
        inner = app

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            await inner({**scope, "client": None}, receive, send)

    transport = httpx.ASGITransport(app=app, client=(host or "", port))
    return httpx.AsyncClient(transport=transport, base_url="http://api")


async def settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


# Client key ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("client", "key"),
    [
        (("192.0.2.7", 5000), "192.0.2.7"),
        (("::ffff:192.0.2.7", 5000), "192.0.2.7"),
        (("2001:db8:1:2:aaaa::1", 5000), "2001:db8:1:2::/64"),
        (("2001:db8:1:2:ffff:ffff:ffff:ffff", 5000), "2001:db8:1:2::/64"),
        (("2001:db8:1:3::1", 5000), "2001:db8:1:3::/64"),
        (("::1", 5000), "::/64"),
        (("unix-socket", 0), "unix-socket"),
        (("", 0), ""),
        (None, ""),
    ],
)
def test_client_key(client: tuple[str, int] | None, key: str) -> None:
    assert client_key({"type": "http", "client": client}) == key


def test_client_key_without_a_client_entry() -> None:
    assert client_key({"type": "http"}) == ""


# Middleware ------------------------------------------------------------------------------


def test_one_saturating_client_does_not_block_another() -> None:
    inner = Held()
    limit = InflightLimit(inner, limit=10, per_client=3, exempt=EXEMPT)

    async def scenario() -> None:
        async with (
            client_for(limit, "192.0.2.1") as flood,
            client_for(limit, "192.0.2.2") as other,
        ):
            held = [asyncio.create_task(flood.get("/api/v1/forecasts")) for _ in range(3)]
            await settle()
            assert limit.by_client == {"192.0.2.1": 3}
            refused = await flood.get("/api/v1/forecasts")
            assert refused.status_code == 503
            assert refused.json() == BUSY
            assert refused.headers["retry-after"] == str(RETRY_AFTER_SECONDS)
            # A second address is a separate client with its own limit.
            served = asyncio.create_task(other.get("/api/v1/actuals"))
            await settle()
            assert limit.by_client == {"192.0.2.1": 3, "192.0.2.2": 1}
            # Health and status stay reachable for the saturating client.
            exempt = asyncio.create_task(flood.get("/api/v1/health"))
            await settle()
            assert limit.inflight == 4
            inner.release.set()
            assert (await served).status_code == 200
            assert (await exempt).status_code == 200
            assert [r.status_code for r in await asyncio.gather(*held)] == [200] * 3
            assert limit.by_client == {}
            assert limit.inflight == 0
            # Once released, the client is served again.
            assert (await flood.get("/api/v1/forecasts")).status_code == 200

    asyncio.run(scenario())


def test_the_global_limit_still_applies_across_clients() -> None:
    inner = Held()
    limit = InflightLimit(inner, limit=4, per_client=3, exempt=EXEMPT)

    async def scenario() -> None:
        clients = [client_for(limit, f"192.0.2.{i}") for i in range(1, 6)]
        held = [asyncio.create_task(c.get("/api/v1/series")) for c in clients[:4]]
        await settle()
        refused = await clients[4].get("/api/v1/series")
        assert refused.status_code == 503
        assert refused.headers["retry-after"] == str(RETRY_AFTER_SECONDS)
        inner.release.set()
        assert [r.status_code for r in await asyncio.gather(*held)] == [200] * 4
        for c in clients:
            await c.aclose()
        assert limit.by_client == {}

    asyncio.run(scenario())


def test_ipv6_clients_of_one_network_share_a_limit() -> None:
    inner = Held()
    limit = InflightLimit(inner, limit=10, per_client=2, exempt=EXEMPT)

    async def scenario() -> None:
        async with (
            client_for(limit, "2001:db8:5:6::1") as a,
            client_for(limit, "2001:db8:5:6::2") as b,
            client_for(limit, "2001:db8:5:6:1:2:3:4") as c,
            client_for(limit, "2001:db8:5:7::1") as d,
        ):
            held = [asyncio.create_task(x.get("/api/v1/series")) for x in (a, b)]
            await settle()
            assert (await c.get("/api/v1/series")).status_code == 503
            other = asyncio.create_task(d.get("/api/v1/series"))
            await settle()
            inner.release.set()
            assert (await other).status_code == 200
            assert [r.status_code for r in await asyncio.gather(*held)] == [200, 200]
            assert limit.by_client == {}

    asyncio.run(scenario())


def test_requests_without_a_client_address_share_one_limit() -> None:
    inner = Held()
    limit = InflightLimit(inner, limit=10, per_client=2, exempt=EXEMPT)

    async def scenario() -> None:
        async with client_for(limit, None) as anon, client_for(limit, "192.0.2.9") as known:
            held = [asyncio.create_task(anon.get("/api/v1/series")) for _ in range(2)]
            await settle()
            assert limit.by_client == {"": 2}
            assert (await anon.get("/api/v1/series")).status_code == 503
            other = asyncio.create_task(known.get("/api/v1/series"))
            await settle()
            inner.release.set()
            assert (await other).status_code == 200
            assert [r.status_code for r in await asyncio.gather(*held)] == [200, 200]
            assert limit.by_client == {}

    asyncio.run(scenario())


async def call(app: Any, scope: Scope, receive: Receive | None = None) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    async def default_receive() -> dict[str, Any]:
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(scope, receive or default_receive, send)
    return sent


def scope_from(host: str) -> dict[str, Any]:
    return {
        "type": "http",
        "path": "/api/v1/series",
        "client": (host, 1),
        "headers": [],
        "query_string": b"",
        "method": "GET",
    }


def test_counts_are_released_when_the_app_fails() -> None:
    async def failing(scope: Scope, receive: Receive, send: Send) -> None:
        raise RuntimeError("query failed")

    limit = InflightLimit(failing, limit=10, per_client=1, exempt=EXEMPT)

    async def scenario() -> None:
        for _ in range(3):
            with pytest.raises(RuntimeError):
                await call(limit, scope_from("192.0.2.1"))
            assert limit.by_client == {}
            assert limit.inflight == 0

    asyncio.run(scenario())


def test_counts_are_released_when_the_client_disconnects() -> None:
    async def gone(scope: Scope, receive: Receive, send: Send) -> None:
        # The client went away: the app returns without a response.
        assert (await receive())["type"] == "http.disconnect"

    limit = InflightLimit(gone, limit=10, per_client=1, exempt=EXEMPT)

    async def scenario() -> None:
        for _ in range(3):
            assert await call(limit, scope_from("192.0.2.1")) == []
        assert limit.by_client == {}
        assert limit.inflight == 0

    asyncio.run(scenario())


def test_counts_are_released_when_the_request_is_cancelled() -> None:
    inner = Held()
    limit = InflightLimit(inner, limit=10, per_client=1, exempt=EXEMPT)

    async def scenario() -> None:
        task = asyncio.create_task(call(limit, scope_from("192.0.2.1")))
        await settle()
        assert limit.by_client == {"192.0.2.1": 1}
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert limit.by_client == {}
        assert limit.inflight == 0

    asyncio.run(scenario())


def test_refused_requests_are_not_counted() -> None:
    inner = Held()
    limit = InflightLimit(inner, limit=10, per_client=1, exempt=EXEMPT)

    async def scenario() -> None:
        held = asyncio.create_task(call(limit, scope_from("192.0.2.1")))
        await settle()
        for _ in range(5):
            sent = await call(limit, scope_from("192.0.2.1"))
            assert sent[0]["status"] == 503
        assert limit.by_client == {"192.0.2.1": 1}
        inner.release.set()
        await held
        assert limit.by_client == {}

    asyncio.run(scenario())


# Client address behind the proxy ----------------------------------------------------------


def behind_proxy(inner: Held, per_client: int = 1) -> tuple[Any, InflightLimit]:
    """The middleware chain of the deployed API: uvicorn's proxy headers, then the limit."""
    limit = InflightLimit(inner, limit=10, per_client=per_client, exempt=EXEMPT)
    return ProxyHeadersMiddleware(cast(Any, limit), trusted_hosts=PROXY), limit


def test_a_forwarded_for_header_from_an_untrusted_peer_is_ignored() -> None:
    inner = Held()
    app, limit = behind_proxy(inner)

    async def scenario() -> None:
        async with client_for(app, "10.46.60.5") as direct:
            held = asyncio.create_task(
                direct.get("/api/v1/series", headers={"X-Forwarded-For": "203.0.113.1"})
            )
            await settle()
            assert inner.seen == ["10.46.60.5"]
            # A different forged address does not open a new bucket.
            spoofed = await direct.get("/api/v1/series", headers={"X-Forwarded-For": "203.0.113.2"})
            assert spoofed.status_code == 503
            assert limit.by_client == {"10.46.60.5": 1}
            inner.release.set()
            assert (await held).text == "10.46.60.5"

    asyncio.run(scenario())


def test_the_trusted_proxy_forwards_the_right_most_untrusted_address() -> None:
    inner = Held()
    app, limit = behind_proxy(inner, per_client=2)

    async def scenario() -> None:
        async with client_for(app, PROXY) as proxy:
            inner.release.set()
            plain = await proxy.get("/api/v1/series", headers={"X-Forwarded-For": "198.51.100.4"})
            assert plain.text == "198.51.100.4"
            # A client-supplied value in front of the proxy's own entry is not used.
            chained = await proxy.get(
                "/api/v1/series", headers={"X-Forwarded-For": "203.0.113.1, 198.51.100.4"}
            )
            assert chained.text == "198.51.100.4"
            v6 = await proxy.get("/api/v1/series", headers={"X-Forwarded-For": "2001:db8::7"})
            assert v6.text == "2001:db8::7"
            # Without the header the proxy itself is the client.
            assert (await proxy.get("/api/v1/series")).text == PROXY
            assert limit.by_client == {}

    asyncio.run(scenario())


def test_clients_behind_the_trusted_proxy_are_limited_separately() -> None:
    inner = Held()
    app, limit = behind_proxy(inner)

    async def scenario() -> None:
        async with client_for(app, PROXY) as proxy:

            async def get(address: str) -> httpx.Response:
                return await proxy.get("/api/v1/series", headers={"X-Forwarded-For": address})

            held = asyncio.create_task(get("198.51.100.4"))
            await settle()
            assert (await get("198.51.100.4")).status_code == 503
            other = asyncio.create_task(get("198.51.100.5"))
            await settle()
            assert limit.by_client == {"198.51.100.4": 1, "198.51.100.5": 1}
            inner.release.set()
            assert (await held).status_code == 200
            assert (await other).status_code == 200

    asyncio.run(scenario())


# Application -----------------------------------------------------------------------------


def test_app_serves_a_second_client_while_the_first_is_at_its_limit(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    settings = Settings(data_dir=data_dir)
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    migrate(con)
    add_run(con, "r1", "lightgbm", DAY, ISSUED, 1.0)
    publish(con, settings)
    con.close()
    app = create_app(settings, limits=Limits(heavy_wait=2.0, per_client_inflight=2))
    gate = app.state.heavy_gate

    async def scenario() -> None:
        await gate.acquire()
        await gate.acquire()
        async with (
            client_for(app, "192.0.2.1") as flood,
            client_for(app, "192.0.2.2") as other,
        ):
            waiting = [
                asyncio.create_task(flood.get("/api/v1/actuals", params=HEAVY)) for _ in range(2)
            ]
            await asyncio.sleep(0.2)
            refused = await flood.get("/api/v1/forecasts")
            assert refused.status_code == 503
            assert refused.json() == BUSY
            assert refused.headers["retry-after"] == str(RETRY_AFTER_SECONDS)
            assert (await flood.get("/api/v1/status")).status_code == 200
            assert (await other.get("/api/v1/forecasts")).status_code == 200
            gate.release()
            gate.release()
            assert [r.status_code for r in await asyncio.gather(*waiting)] == [200, 200]
            assert (await flood.get("/api/v1/forecasts")).status_code == 200

    asyncio.run(scenario())


def test_busy_responses_of_the_gates_carry_retry_after(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    settings = Settings(data_dir=data_dir)
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    migrate(con)
    publish(con, settings)
    con.close()
    app = create_app(settings, limits=Limits(heavy_wait=0.05))
    gate = app.state.heavy_gate

    async def scenario() -> httpx.Response:
        await gate.acquire()
        await gate.acquire()
        async with client_for(app, "192.0.2.1") as client:
            response = await client.get("/api/v1/actuals", params=HEAVY)
        gate.release()
        gate.release()
        return response

    response = asyncio.run(scenario())
    assert response.status_code == 503
    assert response.json() == BUSY
    assert response.headers["retry-after"] == str(RETRY_AFTER_SECONDS)
