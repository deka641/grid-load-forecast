"""Data queries behind the load limits, with ETag revalidation."""

import asyncio
import hashlib
import os
import time
from collections.abc import Callable
from contextlib import AsyncExitStack
from pathlib import Path

import anyio
import duckdb
from fastapi import Request, Response

import gridload
from gridload.api.cache import ResponseCache
from gridload.api.limits import (
    HEAVY_CONCURRENCY,
    HEAVY_MAX_WAITING,
    QUERY_CONCURRENCY,
    QUERY_MAX_WAITING,
    SMALL_BODY_BYTES,
    ByteBudget,
    ChunkedResponse,
    ClientGate,
    Gate,
    Limits,
    client_key,
)
from gridload.api.store import Snapshot, SnapshotStore

UNREADABLE = "the data snapshot cannot be read"
PROCESS_START = str(time.time_ns())
# The image build stores a content hash of the code and the lockfile next to the
# installed package (docker/python.Dockerfile); a local checkout has none.
BUILD_ID_FILE = Path(gridload.__file__).parent / "BUILD_ID"


def default_build_id() -> str:
    """GRIDLOAD_BUILD_ID, else the build id of the image, else the process start time."""
    from_env = os.environ.get("GRIDLOAD_BUILD_ID")
    if from_env:
        return from_env
    try:
        stored = BUILD_ID_FILE.read_text(encoding="ascii").strip()
    except OSError, UnicodeDecodeError:
        stored = ""
    return stored or PROCESS_START


def etag_matches(header: str | None, tag: str) -> bool:
    """Weak comparison of ``tag`` with the entity tags of an If-None-Match header.

    ``*`` is not honoured: it would also match a request that ends in 404, and a
    full response is always a valid answer.
    """
    if not header:
        return False
    return any(item.strip().removeprefix("W/") == tag for item in header.split(","))


async def _until_disconnected(request: Request, scope: anyio.CancelScope) -> None:
    """Cancel ``scope`` when the client disconnects."""
    while (await request.receive())["type"] != "http.disconnect":
        pass
    scope.cancel()


class DataService:
    """Run data queries in worker threads behind the gates, with ETag revalidation."""

    def __init__(self, store: SnapshotStore, limits: Limits, variant: str) -> None:
        self.store = store
        self.heavy = Gate(HEAVY_CONCURRENCY, limits.heavy_wait, HEAVY_MAX_WAITING)
        self.query = Gate(QUERY_CONCURRENCY, limits.query_wait, QUERY_MAX_WAITING)
        self.client_queries = ClientGate(limits.per_client_queries)
        self.budget = ByteBudget(limits.byte_budget)
        self.cache = ResponseCache(limits.cache_bytes, limits.cache_min_bytes)
        self._limits = limits
        # Build and settings that change a response without a new snapshot.
        self._variant = variant

    def etag(self, snapshot: Snapshot, request: Request) -> str:
        """Derive the entity tag from the snapshot file and the request."""
        raw = "|".join(
            (self._variant, *map(str, snapshot.key), request.url.path, request.url.query)
        )
        return f'"{hashlib.sha256(raw.encode()).hexdigest()[:32]}"'

    async def _query(
        self,
        request: Request,
        run: Callable[[], bytes],
        cached: Callable[[], bytes | None],
        *,
        gated: bool,
    ) -> bytes | None:
        """Run the query behind the gates; None if the client disconnected first.

        A body that an identical request cached while this one waited for its
        slots is returned without running the query.
        """
        with anyio.CancelScope() as scope:
            watcher = asyncio.create_task(_until_disconnected(request, scope))
            try:
                async with AsyncExitStack() as gates:
                    if gated:
                        await gates.enter_async_context(self.heavy())
                    # The client's own slot first, so its excess requests wait
                    # outside the shared queue; one deadline covers both waits.
                    deadline = time.monotonic() + self._limits.query_wait
                    await gates.enter_async_context(
                        self.client_queries(client_key(request.scope), self._limits.query_wait)
                    )
                    await gates.enter_async_context(self.query(deadline - time.monotonic()))
                    # Lets the watcher see a disconnect before the query starts.
                    await anyio.lowlevel.checkpoint()
                    if (body := cached()) is not None:
                        return body
                    return await anyio.to_thread.run_sync(run)
            finally:
                watcher.cancel()
        return None

    async def serve(
        self,
        request: Request,
        build: Callable[[duckdb.DuckDBPyConnection], bytes],
        *,
        estimate: int = SMALL_BODY_BYTES,
        gated: bool = False,
    ) -> Response:
        """Answer a data request; the body only changes with the snapshot.

        A matching If-None-Match is answered with 304 before any gate or query,
        and a cached body is sent without them. ``estimate`` is the expected
        body size, reserved in the byte budget.
        """
        snapshot = self.store.current()
        self.cache.select(snapshot.key)
        tag = self.etag(snapshot, request)
        headers = {"ETag": tag, "Cache-Control": "no-cache"}
        if etag_matches(request.headers.get("if-none-match"), tag):
            return Response(status_code=304, headers=headers)

        def cached() -> bytes | None:
            return self.cache.get(snapshot.key, tag)

        def run() -> bytes:
            with self.store.cursor(snapshot) as (cur, _created):
                return build(cur)

        body = cached()
        if body is None:
            self.budget.reserve(estimate)
            try:
                body = await self._query(request, run, cached, gated=gated)
            finally:
                self.budget.release(estimate)
            if body is None:
                # The client is gone; nobody reads this.
                return Response(status_code=499)
            self.cache.put(snapshot.key, tag, body)
        size = len(body)
        # The estimate may have been low: the real size must fit as well.
        self.budget.reserve(size)
        return ChunkedResponse(body, headers, lambda: self.budget.release(size), self._limits)
