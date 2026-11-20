import functools
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from gridload import cli
from gridload.config import Settings
from gridload.pipeline import heartbeat, jobs
from gridload.pipeline.db import connect
from gridload.pipeline.ingest import Ingestor, RunResult
from gridload.series import CATALOG
from tests.fake_smard import (
    FIXTURE_NOW,
    FakeSmard,
    advancing_clock,
    chunk_name,
    fixture_bytes,
    recorded_chunks,
)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path)


def pin_ingest_clock(monkeypatch: pytest.MonkeyPatch, clock: Callable[[], datetime]) -> None:
    """Make ``jobs.load`` ingest with ``clock`` instead of the wall clock."""
    monkeypatch.setattr(jobs, "Ingestor", functools.partial(Ingestor, clock=clock))


@pytest.fixture(autouse=True)
def _fixture_time(monkeypatch: pytest.MonkeyPatch) -> None:
    # Which recorded chunks count as open or recent depends on "now"; the clock
    # advances so that consecutive runs keep their order.
    pin_ingest_clock(monkeypatch, advancing_clock())


@pytest.fixture
def fake() -> FakeSmard:
    smard = FakeSmard()
    for series in CATALOG:
        smard.use_index(series, recorded_chunks(series))
    return smard


def test_load_publishes_snapshot_and_heartbeat(
    settings: Settings, fake: FakeSmard, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(heartbeat, "now_iso", FIXTURE_NOW.isoformat)
    calls: list[str] = []

    def post(_con: duckdb.DuckDBPyConnection, _s: Settings, _tick: Callable[[], None]) -> list[str]:
        calls.append("post")
        return []

    result = jobs.load(
        settings,
        mode="backfill",
        keys=["load"],
        client_factory=lambda _s: fake.client(),
        post_ingest=post,
    )
    assert result.status == "ok"
    assert calls == ["post"]
    assert settings.serving_path.exists()
    beat = heartbeat.read(settings.heartbeat_path)
    assert beat is not None
    assert (beat.state, beat.last_status) == ("idle", "ok")
    assert beat.last_success is not None
    assert beat.last_success == FIXTURE_NOW.isoformat()
    # The run just succeeded, so the age of the newest load value decides health.
    newest = datetime(2026, 11, 20, 9, 45, tzinfo=UTC)
    assert heartbeat.is_healthy(beat, FIXTURE_NOW)
    assert heartbeat.is_healthy(beat, newest + timedelta(hours=12))
    assert not heartbeat.is_healthy(beat, newest + timedelta(hours=12, minutes=1))


def test_failed_load_keeps_previous_success(settings: Settings, fake: FakeSmard) -> None:
    jobs.load(settings, mode="backfill", keys=["load"], client_factory=lambda _s: fake.client())
    first = heartbeat.read(settings.heartbeat_path)
    assert first is not None
    fake.override(chunk_name(CATALOG[0], recorded_chunks(CATALOG[0])[-1]), b"")
    result = jobs.load(
        settings, mode="incremental", keys=["load"], client_factory=lambda _s: fake.client()
    )
    assert result.status == "failed"
    second = heartbeat.read(settings.heartbeat_path)
    assert second is not None
    assert second.last_success == first.last_success
    assert second.last_status == "failed"


def test_prices_can_be_switched_off(tmp_path: Path, fake: FakeSmard) -> None:
    settings = Settings(data_dir=tmp_path, include_prices=False)
    jobs.load(settings, mode="backfill", client_factory=lambda _s: fake.client())
    assert not any("/4169/" in path for path in fake.requests)
    con = duckdb.connect(str(settings.serving_path), read_only=True)
    try:
        prices = con.execute(
            "SELECT count(*) FROM observations WHERE series_key = 'price_day_ahead'"
        ).fetchone()
        assert prices == (0,)
    finally:
        con.close()


def test_running_heartbeat_is_throttled(settings: Settings) -> None:
    beat = jobs._Beat(settings, interval=3600)
    beat()
    first = heartbeat.read(settings.heartbeat_path)
    assert first is not None
    beat()
    assert heartbeat.read(settings.heartbeat_path) == first


def test_status_before_and_after_load(settings: Settings, fake: FakeSmard) -> None:
    assert jobs.status(settings) == {"snapshot": str(settings.serving_path), "initialised": False}
    fake.override(chunk_name(CATALOG[0], recorded_chunks(CATALOG[0])[0]), b"")
    jobs.load(
        settings,
        mode="backfill",
        keys=["load"],
        refetch_weeks=2,
        client_factory=lambda _s: fake.client(),
    )
    report = jobs.status(settings)
    assert report["initialised"] is True
    # The broken chunk ended more than seven days ago, so the run is partial.
    assert report["runs"][0]["status"] == "partial"
    assert report["series"][0]["series"] == "load"
    assert report["errors_last_run"][0]["message"] == "empty response body"
    json.loads(jobs.dumps(report))


def test_status_works_while_the_warehouse_is_locked(settings: Settings, fake: FakeSmard) -> None:
    jobs.load(settings, mode="backfill", keys=["load"], client_factory=lambda _s: fake.client())
    with connect(settings.warehouse_path):
        assert jobs.status(settings)["initialised"] is True


def test_default_client_uses_settings(settings: Settings) -> None:
    client = jobs.default_client(settings)
    client.close()


# CLI ----------------------------------------------------------------------------------


def fake_load(calls: list[dict[str, object]], status: str = "ok"):  # type: ignore[no-untyped-def]
    def load(settings: Settings, **kwargs: object) -> RunResult:
        calls.append(kwargs)
        result = RunResult(run_id="r1", mode=str(kwargs["mode"]), status=status)
        if status != "ok":
            result.errors.append("load: boom")
        return result

    return load


def test_cli_requires_a_command(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2
    assert "required" in capsys.readouterr().err


def test_cli_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert "gridload" in capsys.readouterr().out


def test_cli_backfill_arguments(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(jobs, "load", fake_load(calls))
    assert cli.main(["backfill", "--since", "2024-01-01", "--series", "load", "gen_solar"]) == 0
    assert calls[0]["mode"] == "backfill"
    assert calls[0]["keys"] == ["load", "gen_solar"]
    assert str(calls[0]["since"]) == "2024-01-01 00:00:00+00:00"
    assert "run r1 ok" in capsys.readouterr().out


def test_cli_rejects_bad_date(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["backfill", "--since", "01.01.2024"])
    assert "expected YYYY-MM-DD" in capsys.readouterr().err


def test_cli_ingest_and_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(jobs, "load", fake_load(calls, status="failed"))
    assert cli.main(["ingest"]) == 1
    assert cli.main(["run"]) == 1
    assert calls[0] == {
        "mode": "incremental",
        "keys": None,
        "refetch_weeks": 4,
        "accept_withdrawals": False,
    }
    assert calls[1]["post_ingest"] is jobs.post_ingest_steps
    assert "error: load: boom" in capsys.readouterr().err


@pytest.mark.parametrize(("value", "fragment"), [("-3", "must not be negative"), ("x", "whole")])
def test_cli_rejects_bad_refetch_weeks(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], value: str, fragment: str
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(jobs, "load", fake_load(calls))
    with pytest.raises(SystemExit) as exc:
        cli.main(["ingest", "--refetch-weeks", value])
    assert exc.value.code == 2
    assert fragment in capsys.readouterr().err
    assert calls == []
    assert cli.main(["ingest", "--refetch-weeks", "0"]) == 0
    assert calls[0]["refetch_weeks"] == 0


def test_cli_status_and_healthcheck(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GRIDLOAD_DATA_DIR", str(tmp_path))
    assert cli.main(["status"]) == 0
    assert json.loads(capsys.readouterr().out)["initialised"] is False
    assert cli.main(["healthcheck"]) == 1
    heartbeat.write(
        tmp_path / "heartbeat.json", heartbeat.Heartbeat("running", heartbeat.now_iso())
    )
    assert cli.main(["healthcheck"]) == 0


def test_post_ingest_steps_without_data_does_nothing(settings: Settings) -> None:
    with connect(settings.warehouse_path) as con:
        jobs.post_ingest_steps(con, settings)
        assert con.execute("SELECT count(*) FROM forecast_runs").fetchone() == (0,)


def test_cli_reports_value_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GRIDLOAD_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GRIDLOAD_INCLUDE_PRICES", "false")
    assert cli.main(["backfill", "--series", "price_day_ahead"]) == 2
    assert "prices are switched off" in capsys.readouterr().err


def test_cli_partial_run_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(jobs, "load", fake_load(calls, status="partial"))
    assert cli.main(["ingest", "--refetch-weeks", "9999"]) == 0
    assert calls[0]["refetch_weeks"] == 9999


def test_prices_loaded_earlier_disappear_when_switched_off(tmp_path: Path, fake: FakeSmard) -> None:
    jobs.load(Settings(data_dir=tmp_path), mode="backfill", client_factory=lambda _s: fake.client())
    off = Settings(data_dir=tmp_path, include_prices=False)
    jobs.load(off, mode="incremental", client_factory=lambda _s: fake.client())
    con = duckdb.connect(str(off.serving_path), read_only=True)
    try:
        for table in ("observations", "hourly", "series", "load_log"):
            row = con.execute(
                f"SELECT count(*) FROM {table} WHERE series_key = 'price_day_ahead'"  # noqa: S608
            ).fetchone()
            assert row == (0,), table
        assert con.execute("SELECT count(*) FROM observations").fetchone() != (0,)
    finally:
        con.close()


def test_failure_after_ingest_marks_run_failed(settings: Settings, fake: FakeSmard) -> None:
    def broken(
        _con: duckdb.DuckDBPyConnection, _settings: Settings, _tick: Callable[[], None]
    ) -> list[str]:
        raise RuntimeError("forecast failed")

    with pytest.raises(RuntimeError, match="forecast failed"):
        jobs.load(
            settings,
            mode="backfill",
            keys=["load"],
            client_factory=lambda _s: fake.client(),
            post_ingest=broken,
        )
    with connect(settings.warehouse_path) as con:
        assert con.execute("SELECT status FROM load_runs").fetchall() == [("failed",)]
    beat = heartbeat.read(settings.heartbeat_path)
    assert beat is not None
    assert beat.last_status == "failed"


def test_cli_does_not_hide_unexpected_value_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(_settings: Settings, **_kwargs: object) -> RunResult:
        raise ValueError("bug deep in the pipeline")

    monkeypatch.setattr(jobs, "load", broken)
    with pytest.raises(ValueError, match="bug deep"):
        cli.main(["ingest"])


def test_heartbeat_records_newest_load_value(settings: Settings, fake: FakeSmard) -> None:
    jobs.load(settings, mode="backfill", keys=["load"], client_factory=lambda _s: fake.client())
    beat = heartbeat.read(settings.heartbeat_path)
    assert beat is not None
    assert beat.newest_load == "2026-11-20T09:45:00+00:00"


def test_failed_follow_up_step_makes_the_run_partial(settings: Settings, fake: FakeSmard) -> None:
    result = jobs.load(
        settings,
        mode="backfill",
        keys=["load"],
        client_factory=lambda _s: fake.client(),
        post_ingest=lambda _con, _s, _tick: ["backtest"],
    )
    assert result.status == "partial"
    assert result.errors == ["step failed: backtest"]
    with connect(settings.warehouse_path) as con:
        assert con.execute("SELECT status FROM load_runs").fetchall() == [("partial",)]


def test_failed_step_after_a_partial_ingest_keeps_partial(
    settings: Settings, fake: FakeSmard
) -> None:
    fake.override(chunk_name(CATALOG[0], recorded_chunks(CATALOG[0])[0]), b"")
    result = jobs.load(
        settings,
        mode="backfill",
        keys=["load"],
        refetch_weeks=2,
        client_factory=lambda _s: fake.client(),
        post_ingest=lambda _con, _s, _tick: ["forecast"],
    )
    assert result.status == "partial"
    assert result.errors[-1] == "step failed: forecast"


def test_run_finishes_after_the_follow_up_steps(
    settings: Settings, fake: FakeSmard, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[object, ...]] = []
    clock = advancing_clock()
    pin_ingest_clock(monkeypatch, clock)

    def steps(
        con: duckdb.DuckDBPyConnection, _settings: Settings, _tick: Callable[[], None]
    ) -> list[str]:
        row = con.execute("SELECT status, finished_at FROM load_runs").fetchone()
        assert row is not None
        seen.append((*row, clock()))
        return []

    result = jobs.load(
        settings,
        mode="backfill",
        keys=["load"],
        client_factory=lambda _s: fake.client(),
        post_ingest=steps,
    )
    assert result.status == "ok"
    assert seen[0][:2] == ("running", None)
    served = duckdb.connect(str(settings.serving_path), read_only=True)
    try:
        row = served.execute("SELECT status, finished_at FROM load_runs").fetchone()
    finally:
        served.close()
    assert row is not None
    assert row[0] == "ok"
    assert row[1] > seen[0][2]


def test_withdrawal_in_a_generation_series_keeps_the_pipeline_healthy(
    settings: Settings, fake: FakeSmard
) -> None:
    solar = next(s for s in CATALOG if s.key == "gen_solar")
    keys = ["load", "gen_solar"]
    jobs.load(settings, mode="backfill", keys=keys, client_factory=lambda _s: fake.client())
    first = heartbeat.read(settings.heartbeat_path)
    assert first is not None
    assert first.last_success is not None
    latest = recorded_chunks(solar)[-1]
    data = json.loads(fixture_bytes(chunk_name(solar, latest)))
    for point in data["series"][10:20]:
        point[1] = None
    fake.override(chunk_name(solar, latest), json.dumps(data).encode())
    result = jobs.load(
        settings, mode="incremental", keys=keys, client_factory=lambda _s: fake.client()
    )
    assert result.status == "partial"
    assert any(e.startswith("gen_solar: ") for e in result.errors)
    second = heartbeat.read(settings.heartbeat_path)
    assert second is not None
    assert second.last_status == "partial"
    assert second.last_success is not None
    assert second.last_success > first.last_success
    report = jobs.status(settings)
    assert report["runs"][0]["status"] == "partial"
    assert any(e["series"] == "gen_solar" for e in report["errors_last_run"])
