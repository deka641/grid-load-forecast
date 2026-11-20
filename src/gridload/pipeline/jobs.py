"""Pipeline jobs used by the CLI and the scheduler."""

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import duckdb

from gridload.config import Settings
from gridload.pipeline import heartbeat, snapshot
from gridload.pipeline.db import connect
from gridload.pipeline.ingest import Ingestor, RunResult
from gridload.series import price_keys, select
from gridload.sources.smard import SmardClient

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
    newest: datetime | None = None
    try:
        with connect(settings.warehouse_path) as con:
            ingestor = Ingestor(con, client, progress=beat)
            result = ingestor.run(series, mode=mode, since=since, refetch_weeks=refetch_weeks)
            try:
                if post_ingest is not None:
                    post_ingest(con, settings)
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


def post_ingest_steps(con: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    """Steps after ingestion in the scheduled job (none yet; the forecast hooks in here)."""
