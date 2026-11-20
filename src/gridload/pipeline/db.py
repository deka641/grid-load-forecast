"""DuckDB connection handling and versioned migrations."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

import duckdb

from gridload.series import CATALOG


def migrations() -> list[tuple[int, str]]:
    """Return ``(version, sql)`` for every bundled migration, in order."""
    files = resources.files("gridload.pipeline").joinpath("migrations")
    found = []
    for entry in files.iterdir():
        if entry.name.endswith(".sql"):
            found.append((int(entry.name.split("_", 1)[0]), entry.read_text("utf-8")))
    return sorted(found)


def migrate(con: duckdb.DuckDBPyConnection) -> int:
    """Apply pending migrations and sync the series catalog. Returns the schema version."""
    con.execute(
        "CREATE TABLE IF NOT EXISTS schema_version "
        "(version INTEGER PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL)"
    )
    row = con.execute("SELECT coalesce(max(version), 0) FROM schema_version").fetchone()
    current = int(row[0]) if row else 0
    for version, sql in migrations():
        if version <= current:
            continue
        con.execute("BEGIN")
        try:
            con.execute(sql)
            con.execute("INSERT INTO schema_version VALUES (?, ?)", [version, datetime.now(UTC)])
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        current = version
    con.executemany(
        "INSERT OR REPLACE INTO series VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            [s.key, s.filter_id, s.region, s.label, s.unit, s.category.value, s.aggregation.value]
            for s in CATALOG
        ],
    )
    return current


@contextmanager
def connect(path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    """Open the warehouse for writing and make sure the schema is current."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    try:
        con.execute("SET TimeZone = 'UTC'")
        migrate(con)
        yield con
    finally:
        con.close()
