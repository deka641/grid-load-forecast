"""Read-only access to the serving snapshot, reopened when the file is replaced.

The pipeline publishes ``serving.duckdb`` by renaming a finished file over the
old one. A replacement shows up as a new ``(st_ino, st_mtime_ns)`` pair, and the
next request opens the new file. The previous connection stays in use until the
new one opened successfully, and it is never closed explicitly: requests still
running on it hold cursors, and the connection is freed once the last of them
is gone. A file that cannot be opened is tried once per ``(st_ino,
st_mtime_ns)`` pair, not on every request.

DuckDB caches database instances per path, so a second ``connect(path)`` while
the old file is still open would return the old data. Each snapshot is
therefore attached to a fresh in-memory instance. Every instance gets a small
thread pool and memory limit: DuckDB's defaults scale with the number of cores
and would exceed the container's process and memory limits.
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

INSTANCE_CONFIG: dict[str, str | bool | int | float | list[str]] = {
    "threads": 2,
    "async_threads": 1,
    "memory_limit": "128MB",
    "temp_directory": "/tmp/duckdb-api",  # noqa: S108 (tmpfs in the container)
    "max_temp_directory_size": "32MB",
    "autoload_known_extensions": False,
    "autoinstall_known_extensions": False,
    "allow_community_extensions": False,
}
MISSING = "no data snapshot is available yet; the pipeline has not published one"
UNOPENABLE = "the data snapshot cannot be opened"
NEWEST_UNOPENABLE = "the newest data snapshot file cannot be opened"


class SnapshotUnavailableError(RuntimeError):
    """The serving snapshot does not exist or cannot be opened."""


@dataclass(frozen=True)
class Snapshot:
    """An open snapshot, the modification time of its file and its series keys."""

    connection: duckdb.DuckDBPyConnection
    created_at: datetime
    series_keys: frozenset[str]


def _open(path: Path) -> tuple[duckdb.DuckDBPyConnection, frozenset[str]]:
    con = duckdb.connect(":memory:", config=INSTANCE_CONFIG)
    try:
        con.execute("ATTACH ? AS snap (READ_ONLY)", [str(path)])
        con.execute("SET GLOBAL TimeZone = 'UTC'")
        con.execute("SET enable_external_access = false")
        con.execute("SET lock_configuration = true")
        keys = frozenset(r[0] for r in con.execute("SELECT series_key FROM snap.series").fetchall())
    except duckdb.Error:
        con.close()
        raise
    return con, keys


class SnapshotStore:
    """Thread-safe provider of read-only cursors on the current snapshot."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._snapshot: Snapshot | None = None
        self._key: tuple[int, int] | None = None
        self._failed_key: tuple[int, int] | None = None
        self._error: str | None = None

    @property
    def path(self) -> Path:
        return self._path

    @property
    def error(self) -> str | None:
        """Why the newest snapshot file could not be opened, or None."""
        return self._error

    def _stat(self) -> os.stat_result | None:
        try:
            return self._path.stat()
        except FileNotFoundError:
            return None

    def _refresh(self) -> Snapshot:
        """Return the open snapshot, reopening it if the file changed. Needs the lock."""
        stat = self._stat()
        if stat is None:
            self._snapshot = None
            self._key = self._failed_key = None
            self._error = None
            raise SnapshotUnavailableError(MISSING)
        key = (stat.st_ino, stat.st_mtime_ns)
        if key not in (self._key, self._failed_key):
            try:
                con, series_keys = _open(self._path)
            except duckdb.Error as exc:
                log.warning("cannot open snapshot %s: %s", self._path, exc)
                self._failed_key = key
                self._error = NEWEST_UNOPENABLE
            else:
                created = datetime.fromtimestamp(stat.st_mtime_ns / 1e9, tz=UTC)
                self._snapshot = Snapshot(con, created, series_keys)
                self._key = key
                self._failed_key = None
                self._error = None
        if self._snapshot is None:
            raise SnapshotUnavailableError(UNOPENABLE)
        return self._snapshot

    def series_keys(self) -> frozenset[str] | None:
        """Series keys present in the snapshot, or None without a usable snapshot."""
        with self._lock:
            try:
                return self._refresh().series_keys
            except SnapshotUnavailableError:
                return None

    @contextmanager
    def cursor(self) -> Iterator[tuple[duckdb.DuckDBPyConnection, datetime]]:
        """Yield a private cursor on the snapshot and the snapshot's creation time."""
        with self._lock:
            snapshot = self._refresh()
            cur = snapshot.connection.cursor()
        try:
            cur.execute("USE snap")
            yield cur, snapshot.created_at
        finally:
            cur.close()
