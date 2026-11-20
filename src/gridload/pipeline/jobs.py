"""Pipeline jobs used by the CLI and the scheduler."""

import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from datetime import time as clock_time
from pathlib import Path
from typing import Any

import duckdb
import numpy as np

from gridload.config import Settings
from gridload.forecast import calendar as fcal
from gridload.forecast import service
from gridload.pipeline import heartbeat, snapshot
from gridload.pipeline.db import connect
from gridload.pipeline.ingest import Ingestor, RunResult
from gridload.series import price_keys, select
from gridload.sources.smard import SmardClient

log = logging.getLogger(__name__)

ClientFactory = Callable[[Settings], SmardClient]
PostIngest = Callable[[duckdb.DuckDBPyConnection, Settings, Callable[[], None]], list[str]]


def default_client(settings: Settings) -> SmardClient:
    return SmardClient.create(settings.base_url)


class _Beat:
    """Write a running heartbeat at most once per ``interval`` seconds."""

    def __init__(self, settings: Settings, interval: float = 60.0) -> None:
        self._path = settings.heartbeat_path
        self._interval = interval
        self._last = float("-inf")
        previous = heartbeat.read(self._path)
        self.last_success = previous.last_success if previous else None
        self.newest_load = previous.newest_load if previous else None

    def __call__(self, _series: str = "", _chunk: int = 0) -> None:
        now = time.monotonic()
        if now - self._last >= self._interval:
            self._last = now
            heartbeat.write(
                self._path,
                heartbeat.Heartbeat(
                    "running", heartbeat.now_iso(), self.last_success, None, self.newest_load
                ),
            )

    def finish(self, status: str, newest_load: datetime | None = None) -> None:
        if status in {"ok", "partial"}:
            self.last_success = heartbeat.now_iso()
        if newest_load is not None:
            self.newest_load = newest_load.astimezone(UTC).isoformat()
        heartbeat.write(
            self._path,
            heartbeat.Heartbeat(
                "idle", heartbeat.now_iso(), self.last_success, status, self.newest_load
            ),
        )


def load(
    settings: Settings,
    *,
    mode: str,
    keys: list[str] | None = None,
    since: datetime | None = None,
    refetch_weeks: int = 4,
    accept_withdrawals: bool = False,
    client_factory: ClientFactory = default_client,
    post_ingest: PostIngest | None = None,
) -> RunResult:
    """Ingest, run follow-up steps, publish the snapshot and update the heartbeat.

    The load run gets its final status and ``finished_at`` once the follow-up steps
    are done, just before the snapshot that contains it is published. If a step
    after ingestion fails, the load run is marked failed as well.
    """
    series = select(keys, include_prices=settings.include_prices)
    excluded = [] if settings.include_prices else price_keys()
    beat = _Beat(settings)
    beat()
    client = client_factory(settings)
    status = "failed"
    newest: datetime | None = None
    try:
        with connect(settings.warehouse_path) as con:
            ingestor = Ingestor(con, client, progress=beat, accept_withdrawals=accept_withdrawals)
            result = ingestor.run(
                series, mode=mode, since=since, refetch_weeks=refetch_weeks, finish=False
            )
            try:
                failed_steps = [] if post_ingest is None else post_ingest(con, settings, beat)
                if failed_steps:
                    # Data is published, but a follow-up step failed: make that visible.
                    result.errors.extend(f"step failed: {name}" for name in failed_steps)
                    if result.status == "ok":
                        result.status = "partial"
                ingestor.finish(result.run_id, result.status)
                row = con.execute(
                    "SELECT max(ts_utc) FROM observations WHERE series_key = 'load'"
                ).fetchone()
                newest = row[0] if row else None
                con.execute("CHECKPOINT")
                snapshot.publish(con, settings.serving_path, exclude_series=excluded)
            except Exception:
                ingestor.finish(result.run_id, "failed")
                raise
        status = result.status
    finally:
        client.close()
        beat.finish(status, newest)
    return result


def status(settings: Settings) -> dict[str, Any]:
    """Summarise the published snapshot: last runs, per-series coverage, recent errors.

    Reads ``serving.duckdb`` read-only, so it works while the pipeline is writing.
    """
    path = settings.serving_path
    if not path.exists():
        return {"snapshot": str(path), "initialised": False}
    con = duckdb.connect(str(path), read_only=True)
    try:
        con.execute("SET TimeZone = 'UTC'")
        runs = con.execute(
            "SELECT run_id, mode, started_at, finished_at, status FROM load_runs "
            "ORDER BY started_at DESC LIMIT 5"
        ).fetchall()
        coverage = con.execute(
            "SELECT series_key, min(ts_utc), max(ts_utc), count(*) FROM observations "
            "GROUP BY series_key ORDER BY series_key"
        ).fetchall()
        errors = con.execute(
            "SELECT series_key, chunk_ts, message FROM load_log "
            "WHERE status IN ('error', 'warning') "
            "AND run_id = (SELECT run_id FROM load_runs ORDER BY started_at DESC LIMIT 1)"
        ).fetchall()
    finally:
        con.close()
    return {
        "snapshot": str(path),
        "initialised": True,
        "runs": [
            {
                "run_id": r[0],
                "mode": r[1],
                "started_at": _iso(r[2]),
                "finished_at": _iso(r[3]),
                "status": r[4],
            }
            for r in runs
        ],
        "series": [
            {"series": c[0], "first": _iso(c[1]), "last": _iso(c[2]), "points": c[3]}
            for c in coverage
        ],
        "errors_last_run": [{"series": e[0], "chunk": _iso(e[1]), "message": e[2]} for e in errors],
    }


def healthcheck(settings: Settings) -> bool:
    return heartbeat.is_healthy(heartbeat.read(settings.heartbeat_path), datetime.now(UTC))


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat()


def dumps(data: object) -> str:
    return json.dumps(data, indent=2)


def post_ingest_steps(
    con: duckdb.DuckDBPyConnection,
    settings: Settings,
    progress: Callable[[], None] | None = None,
    now: datetime | None = None,
) -> list[str]:
    """Issue the day-ahead forecast when due and refresh a stale backtest.

    The forecast waits until the hour that ends at the cutoff (09:00 to 10:00
    local) is loaded and retries every hourly run; from ``DEGRADED_AFTER`` local
    time it is issued anyway and marked as degraded, unless the newest hour is
    older than ``DEGRADED_MAX_AGE_HOURS``. A forecast is also marked as degraded
    when key features are missing for the delivery day (see
    :func:`service.degraded_checks`); that does not delay it. A backtest is attempted at most once
    per ``BACKTEST_RETRY`` (state in ``backtest_attempt.json``), so a failing or
    empty backtest does not repeat every hour. Each step is isolated: a failure
    is logged, returned by name and does not stop the snapshot from being
    published.
    """
    current = now or datetime.now(UTC)
    tick = progress or (lambda: None)
    failed: list[str] = []
    try:
        _forecast_step(con, current)
    except Exception:
        log.exception("forecast step failed")
        failed.append("forecast")
    tick()
    attempt_file = settings.data_dir / "backtest_attempt.json"
    try:
        if service.backtest_is_stale(con, current) and _backtest_due(attempt_file, current):
            _write_attempt(attempt_file, current)
            result = service.run_and_store_backtest(con, now=current, progress=lambda _day: tick())
            if result is not None:
                log.info("backtest stored for %s to %s", result.first_day, result.last_day)
            else:
                log.warning("backtest skipped: not enough history to score")
    except Exception:
        log.exception("backtest step failed")
        failed.append("backtest")
    return failed


BACKTEST_RETRY = timedelta(hours=24)
DEGRADED_MAX_AGE_HOURS = 24


def _backtest_due(path: Path, now: datetime) -> bool:
    try:
        last = datetime.fromisoformat(json.loads(path.read_text(encoding="utf-8"))["attempted_at"])
    except OSError, ValueError, KeyError, TypeError:
        return True
    return last.utcoffset() is None or now - last >= BACKTEST_RETRY


def _write_attempt(path: Path, now: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"attempted_at": now.astimezone(UTC).isoformat()}), encoding="utf-8")


DEGRADED_AFTER = clock_time(20, 0)


def _forecast_step(con: duckdb.DuckDBPyConnection, current: datetime) -> None:
    day = service.due_delivery_day(con, current)
    if day is None:
        return
    load = service.load_history(con, "load")
    complete = load.has_hour_before(fcal.cutoff_for(day))
    late = current.astimezone(fcal.BERLIN).time() >= DEGRADED_AFTER
    if not complete and not late:
        log.info("forecast for %s waits for data up to %s", day, fcal.cutoff_for(day))
        return
    if not complete and np.isnan(load.last_value(fcal.cutoff_for(day), DEGRADED_MAX_AGE_HOURS)):
        log.warning(
            "forecast for %s skipped: no load data in the %s h before the cutoff",
            day,
            DEGRADED_MAX_AGE_HOURS,
        )
        return
    run_id = service.issue_forecast(con, day, now=current, degraded=not complete)
    if run_id is None:
        log.warning("forecast for %s skipped: not enough training history", day)
        return
    checks = service.degraded_checks(load, day, cutoff_hour_missing=not complete)
    note = f" (degraded: {', '.join(checks)})" if checks else ""
    log.info("forecast issued for delivery day %s%s", day, note)


def backtest(settings: Settings, *, days: int) -> str:
    """Run and store a backtest, then republish the snapshot. Returns a summary line."""
    if days < 7:
        return "error: --days must be at least 7"
    with connect(settings.warehouse_path) as con:
        result = service.run_and_store_backtest(con, days=days)
        if result is None:
            return "error: no complete day of load data"
        con.execute("CHECKPOINT")
        snapshot.publish(
            con,
            settings.serving_path,
            exclude_series=[] if settings.include_prices else price_keys(),
        )
    return f"backtest {result.first_day} to {result.last_day}: {len(result.metrics)} metrics"
