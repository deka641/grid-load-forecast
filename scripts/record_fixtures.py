"""Record SMARD responses used as test fixtures.

Run manually (needs network): ``uv run python scripts/record_fixtures.py``.
Files are stored verbatim under ``tests/fixtures/smard/`` with the same names
as on the server, so tests can serve them through a mock transport.

Selection (each chunk starts on Monday 00:00 Europe/Berlin):
- load: spring and autumn clock-change weeks, a regular week, the newest week;
- price: last week with hourly prices repeated per quarter hour, the week of the
  switch to 15 minute prices, a week with negative prices, the newest week;
- hourly files for the same weeks, to check the derived hourly values;
- all generation series and the SMARD load forecast for the regular week;
- the newest chunk of every series (for nuclear power, which ended in 2023, its last).
"""

import sys
from datetime import datetime
from pathlib import Path

import httpx2

from gridload.config import DEFAULT_BASE_URL, USER_AGENT
from gridload.series import BY_KEY, Category, Series
from gridload.sources.smard import BERLIN, SmardClient, parse_index

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "smard"


def week(year: int, month: int, day: int) -> int:
    """Chunk start for the week beginning on the given Monday, in UTC ms."""
    start = datetime(year, month, day, tzinfo=BERLIN)
    if start.weekday() != 0:
        raise ValueError(f"{start.date()} is not a Monday")
    return int(start.timestamp() * 1000)


SPRING_DST_2025 = week(2025, 3, 24)
AUTUMN_DST_2025 = week(2025, 10, 20)
PRE_SWITCH_2025 = week(2025, 9, 22)  # hourly prices repeated per quarter hour
SWITCH_2025 = week(2025, 9, 29)  # first week with 15 minute prices
NEGATIVE_PRICES_2025 = week(2025, 5, 5)  # minimum -250.32 EUR/MWh
REGULAR_2026 = week(2026, 11, 9)


def save(name: str, payload: bytes) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_bytes(payload)
    print(f"{name}: {len(payload)} bytes")


def fetch(http: httpx2.Client, path: str) -> bytes:
    response = http.get(f"{DEFAULT_BASE_URL}/{path}")
    response.raise_for_status()
    return response.content


def record(smard: SmardClient, http: httpx2.Client, series: Series, chunks: list[int]) -> None:
    prefix = f"{series.filter_id}/{series.region}"
    index_q = fetch(http, f"{prefix}/index_quarterhour.json")
    save(f"{series.filter_id}_{series.region}_index_quarterhour.json", index_q)
    stamps = parse_index(index_q)
    # Series that ended (nuclear power, 2023) only get their final chunk.
    wanted = [ts for ts in chunks if ts in stamps]
    for ts in dict.fromkeys([*wanted, stamps[-1]]):
        raw = smard.chunk(series, ts)
        save(f"{series.filter_id}_{series.region}_quarterhour_{ts}.json", raw.payload)


def record_hourly(http: httpx2.Client, series: Series, chunks: list[int]) -> None:
    for ts in chunks:
        name = f"{series.filter_id}_{series.region}_hour_{ts}.json"
        save(name, fetch(http, f"{series.filter_id}/{series.region}/{name}"))


def main() -> int:
    http = httpx2.Client(headers={"User-Agent": USER_AGENT}, timeout=30.0)
    smard = SmardClient(http)
    try:
        load_weeks = [SPRING_DST_2025, AUTUMN_DST_2025, REGULAR_2026]
        price_weeks = [NEGATIVE_PRICES_2025, PRE_SWITCH_2025, SWITCH_2025, REGULAR_2026]
        record(smard, http, BY_KEY["load"], load_weeks)
        record_hourly(http, BY_KEY["load"], [SPRING_DST_2025, AUTUMN_DST_2025])
        record(smard, http, BY_KEY["price_day_ahead"], price_weeks)
        record_hourly(http, BY_KEY["price_day_ahead"], [PRE_SWITCH_2025, SWITCH_2025])
        record(smard, http, BY_KEY["load_forecast_tso"], [REGULAR_2026])
        for series in BY_KEY.values():
            if series.category is Category.GENERATION:
                record(smard, http, series, [REGULAR_2026])
    finally:
        smard.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
