import json
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pytest

from gridload import cli
from gridload.config import Settings
from gridload.forecast import calendar as cal
from gridload.forecast import report, service
from gridload.forecast.backtest import BacktestResult, run_backtest
from gridload.pipeline import jobs
from gridload.pipeline.db import connect
from tests import synthetic

FIRST = date(2025, 1, 1)
LAST = date(2025, 4, 30)


@pytest.fixture(scope="module")
def result() -> BacktestResult:
    load = synthetic.history(FIRST, LAST)
    tso = synthetic.history(FIRST, LAST, seed=11)
    return run_backtest(load, tso, date(2025, 5, 1), days=8, training_start=date(2025, 1, 15))


def test_report_json_is_deterministic_and_rounded(result: BacktestResult) -> None:
    text = report.to_json(result)
    assert text == report.to_json(result)
    data = json.loads(text)
    assert data["first_day"] == "2025-04-24"
    assert data["skipped_days"] == ["2025-05-01"]
    overall = [m for m in data["metrics"] if m["scope"] == "overall"]
    assert {m["model"] for m in overall} == {"lightgbm", "baseline_week", "tso"}
    assert all(m["n"] == 7 * 24 for m in overall)


def test_report_markdown_lists_all_sections(result: BacktestResult) -> None:
    text = report.to_markdown(result)
    for heading in ("## Overall", "## By day type (MAPE)", "## By local hour (MAPE)", "## Notes"):
        assert heading in text
    assert "| LightGBM model |" in text
    assert "| 23:00 |" in text
    assert "not scored: 1" in text
    assert chr(0x2013) not in text
    assert chr(0x2014) not in text


def test_report_markdown_without_metrics() -> None:
    empty = BacktestResult(date(2025, 1, 1), date(2025, 1, 2), {"retrain_every": 7, "seed": 42})
    assert "No hours" in report.to_markdown(empty)


def test_report_marks_missing_day_types(result: BacktestResult) -> None:
    partial = BacktestResult(
        result.first_day,
        result.last_day,
        result.config,
        metrics=[m for m in result.metrics if m.scope_value not in {"saturday", "5"}],
    )
    text = report.to_markdown(partial)
    assert "| Saturdays | n/a" in text
    assert "| 05:00 | n/a" in text


def test_rows_csv(tmp_path: Path, result: BacktestResult) -> None:
    path = tmp_path / "sub" / "rows.csv"
    report.write_rows(result, path)
    lines = path.read_text().splitlines()
    assert lines[0].startswith("delivery_day,target_ts")
    assert len(lines) == 1 + len(result.rows)
    assert lines[1].startswith("2025-04-24,2025-04-23T22:00:00+00:00,0,workday")


def _settings_with_warehouse(tmp_path: Path, first: date, last: date) -> Settings:
    settings = Settings(data_dir=tmp_path)
    source = synthetic.warehouse(first, last)
    escaped = str(settings.warehouse_path).replace("'", "''")
    source.execute(f"ATTACH '{escaped}' AS target")
    source.execute("COPY FROM DATABASE memory TO target")
    source.execute("DETACH target")
    source.close()
    return settings


def test_post_ingest_issues_forecast_and_backtest(tmp_path: Path) -> None:
    settings = _settings_with_warehouse(tmp_path, FIRST, LAST)
    con = duckdb.connect(str(settings.warehouse_path))
    try:
        now = datetime(2025, 5, 1, 12, 30, tzinfo=cal.BERLIN)
        jobs.post_ingest_steps(con, settings, now=now)
        runs = con.execute(
            "SELECT model, delivery_day FROM forecast_runs ORDER BY model"
        ).fetchall()
        assert runs == [("baseline_week", date(2025, 5, 2)), ("lightgbm", date(2025, 5, 2))]
        assert con.execute("SELECT count(*) FROM backtest_runs").fetchone() == (1,)
    finally:
        con.close()


def test_post_ingest_skips_forecast_on_stale_data(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    settings = _settings_with_warehouse(tmp_path, FIRST, LAST)
    con = duckdb.connect(str(settings.warehouse_path))
    try:
        con.execute(
            "INSERT INTO backtest_runs VALUES ('b', ?, ?, ?, '{}')",
            [datetime(2025, 5, 9, tzinfo=UTC), FIRST, LAST],
        )
        jobs.post_ingest_steps(con, settings, now=datetime(2025, 5, 10, 13, tzinfo=cal.BERLIN))
        assert con.execute("SELECT count(*) FROM forecast_runs").fetchone() == (0,)
        assert "skipped" in caplog.text
    finally:
        con.close()


def test_post_ingest_with_empty_warehouse(tmp_path: Path) -> None:
    with connect(tmp_path / "w.duckdb") as con:
        jobs.post_ingest_steps(
            con, Settings(data_dir=tmp_path), now=datetime(2025, 5, 1, 13, tzinfo=UTC)
        )
        assert con.execute("SELECT count(*) FROM backtest_runs").fetchone() == (0,)


def test_backtest_job_and_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = _settings_with_warehouse(tmp_path, FIRST, LAST)
    assert jobs.backtest(settings, days=3) == "error: --days must be at least 7"
    summary = jobs.backtest(settings, days=7)
    assert summary.startswith("backtest 2025-04-24 to 2025-04-30")
    assert settings.serving_path.exists()

    monkeypatch.setenv("GRIDLOAD_DATA_DIR", str(tmp_path))
    assert cli.main(["backtest", "--days", "7"]) == 0
    assert cli.main(["backtest", "--days", "2"]) == 1
    assert "at least 7" in capsys.readouterr().out


def test_backtest_job_without_data(tmp_path: Path) -> None:
    assert (
        jobs.backtest(Settings(data_dir=tmp_path), days=7) == "error: no complete day of load data"
    )


def test_short_history_backtest_scores_fewer_days() -> None:
    load = synthetic.history(FIRST, date(2025, 2, 20))
    result = run_backtest(load, load, date(2025, 2, 20), days=365, training_start=date(2025, 1, 15))
    assert result.first_day == date(2025, 2, 12)
    too_short = run_backtest(
        load, load, date(2025, 2, 1), days=365, training_start=date(2025, 1, 15)
    )
    assert too_short.rows == []
    assert too_short.metrics == []


def test_backtest_without_metrics_is_stored(tmp_path: Path) -> None:
    settings = _settings_with_warehouse(tmp_path, FIRST, date(2025, 2, 5))
    with connect(settings.warehouse_path) as con:
        result = service.run_and_store_backtest(con)
        assert result is not None
        assert result.metrics == []
        assert con.execute("SELECT count(*) FROM backtest_metrics").fetchone() == (0,)


def test_forecast_with_no_baseline_values(tmp_path: Path) -> None:
    con = synthetic.warehouse(FIRST, date(2025, 2, 10))
    # Delivery day far ahead: the model runs on empty lags, the baseline has no values at all.
    con.execute(
        "DELETE FROM observations WHERE ts_utc >= ?", [cal.local_midnight(date(2025, 2, 1))]
    )
    service.issue_forecast(con, date(2025, 2, 9))
    counts = con.execute(
        "SELECT r.model, count(f.run_id) FROM forecast_runs r LEFT JOIN forecasts f USING (run_id) "
        "GROUP BY 1 ORDER BY 1"
    ).fetchall()
    assert counts == [("baseline_week", 0), ("lightgbm", 24)]


def test_post_ingest_with_too_little_history_for_backtest(tmp_path: Path) -> None:
    settings = _settings_with_warehouse(tmp_path, FIRST, FIRST)
    with connect(settings.warehouse_path) as con:
        con.execute("DELETE FROM observations WHERE ts_utc = ?", [cal.local_midnight(FIRST)])
        jobs.post_ingest_steps(con, settings, now=datetime(2025, 1, 1, 9, tzinfo=UTC))
        assert con.execute("SELECT count(*) FROM backtest_runs").fetchone() == (0,)
