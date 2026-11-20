"""Validation of query parameters and conversion of local days to UTC ranges."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi.exceptions import RequestValidationError

from gridload.series import Series

BERLIN = ZoneInfo("Europe/Berlin")
MIN_DAY = date(2000, 1, 1)
MAX_DAY = date(2099, 12, 31)
MAX_SERIES = 20


def invalid(name: str, value: object, message: str) -> RequestValidationError:
    """Build a 422 error in the same shape as FastAPI's own validation errors."""
    return RequestValidationError(
        [{"type": "value_error", "loc": ("query", name), "msg": message, "input": value}]
    )


def parse_day(name: str, value: str) -> date:
    """Parse a local day within the supported range.

    The route already enforces the ``YYYY-MM-DD`` shape with a pattern, so only
    calendar errors such as ``2025-02-30`` remain.
    """
    try:
        day = date.fromisoformat(value)
    except ValueError as exc:
        raise invalid(name, value, f"invalid date: {exc}") from exc
    if not MIN_DAY <= day <= MAX_DAY:
        raise invalid(name, value, f"date must be between {MIN_DAY} and {MAX_DAY}")
    return day


def parse_series(value: str, catalog: dict[str, Series]) -> list[Series]:
    """Parse a comma-separated list of series keys, keeping order and dropping repeats."""
    keys = [k.strip() for k in value.split(",")]
    if any(k == "" for k in keys):
        raise invalid("series", value, "empty series key")
    if len(keys) > MAX_SERIES:
        raise invalid("series", value, f"at most {MAX_SERIES} series per request")
    unknown = sorted({k for k in keys if k not in catalog})
    if unknown:
        raise invalid("series", value, f"unknown series: {', '.join(unknown)}")
    return [catalog[k] for k in dict.fromkeys(keys)]


def local_midnight_utc(day: date) -> datetime:
    """Return the UTC instant of local midnight at the start of ``day``."""
    return datetime.combine(day, time(0), tzinfo=BERLIN).astimezone(UTC)


def day_range_utc(first: date, last: date) -> tuple[datetime, datetime]:
    """Return the half-open UTC range covering the local days ``first`` to ``last``."""
    return local_midnight_utc(first), local_midnight_utc(last + timedelta(days=1))


def hours_of_day(day: date) -> list[datetime]:
    """Return the UTC start of every local hour of ``day`` (23, 24 or 25 hours).

    The grid is built in UTC because adding a timedelta to a zoneinfo datetime
    is wall-clock arithmetic and would ignore the clock change.
    """
    start, end = day_range_utc(day, day)
    count = int((end - start) / timedelta(hours=1))
    return [start + timedelta(hours=i) for i in range(count)]


def to_local(ts: datetime) -> datetime:
    """Convert an aware timestamp to Europe/Berlin."""
    return ts.astimezone(BERLIN)
