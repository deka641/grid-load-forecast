"""ETag and cache key from the validated parameters."""

from pathlib import Path
from typing import Any

import duckdb
import pytest
from fastapi.testclient import TestClient

from gridload.api import queries
from gridload.api.app import create_app
from gridload.api.limits import Limits
from gridload.config import Settings
from gridload.pipeline.db import migrate
from tests.test_api import add_run, publish
from tests.test_api_cache import HEAVY, fill
from tests.test_api_hardening import DAY, ISSUED

PAIR = {"series": "load,gen_solar", "start": "2025-01-01", "end": "2025-05-19"}


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    migrate(con)
    fill(con)
    add_run(con, "r1", "lightgbm", DAY, ISSUED, 1.0)
    settings = Settings(data_dir=data_dir)
    publish(con, settings)
    con.close()
    return settings


@pytest.fixture
def counted(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []
    original = queries.actuals_json

    def counting(*args: Any) -> bytes:
        calls.append(1)
        return original(*args)

    monkeypatch.setattr(queries, "actuals_json", counting)
    return calls


def tag(client: TestClient, url: str) -> str:
    response = client.get(url)
    assert response.status_code == 200, response.text
    return str(response.headers["etag"])


def test_ignored_parameters_neither_bypass_nor_evict_the_cache(
    settings: Settings, counted: list[int]
) -> None:
    query = "&".join(f"{k}={v}" for k, v in HEAVY.items())
    # Room for one body of this size only, so a second entry would evict the first.
    with TestClient(create_app(settings, limits=Limits(cache_bytes=3 * 2**20))) as client:
        first = client.get(f"/api/v1/actuals?{query}")
        assert first.status_code == 200
        assert len(first.content) > 2 * 2**20
        for n in range(5):
            again = client.get(f"/api/v1/actuals?{query}&n={n}")
            assert again.content == first.content
            assert again.headers["etag"] == first.headers["etag"]
        assert client.get(f"/api/v1/actuals?{query}").content == first.content
        assert len(counted) == 1
        assert len(client.app.state.cache) == 1  # type: ignore[attr-defined]


def test_equivalent_spellings_share_the_tag(settings: Settings) -> None:
    client = TestClient(create_app(settings))
    base = tag(client, "/api/v1/actuals?series=load&start=2025-01-01&end=2025-01-07")
    for url in (
        # Reordered parameters, the default resolution spelled out, a repeated key.
        "/api/v1/actuals?end=2025-01-07&series=load&start=2025-01-01",
        "/api/v1/actuals?series=load&start=2025-01-01&end=2025-01-07&resolution=hour",
        "/api/v1/actuals?series=load,load&start=2025-01-01&end=2025-01-07",
        "/api/v1/actuals?series=%20load&start=2025-01-01&end=2025-01-07&x=1",
    ):
        assert tag(client, url) == base, url
    days = tag(client, "/api/v1/forecasts/days")
    assert tag(client, "/api/v1/forecasts/days?limit=400") == days
    assert tag(client, "/api/v1/forecasts/days?limit=0400&junk=1") == days
    assert tag(client, "/api/v1/forecasts/days?limit=5") != days
    forecast = tag(client, "/api/v1/forecasts?delivery_day=2026-11-12")
    assert tag(client, "/api/v1/forecasts?x=1&delivery_day=2026-11-12") == forecast


def test_different_requests_keep_different_tags_and_bodies(
    settings: Settings, counted: list[int]
) -> None:
    # Every body is cached, so a shared key would serve the first body for both.
    with TestClient(create_app(settings, limits=Limits(cache_min_bytes=1))) as client:
        ordered = client.get("/api/v1/actuals", params=PAIR)
        swapped = client.get("/api/v1/actuals", params={**PAIR, "series": "gen_solar,load"})
        # The body lists the series in the requested order, so the order is part of the key.
        assert [s["key"] for s in ordered.json()["series"]] == ["load", "gen_solar"]
        assert [s["key"] for s in swapped.json()["series"]] == ["gen_solar", "load"]
        assert ordered.headers["etag"] != swapped.headers["etag"]
        assert len(counted) == 2
        quarter = client.get(
            "/api/v1/actuals",
            params={**PAIR, "end": "2025-01-31", "resolution": "quarterhour"},
        )
        hourly = client.get("/api/v1/actuals", params={**PAIR, "end": "2025-01-31"})
        assert quarter.headers["etag"] != hourly.headers["etag"]
