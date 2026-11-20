"""Read-only access to the serving snapshot, reopened when the file is replaced.

The pipeline publishes ``serving.duckdb`` by renaming a finished file over the
old one. A replacement shows up as a new ``(st_ino, st_mtime_ns)`` pair, and the
next request opens the new file. The previous connection stays in use until the
new one opened successfully, and it is never closed explicitly: requests still
running on it hold cursors, and the connection is freed once the last of them
is gone.

DuckDB caches database instances per path, so a second ``connect(path)`` while
the old file is still open would return the old data. Each snapshot is
therefore attached to a fresh in-memory instance.
"""

import logging
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb

log = logging.getLogger(__name__)


class SnapshotUnavailableError(RuntimeError):
    """The serving snapshot does not exist or cannot be opened."""


@dataclass(frozen=True)
class Snapshot:
    """An open snapshot and the modification time of its file."""

    connection: duckdb.DuckDBPyConnection
    created_at: datetime


def _open(path: Path) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    try:
        con.execute("ATTACH ? AS snap (READ_ONLY)", [str(path)])
        con.execute("SET GLOBAL TimeZone = 'UTC'")
        con.execute("SET enable_external_access = false")
        con.execute("SET lock_configuration = true")
    except duckdb.Error:
        con.close()
        raise
    return con


class SnapshotStore:
    """Thread-safe provider of read-only cursors on the current snapshot."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._snapshot: Snapshot | None = None
        self._key: tuple[int, int] | None = None

    @property
    def path(self) -> Path:
        return self._path

    def _stat(self) -> os.stat_result | None:
        try:
            return self._path.stat()
        except FileNotFoundError:
            return None

    def _acquire(self) -> tuple[duckdb.DuckDBPyConnection, datetime]:
        """Open a cursor on the snapshot, reopening it first if the file was replaced."""
        with self._lock:
            stat = self._stat()
            if stat is None:
                self._snapshot = None
                self._key = None
                raise SnapshotUnavailableError(
                    "no data snapshot is available yet; the pipeline has not published one"
                )
            key = (stat.st_ino, stat.st_mtime_ns)
            if key != self._key:
                try:
                    con = _open(self._path)
                except duckdb.Error as exc:
                    log.warning("cannot open snapshot %s: %s", self._path, exc)
                    if self._snapshot is None:
                        raise SnapshotUnavailableError(
                            "the data snapshot cannot be opened"
                        ) from exc
                else:
                    created = datetime.fromtimestamp(stat.st_mtime_ns / 1e9, tz=UTC)
                    self._snapshot = Snapshot(con, created)
                    self._key = key
            assert self._snapshot is not None  # noqa: S101 (both branches above set or raise)
            return self._snapshot.connection.cursor(), self._snapshot.created_at

    @contextmanager
    def cursor(self) -> Iterator[tuple[duckdb.DuckDBPyConnection, datetime]]:
        """Yield a private cursor on the snapshot and the snapshot's creation time."""
        cur, created_at = self._acquire()
        try:
            cur.execute("USE snap")
            yield cur, created_at
        finally:
            cur.close()
