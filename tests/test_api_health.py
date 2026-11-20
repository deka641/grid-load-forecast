from fastapi.testclient import TestClient

from gridload import __version__
from gridload.api.app import create_app


def test_health_reports_ok_and_version() -> None:
    client = TestClient(create_app())
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_openapi_is_served_under_api_prefix() -> None:
    client = TestClient(create_app())
    assert client.get("/api/openapi.json").json()["info"]["title"] == "grid-load-forecast API"
