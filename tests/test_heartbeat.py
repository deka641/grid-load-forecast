from datetime import datetime, timedelta
from pathlib import Path

from gridload.pipeline import heartbeat
from gridload.pipeline.heartbeat import Heartbeat, is_healthy
from tests.fake_smard import FIXTURE_NOW


def iso(delta: timedelta) -> str:
    return (FIXTURE_NOW - delta).isoformat()


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
    (tmp_path / "bad_ts.json").write_text('{"state": "idle", "updated_at": "yesterday"}')
    assert heartbeat.read(tmp_path / "bad_ts.json") is None
    (tmp_path / "naive.json").write_text(
        '{"state": "idle", "updated_at": "2026-11-20T10:00:00+00:00", '
        '"last_success": "2026-11-20T09:00:00"}'
    )
    assert heartbeat.read(tmp_path / "naive.json") is None


def test_health_rules() -> None:
    fresh = iso(timedelta(hours=2))
    assert not is_healthy(None, FIXTURE_NOW)
    assert is_healthy(Heartbeat("running", iso(timedelta(minutes=5))), FIXTURE_NOW)
    assert not is_healthy(Heartbeat("running", iso(timedelta(minutes=30))), FIXTURE_NOW)
    assert is_healthy(
        Heartbeat("idle", iso(timedelta()), iso(timedelta(hours=25)), "ok", fresh), FIXTURE_NOW
    )
    assert not is_healthy(
        Heartbeat("idle", iso(timedelta()), iso(timedelta(hours=27)), "ok", fresh), FIXTURE_NOW
    )
    assert not is_healthy(Heartbeat("idle", iso(timedelta()), None, "failed", fresh), FIXTURE_NOW)
    # Every run succeeds, but the source stopped publishing new values.
    stale_data = Heartbeat(
        "idle", iso(timedelta()), iso(timedelta(hours=1)), "ok", iso(timedelta(hours=13))
    )
    assert not is_healthy(stale_data, FIXTURE_NOW)
    assert not is_healthy(
        Heartbeat("idle", iso(timedelta()), iso(timedelta(hours=1)), "ok"), FIXTURE_NOW
    )
    stale_run_recent_success = Heartbeat(
        "running", iso(timedelta(hours=2)), iso(timedelta(hours=3)), None, fresh
    )
    assert is_healthy(stale_run_recent_success, FIXTURE_NOW)


def test_now_iso_is_utc() -> None:
    assert datetime.fromisoformat(heartbeat.now_iso()).utcoffset() == timedelta(0)
