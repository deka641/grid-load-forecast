"""Conversions between UTC and local days in Europe/Berlin.

Timestamps are stored and computed in UTC. A local day has 23, 24 or 25 hours,
so day boundaries and hour grids are derived from local midnight and then
built in UTC: adding a timedelta to a zoneinfo datetime is wall-clock
arithmetic and would ignore the clock change.
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")


def local_midnight(day: date) -> datetime:
    """Return the UTC instant of local midnight at the start of ``day``."""
    return datetime.combine(day, time(0, 0), tzinfo=BERLIN).astimezone(UTC)


def day_range(first: date, last: date) -> tuple[datetime, datetime]:
    """Return the half-open UTC range covering the local days ``first`` to ``last``."""
    return local_midnight(first), local_midnight(last + timedelta(days=1))


def day_hours(day: date) -> list[datetime]:
    """Return the UTC start of every local hour of ``day`` (23, 24 or 25 entries)."""
    start, end = day_range(day, day)
    count = int((end - start) / timedelta(hours=1))
    return [start + timedelta(hours=i) for i in range(count)]


def to_local(ts: datetime) -> datetime:
    """Convert an aware timestamp to Europe/Berlin."""
    return ts.astimezone(BERLIN)
