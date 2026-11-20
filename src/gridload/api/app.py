"""FastAPI application factory."""

from fastapi import FastAPI
from pydantic import BaseModel

from gridload import __version__


class Health(BaseModel):
    """Liveness response."""

    status: str
    version: str


def create_app() -> FastAPI:
    """Build the API application."""
    app = FastAPI(
        title="grid-load-forecast API",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )

    @app.get("/api/v1/health", tags=["status"])
    def health() -> Health:
        """Report that the process is alive."""
        return Health(status="ok", version=__version__)

    return app


app = create_app()
