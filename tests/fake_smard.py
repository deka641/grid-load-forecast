"""Serve recorded SMARD responses through an in-memory HTTP transport."""

import itertools
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2

from gridload.config import DEFAULT_BASE_URL
from gridload.series import Series
from gridload.sources.smard import SmardClient

FIXTURES = Path(__file__).parent / "fixtures" / "smard"

Handler = Callable[[httpx2.Request], httpx2.Response | None]

# A moment shortly after the fixtures were recorded: the newest recorded chunks end
# after it and are still open (their last value is 09:45 UTC). Tests whose
# result depends on "now" use this instead of the wall clock, so they do not change
# as the fixtures age.
FIXTURE_NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def fixture_clock() -> datetime:
    return FIXTURE_NOW


def advancing_clock(start: datetime = FIXTURE_NOW) -> Callable[[], datetime]:
    """A clock that starts at ``start`` and moves one second forward per reading."""
    ticks = itertools.count()
    return lambda: start + timedelta(seconds=next(ticks))


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def chunk_name(series: Series, ts: int, res: str = "quarterhour") -> str:
    return f"{series.filter_id}_{series.region}_{res}_{ts}.json"


def recorded_chunks(series: Series) -> list[int]:
    stamps = [
        int(p.stem.rsplit("_", 1)[1])
        for p in FIXTURES.glob(f"{series.filter_id}_{series.region}_quarterhour_*.json")
    ]
    return sorted(stamps)


class FakeSmard:
    """Answer index and chunk requests from fixtures, with optional overrides.

    ``indexes`` replaces index files with a given list of chunk timestamps, so a
    test can work with a short, consistent history. ``responses`` maps a file
    name to ``(status, body)``. ``requests`` records every requested path.
    """

    def __init__(self) -> None:
        self.indexes: dict[int, list[int]] = {}
        self.responses: dict[str, tuple[int, bytes]] = {}
        self.requests: list[str] = []

    def use_index(self, series: Series, stamps: list[int]) -> None:
        self.indexes[series.filter_id] = stamps

    def override(self, name: str, body: bytes, status: int = 200) -> None:
        self.responses[name] = (status, body)

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        self.requests.append(path)
        name = path.rsplit("/", 1)[1]
        if name in self.responses:
            status, body = self.responses[name]
            return httpx2.Response(status, content=body)
        if name == "index_quarterhour.json":
            filter_id = int(path.split("/")[-3])
            if filter_id in self.indexes:
                body = json.dumps({"timestamps": self.indexes[filter_id]}).encode()
                return httpx2.Response(200, content=body)
            name = f"{filter_id}_DE_index_quarterhour.json"
        file = FIXTURES / name
        if file.exists():
            return httpx2.Response(200, content=file.read_bytes())
        return httpx2.Response(404, content=b"not found")

    def client(self, **kwargs: object) -> SmardClient:
        http = httpx2.Client(transport=httpx2.MockTransport(self.handle))
        options: dict[str, object] = {"min_interval": 0.0, "sleep": lambda _s: None}
        options.update(kwargs)
        return SmardClient(http, base_url=DEFAULT_BASE_URL, **options)  # type: ignore[arg-type]
