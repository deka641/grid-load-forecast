"""Load limits of the API: concurrency gates, the in-flight limit and the byte budget."""

import asyncio
import ipaddress
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from weakref import WeakKeyDictionary

from fastapi import Response
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

# Records keep the logger name of the application module.
log = logging.getLogger("gridload.api.app")

BUSY = "the server is busy; try again shortly"
# Actuals requests of more than this many points (series x days x points per day)
# are heavy: at most HEAVY_CONCURRENCY of them run at a time, and a request that
# finds no free slot within HEAVY_WAIT_SECONDS is answered with a JSON 503. At
# most HEAVY_MAX_WAITING requests wait; any further one is answered at once. One
# year of all series hourly is about 130,000 points and takes about 0.45 s alone.
# Waiting holds no thread. With 5 s, scripts/loadtest.py (10 clients, 60 s)
# measured no busy responses; waits of 0.5 s and 2 s exceeded its 5 % limit.
HEAVY_POINTS = 50_000
HEAVY_CONCURRENCY = 2
HEAVY_WAIT_SECONDS = 5.0
HEAVY_MAX_WAITING = 8
# Every data query runs in a worker thread; at most QUERY_CONCURRENCY at a time,
# which bounds the memory of the responses being built. Waiting requests hold no
# thread, so /health and /status, which run on the event loop, stay responsive.
QUERY_CONCURRENCY = 8
QUERY_WAIT_SECONDS = 10.0
# Requests in flight, apart from /health and /status, counted until the last byte
# of the response is handed to the server. This is the effective bound on queued
# data requests: QUERY_MAX_WAITING plus QUERY_CONCURRENCY equals it.
MAX_INFLIGHT = 64
QUERY_MAX_WAITING = MAX_INFLIGHT - QUERY_CONCURRENCY
# Of these, one client address may have at most PER_CLIENT_INFLIGHT in flight, so
# a single client fills at most a quarter of MAX_INFLIGHT. Loading the dashboard
# sends five data requests, and scripts/loadtest.py runs ten clients from one
# address; both stay below the limit. IPv6 clients count per /64 network, since a
# single host can pick any address in its /64. The client address is the one
# uvicorn reports, from X-Forwarded-For only when the peer is a trusted proxy.
PER_CLIENT_INFLIGHT = 16
IPV6_CLIENT_PREFIX = 64
# Busy responses ask the client to wait this many seconds before retrying.
RETRY_AFTER_SECONDS = 1
# Response bodies are sent in chunks, and each chunk waits until the client has
# read enough of the previous ones (uvicorn's write flow control). A client that
# reads nothing for SEND_TIMEOUT_SECONDS is dropped, and so is one that has not
# read the whole body within SEND_TIMEOUT_SECONDS plus its size at
# MIN_THROUGHPUT bytes per second. Bodies held for sending
# count against BYTE_BUDGET; a request that would exceed it gets a JSON 503. One
# year of all series hourly is about 6.6 MB, about 50 bytes per point.
CHUNK_BYTES = 64 * 1024
SEND_TIMEOUT_SECONDS = 10.0
MIN_THROUGHPUT = 64 * 1024
BYTE_BUDGET = 100 * 2**20
BYTES_PER_POINT = 52
SMALL_BODY_BYTES = 64 * 1024
# Bodies of at least CACHE_MIN_BYTES are kept for the current snapshot, at most
# CACHE_BYTES in total, least recently used out first. A repeated large request
# is then answered from memory without a gate or a query slot; sending it still
# counts against BYTE_BUDGET. 1 MiB is about 20,000 points, well below the
# heavy threshold; one year of all series hourly needs about 6.6 MB of it.
CACHE_BYTES = 32 * 2**20
CACHE_MIN_BYTES = 2**20
# All limits are per process; compose runs a single uvicorn worker. uvicorn's own
# --limit-concurrency (256 in compose.yaml) is only a backstop far above them.


class ServerBusyError(RuntimeError):
    """A gate, the in-flight limit or the byte budget has no room for the request."""


def error_response(detail: str) -> JSONResponse:
    """A JSON 503 with ``detail``."""
    return JSONResponse(status_code=503, content={"detail": detail})


def busy_response() -> JSONResponse:
    """The JSON 503 of a request refused for load, with Retry-After."""
    response = error_response(BUSY)
    response.headers["Retry-After"] = str(RETRY_AFTER_SECONDS)
    return response


def client_key(scope: Scope) -> str:
    """The address a request is counted under for the per-client limit.

    IPv4 addresses count as they are, IPv4-mapped IPv6 addresses as their IPv4
    address, other IPv6 addresses by their /64 network. A client value that is
    no IP address counts as it is; requests without a client share one key.
    """
    client = scope.get("client")
    if not client:
        return ""
    host = str(client[0])
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return str(address.ipv4_mapped)
        return str(ipaddress.IPv6Network((address, IPV6_CLIENT_PREFIX), strict=False))
    return str(address)


@dataclass(frozen=True)
class Limits:
    """Load limits of one API process; tests pass smaller values."""

    heavy_wait: float = HEAVY_WAIT_SECONDS
    query_wait: float = QUERY_WAIT_SECONDS
    max_inflight: int = MAX_INFLIGHT
    per_client_inflight: int = PER_CLIENT_INFLIGHT
    byte_budget: int = BYTE_BUDGET
    send_timeout: float = SEND_TIMEOUT_SECONDS
    min_throughput: float = MIN_THROUGHPUT
    cache_bytes: int = CACHE_BYTES
    cache_min_bytes: int = CACHE_MIN_BYTES


@dataclass
class ByteBudget:
    """Bytes of response bodies built and not yet handed to the server."""

    limit: int
    used: int = field(default=0)

    def reserve(self, size: int) -> None:
        if self.used + size > self.limit:
            raise ServerBusyError(BUSY)
        self.used += size

    def release(self, size: int) -> None:
        self.used -= size


class ChunkedResponse(Response):
    """Send the body in chunks so a slow client applies backpressure.

    ``done`` runs once the body is handed over or the client is dropped: after
    ``send_timeout`` seconds without progress, or when the whole body is not
    sent within ``send_timeout`` plus its size at ``min_throughput``.
    """

    def __init__(
        self,
        content: bytes,
        headers: dict[str, str],
        done: Callable[[], None],
        limits: Limits,
    ) -> None:
        super().__init__(content=content, media_type="application/json", headers=headers)
        self._done = done
        self._limits = limits

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        body = memoryview(self.body)
        total = self._limits.send_timeout + len(body) / self._limits.min_throughput
        try:
            async with asyncio.timeout(total):
                await send(
                    {
                        "type": "http.response.start",
                        "status": self.status_code,
                        "headers": self.raw_headers,
                    }
                )
                for offset in range(0, max(len(body), 1), CHUNK_BYTES):
                    end = offset + CHUNK_BYTES
                    async with asyncio.timeout(self._limits.send_timeout):
                        await send(
                            {
                                "type": "http.response.body",
                                "body": bytes(body[offset:end]),
                                "more_body": end < len(body),
                            }
                        )
        except TimeoutError:
            # Returning without the final body message is the only way ASGI offers
            # to abort a started response: the server closes the connection, and
            # the client gets fewer bytes than Content-Length announced, so it
            # knows the body is truncated. uvicorn also logs "ASGI callable
            # returned without completing response." at ERROR level for every
            # such drop; raising instead would log a traceback, and a final
            # message with more_body False would present the truncated body as
            # complete.
            log.warning("dropped a client that was too slow to read a %d byte body", len(body))
        finally:
            self._done()


class Gate:
    """Bound the number of concurrent requests of one kind.

    A request waits on the event loop, not in a worker thread, and gives up
    after ``wait`` seconds; when ``max_waiting`` requests already wait, it gives
    up at once. The semaphore is created per event loop, since asyncio
    primitives are bound to the loop they first wait on.
    """

    def __init__(self, slots: int, wait: float, max_waiting: int) -> None:
        self._slots = slots
        self._wait = wait
        self._max_waiting = max_waiting
        self._semaphores: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.BoundedSemaphore] = (
            WeakKeyDictionary()
        )
        self.waiting = 0

    def _semaphore(self) -> asyncio.BoundedSemaphore:
        loop = asyncio.get_running_loop()
        semaphore = self._semaphores.get(loop)
        if semaphore is None:
            semaphore = self._semaphores[loop] = asyncio.BoundedSemaphore(self._slots)
        return semaphore

    async def acquire(self) -> None:
        """Take a slot, or raise ServerBusyError."""
        semaphore = self._semaphore()
        if not semaphore.locked():
            await semaphore.acquire()
            return
        if self.waiting >= self._max_waiting:
            raise ServerBusyError(BUSY)
        self.waiting += 1
        try:
            async with asyncio.timeout(self._wait):
                await semaphore.acquire()
        except TimeoutError:
            raise ServerBusyError(BUSY) from None
        finally:
            self.waiting -= 1

    def release(self) -> None:
        self._semaphore().release()

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[None]:
        await self.acquire()
        try:
            yield
        finally:
            self.release()


class InflightLimit:
    """Answer requests beyond ``limit`` in flight, or beyond ``per_client`` in
    flight from one client address (see ``client_key``), with a JSON 503.

    /health and /status are neither counted nor limited, so they stay
    reachable under any number of connections. A request counts until the app
    returns, whether it completed, failed or the client went away.
    """

    def __init__(self, app: ASGIApp, limit: int, per_client: int, exempt: frozenset[str]) -> None:
        self.app = app
        self.limit = limit
        self.per_client = per_client
        self.exempt = exempt
        self.inflight = 0
        # Only clients with requests in flight have an entry.
        self.by_client: dict[str, int] = {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in self.exempt:
            await self.app(scope, receive, send)
            return
        key = client_key(scope)
        count = self.by_client.get(key, 0)
        if self.inflight >= self.limit or count >= self.per_client:
            await busy_response()(scope, receive, send)
            return
        self.inflight += 1
        self.by_client[key] = count + 1
        try:
            await self.app(scope, receive, send)
        finally:
            self.inflight -= 1
            remaining = self.by_client[key] - 1
            if remaining:
                self.by_client[key] = remaining
            else:
                del self.by_client[key]
