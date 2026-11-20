"""Warehouse access for the forecast: load history, issue forecasts, store backtests."""

import json
import uuid
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta

import duckdb
import numpy as np
import polars as pl

from gridload import timezones as tz
from gridload.forecast import calendar as cal
from gridload.forecast.backtest import BacktestResult, run_backtest
from gridload.forecast.features import History, baseline_week, feature_gaps
from gridload.forecast.model import (
    BASELINE_NAME,
    MODEL_NAME,
    FeatureStore,
    params_hash,
    predict,
    train,
)

BACKTEST_MAX_AGE = timedelta(days=7)
MIN_TRAINING_ROWS = 28 * 24


def load_history(con: duckdb.DuckDBPyConnection, series_key: str) -> History:
    """Hourly values of one series (complete hours only) as a :class:`History`."""
    result = con.execute(
        "SELECT epoch_ms(ts_utc) AS ts_ms, value FROM hourly WHERE series_key = ? ORDER BY ts_ms",
        [series_key],
    ).fetchnumpy()
    frame = pl.DataFrame(
        {"ts_ms": result["ts_ms"], "value": result["value"]},
        schema={"ts_ms": pl.Int64, "value": pl.Float64},
    )
    return History.from_frame(frame)


def last_complete_day(history: History) -> date | None:
    """Latest local day for which every hour is present."""
    if len(history) == 0:
        return None
    day = cal.local_day(datetime.fromtimestamp(int(history.ts[-1]) / 1000, UTC))
    first = cal.local_day(datetime.fromtimestamp(int(history.ts[0]) / 1000, UTC))
    while day >= first:
        hours = tz.day_hours(day)
        if all(not np.isnan(history.value_at(ts, cal.never())) for ts in hours):
            return day
        day -= timedelta(days=1)
    return None


def due_delivery_day(con: duckdb.DuckDBPyConnection, now: datetime) -> date | None:
    """Delivery day to forecast now, or None if not yet due or already issued."""
    local_now = now.astimezone(tz.BERLIN)
    if local_now.time() < cal.ORIGIN_LOCAL_TIME:
        return None
    delivery_day = local_now.date() + timedelta(days=1)
    exists = con.execute(
        "SELECT count(*) FROM forecast_runs WHERE model = ? AND delivery_day = ?",
        [MODEL_NAME, delivery_day],
    ).fetchone()
    return None if exists and exists[0] else delivery_day


def degraded_checks(load: History, delivery_day: date, *, cutoff_hour_missing: bool) -> list[str]:
    """Reasons to mark the forecast for ``delivery_day`` as degraded (empty if none)."""
    reasons = ["cutoff_hour_missing"] if cutoff_hour_missing else []
    return reasons + feature_gaps(load, delivery_day)


def issue_forecast(
    con: duckdb.DuckDBPyConnection,
    delivery_day: date,
    *,
    now: datetime | None = None,
    degraded: bool = False,
) -> str | None:
    """Train on all days that ended before the origin day and store model and baseline runs.

    Returns None (and stores nothing) if there are fewer than ``MIN_TRAINING_ROWS``
    training hours. ``degraded`` means the newest admissible hour is missing. The
    run details record ``degraded`` (true if any check in :func:`degraded_checks`
    fails, for example a hollowed week before the cutoff) and ``degraded_checks``.
    """
    created = now or datetime.now(UTC)
    load = load_history(con, "load")
    if len(load) == 0:
        return None
    last_training_day = delivery_day - timedelta(days=2)
    store = FeatureStore.build(load, cal.days_between(training_start(load), delivery_day))
    mask = store.training_mask(last_training_day)
    if int(mask.sum()) < MIN_TRAINING_ROWS:
        return None
    checks = degraded_checks(load, delivery_day, cutoff_hour_missing=degraded)
    booster = train(store.matrix[mask], store.labels[mask])
    rows = store.day_mask(delivery_day)
    targets = tz.day_hours(delivery_day)
    forecasts = {
        MODEL_NAME: predict(booster, store.matrix[rows]),
        BASELINE_NAME: baseline_week(load, delivery_day),
    }
    details = {
        "cutoff": cal.cutoff_for(delivery_day).isoformat(),
        "training_rows": int(mask.sum()),
        "last_training_day": last_training_day.isoformat(),
        "params_hash": params_hash(),
        "degraded": bool(checks),
        "degraded_checks": checks,
    }
    run_id = ""
    con.execute("BEGIN")
    try:
        for model, values in forecasts.items():
            run_id = uuid.uuid4().hex
            con.execute(
                "INSERT INTO forecast_runs VALUES (?, ?, ?, ?, ?, ?)",
                [
                    run_id,
                    model,
                    cal.origin_for(delivery_day),
                    delivery_day,
                    created,
                    json.dumps(details if model == MODEL_NAME else {"cutoff": details["cutoff"]}),
                ],
            )
            points = [
                [run_id, ts, float(v)]
                for ts, v in zip(targets, values, strict=True)
                if not np.isnan(v)
            ]
            if points:
                con.executemany("INSERT INTO forecasts VALUES (?, ?, ?)", points)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return run_id


def training_start(load: History) -> date:
    """First delivery day with two weeks of history before it."""
    first = cal.local_day(datetime.fromtimestamp(int(load.ts[0]) / 1000, UTC))
    return first + timedelta(days=15)


def backtest_is_stale(con: duckdb.DuckDBPyConnection, now: datetime) -> bool:
    """True if there is no backtest with metrics younger than ``BACKTEST_MAX_AGE``."""
    row = con.execute(
        "SELECT max(created_at) FROM backtest_runs r "
        "WHERE EXISTS (SELECT 1 FROM backtest_metrics m WHERE m.run_id = r.run_id)"
    ).fetchone()
    return row is None or row[0] is None or now - row[0] > BACKTEST_MAX_AGE


def run_and_store_backtest(
    con: duckdb.DuckDBPyConnection,
    *,
    days: int = 365,
    now: datetime | None = None,
    progress: Callable[[date], None] | None = None,
) -> BacktestResult | None:
    """Backtest up to the last complete day and store run and metrics.

    Returns None and stores nothing if there is no complete day or no hour could
    be scored.
    """
    load = load_history(con, "load")
    last_day = last_complete_day(load)
    if last_day is None:
        return None
    result = run_backtest(
        load,
        load_history(con, "load_forecast_tso"),
        last_day,
        days=days,
        training_start=training_start(load),
        progress=progress,
    )
    if not result.metrics:
        return None  # nothing scored (history too short): store nothing
    run_id = uuid.uuid4().hex
    con.execute("BEGIN")
    try:
        con.execute(
            "INSERT INTO backtest_runs VALUES (?, ?, ?, ?, ?)",
            [
                run_id,
                now or datetime.now(UTC),
                result.first_day,
                result.last_day,
                json.dumps(result.config),
            ],
        )
        con.executemany(
            "INSERT INTO backtest_metrics VALUES (?, ?, ?, ?, ?, ?, ?)",
            [[run_id, *asdict(m).values()] for m in result.metrics],
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return result
