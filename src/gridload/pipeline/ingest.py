"""Backfill and incremental loading of SMARD chunks into the warehouse.

Each run fetches the chunk index per series and then loads:
- every chunk that is not yet stored (so a chunk that failed earlier is retried);
- the newest ``refetch_weeks`` chunks again, because SMARD fills the current
  week continuously and corrects recent values.

A chunk is processed in one transaction: raw payload, observations and the log
entry are written together. Unchanged payloads (same SHA-256) are skipped.
Changed values overwrite the stored value and increase its revision counter, so
there are never duplicate timestamps. Any source or validation error is written
to ``load_log`` and marks the run as failed; nothing is stored for that chunk.
"""

import uuid
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

import duckdb
import polars as pl

from gridload.series import Series
from gridload.sources.smard import (
    RESOLUTION,
    ChunkValidationError,
    RawChunk,
    SmardClient,
    SourceError,
    chunk_end_ms,
    ms_to_datetime,
    parse_chunk,
)

Progress = Callable[[str, int], None]


@dataclass
class RunResult:
    run_id: str
    mode: str
    status: str = "running"
    loaded: int = 0
    unchanged: int = 0
    errors: list[str] = field(default_factory=list)
    rows_written: int = 0


def _now() -> datetime:
    return datetime.now(UTC)


class Ingestor:
    def __init__(
        self,
        con: duckdb.DuckDBPyConnection,
        client: SmardClient,
        *,
        clock: Callable[[], datetime] = _now,
        progress: Progress | None = None,
    ) -> None:
        self._con = con
        self._client = client
        self._clock = clock
        self._progress = progress

    def run(
        self,
        series: list[Series],
        *,
        mode: str,
        since: datetime | None = None,
        refetch_weeks: int = 4,
    ) -> RunResult:
        result = RunResult(run_id=uuid.uuid4().hex, mode=mode)
        self._con.execute(
            "INSERT INTO load_runs VALUES (?, ?, ?, NULL, 'running')",
            [result.run_id, mode, self._clock()],
        )
        crashed = True
        try:
            for s in series:
                self._run_series(result, s, since=since, refetch_weeks=refetch_weeks)
            crashed = False
        finally:
            result.status = "failed" if crashed or result.errors else "ok"
            self._con.execute(
                "UPDATE load_runs SET finished_at = ?, status = ? WHERE run_id = ?",
                [self._clock(), result.status, result.run_id],
            )
        return result

    def _run_series(
        self, result: RunResult, series: Series, *, since: datetime | None, refetch_weeks: int
    ) -> None:
        try:
            index = self._client.index(series)
        except (SourceError, ChunkValidationError) as exc:
            self._log_error(result, series, None, f"index: {exc}")
            return

        latest = index[-1]
        if since is not None:
            since_ms = int(since.timestamp() * 1000)
            index = [ts for ts in index if chunk_end_ms(ts) > since_ms]
        known = self._known_chunks(series)
        refetch = set(index[-refetch_weeks:]) if refetch_weeks > 0 else set()
        targets = [ts for ts in index if ts not in known or ts in refetch]

        for ts in targets:
            if self._progress is not None:
                self._progress(series.key, ts)
            try:
                raw = self._client.chunk(series, ts)
                frame = parse_chunk(series, ts, raw.payload, is_latest=ts == latest)
            except (SourceError, ChunkValidationError) as exc:
                self._log_error(result, series, ts, str(exc))
                continue
            self._store(result, series, raw, frame)

    def _known_chunks(self, series: Series) -> set[int]:
        rows = self._con.execute(
            "SELECT epoch_ms(chunk_ts) FROM raw_chunks WHERE series_key = ? AND resolution = ?",
            [series.key, RESOLUTION],
        ).fetchall()
        return {int(r[0]) for r in rows}

    def _store(self, result: RunResult, series: Series, raw: RawChunk, frame: pl.DataFrame) -> None:
        chunk_at = ms_to_datetime(raw.chunk_ts)
        now = self._clock()
        con = self._con
        stored = con.execute(
            "SELECT sha256 FROM raw_chunks "
            "WHERE series_key = ? AND resolution = ? AND chunk_ts = ?",
            [series.key, RESOLUTION, chunk_at],
        ).fetchone()
        if stored is not None and stored[0] == raw.sha256:
            self._log(result.run_id, series, raw.chunk_ts, status="unchanged", rows=0, message=None)
            result.unchanged += 1
            return

        incoming = frame.select(
            pl.lit(series.key).alias("series_key"),
            pl.lit(RESOLUTION).alias("resolution"),
            "ts_utc",
            "value",
        )
        con.execute("BEGIN")
        try:
            con.register("incoming", incoming)
            changed_row = con.execute(
                """
                SELECT count(*) FROM incoming i
                LEFT JOIN observations o USING (series_key, resolution, ts_utc)
                WHERE o.value IS NULL OR o.value IS DISTINCT FROM i.value
                """
            ).fetchone()
            changed = int(changed_row[0]) if changed_row else 0
            con.execute(
                """
                INSERT INTO observations
                    (series_key, resolution, ts_utc, value, updated_at, revision)
                SELECT series_key, resolution, ts_utc, value, ?, 0 FROM incoming
                ON CONFLICT (series_key, resolution, ts_utc) DO UPDATE
                SET value = excluded.value,
                    updated_at = excluded.updated_at,
                    revision = observations.revision + 1
                WHERE observations.value IS DISTINCT FROM excluded.value
                """,
                [now],
            )
            con.execute(
                "INSERT OR REPLACE INTO raw_chunks VALUES (?, ?, ?, ?, ?, ?)",
                [series.key, RESOLUTION, chunk_at, now, raw.sha256, zlib.compress(raw.payload, 9)],
            )
            self._log(
                result.run_id, series, raw.chunk_ts, status="loaded", rows=changed, message=None
            )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.unregister("incoming")
        result.loaded += 1
        result.rows_written += changed

    def _log_error(
        self, result: RunResult, series: Series, chunk_ts: int | None, message: str
    ) -> None:
        result.errors.append(f"{series.key}: {message}")
        self._log(result.run_id, series, chunk_ts, status="error", rows=0, message=message)

    def _log(
        self,
        run_id: str,
        series: Series,
        chunk_ts: int | None,
        *,
        status: str,
        rows: int,
        message: str | None,
    ) -> None:
        self._con.execute(
            "INSERT INTO load_log VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                run_id,
                series.key,
                None if chunk_ts is None else ms_to_datetime(chunk_ts),
                status,
                rows,
                message,
                self._clock(),
            ],
        )
