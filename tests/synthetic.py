"""Deterministic synthetic hourly load for forecast tests."""

import math
from datetime import UTC, date, datetime, timedelta

import duckdb
import numpy as np
import polars as pl

from gridload import timezones as tz
from gridload.forecast.features import History
from gridload.pipeline.db import migrate


def hourly_load(first: date, last: date, *, seed: int = 7) -> tuple[np.ndarray, np.ndarray]:
    """UTC hour starts (ms) and values with daily and weekly shape plus noise."""
    rng = np.random.default_rng(seed)
    start = tz.local_midnight(first)
    end = tz.local_midnight(last + timedelta(days=1))
    hours = int((end - start) / timedelta(hours=1))
    ts = [start + timedelta(hours=i) for i in range(hours)]
    values = []
    for t in ts:
        local = t.astimezone(tz.BERLIN)
        daily = math.sin((local.hour - 6) / 24 * 2 * math.pi)
        weekly = -6000.0 if local.weekday() >= 5 else 0.0
        values.append(55000.0 + 9000.0 * daily + weekly + rng.normal(0, 800))
    ms = np.array([int(t.timestamp() * 1000) for t in ts], dtype=np.int64)
    return ms, np.array(values)


def history(first: date, last: date, *, seed: int = 7) -> History:
    ms, values = hourly_load(first, last, seed=seed)
    return History(ms, values)


def warehouse(first: date, last: date) -> duckdb.DuckDBPyConnection:
    """In-memory warehouse with quarter-hour load and TSO forecast series."""
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    migrate(con)
    ms, values = hourly_load(first, last)
    tso = values * 1.03
    now = datetime(2026, 1, 1, tzinfo=UTC)
    for key, series in (("load", values), ("load_forecast_tso", tso)):
        quarter_ms = np.repeat(ms, 4) + np.tile(np.arange(4) * 900_000, ms.size)
        frame = pl.DataFrame(
            {
                "series_key": key,
                "resolution": "quarterhour",
                "ts_ms": quarter_ms,
                "value": np.repeat(series / 4, 4),
            }
        )
        con.register("frame", frame)
        con.execute(
            "INSERT INTO observations SELECT series_key, resolution, "
            "make_timestamptz(ts_ms * 1000), value, ?, 0 FROM frame",
            [now],
        )
        con.unregister("frame")
    return con
