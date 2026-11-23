import json
import math
from datetime import date

import numpy as np
import pytest

from gridload.forecast import report
from gridload.forecast.backtest import BacktestResult, error_stats, error_summary, metrics_for

NAN = math.nan


def test_error_stats_hand_computed() -> None:
    # errors 10, -10, 0, -100; absolute percentage errors 10 %, 5 %, 0 %, 10 %
    stats = error_stats(np.array([100.0, 200, 400, 1000]), np.array([110.0, 190, 400, 900]))
    assert stats is not None
    assert stats.bias == pytest.approx(-25.0)
    # sorted 0, 10, 10, 100; position 0.95 * 3 = 2.85 -> 10 + 0.85 * 90
    assert stats.p95_abs == pytest.approx(86.5)
    assert stats.max_abs == pytest.approx(100.0)
    assert stats.share_above_5_percent == pytest.approx(50.0)  # exactly 5 % does not count
    assert stats.n == 4


def test_error_stats_skips_nan_and_zero_actuals() -> None:
    stats = error_stats(np.array([100.0, NAN, 300, 0, 200]), np.array([90.0, 50, NAN, 10, 230]))
    assert stats is not None
    assert stats.n == 2
    assert stats.bias == pytest.approx(10.0)  # (-10 + 30) / 2
    assert stats.max_abs == pytest.approx(30.0)
    assert stats.share_above_5_percent == pytest.approx(100.0)


@pytest.mark.parametrize(
    ("actual", "predicted"),
    [([], []), ([NAN, 100.0], [100.0, NAN]), ([0.0], [5.0])],
)
def test_error_stats_without_usable_hours(actual: list[float], predicted: list[float]) -> None:
    assert error_stats(np.array(actual), np.array(predicted)) is None


def _row(actual: float, lgbm: float, base: float, tso: float) -> dict[str, object]:
    return {
        "actual": actual,
        "lightgbm": lgbm,
        "baseline_week": base,
        "tso": tso,
        "hour_local": 0,
        "day_type": "workday",
    }


def test_error_summary_uses_the_hours_of_the_overall_metrics() -> None:
    rows = [
        _row(100.0, 110.0, 100.0, 120.0),
        _row(200.0, 200.0, NAN, 100.0),  # no baseline: dropped for every forecast
        _row(100.0, 90.0, 100.0, NAN),  # no SMARD value: dropped for SMARD only
    ]
    summary = error_summary(rows)
    assert summary["lightgbm"].n == 2
    assert summary["lightgbm"].bias == pytest.approx(0.0)
    assert summary["baseline_week"].max_abs == pytest.approx(0.0)
    assert summary["tso"].n == 1
    assert summary["tso"].bias == pytest.approx(20.0)
    assert error_summary([]) == {}


def _result_with_rows() -> BacktestResult:
    result = BacktestResult(date(2025, 1, 1), date(2025, 1, 1), {"retrain_every": 7, "seed": 42})
    result.rows = [_row(100.0, 110.0, 100.0, 120.0), _row(100.0, 90.0, 95.0, 100.0)]
    result.metrics = metrics_for(result.rows)
    result.inputs = {"sha256": "ab" * 32, "hours": 4}
    return result


def test_report_contains_error_distribution_and_inputs() -> None:
    result = _result_with_rows()
    data = json.loads(report.to_json(result))
    assert data["inputs"] == {"sha256": "ab" * 32, "hours": 4}
    assert data["error_distribution"]["lightgbm"] == {
        "bias_mwh": 0.0,
        "p95_abs_error_mwh": 10.0,
        "max_abs_error_mwh": 10.0,
        "hours_above_5_percent": 100.0,
        "n": 2,
    }
    assert data["error_distribution"]["tso"]["bias_mwh"] == 10.0
    text = report.to_markdown(result)
    assert "## Error distribution" in text
    assert "| LightGBM model | +0 | 10 | 10 | 100.0 % |" in text
    assert "| SMARD grid load forecast | +10 | 19 | 20 | 50.0 % |" in text
    assert "SHA-256 `" + "ab" * 32 + "` over 4 hourly values" in text
    assert "corrected" not in text


def test_report_without_rows_has_no_error_distribution() -> None:
    empty = BacktestResult(date(2025, 1, 1), date(2025, 1, 2), {"retrain_every": 7, "seed": 42})
    assert json.loads(report.to_json(empty))["error_distribution"] == {}
    assert "## Error distribution" not in report.to_markdown(empty)
