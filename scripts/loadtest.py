"""Load test against a running stack (isolated instance, never the live one).

Usage::

    uv run python scripts/loadtest.py --base http://127.0.0.1:8461 \
        --project grid-load-forecast-check --clients 10 --duration 60

Simulates dashboard visitors in parallel: each client repeatedly requests the
status, the forecast of the newest delivery day, the backtest metrics, the last
seven days of generation and price, and the largest allowed actuals requests
(one year of all series hourly, 31 days quarter-hourly). Container memory is
sampled with ``docker stats`` during the run and compared with the configured
limits. Exit code 1 if a request failed with anything other than the API's own
"busy" 503, or if a container used more than 90 % of its memory limit.
"""

import argparse
import asyncio
import json
import math
import random
import statistics
import subprocess
import sys
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

import httpx2

MEMORY_SHARE_LIMIT = 0.9
BUSY_SHARE_LIMIT = 0.05


@dataclass
class Stats:
    latencies: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    statuses: dict[str, dict[int, int]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(int))
    )
    failures: list[str] = field(default_factory=list)
    busy: int = 0
    total: int = 0


def scenarios(newest: date, keys: list[str]) -> dict[str, tuple[str, dict[str, str]]]:
    week = (newest - timedelta(days=6)).isoformat()
    year = (newest - timedelta(days=365)).isoformat()
    month = (newest - timedelta(days=30)).isoformat()
    generation = [k for k in keys if k.startswith("gen_")]
    return {
        "status": ("/api/v1/status", {}),
        "forecast": ("/api/v1/forecasts", {}),
        "forecast_days": ("/api/v1/forecasts/days", {}),
        "backtest": ("/api/v1/backtest/metrics", {}),
        "mix_7d": (
            "/api/v1/actuals",
            {
                "series": ",".join(generation),
                "start": week,
                "end": newest.isoformat(),
                "resolution": "hour",
            },
        ),
        "all_series_1y_hourly": (
            "/api/v1/actuals",
            {
                "series": ",".join(keys),
                "start": year,
                "end": newest.isoformat(),
                "resolution": "hour",
            },
        ),
        "load_31d_quarterhour": (
            "/api/v1/actuals",
            {
                "series": "load",
                "start": month,
                "end": newest.isoformat(),
                "resolution": "quarterhour",
            },
        ),
    }


async def client(
    http: httpx2.AsyncClient,
    plan: dict[str, tuple[str, dict[str, str]]],
    stats: Stats,
    stop_at: float,
    seed: int,
) -> None:
    rng = random.Random(seed)  # noqa: S311  # request mix only, not security relevant
    names = list(plan)
    while time.monotonic() < stop_at:
        name = rng.choice(names)
        path, params = plan[name]
        start = time.perf_counter()
        try:
            response = await http.get(path, params=params)
        except httpx2.HTTPError as exc:
            stats.failures.append(f"{name}: {type(exc).__name__}")
            continue
        stats.total += 1
        stats.statuses[name][response.status_code] += 1
        if response.status_code == 503 and "busy" in response.text:
            stats.busy += 1  # the API's own load shedding: counted, not timed
            continue
        stats.latencies[name].append(time.perf_counter() - start)
        if response.status_code != 200:
            stats.failures.append(f"{name}: HTTP {response.status_code}")


def sample_memory(
    project: str, stop: threading.Event, peaks: dict[str, tuple[float, float]]
) -> None:
    """Record peak memory use and limit (MiB) per container of the compose project."""
    while not stop.is_set():
        try:
            _sample_once(project, peaks)
        except (OSError, ValueError, KeyError) as exc:
            print(f"memory sampling failed: {exc}", file=sys.stderr)
            return
        stop.wait(1.0)


def _sample_once(project: str, peaks: dict[str, tuple[float, float]]) -> None:
    result = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{json .}}"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    for line in result.stdout.splitlines():
        row = json.loads(line)
        name = row["Name"]
        if not name.startswith(project + "-"):
            continue
        used_text, limit_text = (part.strip() for part in row["MemUsage"].split("/"))
        used, limit = _mib(used_text), _mib(limit_text)
        if used > peaks.get(name, (0.0, limit))[0]:
            peaks[name] = (used, limit)


def _mib(text: str) -> float:
    units = {"KiB": 1 / 1024, "MiB": 1.0, "GiB": 1024.0, "B": 1 / 1024 / 1024}
    for unit, factor in units.items():
        if text.endswith(unit):
            return float(text[: -len(unit)]) * factor
    raise ValueError(f"unknown memory unit in {text!r}")


async def run(args: argparse.Namespace) -> int:
    async with httpx2.AsyncClient(base_url=args.base, timeout=60.0) as http:
        status = (await http.get("/api/v1/status")).json()
        keys = [s["key"] for s in (await http.get("/api/v1/series")).json()["series"]]
        load = next(s for s in status["series"] if s["key"] == "load")
        newest = date.fromisoformat(load["last_ts"][:10])
        plan = scenarios(newest, keys)
        stats = Stats()
        stop = threading.Event()
        peaks: dict[str, tuple[float, float]] = {}
        sampler = threading.Thread(target=sample_memory, args=(args.project, stop, peaks))
        sampler.start()
        stop_at = time.monotonic() + args.duration
        try:
            await asyncio.gather(
                *(client(http, plan, stats, stop_at, seed) for seed in range(args.clients))
            )
        finally:
            stop.set()
            sampler.join()

    print(f"{args.clients} clients for {args.duration} s against {args.base}\n")
    print("| Request | Count | p50 (ms) | p95 (ms) | Max (ms) | Status codes |")
    print("|---|---:|---:|---:|---:|---|")
    for name in plan:
        values = sorted(stats.latencies.get(name, []))
        if not values:
            continue
        p95 = values[math.ceil(0.95 * len(values)) - 1]  # nearest-rank percentile
        codes = ", ".join(f"{c}: {n}" for c, n in sorted(stats.statuses[name].items()))
        print(
            f"| {name} | {len(values)} | {statistics.median(values) * 1000:.0f} | "
            f"{p95 * 1000:.0f} | {values[-1] * 1000:.0f} | {codes} |"
        )
    print("\n| Container | Peak memory (MiB) | Limit (MiB) | Share |")
    print("|---|---:|---:|---:|")
    over = False
    for name, (used, limit) in sorted(peaks.items()):
        share = used / limit
        over |= share > MEMORY_SHARE_LIMIT
        print(f"| {name} | {used:.0f} | {limit:.0f} | {share:.0%} |")
    busy_share = stats.busy / stats.total if stats.total else 0.0
    print(
        f"\nBusy responses (503, load shedding): {stats.busy} of {stats.total} ({busy_share:.1%})"
    )
    if stats.failures:
        print(f"{len(stats.failures)} failed requests, first: {stats.failures[:5]}")
    if not peaks:
        print("no memory samples: is docker available?")
    return 1 if stats.failures or over or not peaks or busy_share > BUSY_SHARE_LIMIT else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="http://127.0.0.1:8461")
    parser.add_argument("--project", default="grid-load-forecast-check")
    parser.add_argument("--clients", type=int, default=10)
    parser.add_argument("--duration", type=int, default=60)
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
