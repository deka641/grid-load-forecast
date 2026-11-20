"""Resource limits and failure handling of the API."""

import json
import logging
import math
import tracemalloc
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pytest

from gridload.api.models import Actuals, ForecastDay
from gridload.api.store import NEWEST_UNOPENABLE
from gridload.config import Settings
from gridload.pipeline import snapshot
from gridload.pipeline.db import migrate
from gridload.series import CATALOG
from tests.test_api import add_run, get, make_client, publish

DAY = date(2026, 11, 12)
ISSUED = datetime(2026, 11, 11, 9, tzinfo=UTC)
ALL_KEYS = ",".join(s.key for s in CATALOG)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return Settings(data_dir=data_dir)


@pytest.fixture
def empty_wh() -> Iterator[duckdb.DuckDBPyConnection]:
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    migrate(con)
    yield con
    con.close()


def fill_year(con: duckdb.DuckDBPyConnection) -> None:
    """Synthetic quarter hours for every series from 2025-01-01 to 2026-01-01 local."""
    con.execute(
        "INSERT INTO observations SELECT s.series_key, 'quarterhour', g.ts, "
        "1000 + (epoch(g.ts) % 7919) / 7.0, now(), 0 FROM series s, generate_series("
        "TIMESTAMPTZ '2024-12-31 23:00:00+00', TIMESTAMPTZ '2026-01-01 22:45:00+00', "
        "INTERVAL 15 MINUTE) g(ts)"
    )


# Response size and memory --------------------------------------------------------------


def test_maximal_actuals_request_stays_within_a_memory_bound(
    empty_wh: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    fill_year(empty_wh)
    publish(empty_wh, settings)
    client = make_client(settings)
    params = {"series": ALL_KEYS, "start": "2025-01-01", "end": "2026-01-01"}
    get(client, "/api/v1/status")  # open the snapshot outside the measurement
    tracemalloc.start()
    try:
        response = client.get("/api/v1/actuals", params=params)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert response.status_code == 200
    assert len(response.content) > 7_500_000
    body = response.json()
    assert [len(s["points"]) for s in body["series"]] == [8784] * len(CATALOG)
    # Measured: 22.7 MB peak of Python allocations for this request (7.8 MB body,
    # 131,760 points), including the test client's copy of the body. With one
    # pydantic model per point, a comparable snapshot (8.1 MB body) peaked at 96.6 MB.
    assert peak < 40 * 2**20


def test_actuals_json_matches_the_response_model(
    empty_wh: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    fill_year(empty_wh)
    publish(empty_wh, settings)
    response = make_client(settings).get(
        "/api/v1/actuals",
        params={
            "series": "load,price_day_ahead",
            "start": "2025-10-26",
            "end": "2025-10-26",
            "resolution": "quarterhour",
        },
    )
    assert response.headers["content-type"] == "application/json"
    model = Actuals.model_validate_json(response.content)
    assert json.loads(model.model_dump_json()) == response.json()
    assert [len(s.points) for s in model.series] == [100, 100]
    assert model.series[1].unit == "EUR/MWh"


def test_heavy_endpoints_answer_503_while_all_slots_are_taken(
    empty_wh: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    add_run(empty_wh, "r1", "lightgbm", DAY, ISSUED, 1.0)
    publish(empty_wh, settings)
    client = make_client(settings, heavy_wait=0.01)
    slots = client.app.state.heavy_slots  # type: ignore[attr-defined]
    params = {"series": "load", "start": "2026-11-10", "end": "2026-11-10"}
    for _ in range(2):
        assert slots.acquire(blocking=False)
    try:
        for url, query in (("/api/v1/actuals", params), ("/api/v1/forecasts", {})):
            response = client.get(url, params=query)
            assert response.status_code == 503
            assert response.json() == {"detail": "the server is busy; try again shortly"}
        # Light endpoints are not limited.
        assert get(client, "/api/v1/forecasts/days")["days"] == ["2026-11-12"]
    finally:
        slots.release()
        slots.release()
    assert client.get("/api/v1/actuals", params=params).status_code == 200
    assert client.get("/api/v1/forecasts").status_code == 200


# Broken snapshots ----------------------------------------------------------------------


def test_query_error_is_503_and_status_falls_back(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    # Opens (it has a series table) but lacks every other table.
    con = duckdb.connect(str(settings.serving_path))
    con.execute("CREATE TABLE series (series_key VARCHAR)")
    con.execute("INSERT INTO series VALUES ('load')")
    con.close()
    client = make_client(settings)
    with caplog.at_level(logging.ERROR, logger="gridload.api.app"):
        response = client.get("/api/v1/forecasts/days")
    assert response.status_code == 503
    assert response.json() == {"detail": "the data snapshot cannot be read"}
    assert "query failed for /api/v1/forecasts/days" in caplog.text

    body = get(client, "/api/v1/status")
    assert body["initialised"] is False
    assert body["snapshot_error"] == "the data snapshot cannot be read"
    assert body["attribution"] == "Bundesnetzagentur | SMARD.de"
    assert body["licence"] == "CC BY 4.0"
    assert [s["key"] for s in body["series"]] == ["load"]
    assert [s["key"] for s in get(client, "/api/v1/series")["series"]] == ["load"]


def test_unreadable_replacement_is_tried_once_and_reported(
    empty_wh: duckdb.DuckDBPyConnection, settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    add_run(empty_wh, "r1", "lightgbm", DAY, ISSUED, 1.0)
    publish(empty_wh, settings)
    client = make_client(settings)
    assert get(client, "/api/v1/status")["snapshot_error"] is None

    settings.serving_path.with_name("bad").write_bytes(b"garbage" * 1000)
    settings.serving_path.with_name("bad").replace(settings.serving_path)
    with caplog.at_level(logging.WARNING, logger="gridload.api.store"):
        for _ in range(3):
            assert get(client, "/api/v1/forecasts/days")["days"] == ["2026-11-12"]
        body = get(client, "/api/v1/status")
    assert len([r for r in caplog.records if "cannot open snapshot" in r.message]) == 1
    assert body["initialised"] is True
    assert body["snapshot_error"] == NEWEST_UNOPENABLE

    publish(empty_wh, settings)
    assert get(client, "/api/v1/status")["snapshot_error"] is None


def test_unreadable_first_snapshot_is_reported_in_status(settings: Settings) -> None:
    settings.serving_path.write_bytes(b"garbage" * 1000)
    body = get(make_client(settings), "/api/v1/status")
    assert body["initialised"] is False
    assert body["snapshot_error"] == NEWEST_UNOPENABLE


# Catalog and prices --------------------------------------------------------------------


def test_series_missing_from_the_snapshot_are_not_served(
    empty_wh: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    snapshot.publish(empty_wh, settings.serving_path, exclude_series=["price_day_ahead"])
    client = make_client(settings)
    keys = [s["key"] for s in get(client, "/api/v1/series")["series"]]
    assert keys == [s.key for s in CATALOG if s.key != "price_day_ahead"]
    response = client.get(
        "/api/v1/actuals",
        params={"series": "load,price_day_ahead", "start": "2026-11-10", "end": "2026-11-10"},
    )
    assert response.status_code == 422
    assert "unknown series: price_day_ahead" in response.json()["detail"][0]["msg"]
    assert "price_day_ahead" not in [s["key"] for s in get(client, "/api/v1/status")["series"]]


def test_price_note_says_when_prices_are_switched_off(settings: Settings) -> None:
    on = get(make_client(settings), "/api/v1/status")["price_note"]
    off = get(
        make_client(Settings(data_dir=settings.data_dir, include_prices=False)), "/api/v1/status"
    )["price_note"]
    assert "price ingestion can be switched off" in on
    assert off.startswith("Wholesale prices are switched off in this deployment.")
    assert "HEDWIG" in off


# Non-finite values ---------------------------------------------------------------------


def test_non_finite_values_never_break_a_response(
    empty_wh: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    hour = datetime(2026, 11, 11, 23, tzinfo=UTC)  # 2026-11-12 00:00 local
    empty_wh.executemany(
        "INSERT INTO observations VALUES ('load', 'quarterhour', ?, ?, now(), 0)",
        [
            [datetime(2026, 11, 11, 23, 15 * q, tzinfo=UTC), v]
            for q, v in enumerate([1.0, math.nan, 1.0, 1.0])
        ]
        + [[datetime(2026, 11, 12, 0, 15 * q, tzinfo=UTC), 2.0] for q in range(4)],
    )
    add_run(
        empty_wh,
        "r1",
        "lightgbm",
        DAY,
        ISSUED,
        1.0,
        extra={hour: math.inf},
    )
    empty_wh.execute(
        "INSERT INTO backtest_runs VALUES ('b', ?, ?, ?, '{}')",
        [ISSUED, date(2025, 1, 1), date(2025, 12, 31)],
    )
    empty_wh.executemany(
        "INSERT INTO backtest_metrics VALUES ('b', 'lightgbm', ?, ?, ?, ?, 10)",
        [
            ["overall", "all", 1.0, 2.0],
            ["hour", "0", math.nan, 2.0],
            ["hour", "1", 1.0, math.inf],
            ["hour", "2", 1.0, 2.0],
        ],
    )
    publish(empty_wh, settings)
    client = make_client(settings)

    quarter = get(
        client,
        "/api/v1/actuals",
        {"series": "load", "start": "2026-11-12", "end": "2026-11-12", "resolution": "quarterhour"},
    )["series"][0]["points"]
    expected = ["00:00", "00:30", "00:45", "01:00", "01:15", "01:30", "01:45"]
    assert [p["ts"][11:16] for p in quarter] == expected
    hourly = get(
        client, "/api/v1/actuals", {"series": "load", "start": "2026-11-12", "end": "2026-11-12"}
    )["series"][0]["points"]
    assert hourly == [{"ts": "2026-11-12T01:00:00+01:00", "value": 8.0}]

    forecast = ForecastDay.model_validate(get(client, "/api/v1/forecasts"))
    assert forecast.points[0].lightgbm is None
    assert forecast.points[0].actual is None
    assert forecast.points[1].lightgbm == 2.0
    assert forecast.points[1].actual == 8.0

    metrics = get(client, "/api/v1/backtest/metrics")["metrics"]["lightgbm"]
    assert [m["scope_value"] for m in metrics["hour"]] == ["2"]
    assert len(metrics["overall"]) == 1
