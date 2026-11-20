"""Read queries against the serving snapshot.

All SQL is static. User input reaches DuckDB only as bound parameters, and
series keys are validated against the catalog before they get here.
"""

import json
import math
from collections import defaultdict
from datetime import date, datetime, timedelta

import duckdb

from gridload.api.models import (
    BacktestMetric,
    BacktestMetrics,
    ForecastDay,
    ForecastPoint,
    ForecastRunInfo,
    LastForecast,
    LoadRunInfo,
    Resolution,
    SeriesFreshness,
)
from gridload.series import Series
from gridload.timezones import BERLIN, day_hours, day_range, to_local

FORECAST_MODELS = ("lightgbm", "baseline_week")
PRIMARY_MODEL = "lightgbm"
LOAD_KEY = "load"
TSO_KEY = "load_forecast_tso"
QUARTER_HOUR = timedelta(minutes=15)

# Points are read in batches and written straight to JSON text: a maximal request
# (15 series, 366 days, hourly) has about 130,000 points, and one pydantic model
# per point would multiply the memory of the response.
_ACTUALS_SQL = {
    Resolution.HOUR: (
        "SELECT epoch_ms(ts_utc), value FROM hourly "
        "WHERE series_key = ? AND ts_utc >= ? AND ts_utc < ? AND isfinite(value) "
        "ORDER BY ts_utc"
    ),
    Resolution.QUARTERHOUR: (
        "SELECT epoch_ms(ts_utc), value FROM observations "
        "WHERE series_key = ? AND ts_utc >= ? AND ts_utc < ? AND isfinite(value) "
        "ORDER BY ts_utc"
    ),
}
BATCH = 8192


def _text(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _local_iso(epoch_ms: int) -> str:
    return datetime.fromtimestamp(epoch_ms / 1000, tz=BERLIN).isoformat()


def actuals_json(
    cur: duckdb.DuckDBPyConnection,
    series: list[Series],
    first: date,
    last: date,
    resolution: Resolution,
) -> bytes:
    """Return the ``Actuals`` document as compact JSON bytes.

    The layout is the one pydantic would produce for ``Actuals``; a test
    compares both. Non-finite values are left out, since ``value`` is not nullable.
    """
    start, end = day_range(first, last)
    parts = [
        f'{{"resolution":{_text(resolution.value)},"start":"{first.isoformat()}",'
        f'"end":"{last.isoformat()}","series":['
    ]
    for i, s in enumerate(series):
        parts.append(
            f'{"," if i else ""}{{"key":{_text(s.key)},"label":{_text(s.label)},'
            f'"unit":{_text(s.unit)},"points":['
        )
        result = cur.execute(_ACTUALS_SQL[resolution], [s.key, start, end])
        separator = ""
        while rows := result.fetchmany(BATCH):
            parts.append(
                separator + ",".join(f'{{"ts":"{_local_iso(t)}","value":{v!r}}}' for t, v in rows)
            )
            separator = ","
        parts.append("]}")
    parts.append("]}")
    return "".join(parts).encode()


def _finite(value: float | None) -> float | None:
    return value if value is not None and math.isfinite(value) else None


def latest_forecast_day(cur: duckdb.DuckDBPyConnection) -> date | None:
    row = cur.execute(
        "SELECT max(delivery_day) FROM forecast_runs WHERE model = ?", [PRIMARY_MODEL]
    ).fetchone()
    assert row is not None  # noqa: S101 (an aggregate always returns one row)
    day: date | None = row[0]
    return day


def forecast_days(cur: duckdb.DuckDBPyConnection, limit: int) -> list[date]:
    rows = cur.execute(
        "SELECT DISTINCT delivery_day FROM forecast_runs WHERE list_contains(?, model) "
        "ORDER BY delivery_day DESC LIMIT ?",
        [list(FORECAST_MODELS), limit],
    ).fetchall()
    return [r[0] for r in rows]


def forecast_day(cur: duckdb.DuckDBPyConnection, day: date) -> ForecastDay | None:
    """Return the forecasts for one delivery day, or None if no model has a run for it."""
    runs = cur.execute(
        "SELECT model, run_id, issued_at, created_at FROM forecast_runs "
        "WHERE delivery_day = ? AND list_contains(?, model) "
        "QUALIFY row_number() OVER "
        "(PARTITION BY model ORDER BY issued_at DESC, created_at DESC, run_id DESC) = 1",
        [day, list(FORECAST_MODELS)],
    ).fetchall()
    if not runs:
        return None
    model_of_run = {run_id: model for model, run_id, _, _ in runs}
    start, end = day_range(day, day)
    values: dict[tuple[str, datetime], float] = {}
    for run_id, ts, value in cur.execute(
        "SELECT run_id, target_ts_utc, value FROM forecasts "
        "WHERE list_contains(?, run_id) AND target_ts_utc >= ? AND target_ts_utc < ?",
        [list(model_of_run), start, end],
    ).fetchall():
        values[model_of_run[run_id], ts] = value
    for key, ts, value in cur.execute(
        "SELECT series_key, ts_utc, value FROM hourly "
        "WHERE list_contains(?, series_key) AND ts_utc >= ? AND ts_utc < ?",
        [[LOAD_KEY, TSO_KEY], start, end],
    ).fetchall():
        values[key, ts] = value
    points = [
        ForecastPoint(
            ts=to_local(ts),
            lightgbm=_finite(values.get(("lightgbm", ts))),
            baseline_week=_finite(values.get(("baseline_week", ts))),
            tso=_finite(values.get((TSO_KEY, ts))),
            actual=_finite(values.get((LOAD_KEY, ts))),
        )
        for ts in day_hours(day)
    ]
    return ForecastDay(
        delivery_day=day,
        unit="MWh",
        runs={
            model: ForecastRunInfo(
                run_id=run_id, issued_at=to_local(issued), created_at=to_local(created)
            )
            for model, run_id, issued, created in sorted(runs)
        },
        points=points,
    )


def _scope_order(value: str) -> tuple[int, str]:
    # Local hours are stored as text; sort them numerically.
    return (int(value), "") if value.isdigit() else (24, value)


def backtest(cur: duckdb.DuckDBPyConnection) -> BacktestMetrics | None:
    run = cur.execute(
        "SELECT run_id, created_at, first_day, last_day, config FROM backtest_runs "
        "ORDER BY created_at DESC, run_id DESC LIMIT 1"
    ).fetchone()
    if run is None:
        return None
    run_id, created_at, first_day, last_day, config = run
    grouped: dict[str, dict[str, list[BacktestMetric]]] = defaultdict(lambda: defaultdict(list))
    rows = cur.execute(
        "SELECT model, scope, scope_value, mae, mape, n FROM backtest_metrics "
        "WHERE run_id = ? AND isfinite(mae) AND isfinite(mape) ORDER BY model, scope",
        [run_id],
    ).fetchall()
    for model, scope, scope_value, mae, mape, n in rows:
        grouped[model][scope].append(
            BacktestMetric(scope_value=scope_value, mae=mae, mape=mape, n=n)
        )
    for scopes in grouped.values():
        for items in scopes.values():
            items.sort(key=lambda m: _scope_order(m.scope_value))
    return BacktestMetrics(
        run_id=run_id,
        created_at=to_local(created_at),
        first_day=first_day,
        last_day=last_day,
        config=json.loads(config),
        metrics={model: dict(scopes) for model, scopes in grouped.items()},
    )


def last_timestamps(cur: duckdb.DuckDBPyConnection) -> dict[str, datetime]:
    """Return the newest quarter-hour timestamp of every series in the snapshot."""
    return dict(
        cur.execute(
            "SELECT series_key, max(ts_utc) FROM observations GROUP BY series_key"
        ).fetchall()
    )


def freshness(
    last: dict[str, datetime], series: list[Series], now: datetime
) -> list[SeriesFreshness]:
    result = []
    for s in series:
        ts = last.get(s.key)
        result.append(
            SeriesFreshness(
                key=s.key,
                label=s.label,
                last_ts=None if ts is None else to_local(ts),
                age_seconds=None if ts is None else (now - (ts + QUARTER_HOUR)).total_seconds(),
            )
        )
    return result


def last_load_run(cur: duckdb.DuckDBPyConnection) -> LoadRunInfo | None:
    row = cur.execute(
        "SELECT run_id, mode, status, started_at, finished_at FROM load_runs "
        "ORDER BY started_at DESC, run_id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    run_id, mode, status, started_at, finished_at = row
    return LoadRunInfo(
        run_id=run_id,
        mode=mode,
        status=status,
        started_at=to_local(started_at),
        finished_at=None if finished_at is None else to_local(finished_at),
    )


def last_forecast(cur: duckdb.DuckDBPyConnection) -> LastForecast | None:
    row = cur.execute(
        "SELECT model, delivery_day, issued_at FROM forecast_runs WHERE model = ? "
        "ORDER BY delivery_day DESC, issued_at DESC LIMIT 1",
        [PRIMARY_MODEL],
    ).fetchone()
    if row is None:
        return None
    model, delivery_day, issued_at = row
    return LastForecast(model=model, delivery_day=delivery_day, issued_at=to_local(issued_at))


def schema_version(cur: duckdb.DuckDBPyConnection) -> int | None:
    row = cur.execute("SELECT max(version) FROM schema_version").fetchone()
    assert row is not None  # noqa: S101 (an aggregate always returns one row)
    version: int | None = row[0]
    return version
