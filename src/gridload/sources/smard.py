"""Client and validation for the SMARD chart data backend.

SMARD publishes each series in weekly chunks that start on Monday 00:00
Europe/Berlin. An index file lists the chunk start timestamps; each chunk file
holds ``[epoch_ms_utc, value | null]`` pairs. The backend is not a documented
API, so every response is validated strictly: a malformed, empty, truncated or
gapped chunk raises :class:`ChunkValidationError` and is never treated as
"no data".
"""

import hashlib
import itertools
import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx2
import polars as pl

from gridload.config import DEFAULT_BASE_URL, USER_AGENT
from gridload.series import Series

BERLIN = ZoneInfo("Europe/Berlin")
QUARTER_HOUR_MS = 15 * 60 * 1000
RESOLUTION = "quarterhour"
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


class SourceError(RuntimeError):
    """The source could not be reached or answered with an error."""


class ChunkValidationError(ValueError):
    """A response does not have the expected structure or completeness."""


@dataclass(frozen=True)
class RawChunk:
    """One fetched chunk file, kept verbatim for auditing and reprocessing."""

    series_key: str
    chunk_ts: int
    payload: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.payload).hexdigest()


def ms_to_datetime(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, UTC)


def chunk_end_ms(chunk_ts: int) -> int:
    """Return the exclusive end of the weekly chunk starting at ``chunk_ts``.

    The chunk covers seven local calendar days, so it is 167 hours long in the
    week of the spring clock change and 169 hours in the autumn week.
    """
    start_local = ms_to_datetime(chunk_ts).astimezone(BERLIN)
    if (start_local.weekday(), start_local.hour, start_local.minute) != (0, 0, 0):
        raise ChunkValidationError(
            f"chunk {chunk_ts} does not start on Monday 00:00 Europe/Berlin: {start_local}"
        )
    end_naive = start_local.replace(tzinfo=None) + timedelta(days=7)
    end_local = end_naive.replace(tzinfo=BERLIN)
    return int(end_local.astimezone(UTC).timestamp() * 1000)


def parse_index(payload: bytes) -> list[int]:
    """Parse and validate an index file into sorted chunk timestamps."""
    data = _load_json(payload)
    stamps = data.get("timestamps") if isinstance(data, dict) else None
    if not isinstance(stamps, list) or not stamps:
        raise ChunkValidationError("index has no timestamps")
    if not all(isinstance(t, int) and not isinstance(t, bool) for t in stamps):
        raise ChunkValidationError("index contains non-integer timestamps")
    if any(b <= a for a, b in itertools.pairwise(stamps)):
        raise ChunkValidationError("index timestamps are not strictly increasing")
    for ts in stamps:
        chunk_end_ms(ts)
    return list(stamps)


def parse_chunk(
    series: Series, chunk_ts: int, payload: bytes, *, allow_trailing_nulls: bool
) -> pl.DataFrame:
    """Validate a chunk and return its non-null values.

    Rules:
    - the series starts exactly at ``chunk_ts`` and advances in 15 minute steps;
    - it covers the whole local week (668, 672 or 676 values, depending on clock changes);
    - ``null`` is only allowed as a trailing run, and only when
      ``allow_trailing_nulls`` is set (newest or recently ended chunk), where it
      marks points not yet published;
    - values are finite numbers, energy values are not negative.
    """
    data = _load_json(payload)
    rows = data.get("series") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ChunkValidationError(f"{series.key} chunk {chunk_ts}: empty series")

    expected = (chunk_end_ms(chunk_ts) - chunk_ts) // QUARTER_HOUR_MS
    if len(rows) != expected:
        raise ChunkValidationError(
            f"{series.key} chunk {chunk_ts}: {len(rows)} points, expected {expected}"
        )

    stamps: list[int] = []
    values: list[float | None] = []
    for i, row in enumerate(rows):
        if not (isinstance(row, list) and len(row) == 2):
            raise ChunkValidationError(f"{series.key} chunk {chunk_ts}: malformed point {i}")
        ts, value = row
        if not isinstance(ts, int) or isinstance(ts, bool):
            raise ChunkValidationError(f"{series.key} chunk {chunk_ts}: bad timestamp at {i}")
        if ts != chunk_ts + i * QUARTER_HOUR_MS:
            raise ChunkValidationError(
                f"{series.key} chunk {chunk_ts}: gap or wrong step at point {i}"
            )
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise ChunkValidationError(f"{series.key} chunk {chunk_ts}: bad value at {i}")
            value = float(value)
            if not math.isfinite(value):
                raise ChunkValidationError(f"{series.key} chunk {chunk_ts}: non-finite at {i}")
            if series.non_negative and value < 0:
                raise ChunkValidationError(f"{series.key} chunk {chunk_ts}: negative at {i}")
        stamps.append(ts)
        values.append(value)

    first_null = next((i for i, v in enumerate(values) if v is None), len(values))
    if any(v is not None for v in values[first_null:]):
        raise ChunkValidationError(f"{series.key} chunk {chunk_ts}: null inside the series")
    if first_null < len(values) and not allow_trailing_nulls:
        raise ChunkValidationError(
            f"{series.key} chunk {chunk_ts}: trailing nulls in a completed chunk"
        )

    return pl.DataFrame(
        {"ts_ms": stamps[:first_null], "value": values[:first_null]},
        schema={"ts_ms": pl.Int64, "value": pl.Float64},
    ).with_columns(
        pl.from_epoch("ts_ms", time_unit="ms").dt.replace_time_zone("UTC").alias("ts_utc")
    )


def _load_json(payload: bytes) -> Any:
    if not payload:
        raise ChunkValidationError("empty response body")
    try:
        return json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ChunkValidationError(f"response is not valid JSON: {exc}") from exc


def _retry_after(value: str | None, cap: float = 60.0) -> float:
    """Seconds from a Retry-After header (numeric form only), capped."""
    try:
        return min(max(float(value), 0.0), cap) if value else 0.0
    except ValueError:
        return 0.0


class SmardClient:
    """Throttled HTTP client with retry and backoff for transient errors."""

    def __init__(
        self,
        client: httpx2.Client,
        *,
        base_url: str = DEFAULT_BASE_URL,
        min_interval: float = 0.5,
        retries: int = 4,
        backoff: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._min_interval = min_interval
        self._retries = retries
        self._backoff = backoff
        self._sleep = sleep
        self._clock = clock
        self._last_request: float | None = None

    @classmethod
    def create(cls, base_url: str = DEFAULT_BASE_URL) -> SmardClient:
        http = httpx2.Client(
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=httpx2.Timeout(30.0),
            follow_redirects=False,
        )
        return cls(http, base_url=base_url)

    def close(self) -> None:
        self._client.close()

    def index(self, series: Series) -> list[int]:
        url = f"{self._base_url}/{series.filter_id}/{series.region}/index_{RESOLUTION}.json"
        return parse_index(self._get(url))

    def chunk(self, series: Series, chunk_ts: int) -> RawChunk:
        name = f"{series.filter_id}_{series.region}_{RESOLUTION}_{chunk_ts}.json"
        url = f"{self._base_url}/{series.filter_id}/{series.region}/{name}"
        return RawChunk(series.key, chunk_ts, self._get(url))

    def fetch(self, path: str) -> bytes:
        """Fetch any file below the base URL (throttled, with retry)."""
        return self._get(f"{self._base_url}/{path.lstrip('/')}")

    def _throttle(self) -> None:
        if self._last_request is not None:
            wait = self._min_interval - (self._clock() - self._last_request)
            if wait > 0:
                self._sleep(wait)
        self._last_request = self._clock()

    def _get(self, url: str) -> bytes:
        attempt = 0
        while True:
            retry_after = 0.0
            self._throttle()
            try:
                response = self._client.get(url)
            except httpx2.TransportError as exc:
                error: str = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code == 200:
                    return response.content
                error = f"HTTP {response.status_code}"
                if response.status_code not in RETRY_STATUS:
                    raise SourceError(f"{url}: {error}")
                retry_after = _retry_after(response.headers.get("Retry-After"))
            if attempt >= self._retries:
                raise SourceError(f"{url}: {error} after {attempt + 1} attempts")
            self._sleep(max(self._backoff * 2**attempt, retry_after))
            attempt += 1
