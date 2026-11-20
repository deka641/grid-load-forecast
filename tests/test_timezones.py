import itertools
from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st

from gridload import timezones as tz

SPRING_2025 = date(2025, 3, 30)
AUTUMN_2025 = date(2025, 10, 26)
SPRING_2026 = date(2026, 3, 29)
AUTUMN_2026 = date(2026, 10, 25)


def utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def wall(stamps: list[datetime]) -> list[tuple[int, int]]:
    """Local (hour, fold) of every timestamp."""
    return [(t.hour, t.fold) for t in (tz.to_local(s) for s in stamps)]


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 1, 15), utc(2026, 1, 14, 23)),
        (date(2026, 7, 15), utc(2026, 7, 14, 22)),
        (SPRING_2026, utc(2026, 3, 28, 23)),
        (date(2026, 3, 30), utc(2026, 3, 29, 22)),
        (AUTUMN_2025, utc(2025, 10, 25, 22)),
        (date(2025, 10, 27), utc(2025, 10, 26, 23)),
        (date(2026, 1, 1), utc(2025, 12, 31, 23)),
    ],
)
def test_local_midnight_in_utc(day: date, expected: datetime) -> None:
    result = tz.local_midnight(day)
    assert result == expected
    assert result.tzinfo is UTC


@pytest.mark.parametrize(
    ("day", "count"),
    [
        (SPRING_2025, 23),
        (SPRING_2026, 23),
        (date(2026, 3, 28), 24),
        (date(2026, 6, 1), 24),
        (AUTUMN_2025, 25),
        (AUTUMN_2026, 25),
        (date(2025, 12, 31), 24),
        (date(2026, 1, 1), 24),
    ],
)
def test_day_hours_count_and_spacing(day: date, count: int) -> None:
    hours = tz.day_hours(day)
    assert len(hours) == count
    assert hours[0] == tz.local_midnight(day)
    assert all(b - a == timedelta(hours=1) for a, b in itertools.pairwise(hours))
    assert hours[-1] + timedelta(hours=1) == tz.local_midnight(day + timedelta(days=1))
    assert all(h.tzinfo is UTC for h in hours)
    assert all(tz.to_local(h).date() == day for h in hours)


def test_spring_day_skips_local_two_oclock() -> None:
    assert wall(tz.day_hours(SPRING_2026)) == [(0, 0), (1, 0)] + [(h, 0) for h in range(3, 24)]


def test_autumn_day_repeats_local_two_oclock() -> None:
    hours = tz.day_hours(AUTUMN_2025)
    assert wall(hours) == [(0, 0), (1, 0), (2, 0), (2, 1)] + [(h, 0) for h in range(3, 24)]
    assert [h.isoformat() for h in hours[2:4]] == [
        "2025-10-26T00:00:00+00:00",
        "2025-10-26T01:00:00+00:00",
    ]


def test_day_range_is_half_open_over_local_days() -> None:
    assert tz.day_range(SPRING_2026, SPRING_2026) == (utc(2026, 3, 28, 23), utc(2026, 3, 29, 22))
    assert tz.day_range(AUTUMN_2025, AUTUMN_2025) == (utc(2025, 10, 25, 22), utc(2025, 10, 26, 23))
    start, end = tz.day_range(date(2025, 12, 31), date(2026, 1, 1))
    assert (start, end) == (utc(2025, 12, 30, 23), utc(2026, 1, 1, 23))
    assert end - start == timedelta(hours=48)


def test_day_range_over_a_week_with_a_clock_change() -> None:
    start, end = tz.day_range(date(2026, 3, 23), date(2026, 3, 29))
    assert end - start == timedelta(hours=7 * 24 - 1)
    start, end = tz.day_range(date(2025, 10, 20), date(2025, 10, 26))
    assert end - start == timedelta(hours=7 * 24 + 1)


@pytest.mark.parametrize(
    ("ts", "local"),
    [
        # spring: 02:00 local does not exist, 01:59 CET is followed by 03:00 CEST
        (utc(2026, 3, 29, 0, 59), "2026-03-29T01:59:00+01:00"),
        (utc(2026, 3, 29, 1, 0), "2026-03-29T03:00:00+02:00"),
        # autumn: 02:00 local exists twice
        (utc(2025, 10, 26, 0, 0), "2025-10-26T02:00:00+02:00"),
        (utc(2025, 10, 26, 0, 59), "2025-10-26T02:59:00+02:00"),
        (utc(2025, 10, 26, 1, 0), "2025-10-26T02:00:00+01:00"),
        (utc(2025, 10, 26, 2, 0), "2025-10-26T03:00:00+01:00"),
        # year boundary
        (utc(2025, 12, 31, 22, 59), "2025-12-31T23:59:00+01:00"),
        (utc(2025, 12, 31, 23, 0), "2026-01-01T00:00:00+01:00"),
    ],
)
def test_to_local(ts: datetime, local: str) -> None:
    result = tz.to_local(ts)
    assert result.isoformat() == local
    assert result.tzinfo is tz.BERLIN
    # compare in UTC: an aware time in a repeated hour never equals one in another zone
    assert result.astimezone(UTC) == ts


def test_to_local_folds_on_autumn_day() -> None:
    assert tz.to_local(utc(2025, 10, 26, 0, 30)).fold == 0
    assert tz.to_local(utc(2025, 10, 26, 1, 30)).fold == 1


def test_to_local_accepts_any_aware_timestamp() -> None:
    ts = datetime(2026, 1, 1, 5, tzinfo=timezone(timedelta(hours=5)))
    assert tz.to_local(ts).isoformat() == "2026-01-01T01:00:00+01:00"


@given(st.dates(min_value=date(2000, 1, 1), max_value=date(2099, 12, 30)))
def test_consecutive_days_tile_utc_without_gaps(day: date) -> None:
    today, tomorrow = tz.day_hours(day), tz.day_hours(day + timedelta(days=1))
    assert tomorrow[0] - today[-1] == timedelta(hours=1)
    assert len(today) in (23, 24, 25)
    start, end = tz.day_range(day, day + timedelta(days=1))
    assert (start, end - timedelta(hours=1)) == (today[0], tomorrow[-1])
    local = tz.to_local(today[0])
    assert (local.date(), local.hour, local.minute) == (day, 0, 0)
