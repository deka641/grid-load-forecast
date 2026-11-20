"""Leakage-safe features for the day-ahead load forecast.

Every feature row belongs to one target hour of a local delivery day. Hourly
values are stamped with the start of their hour, and a value counts as known
only once its hour has ended. All history-based features read only hours that
ended at or before the cutoff of that delivery day (origin 12:00 local on the
previous day, minus two hours, so the newest usable hour is 09:00 to 10:00).
Rows for a delivery day therefore depend only on that day's cutoff, which lets
the backtest compute the feature matrix once and still train each model on
strictly past data.
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime

import numpy as np
import polars as pl

from gridload.forecast import calendar as cal

HOUR_MS = 3_600_000

FEATURES: tuple[str, ...] = (
    "hour_local",
    "weekday",
    "month",
    "day_of_year",
    "day_type",
    "is_holiday",
    "is_bridge_day",
    "hours_in_day",
    "lag_1d",
    "lag_2d",
    "lag_7d",
    "lag_14d",
    "mean_24h",
    "mean_168h",
    "last_value",
)
DAY_TYPE_CODE = {"workday": 0, "saturday": 1, "sunday_holiday": 2}


def to_ms(ts: datetime) -> int:
    return int(ts.timestamp() * 1000)


class History:
    """Hourly series with point lookup and window means, filtered by a cutoff."""

    def __init__(self, ts_ms: np.ndarray, values: np.ndarray) -> None:
        order = np.argsort(ts_ms, kind="stable")
        self.ts = np.asarray(ts_ms, dtype=np.int64)[order]
        self.values = np.asarray(values, dtype=np.float64)[order]
        if self.ts.size and np.any(np.diff(self.ts) <= 0):
            raise ValueError("history timestamps must be unique")
        self._index = {int(t): i for i, t in enumerate(self.ts)}
        self._cumsum = np.concatenate(([0.0], np.cumsum(self.values)))

    @classmethod
    def from_frame(cls, frame: pl.DataFrame) -> History:
        """Build from a frame with columns ``ts_ms`` (Int64) and ``value`` (Float64)."""
        return cls(frame["ts_ms"].to_numpy(), frame["value"].to_numpy())

    def __len__(self) -> int:
        return int(self.ts.size)

    def value_at(self, ts: datetime, cutoff: datetime) -> float:
        """Value of the hour starting at ``ts`` if that hour ended by ``cutoff``."""
        if to_ms(ts) + HOUR_MS > to_ms(cutoff):
            return math.nan
        i = self._index.get(to_ms(ts))
        return math.nan if i is None else float(self.values[i])

    def _window(self, cutoff: datetime, hours: int) -> tuple[int, int]:
        end_ms = to_ms(cutoff)
        lo = int(np.searchsorted(self.ts, end_ms - hours * HOUR_MS, side="left"))
        hi = int(np.searchsorted(self.ts, end_ms - HOUR_MS, side="right"))
        return lo, hi

    def hours_present(self, cutoff: datetime, hours: int) -> int:
        """How many of the ``hours`` hours that ended last by ``cutoff`` exist."""
        lo, hi = self._window(cutoff, hours)
        return hi - lo

    def window_mean(self, cutoff: datetime, hours: int) -> float:
        """Mean of the ``hours`` hours that ended last by ``cutoff``; NaN if under half exist."""
        lo, hi = self._window(cutoff, hours)
        if (hi - lo) * 2 < hours:
            return math.nan
        return float((self._cumsum[hi] - self._cumsum[lo]) / (hi - lo))

    def last_value(self, cutoff: datetime, max_age_hours: int = 6) -> float:
        """Newest hour that ended by ``cutoff``, if it ended at most ``max_age_hours`` before."""
        end_ms = to_ms(cutoff)
        hi = int(np.searchsorted(self.ts, end_ms - HOUR_MS, side="right"))
        if hi == 0 or end_ms - (int(self.ts[hi - 1]) + HOUR_MS) > max_age_hours * HOUR_MS:
            return math.nan
        return float(self.values[hi - 1])

    def has_hour_before(self, cutoff: datetime) -> bool:
        """True if the hour that ends exactly at ``cutoff`` is present."""
        return to_ms(cutoff) - HOUR_MS in self._index


@dataclass(frozen=True)
class DayRows:
    delivery_day: date
    targets: list[datetime]
    matrix: np.ndarray  # shape (hours, len(FEATURES))


def day_features(history: History, delivery_day: date) -> DayRows:
    cutoff = cal.cutoff_for(delivery_day)
    targets = cal.day_hours(delivery_day)
    hours_in_day = len(targets)
    dtype = DAY_TYPE_CODE[cal.day_type(delivery_day)]
    common = (
        delivery_day.weekday(),
        delivery_day.month,
        delivery_day.timetuple().tm_yday,
        dtype,
        float(cal.is_holiday(delivery_day)),
        float(cal.is_bridge_day(delivery_day)),
        hours_in_day,
    )
    mean_24h = history.window_mean(cutoff, 24)
    mean_168h = history.window_mean(cutoff, 168)
    last = history.last_value(cutoff)
    rows = []
    for ts in targets:
        hour_local = ts.astimezone(cal.BERLIN).hour
        rows.append(
            [
                hour_local,
                *common,
                history.value_at(cal.shift_local(ts, 1), cutoff),
                history.value_at(cal.shift_local(ts, 2), cutoff),
                history.value_at(cal.shift_local(ts, 7), cutoff),
                history.value_at(cal.shift_local(ts, 14), cutoff),
                mean_24h,
                mean_168h,
                last,
            ]
        )
    return DayRows(delivery_day, targets, np.asarray(rows, dtype=np.float64))


# Share of the 168 hours before the cutoff that must exist for a forecast that
# is not marked as degraded.
MIN_WEEK_COVERAGE = 0.9


def feature_gaps(history: History, delivery_day: date) -> list[str]:
    """Names of the history checks that fail for ``delivery_day`` (empty if none).

    ``lag_7d`` is missing for some target hour, ``mean_24h`` or ``mean_168h``
    could not be computed, or ``coverage_168h``: fewer than
    ``MIN_WEEK_COVERAGE`` of the 168 hours before the cutoff exist.
    """
    cutoff = cal.cutoff_for(delivery_day)
    gaps = []
    if any(math.isnan(v) for v in baseline_week(history, delivery_day)):
        gaps.append("lag_7d")
    for hours in (24, 168):
        if math.isnan(history.window_mean(cutoff, hours)):
            gaps.append(f"mean_{hours}h")
    if history.hours_present(cutoff, 168) < MIN_WEEK_COVERAGE * 168:
        gaps.append("coverage_168h")
    return gaps


def baseline_week(history: History, delivery_day: date) -> np.ndarray:
    """Same local hour seven days earlier (naive seasonal baseline)."""
    cutoff = cal.cutoff_for(delivery_day)
    return np.array(
        [history.value_at(cal.shift_local(ts, 7), cutoff) for ts in cal.day_hours(delivery_day)]
    )


def actuals(history: History, targets: Iterable[datetime]) -> np.ndarray:
    return np.array([history.value_at(ts, cal.never()) for ts in targets])
