"""Atomic read-only snapshot of the warehouse for the API.

DuckDB allows either one writing process or several reading processes on a
file, not both at once. The pipeline therefore writes ``warehouse.duckdb`` and
publishes ``serving.duckdb`` by writing a temporary file and renaming it, so the
API never sees a partially written database.
"""

import os
from pathlib import Path

import duckdb

SERVING_TABLES = (
    "schema_version",
    "series",
    "load_runs",
    "forecast_runs",
    "forecasts",
    "backtest_runs",
    "backtest_metrics",
)


def publish(con: duckdb.DuckDBPyConnection, target: Path) -> Path:
    """Copy the serving tables to ``target`` atomically and return its path."""
    tmp = target.with_name(target.name + ".tmp")
    tmp.unlink(missing_ok=True)
    escaped = str(tmp).replace("'", "''")
    con.execute(f"ATTACH '{escaped}' AS snap")
    try:
        for table in SERVING_TABLES:
            # Table names come from the fixed SERVING_TABLES tuple, never from input.
            con.execute(f"CREATE TABLE snap.{table} AS SELECT * FROM main.{table}")  # noqa: S608
        con.execute(
            "CREATE TABLE snap.observations AS "
            "SELECT series_key, ts_utc, value FROM main.observations "
            "WHERE resolution = 'quarterhour' ORDER BY series_key, ts_utc"
        )
        con.execute(
            "CREATE TABLE snap.hourly AS SELECT * FROM main.hourly ORDER BY series_key, ts_utc"
        )
        con.execute(
            "CREATE TABLE snap.load_log AS SELECT * FROM main.load_log "
            "WHERE run_id IN (SELECT run_id FROM main.load_runs "
            "ORDER BY started_at DESC LIMIT 30)"
        )
    finally:
        con.execute("DETACH snap")
    with tmp.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(tmp, target)
    return target
