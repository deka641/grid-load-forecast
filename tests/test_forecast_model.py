from datetime import UTC, date, datetime, timedelta

import numpy as np
import pytest

from gridload import timezones as tz
from gridload.forecast import calendar as cal
from gridload.forecast.backtest import Metric, mae, mape, metrics_for, run_backtest, score
from gridload.forecast.features import FEATURES, History, baseline_week, day_features
from gridload.forecast.model import FeatureStore, params_hash, train
from tests import synthetic

FIRST = date(2025, 1, 1)
LAST = date(2025, 4, 30)


@pytest.fixture(scope="module")
def load() -> History:
    return synthetic.history(FIRST, LAST)


def corrupted_from(history: History, start: datetime) -> History:
    """Copy with every hour starting at or after ``start`` replaced by nonsense."""
    values = history.values.copy()
    late = history.ts >= int(start.timestamp() * 1000)
    values[late] = values[late] * 10 + 12345.0
    return History(history.ts.copy(), values)


def unknown_at_origin(history: History, day: date) -> History:
    """Corrupt all hours that had not ended by the cutoff of ``day``."""
    return corrupted_from(history, cal.cutoff_for(day))


def test_features_ignore_everything_after_the_cutoff(load: History) -> None:
    day = date(2025, 3, 30)  # 23 hour day
    leaked = unknown_at_origin(load, day)
    original = day_features(load, day)
    assert original.matrix.shape == (23, len(FEATURES))
    np.testing.assert_array_equal(original.matrix, day_features(leaked, day).matrix)
    np.testing.assert_array_equal(baseline_week(load, day), baseline_week(leaked, day))


def test_lag_1d_is_only_set_for_hours_before_the_cutoff(load: History) -> None:
    day = date(2025, 3, 12)
    rows = day_features(load, day)
    lag_1d = rows.matrix[:, FEATURES.index("lag_1d")]
    hour = rows.matrix[:, FEATURES.index("hour_local")]
    # The hour h on the previous day ends at h + 1, so only hours up to 09:00 are known.
    assert not np.isnan(lag_1d[hour <= 9]).any()
    assert np.isnan(lag_1d[hour >= 10]).all()


def test_the_last_admissible_hour_is_used(load: History) -> None:
    """Counter-check: corrupting the hour 09:00 to 10:00 must change the features."""
    day = date(2025, 3, 12)
    last_known = cal.cutoff_for(day) - timedelta(hours=1)
    changed = day_features(corrupted_from(load, last_known), day).matrix
    assert not np.array_equal(np.nan_to_num(changed), np.nan_to_num(day_features(load, day).matrix))


def test_baseline_uses_neighbour_hour_after_spring_change(load: History) -> None:
    day = date(2025, 4, 6)
    baseline = baseline_week(load, day)
    assert not np.isnan(baseline).any()
    # 02:00 CEST on 2025-04-06 maps to 01:00 CET on 2025-03-30, which is 00:00 UTC.
    assert baseline[2] == load.value_at(datetime(2025, 3, 30, 0, tzinfo=UTC), cal.never())


def test_backtest_predictions_do_not_change_when_future_data_is_corrupted(load: History) -> None:
    last = date(2025, 4, 12)
    first = date(2025, 4, 5)
    tso = synthetic.history(FIRST, LAST, seed=11)
    kwargs = {"days": 8, "retrain_every": 7, "training_start": date(2025, 1, 15)}
    clean = run_backtest(load, tso, last, **kwargs)  # type: ignore[arg-type]
    # Corrupt everything unknown at the first origin: the first day's predictions
    # must stay the same, although the corruption reaches all later training rows.
    leaked_all = run_backtest(unknown_at_origin(load, first), tso, last, **kwargs)  # type: ignore[arg-type]
    first_day_rows = [r for r in clean.rows if r["delivery_day"] == first]
    leaked_rows = [r for r in leaked_all.rows if r["delivery_day"] == first]
    assert [r["lightgbm"] for r in first_day_rows] == [r["lightgbm"] for r in leaked_rows]
    assert [r["baseline_week"] for r in first_day_rows] == [r["baseline_week"] for r in leaked_rows]
    assert [r["actual"] for r in first_day_rows] != [r["actual"] for r in leaked_rows]
    # Per origin, on a retraining day (04-12, index 7) and on a day that reuses the
    # previous model (04-08): data unknown at that origin leaves every prediction
    # up to that day unchanged.
    for day in (last, date(2025, 4, 8)):
        leaked = run_backtest(unknown_at_origin(load, day), tso, last, **kwargs)  # type: ignore[arg-type]
        upto = [r["lightgbm"] for r in clean.rows if r["delivery_day"] <= day]  # type: ignore[operator]
        assert upto == [r["lightgbm"] for r in leaked.rows if r["delivery_day"] <= day]  # type: ignore[operator]


def test_backtest_is_deterministic_and_scores_all_models(load: History) -> None:
    tso = synthetic.history(FIRST, LAST, seed=11)
    kwargs = {"days": 10, "retrain_every": 7, "training_start": date(2025, 1, 15)}
    first = run_backtest(load, tso, date(2025, 4, 20), **kwargs)  # type: ignore[arg-type]
    second = run_backtest(load, tso, date(2025, 4, 20), **kwargs)  # type: ignore[arg-type]
    assert first.metrics == second.metrics
    overall = {m.model: m for m in first.metrics if m.scope == "overall"}
    assert set(overall) == {"lightgbm", "baseline_week", "tso"}
    assert all(m.n == 240 for m in overall.values())
    hours = [m for m in first.metrics if m.model == "lightgbm" and m.scope == "hour"]
    assert [m.scope_value for m in hours] == [str(h) for h in range(24)]
    assert first.first_day == date(2025, 4, 11)
    assert first.config["seed"] == 42
    # On this clean synthetic series the model must beat the naive baseline.
    assert overall["lightgbm"].mae < overall["baseline_week"].mae


def test_backtest_skips_days_without_actuals(load: History) -> None:
    tso = synthetic.history(FIRST, LAST)
    calls: list[date] = []
    result = run_backtest(
        load, tso, date(2025, 5, 3), days=4, training_start=date(2025, 1, 15), progress=calls.append
    )
    assert result.skipped_days == [date(2025, 5, 1), date(2025, 5, 2), date(2025, 5, 3)]
    assert calls == cal.days_between(date(2025, 4, 30), date(2025, 5, 3))


def test_metric_functions() -> None:
    actual = np.array([100.0, 200.0])
    predicted = np.array([110.0, 180.0])
    assert mae(actual, predicted) == 15.0
    assert mape(actual, predicted) == pytest.approx(10.0)
    with pytest.raises(ValueError, match="zero"):
        mape(np.array([0.0]), np.array([1.0]))


def test_score_groups_and_metrics_for_filters_missing() -> None:
    metrics = score(
        np.array([100.0, 200.0, 300.0]),
        {"m": np.array([100.0, 220.0, 330.0])},
        {"day_type": np.array(["workday", "saturday", "workday"])},
    )
    assert Metric("m", "day_type", "saturday", 20.0, pytest.approx(10.0), 1) in metrics  # type: ignore[arg-type]
    row = {
        "actual": 100.0,
        "lightgbm": 90.0,
        "baseline_week": 80.0,
        "tso": float("nan"),
        "hour_local": 0,
        "day_type": "workday",
    }
    # Without a SMARD value the other two forecasts are still scored.
    scored = {(m.model, m.scope) for m in metrics_for([row])}
    assert ("lightgbm", "overall") in scored
    assert not any(model == "tso" for model, _ in scored)
    assert not any(scope == "overall_tso_hours" for _, scope in scored)
    assert metrics_for([{**row, "actual": 0.0}]) == []
    assert metrics_for([{**row, "lightgbm": float("nan")}]) == []
    with pytest.raises(TypeError, match="expected a number"):
        metrics_for([{**row, "tso": "x"}])


def test_training_needs_rows() -> None:
    with pytest.raises(ValueError, match="no training rows"):
        train(np.empty((0, len(FEATURES))), np.empty(0))


def test_feature_store_masks(load: History) -> None:
    store = FeatureStore.build(load, cal.days_between(date(2025, 3, 1), date(2025, 3, 5)))
    assert store.matrix.shape[0] == 5 * 24
    assert int(store.training_mask(date(2025, 3, 2)).sum()) == 48
    assert int(store.day_mask(date(2025, 3, 5)).sum()) == 24
    empty = FeatureStore.build(load, [])
    assert empty.matrix.shape == (0, len(FEATURES))
    assert len(params_hash()) == 12
    assert store.row_ts[0] == tz.day_hours(date(2025, 3, 1))[0]
