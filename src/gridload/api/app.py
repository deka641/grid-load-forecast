"""FastAPI application factory and the read-only HTTP API under ``/api/v1``."""

import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from typing import Annotated, Any

import duckdb
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from gridload import __version__
from gridload.api import queries
from gridload.api.models import (
    Actuals,
    BacktestMetrics,
    ErrorResponse,
    ForecastDay,
    ForecastDays,
    Health,
    Resolution,
    SeriesCatalog,
    SeriesFreshness,
    SeriesInfo,
    Status,
)
from gridload.api.params import invalid, parse_day, parse_series, to_local
from gridload.api.store import SnapshotStore, SnapshotUnavailableError
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
# Requests that read many rows (actuals, forecasts) run at most this many at a time.
HEAVY_CONCURRENCY = 2
# Fail fast under load so waiting requests do not hold uvicorn slots that
# /health and /status need (uvicorn runs with --limit-concurrency 32).
HEAVY_WAIT_SECONDS = 5.0
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


class ServerBusyError(RuntimeError):
    """Too many heavy requests are running."""


class HeavyGate:
    """Bound the number of concurrent requests that read many rows."""

    def __init__(self, slots: int, wait: float) -> None:
        self.slots = threading.BoundedSemaphore(slots)
        self._wait = wait

    @contextmanager
    def __call__(self) -> Iterator[None]:
        if not self.slots.acquire(timeout=self._wait):
            raise ServerBusyError(BUSY)
        try:
            yield
        finally:
            self.slots.release()


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


def _status(store: SnapshotStore, series: list[Series], now: datetime, price_note: str) -> Status:
    base: dict[str, Any] = {
        "generated_at": to_local(now),
        "attribution": ATTRIBUTION,
        "licence": LICENCE,
        "licence_url": LICENCE_URL,
        "price_note": price_note,
    }
    try:
        with store.cursor() as (cur, created):
            return Status(
                initialised=True,
                snapshot_created_at=to_local(created),
                snapshot_error=store.error,
                schema_version=queries.schema_version(cur),
                series=queries.freshness(cur, series, now),
                last_load_run=queries.last_load_run(cur),
                last_forecast=queries.last_forecast(cur),
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


def _utcnow() -> datetime:
    return datetime.now(UTC)


def create_app(
    settings: Settings | None = None,
    *,
    clock: Callable[[], datetime] = _utcnow,
    heavy_wait: float = HEAVY_WAIT_SECONDS,
) -> FastAPI:
    """Build the API application. The snapshot is opened lazily on the first request."""
    settings = settings or Settings.from_env()
    store = SnapshotStore(settings.serving_path)
    configured = {s.key: s for s in select(include_prices=settings.include_prices)}
    price_note = PRICE_NOTE if settings.include_prices else PRICE_NOTE_OFF
    heavy = HeavyGate(HEAVY_CONCURRENCY, heavy_wait)

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
    app.state.heavy_slots = heavy.slots

    @app.get("/api/v1/health", tags=["status"])
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
        responses=DATA_ERRORS,
    )
    def actuals(
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
        with heavy(), store.cursor() as (cur, _created):
            body = queries.actuals_json(cur, chosen, first, last, resolution)
        return Response(content=body, media_type="application/json")

    @app.get("/api/v1/forecasts", tags=["forecasts"], responses=DATA_ERRORS | NOT_FOUND)
    def forecasts(
        delivery_day: Annotated[
            str | None,
            Query(
                description="Local delivery day (Europe/Berlin); default: the latest day "
                "with a lightgbm forecast",
                pattern=DAY_PATTERN,
            ),
        ] = None,
    ) -> ForecastDay:
        """Return hourly forecasts, the SMARD forecast and the actual load for one day."""
        requested = None if delivery_day is None else parse_day("delivery_day", delivery_day)
        with heavy(), store.cursor() as (cur, _created):
            day = requested or queries.latest_forecast_day(cur)
            if day is None:
                raise HTTPException(404, "no forecast is available yet")
            result = queries.forecast_day(cur, day)
        if result is None:
            raise HTTPException(404, f"no forecast exists for delivery day {day.isoformat()}")
        return result

    @app.get("/api/v1/forecasts/days", tags=["forecasts"], responses=DATA_ERRORS)
    def forecast_days(
        limit: Annotated[
            int, Query(ge=1, le=MAX_DAYS_LIMIT, description="Maximum number of days")
        ] = DEFAULT_DAYS_LIMIT,
    ) -> ForecastDays:
        """List delivery days that have a forecast, newest first."""
        with store.cursor() as (cur, _created):
            return ForecastDays(days=queries.forecast_days(cur, limit), limit=limit)

    @app.get("/api/v1/backtest/metrics", tags=["forecasts"], responses=DATA_ERRORS | NOT_FOUND)
    def backtest_metrics() -> BacktestMetrics:
        """Return the metrics of the latest backtest run."""
        with store.cursor() as (cur, _created):
            result = queries.backtest(cur)
        if result is None:
            raise HTTPException(404, "no backtest run is available yet")
        return result

    @app.get("/api/v1/status", tags=["status"])
    def status() -> Status:
        """Report data freshness, licence and attribution; works without a snapshot."""
        return _status(store, list(served().values()), clock(), price_note)

    return app


app = create_app()
