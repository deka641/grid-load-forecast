"""FastAPI application factory and the read-only HTTP API under ``/api/v1``."""

import asyncio
import hashlib
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Annotated, Any
from weakref import WeakKeyDictionary

import anyio
import duckdb
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from gridload import __version__
from gridload.api import queries
from gridload.api.models import (
    Actuals,
    BacktestMetrics,
    ErrorResponse,
    ForecastDay,
    ForecastDays,
    Health,
    LastForecast,
    LoadRunInfo,
    Resolution,
    SeriesCatalog,
    SeriesFreshness,
    SeriesInfo,
    Status,
)
from gridload.api.params import invalid, parse_day, parse_series, to_local
from gridload.api.store import Snapshot, SnapshotStore, SnapshotUnavailableError
from gridload.config import Settings
from gridload.series import Series, select

__all__ = ["Health", "app", "create_app"]

log = logging.getLogger(__name__)

ATTRIBUTION = "Bundesnetzagentur | SMARD.de"
LICENCE = "CC BY 4.0"
LICENCE_URL = "https://creativecommons.org/licenses/by/4.0/"
PRICE_NOTE = (
    "The Bundesnetzagentur reviews the licensing of the wholesale electricity prices "
    "shown on SMARD.de in its HEDWIG procedure. Until that review is concluded, price "
    "data is shown with that reservation, and price ingestion can be switched off."
)
PRICE_NOTE_OFF = (
    "Wholesale prices are switched off in this deployment. The Bundesnetzagentur reviews "
    "the licensing of the wholesale electricity prices shown on SMARD.de in its HEDWIG "
    "procedure."
)
UNREADABLE = "the data snapshot cannot be read"
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
QUERY_MAX_WAITING = 64
# Requests in flight, apart from /health and /status.
MAX_INFLIGHT = 64
HEALTH_PATH = "/api/v1/health"
STATUS_PATH = "/api/v1/status"
POINTS_PER_DAY = {Resolution.QUARTERHOUR: 96, Resolution.HOUR: 24}
MAX_DAYS = {Resolution.QUARTERHOUR: 31, Resolution.HOUR: 366}
DEFAULT_DAYS_LIMIT = 400
MAX_DAYS_LIMIT = 5000

DAY_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
DATA_ERRORS: dict[int | str, dict[str, Any]] = {
    503: {
        "model": ErrorResponse,
        "description": "No readable data snapshot is available, or the server is busy",
    }
}
NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "No matching data in the snapshot"}
}
NOT_MODIFIED: dict[int | str, dict[str, Any]] = {
    304: {"description": "The ETag in If-None-Match is current; the data has not changed"}
}


class ServerBusyError(RuntimeError):
    """Too many heavy requests are running."""


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
        self._semaphores: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
            WeakKeyDictionary()
        )
        self.waiting = 0

    def _semaphore(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        semaphore = self._semaphores.get(loop)
        if semaphore is None:
            semaphore = self._semaphores[loop] = asyncio.Semaphore(self._slots)
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
    """Answer requests beyond ``limit`` in flight with a JSON 503.

    /health and /status are neither counted nor limited, so they stay
    reachable under any number of connections.
    """

    def __init__(self, app: ASGIApp, limit: int, exempt: frozenset[str]) -> None:
        self.app = app
        self.limit = limit
        self.exempt = exempt
        self.inflight = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in self.exempt:
            await self.app(scope, receive, send)
            return
        if self.inflight >= self.limit:
            await _error_response(BUSY)(scope, receive, send)
            return
        self.inflight += 1
        try:
            await self.app(scope, receive, send)
        finally:
            self.inflight -= 1


def etag_matches(header: str | None, tag: str) -> bool:
    """Weak comparison of ``tag`` with the entity tags of an If-None-Match header.

    ``*`` is not honoured: it would also match a request that ends in 404, and a
    full response is always a valid answer.
    """
    if not header:
        return False
    return any(item.strip().removeprefix("W/") == tag for item in header.split(","))


@dataclass(frozen=True)
class StatusFacts:
    """The parts of /status that only change with the snapshot."""

    key: tuple[int, int]
    created_at: datetime
    schema_version: int | None
    last_timestamps: dict[str, datetime]
    last_load_run: LoadRunInfo | None
    last_forecast: LastForecast | None


def _status_facts(store: SnapshotStore, snapshot: Snapshot) -> StatusFacts:
    with store.cursor(snapshot) as (cur, created):
        return StatusFacts(
            key=snapshot.key,
            created_at=created,
            schema_version=queries.schema_version(cur),
            last_timestamps=queries.last_timestamps(cur),
            last_load_run=queries.last_load_run(cur),
            last_forecast=queries.last_forecast(cur),
        )


def _error_response(detail: str) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": detail})


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(SnapshotUnavailableError)
    def snapshot_unavailable(_request: Request, exc: SnapshotUnavailableError) -> JSONResponse:
        return _error_response(str(exc))

    @app.exception_handler(ServerBusyError)
    def server_busy(_request: Request, exc: ServerBusyError) -> JSONResponse:
        return _error_response(str(exc))

    @app.exception_handler(duckdb.Error)
    def snapshot_unreadable(request: Request, exc: duckdb.Error) -> JSONResponse:
        log.error("query failed for %s: %s", request.url.path, exc)
        return _error_response(UNREADABLE)


def _day_range(start: str, end: str, resolution: Resolution) -> tuple[date, date]:
    first = parse_day("start", start)
    last = parse_day("end", end)
    if last < first:
        raise invalid("end", end, "end must not be before start")
    if (last - first).days + 1 > MAX_DAYS[resolution]:
        raise invalid(
            "end", end, f"at most {MAX_DAYS[resolution]} days at resolution {resolution.value}"
        )
    return first, last


def _status(
    store: SnapshotStore,
    cache: dict[str, StatusFacts],
    series: list[Series],
    now: datetime,
    price_note: str,
) -> Status:
    """Build /status; the snapshot queries run once per snapshot, the ages on every call."""
    base: dict[str, Any] = {
        "generated_at": to_local(now),
        "attribution": ATTRIBUTION,
        "licence": LICENCE,
        "licence_url": LICENCE_URL,
        "price_note": price_note,
    }
    try:
        snapshot = store.current()
        facts = cache.get("facts")
        if facts is None or facts.key != snapshot.key:
            facts = cache["facts"] = _status_facts(store, snapshot)
        return Status(
            initialised=True,
            snapshot_created_at=to_local(facts.created_at),
            snapshot_error=store.error,
            schema_version=facts.schema_version,
            series=queries.freshness(facts.last_timestamps, series, now),
            last_load_run=facts.last_load_run,
            last_forecast=facts.last_forecast,
            **base,
        )
    except SnapshotUnavailableError:
        error = store.error
    except duckdb.Error as exc:
        log.error("status query failed: %s", exc)
        error = UNREADABLE
    # Attribution and licence are reported even without readable data.
    return Status(
        initialised=False,
        snapshot_created_at=None,
        snapshot_error=error,
        schema_version=None,
        series=[
            SeriesFreshness(key=s.key, label=s.label, last_ts=None, age_seconds=None)
            for s in series
        ],
        last_load_run=None,
        last_forecast=None,
        **base,
    )


class DataService:
    """Run data queries in worker threads behind the gates, with ETag revalidation."""

    def __init__(self, store: SnapshotStore, heavy: Gate, query: Gate, variant: str) -> None:
        self.store = store
        self.heavy = heavy
        self.query = query
        # Code version and settings that change a response without a new snapshot.
        self._variant = variant

    def etag(self, snapshot: Snapshot, request: Request) -> str:
        """Derive the entity tag from the snapshot file and the request."""
        raw = "|".join(
            (self._variant, *map(str, snapshot.key), request.url.path, request.url.query)
        )
        return f'"{hashlib.sha256(raw.encode()).hexdigest()[:32]}"'

    async def serve(
        self,
        request: Request,
        build: Callable[[duckdb.DuckDBPyConnection], bytes],
        *,
        gated: bool = False,
    ) -> Response:
        """Answer a data request; the body only changes with the snapshot.

        A matching If-None-Match is answered with 304 before any gate or query.
        """
        snapshot = self.store.current()
        headers = {"ETag": self.etag(snapshot, request), "Cache-Control": "no-cache"}
        if etag_matches(request.headers.get("if-none-match"), headers["ETag"]):
            return Response(status_code=304, headers=headers)

        def run() -> bytes:
            with self.store.cursor(snapshot) as (cur, _created):
                return build(cur)

        async with AsyncExitStack() as gates:
            if gated:
                await gates.enter_async_context(self.heavy())
            await gates.enter_async_context(self.query())
            body = await anyio.to_thread.run_sync(run)
        return Response(content=body, media_type="application/json", headers=headers)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def create_app(
    settings: Settings | None = None,
    *,
    clock: Callable[[], datetime] = _utcnow,
    heavy_wait: float = HEAVY_WAIT_SECONDS,
    query_wait: float = QUERY_WAIT_SECONDS,
    max_inflight: int = MAX_INFLIGHT,
) -> FastAPI:
    """Build the API application. The snapshot is opened lazily on the first request."""
    settings = settings or Settings.from_env()
    store = SnapshotStore(settings.serving_path)
    configured = {s.key: s for s in select(include_prices=settings.include_prices)}
    price_note = PRICE_NOTE if settings.include_prices else PRICE_NOTE_OFF
    data = DataService(
        store,
        Gate(HEAVY_CONCURRENCY, heavy_wait, HEAVY_MAX_WAITING),
        Gate(QUERY_CONCURRENCY, query_wait, QUERY_MAX_WAITING),
        f"{__version__}|{settings.include_prices}",
    )
    serve = data.serve
    status_cache: dict[str, StatusFacts] = {}

    def served() -> dict[str, Series]:
        """Configured series, limited to those in the snapshot when one is available."""
        present = store.series_keys()
        if present is None:
            return configured
        return {k: s for k, s in configured.items() if k in present}

    app = FastAPI(
        title="grid-load-forecast API",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )

    _install_error_handlers(app)
    app.add_middleware(
        InflightLimit, limit=max_inflight, exempt=frozenset({HEALTH_PATH, STATUS_PATH})
    )
    app.state.heavy_gate = data.heavy
    app.state.query_gate = data.query

    @app.get(HEALTH_PATH, tags=["status"])
    async def health() -> Health:
        """Report that the process is alive."""
        return Health(status="ok", version=__version__)

    @app.get("/api/v1/series", tags=["data"])
    def series() -> SeriesCatalog:
        """List the series this deployment serves."""
        return SeriesCatalog(
            series=[
                SeriesInfo(key=s.key, label=s.label, unit=s.unit, category=s.category.value)
                for s in served().values()
            ]
        )

    @app.get(
        "/api/v1/actuals",
        tags=["data"],
        response_model=Actuals,
        response_class=JSONResponse,
        responses=NOT_MODIFIED | DATA_ERRORS,
    )
    async def actuals(
        request: Request,
        series: Annotated[
            str,
            Query(
                description="Comma-separated series keys from /api/v1/series (at most 20)",
                examples=["load,gen_solar"],
                max_length=1000,
            ),
        ],
        start: Annotated[
            str, Query(description="First local day (Europe/Berlin)", pattern=DAY_PATTERN)
        ],
        end: Annotated[
            str, Query(description="Last local day (Europe/Berlin)", pattern=DAY_PATTERN)
        ],
        resolution: Annotated[
            Resolution,
            Query(description="hour: at most 366 days; quarterhour: at most 31 days"),
        ] = Resolution.HOUR,
    ) -> Response:
        """Return measured values for the local days ``start`` to ``end`` inclusive."""
        chosen = parse_series(series, served())
        first, last = _day_range(start, end, resolution)
        points = len(chosen) * ((last - first).days + 1) * POINTS_PER_DAY[resolution]
        return await serve(
            request,
            lambda cur: queries.actuals_json(cur, chosen, first, last, resolution),
            gated=points > HEAVY_POINTS,
        )

    @app.get(
        "/api/v1/forecasts",
        tags=["forecasts"],
        response_model=ForecastDay,
        response_class=JSONResponse,
        responses=NOT_MODIFIED | DATA_ERRORS | NOT_FOUND,
    )
    async def forecasts(
        request: Request,
        delivery_day: Annotated[
            str | None,
            Query(
                description="Local delivery day (Europe/Berlin); default: the latest day "
                "with a lightgbm forecast",
                pattern=DAY_PATTERN,
            ),
        ] = None,
    ) -> Response:
        """Return hourly forecasts, the SMARD forecast and the actual load for one day."""
        requested = None if delivery_day is None else parse_day("delivery_day", delivery_day)

        def build(cur: duckdb.DuckDBPyConnection) -> bytes:
            day = requested or queries.latest_forecast_day(cur)
            if day is None:
                raise HTTPException(404, "no forecast is available yet")
            result = queries.forecast_day(cur, day)
            if result is None:
                raise HTTPException(404, f"no forecast exists for delivery day {day.isoformat()}")
            return result.model_dump_json().encode()

        return await serve(request, build)

    @app.get(
        "/api/v1/forecasts/days",
        tags=["forecasts"],
        response_model=ForecastDays,
        response_class=JSONResponse,
        responses=NOT_MODIFIED | DATA_ERRORS,
    )
    async def forecast_days(
        request: Request,
        limit: Annotated[
            int, Query(ge=1, le=MAX_DAYS_LIMIT, description="Maximum number of days")
        ] = DEFAULT_DAYS_LIMIT,
    ) -> Response:
        """List delivery days that have a forecast, newest first."""
        return await serve(
            request,
            lambda cur: (
                ForecastDays(days=queries.forecast_days(cur, limit), limit=limit)
                .model_dump_json()
                .encode()
            ),
        )

    @app.get(
        "/api/v1/backtest/metrics",
        tags=["forecasts"],
        response_model=BacktestMetrics,
        response_class=JSONResponse,
        responses=NOT_MODIFIED | DATA_ERRORS | NOT_FOUND,
    )
    async def backtest_metrics(request: Request) -> Response:
        """Return the metrics of the latest backtest run."""

        def build(cur: duckdb.DuckDBPyConnection) -> bytes:
            result = queries.backtest(cur)
            if result is None:
                raise HTTPException(404, "no backtest run is available yet")
            return result.model_dump_json().encode()

        return await serve(request, build)

    @app.get(STATUS_PATH, tags=["status"])
    async def status() -> Status:
        """Report data freshness, licence and attribution; works without a snapshot."""
        # Runs on the event loop: it needs no worker thread and no gate. The
        # snapshot queries run once per snapshot and take about 25 ms.
        return _status(store, status_cache, list(served().values()), clock(), price_note)

    return app


app = create_app()
