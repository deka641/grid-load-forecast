"""Backfill and incremental loading of SMARD chunks into the warehouse.

Each run fetches the chunk index per series and then loads:
- every chunk that is not yet stored (so a chunk that failed earlier is retried);
- every stored chunk that was last fetched while it was still open (before its
  end plus ``LATE_DATA_ALLOWANCE``), however old it is, so values published
  late are filled in even after a long downtime;
- the newest ``refetch_weeks`` chunks again, because SMARD corrects recent
  values. Corrections to older chunks are only picked up with a larger
  ``refetch_weeks`` (for example ``gridload ingest --refetch-weeks 9999``).

A chunk is processed in one transaction: raw payload, observations and the log
entry are written together. Unchanged payloads (same SHA-256) are skipped.
Changed values overwrite the stored value and increase its revision counter, so
there are never duplicate timestamps. In an open chunk, a short run of values at
its very end that the source no longer publishes (at most
``MAX_OPEN_WITHDRAWAL`` quarter hours, all newer than the newest value still
published) is removed as a revision. Any other stored value that turns null
points to a degraded response, unless withdrawals are accepted explicitly
(``gridload ingest --accept-withdrawals``): in an open chunk the stored values
are kept, new values are still written, the payload is not stored (so the
chunk is fetched again) and the chunk is reported as an error; in a completed
chunk nothing is written and the chunk is reported as an error. Quarter hours
that SMARD leaves out of a completed chunk (permanent gaps in the source) are
not stored as values; the load log records how many are missing and where the
gap starts. Any source or validation error is written to ``load_log`` and
nothing is stored for that chunk.

Run status: ``failed`` if the run crashed, the source was unavailable, an index
could not be read or a recent chunk (one that ended in the last 7 days, which
includes the open chunk of an active series) failed; ``partial`` if only older chunks failed,
including older chunks reloaded through ``refetch_weeks``; otherwise ``ok``.
"""

import uuid
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

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
    missing_points,
    ms_to_datetime,
    parse_chunk,
)

Progress = Callable[[str, int], None]

# The newest values lag real time by one to two hours, so a chunk that ended
# recently may still miss its last values while the next chunk already exists.
LATE_DATA_ALLOWANCE = timedelta(hours=6)
RECENT = timedelta(days=7)
# Values at the end of an open chunk that SMARD withdraws again (a revision of the
# newest quarter hours). Larger or interior removals are treated as a bad response.
MAX_OPEN_WITHDRAWAL = 8
MAX_CONSECUTIVE_SOURCE_ERRORS = 5


class SourceUnavailableError(RuntimeError):
    """Too many consecutive transient source errors; the rest of the run is skipped."""


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
        accept_withdrawals: bool = False,
    ) -> None:
        self._accept_withdrawals = accept_withdrawals
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
        finish: bool = True,
    ) -> RunResult:
        """Load the series and set the run status.

        With ``finish=False`` the run stays ``running`` and the caller sets the final
        status with :meth:`finish` after its own follow-up steps; a crash still marks
        the run failed here.
        """
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
            if finish or crashed:
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
            # One rule for every error of this chunk: recent means it ended within
            # RECENT. The newest chunk of an active series ends in the future; the
            # last chunk of an ended series ages out like any other.
            recent = end > recent_ms
            try:
                raw = self._client.chunk(series, ts)
                self._source_errors = 0
                is_open = end > late_ms
                frame = parse_chunk(series, ts, raw.payload, is_open=is_open)
            except (SourceError, ChunkValidationError) as exc:
                self._log_error(result, series, ts, str(exc), recent=recent)
                self._count_source_error(exc)
                continue
            missing, first_missing = missing_points(frame, ts, is_open=is_open)
            note = None
            if first_missing is not None:
                kind = "pending gap" if is_open else "source gap"
                first = ms_to_datetime(first_missing).isoformat()
                note = f"{kind}: {missing} quarter hours missing, first at {first}"
                if not is_open and end > recent_ms:
                    # A gap in a week that just ended is worth a look: run becomes partial.
                    result.errors.append(f"{series.key}: {note}")
                    self._log(result.run_id, series, ts, status="warning", rows=0, message=note)
            self._store(result, series, raw, frame, note=note, is_open=is_open, recent=recent)

    def _count_source_error(self, exc: Exception) -> None:
        if not (isinstance(exc, SourceError) and exc.transient):
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
        """Stored chunks last fetched while still open (their tail may have been missing).

        A chunk fetched after its end plus ``LATE_DATA_ALLOWANCE`` is final: gaps in
        it are permanent and only rechecked through ``refetch_weeks``.
        """
        if not known:
            return set()
        rows = self._con.execute(
            "SELECT epoch_ms(chunk_ts), epoch_ms(fetched_at) FROM raw_chunks "
            "WHERE series_key = ? AND resolution = ?",
            [series.key, RESOLUTION],
        ).fetchall()
        allowance = int(LATE_DATA_ALLOWANCE.total_seconds() * 1000)
        return {int(ts) for ts, fetched in rows if int(fetched) < chunk_end_ms(int(ts)) + allowance}

    def _store(
        self,
        result: RunResult,
        series: Series,
        raw: RawChunk,
        frame: pl.DataFrame,
        *,
        note: str | None = None,
        is_open: bool = True,
        recent: bool = True,
    ) -> None:
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
            # Record the fetch time: an unchanged chunk fetched after it closed is final.
            con.execute(
                "UPDATE raw_chunks SET fetched_at = ? "
                "WHERE series_key = ? AND resolution = ? AND chunk_ts = ?",
                [now, series.key, RESOLUTION, chunk_at],
            )
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
                SELECT count(*),
                    count(*) FILTER (WHERE o.ts_utc <= (SELECT max(ts_utc) FROM incoming))
                FROM observations o
                WHERE o.series_key = ? AND o.resolution = ? AND o.ts_utc >= ? AND o.ts_utc < ?
                AND NOT EXISTS (SELECT 1 FROM incoming i WHERE i.ts_utc = o.ts_utc)
                """,
                params,
            ).fetchone()
            removed = int(removed_row[0]) if removed_row else 0
            # Removed values at or before the newest value still published.
            interior = int(removed_row[1]) if removed_row else 0
            # Only a short tail may vanish from an open week; anything more (for
            # example a response with every value null) is not a revision.
            suspicious = (
                removed > MAX_OPEN_WITHDRAWAL or interior > 0 or (removed > 0 and frame.is_empty())
            )
            rejected = is_open and suspicious and not self._accept_withdrawals
            if removed and not is_open and not self._accept_withdrawals:
                # A completed week whose stored values turn null points to a degraded
                # response, not a correction: keep the stored values and report it.
                raise ChunkValidationError(
                    f"{series.key} chunk {raw.chunk_ts}: {removed} stored values are now "
                    "missing in a completed chunk; kept the stored values"
                )
            if removed and not rejected:
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
            if rejected:
                # Keep the stored values but take the new ones; the payload is not
                # stored, so the chunk is fetched again by the next run.
                self._log_error(
                    result,
                    series,
                    raw.chunk_ts,
                    f"{series.key} chunk {raw.chunk_ts}: {removed} stored values are now "
                    f"missing in an open chunk (more than {MAX_OPEN_WITHDRAWAL}, not at its "
                    f"end or no value left); kept the stored values, wrote {changed} new values",
                    recent=recent,
                )
                con.execute("COMMIT")
                result.rows_written += changed
                return
            con.execute(
                "INSERT OR REPLACE INTO raw_chunks VALUES (?, ?, ?, ?, ?, ?)",
                [series.key, RESOLUTION, chunk_at, now, raw.sha256, zlib.compress(raw.payload, 9)],
            )
            notes = [note] if note else []
            if removed:
                notes.append(f"removed {removed} values no longer published")
            message = "; ".join(notes) or None
            self._log(
                result.run_id, series, raw.chunk_ts, status="loaded", rows=changed, message=message
            )
            con.execute("COMMIT")
        except ChunkValidationError as exc:
            con.execute("ROLLBACK")
            self._log_error(result, series, raw.chunk_ts, str(exc), recent=recent)
            return
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
