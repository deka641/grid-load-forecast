"""Resource limits and failure handling of the API."""

import asyncio
import json
import logging
import math
import time
import tracemalloc
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient

from gridload.api import queries
from gridload.api.app import HEAVY_MAX_WAITING, create_app
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


BUSY = {"detail": "the server is busy; try again shortly"}
# 15 series x 139 days x 24 hours = 50,040 points: just above the heavy threshold.
HEAVY = {"series": ALL_KEYS, "start": "2025-01-01", "end": "2025-05-19"}
LIGHT = {"series": ALL_KEYS, "start": "2025-01-01", "end": "2025-05-18"}


@pytest.fixture
def forecast_client(
    empty_wh: duckdb.DuckDBPyConnection, settings: Settings
) -> Iterator[TestClient]:
    add_run(empty_wh, "r1", "lightgbm", DAY, ISSUED, 1.0)
    publish(empty_wh, settings)
    # As a context manager the client keeps one event loop, which the gates need.
    with make_client(settings, heavy_wait=0.01, query_wait=0.01) as client:
        yield client


def hold(client: TestClient, name: str, count: int) -> None:
    gate = getattr(client.app.state, name)  # type: ignore[attr-defined]
    for _ in range(count):
        client.portal.call(gate.acquire)  # type: ignore[union-attr]


def free(client: TestClient, name: str, count: int) -> None:
    gate = getattr(client.app.state, name)  # type: ignore[attr-defined]
    for _ in range(count):
        client.portal.call(gate.release)  # type: ignore[union-attr]


def test_only_large_actuals_requests_need_a_heavy_slot(forecast_client: TestClient) -> None:
    assert len(CATALOG) == 15
    hold(forecast_client, "heavy_gate", 2)
    try:
        response = forecast_client.get("/api/v1/actuals", params=HEAVY)
        assert response.status_code == 503
        assert response.headers["content-type"] == "application/json"
        assert response.json() == BUSY
        assert "etag" not in response.headers
        # Smaller requests and forecasts do not queue behind the large ones.
        assert forecast_client.get("/api/v1/actuals", params=LIGHT).status_code == 200
        assert get(forecast_client, "/api/v1/forecasts")["delivery_day"] == "2026-11-12"
        assert get(forecast_client, "/api/v1/forecasts/days")["days"] == ["2026-11-12"]
        assert get(forecast_client, "/api/v1/status")["initialised"] is True
        assert get(forecast_client, "/api/v1/health")["status"] == "ok"
    finally:
        free(forecast_client, "heavy_gate", 2)
    assert forecast_client.get("/api/v1/actuals", params=HEAVY).status_code == 200


def test_data_queries_answer_503_while_all_query_slots_are_taken(
    forecast_client: TestClient,
) -> None:
    hold(forecast_client, "query_gate", 8)
    try:
        for url in ("/api/v1/forecasts", "/api/v1/forecasts/days", "/api/v1/backtest/metrics"):
            response = forecast_client.get(url)
            assert response.status_code == 503
            assert response.json() == BUSY
        assert get(forecast_client, "/api/v1/status")["initialised"] is True
        assert get(forecast_client, "/api/v1/health")["status"] == "ok"
    finally:
        free(forecast_client, "query_gate", 8)
    assert forecast_client.get("/api/v1/forecasts").status_code == 200


def test_health_and_status_answer_while_heavy_requests_wait(
    empty_wh: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    publish(empty_wh, settings)
    app = create_app(settings, heavy_wait=2.0)
    gate = app.state.heavy_gate

    async def scenario() -> tuple[list[int], list[tuple[int, float]], int, int]:
        await gate.acquire()
        await gate.acquire()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
            # More waiting heavy requests than the worker thread pool has threads.
            waiting = [
                asyncio.create_task(client.get("/api/v1/actuals", params=HEAVY)) for _ in range(60)
            ]
            await asyncio.sleep(0.2)
            # Requests beyond the waiting limit are answered at once.
            refused = sum(task.done() for task in waiting)
            probes = []
            for url in ("/api/v1/health", "/api/v1/status", "/api/v1/forecasts/days"):
                started = time.monotonic()
                response = await client.get(url)
                probes.append((response.status_code, time.monotonic() - started))
            pending = sum(not task.done() for task in waiting)
            codes = [r.status_code for r in await asyncio.gather(*waiting)]
            gate.release()
            gate.release()
            assert (await client.get("/api/v1/actuals", params=HEAVY)).status_code == 200
            return codes, probes, refused, pending

    codes, probes, refused, pending = asyncio.run(scenario())
    assert refused == 60 - HEAVY_MAX_WAITING
    assert pending == HEAVY_MAX_WAITING
    assert [code for code, _ in probes] == [200, 200, 200]
    assert max(seconds for _, seconds in probes) < 1.0
    assert codes == [503] * 60


def test_requests_beyond_the_inflight_limit_are_refused_except_health_and_status(
    empty_wh: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    add_run(empty_wh, "r1", "lightgbm", DAY, ISSUED, 1.0)
    publish(empty_wh, settings)
    app = create_app(settings, heavy_wait=2.0, max_inflight=5)
    gate = app.state.heavy_gate

    async def scenario() -> tuple[list[int], list[httpx.Response], int]:
        await gate.acquire()
        await gate.acquire()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
            waiting = [
                asyncio.create_task(client.get("/api/v1/actuals", params=HEAVY)) for _ in range(5)
            ]
            await asyncio.sleep(0.2)
            probes = [
                await client.get(url)
                for url in (
                    "/api/v1/health",
                    "/api/v1/status",
                    "/api/v1/forecasts",
                    "/api/v1/series",
                )
            ]
            pending = sum(not task.done() for task in waiting)
            gate.release()
            gate.release()
            codes = [r.status_code for r in await asyncio.gather(*waiting)]
            assert (await client.get("/api/v1/forecasts")).status_code == 200
            return codes, probes, pending

    codes, probes, pending = asyncio.run(scenario())
    assert pending == 5
    assert [r.status_code for r in probes] == [200, 200, 503, 503]
    assert probes[2].headers["content-type"] == "application/json"
    assert probes[2].json() == BUSY
    assert probes[3].json() == BUSY
    assert codes == [200] * 5


# Revalidation --------------------------------------------------------------------------


def test_data_responses_carry_an_etag_and_answer_304(forecast_client: TestClient) -> None:
    for url, params in (
        ("/api/v1/actuals", {"series": "load", "start": "2026-11-10", "end": "2026-11-10"}),
        ("/api/v1/forecasts", {}),
        ("/api/v1/forecasts/days", {"limit": 3}),
    ):
        first = forecast_client.get(url, params=params)
        assert first.status_code == 200
        assert first.headers["cache-control"] == "no-cache"
        tag = first.headers["etag"]
        assert tag.startswith('"')
        assert tag.endswith('"')
        for header in (tag, f"W/{tag}", f'"other", {tag}'):
            again = forecast_client.get(url, params=params, headers={"If-None-Match": header})
            assert again.status_code == 304
            assert again.content == b""
            assert again.headers["etag"] == tag
            assert again.headers["cache-control"] == "no-cache"
        for header in ('"other"', "*", ""):
            full = forecast_client.get(url, params=params, headers={"If-None-Match": header})
            assert full.status_code == 200
            assert full.content == first.content


def test_etag_depends_on_the_request_and_the_snapshot(
    empty_wh: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    add_run(empty_wh, "r1", "lightgbm", DAY, ISSUED, 1.0)
    publish(empty_wh, settings)
    client = make_client(settings)
    tag = client.get("/api/v1/forecasts/days").headers["etag"]
    assert client.get("/api/v1/forecasts/days").headers["etag"] == tag
    assert client.get("/api/v1/forecasts/days", params={"limit": 1}).headers["etag"] != tag

    add_run(empty_wh, "r2", "lightgbm", date(2026, 11, 13), ISSUED, 1.0)
    publish(empty_wh, settings)
    response = client.get("/api/v1/forecasts/days", headers={"If-None-Match": tag})
    assert response.status_code == 200
    assert response.json()["days"] == ["2026-11-13", "2026-11-12"]
    assert response.headers["etag"] != tag


def test_revalidation_needs_no_slot_and_errors_have_no_etag(
    forecast_client: TestClient,
) -> None:
    tag = forecast_client.get("/api/v1/actuals", params=HEAVY).headers["etag"]
    hold(forecast_client, "heavy_gate", 2)
    hold(forecast_client, "query_gate", 8)
    try:
        response = forecast_client.get(
            "/api/v1/actuals", params=HEAVY, headers={"If-None-Match": tag}
        )
        assert response.status_code == 304
    finally:
        free(forecast_client, "heavy_gate", 2)
        free(forecast_client, "query_gate", 8)
    for url, params in (
        ("/api/v1/forecasts", {"delivery_day": "2026-01-01"}),
        ("/api/v1/backtest/metrics", {}),
        ("/api/v1/actuals", {"series": "nope", "start": "2026-11-10", "end": "2026-11-10"}),
    ):
        assert "etag" not in forecast_client.get(url, params=params).headers
        response = forecast_client.get(url, params=params, headers={"If-None-Match": "*"})
        assert response.status_code in (404, 422)
        assert "etag" not in response.headers


# Status --------------------------------------------------------------------------------


def test_status_queries_the_snapshot_once_per_snapshot(
    empty_wh: duckdb.DuckDBPyConnection, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty_wh.execute(
        "INSERT INTO observations VALUES ('load', 'quarterhour', ?, 1.0, now(), 0)",
        [datetime(2026, 11, 12, 10, tzinfo=UTC)],
    )
    publish(empty_wh, settings)
    calls = []
    original = queries.last_timestamps

    def counting(cur: duckdb.DuckDBPyConnection) -> dict[str, datetime]:
        calls.append(1)
        return original(cur)

    monkeypatch.setattr(queries, "last_timestamps", counting)
    now = [datetime(2026, 11, 12, 11, tzinfo=UTC)]
    client = TestClient(create_app(settings, clock=lambda: now[0]))

    def load_age() -> float:
        body = get(client, "/api/v1/status")
        return float(next(s for s in body["series"] if s["key"] == "load")["age_seconds"])

    assert load_age() == 2700.0
    now[0] += timedelta(minutes=10)
    assert load_age() == 3300.0
    assert len(calls) == 1

    empty_wh.execute(
        "INSERT INTO observations VALUES ('load', 'quarterhour', ?, 1.0, now(), 0)",
        [datetime(2026, 11, 12, 10, 45, tzinfo=UTC)],
    )
    publish(empty_wh, settings)
    assert load_age() == 600.0
    assert len(calls) == 2


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
