"""Local calendar for the German delivery day.

All forecasting logic works on local delivery days in Europe/Berlin, which have
23, 24 or 25 hours. Timestamps stay in UTC; local time is derived where needed.
"""

from datetime import UTC, date, datetime, time, timedelta
from functools import cache
from zoneinfo import ZoneInfo

import holidays

BERLIN = ZoneInfo("Europe/Berlin")
ORIGIN_LOCAL_TIME = time(12, 0)
DATA_DELAY = timedelta(hours=2)


def local_midnight(day: date) -> datetime:
    return datetime.combine(day, time(0, 0), tzinfo=BERLIN).astimezone(UTC)


def day_hours(day: date) -> list[datetime]:
    """UTC start of every hour of the local day (23, 24 or 25 entries)."""
    start = local_midnight(day)
    end = local_midnight(day + timedelta(days=1))
    count = int((end - start) / timedelta(hours=1))
    return [start + timedelta(hours=i) for i in range(count)]


def origin_for(delivery_day: date) -> datetime:
    """Forecast origin: 12:00 local time on the day before delivery, in UTC."""
    return datetime.combine(
        delivery_day - timedelta(days=1), ORIGIN_LOCAL_TIME, tzinfo=BERLIN
    ).astimezone(UTC)


def cutoff_for(delivery_day: date) -> datetime:
    """End of the newest hour a feature may use: origin minus ``DATA_DELAY``.

    Hourly values are stamped with the start of their hour, so with an origin of
    12:00 the newest admissible value is the hour 09:00 to 10:00 local time. The
    two hours cover the measured publication lag of one to two hours.
    """
    return origin_for(delivery_day) - DATA_DELAY


def local_day(ts_utc: datetime) -> date:
    return ts_utc.astimezone(BERLIN).date()


def shift_local(ts_utc: datetime, days: int) -> datetime:
    """Same local wall-clock hour ``days`` earlier, as UTC.

    If that hour does not exist (spring clock change, 02:00), the previous hour
    is used. If it exists twice (autumn clock change), the occurrence with the
    same ``fold`` as the source timestamp is used.
    """
    local = ts_utc.astimezone(BERLIN)
    wall = local.replace(tzinfo=None) - timedelta(days=days)
    candidate = wall.replace(tzinfo=BERLIN, fold=local.fold)
    if candidate.astimezone(UTC).astimezone(BERLIN).replace(tzinfo=None) != wall:
        candidate = (wall - timedelta(hours=1)).replace(tzinfo=BERLIN)
    return candidate.astimezone(UTC)


@cache
def _holidays(year: int) -> frozenset[date]:
    return frozenset(holidays.country_holidays("DE", years=year).keys())


def is_holiday(day: date) -> bool:
    """Nationwide public holiday in Germany."""
    return day in _holidays(day.year)


def is_workday(day: date) -> bool:
    return day.weekday() < 5 and not is_holiday(day)


def is_bridge_day(day: date) -> bool:
    """A single working day between two non-working days (for example Friday after Ascension)."""
    return (
        is_workday(day)
        and not is_workday(day - timedelta(days=1))
        and not is_workday(day + timedelta(days=1))
    )


def day_type(day: date) -> str:
    if day.weekday() == 6 or is_holiday(day):
        return "sunday_holiday"
    if day.weekday() == 5:
        return "saturday"
    return "workday"


def days_between(first: date, last: date) -> list[date]:
    """All days from ``first`` to ``last``, both inclusive."""
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def never() -> datetime:
    """A cutoff that admits every timestamp (for reading actual values)."""
    return datetime.max.replace(tzinfo=UTC)
