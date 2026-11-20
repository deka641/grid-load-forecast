"""Atomic read-only snapshot of the warehouse for the API.

DuckDB allows either one writing process or several reading processes on a
file, not both at once. The pipeline therefore writes ``warehouse.duckdb`` and
publishes ``serving.duckdb`` by writing a temporary file and renaming it, so the
API never sees a partially written database. The temporary file is checkpointed
before it is renamed, so the published file needs no write-ahead log, and
leftovers of an earlier crashed or failed publish are removed.
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

# SMARD publishes quarter-hour values with at most SOURCE_DECIMALS decimals. The
# sum of four such values has as many, and their mean (a quarter of the sum) two more.
SOURCE_DECIMALS = 2
SUM_DECIMALS = SOURCE_DECIMALS
MEAN_DECIMALS = SOURCE_DECIMALS + 2


def publish(
    con: duckdb.DuckDBPyConnection, target: Path, *, exclude_series: Sequence[str] = ()
) -> Path:
    """Copy the serving tables to ``target`` atomically and return its path.

    ``exclude_series`` drops series (for example prices when they are switched
    off) from every published table, including data loaded earlier.
    """
    tmp = target.with_name(target.name + ".tmp")
    leftovers = (tmp, tmp.with_name(tmp.name + ".wal"))
    for path in leftovers:
        path.unlink(missing_ok=True)
    escaped = str(tmp).replace("'", "''")
    excluded = list(exclude_series)
    try:
        con.execute(f"ATTACH '{escaped}' AS snap")
        try:
            _copy(con, excluded)
            con.execute("CHECKPOINT snap")
        finally:
            con.execute("DETACH snap")
        with tmp.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except BaseException:
        for path in leftovers:
            path.unlink(missing_ok=True)
        raise
    directory = os.open(target.parent, os.O_RDONLY)
    try:
        os.fsync(directory)  # make the rename itself durable
    finally:
        os.close(directory)
    return target


def _copy(con: duckdb.DuckDBPyConnection, excluded: list[str]) -> None:
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
    # The sum or mean of four floats carries rounding noise in the last digits
    # (43648.21000000001), which can differ between two snapshots of the same
    # data. Rounded to the decimals the exact result has, the values are exact.
    con.execute(
        "CREATE TABLE snap.hourly AS SELECT h.series_key, h.ts_utc, "
        "round(h.value, CASE s.aggregation WHEN 'sum' THEN ? ELSE ? END) AS value "
        "FROM main.hourly h JOIN main.series s USING (series_key) "
        "WHERE h.series_key NOT IN (SELECT unnest(?::VARCHAR[])) "
        "ORDER BY h.series_key, h.ts_utc",
        [SUM_DECIMALS, MEAN_DECIMALS, excluded],
    )
    con.execute(
        "CREATE TABLE snap.load_log AS SELECT * FROM main.load_log "
        "WHERE series_key NOT IN (SELECT unnest(?::VARCHAR[])) "
        "AND run_id IN (SELECT run_id FROM main.load_runs "
        "ORDER BY started_at DESC LIMIT 30)",
        [excluded],
    )
