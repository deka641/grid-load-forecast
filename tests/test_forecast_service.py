import json
from datetime import UTC, date, datetime

import duckdb
import pytest

from gridload import timezones as tz
from gridload.forecast import calendar as cal
from gridload.forecast import service
from gridload.pipeline.db import migrate
from tests import synthetic

FIRST = date(2025, 1, 1)
LAST = date(2025, 4, 30)


@pytest.fixture(scope="module")
def con() -> duckdb.DuckDBPyConnection:
    return synthetic.warehouse(FIRST, LAST)


def test_history_comes_from_complete_hours(con: duckdb.DuckDBPyConnection) -> None:
    load = service.load_history(con, "load")
    assert len(load) == sum(len(tz.day_hours(d)) for d in cal.days_between(FIRST, LAST))
    assert service.last_complete_day(load) == LAST


def test_last_complete_day_ignores_partial_days(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        "INSERT INTO observations VALUES ('load', 'quarterhour', ?, 1.0, ?, 0)",
        [tz.local_midnight(date(2025, 5, 1)), datetime(2026, 1, 1, tzinfo=UTC)],
    )
    try:
        assert service.last_complete_day(service.load_history(con, "load")) == LAST
    finally:
        con.execute(
            "DELETE FROM observations WHERE ts_utc = ?", [tz.local_midnight(date(2025, 5, 1))]
        )


def test_empty_history() -> None:
    empty = duckdb.connect()
    migrate(empty)
    assert service.last_complete_day(service.load_history(empty, "load")) is None
    assert service.run_and_store_backtest(empty) is None


def test_history_without_any_complete_day() -> None:
    con = synthetic.warehouse(FIRST, FIRST)
    con.execute("DELETE FROM observations WHERE ts_utc = ?", [tz.local_midnight(FIRST)])
    assert service.last_complete_day(service.load_history(con, "load")) is None


def test_forecast_is_due_after_noon_once(con: duckdb.DuckDBPyConnection) -> None:
    morning = datetime(2025, 4, 30, 11, 59, tzinfo=tz.BERLIN)
    noon = datetime(2025, 4, 30, 12, 0, tzinfo=tz.BERLIN)
    assert service.due_delivery_day(con, morning) is None
    assert service.due_delivery_day(con, noon) == date(2025, 5, 1)


def test_issue_forecast_stores_model_and_baseline(con: duckdb.DuckDBPyConnection) -> None:
    day = date(2025, 4, 30)
    service.issue_forecast(con, day, now=datetime(2025, 4, 29, 12, 5, tzinfo=UTC))
    runs = con.execute(
        "SELECT model, epoch_ms(issued_at), details FROM forecast_runs WHERE delivery_day = ? "
        "ORDER BY model",
        [day],
    ).fetchall()
    assert [r[0] for r in runs] == ["baseline_week", "lightgbm"]
    assert runs[1][1] == int(cal.origin_for(day).timestamp() * 1000)
    details = json.loads(runs[1][2])
    assert details["last_training_day"] == "2025-04-28"
    points = con.execute(
        "SELECT count(*) FROM forecasts f JOIN forecast_runs r USING (run_id) "
        "WHERE r.delivery_day = ? AND r.model = 'lightgbm'",
        [day],
    ).fetchone()
    assert points == (24,)
    assert service.due_delivery_day(con, datetime(2025, 4, 29, 13, tzinfo=tz.BERLIN)) is None


def test_forecast_for_a_25_hour_day() -> None:
    con = synthetic.warehouse(date(2025, 8, 1), date(2025, 10, 25))
    service.issue_forecast(con, date(2025, 10, 26))
    points = con.execute(
        "SELECT r.model, count(*) FROM forecasts JOIN forecast_runs r USING (run_id) "
        "GROUP BY 1 ORDER BY 1"
    ).fetchall()
    # The baseline for 2025-10-26 needs 2025-10-19, the model rows exist for all 25 hours.
    assert points == [("baseline_week", 25), ("lightgbm", 25)]


def test_issue_forecast_rolls_back_on_error(con: duckdb.DuckDBPyConnection) -> None:
    before = con.execute("SELECT count(*) FROM forecast_runs").fetchone()

    proxy = _FailingExecutemany(con)
    with pytest.raises(duckdb.IOException):
        service.issue_forecast(proxy, date(2025, 4, 25))  # type: ignore[arg-type]
    assert con.execute("SELECT count(*) FROM forecast_runs").fetchone() == before


class _FailingExecutemany:
    def __init__(self, inner: duckdb.DuckDBPyConnection) -> None:
        self._inner = inner

    def executemany(self, *_args: object) -> None:
        raise duckdb.IOException("disk full")

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


def test_backtest_is_stored_and_staleness(con: duckdb.DuckDBPyConnection) -> None:
    now = datetime(2025, 5, 1, tzinfo=UTC)
    assert service.backtest_is_stale(con, now)
    seen: list[date] = []
    result = service.run_and_store_backtest(con, days=3, now=now, progress=seen.append)
    assert result is not None
    assert seen == cal.days_between(date(2025, 4, 28), LAST)
    assert not service.backtest_is_stale(con, now)
    assert service.backtest_is_stale(con, datetime(2025, 5, 9, tzinfo=UTC))
    stored = con.execute(
        "SELECT model, n FROM backtest_metrics WHERE scope = 'overall' ORDER BY model"
    ).fetchall()
    assert stored == [("baseline_week", 72), ("lightgbm", 72), ("tso", 72)]


def test_backtest_storage_rolls_back(con: duckdb.DuckDBPyConnection) -> None:
    before = con.execute("SELECT count(*) FROM backtest_runs").fetchone()
    with pytest.raises(duckdb.IOException):
        service.run_and_store_backtest(_FailingExecutemany(con), days=1)  # type: ignore[arg-type]
    assert con.execute("SELECT count(*) FROM backtest_runs").fetchone() == before
