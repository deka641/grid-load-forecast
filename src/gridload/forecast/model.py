"""LightGBM model for the hourly day-ahead load forecast."""

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime

import lightgbm as lgb
import numpy as np

from gridload.forecast.features import FEATURES, History, actuals, day_features

MODEL_NAME = "lightgbm"
BASELINE_NAME = "baseline_week"
TSO_NAME = "tso"
SEED = 42
NUM_BOOST_ROUND = 600
PARAMS: dict[str, object] = {
    "objective": "l1",
    "learning_rate": 0.05,
    "num_leaves": 63,
    "min_data_in_leaf": 50,
    "feature_fraction": 0.9,
    "seed": SEED,
    "deterministic": True,
    "force_col_wise": True,
    "num_threads": 2,
    "verbose": -1,
}


def params_hash() -> str:
    text = json.dumps({"params": PARAMS, "rounds": NUM_BOOST_ROUND, "features": FEATURES})
    return hashlib.sha256(text.encode()).hexdigest()[:12]


@dataclass
class FeatureStore:
    """Feature rows and labels for a range of delivery days, computed once."""

    days: list[date]
    matrix: np.ndarray
    labels: np.ndarray
    row_day: np.ndarray  # delivery day ordinal per row
    row_ts: list[datetime]

    @classmethod
    def build(cls, history: History, days: list[date]) -> FeatureStore:
        matrices: list[np.ndarray] = []
        labels: list[np.ndarray] = []
        row_day: list[int] = []
        row_ts: list[datetime] = []
        for day in days:
            rows = day_features(history, day)
            matrices.append(rows.matrix)
            labels.append(actuals(history, rows.targets))
            row_day.extend([day.toordinal()] * len(rows.targets))
            row_ts.extend(rows.targets)
        width = len(FEATURES)
        return cls(
            days=days,
            matrix=np.vstack(matrices) if matrices else np.empty((0, width)),
            labels=np.concatenate(labels) if labels else np.empty(0),
            row_day=np.asarray(row_day, dtype=np.int64),
            row_ts=row_ts,
        )

    def training_mask(self, last_training_day: date) -> np.ndarray:
        mask: np.ndarray = (self.row_day <= last_training_day.toordinal()) & ~np.isnan(self.labels)
        return mask

    def day_mask(self, day: date) -> np.ndarray:
        mask: np.ndarray = self.row_day == day.toordinal()
        return mask


def train(matrix: np.ndarray, labels: np.ndarray) -> lgb.Booster:
    if matrix.shape[0] == 0:
        raise ValueError("no training rows")
    data = lgb.Dataset(matrix, label=labels, feature_name=list(FEATURES), free_raw_data=True)
    return lgb.train(PARAMS, data, num_boost_round=NUM_BOOST_ROUND)


def predict(booster: lgb.Booster, matrix: np.ndarray) -> np.ndarray:
    return np.asarray(booster.predict(matrix), dtype=np.float64)
