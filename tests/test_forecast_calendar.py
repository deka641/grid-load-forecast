from datetime import UTC, date, datetime, timedelta

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from gridload.forecast import calendar as cal
from gridload.forecast.features import History


@pytest.mark.parametrize(
    ("day", "hours"), [(date(2025, 3, 30), 23), (date(2025, 10, 26), 25), (date(2025, 6, 1), 24)]
)
def test_day_hours_follow_clock_changes(day: date, hours: int) -> None:
    stamps = cal.day_hours(day)
    assert len(stamps) == hours
    assert len(set(stamps)) == hours
    assert all(cal.local_day(ts) == day for ts in stamps)


def test_origin_and_cutoff_are_local_noon_and_ten() -> None:
    day = date(2025, 10, 27)
    assert cal.origin_for(day).astimezone(cal.BERLIN) == datetime(
        2025, 10, 26, 12, tzinfo=cal.BERLIN
    )
    assert cal.cutoff_for(day).astimezone(cal.BERLIN) == datetime(
        2025, 10, 26, 10, tzinfo=cal.BERLIN
    )


def test_shift_into_missing_spring_hour_uses_previous_hour() -> None:
    target = datetime(2025, 4, 6, 0, tzinfo=UTC)  # 02:00 CEST
    shifted = cal.shift_local(target, 7).astimezone(cal.BERLIN)
    assert shifted.replace(tzinfo=None) == datetime(2025, 3, 30, 1)


@pytest.mark.parametrize(("utc_hour", "fold"), [(0, 0), (1, 1)])
def test_shift_from_repeated_autumn_hour_keeps_wall_clock(utc_hour: int, fold: int) -> None:
    target = datetime(2025, 10, 26, utc_hour, tzinfo=UTC)
    assert target.astimezone(cal.BERLIN).fold == fold
    shifted = cal.shift_local(target, 7).astimezone(cal.BERLIN)
    assert shifted.replace(tzinfo=None) == datetime(2025, 10, 19, 2)


def test_shift_into_repeated_autumn_hour_uses_first_occurrence() -> None:
    target = datetime(2025, 11, 2, 1, tzinfo=UTC)  # 02:00 CET on a normal day
    shifted = cal.shift_local(target, 7)
    assert shifted == datetime(2025, 10, 26, 0, tzinfo=UTC)


@given(
    st.datetimes(min_value=datetime(2016, 1, 1), max_value=datetime(2035, 12, 31)),
    st.integers(1, 14),
)
def test_shift_preserves_local_hour_where_it_exists(naive: datetime, days: int) -> None:
    ts = naive.replace(minute=0, second=0, microsecond=0, tzinfo=UTC)
    shifted = cal.shift_local(ts, days)
    source = ts.astimezone(cal.BERLIN)
    result = shifted.astimezone(cal.BERLIN)
    assert result.date() == source.date() - timedelta(days=days)
    assert result.hour in (source.hour, source.hour - 1)


def test_holidays_bridge_days_and_day_types() -> None:
    assert cal.is_holiday(date(2025, 10, 3))
    assert not cal.is_holiday(date(2025, 10, 31))  # regional only
    assert cal.is_bridge_day(date(2025, 5, 30))  # Friday after Ascension
    assert not cal.is_bridge_day(date(2025, 6, 2))
    assert cal.day_type(date(2025, 12, 25)) == "sunday_holiday"
    assert cal.day_type(date(2025, 11, 23)) == "sunday_holiday"
    assert cal.day_type(date(2025, 11, 22)) == "saturday"
    assert cal.day_type(date(2025, 11, 24)) == "workday"
    assert not cal.is_workday(date(2025, 11, 22))


def test_days_between_and_never() -> None:
    assert cal.days_between(date(2025, 1, 30), date(2025, 2, 1)) == [
        date(2025, 1, 30),
        date(2025, 1, 31),
        date(2025, 2, 1),
    ]
    assert cal.days_between(date(2025, 2, 1), date(2025, 1, 1)) == []
    assert cal.never() > datetime(9000, 1, 1, tzinfo=UTC)


def test_history_lookups_respect_cutoff() -> None:
    """Hourly values are stamped at the start of their hour and known once it ended."""
    base = int(datetime(2025, 1, 1, tzinfo=UTC).timestamp() * 1000)
    ts = np.array([base + 2 * 3_600_000, base, base + 3_600_000])
    history = History(ts, np.array([3.0, 1.0, 2.0]))
    t0 = datetime(2025, 1, 1, tzinfo=UTC)
    hour = timedelta(hours=1)
    # The hour 02:00 to 03:00 is unknown at 02:59 and known from 03:00.
    assert np.isnan(history.value_at(t0 + 2 * hour, t0 + 3 * hour - timedelta(minutes=1)))
    assert history.value_at(t0 + 2 * hour, t0 + 3 * hour) == 3.0
    assert np.isnan(history.value_at(t0 + 9 * hour, cal.never()))
    assert history.window_mean(t0 + 3 * hour, 2) == 2.5
    assert history.window_mean(t0 + 2 * hour, 2) == 1.5
    assert np.isnan(history.window_mean(t0 + 3 * hour, 24))
    assert history.last_value(t0 + 4 * hour) == 3.0
    assert history.last_value(t0 + 3 * hour) == 3.0
    assert history.last_value(t0 + 2 * hour) == 2.0
    assert np.isnan(history.last_value(t0 + 10 * hour))
    assert np.isnan(History(np.array([], dtype=np.int64), np.array([])).last_value(t0))
    assert history.has_hour_before(t0 + 3 * hour)
    assert not history.has_hour_before(t0 + 4 * hour)
    assert len(history) == 3


def test_history_rejects_duplicate_timestamps() -> None:
    with pytest.raises(ValueError, match="unique"):
        History(np.array([1, 1]), np.array([1.0, 2.0]))
