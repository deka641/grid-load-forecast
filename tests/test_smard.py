import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx2
import pytest
from hypothesis import given
from hypothesis import strategies as st

from gridload.config import USER_AGENT
from gridload.series import BY_KEY, CATALOG, Series
from gridload.sources.smard import (
    QUARTER_HOUR_MS,
    ChunkValidationError,
    SmardClient,
    SourceError,
    chunk_end_ms,
    missing_points,
    parse_chunk,
    parse_index,
)
from tests.fake_smard import FIXTURES, FakeSmard, chunk_name, fixture_bytes, recorded_chunks

BERLIN = ZoneInfo("Europe/Berlin")
LOAD = BY_KEY["load"]
PRICE = BY_KEY["price_day_ahead"]
SPRING = 1742770800000  # 2025-03-24, clock change on 2025-03-30
AUTUMN = 1760911200000  # 2025-10-20, clock change on 2025-10-26
REGULAR = 1794178800000  # 2026-11-09
NEGATIVE = 1746396000000  # 2025-05-05


def monday_ms(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=BERLIN).timestamp() * 1000)


def series_payload(start: int, values: list[float | None]) -> bytes:
    rows = [[start + i * QUARTER_HOUR_MS, v] for i, v in enumerate(values)]
    return json.dumps({"meta_data": {"version": 1}, "series": rows}).encode()


def full_week(start: int, value: float = 1.0) -> list[float | None]:
    return [value] * ((chunk_end_ms(start) - start) // QUARTER_HOUR_MS)


def latest_of(series: Series) -> int:
    return parse_index(fixture_bytes(f"{series.filter_id}_DE_index_quarterhour.json"))[-1]


# Recorded responses -----------------------------------------------------------------


@pytest.mark.parametrize("series", CATALOG, ids=lambda s: s.key)
def test_every_recorded_chunk_is_valid(series: Series) -> None:
    latest = latest_of(series)
    for ts in recorded_chunks(series):
        frame = parse_chunk(series, ts, fixture_bytes(chunk_name(series, ts)), is_open=ts == latest)
        assert frame.height > 0
        assert frame["ts_utc"].is_sorted()
        assert frame["ts_utc"].n_unique() == frame.height


@pytest.mark.parametrize(("chunk", "points"), [(SPRING, 668), (AUTUMN, 676), (REGULAR, 672)])
def test_clock_change_weeks_have_23_and_25_hour_sundays(chunk: int, points: int) -> None:
    frame = parse_chunk(LOAD, chunk, fixture_bytes(chunk_name(LOAD, chunk)), is_open=False)
    assert frame.height == points
    local_days = frame["ts_utc"].dt.convert_time_zone("Europe/Berlin").dt.date().value_counts()
    sunday = local_days.sort("ts_utc")["count"].to_list()[-1]
    assert sunday == {668: 92, 676: 100, 672: 96}[points]


def test_newest_chunk_drops_future_nulls() -> None:
    latest = latest_of(LOAD)
    payload = fixture_bytes(chunk_name(LOAD, latest))
    total = len(json.loads(payload)["series"])
    frame = parse_chunk(LOAD, latest, payload, is_open=True)
    assert 0 < frame.height < total


def test_negative_prices_are_valid() -> None:
    payload = fixture_bytes(chunk_name(PRICE, NEGATIVE))
    frame = parse_chunk(PRICE, NEGATIVE, payload, is_open=False)
    assert frame["value"].min() == pytest.approx(-250.32)


def test_real_index_files_parse() -> None:
    stamps = parse_index(fixture_bytes("410_DE_index_quarterhour.json"))
    assert stamps[0] == monday_ms(date(2014, 12, 29))
    assert REGULAR in stamps


# Validation errors --------------------------------------------------------------------

WEEK = monday_ms(date(2026, 11, 9))


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"", "empty response body"),
        (b"<html>maintenance</html>", "not valid JSON"),
        (b"\xff\xfe", "not valid JSON"),
        (b"[]", "empty series"),
        (b'{"series": []}', "empty series"),
        (b'{"meta_data": {}}', "empty series"),
    ],
)
def test_empty_or_broken_responses_are_errors(payload: bytes, message: str) -> None:
    with pytest.raises(ChunkValidationError, match=message):
        parse_chunk(LOAD, WEEK, payload, is_open=True)


def test_truncated_chunk_is_an_error() -> None:
    values = full_week(WEEK)[:-4]
    with pytest.raises(ChunkValidationError, match="expected 672"):
        parse_chunk(LOAD, WEEK, series_payload(WEEK, values), is_open=False)


def test_gap_is_an_error() -> None:
    rows = [[WEEK + i * QUARTER_HOUR_MS, 1.0] for i in range(673) if i != 100]
    payload = json.dumps({"series": rows}).encode()
    with pytest.raises(ChunkValidationError, match="gap or wrong step at point 100"):
        parse_chunk(LOAD, WEEK, payload, is_open=False)


def test_duplicate_timestamp_is_an_error() -> None:
    rows = [[WEEK + i * QUARTER_HOUR_MS, 1.0] for i in range(672)]
    rows[5][0] = rows[4][0]
    with pytest.raises(ChunkValidationError, match="point 5"):
        parse_chunk(LOAD, WEEK, json.dumps({"series": rows}).encode(), is_open=False)


def test_hourly_resolution_is_rejected_as_wrong_step() -> None:
    rows = [[WEEK + i * 4 * QUARTER_HOUR_MS, 1.0] for i in range(672)]
    with pytest.raises(ChunkValidationError, match="point 1"):
        parse_chunk(LOAD, WEEK, json.dumps({"series": rows}).encode(), is_open=False)


def test_chunk_must_start_at_its_timestamp() -> None:
    with pytest.raises(ChunkValidationError, match="point 0"):
        parse_chunk(
            LOAD,
            WEEK,
            series_payload(WEEK + QUARTER_HOUR_MS, full_week(WEEK)),
            is_open=False,
        )


def test_source_gaps_are_left_out_and_counted() -> None:
    values = full_week(WEEK)
    values[10:106] = [None] * 96  # one missing day, as in real SMARD data
    frame = parse_chunk(LOAD, WEEK, series_payload(WEEK, values), is_open=False)
    assert frame.height == 672 - 96
    assert frame["value"].null_count() == 0
    assert missing_points(frame, WEEK, is_open=False) == (96, WEEK + 10 * QUARTER_HOUR_MS)


def test_leading_gap_at_the_start_of_the_history() -> None:
    first = 1419807600000  # 2014-12-29: SMARD values start on 2015-01-01
    values: list[float | None] = [None] * 288 + [1.0] * 384
    frame = parse_chunk(LOAD, first, series_payload(first, values), is_open=False)
    assert frame.height == 384
    assert missing_points(frame, first, is_open=False) == (288, first)


def test_trailing_nulls_are_pending_in_open_chunks_and_gaps_in_completed_ones() -> None:
    values = full_week(WEEK)
    values[-8:] = [None] * 8
    payload = series_payload(WEEK, values)
    open_frame = parse_chunk(LOAD, WEEK, payload, is_open=True)
    assert open_frame.height == 664
    assert missing_points(open_frame, WEEK, is_open=True) == (0, None)
    closed = parse_chunk(LOAD, WEEK, payload, is_open=False)
    assert missing_points(closed, WEEK, is_open=False) == (8, WEEK + 664 * QUARTER_HOUR_MS)


def test_open_chunk_counts_gaps_before_the_newest_value() -> None:
    values = full_week(WEEK)
    values[5] = None
    values[-8:] = [None] * 8
    frame = parse_chunk(LOAD, WEEK, series_payload(WEEK, values), is_open=True)
    assert missing_points(frame, WEEK, is_open=True) == (1, WEEK + 5 * QUARTER_HOUR_MS)


def test_completed_chunk_without_values_is_an_error() -> None:
    with pytest.raises(ChunkValidationError, match="no values in a completed chunk"):
        parse_chunk(LOAD, WEEK, series_payload(WEEK, [None] * 672), is_open=False)


def test_empty_open_chunk_has_no_gap() -> None:
    frame = parse_chunk(LOAD, WEEK, series_payload(WEEK, [None] * 672), is_open=True)
    assert missing_points(frame, WEEK, is_open=True) == (0, None)


def test_newest_chunk_without_any_value_yields_no_rows() -> None:
    payload = series_payload(WEEK, [None] * 672)
    assert parse_chunk(LOAD, WEEK, payload, is_open=True).height == 0


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ([1, 2, 3], "malformed point"),
        ("x", "malformed point"),
        (["t", 1.0], "bad timestamp"),
        ([True, 1.0], "bad timestamp"),
        ([None, "12"], "bad timestamp"),
    ],
)
def test_malformed_points_are_errors(row: object, message: str) -> None:
    rows: list[object] = [[WEEK + i * QUARTER_HOUR_MS, 1.0] for i in range(672)]
    rows[0] = row
    with pytest.raises(ChunkValidationError, match=message):
        parse_chunk(LOAD, WEEK, json.dumps({"series": rows}).encode(), is_open=False)


@pytest.mark.parametrize("bad", ["12", True, [1]])
def test_non_numeric_values_are_errors(bad: object) -> None:
    rows: list[list[object]] = [[WEEK + i * QUARTER_HOUR_MS, 1.0] for i in range(672)]
    rows[3][1] = bad
    with pytest.raises(ChunkValidationError, match="bad value at 3"):
        parse_chunk(LOAD, WEEK, json.dumps({"series": rows}).encode(), is_open=False)


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_values_are_errors(literal: str) -> None:
    rows = [[WEEK + i * QUARTER_HOUR_MS, 1.0] for i in range(672)]
    text = json.dumps({"series": rows}).replace("1.0]", f"{literal}]", 1)
    with pytest.raises(ChunkValidationError, match="non-finite"):
        parse_chunk(LOAD, WEEK, text.encode(), is_open=False)


def test_negative_energy_is_an_error_but_negative_price_is_not() -> None:
    values = full_week(WEEK)
    values[0] = -5.0
    payload = series_payload(WEEK, values)
    with pytest.raises(ChunkValidationError, match="negative"):
        parse_chunk(LOAD, WEEK, payload, is_open=False)
    assert parse_chunk(PRICE, WEEK, payload, is_open=False)["value"][0] == -5.0


def test_integer_values_are_accepted_as_float() -> None:
    values: list[float | None] = [int(v) if v is not None else None for v in full_week(WEEK, 7)]
    frame = parse_chunk(LOAD, WEEK, series_payload(WEEK, values), is_open=False)
    assert frame["value"].dtype.is_float()


# Index -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b'{"timestamps": []}', "no timestamps"),
        (b"{}", "no timestamps"),
        (b"[]", "no timestamps"),
        (b'{"timestamps": [1.5]}', "non-integer"),
        (b'{"timestamps": [true]}', "non-integer"),
        (b'{"timestamps": [1794178800000, 1794178800000]}', "strictly increasing"),
        (b'{"timestamps": [1794182400000]}', "Monday 00:00"),
    ],
)
def test_invalid_index_is_an_error(payload: bytes, message: str) -> None:
    with pytest.raises(ChunkValidationError, match=message):
        parse_index(payload)


# Week boundaries ----------------------------------------------------------------------


@given(st.dates(min_value=date(2014, 12, 29), max_value=date(2040, 12, 31)))
def test_week_length_follows_clock_changes(day: date) -> None:
    monday = day - timedelta(days=day.weekday())
    start = monday_ms(monday)
    hours = (chunk_end_ms(start) - start) // 3_600_000
    next_monday = monday + timedelta(days=7)
    end_local = datetime.fromtimestamp(chunk_end_ms(start) / 1000, BERLIN)
    assert end_local == datetime(
        next_monday.year, next_monday.month, next_monday.day, tzinfo=BERLIN
    )

    def offset_hours(d: date) -> int:
        offset = datetime(d.year, d.month, d.day, tzinfo=BERLIN).utcoffset()
        assert offset is not None
        return int(offset.total_seconds() // 3600)

    offset_change = offset_hours(next_monday) - offset_hours(monday)
    assert hours == 168 - offset_change


# Client ------------------------------------------------------------------------------


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def __call__(self) -> float:
        return self.now


def test_requests_are_throttled() -> None:
    fake = FakeSmard()
    clock = FakeClock()
    client = fake.client(min_interval=0.5, sleep=clock.sleep, clock=clock)
    client.index(LOAD)
    client.index(LOAD)
    clock.now += 2.0
    client.index(LOAD)
    assert clock.sleeps == [0.5]


def test_transient_errors_are_retried_with_backoff() -> None:
    fake = FakeSmard()
    statuses = iter([503, 429, 200])
    original = fake.handle

    def flaky(request: httpx2.Request) -> httpx2.Response:
        status = next(statuses)
        return original(request) if status == 200 else httpx2.Response(status)

    clock = FakeClock()
    http = httpx2.Client(transport=httpx2.MockTransport(flaky))
    client = SmardClient(http, min_interval=0.0, backoff=1.0, sleep=clock.sleep, clock=clock)
    assert client.index(LOAD)
    assert clock.sleeps == [1.0, 2.0]


def test_transport_errors_are_retried_and_then_reported() -> None:
    calls = 0

    def broken(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        raise httpx2.ConnectTimeout("timed out", request=request)

    clock = FakeClock()
    http = httpx2.Client(transport=httpx2.MockTransport(broken))
    client = SmardClient(http, min_interval=0.0, retries=2, sleep=clock.sleep, clock=clock)
    with pytest.raises(SourceError, match=r"ConnectTimeout.*after 3 attempts"):
        client.index(LOAD)
    assert calls == 3


def test_client_errors_are_not_retried() -> None:
    fake = FakeSmard()
    client = fake.client()
    with pytest.raises(SourceError, match="HTTP 404"):
        client.chunk(LOAD, 1)
    assert len(fake.requests) == 1


def test_production_client_sends_neutral_user_agent() -> None:
    client = SmardClient.create()
    try:
        headers = client._client.headers
        assert headers["User-Agent"] == USER_AGENT
    finally:
        client.close()


def test_raw_chunk_keeps_payload_and_hash() -> None:
    fake = FakeSmard()
    raw = fake.client().chunk(LOAD, REGULAR)
    assert raw.payload == (FIXTURES / chunk_name(LOAD, REGULAR)).read_bytes()
    assert len(raw.sha256) == 64


def test_retry_after_header_is_honoured() -> None:
    responses = iter(
        [
            httpx2.Response(429, headers={"Retry-After": "30"}),
            httpx2.Response(503, headers={"Retry-After": "soon"}),
            httpx2.Response(503, headers={"Retry-After": "999"}),
        ]
    )
    final = FakeSmard()

    def handler(request: httpx2.Request) -> httpx2.Response:
        return next(responses, None) or final.handle(request)

    clock = FakeClock()
    http = httpx2.Client(transport=httpx2.MockTransport(handler))
    client = SmardClient(http, min_interval=0.0, backoff=1.0, sleep=clock.sleep, clock=clock)
    assert client.index(LOAD)
    assert clock.sleeps == [30.0, 2.0, 60.0]


def test_fetch_reads_any_file() -> None:
    fake = FakeSmard()
    payload = fake.client().fetch("/410/DE/410_DE_hour_1742770800000.json")
    assert payload == fixture_bytes("410_DE_hour_1742770800000.json")
