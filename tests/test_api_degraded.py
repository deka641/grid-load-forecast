"""The degraded assessment of forecast runs, from the forecast service to the API."""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest
from fastapi.testclient import TestClient

from gridload import timezones as tz
from gridload.api.app import create_app
from gridload.api.queries import assessment
from gridload.config import Settings
from gridload.forecast import service
from gridload.pipeline import snapshot
from gridload.pipeline.db import migrate
from tests import synthetic

DAY = date(2025, 4, 30)


def _client(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> TestClient:
    settings = Settings(data_dir=tmp_path)
    snapshot.publish(con, settings.serving_path)
    return TestClient(create_app(settings))


def _stored_details(con: duckdb.DuckDBPyConnection, model: str) -> dict[str, Any]:
    row = con.execute("SELECT details FROM forecast_runs WHERE model = ?", [model]).fetchone()
    assert row is not None
    details: dict[str, Any] = json.loads(row[0])
    return details


def test_degraded_forecast_reaches_forecasts_and_status(tmp_path: Path) -> None:
    con = synthetic.warehouse(date(2025, 1, 1), DAY)
    # Remove the load of the day seven days before delivery, so lag_7d is missing.
    start = tz.local_midnight(DAY - timedelta(days=7))
    con.execute(
        "DELETE FROM observations WHERE series_key = 'load' AND ts_utc >= ? AND ts_utc < ?",
        [start, start + timedelta(days=1)],
    )
    assert service.issue_forecast(con, DAY, degraded=True) is not None
    stored = _stored_details(con, "lightgbm")
    assert stored["degraded"] is True
    assert {"cutoff_hour_missing", "lag_7d"} <= set(stored["degraded_checks"])

    client = _client(con, tmp_path)
    runs = client.get("/api/v1/forecasts", params={"delivery_day": DAY.isoformat()}).json()["runs"]
    assert runs["lightgbm"]["degraded"] is True
    assert runs["lightgbm"]["degraded_checks"] == stored["degraded_checks"]
    # The baseline run is not assessed.
    assert runs["baseline_week"]["degraded"] is None
    assert runs["baseline_week"]["degraded_checks"] is None

    last = client.get("/api/v1/status").json()["last_forecast"]
    assert last["delivery_day"] == DAY.isoformat()
    assert last["degraded"] is True
    assert last["degraded_checks"] == stored["degraded_checks"]


def test_complete_forecast_is_not_degraded(tmp_path: Path) -> None:
    con = synthetic.warehouse(date(2025, 1, 1), DAY)
    assert service.issue_forecast(con, DAY) is not None
    client = _client(con, tmp_path)
    run = client.get("/api/v1/forecasts", params={"delivery_day": DAY.isoformat()}).json()["runs"]
    assert run["lightgbm"]["degraded"] is False
    assert run["lightgbm"]["degraded_checks"] == []
    last = client.get("/api/v1/status").json()["last_forecast"]
    assert (last["degraded"], last["degraded_checks"]) == (False, [])


def test_run_without_assessment_is_null(tmp_path: Path) -> None:
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    migrate(con)
    issued = datetime(2025, 4, 29, 10, tzinfo=UTC)
    con.execute(
        "INSERT INTO forecast_runs VALUES ('old', 'lightgbm', ?, ?, ?, ?)",
        [issued, DAY, issued, json.dumps({"cutoff": "2025-04-29T08:00:00+00:00"})],
    )
    con.execute("INSERT INTO forecasts VALUES ('old', ?, 1.0)", [tz.local_midnight(DAY)])
    client = _client(con, tmp_path)
    run = client.get("/api/v1/forecasts").json()["runs"]["lightgbm"]
    assert (run["degraded"], run["degraded_checks"]) == (None, None)
    last = client.get("/api/v1/status").json()["last_forecast"]
    assert (last["degraded"], last["degraded_checks"]) == (None, None)


def test_status_reports_the_run_that_forecasts_shows(tmp_path: Path) -> None:
    """Two runs with the same issue time: /status and /forecasts pick the same one."""
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    migrate(con)
    issued = datetime(2025, 4, 29, 10, tzinfo=UTC)
    for run_id, created, degraded in (("a", 1, True), ("b", 2, False)):
        con.execute(
            "INSERT INTO forecast_runs VALUES (?, 'lightgbm', ?, ?, ?, ?)",
            [
                run_id,
                issued,
                DAY,
                issued + timedelta(minutes=created),
                json.dumps(
                    {"degraded": degraded, "degraded_checks": ["lag_1d"] if degraded else []}
                ),
            ],
        )
    client = _client(con, tmp_path)
    run = client.get("/api/v1/forecasts").json()["runs"]["lightgbm"]
    assert (run["run_id"], run["degraded"]) == ("b", False)
    assert client.get("/api/v1/status").json()["last_forecast"]["degraded"] is False


@pytest.mark.parametrize(
    ("details", "expected"),
    [
        (None, (None, None)),
        ("not json", (None, None)),
        ("[1, 2]", (None, None)),
        ("null", (None, None)),
        ("{}", (None, None)),
        ('{"cutoff": "x"}', (None, None)),
        ('{"degraded": true, "degraded_checks": ["lag_1d"]}', (True, ["lag_1d"])),
        ('{"degraded": false, "degraded_checks": []}', (False, [])),
        # A value of the wrong type is reported as not assessed, not guessed.
        ('{"degraded": "yes", "degraded_checks": "lag_1d"}', (None, None)),
        ('{"degraded": 1, "degraded_checks": [1, "lag_1d"]}', (None, None)),
        ('{"degraded": true}', (True, None)),
    ],
)
def test_assessment_of_stored_details(
    details: str | None, expected: tuple[bool | None, list[str] | None]
) -> None:
    assert assessment(details) == expected
