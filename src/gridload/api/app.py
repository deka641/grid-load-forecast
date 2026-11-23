"""FastAPI application factory and the read-only HTTP API under ``/api/v1``."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime

import duckdb
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from gridload import __version__
from gridload.api.limits import (
    BYTES_PER_POINT,
    InflightLimit,
    Limits,
    ServerBusyError,
    busy_response,
    error_response,
)
from gridload.api.routes import (
    HEALTH_PATH,
    STATUS_PATH,
    data_router,
    forecasts_router,
    health_router,
    status_router,
)
from gridload.api.service import UNREADABLE, DataService, default_build_id
from gridload.api.status import PRICE_NOTE, PRICE_NOTE_OFF
from gridload.api.store import SnapshotStore, SnapshotUnavailableError
from gridload.config import Settings
from gridload.series import Series, select

__all__ = ["app", "create_app"]

log = logging.getLogger(__name__)


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(SnapshotUnavailableError)
    def snapshot_unavailable(_request: Request, exc: SnapshotUnavailableError) -> JSONResponse:
        return error_response(str(exc))

    @app.exception_handler(ServerBusyError)
    def server_busy(_request: Request, _exc: ServerBusyError) -> JSONResponse:
        return busy_response()

    @app.exception_handler(duckdb.Error)
    def snapshot_unreadable(request: Request, exc: duckdb.Error) -> JSONResponse:
        log.error("query failed for %s: %s", request.url.path, exc)
        return error_response(UNREADABLE)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def create_app(
    settings: Settings | None = None,
    *,
    clock: Callable[[], datetime] = _utcnow,
    limits: Limits | None = None,
    build_id: str | None = None,
) -> FastAPI:
    """Build the API application. The snapshot is opened lazily on the first request.

    ``build_id`` (default: GRIDLOAD_BUILD_ID, else the content hash stored by
    the image build, else the process start time) is part of every ETag, so a
    deployment of changed code invalidates the tags of the previous one.
    """
    limits = limits or Limits()
    build_id = build_id or default_build_id()
    settings = settings or Settings.from_env()
    store = SnapshotStore(settings.serving_path)
    configured = {s.key: s for s in select(include_prices=settings.include_prices)}
    price_note = PRICE_NOTE if settings.include_prices else PRICE_NOTE_OFF
    data = DataService(store, limits, f"{__version__}|{build_id}|{settings.include_prices}")

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
        # No redirect for a trailing slash: Starlette builds its Location from the
        # Host header, which any client chooses, and always with http.
        redirect_slashes=False,
    )

    _install_error_handlers(app)
    app.add_middleware(
        InflightLimit,
        limit=limits.max_inflight,
        per_client=limits.per_client_inflight,
        exempt=frozenset({HEALTH_PATH, STATUS_PATH}),
    )
    app.state.heavy_gate = data.heavy
    app.state.budget = data.budget
    app.state.query_gate = data.query
    app.state.client_queries = data.client_queries
    app.state.cache = data.cache

    # The include order is the route order of the OpenAPI document.
    app.include_router(health_router())
    app.include_router(data_router(data, served, bytes_per_point=BYTES_PER_POINT))
    app.include_router(forecasts_router(data))
    app.include_router(status_router(store, served, clock, price_note))
    return app


app = create_app()
