"""Pipeline jobs used by the CLI and the scheduler."""

import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import duckdb

from gridload.config import Settings
from gridload.forecast import service
from gridload.pipeline import heartbeat, snapshot
from gridload.pipeline.db import connect
from gridload.pipeline.ingest import Ingestor, RunResult
from gridload.series import price_keys, select
from gridload.sources.smard import SmardClient

log = logging.getLogger(__name__)

ClientFactory = Callable[[Settings], SmardClient]
PostIngest = Callable[[duckdb.DuckDBPyConnection, Settings], None]


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

    def __call__(self, _series: str = "", _chunk: int = 0) -> None:
        now = time.monotonic()
        if now - self._last >= self._interval:
            self._last = now
            heartbeat.write(
                self._path,
                heartbeat.Heartbeat("running", heartbeat.now_iso(), self.last_success),
            )

    def finish(self, status: str) -> None:
        if status in {"ok", "partial"}:
            self.last_success = heartbeat.now_iso()
        heartbeat.write(
            self._path,
            heartbeat.Heartbeat("idle", heartbeat.now_iso(), self.last_success, status),
        )


def load(
    settings: Settings,
    *,
    mode: str,
    keys: list[str] | None = None,
    since: datetime | None = None,
    refetch_weeks: int = 4,
    client_factory: ClientFactory = default_client,
    post_ingest: PostIngest | None = None,
) -> RunResult:
    """Ingest, run follow-up steps, publish the snapshot and update the heartbeat.

    If a step after ingestion fails, the load run is marked failed as well.
    """
    series = select(keys, include_prices=settings.include_prices)
    excluded = [] if settings.include_prices else price_keys()
    beat = _Beat(settings)
    beat()
    client = client_factory(settings)
    status = "failed"
    try:
        with connect(settings.warehouse_path) as con:
            ingestor = Ingestor(con, client, progress=beat)
            result = ingestor.run(series, mode=mode, since=since, refetch_weeks=refetch_weeks)
            try:
                if post_ingest is not None:
                    post_ingest(con, settings)
                con.execute("CHECKPOINT")
                snapshot.publish(con, settings.serving_path, exclude_series=excluded)
            except Exception:
                ingestor.finish(result.run_id, "failed")
                raise
        status = result.status
    finally:
        client.close()
        beat.finish(status)
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
            "SELECT series_key, chunk_ts, message FROM load_log WHERE status = 'error' "
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
    con: duckdb.DuckDBPyConnection, settings: Settings, now: datetime | None = None
) -> None:
    """Issue the day-ahead forecast when due and refresh a stale backtest."""
    current = now or datetime.now(UTC)
    day = service.due_delivery_day(con, current)
    if day is not None:
        last = service.last_complete_day(service.load_history(con, "load"))
        if last is None or last < day - timedelta(days=2):
            log.warning("forecast for %s skipped: last complete day is %s", day, last)
        else:
            service.issue_forecast(con, day, now=current)
            log.info("forecast issued for delivery day %s", day)
    if service.backtest_is_stale(con, current):
        result = service.run_and_store_backtest(con, now=current)
        if result is not None:
            log.info("backtest stored for %s to %s", result.first_day, result.last_day)


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
