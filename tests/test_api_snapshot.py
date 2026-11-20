"""Snapshot lifecycle: missing, unreadable, replaced and removed serving files."""

import os
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from gridload.api.app import create_app
from gridload.api.store import SnapshotStore, SnapshotUnavailableError
from gridload.config import Settings
from gridload.pipeline.db import migrate
from tests.test_api import add_run, get, make_client, publish

DATA_ENDPOINTS = [
    ("/api/v1/actuals", {"series": "load", "start": "2026-11-10", "end": "2026-11-10"}),
    ("/api/v1/forecasts", {}),
    ("/api/v1/forecasts/days", {}),
    ("/api/v1/backtest/metrics", {}),
]
DAY = date(2026, 11, 12)
ISSUED = datetime(2026, 11, 11, 9, tzinfo=UTC)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return Settings(data_dir=data_dir)


@pytest.fixture
def empty_wh() -> Iterator[duckdb.DuckDBPyConnection]:
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    migrate(con)
    yield con
    con.close()


def replace_with_garbage(path: Path) -> None:
    tmp = path.with_name("garbage.tmp")
    tmp.write_bytes(b"not a database" * 512)
    os.replace(tmp, path)


def days(settings: Settings) -> list[str]:
    client = make_client(settings)
    result: list[str] = get(client, "/api/v1/forecasts/days")["days"]
    return result


@pytest.mark.parametrize(("url", "params"), DATA_ENDPOINTS)
def test_data_endpoints_answer_503_without_a_snapshot(
    settings: Settings, url: str, params: dict[str, str]
) -> None:
    response = make_client(settings).get(url, params=params)
    assert response.status_code == 503
    assert response.json() == {
        "detail": "no data snapshot is available yet; the pipeline has not published one"
    }


def test_parameters_are_validated_before_the_snapshot_is_needed(settings: Settings) -> None:
    response = make_client(settings).get(
        "/api/v1/actuals", params={"series": "nope", "start": "2026-11-10", "end": "2026-11-10"}
    )
    assert response.status_code == 422


def test_status_and_health_work_without_a_snapshot(settings: Settings) -> None:
    client = make_client(settings)
    assert get(client, "/api/v1/health")["status"] == "ok"
    body = get(client, "/api/v1/status")
    assert body["initialised"] is False
    assert body["snapshot_created_at"] is None
    assert body["schema_version"] is None
    assert body["last_load_run"] is None
    assert body["last_forecast"] is None
    assert body["series"][0] == {
        "key": "load",
        "label": "Grid load",
        "last_ts": None,
        "age_seconds": None,
    }
    assert body["attribution"] == "Bundesnetzagentur | SMARD.de"
    assert body["licence"] == "CC BY 4.0"
    assert "HEDWIG" in body["price_note"]


def test_unreadable_first_snapshot_is_503(settings: Settings) -> None:
    replace_with_garbage(settings.serving_path)
    client = make_client(settings)
    response = client.get("/api/v1/forecasts/days")
    assert response.status_code == 503
    assert response.json() == {"detail": "the data snapshot cannot be opened"}
    assert get(client, "/api/v1/status")["initialised"] is False


def test_replaced_snapshot_is_picked_up_by_the_next_request(
    empty_wh: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    publish(empty_wh, settings)
    client = make_client(settings)
    assert get(client, "/api/v1/forecasts/days")["days"] == []
    inode = settings.serving_path.stat().st_ino

    add_run(empty_wh, "r1", "lightgbm", DAY, ISSUED, 1.0)
    publish(empty_wh, settings)
    assert settings.serving_path.stat().st_ino != inode
    assert get(client, "/api/v1/forecasts/days")["days"] == ["2026-11-12"]
    assert get(client, "/api/v1/forecasts")["runs"]["lightgbm"]["run_id"] == "r1"


def test_unreadable_replacement_keeps_serving_the_previous_snapshot(
    empty_wh: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    add_run(empty_wh, "r1", "lightgbm", DAY, ISSUED, 1.0)
    publish(empty_wh, settings)
    client = make_client(settings)
    assert get(client, "/api/v1/forecasts/days")["days"] == ["2026-11-12"]

    replace_with_garbage(settings.serving_path)
    assert get(client, "/api/v1/forecasts/days")["days"] == ["2026-11-12"]
    assert get(client, "/api/v1/status")["initialised"] is True

    add_run(empty_wh, "r2", "lightgbm", date(2026, 11, 13), ISSUED, 1.0)
    publish(empty_wh, settings)
    assert get(client, "/api/v1/forecasts/days")["days"] == ["2026-11-13", "2026-11-12"]


def test_removed_snapshot_is_503_again(
    empty_wh: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    publish(empty_wh, settings)
    client = make_client(settings)
    assert client.get("/api/v1/forecasts/days").status_code == 200
    settings.serving_path.unlink()
    assert client.get("/api/v1/forecasts/days").status_code == 503
    assert get(client, "/api/v1/status")["initialised"] is False
    publish(empty_wh, settings)
    assert client.get("/api/v1/forecasts/days").status_code == 200


def test_open_cursor_survives_a_replacement(
    empty_wh: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    publish(empty_wh, settings)
    store = SnapshotStore(settings.serving_path)
    assert store.path == settings.serving_path
    with store.cursor() as (old, old_created):
        add_run(empty_wh, "r1", "lightgbm", DAY, ISSUED, 1.0)
        publish(empty_wh, settings)
        with store.cursor() as (new, new_created):
            assert new.execute("SELECT count(*) FROM forecast_runs").fetchone() == (1,)
        assert new_created >= old_created
        # The request that started on the old file still reads it to the end.
        assert old.execute("SELECT count(*) FROM forecast_runs").fetchone() == (0,)


def test_snapshot_is_read_only_and_isolated(
    empty_wh: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    publish(empty_wh, settings)
    store = SnapshotStore(settings.serving_path)
    with store.cursor() as (cur, _created):
        assert cur.execute("SELECT current_setting('TimeZone')").fetchone() == ("UTC",)
        with pytest.raises(duckdb.Error):
            cur.execute("DELETE FROM series")
        with pytest.raises(duckdb.Error):
            cur.execute("SELECT * FROM read_text(?)", [str(tmp_path / "x")])
        with pytest.raises(duckdb.Error):
            cur.execute("SET enable_external_access = true")


def test_missing_snapshot_raises_in_the_store(settings: Settings) -> None:
    store = SnapshotStore(settings.serving_path)
    with pytest.raises(SnapshotUnavailableError), store.cursor():
        pytest.fail("a cursor without a snapshot")


def test_concurrent_requests_during_replacements(
    empty_wh: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    publish(empty_wh, settings)
    client = make_client(settings)
    stop = threading.Event()
    run_days = [date(2026, 11, 1 + i) for i in range(10)]
    # State k has the first k runs; the API lists their days newest first.
    states = {tuple(d.isoformat() for d in reversed(run_days[:k])) for k in range(11)}

    def reader() -> list[tuple[int, tuple[str, ...]]]:
        seen = []
        while not stop.is_set():
            response = client.get("/api/v1/forecasts/days")
            seen.append((response.status_code, tuple(response.json()["days"])))
        return seen

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(reader) for _ in range(4)]
        for i, day in enumerate(run_days):
            add_run(empty_wh, f"r{i}", "lightgbm", day, ISSUED, 1.0)
            publish(empty_wh, settings)
        stop.set()
        seen = [item for f in futures for item in f.result()]
    assert seen
    assert {code for code, _ in seen} == {200}
    assert {body for _, body in seen} <= states
    assert len(days(settings)) == 10


def _task_count() -> int:
    return len(os.listdir("/proc/self/task"))


def _fd_count() -> int:
    return len(os.listdir("/proc/self/fd"))


def test_open_snapshots_use_few_threads(
    empty_wh: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    publish(empty_wh, settings)
    before = _task_count()
    first, second = SnapshotStore(settings.serving_path), SnapshotStore(settings.serving_path)
    with first.cursor() as (a, _), second.cursor() as (b, _):
        a.execute("SELECT count(*) FROM hourly").fetchall()
        b.execute("SELECT count(*) FROM hourly").fetchall()
        # Two instances with threads=2 and async_threads=1. DuckDB's defaults
        # (threads = cores, async_threads = 4 x cores) gave 119 threads per
        # instance on a 24-core host.
        assert _task_count() - before <= 8


def test_repeated_swaps_do_not_leak_threads_or_files(
    empty_wh: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    publish(empty_wh, settings)
    store = SnapshotStore(settings.serving_path)

    def swap_and_read() -> None:
        publish(empty_wh, settings)
        with store.cursor() as (cur, _):
            cur.execute("SELECT count(*) FROM series").fetchall()

    for _ in range(5):
        swap_and_read()
    tasks, fds = _task_count(), _fd_count()
    for _ in range(200):
        swap_and_read()
    assert _task_count() <= tasks + 2
    assert _fd_count() <= fds + 2


def test_status_uses_the_wall_clock_by_default(settings: Settings) -> None:
    before = datetime.now(UTC)
    body = get(TestClient(create_app(settings)), "/api/v1/status")
    generated = datetime.fromisoformat(body["generated_at"])
    assert before <= generated <= datetime.now(UTC)
    assert generated.utcoffset() is not None
