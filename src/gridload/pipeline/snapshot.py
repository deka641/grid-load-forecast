"""Atomic read-only snapshot of the warehouse for the API.

DuckDB allows either one writing process or several reading processes on a
file, not both at once. The pipeline therefore writes ``warehouse.duckdb`` and
publishes ``serving.duckdb`` by writing a temporary file and renaming it, so the
API never sees a partially written database.
"""

import os
from collections.abc import Sequence
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


def publish(
    con: duckdb.DuckDBPyConnection, target: Path, *, exclude_series: Sequence[str] = ()
) -> Path:
    """Copy the serving tables to ``target`` atomically and return its path.

    ``exclude_series`` drops series (for example prices when they are switched
    off) from every published table, including data loaded earlier.
    """
    tmp = target.with_name(target.name + ".tmp")
    tmp.unlink(missing_ok=True)
    escaped = str(tmp).replace("'", "''")
    con.execute(f"ATTACH '{escaped}' AS snap")
    excluded = list(exclude_series)
    try:
        for table in SERVING_TABLES:
            # Table names come from the fixed SERVING_TABLES tuple, never from input.
            con.execute(f"CREATE TABLE snap.{table} AS SELECT * FROM main.{table}")  # noqa: S608
        con.execute(
            "DELETE FROM snap.series WHERE series_key IN (SELECT unnest(?::VARCHAR[]))",
            [excluded],
        )
        con.execute(
            "CREATE TABLE snap.observations AS "
            "SELECT series_key, ts_utc, value FROM main.observations "
            "WHERE resolution = 'quarterhour' AND series_key NOT IN (SELECT unnest(?::VARCHAR[])) "
            "ORDER BY series_key, ts_utc",
            [excluded],
        )
        con.execute(
            "CREATE TABLE snap.hourly AS SELECT * FROM main.hourly WHERE "
            "series_key NOT IN (SELECT unnest(?::VARCHAR[])) "
            "ORDER BY series_key, ts_utc",
            [excluded],
        )
        con.execute(
            "CREATE TABLE snap.load_log AS SELECT * FROM main.load_log "
            "WHERE series_key NOT IN (SELECT unnest(?::VARCHAR[])) "
            "AND run_id IN (SELECT run_id FROM main.load_runs "
            "ORDER BY started_at DESC LIMIT 30)",
            [excluded],
        )
    finally:
        con.execute("DETACH snap")
    with tmp.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(tmp, target)
    directory = os.open(target.parent, os.O_RDONLY)
    try:
        os.fsync(directory)  # make the rename itself durable
    finally:
        os.close(directory)
    return target
