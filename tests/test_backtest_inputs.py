import hashlib
import json
import runpy
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from gridload.config import Settings
from gridload.forecast import service
from gridload.forecast.features import History
from tests import synthetic

ROOT = Path(__file__).resolve().parent.parent
FIRST = date(2025, 1, 1)
LAST = date(2025, 4, 30)


def test_input_fingerprint_is_canonical_and_ends_at_last_day() -> None:
    # 2025-01-01 starts at 2024-12-31 23:00 UTC; hours after 2025-01-01 must not count
    start = int(datetime(2024, 12, 31, 23, tzinfo=UTC).timestamp() * 1000)
    hour = 3_600_000
    load = History(np.array([start, start + hour, start + 24 * hour]), np.array([1.0, 2.5, 9.0]))
    tso = History(np.array([start]), np.array([1.0004]))
    digest, count = service.input_fingerprint(
        {"load": load, "load_forecast_tso": tso}, date(2025, 1, 1)
    )
    assert count == 3
    expected = f"load,{start},1.000\nload,{start + hour},2.500\nload_forecast_tso,{start},1.000\n"
    assert digest == hashlib.sha256(expected.encode()).hexdigest()
    shifted = History(np.array([start]), np.array([1.01]))
    other, _ = service.input_fingerprint(
        {"load": load, "load_forecast_tso": shifted}, date(2025, 1, 1)
    )
    assert other != digest


def test_resolve_last_day() -> None:
    load = synthetic.history(FIRST, LAST)
    today = date(2025, 6, 1)
    assert service.resolve_last_day(load, None, today) == LAST
    assert service.resolve_last_day(load, date(2025, 4, 1), today) == date(2025, 4, 1)
    with pytest.raises(ValueError, match="after the last complete day 2025-04-30"):
        service.resolve_last_day(load, date(2025, 5, 1), today)
    with pytest.raises(ValueError, match="not in the past"):
        service.resolve_last_day(load, date(2025, 4, 20), date(2025, 4, 20))
    with pytest.raises(ValueError, match="no training data; use 2025-02-13 or later"):
        service.resolve_last_day(load, date(2025, 2, 12), today)
    assert service.resolve_last_day(load, date(2025, 2, 13), today) == date(2025, 2, 13)
    with pytest.raises(ValueError, match="no complete day"):
        service.resolve_last_day(History(np.array([]), np.array([])), None, today)


def _warehouse_file(tmp_path: Path) -> Path:
    settings = Settings(data_dir=tmp_path)
    source = synthetic.warehouse(FIRST, LAST)
    escaped = str(settings.warehouse_path).replace("'", "''")
    source.execute(f"ATTACH '{escaped}' AS target")
    source.execute("COPY FROM DATABASE memory TO target")
    source.execute("DETACH target")
    source.close()
    return settings.warehouse_path


def _script() -> Any:
    return runpy.run_path(str(ROOT / "scripts" / "backtest.py"))["main"]


def test_backtest_script_last_day_and_fingerprint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    warehouse = _warehouse_file(tmp_path)
    main = _script()
    args = ["--warehouse", str(warehouse), "--days", "7", "--rows", str(tmp_path / "rows.csv")]
    today = date(2025, 6, 1)
    assert main([*args, "--out", str(tmp_path / "a"), "--last-day", "2025-04-20"], today=today) == 0
    data = json.loads((tmp_path / "a" / "backtest.json").read_text())
    assert data["last_day"] == "2025-04-20"
    assert data["first_day"] == "2025-04-14"
    assert set(data["inputs"]) == {"sha256", "hours"}
    assert len(data["inputs"]["sha256"]) == 64
    first_digest = data["inputs"]["sha256"]
    assert "Input data: SHA-256" in (tmp_path / "a" / "backtest.md").read_text()

    assert main([*args, "--out", str(tmp_path / "b")], today=today) == 0
    later = json.loads((tmp_path / "b" / "backtest.json").read_text())
    assert later["last_day"] == "2025-04-30"
    assert later["inputs"]["sha256"] != first_digest
    assert later["inputs"]["hours"] > data["inputs"]["hours"]

    assert main([*args, "--out", str(tmp_path / "c"), "--last-day", "2025-05-02"], today=today) == 1
    assert "after the last complete day" in capsys.readouterr().err
    assert (
        main(
            [*args, "--out", str(tmp_path / "c"), "--last-day", "2025-04-20"],
            today=date(2025, 4, 20),
        )
        == 1
    )
    assert "not in the past" in capsys.readouterr().err
    assert not (tmp_path / "c").exists()
    # the script only writes the reports and the rows, never the serving snapshot
    assert not Settings(data_dir=tmp_path).serving_path.exists()
    with pytest.raises(SystemExit):
        main([*args, "--last-day", "20.04.2025"], today=today)
