import json
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
    return Ingestor(con, client).run(series, mode=str(kw.pop("mode", "backfill")), **kw)  # type: ignore[arg-type]


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
    result = run(con, fake.client(retries=0), [LOAD, PRICE])
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
    result = run(con, fake.client(), [LOAD], mode="incremental")
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


def test_unexpected_exception_marks_run_failed(con: duckdb.DuckDBPyConnection) -> None:
    class Exploding(SmardClient):
        def __init__(self) -> None:
            pass

        def index(self, series: Series) -> list[int]:
            raise RuntimeError("bug")

    with pytest.raises(RuntimeError):
        Ingestor(con, Exploding()).run([LOAD], mode="backfill")
    assert con.execute("SELECT status FROM load_runs").fetchall() == [("failed",)]


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


def test_incomplete_chunk_is_completed_after_leaving_the_refetch_window(
    con: duckdb.DuckDBPyConnection, fake: FakeSmard
) -> None:
    fake.use_index(LOAD, [REGULAR])
    fake.override(chunk_name(LOAD, REGULAR), _with_trailing_nulls(chunk_name(LOAD, REGULAR), 100))
    run(con, fake.client(), [LOAD])
    assert count(con, "SELECT count(*) FROM observations") == 100

    later = [REGULAR + i * 7 * 86_400_000 for i in range(1, 6)]
    for ts in later:
        fake.override(
            chunk_name(LOAD, ts),
            fixture_bytes(chunk_name(LOAD, REGULAR)).replace(str(REGULAR).encode(), b"0"),
            404,
        )
    fake.use_index(LOAD, [REGULAR, *later])
    fake.responses.pop(chunk_name(LOAD, REGULAR))
    fake.requests.clear()
    run(con, fake.client(retries=0), [LOAD], mode="incremental", refetch_weeks=1)
    assert f"/app/chart_data/410/DE/{chunk_name(LOAD, REGULAR)}" in fake.requests
    assert (
        count(
            con,
            "SELECT count(*) FROM observations WHERE ts_utc < make_timestamptz(? * 1000)",
            [later[0]],
        )
        == 672
    )


def test_recently_ended_chunk_may_still_have_trailing_nulls(fake: FakeSmard) -> None:
    week_end = datetime.fromtimestamp(chunk_end_ms(REGULAR) / 1000, UTC)
    name = chunk_name(LOAD, REGULAR)
    fake.use_index(LOAD, [REGULAR, recorded_chunks(LOAD)[-1]])
    fake.override(name, _with_trailing_nulls(name, 668))
    for hours_after, status in ((2, "ok"), (8, "failed")):
        connection = duckdb.connect()
        connection.execute("SET TimeZone = 'UTC'")
        migrate(connection)
        now = week_end + timedelta(hours=hours_after)

        def clock(at: datetime = now) -> datetime:
            return at

        result = Ingestor(connection, fake.client(), clock=clock).run([LOAD], mode="backfill")
        assert result.status == status, hours_after
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
