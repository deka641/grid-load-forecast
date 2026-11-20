"""Data queries behind the load limits, with ETag revalidation."""

import asyncio
import hashlib
import time
from collections.abc import Callable
from contextlib import AsyncExitStack

import anyio
import duckdb
from fastapi import Request, Response

from gridload.api.limits import (
    HEAVY_CONCURRENCY,
    HEAVY_MAX_WAITING,
    QUERY_CONCURRENCY,
    QUERY_MAX_WAITING,
    SMALL_BODY_BYTES,
    ByteBudget,
    ChunkedResponse,
    Gate,
    Limits,
)
from gridload.api.store import Snapshot, SnapshotStore

UNREADABLE = "the data snapshot cannot be read"
PROCESS_START = str(time.time_ns())


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
        self.budget = ByteBudget(limits.byte_budget)
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
        self, request: Request, run: Callable[[], bytes], *, gated: bool
    ) -> bytes | None:
        """Run the query behind the gates; None if the client disconnected first."""
        with anyio.CancelScope() as scope:
            watcher = asyncio.create_task(_until_disconnected(request, scope))
            try:
                async with AsyncExitStack() as gates:
                    if gated:
                        await gates.enter_async_context(self.heavy())
                    await gates.enter_async_context(self.query())
                    # Lets the watcher see a disconnect before the query starts.
                    await anyio.lowlevel.checkpoint()
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

        A matching If-None-Match is answered with 304 before any gate or query.
        ``estimate`` is the expected body size, reserved in the byte budget.
        """
        snapshot = self.store.current()
        headers = {"ETag": self.etag(snapshot, request), "Cache-Control": "no-cache"}
        if etag_matches(request.headers.get("if-none-match"), headers["ETag"]):
            return Response(status_code=304, headers=headers)

        def run() -> bytes:
            with self.store.cursor(snapshot) as (cur, _created):
                return build(cur)

        self.budget.reserve(estimate)
        try:
            body = await self._query(request, run, gated=gated)
        finally:
            self.budget.release(estimate)
        if body is None:
            # The client is gone; nobody reads this.
            return Response(status_code=499)
        size = len(body)
        # The estimate may have been low: the real size must fit as well.
        self.budget.reserve(size)
        return ChunkedResponse(body, headers, lambda: self.budget.release(size), self._limits)
