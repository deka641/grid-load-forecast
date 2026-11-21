"""Routes of the HTTP API under ``/api/v1``, one router per OpenAPI tag.

The status tag has two routers, so that the application can include /health
first and /status last and keep the route order of the OpenAPI document.
"""

from collections.abc import Callable
from datetime import date, datetime
from typing import Annotated, Any

import duckdb
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from gridload import __version__
from gridload.api import queries
from gridload.api.limits import HEAVY_POINTS
from gridload.api.models import (
    Actuals,
    BacktestMetrics,
    ErrorResponse,
    ForecastDay,
    ForecastDays,
    Health,
    Resolution,
    SeriesCatalog,
    SeriesInfo,
    Status,
)
from gridload.api.params import invalid, parse_day, parse_series
from gridload.api.service import DataService
from gridload.api.status import StatusFacts, build_status
from gridload.api.store import SnapshotStore
from gridload.series import Series

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
BUSY_ERROR: dict[int | str, dict[str, Any]] = {
    503: {"model": ErrorResponse, "description": "The server is busy"}
}
NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "No matching data in the snapshot"}
}
NOT_MODIFIED: dict[int | str, dict[str, Any]] = {
    304: {"description": "The ETag in If-None-Match is current; the data has not changed"}
}

Served = Callable[[], dict[str, Series]]


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


def health_router() -> APIRouter:
    """The liveness route."""
    router = APIRouter(tags=["status"])

    @router.get(HEALTH_PATH)
    async def health() -> Health:
        """Report that the process is alive."""
        return Health(status="ok", version=__version__)

    return router


def data_router(data: DataService, served: Served, *, bytes_per_point: int) -> APIRouter:
    """The series catalogue and measured values.

    ``bytes_per_point`` is the body size per point reserved in the byte budget.
    """
    router = APIRouter(tags=["data"])
    serve = data.serve

    @router.get("/api/v1/series", responses=BUSY_ERROR)
    def series() -> SeriesCatalog:
        """List the series this deployment serves."""
        return SeriesCatalog(
            series=[
                SeriesInfo(key=s.key, label=s.label, unit=s.unit, category=s.category.value)
                for s in served().values()
            ]
        )

    @router.get(
        "/api/v1/actuals",
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
            # The body lists the series in the requested order, so the key keeps it.
            params={
                "series": ",".join(s.key for s in chosen),
                "start": first.isoformat(),
                "end": last.isoformat(),
                "resolution": resolution.value,
            },
            estimate=points * bytes_per_point,
            gated=points > HEAVY_POINTS,
        )

    return router


def forecasts_router(data: DataService) -> APIRouter:
    """Forecasts, their delivery days and the backtest metrics."""
    router = APIRouter(tags=["forecasts"])
    serve = data.serve

    @router.get(
        "/api/v1/forecasts",
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

        params = {} if requested is None else {"delivery_day": requested.isoformat()}
        return await serve(request, build, params=params)

    @router.get(
        "/api/v1/forecasts/days",
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
            params={"limit": str(limit)},
        )

    @router.get(
        "/api/v1/backtest/metrics",
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

        return await serve(request, build, params={})

    return router


def status_router(
    store: SnapshotStore, served: Served, clock: Callable[[], datetime], price_note: str
) -> APIRouter:
    """The freshness, licence and attribution report."""
    router = APIRouter(tags=["status"])
    status_cache: dict[str, StatusFacts] = {}

    @router.get(STATUS_PATH)
    async def status() -> Status:
        """Report data freshness, licence and attribution; works without a snapshot."""
        # Runs on the event loop: it needs no worker thread and no gate. The
        # snapshot queries run once per snapshot and take about 25 ms.
        return build_status(store, status_cache, list(served().values()), clock(), price_note)

    return router
