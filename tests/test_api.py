"""Endpoint tests against real snapshots built from the recorded SMARD fixtures."""

import json
import os
import runpy
import shutil
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import pytest
from fastapi.testclient import TestClient

from gridload.api.app import create_app
from gridload.config import Settings
from gridload.pipeline import snapshot
from gridload.pipeline.db import connect, migrate
from gridload.pipeline.ingest import Ingestor
from gridload.series import BY_KEY, CATALOG
from tests.fake_smard import FIXTURE_NOW, FakeSmard, fixture_clock, recorded_chunks

ROOT = Path(__file__).resolve().parent.parent
BERLIN = ZoneInfo("Europe/Berlin")
NOW = FIXTURE_NOW
BASE_SERIES = [BY_KEY[k] for k in ("load", "load_forecast_tso", "gen_solar", "price_day_ahead")]
SPRING_DAY = date(2025, 3, 30)
AUTUMN_DAY = date(2025, 10, 26)
REGULAR_DAY = date(2026, 11, 12)


# Fixtures and helpers -----------------------------------------------------------------


@pytest.fixture(scope="module")
def base_warehouse(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Warehouse with four series ingested from fixtures, built once per module."""
    path = tmp_path_factory.mktemp("base") / "warehouse.duckdb"
    fake = FakeSmard()
    for series in BASE_SERIES:
        fake.use_index(series, recorded_chunks(series))
    with connect(path) as con:
        result = Ingestor(con, fake.client(), clock=fixture_clock).run(BASE_SERIES, mode="backfill")
        assert result.status == "ok"
    return path


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return Settings(data_dir=data_dir)


@pytest.fixture
def wh(tmp_path: Path, base_warehouse: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    """Writable copy of the base warehouse for one test."""
    path = tmp_path / "warehouse.duckdb"
    shutil.copy(base_warehouse, path)
    with connect(path) as con:
        yield con


@pytest.fixture
def empty_wh() -> Iterator[duckdb.DuckDBPyConnection]:
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    migrate(con)
    yield con
    con.close()


def make_client(settings: Settings, **kwargs: Any) -> TestClient:
    return TestClient(create_app(settings, clock=lambda: NOW, **kwargs))


def publish(con: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    snapshot.publish(con, settings.serving_path)


def local_hours(day: date) -> list[datetime]:
    """UTC start of every local hour of ``day``, computed independently of the API code."""
    start = datetime(day.year, day.month, day.day, tzinfo=BERLIN).astimezone(UTC)
    nxt = day + timedelta(days=1)
    end = datetime(nxt.year, nxt.month, nxt.day, tzinfo=BERLIN).astimezone(UTC)
    return [start + timedelta(hours=h) for h in range(int((end - start).total_seconds()) // 3600)]


def add_run(
    con: duckdb.DuckDBPyConnection,
    run_id: str,
    model: str,
    day: date,
    issued_at: datetime,
    value: float,
    *,
    created_at: datetime | None = None,
    extra: dict[datetime, float] | None = None,
) -> None:
    """Insert a forecast run with ``value`` for every hour of ``day`` plus ``extra`` rows."""
    con.execute(
        "INSERT INTO forecast_runs VALUES (?, ?, ?, ?, ?, ?)",
        [run_id, model, issued_at, day, created_at or issued_at, json.dumps({"x": 1})],
    )
    rows = {ts: value + i for i, ts in enumerate(local_hours(day))}
    rows.update(extra or {})
    con.executemany(
        "INSERT INTO forecasts VALUES (?, ?, ?)", [[run_id, ts, v] for ts, v in rows.items()]
    )


def hourly_value(con: duckdb.DuckDBPyConnection, key: str, ts: datetime) -> float:
    row = con.execute(
        "SELECT value FROM hourly WHERE series_key = ? AND ts_utc = ?", [key, ts]
    ).fetchone()
    assert row is not None
    return float(row[0])


def get(client: TestClient, url: str, params: dict[str, Any] | None = None) -> Any:
    response = client.get(url, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def error_message(client: TestClient, url: str, params: dict[str, Any]) -> str:
    response = client.get(url, params=params)
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert isinstance(detail, list)
    return str(detail[0]["msg"])


@pytest.fixture
def client(wh: duckdb.DuckDBPyConnection, settings: Settings) -> TestClient:
    publish(wh, settings)
    return make_client(settings)


# Catalog ------------------------------------------------------------------------------


def test_series_lists_the_catalog_without_a_snapshot(settings: Settings) -> None:
    body = get(make_client(settings), "/api/v1/series")
    assert [s["key"] for s in body["series"]] == [s.key for s in CATALOG]
    assert body["series"][0] == {
        "key": "load",
        "label": "Grid load",
        "unit": "MWh",
        "category": "load",
    }


def test_prices_are_hidden_when_disabled(wh: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    publish(wh, settings)
    client = make_client(Settings(data_dir=settings.data_dir, include_prices=False))
    keys = [s["key"] for s in get(client, "/api/v1/series")["series"]]
    assert "price_day_ahead" not in keys
    assert "load" in keys
    message = error_message(
        client,
        "/api/v1/actuals",
        {"series": "price_day_ahead", "start": "2026-11-10", "end": "2026-11-10"},
    )
    assert "unknown series: price_day_ahead" in message
    status_keys = [s["key"] for s in get(client, "/api/v1/status")["series"]]
    assert "price_day_ahead" not in status_keys


# Actuals ------------------------------------------------------------------------------


def test_hourly_actuals_on_the_23_hour_day(client: TestClient) -> None:
    body = get(
        client, "/api/v1/actuals", {"series": "load", "start": "2025-03-30", "end": "2025-03-30"}
    )
    assert body["resolution"] == "hour"
    assert (body["start"], body["end"]) == ("2025-03-30", "2025-03-30")
    load = body["series"][0]
    assert (load["key"], load["unit"], load["label"]) == ("load", "MWh", "Grid load")
    stamps = [p["ts"] for p in load["points"]]
    assert len(stamps) == 23
    assert stamps[0] == "2025-03-30T00:00:00+01:00"
    assert stamps[1] == "2025-03-30T01:00:00+01:00"
    assert stamps[2] == "2025-03-30T03:00:00+02:00"
    assert stamps[-1] == "2025-03-30T23:00:00+02:00"
    assert not any(s.startswith("2025-03-30T02:") for s in stamps)


def test_hourly_actuals_on_the_25_hour_day(
    client: TestClient, wh: duckdb.DuckDBPyConnection
) -> None:
    body = get(
        client, "/api/v1/actuals", {"series": "load", "start": "2025-10-26", "end": "2025-10-26"}
    )
    points = body["series"][0]["points"]
    stamps = [p["ts"] for p in points]
    assert len(stamps) == 25
    assert stamps[:5] == [
        "2025-10-26T00:00:00+02:00",
        "2025-10-26T01:00:00+02:00",
        "2025-10-26T02:00:00+02:00",
        "2025-10-26T02:00:00+01:00",
        "2025-10-26T03:00:00+01:00",
    ]
    assert stamps[-1] == "2025-10-26T23:00:00+01:00"
    # The two 02:00 hours are distinct instants with their own values.
    first, second = local_hours(AUTUMN_DAY)[2:4]
    assert points[2]["value"] == pytest.approx(hourly_value(wh, "load", first))
    assert points[3]["value"] == pytest.approx(hourly_value(wh, "load", second))


def test_quarterhour_actuals_on_the_25_hour_day(client: TestClient) -> None:
    body = get(
        client,
        "/api/v1/actuals",
        {"series": "load", "start": "2025-10-26", "end": "2025-10-26", "resolution": "quarterhour"},
    )
    stamps = [p["ts"] for p in body["series"][0]["points"]]
    assert len(stamps) == 100
    assert stamps[8:13] == [
        "2025-10-26T02:00:00+02:00",
        "2025-10-26T02:15:00+02:00",
        "2025-10-26T02:30:00+02:00",
        "2025-10-26T02:45:00+02:00",
        "2025-10-26T02:00:00+01:00",
    ]


def test_quarterhour_values_sum_to_the_hourly_values(client: TestClient) -> None:
    params = {"series": "load", "start": "2026-11-10", "end": "2026-11-10"}
    hourly = get(client, "/api/v1/actuals", params)["series"][0]["points"]
    quarter = get(client, "/api/v1/actuals", params | {"resolution": "quarterhour"})["series"][0][
        "points"
    ]
    assert len(hourly) == 24
    assert len(quarter) == 96
    for i, hour in enumerate(hourly):
        assert hour["value"] == pytest.approx(sum(q["value"] for q in quarter[4 * i : 4 * i + 4]))


def test_actuals_keep_request_order_drop_repeats_and_report_units(client: TestClient) -> None:
    body = get(
        client,
        "/api/v1/actuals",
        {
            "series": " price_day_ahead,load,price_day_ahead ",
            "start": "2026-11-10",
            "end": "2026-11-11",
        },
    )
    assert [(s["key"], s["unit"]) for s in body["series"]] == [
        ("price_day_ahead", "EUR/MWh"),
        ("load", "MWh"),
    ]
    assert len(body["series"][1]["points"]) == 48


def test_series_without_data_in_range_has_no_points(client: TestClient) -> None:
    body = get(
        client,
        "/api/v1/actuals",
        {"series": "load,gen_solar,gen_gas", "start": "2025-03-30", "end": "2025-03-30"},
    )
    assert [len(s["points"]) for s in body["series"]] == [23, 0, 0]


def test_incomplete_last_hour_is_missing_from_hourly_but_not_quarterhour(
    wh: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    # The newest load value is 2026-11-20 09:45 UTC (10:45 local). Without it, the
    # local hour 10:00 has only three quarter hours and is not reported hourly.
    wh.execute(
        "DELETE FROM observations WHERE series_key = 'load' AND ts_utc = ?",
        [datetime(2026, 11, 20, 9, 45, tzinfo=UTC)],
    )
    publish(wh, settings)
    client = make_client(settings)
    params = {"series": "load", "start": "2026-11-20", "end": "2026-11-20"}
    hourly = get(client, "/api/v1/actuals", params)["series"][0]["points"]
    quarter = get(client, "/api/v1/actuals", params | {"resolution": "quarterhour"})["series"][0][
        "points"
    ]
    assert hourly[-1]["ts"] == "2026-11-20T09:00:00+01:00"
    assert len(hourly) == 10
    assert quarter[-1]["ts"] == "2026-11-20T10:30:00+01:00"
    assert len(quarter) == 43


@pytest.mark.parametrize(
    ("resolution", "start", "end"),
    [("hour", "2025-01-01", "2026-01-01"), ("quarterhour", "2026-10-21", "2026-11-20")],
)
def test_longest_allowed_range_is_accepted(
    client: TestClient, resolution: str, start: str, end: str
) -> None:
    body = get(
        client,
        "/api/v1/actuals",
        {"series": "load", "start": start, "end": end, "resolution": resolution},
    )
    assert body["series"][0]["points"]


@pytest.mark.parametrize(
    ("params", "fragment"),
    [
        ({"start": "2025-01-01", "end": "2026-01-02"}, "at most 366 days at resolution hour"),
        (
            {"start": "2026-10-20", "end": "2026-11-20", "resolution": "quarterhour"},
            "at most 31 days at resolution quarterhour",
        ),
        ({"start": "2025-03-30", "end": "2025-03-29"}, "end must not be before start"),
        ({"start": "2025-02-30", "end": "2025-03-01"}, "invalid date"),
        ({"start": "2025-03-01", "end": "2025-13-01"}, "invalid date"),
        ({"start": "0001-01-01", "end": "0001-01-01"}, "between 2000-01-01 and 2099-12-31"),
        ({"start": "9999-12-31", "end": "9999-12-31"}, "between 2000-01-01 and 2099-12-31"),
        ({"start": "2025-03-30", "end": "9999-12-31"}, "between 2000-01-01 and 2099-12-31"),
        ({"start": "2025-3-30", "end": "2025-03-30"}, "String should match pattern"),
        ({"start": "20250330", "end": "2025-03-30"}, "String should match pattern"),
        ({"start": "2025-03-30T00:00", "end": "2025-03-30"}, "String should match pattern"),
        ({"start": "2025-03-30", "end": "2025-03-30", "resolution": "day"}, "Input should be"),
        ({"series": "nope", "start": "2025-03-30", "end": "2025-03-30"}, "unknown series: nope"),
        (
            {"series": "load';DROP TABLE hourly;--", "start": "2025-03-30", "end": "2025-03-30"},
            "unknown series",
        ),
        ({"series": "load,", "start": "2025-03-30", "end": "2025-03-30"}, "empty series key"),
        ({"series": "", "start": "2025-03-30", "end": "2025-03-30"}, "empty series key"),
        (
            {"series": ",".join(["load"] * 21), "start": "2025-03-30", "end": "2025-03-30"},
            "at most 20 series",
        ),
        (
            {"series": "x" * 1001, "start": "2025-03-30", "end": "2025-03-30"},
            "at most 1000 characters",
        ),
    ],
)
def test_invalid_actuals_requests_are_rejected(
    client: TestClient, params: dict[str, str], fragment: str
) -> None:
    query = {"series": "load"} | params
    assert fragment in error_message(client, "/api/v1/actuals", query)


@pytest.mark.parametrize("missing", ["series", "start", "end"])
def test_required_actuals_parameters(client: TestClient, missing: str) -> None:
    query = {"series": "load", "start": "2025-03-30", "end": "2025-03-30"}
    del query[missing]
    response = client.get("/api/v1/actuals", params=query)
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", missing]


def test_twenty_series_are_allowed(client: TestClient) -> None:
    keys = [s.key for s in CATALOG] + ["load"] * 5
    assert len(keys) == 20
    body = get(
        client,
        "/api/v1/actuals",
        {"series": ",".join(keys), "start": "2026-11-10", "end": "2026-11-10"},
    )
    assert len(body["series"]) == len(CATALOG)


# Forecasts ----------------------------------------------------------------------------


def issued(day: date) -> datetime:
    """Forecast origin: 09:00 UTC on the day before delivery."""
    return datetime(day.year, day.month, day.day, 9, tzinfo=UTC) - timedelta(days=1)


def test_forecast_on_the_25_hour_day_uses_the_latest_run(
    wh: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    hours = local_hours(AUTUMN_DAY)
    outside = {hours[0] - timedelta(hours=1): -1.0, hours[-1] + timedelta(hours=1): -1.0}
    add_run(wh, "old", "lightgbm", AUTUMN_DAY, issued(AUTUMN_DAY), 1000.0, extra=outside)
    add_run(wh, "new", "lightgbm", AUTUMN_DAY, issued(AUTUMN_DAY) + timedelta(hours=2), 2000.0)
    add_run(wh, "base", "baseline_week", AUTUMN_DAY, issued(AUTUMN_DAY), 3000.0, extra=outside)
    add_run(wh, "alien", "other_model", AUTUMN_DAY, issued(AUTUMN_DAY), 9.0)
    publish(wh, settings)

    body = get(make_client(settings), "/api/v1/forecasts", {"delivery_day": "2025-10-26"})
    assert body["delivery_day"] == "2025-10-26"
    assert body["unit"] == "MWh"
    assert set(body["runs"]) == {"lightgbm", "baseline_week"}
    assert body["runs"]["lightgbm"]["run_id"] == "new"
    assert body["runs"]["lightgbm"]["issued_at"] == "2025-10-25T13:00:00+02:00"
    assert body["runs"]["baseline_week"]["issued_at"] == "2025-10-25T11:00:00+02:00"
    points = body["points"]
    assert len(points) == 25
    assert points[2]["ts"] == "2025-10-26T02:00:00+02:00"
    assert points[3]["ts"] == "2025-10-26T02:00:00+01:00"
    assert [p["lightgbm"] for p in points] == [2000.0 + i for i in range(25)]
    assert [p["baseline_week"] for p in points] == [3000.0 + i for i in range(25)]
    # SMARD's forecast series is only recorded for November 2026.
    assert all(p["tso"] is None for p in points)
    assert points[3]["actual"] == pytest.approx(hourly_value(wh, "load", hours[3]))
    assert all(p["actual"] is not None for p in points)


def test_forecast_on_the_23_hour_day(wh: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    add_run(wh, "spring", "lightgbm", SPRING_DAY, issued(SPRING_DAY), 500.0)
    publish(wh, settings)
    body = get(make_client(settings), "/api/v1/forecasts", {"delivery_day": "2025-03-30"})
    stamps = [p["ts"] for p in body["points"]]
    assert len(stamps) == 23
    assert stamps[1:3] == ["2025-03-30T01:00:00+01:00", "2025-03-30T03:00:00+02:00"]
    assert body["points"][2]["lightgbm"] == 502.0
    assert all(p["baseline_week"] is None for p in body["points"])
    assert set(body["runs"]) == {"lightgbm"}


def test_tie_on_issue_time_is_broken_by_creation_time(
    wh: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    t = issued(REGULAR_DAY)
    add_run(wh, "b", "lightgbm", REGULAR_DAY, t, 1.0, created_at=t + timedelta(minutes=5))
    add_run(wh, "a", "lightgbm", REGULAR_DAY, t, 2.0, created_at=t + timedelta(minutes=9))
    publish(wh, settings)
    body = get(make_client(settings), "/api/v1/forecasts", {"delivery_day": "2026-11-12"})
    assert body["runs"]["lightgbm"]["run_id"] == "a"
    assert body["points"][0]["lightgbm"] == 2.0


def test_default_day_is_the_latest_lightgbm_day(
    wh: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    add_run(wh, "older", "lightgbm", REGULAR_DAY, issued(REGULAR_DAY), 1.0)
    later = date(2026, 11, 15)
    add_run(wh, "latest", "lightgbm", later, issued(later), 1.0)
    baseline_only = date(2026, 11, 21)
    add_run(wh, "base", "baseline_week", baseline_only, issued(baseline_only), 7.0)
    publish(wh, settings)
    client = make_client(settings)

    body = get(client, "/api/v1/forecasts")
    assert body["delivery_day"] == "2026-11-15"
    hours = local_hours(later)
    assert len(body["points"]) == 24
    assert body["points"][0]["ts"] == "2026-11-15T00:00:00+01:00"
    assert body["points"][5]["tso"] == pytest.approx(
        hourly_value(wh, "load_forecast_tso", hours[5])
    )
    assert body["points"][5]["actual"] == pytest.approx(hourly_value(wh, "load", hours[5]))

    # A day with only the baseline still answers, without lightgbm values.
    body = get(client, "/api/v1/forecasts", {"delivery_day": "2026-11-21"})
    assert set(body["runs"]) == {"baseline_week"}
    assert all(p["lightgbm"] is None for p in body["points"])
    assert body["points"][0]["baseline_week"] == 7.0
    # The SMARD forecast reaches into the future; actual load does not.
    assert body["points"][0]["tso"] is not None
    assert all(p["actual"] is None for p in body["points"])


def test_missing_forecast_day_is_404(wh: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    add_run(wh, "x", "lightgbm", REGULAR_DAY, issued(REGULAR_DAY), 1.0)
    add_run(wh, "y", "other_model", AUTUMN_DAY, issued(AUTUMN_DAY), 1.0)
    publish(wh, settings)
    client = make_client(settings)
    for day in ("2026-11-13", "2025-10-26"):
        response = client.get("/api/v1/forecasts", params={"delivery_day": day})
        assert response.status_code == 404
        assert response.json() == {"detail": f"no forecast exists for delivery day {day}"}


def test_no_forecast_at_all_is_404(empty_wh: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    publish(empty_wh, settings)
    response = make_client(settings).get("/api/v1/forecasts")
    assert response.status_code == 404
    assert response.json() == {"detail": "no forecast is available yet"}


@pytest.mark.parametrize(
    ("value", "fragment"),
    [
        ("2025-02-29", "invalid date"),
        ("26.10.2025", "String should match pattern"),
        ("1999-12-31", "between 2000-01-01 and 2099-12-31"),
        ("9999-12-31", "between 2000-01-01 and 2099-12-31"),
        ("", "String should match pattern"),
    ],
)
def test_invalid_delivery_day_is_rejected(client: TestClient, value: str, fragment: str) -> None:
    assert fragment in error_message(client, "/api/v1/forecasts", {"delivery_day": value})


def test_forecast_days_are_distinct_newest_first_and_limited(
    wh: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    for i, day in enumerate([date(2026, 11, d) for d in (10, 12, 11)]):
        add_run(wh, f"l{i}", "lightgbm", day, issued(day), 1.0)
        add_run(wh, f"b{i}", "baseline_week", day, issued(day), 1.0)
    add_run(wh, "late", "lightgbm", REGULAR_DAY, issued(REGULAR_DAY) + timedelta(hours=1), 1.0)
    add_run(wh, "base", "baseline_week", date(2026, 11, 13), issued(date(2026, 11, 13)), 1.0)
    add_run(wh, "other", "other_model", date(2026, 11, 14), issued(date(2026, 11, 14)), 1.0)
    publish(wh, settings)
    client = make_client(settings)
    body = get(client, "/api/v1/forecasts/days")
    assert body == {
        "days": ["2026-11-13", "2026-11-12", "2026-11-11", "2026-11-10"],
        "limit": 400,
    }
    assert get(client, "/api/v1/forecasts/days", {"limit": 2})["days"] == [
        "2026-11-13",
        "2026-11-12",
    ]
    for bad in ("0", "5001", "-1", "abc"):
        assert client.get("/api/v1/forecasts/days", params={"limit": bad}).status_code == 422


def test_forecast_days_empty(empty_wh: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    publish(empty_wh, settings)
    assert get(make_client(settings), "/api/v1/forecasts/days") == {"days": [], "limit": 400}


# Backtest -----------------------------------------------------------------------------


def add_backtest(
    con: duckdb.DuckDBPyConnection, run_id: str, created_at: datetime, models: list[str]
) -> None:
    con.execute(
        "INSERT INTO backtest_runs VALUES (?, ?, ?, ?, ?)",
        [run_id, created_at, date(2025, 1, 1), date(2025, 12, 31), json.dumps({"folds": 12})],
    )
    rows: list[list[object]] = []
    for model in models:
        rows.append([run_id, model, "overall", "all", 1000.0, 2.5, 8760])
        rows.extend([run_id, model, "hour", str(h), 900.0 + h, 2.0, 365] for h in range(24))
        rows.extend(
            [run_id, model, "day_type", t, 950.0, 2.2, 100]
            for t in ("workday", "saturday", "sunday_holiday")
        )
    con.executemany("INSERT INTO backtest_metrics VALUES (?, ?, ?, ?, ?, ?, ?)", rows)


def test_backtest_metrics_of_the_latest_run(
    empty_wh: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    add_backtest(empty_wh, "old", datetime(2026, 1, 1, tzinfo=UTC), ["lightgbm"])
    add_backtest(
        empty_wh,
        "new",
        datetime(2026, 7, 1, 10, tzinfo=UTC),
        ["tso", "lightgbm", "baseline_week"],
    )
    publish(empty_wh, settings)
    body = get(make_client(settings), "/api/v1/backtest/metrics")
    assert body["run_id"] == "new"
    assert body["created_at"] == "2026-07-01T12:00:00+02:00"
    assert (body["first_day"], body["last_day"]) == ("2025-01-01", "2025-12-31")
    assert body["config"] == {"folds": 12}
    metrics = body["metrics"]
    assert list(metrics) == ["baseline_week", "lightgbm", "tso"]
    lightgbm = metrics["lightgbm"]
    assert set(lightgbm) == {"overall", "hour", "day_type"}
    assert lightgbm["overall"] == [{"scope_value": "all", "mae": 1000.0, "mape": 2.5, "n": 8760}]
    assert [m["scope_value"] for m in lightgbm["hour"]] == [str(h) for h in range(24)]
    assert lightgbm["hour"][23]["mae"] == 923.0
    assert [m["scope_value"] for m in lightgbm["day_type"]] == [
        "saturday",
        "sunday_holiday",
        "workday",
    ]


def test_no_backtest_is_404(empty_wh: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    publish(empty_wh, settings)
    response = make_client(settings).get("/api/v1/backtest/metrics")
    assert response.status_code == 404
    assert response.json() == {"detail": "no backtest run is available yet"}


# Status -------------------------------------------------------------------------------


def test_status_reports_freshness_runs_and_licence(
    wh: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    add_run(wh, "f1", "lightgbm", REGULAR_DAY, issued(REGULAR_DAY), 1.0)
    later = date(2026, 11, 21)
    add_run(wh, "f2", "lightgbm", later, issued(later), 1.0)
    add_run(wh, "f3", "baseline_week", date(2026, 11, 22), issued(later), 1.0)
    publish(wh, settings)
    # Pin the file time to a summer instant so the offset does not depend on today.
    mtime = datetime(2026, 7, 1, 10, 30, 15, 250000, tzinfo=UTC)
    stamp_ns = int(mtime.timestamp()) * 10**9 + 250_000_000
    os.utime(settings.serving_path, ns=(stamp_ns, stamp_ns))
    body = get(make_client(settings), "/api/v1/status")

    assert body["initialised"] is True
    assert body["generated_at"] == "2026-11-20T13:00:00+01:00"
    assert body["schema_version"] == 1
    assert body["snapshot_created_at"] == "2026-07-01T12:30:15.250000+02:00"

    series = {s["key"]: s for s in body["series"]}
    assert list(series) == [s.key for s in CATALOG]
    assert series["load"]["last_ts"] == "2026-11-20T10:45:00+01:00"
    assert series["load"]["age_seconds"] == pytest.approx(2 * 3600)
    # The SMARD forecast extends into the future, so its age is negative.
    assert series["load_forecast_tso"]["last_ts"] == "2026-11-21T23:45:00+01:00"
    assert series["load_forecast_tso"]["age_seconds"] < 0
    assert series["gen_gas"] == {
        "key": "gen_gas",
        "label": "Natural gas",
        "last_ts": None,
        "age_seconds": None,
    }

    run = body["last_load_run"]
    assert (run["mode"], run["status"]) == ("backfill", "ok")
    assert run["finished_at"] is not None
    assert body["last_forecast"] == {
        "model": "lightgbm",
        "delivery_day": "2026-11-21",
        "issued_at": "2026-11-20T10:00:00+01:00",
    }
    assert body["attribution"] == "Bundesnetzagentur | SMARD.de"
    assert body["licence"] == "CC BY 4.0"
    assert body["licence_url"] == "https://creativecommons.org/licenses/by/4.0/"
    assert "HEDWIG" in body["price_note"]


def test_status_with_empty_tables_and_a_running_load(
    empty_wh: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    publish(empty_wh, settings)
    client = make_client(settings)
    body = get(client, "/api/v1/status")
    assert body["initialised"] is True
    assert body["last_load_run"] is None
    assert body["last_forecast"] is None
    assert all(s["last_ts"] is None for s in body["series"])

    empty_wh.execute(
        "INSERT INTO load_runs VALUES ('r1', 'incremental', ?, NULL, 'running')",
        [datetime(2026, 11, 20, 11, tzinfo=UTC)],
    )
    publish(empty_wh, settings)
    run = get(client, "/api/v1/status")["last_load_run"]
    assert run == {
        "run_id": "r1",
        "mode": "incremental",
        "status": "running",
        "started_at": "2026-11-20T12:00:00+01:00",
        "finished_at": None,
    }


def test_actuals_on_empty_snapshot(empty_wh: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    publish(empty_wh, settings)
    body = get(
        make_client(settings),
        "/api/v1/actuals",
        {"series": "load", "start": "2026-11-10", "end": "2026-11-10"},
    )
    assert body["series"][0]["points"] == []


# OpenAPI ------------------------------------------------------------------------------


def test_openapi_documents_every_endpoint_and_error_model(client: TestClient) -> None:
    spec = get(client, "/api/openapi.json")
    paths = spec["paths"]
    assert set(paths) == {
        "/api/v1/health",
        "/api/v1/series",
        "/api/v1/actuals",
        "/api/v1/forecasts",
        "/api/v1/forecasts/days",
        "/api/v1/backtest/metrics",
        "/api/v1/status",
    }
    assert "503" in paths["/api/v1/actuals"]["get"]["responses"]
    assert "404" in paths["/api/v1/forecasts"]["get"]["responses"]
    assert "ErrorResponse" in spec["components"]["schemas"]


def test_committed_openapi_document_is_current(tmp_path: Path) -> None:
    module = runpy.run_path(str(ROOT / "scripts" / "export_openapi.py"))
    target = tmp_path / "openapi.json"
    assert module["main"](["export_openapi.py", str(target)]) == 0
    assert target.read_text(encoding="utf-8") == (ROOT / "openapi.json").read_text(encoding="utf-8")
