"""Backfill and incremental loading of SMARD chunks into the warehouse.

Each run fetches the chunk index per series and then loads:
- every chunk that is not yet stored (so a chunk that failed earlier is retried);
- every stored chunk that is still incomplete (it had trailing nulls when it was
  loaded), however old it is;
- the newest ``refetch_weeks`` chunks again, because SMARD corrects recent
  values. Corrections to older chunks are only picked up with a larger
  ``refetch_weeks`` (for example ``gridload ingest --refetch-weeks 9999``).

A chunk is processed in one transaction: raw payload, observations and the log
entry are written together. Unchanged payloads (same SHA-256) are skipped.
Changed values overwrite the stored value and increase its revision counter, so
there are never duplicate timestamps. Values that the source no longer
publishes for a chunk are removed, so the cleaned data always matches the
stored raw payload. Any source or validation error is written to ``load_log``
and nothing is stored for that chunk.

Run status: ``failed`` if the run crashed, the source was unavailable, an index
could not be read or a recent chunk (newest ``refetch_weeks`` or ending in the
last 7 days) failed; ``partial`` if only older chunks failed; otherwise ``ok``.
"""

import uuid
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import duckdb
import numpy as np
import polars as pl

from gridload.series import Series
from gridload.sources.smard import (
    QUARTER_HOUR_MS,
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

# The newest values lag real time by one to two hours, so a chunk that ended
# recently may still miss its last values while the next chunk already exists.
LATE_DATA_ALLOWANCE = timedelta(hours=6)
RECENT = timedelta(days=7)
MAX_CONSECUTIVE_SOURCE_ERRORS = 5


class SourceUnavailableError(RuntimeError):
    """Too many consecutive source errors; the rest of the run is skipped."""


@dataclass
class RunResult:
    run_id: str
    mode: str
    status: str = "running"
    loaded: int = 0
    unchanged: int = 0
    errors: list[str] = field(default_factory=list)
    recent_errors: int = 0
    rows_written: int = 0
    rows_removed: int = 0


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
        self._source_errors = 0

    def run(
        self,
        series: list[Series],
        *,
        mode: str,
        since: datetime | None = None,
        refetch_weeks: int = 4,
    ) -> RunResult:
        result = RunResult(run_id=uuid.uuid4().hex, mode=mode)
        started = self._clock()
        # Runs left in 'running' were killed; the file lock guarantees no other writer.
        self._con.execute(
            "UPDATE load_runs SET status = 'aborted', finished_at = ? WHERE status = 'running'",
            [started],
        )
        self._con.execute(
            "INSERT INTO load_runs VALUES (?, ?, ?, NULL, 'running')",
            [result.run_id, mode, started],
        )
        crashed = True
        try:
            for s in series:
                self._run_series(result, s, since=since, refetch_weeks=refetch_weeks)
            crashed = False
        except SourceUnavailableError as exc:
            crashed = False
            result.errors.append(str(exc))
            result.recent_errors += 1
        finally:
            if crashed or result.recent_errors:
                result.status = "failed"
            else:
                result.status = "partial" if result.errors else "ok"
            self.finish(result.run_id, result.status)
        return result

    def finish(self, run_id: str, status: str) -> None:
        """Set the final status of a run (also used when a later step fails)."""
        self._con.execute(
            "UPDATE load_runs SET finished_at = ?, status = ? WHERE run_id = ?",
            [self._clock(), status, run_id],
        )

    def _run_series(
        self, result: RunResult, series: Series, *, since: datetime | None, refetch_weeks: int
    ) -> None:
        try:
            index = self._client.index(series)
            self._source_errors = 0
        except (SourceError, ChunkValidationError) as exc:
            self._log_error(result, series, None, f"index: {exc}", recent=True)
            self._count_source_error(exc)
            return

        latest = index[-1]
        if since is not None:
            since_ms = int(since.timestamp() * 1000)
            index = [ts for ts in index if chunk_end_ms(ts) > since_ms]
        known = self._known_chunks(series)
        incomplete = self._incomplete_chunks(series, known)
        refetch = set(index[-refetch_weeks:]) if refetch_weeks > 0 else set()
        targets = [ts for ts in index if ts not in known or ts in incomplete or ts in refetch]
        now_ms = int(self._clock().timestamp() * 1000)
        late_ms = now_ms - int(LATE_DATA_ALLOWANCE.total_seconds() * 1000)
        recent_ms = now_ms - int(RECENT.total_seconds() * 1000)

        for ts in targets:
            if self._progress is not None:
                self._progress(series.key, ts)
            end = chunk_end_ms(ts)
            try:
                raw = self._client.chunk(series, ts)
                self._source_errors = 0
                frame = parse_chunk(
                    series, ts, raw.payload, allow_trailing_nulls=ts == latest or end > late_ms
                )
            except (SourceError, ChunkValidationError) as exc:
                self._log_error(
                    result, series, ts, str(exc), recent=ts in refetch or end > recent_ms
                )
                self._count_source_error(exc)
                continue
            self._store(result, series, raw, frame)

    def _count_source_error(self, exc: Exception) -> None:
        if not isinstance(exc, SourceError):
            return
        self._source_errors += 1
        if self._source_errors >= MAX_CONSECUTIVE_SOURCE_ERRORS:
            raise SourceUnavailableError(
                f"source unavailable: {self._source_errors} consecutive request failures, "
                "run stopped"
            )

    def _known_chunks(self, series: Series) -> set[int]:
        rows = self._con.execute(
            "SELECT epoch_ms(chunk_ts) FROM raw_chunks WHERE series_key = ? AND resolution = ?",
            [series.key, RESOLUTION],
        ).fetchall()
        return {int(r[0]) for r in rows}

    def _incomplete_chunks(self, series: Series, known: set[int]) -> set[int]:
        """Stored chunks with fewer values than quarter hours in their week."""
        if not known:
            return set()
        stamps = self._con.execute(
            "SELECT epoch_ms(ts_utc) AS t FROM observations "
            "WHERE series_key = ? AND resolution = ? ORDER BY t",
            [series.key, RESOLUTION],
        ).fetchnumpy()["t"]
        starts = np.array(sorted(known), dtype=np.int64)
        ends = np.array([chunk_end_ms(int(t)) for t in starts], dtype=np.int64)
        present = np.searchsorted(stamps, ends) - np.searchsorted(stamps, starts)
        expected = (ends - starts) // QUARTER_HOUR_MS
        return {int(t) for t, n, e in zip(starts, present, expected, strict=True) if n < e}

    def _store(self, result: RunResult, series: Series, raw: RawChunk, frame: pl.DataFrame) -> None:
        chunk_at = ms_to_datetime(raw.chunk_ts)
        chunk_end = ms_to_datetime(chunk_end_ms(raw.chunk_ts))
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
            params = [series.key, RESOLUTION, chunk_at, chunk_end]
            removed_row = con.execute(
                """
                SELECT count(*) FROM observations o
                WHERE o.series_key = ? AND o.resolution = ? AND o.ts_utc >= ? AND o.ts_utc < ?
                AND NOT EXISTS (SELECT 1 FROM incoming i WHERE i.ts_utc = o.ts_utc)
                """,
                params,
            ).fetchone()
            removed = int(removed_row[0]) if removed_row else 0
            if removed:
                con.execute(
                    """
                    DELETE FROM observations
                    WHERE series_key = ? AND resolution = ? AND ts_utc >= ? AND ts_utc < ?
                    AND ts_utc NOT IN (SELECT ts_utc FROM incoming)
                    """,
                    params,
                )
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
            message = f"removed {removed} values no longer published" if removed else None
            self._log(
                result.run_id, series, raw.chunk_ts, status="loaded", rows=changed, message=message
            )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.unregister("incoming")
        result.loaded += 1
        result.rows_written += changed
        result.rows_removed += removed

    def _log_error(
        self,
        result: RunResult,
        series: Series,
        chunk_ts: int | None,
        message: str,
        *,
        recent: bool,
    ) -> None:
        result.errors.append(f"{series.key}: {message}")
        if recent:
            result.recent_errors += 1
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
