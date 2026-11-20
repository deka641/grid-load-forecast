"""Rolling-origin backtest of the day-ahead forecast.

For each delivery day in the evaluation window the forecast is issued as in
production: origin 12:00 local on the previous day, features cut off two hours
earlier. The model is retrained every ``retrain_every`` origins on all delivery
days that ended before the origin day began (an expanding window with a hard
cutoff). The baseline and the SMARD load forecast are scored on exactly the
same hours, namely those where the actual value and all three predictions exist.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta

import lightgbm as lgb
import numpy as np

from gridload.forecast import calendar as cal
from gridload.forecast.features import History, baseline_week
from gridload.forecast.model import (
    BASELINE_NAME,
    MODEL_NAME,
    NUM_BOOST_ROUND,
    PARAMS,
    SEED,
    TSO_NAME,
    FeatureStore,
    params_hash,
    predict,
    train,
)

MODELS = (MODEL_NAME, BASELINE_NAME, TSO_NAME)
TRAINING_START = date(2015, 1, 15)
MIN_TRAINING_DAYS = timedelta(days=28)


@dataclass(frozen=True)
class Metric:
    model: str
    scope: str
    scope_value: str
    mae: float
    mape: float
    n: int


@dataclass
class BacktestResult:
    first_day: date
    last_day: date
    config: dict[str, object]
    rows: list[dict[str, object]] = field(default_factory=list)
    metrics: list[Metric] = field(default_factory=list)
    skipped_days: list[date] = field(default_factory=list)


def mae(actual: np.ndarray, predicted: np.ndarray) -> float:
    return float(np.mean(np.abs(actual - predicted)))


def mape(actual: np.ndarray, predicted: np.ndarray) -> float:
    """Mean absolute percentage error in percent. Actual values must be non-zero."""
    if np.any(actual == 0):
        raise ValueError("MAPE is undefined for zero actual values")
    return float(100.0 * np.mean(np.abs((actual - predicted) / actual)))


def score(
    actual: np.ndarray, predictions: dict[str, np.ndarray], groups: dict[str, np.ndarray]
) -> list[Metric]:
    """Metrics per model for all rows and per value of each grouping."""
    out: list[Metric] = []
    for model, predicted in predictions.items():
        out.append(
            Metric(
                model,
                "overall",
                "all",
                mae(actual, predicted),
                mape(actual, predicted),
                int(actual.size),
            )
        )
        for scope, labels in groups.items():
            for value in sorted(set(labels.tolist()), key=_sort_key):
                mask = labels == value
                out.append(
                    Metric(
                        model,
                        scope,
                        str(value),
                        mae(actual[mask], predicted[mask]),
                        mape(actual[mask], predicted[mask]),
                        int(mask.sum()),
                    )
                )
    return out


def _sort_key(value: object) -> tuple[int, str]:
    return (int(value), "") if isinstance(value, int | np.integer) else (0, str(value))


def run_backtest(
    load: History,
    tso: History,
    last_day: date,
    *,
    days: int = 365,
    retrain_every: int = 7,
    training_start: date = TRAINING_START,
    progress: Callable[[date], None] | None = None,
) -> BacktestResult:
    # Evaluation starts no earlier than MIN_TRAINING_DAYS after the training start, so
    # every model has data to learn from; a short history yields fewer scored days.
    first_day = max(last_day - timedelta(days=days - 1), training_start + MIN_TRAINING_DAYS)
    store = FeatureStore.build(load, cal.days_between(training_start, last_day))
    config: dict[str, object] = {
        "days": days,
        "retrain_every": retrain_every,
        "training_start": training_start.isoformat(),
        "origin": "12:00 Europe/Berlin on the day before delivery",
        "feature_cutoff_hours_before_origin": int(cal.DATA_DELAY.total_seconds() // 3600),
        "seed": SEED,
        "num_boost_round": NUM_BOOST_ROUND,
        "params": PARAMS,
        "params_hash": params_hash(),
    }
    result = BacktestResult(first_day, last_day, config)
    booster: lgb.Booster | None = None
    for i, day in enumerate(cal.days_between(first_day, last_day)):
        if progress is not None:
            progress(day)
        if booster is None or i % retrain_every == 0:
            mask = store.training_mask(day - timedelta(days=2))
            booster = train(store.matrix[mask], store.labels[mask])
        rows = store.day_mask(day)
        model_pred = predict(booster, store.matrix[rows])
        baseline = baseline_week(load, day)
        targets = cal.day_hours(day)
        actual = store.labels[rows]
        tso_values = np.array([tso.value_at(ts, cal.never()) for ts in targets])
        if np.all(np.isnan(actual)):
            result.skipped_days.append(day)
            continue
        for k, ts in enumerate(targets):
            result.rows.append(
                {
                    "delivery_day": day,
                    "target_ts": ts,
                    "hour_local": ts.astimezone(cal.BERLIN).hour,
                    # fold 1 marks the repeated 02:00 hour on the autumn clock-change day
                    "fold": ts.astimezone(cal.BERLIN).fold,
                    "day_type": cal.day_type(day),
                    "actual": float(actual[k]),
                    MODEL_NAME: float(model_pred[k]),
                    BASELINE_NAME: float(baseline[k]),
                    TSO_NAME: float(tso_values[k]),
                }
            )
    result.metrics = metrics_for(result.rows)
    return result


def metrics_for(rows: list[dict[str, object]]) -> list[Metric]:
    """Score the forecasts on comparable hours.

    LightGBM and the baseline are scored on every hour with an actual value and
    both predictions. The SMARD forecast is scored on the subset of those hours
    where it exists; on that subset the two other forecasts are scored again
    (scope ``overall_tso_hours``), so the three can be compared on equal terms
    even if SMARD values are missing for some hours. Hours with a zero actual
    value are excluded because MAPE is undefined for them (grid load is never
    zero in practice).
    """
    primary = [r for r in rows if _present(r, ("actual", MODEL_NAME, BASELINE_NAME))]
    primary = [r for r in primary if _f(r["actual"]) != 0]
    if not primary:
        return []
    out = _score_rows(primary, (MODEL_NAME, BASELINE_NAME))
    with_tso = [r for r in primary if _present(r, (TSO_NAME,))]
    if with_tso:
        out += _score_rows(with_tso, (TSO_NAME,))
        actual = np.array([_f(r["actual"]) for r in with_tso])
        for model in (MODEL_NAME, BASELINE_NAME):
            predicted = np.array([_f(r[model]) for r in with_tso])
            out.append(
                Metric(
                    model,
                    "overall_tso_hours",
                    "all",
                    mae(actual, predicted),
                    mape(actual, predicted),
                    int(actual.size),
                )
            )
    return out


def _present(row: dict[str, object], keys: tuple[str, ...]) -> bool:
    return all(not math.isnan(_f(row[k])) for k in keys)


def _score_rows(rows: list[dict[str, object]], models: tuple[str, ...]) -> list[Metric]:
    actual = np.array([_f(r["actual"]) for r in rows])
    predictions = {m: np.array([_f(r[m]) for r in rows]) for m in models}
    groups = {
        "hour": np.array([int(_f(r["hour_local"])) for r in rows]),
        "day_type": np.array([str(r["day_type"]) for r in rows]),
    }
    return score(actual, predictions, groups)


def _f(value: object) -> float:
    if isinstance(value, int | float):
        return float(value)
    raise TypeError(f"expected a number, got {type(value).__name__}")
