"""Heartbeat file for the pipeline container healthcheck.

The scheduled job updates the file while it runs and when it finishes. The
container is healthy while a run is in progress and has reported recently, or
when the last run with status ok or partial is younger than ``max_age`` and the
newest load value is younger than ``max_data_age`` (so a source that stops
updating is noticed even though every run succeeds).
"""

import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path


@dataclass(frozen=True)
class Heartbeat:
    state: str  # running | idle
    # last_success: end of the last run with status ok or partial (fresh data loaded).
    updated_at: str
    last_success: str | None = None
    last_status: str | None = None
    newest_load: str | None = None


def write(path: Path, beat: Heartbeat) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(asdict(beat)), encoding="utf-8")
    os.replace(tmp, path)


def read(path: Path) -> Heartbeat | None:
    """Read the heartbeat; None if missing or malformed (including bad timestamps)."""
    try:
        beat = Heartbeat(**json.loads(path.read_text(encoding="utf-8")))
        for stamp in (beat.updated_at, beat.last_success, beat.newest_load):
            if stamp is not None and datetime.fromisoformat(stamp).utcoffset() is None:
                raise ValueError("timestamp without time zone")
    except OSError, ValueError, TypeError:
        return None
    return beat


def is_healthy(
    beat: Heartbeat | None,
    now: datetime,
    *,
    max_age: timedelta = timedelta(hours=26),
    max_data_age: timedelta = timedelta(hours=12),
    running_grace: timedelta = timedelta(minutes=15),
) -> bool:
    if beat is None:
        return False
    if beat.state == "running" and now - datetime.fromisoformat(beat.updated_at) <= running_grace:
        return True
    if beat.last_success is None or beat.newest_load is None:
        return False
    return (
        now - datetime.fromisoformat(beat.last_success) <= max_age
        and now - datetime.fromisoformat(beat.newest_load) <= max_data_age
    )


def now_iso() -> str:
    return datetime.now(UTC).isoformat()
