"""Hourly values in the snapshot are exact, without floating-point noise."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from gridload.api.app import create_app
from gridload.config import Settings
from gridload.pipeline import snapshot
from gridload.pipeline.db import migrate

HOUR = datetime(2026, 11, 12, 10, tzinfo=UTC)
# Real quarter hours from SMARD: the hourly view of DuckDB gives 43648.21000000001
# and 97.83250000000001 for them instead of the exact 43648.21 and 97.8325.
LOAD = [10757.09, 10879.18, 11004.6, 11007.34]
PRICE = [106.06, 99.79, 95.48, 90.0]
# A mean of four values with two decimals can have four.
PRICE_FINE = [0.01, 0.0, 0.0, 0.0]


def _exact(values: list[float], *, mean: bool) -> float:
    total = sum(Decimal(str(v)) for v in values)
    return float(total / 4 if mean else total)


@pytest.fixture
def warehouse() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    migrate(con)
    loaded = datetime(2026, 11, 20, tzinfo=UTC)
    rows = []
    for key, start, values in (
        ("load", HOUR, LOAD),
        ("price_day_ahead", HOUR, PRICE),
        ("price_day_ahead", HOUR + timedelta(hours=1), PRICE_FINE),
    ):
        for i, value in enumerate(values):
            rows.append([key, "quarterhour", start + timedelta(minutes=15 * i), value, loaded, 0])
    con.executemany("INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?)", rows)
    return con


def _hourly(path: Path) -> list[tuple[str, datetime, float]]:
    with duckdb.connect(str(path), read_only=True) as con:
        con.execute("SET TimeZone = 'UTC'")
        return con.execute(
            "SELECT series_key, ts_utc, value FROM hourly ORDER BY series_key, ts_utc"
        ).fetchall()


def test_snapshot_rounds_hourly_values_to_their_exact_decimals(
    warehouse: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    published = _hourly(snapshot.publish(warehouse, tmp_path / "serving.duckdb"))
    assert published == [
        ("load", HOUR, _exact(LOAD, mean=False)),
        ("price_day_ahead", HOUR, _exact(PRICE, mean=True)),
        ("price_day_ahead", HOUR + timedelta(hours=1), 0.0025),
    ]
    assert published[0][2] == 43648.21
    assert published[1][2] == 97.8325


def test_the_warehouse_view_used_by_the_forecast_is_unchanged(
    warehouse: duckdb.DuckDBPyConnection,
) -> None:
    """Model inputs keep the plain floating-point sum, so backtests stay reproducible."""
    row = warehouse.execute(
        "SELECT value FROM hourly WHERE series_key = 'load' AND ts_utc = ?", [HOUR]
    ).fetchone()
    assert row is not None
    assert row[0] != _exact(LOAD, mean=False)
    assert row[0] == pytest.approx(_exact(LOAD, mean=False), abs=1e-9)


def test_two_snapshots_of_the_same_data_are_identical(
    warehouse: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    first = _hourly(snapshot.publish(warehouse, tmp_path / "a.duckdb"))
    second = _hourly(snapshot.publish(warehouse, tmp_path / "b.duckdb"))
    assert first == second


def test_api_emits_the_exact_hourly_values(
    warehouse: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    settings = Settings(data_dir=tmp_path)
    snapshot.publish(warehouse, settings.serving_path)
    response = TestClient(create_app(settings)).get(
        "/api/v1/actuals",
        params={"series": "load,price_day_ahead", "start": "2026-11-12", "end": "2026-11-12"},
    )
    assert response.status_code == 200
    assert '"value":43648.21}' in response.text
    assert '"value":97.8325}' in response.text
    assert '"value":0.0025}' in response.text
    values = {s["key"]: [p["value"] for p in s["points"]] for s in response.json()["series"]}
    assert values == {"load": [43648.21], "price_day_ahead": [97.8325, 0.0025]}
