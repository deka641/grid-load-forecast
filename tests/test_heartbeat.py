from datetime import UTC, datetime, timedelta
from pathlib import Path

from gridload.pipeline import heartbeat
from gridload.pipeline.heartbeat import Heartbeat, is_healthy

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def iso(delta: timedelta) -> str:
    return (NOW - delta).isoformat()


def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "hb.json"
    beat = Heartbeat("idle", iso(timedelta()), iso(timedelta(hours=1)), "ok")
    heartbeat.write(path, beat)
    assert heartbeat.read(path) == beat


def test_missing_or_corrupt_file_reads_as_none(tmp_path: Path) -> None:
    assert heartbeat.read(tmp_path / "none.json") is None
    (tmp_path / "bad.json").write_text("{not json")
    assert heartbeat.read(tmp_path / "bad.json") is None
    (tmp_path / "wrong.json").write_text('{"unexpected": 1}')
    assert heartbeat.read(tmp_path / "wrong.json") is None


def test_health_rules() -> None:
    assert not is_healthy(None, NOW)
    assert is_healthy(Heartbeat("running", iso(timedelta(minutes=5))), NOW)
    assert not is_healthy(Heartbeat("running", iso(timedelta(minutes=30))), NOW)
    assert is_healthy(Heartbeat("idle", iso(timedelta()), iso(timedelta(hours=25))), NOW)
    assert not is_healthy(Heartbeat("idle", iso(timedelta()), iso(timedelta(hours=27))), NOW)
    assert not is_healthy(Heartbeat("idle", iso(timedelta()), None, "failed"), NOW)
    stale_run_recent_success = Heartbeat(
        "running", iso(timedelta(hours=2)), iso(timedelta(hours=3))
    )
    assert is_healthy(stale_run_recent_success, NOW)


def test_now_iso_is_utc() -> None:
    assert datetime.fromisoformat(heartbeat.now_iso()).utcoffset() == timedelta(0)
