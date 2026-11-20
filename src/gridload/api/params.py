"""Validation of query parameters."""

from datetime import date

from fastapi.exceptions import RequestValidationError

from gridload.series import Series

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
