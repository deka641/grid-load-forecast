import json
import os
import zlib
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pytest

from gridload.pipeline import db, snapshot
from gridload.pipeline.db import connect, migrate
from gridload.pipeline.ingest import Ingestor
from gridload.series import BY_KEY, CATALOG, Series, select
from gridload.sources.smard import SmardClient, chunk_end_ms
from tests.fake_smard import FakeSmard, chunk_name, fixture_bytes, recorded_chunks

LOAD = BY_KEY["load"]
PRICE = BY_KEY["price_day_ahead"]
SPRING = 1742770800000
AUTUMN = 1760911200000
PRE_SWITCH = 1758492000000
SWITCH = 1759096800000
REGULAR = 1794178800000
# SMARD rounds every published value to two decimals. A sum of four rounded quarter
# hours can therefore differ from the rounded hourly value by up to 4 x 0.005.
ROUNDING = 0.02 + 1e-9


@pytest.fixture
def con() -> Iterator[duckdb.DuckDBPyConnection]:
    connection = duckdb.connect()
    connection.execute("SET TimeZone = 'UTC'")
    migrate(connection)
    yield connection
    connection.close()


@pytest.fixture
def fake() -> FakeSmard:
    smard = FakeSmard()
    for series in CATALOG:
        smard.use_index(series, recorded_chunks(series))
    return smard


def run(con: duckdb.DuckDBPyConnection, client: SmardClient, series: list[Series], **kw: object):  # type: ignore[no-untyped-def]
    clock = kw.pop("clock", None)
    ingestor = Ingestor(con, client) if clock is None else Ingestor(con, client, clock=clock)  # type: ignore[arg-type]
    return ingestor.run(series, mode=str(kw.pop("mode", "backfill")), **kw)  # type: ignore[arg-type]


def _day_after(chunk_ts: int) -> datetime:
    """A moment one day after the chunk ended, so it counts as recent."""
    return datetime.fromtimestamp(chunk_end_ms(chunk_ts) / 1000, UTC) + timedelta(days=1)


def count(con: duckdb.DuckDBPyConnection, sql: str, params: list[object] | None = None) -> int:
    row = con.execute(sql, params or []).fetchone()
    assert row is not None
    return int(row[0])


def test_backfill_loads_all_recorded_chunks(
    con: duckdb.DuckDBPyConnection, fake: FakeSmard
) -> None:
    result = run(con, fake.client(), select())
    assert result.status == "ok"
    assert result.errors == []
    assert result.loaded == sum(len(recorded_chunks(s)) for s in CATALOG)
    assert count(con, "SELECT count(*) FROM observations") == result.rows_written
    duplicates = count(
        con,
        "SELECT count(*) FROM (SELECT 1 FROM observations "
        "GROUP BY series_key, resolution, ts_utc HAVING count(*) > 1)",
    )
    assert duplicates == 0
    statuses = con.execute("SELECT DISTINCT status FROM load_log").fetchall()
    assert statuses == [("loaded",)]
    assert con.execute("SELECT status FROM load_runs").fetchall() == [("ok",)]


def test_raw_payload_is_stored_verbatim(con: duckdb.DuckDBPyConnection, fake: FakeSmard) -> None:
    run(con, fake.client(), [LOAD])
    row = con.execute(
        "SELECT payload FROM raw_chunks WHERE series_key = 'load' AND epoch_ms(chunk_ts) = ?",
        [REGULAR],
    ).fetchone()
    assert row is not None
    assert zlib.decompress(row[0]) == fixture_bytes(chunk_name(LOAD, REGULAR))


def test_second_run_is_idempotent(con: duckdb.DuckDBPyConnection, fake: FakeSmard) -> None:
    first = run(con, fake.client(), [LOAD])
    before = count(con, "SELECT count(*) FROM observations")
    second = run(con, fake.client(), [LOAD], mode="incremental")
    assert second.status == "ok"
    assert second.loaded == 0
    assert second.unchanged == min(4, first.loaded)
    assert count(con, "SELECT count(*) FROM observations") == before
    assert count(con, "SELECT max(revision) FROM observations") == 0


def test_incremental_refetches_only_recent_and_missing_chunks(
    con: duckdb.DuckDBPyConnection, fake: FakeSmard
) -> None:
    run(con, fake.client(), [LOAD])
    fake.requests.clear()
    run(con, fake.client(), [LOAD], mode="incremental", refetch_weeks=1)
    chunks = [p for p in fake.requests if "index" not in p]
    assert chunks == [f"/app/chart_data/410/DE/{chunk_name(LOAD, recorded_chunks(LOAD)[-1])}"]


def test_corrections_overwrite_and_count_revisions(
    con: duckdb.DuckDBPyConnection, fake: FakeSmard
) -> None:
    run(con, fake.client(), [LOAD])
    name = chunk_name(LOAD, REGULAR)
    data = json.loads(fixture_bytes(name))
    original = data["series"][10][1]
    data["series"][10][1] = original + 1.5
    fake.override(name, json.dumps(data).encode())

    result = run(con, fake.client(), [LOAD], mode="incremental")
    assert result.rows_written == 1
    row = con.execute(
        "SELECT value, revision FROM observations WHERE series_key = 'load' "
        "AND epoch_ms(ts_utc) = ?",
        [data["series"][10][0]],
    ).fetchone()
    assert row == (pytest.approx(original + 1.5), 1)
    duplicates = count(
        con,
        "SELECT count(*) - count(DISTINCT ts_utc) FROM observations WHERE series_key = 'load'",
    )
    assert duplicates == 0


def test_newest_chunk_grows_without_duplicates(
    con: duckdb.DuckDBPyConnection, fake: FakeSmard
) -> None:
    latest = recorded_chunks(LOAD)[-1]
    name = chunk_name(LOAD, latest)
    data = json.loads(fixture_bytes(name))
    filled = sum(v is not None for _, v in data["series"])
    shorter = json.loads(fixture_bytes(name))
    for point in shorter["series"][filled - 8 :]:
        point[1] = None
    fake.override(name, json.dumps(shorter).encode())
    run(con, fake.client(), [LOAD])
    in_db = count(
        con,
        "SELECT count(*) FROM observations WHERE ts_utc >= make_timestamptz(? * 1000)",
        [latest],
    )
    assert in_db == filled - 8

    fake.responses.clear()
    result = run(con, fake.client(), [LOAD], mode="incremental")
    assert result.rows_written == 8
    in_db = count(
        con,
        "SELECT count(*) FROM observations WHERE ts_utc >= make_timestamptz(? * 1000)",
        [latest],
    )
    assert in_db == filled


@pytest.mark.parametrize(
    ("body", "status", "fragment"),
    [
        (b"", 200, "empty response body"),
        (b'{"series": []}', 200, "empty series"),
        (b"gateway timeout", 504, "HTTP 504"),
        (b"not found", 404, "HTTP 404"),
    ],
)
def test_bad_chunk_is_logged_and_never_stored_as_no_data(
    con: duckdb.DuckDBPyConnection, fake: FakeSmard, body: bytes, status: int, fragment: str
) -> None:
    fake.override(chunk_name(LOAD, REGULAR), body, status)
    result = run(con, fake.client(retries=0), [LOAD, PRICE], clock=_clock_at(_day_after(REGULAR)))
    assert result.status == "failed"
    assert len(result.errors) == 1
    assert fragment in result.errors[0]
    log = con.execute(
        "SELECT series_key, epoch_ms(chunk_ts), status, message FROM load_log "
        "WHERE status = 'error'"
    ).fetchall()
    assert log[0][:3] == ("load", REGULAR, "error")
    assert fragment in log[0][3]
    stored = count(
        con,
        "SELECT count(*) FROM raw_chunks WHERE series_key = 'load' AND epoch_ms(chunk_ts) = ?",
        [REGULAR],
    )
    assert stored == 0
    # The other chunks and series are still loaded.
    assert count(con, "SELECT count(*) FROM raw_chunks WHERE series_key = 'price_day_ahead'") > 0
    assert con.execute("SELECT status FROM load_runs").fetchall() == [("failed",)]


def test_truncated_chunk_keeps_previous_values(
    con: duckdb.DuckDBPyConnection, fake: FakeSmard
) -> None:
    run(con, fake.client(), [LOAD])
    before = count(con, "SELECT count(*) FROM observations")
    name = chunk_name(LOAD, REGULAR)
    data = json.loads(fixture_bytes(name))
    data["series"] = data["series"][:300]
    fake.override(name, json.dumps(data).encode())
    result = run(
        con, fake.client(), [LOAD], mode="incremental", clock=_clock_at(_day_after(REGULAR))
    )
    assert result.status == "failed"
    assert count(con, "SELECT count(*) FROM observations") == before


def test_failed_chunk_is_retried_by_the_next_run(
    con: duckdb.DuckDBPyConnection, fake: FakeSmard
) -> None:
    fake.override(chunk_name(LOAD, SPRING), b"", 200)
    run(con, fake.client(), [LOAD])
    fake.responses.clear()
    result = run(con, fake.client(), [LOAD], mode="incremental", refetch_weeks=0)
    assert result.status == "ok"
    assert result.loaded == 1


def test_index_error_is_logged(con: duckdb.DuckDBPyConnection, fake: FakeSmard) -> None:
    fake.override("index_quarterhour.json", b"{}")
    result = run(con, fake.client(), [LOAD])
    assert result.status == "failed"
    assert "index" in result.errors[0]
    row = con.execute("SELECT chunk_ts, status FROM load_log").fetchone()
    assert row == (None, "error")


def test_since_limits_the_backfill(con: duckdb.DuckDBPyConnection, fake: FakeSmard) -> None:
    result = run(con, fake.client(), [LOAD], since=datetime(2026, 1, 1, tzinfo=UTC))
    assert result.loaded == 2


def test_week_containing_since_is_included(con: duckdb.DuckDBPyConnection, fake: FakeSmard) -> None:
    result = run(con, fake.client(), [LOAD], since=datetime(2025, 3, 30, tzinfo=UTC))
    assert result.loaded == 4


@pytest.mark.parametrize("finish", [True, False])
def test_unexpected_exception_marks_run_failed(
    con: duckdb.DuckDBPyConnection, finish: bool
) -> None:
    class Exploding(SmardClient):
        def __init__(self) -> None:
            pass

        def index(self, series: Series) -> list[int]:
            raise RuntimeError("bug")

    with pytest.raises(RuntimeError):
        Ingestor(con, Exploding()).run([LOAD], mode="backfill", finish=finish)
    rows = con.execute("SELECT status, finished_at IS NOT NULL FROM load_runs").fetchall()
    assert rows == [("failed", True)]


def test_progress_callback_sees_every_chunk(
    con: duckdb.DuckDBPyConnection, fake: FakeSmard
) -> None:
    seen: list[tuple[str, int]] = []
    Ingestor(con, fake.client(), progress=lambda s, ts: seen.append((s, ts))).run(
        [LOAD], mode="backfill"
    )
    assert seen == [("load", ts) for ts in recorded_chunks(LOAD)]


# Hourly values ------------------------------------------------------------------------


def hourly_from_db(con: duckdb.DuckDBPyConnection, key: str, chunk: int) -> list[tuple[int, float]]:
    rows = con.execute(
        "SELECT epoch_ms(ts_utc), value FROM hourly WHERE series_key = ? "
        "AND ts_utc >= make_timestamptz(? * 1000) AND ts_utc < make_timestamptz(? * 1000) "
        "ORDER BY 1",
        [key, chunk, chunk_end_ms(chunk)],
    ).fetchall()
    return [(int(r[0]), float(r[1])) for r in rows]


def published_hourly(series: Series, chunk: int) -> list[tuple[int, float]]:
    data = json.loads(fixture_bytes(chunk_name(series, chunk, "hour")))
    return [(int(t), float(v)) for t, v in data["series"] if v is not None]


@pytest.mark.parametrize("chunk", [SPRING, AUTUMN])
def test_hourly_load_equals_published_hourly_series(
    con: duckdb.DuckDBPyConnection, fake: FakeSmard, chunk: int
) -> None:
    run(con, fake.client(), [LOAD])
    derived = hourly_from_db(con, "load", chunk)
    published = published_hourly(LOAD, chunk)
    assert len(derived) == {SPRING: 167, AUTUMN: 169}[chunk]
    assert [t for t, _ in derived] == [t for t, _ in published]
    for (_, mine), (_, theirs) in zip(derived, published, strict=True):
        assert mine == pytest.approx(theirs, abs=ROUNDING)


@pytest.mark.parametrize("chunk", [PRE_SWITCH, SWITCH])
def test_hourly_price_equals_published_hourly_series(
    con: duckdb.DuckDBPyConnection, fake: FakeSmard, chunk: int
) -> None:
    run(con, fake.client(), [PRICE])
    derived = hourly_from_db(con, "price_day_ahead", chunk)
    published = published_hourly(PRICE, chunk)
    assert len(derived) == 168
    for (t1, mine), (t2, theirs) in zip(derived, published, strict=True):
        assert t1 == t2
        assert mine == pytest.approx(theirs, abs=0.01)


def test_before_the_switch_quarter_hour_prices_repeat_the_hourly_price(
    con: duckdb.DuckDBPyConnection, fake: FakeSmard
) -> None:
    run(con, fake.client(), [PRICE])
    spread = con.execute(
        "SELECT max(hi - lo) FROM (SELECT max(value) hi, min(value) lo FROM observations "
        "WHERE series_key = 'price_day_ahead' AND ts_utc >= make_timestamptz(? * 1000) "
        "AND ts_utc < make_timestamptz(? * 1000) GROUP BY time_bucket(INTERVAL 1 HOUR, ts_utc))",
        [PRE_SWITCH, SWITCH],
    ).fetchone()
    assert spread == (0.0,)


def test_incomplete_hours_are_not_reported(con: duckdb.DuckDBPyConnection, fake: FakeSmard) -> None:
    solar = BY_KEY["gen_solar"]
    run(con, fake.client(), [solar])
    latest = recorded_chunks(solar)[-1]
    quarters = count(
        con,
        "SELECT count(*) FROM observations WHERE series_key = 'gen_solar' "
        "AND ts_utc >= make_timestamptz(? * 1000)",
        [latest],
    )
    hours = count(
        con,
        "SELECT count(*) FROM hourly WHERE series_key = 'gen_solar' "
        "AND ts_utc >= make_timestamptz(? * 1000)",
        [latest],
    )
    assert quarters % 4 == 1  # one partial hour at the end
    assert hours == quarters // 4


# Database and snapshot ----------------------------------------------------------------


def test_migrations_are_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "w.duckdb"
    with connect(path) as first:
        version = count(first, "SELECT max(version) FROM schema_version")
    with connect(path) as second:
        assert count(second, "SELECT count(*) FROM schema_version") == version
        assert count(second, "SELECT count(*) FROM series") == len(CATALOG)


def test_failed_migration_rolls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        db, "migrations", lambda: [(1, "CREATE TABLE ok (a INT); SELECT * FROM nope")]
    )
    connection = duckdb.connect()
    with pytest.raises(duckdb.CatalogException):
        db.migrate(connection)
    tables = connection.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_name = 'ok'"
    ).fetchall()
    assert tables == []


def test_snapshot_is_complete_and_atomic(
    tmp_path: Path, con: duckdb.DuckDBPyConnection, fake: FakeSmard
) -> None:
    run(con, fake.client(), [LOAD, PRICE])
    target = tmp_path / "serving.duckdb"
    target.write_bytes(b"old")
    snapshot.publish(con, target)
    assert not (tmp_path / "serving.duckdb.tmp").exists()
    reader = duckdb.connect(str(target), read_only=True)
    try:
        assert count(reader, "SELECT count(*) FROM observations") == count(
            con, "SELECT count(*) FROM observations"
        )
        assert count(reader, "SELECT count(*) FROM hourly") == count(
            con, "SELECT count(*) FROM hourly"
        )
        tables = {r[0] for r in reader.execute("SHOW TABLES").fetchall()}
        assert "raw_chunks" not in tables
        assert {"load_log", "load_runs", "forecasts", "backtest_metrics"} <= tables
    finally:
        reader.close()
    snapshot.publish(con, target)  # a second publish replaces the file


def test_store_rolls_back_on_database_error(
    con: duckdb.DuckDBPyConnection, fake: FakeSmard
) -> None:
    class FailingRawInsert:
        def __init__(self, inner: duckdb.DuckDBPyConnection) -> None:
            self._inner = inner

        def execute(self, sql: str, params: object = None) -> duckdb.DuckDBPyConnection:
            if sql.startswith("INSERT OR REPLACE INTO raw_chunks"):
                raise duckdb.IOException("disk full")
            return self._inner.execute(sql, params)

        def __getattr__(self, name: str) -> object:
            return getattr(self._inner, name)

    proxy = FailingRawInsert(con)
    with pytest.raises(duckdb.IOException):
        Ingestor(proxy, fake.client()).run([LOAD], mode="backfill")  # type: ignore[arg-type]
    assert count(con, "SELECT count(*) FROM observations") == 0
    assert count(con, "SELECT count(*) FROM load_log") == 0


# Review regressions --------------------------------------------------------------------


def _with_trailing_nulls(name: str, keep: int) -> bytes:
    data = json.loads(fixture_bytes(name))
    for point in data["series"][keep:]:
        point[1] = None
    return json.dumps(data).encode()


def _clock_at(moment: datetime):  # type: ignore[no-untyped-def]
    def clock() -> datetime:
        return moment

    return clock


def test_chunk_stored_while_open_is_completed_even_after_long_downtime(fake: FakeSmard) -> None:
    week_end = datetime.fromtimestamp(chunk_end_ms(REGULAR) / 1000, UTC)
    name = chunk_name(LOAD, REGULAR)
    connection = duckdb.connect()
    connection.execute("SET TimeZone = 'UTC'")
    migrate(connection)
    fake.use_index(LOAD, [REGULAR])
    fake.override(name, _with_trailing_nulls(name, 100))
    first = Ingestor(connection, fake.client(), clock=_clock_at(week_end - timedelta(hours=2)))
    first.run([LOAD], mode="backfill")
    assert count(connection, "SELECT count(*) FROM observations") == 100

    # Two months later, five newer weeks exist (all failing), the chunk is far
    # outside the refetch window but was stored while open, so it is reloaded.
    later = week_end + timedelta(days=60)
    newer = [REGULAR + i * 7 * 86_400_000 for i in range(1, 6)]
    for ts in newer:
        fake.override(chunk_name(LOAD, ts), b"", 404)
    fake.use_index(LOAD, [REGULAR, *newer])
    fake.responses.pop(name)
    Ingestor(connection, fake.client(retries=0), clock=_clock_at(later)).run(
        [LOAD], mode="incremental", refetch_weeks=1
    )
    complete = count(
        connection,
        "SELECT count(*) FROM observations WHERE ts_utc < make_timestamptz(? * 1000)",
        [newer[0]],
    )
    assert complete == 672

    # Fetched after it closed: final, not requested again.
    fake.requests.clear()
    Ingestor(connection, fake.client(retries=0), clock=_clock_at(later + timedelta(hours=1))).run(
        [LOAD], mode="incremental", refetch_weeks=1
    )
    assert f"/app/chart_data/410/DE/{name}" not in fake.requests
    connection.close()


def test_unchanged_refetch_after_close_marks_the_chunk_final(fake: FakeSmard) -> None:
    week_end = datetime.fromtimestamp(chunk_end_ms(REGULAR) / 1000, UTC)
    name = chunk_name(LOAD, REGULAR)
    partial = _with_trailing_nulls(name, 600)
    connection = duckdb.connect()
    connection.execute("SET TimeZone = 'UTC'")
    migrate(connection)
    fake.use_index(LOAD, [REGULAR, recorded_chunks(LOAD)[-1]])
    fake.override(name, partial)
    Ingestor(connection, fake.client(), clock=_clock_at(week_end - timedelta(hours=1))).run(
        [LOAD], mode="backfill", refetch_weeks=0
    )
    # SMARD never fills the tail: the unchanged response after close is final.
    Ingestor(connection, fake.client(), clock=_clock_at(week_end + timedelta(days=1))).run(
        [LOAD], mode="incremental", refetch_weeks=0
    )
    fake.requests.clear()
    Ingestor(connection, fake.client(), clock=_clock_at(week_end + timedelta(days=2))).run(
        [LOAD], mode="incremental", refetch_weeks=0
    )
    assert f"/app/chart_data/410/DE/{name}" not in fake.requests
    connection.close()


def test_values_withdrawn_from_a_completed_chunk_are_kept_and_reported(
    con: duckdb.DuckDBPyConnection, fake: FakeSmard
) -> None:
    run(con, fake.client(), [LOAD])
    before = count(con, "SELECT count(*) FROM observations")
    name = chunk_name(LOAD, AUTUMN)
    data = json.loads(fixture_bytes(name))
    for point in data["series"][200:296]:
        point[1] = None
    fake.override(name, json.dumps(data).encode())
    result = run(con, fake.client(), [LOAD], mode="incremental", refetch_weeks=4)
    assert result.status in {"partial", "failed"}
    assert any("now missing in a completed chunk" in e for e in result.errors)
    assert count(con, "SELECT count(*) FROM observations") == before


def test_trailing_nulls_are_pending_shortly_after_the_week_and_a_gap_later(
    fake: FakeSmard,
) -> None:
    week_end = datetime.fromtimestamp(chunk_end_ms(REGULAR) / 1000, UTC)
    name = chunk_name(LOAD, REGULAR)
    fake.use_index(LOAD, [REGULAR, recorded_chunks(LOAD)[-1]])
    fake.override(name, _with_trailing_nulls(name, 668))
    cases = ((2, None, "ok"), (8, "source gap: 4 quarter hours missing, first at ", "partial"))
    for hours_after, message, status in cases:
        connection = duckdb.connect()
        connection.execute("SET TimeZone = 'UTC'")
        migrate(connection)
        now = week_end + timedelta(hours=hours_after)

        def clock(at: datetime = now) -> datetime:
            return at

        result = Ingestor(connection, fake.client(), clock=clock).run([LOAD], mode="backfill")
        assert result.status == status
        row = connection.execute(
            "SELECT message FROM load_log WHERE epoch_ms(chunk_ts) = ?", [REGULAR]
        ).fetchone()
        assert row is not None
        if message is None:
            assert row[0] is None
        else:
            assert row[0].startswith(message)
        connection.close()


def test_permanent_gap_is_logged_and_not_refetched(fake: FakeSmard) -> None:
    name = chunk_name(LOAD, SPRING)
    data = json.loads(fixture_bytes(name))
    for point in data["series"][100:196]:
        point[1] = None
    fake.override(name, json.dumps(data).encode())
    fake.use_index(LOAD, [SPRING, *recorded_chunks(LOAD)[1:]])
    connection = duckdb.connect()
    connection.execute("SET TimeZone = 'UTC'")
    migrate(connection)
    result = Ingestor(connection, fake.client()).run([LOAD], mode="backfill", refetch_weeks=1)
    assert result.status == "ok"
    logged = connection.execute(
        "SELECT message FROM load_log WHERE epoch_ms(chunk_ts) = ?", [SPRING]
    ).fetchone()
    assert logged is not None
    assert logged[0].startswith("source gap: 96 quarter hours missing, first at 2025-03-25")
    stored = count(
        connection,
        "SELECT count(*) FROM observations WHERE ts_utc >= make_timestamptz(? * 1000) "
        "AND ts_utc < make_timestamptz(? * 1000)",
        [SPRING, chunk_end_ms(SPRING)],
    )
    assert stored == 668 - 96
    fake.requests.clear()
    Ingestor(connection, fake.client()).run([LOAD], mode="incremental", refetch_weeks=1)
    assert f"/app/chart_data/410/DE/{name}" not in fake.requests
    connection.close()


def test_old_chunk_errors_make_a_partial_run(
    con: duckdb.DuckDBPyConnection, fake: FakeSmard
) -> None:
    fake.override(chunk_name(LOAD, SPRING), b"")
    result = run(con, fake.client(), [LOAD], refetch_weeks=2)
    assert result.status == "partial"
    assert result.recent_errors == 0
    fake.override(chunk_name(LOAD, recorded_chunks(LOAD)[-1]), b"")
    assert run(con, fake.client(), [LOAD], mode="incremental", refetch_weeks=2).status == "failed"


def test_values_no_longer_published_are_removed(
    con: duckdb.DuckDBPyConnection, fake: FakeSmard
) -> None:
    latest = recorded_chunks(LOAD)[-1]
    name = chunk_name(LOAD, latest)
    run(con, fake.client(), [LOAD])
    full = count(
        con,
        "SELECT count(*) FROM observations WHERE ts_utc >= make_timestamptz(? * 1000)",
        [latest],
    )
    fake.override(name, _with_trailing_nulls(name, full - 3))
    result = run(con, fake.client(), [LOAD], mode="incremental")
    assert result.rows_removed == 3
    after = count(
        con,
        "SELECT count(*) FROM observations WHERE ts_utc >= make_timestamptz(? * 1000)",
        [latest],
    )
    assert after == full - 3
    message = con.execute(
        "SELECT message FROM load_log WHERE message IS NOT NULL AND status = 'loaded'"
    ).fetchone()
    assert message == ("removed 3 values no longer published",)


def test_run_stops_when_the_source_is_down(con: duckdb.DuckDBPyConnection) -> None:
    fake = FakeSmard()
    fake.override("index_quarterhour.json", b"down", 503)
    result = run(con, fake.client(retries=0), select())
    assert result.status == "failed"
    assert "source unavailable" in result.errors[-1]
    assert len(fake.requests) == 5


def test_stale_running_rows_are_marked_aborted(
    con: duckdb.DuckDBPyConnection, fake: FakeSmard
) -> None:
    con.execute(
        "INSERT INTO load_runs VALUES ('old', 'backfill', ?, NULL, 'running')",
        [datetime(2026, 1, 1, tzinfo=UTC)],
    )
    run(con, fake.client(), [LOAD])
    assert con.execute("SELECT status FROM load_runs WHERE run_id = 'old'").fetchone() == (
        "aborted",
    )


def test_reader_keeps_old_snapshot_and_failed_publish_keeps_file(
    tmp_path: Path, con: duckdb.DuckDBPyConnection, fake: FakeSmard, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "serving.duckdb"
    run(con, fake.client(), [LOAD])
    snapshot.publish(con, target)
    reader = duckdb.connect(str(target), read_only=True)
    before = count(reader, "SELECT count(*) FROM observations")
    run(con, fake.client(), [PRICE])
    snapshot.publish(con, target)
    assert count(reader, "SELECT count(*) FROM observations") == before
    reader.close()
    published = target.read_bytes()

    monkeypatch.setattr(snapshot, "SERVING_TABLES", ("schema_version", "missing_table"))
    with pytest.raises(duckdb.CatalogException):
        snapshot.publish(con, target)
    assert target.read_bytes() == published
    assert sorted(p.name for p in tmp_path.iterdir()) == ["serving.duckdb"]


def test_missing_files_do_not_trip_the_source_breaker(
    con: duckdb.DuckDBPyConnection, fake: FakeSmard
) -> None:
    berlin = ZoneInfo("Europe/Berlin")
    mondays = [date(2026, 11, 9) - timedelta(weeks=i) for i in range(6, 0, -1)]
    stamps = [
        int(datetime(d.year, d.month, d.day, tzinfo=berlin).timestamp() * 1000) for d in mondays
    ]
    fake.use_index(LOAD, [*stamps, REGULAR])
    result = run(con, fake.client(), [LOAD, PRICE], refetch_weeks=0)
    assert len([e for e in result.errors if "HTTP 404" in e]) == 6
    assert not any("source unavailable" in e for e in result.errors)
    assert count(con, "SELECT count(*) FROM raw_chunks WHERE series_key = 'price_day_ahead'") > 0


def test_warehouse_connection_has_resource_limits(tmp_path: Path) -> None:
    before = len(os.listdir("/proc/self/task"))
    with connect(tmp_path / "w.duckdb") as con:
        # DuckDB defaults would add about five threads per host core.
        assert len(os.listdir("/proc/self/task")) - before <= 8
        settings = con.execute(
            "SELECT current_setting('threads'), current_setting('async_threads'), "
            "current_setting('memory_limit'), current_setting('temp_directory')"
        ).fetchone()
        assert settings is not None
        assert settings[:2] == (4, 1)
        assert settings[2] == "953.6 MiB"
        assert settings[3] == str(tmp_path / "duckdb-tmp")


def test_withdrawals_can_be_accepted_explicitly(
    con: duckdb.DuckDBPyConnection, fake: FakeSmard
) -> None:
    run(con, fake.client(), [LOAD])
    before = count(con, "SELECT count(*) FROM observations")
    name = chunk_name(LOAD, AUTUMN)
    data = json.loads(fixture_bytes(name))
    for point in data["series"][200:296]:
        point[1] = None
    fake.override(name, json.dumps(data).encode())
    result = Ingestor(con, fake.client(), accept_withdrawals=True).run(
        [LOAD], mode="incremental", refetch_weeks=4
    )
    assert result.rows_removed == 96
    assert count(con, "SELECT count(*) FROM observations") == before - 96


def test_recent_gap_is_logged_as_warning(fake: FakeSmard) -> None:
    week_end = datetime.fromtimestamp(chunk_end_ms(REGULAR) / 1000, UTC)
    name = chunk_name(LOAD, REGULAR)
    fake.use_index(LOAD, [REGULAR, recorded_chunks(LOAD)[-1]])
    fake.override(name, _with_trailing_nulls(name, 668))
    connection = duckdb.connect()
    connection.execute("SET TimeZone = 'UTC'")
    migrate(connection)
    Ingestor(connection, fake.client(), clock=_clock_at(week_end + timedelta(days=1))).run(
        [LOAD], mode="backfill"
    )
    rows = connection.execute("SELECT status FROM load_log WHERE status = 'warning'").fetchall()
    assert rows == [("warning",)]
    connection.close()


def _newest_stored(con: duckdb.DuckDBPyConnection) -> int:
    return count(
        con,
        "SELECT count(*) FROM observations WHERE ts_utc >= make_timestamptz(? * 1000)",
        [recorded_chunks(LOAD)[-1]],
    )


def test_all_null_newest_chunk_keeps_the_stored_values(
    con: duckdb.DuckDBPyConnection, fake: FakeSmard
) -> None:
    latest = recorded_chunks(LOAD)[-1]
    name = chunk_name(LOAD, latest)
    run(con, fake.client(), [LOAD])
    before = count(con, "SELECT count(*) FROM observations")
    sha_sql = "SELECT sha256 FROM raw_chunks WHERE series_key = 'load' AND epoch_ms(chunk_ts) = ?"
    sha = con.execute(sha_sql, [latest]).fetchone()
    fake.override(name, _with_trailing_nulls(name, 0))

    result = run(con, fake.client(), [LOAD], mode="incremental")
    assert result.status == "failed"
    assert result.rows_removed == 0
    assert any("missing in an open chunk" in e for e in result.errors)
    assert count(con, "SELECT count(*) FROM observations") == before
    assert con.execute(sha_sql, [latest]).fetchone() == sha
    logged = con.execute(
        "SELECT status FROM load_log WHERE run_id = ? AND epoch_ms(chunk_ts) = ?",
        [result.run_id, latest],
    ).fetchall()
    assert logged == [("error",)]

    fake.responses.clear()
    again = run(con, fake.client(), [LOAD], mode="incremental")
    assert again.status == "ok"
    assert count(con, "SELECT count(*) FROM observations") == before


@pytest.mark.parametrize(("vanished", "accepted"), [(8, True), (9, False)])
def test_only_a_short_tail_may_vanish_from_the_open_chunk(
    con: duckdb.DuckDBPyConnection, fake: FakeSmard, vanished: int, accepted: bool
) -> None:
    name = chunk_name(LOAD, recorded_chunks(LOAD)[-1])
    run(con, fake.client(), [LOAD])
    full = _newest_stored(con)
    fake.override(name, _with_trailing_nulls(name, full - vanished))
    result = run(con, fake.client(), [LOAD], mode="incremental")
    if accepted:
        assert result.status == "ok"
        assert result.rows_removed == vanished
        assert _newest_stored(con) == full - vanished
    else:
        assert result.status == "failed"
        assert result.rows_removed == 0
        assert _newest_stored(con) == full


def test_interior_value_vanishing_from_the_open_chunk_is_rejected(
    con: duckdb.DuckDBPyConnection, fake: FakeSmard
) -> None:
    name = chunk_name(LOAD, recorded_chunks(LOAD)[-1])
    run(con, fake.client(), [LOAD])
    full = _newest_stored(con)
    data = json.loads(fixture_bytes(name))
    data["series"][10][1] = None
    fake.override(name, json.dumps(data).encode())
    result = run(con, fake.client(), [LOAD], mode="incremental")
    assert result.status == "failed"
    assert any("1 stored values are now missing in an open chunk" in e for e in result.errors)
    assert _newest_stored(con) == full


def test_withdrawals_from_the_open_chunk_can_be_accepted_explicitly(
    con: duckdb.DuckDBPyConnection, fake: FakeSmard
) -> None:
    name = chunk_name(LOAD, recorded_chunks(LOAD)[-1])
    run(con, fake.client(), [LOAD])
    full = _newest_stored(con)
    fake.override(name, _with_trailing_nulls(name, 0))
    result = Ingestor(con, fake.client(), accept_withdrawals=True).run([LOAD], mode="incremental")
    assert result.status == "ok"
    assert result.rows_removed == full
    assert _newest_stored(con) == 0


@pytest.mark.parametrize("failure", ["withdrawal", "fetch error"])
@pytest.mark.parametrize(("days_after", "status"), [(1, "failed"), (8, "partial")])
def test_errors_in_a_completed_chunk_are_recent_only_within_seven_days(
    fake: FakeSmard, failure: str, days_after: int, status: str
) -> None:
    name = chunk_name(LOAD, REGULAR)
    fake.use_index(LOAD, [REGULAR])
    at = _clock_at(
        datetime.fromtimestamp(chunk_end_ms(REGULAR) / 1000, UTC) + timedelta(days=days_after)
    )
    connection = duckdb.connect()
    connection.execute("SET TimeZone = 'UTC'")
    migrate(connection)
    assert Ingestor(connection, fake.client(), clock=at).run([LOAD], mode="backfill").status == "ok"
    if failure == "withdrawal":
        fake.override(name, _with_trailing_nulls(name, 600))
    else:
        fake.override(name, b"", 200)
    # The chunk is inside the refetch window in both cases; only its age decides.
    result = Ingestor(connection, fake.client(retries=0), clock=at).run(
        [LOAD], mode="incremental", refetch_weeks=4
    )
    assert result.status == status
    assert result.recent_errors == (1 if status == "failed" else 0)
    assert len([e for e in result.errors if "source gap" not in e]) == 1
    connection.close()


def test_all_null_open_chunk_keeps_a_short_stored_start(
    con: duckdb.DuckDBPyConnection, fake: FakeSmard
) -> None:
    name = chunk_name(LOAD, recorded_chunks(LOAD)[-1])
    fake.override(name, _with_trailing_nulls(name, 6))
    run(con, fake.client(), [LOAD])
    assert _newest_stored(con) == 6
    fake.override(name, _with_trailing_nulls(name, 0))
    result = run(con, fake.client(), [LOAD], mode="incremental")
    assert result.status == "failed"
    assert result.rows_removed == 0
    assert _newest_stored(con) == 6


def test_rejected_open_chunk_still_takes_its_new_values(
    con: duckdb.DuckDBPyConnection, fake: FakeSmard
) -> None:
    latest = recorded_chunks(LOAD)[-1]
    name = chunk_name(LOAD, latest)
    fake.override(name, _with_trailing_nulls(name, 200))
    run(con, fake.client(), [LOAD])
    sha_sql = "SELECT sha256 FROM raw_chunks WHERE series_key = 'load' AND epoch_ms(chunk_ts) = ?"
    sha = con.execute(sha_sql, [latest]).fetchone()
    data = json.loads(_with_trailing_nulls(name, 300))
    kept_ts, kept_value = data["series"][10]
    data["series"][10][1] = None
    fake.override(name, json.dumps(data).encode())

    result = run(con, fake.client(), [LOAD], mode="incremental")
    assert result.status == "failed"
    assert result.rows_written == 100
    assert any("wrote 100 new values" in e for e in result.errors)
    assert _newest_stored(con) == 300
    stored = con.execute(
        "SELECT value FROM observations WHERE series_key = 'load' AND epoch_ms(ts_utc) = ?",
        [kept_ts],
    ).fetchone()
    assert stored == (pytest.approx(kept_value),)
    # The payload is not stored, so the next run fetches the chunk again.
    assert con.execute(sha_sql, [latest]).fetchone() == sha
    logged = con.execute(
        "SELECT status FROM load_log WHERE run_id = ? AND epoch_ms(chunk_ts) = ?",
        [result.run_id, latest],
    ).fetchall()
    assert logged == [("error",)]


@pytest.mark.parametrize("failure", ["withdrawal", "fetch error"])
def test_last_chunk_of_an_ended_series_is_neither_open_nor_recent(
    con: duckdb.DuckDBPyConnection, fake: FakeSmard, failure: str
) -> None:
    # The series stopped a long time ago: its last chunk ended well over a year ago.
    name = chunk_name(LOAD, SPRING)
    fake.use_index(LOAD, [SPRING])
    assert run(con, fake.client(), [LOAD]).status == "ok"
    if failure == "withdrawal":
        fake.override(name, _with_trailing_nulls(name, 600))
    else:
        fake.override(name, b"", 200)
    result = run(con, fake.client(retries=0), [LOAD], mode="incremental")
    assert result.status == "partial"
    assert result.recent_errors == 0
    if failure == "withdrawal":
        assert any("missing in a completed chunk" in e for e in result.errors)


SOLAR = BY_KEY["gen_solar"]
# The newest recorded chunks are open at this moment (their last value is 09:45 UTC).
OPEN_NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def _with_interior_nulls(series: Series, ts: int, points: range) -> bytes:
    data = json.loads(fixture_bytes(chunk_name(series, ts)))
    for i in points:
        data["series"][i][1] = None
    return json.dumps(data).encode()


def _stored(con: duckdb.DuckDBPyConnection, series: Series) -> int:
    return count(con, "SELECT count(*) FROM observations WHERE series_key = ?", [series.key])


@pytest.mark.parametrize(
    ("withdrawn", "status", "recent_errors"),
    [((SOLAR,), "partial", 0), ((LOAD,), "failed", 1), ((LOAD, SOLAR), "failed", 1)],
)
def test_withdrawal_in_the_open_chunk_fails_the_run_only_for_the_load(
    con: duckdb.DuckDBPyConnection,
    fake: FakeSmard,
    withdrawn: tuple[Series, ...],
    status: str,
    recent_errors: int,
) -> None:
    clock = _clock_at(OPEN_NOW)
    assert run(con, fake.client(), [LOAD, SOLAR], clock=clock).status == "ok"
    before = {s.key: _stored(con, s) for s in (LOAD, SOLAR)}
    for series in withdrawn:
        latest = recorded_chunks(series)[-1]
        fake.override(
            chunk_name(series, latest), _with_interior_nulls(series, latest, range(10, 20))
        )
    result = run(con, fake.client(), [LOAD, SOLAR], mode="incremental", clock=clock)
    assert result.status == status
    assert result.recent_errors == recent_errors
    assert result.rows_removed == 0
    assert {s.key: _stored(con, s) for s in (LOAD, SOLAR)} == before
    logged = con.execute(
        "SELECT series_key FROM load_log WHERE run_id = ? AND status = 'error' ORDER BY 1",
        [result.run_id],
    ).fetchall()
    assert logged == sorted((s.key,) for s in withdrawn)
    assert all("missing in an open chunk" in e for e in result.errors)


@pytest.mark.parametrize(("series", "status"), [(SOLAR, "partial"), (LOAD, "failed")])
def test_withdrawal_in_a_recently_completed_chunk_fails_the_run_only_for_the_load(
    fake: FakeSmard, series: Series, status: str
) -> None:
    fake.use_index(series, [REGULAR])
    at = _clock_at(_day_after(REGULAR))
    connection = duckdb.connect()
    connection.execute("SET TimeZone = 'UTC'")
    migrate(connection)
    assert (
        Ingestor(connection, fake.client(), clock=at).run([series], mode="backfill").status == "ok"
    )
    before = _stored(connection, series)
    fake.override(chunk_name(series, REGULAR), _with_interior_nulls(series, REGULAR, range(10, 20)))
    result = Ingestor(connection, fake.client(), clock=at).run([series], mode="incremental")
    assert result.status == status
    assert any("now missing in a completed chunk" in e for e in result.errors)
    assert _stored(connection, series) == before
    connection.close()


def test_fetch_error_in_the_open_chunk_still_fails_the_run_for_any_series(
    con: duckdb.DuckDBPyConnection, fake: FakeSmard
) -> None:
    clock = _clock_at(OPEN_NOW)
    run(con, fake.client(), [SOLAR], clock=clock)
    fake.override(chunk_name(SOLAR, recorded_chunks(SOLAR)[-1]), b"")
    result = run(con, fake.client(retries=0), [SOLAR], mode="incremental", clock=clock)
    assert result.status == "failed"


class _RecordingConnection:
    """Record statements and which temporary files exist when the snapshot is attached."""

    def __init__(self, inner: duckdb.DuckDBPyConnection, tmp: Path) -> None:
        self._inner = inner
        self._tmp = tmp
        self.statements: list[str] = []
        self.present_at_attach: list[str] = []

    def execute(self, sql: str, params: object = None) -> duckdb.DuckDBPyConnection:
        self.statements.append(sql)
        if sql.startswith("ATTACH"):
            self.present_at_attach = sorted(
                p.name for p in self._tmp.parent.iterdir() if p.name.startswith(self._tmp.name)
            )
        return self._inner.execute(sql, params)


def test_publish_removes_a_stale_temporary_file_and_its_log_first(
    tmp_path: Path, con: duckdb.DuckDBPyConnection, fake: FakeSmard
) -> None:
    run(con, fake.client(), [LOAD])
    target = tmp_path / "serving.duckdb"
    (tmp_path / "serving.duckdb.tmp").write_bytes(b"left by a crashed run")
    (tmp_path / "serving.duckdb.tmp.wal").write_bytes(b"garbage" * 100)
    proxy = _RecordingConnection(con, tmp_path / "serving.duckdb.tmp")
    snapshot.publish(proxy, target)  # type: ignore[arg-type]
    assert proxy.present_at_attach == []
    assert sorted(p.name for p in tmp_path.iterdir()) == ["serving.duckdb"]


def test_published_snapshot_is_checkpointed_and_opens_read_only_without_a_log(
    tmp_path: Path, con: duckdb.DuckDBPyConnection, fake: FakeSmard
) -> None:
    run(con, fake.client(), [LOAD])
    target = tmp_path / "serving.duckdb"
    proxy = _RecordingConnection(con, tmp_path / "serving.duckdb.tmp")
    snapshot.publish(proxy, target)  # type: ignore[arg-type]
    assert proxy.statements[-2:] == ["CHECKPOINT snap", "DETACH snap"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["serving.duckdb"]
    reader = duckdb.connect(str(target), read_only=True)
    try:
        assert count(reader, "SELECT count(*) FROM observations") == _stored(con, LOAD)
    finally:
        reader.close()
    assert not (tmp_path / "serving.duckdb.wal").exists()


def test_failed_rename_leaves_no_temporary_files(
    tmp_path: Path, con: duckdb.DuckDBPyConnection, fake: FakeSmard, monkeypatch: pytest.MonkeyPatch
) -> None:
    run(con, fake.client(), [LOAD])
    target = tmp_path / "serving.duckdb"
    snapshot.publish(con, target)
    published = target.read_bytes()

    def failing_replace(_src: object, _dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises(OSError, match="disk full"):
        snapshot.publish(con, target)
    assert target.read_bytes() == published
    assert sorted(p.name for p in tmp_path.iterdir()) == ["serving.duckdb"]
