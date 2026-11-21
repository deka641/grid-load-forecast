"""Abusive clients for the prod-check stack: connection floods and greedy requests.

Standard library only, so it runs in the Python image on the check stack's
network, where every container counts as a client address of its own::

    docker run --rm --network grid-load-forecast-check_default --read-only \\
        --cap-drop ALL --ulimit nofile=65536:65536 -v "$PWD/scripts:/t:ro" \\
        --entrypoint python grid-load-forecast-python:check -I /t/loadtest_abuse.py MODE ...

Modes (each prints one JSON line with the results):

``halfopen COUNT HOLD``
    Open COUNT connections to ``web:8080`` that send an unfinished request
    header, keep them for HOLD seconds and add a header line every 5 seconds.
``flood DURATION CONNECTIONS URL [URL ...]``
    CONNECTIONS keep-alive connections request the URLs in turn for DURATION
    seconds. In a URL, ``{n}`` is replaced by a counter, and ``{start}`` and
    ``{end}`` by a window of ``--span`` days that starts ``n`` days after
    ``--first``, so that every request asks for different data.
``probe DURATION INTERVAL_MS URL [URL ...]``
    One request per URL every INTERVAL_MS milliseconds, each on a new
    connection: what an ordinary visitor sees during a flood.
"""

import argparse
import http.client
import itertools
import json
import socket
import threading
import time
import urllib.parse
from collections.abc import Callable
from datetime import date, timedelta

HOST = "web"
PORT = 8080
DRIP_SECONDS = 5.0


class Results:
    """Status codes and latencies per request name, shared by all threads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._codes: dict[str, dict[str, int]] = {}
        self._latencies: dict[str, list[float]] = {}

    def add(self, name: str, code: str, ms: float | None) -> None:
        with self._lock:
            codes = self._codes.setdefault(name, {})
            codes[code] = codes.get(code, 0) + 1
            if ms is not None:
                self._latencies.setdefault(name, []).append(ms)

    def summary(self) -> dict[str, object]:
        out: dict[str, object] = {}
        for name, codes in self._codes.items():
            lat = sorted(self._latencies.get(name, []))

            def pct(q: float, lat: list[float] = lat) -> int | None:
                return round(lat[min(len(lat) - 1, int(q * len(lat)))]) if lat else None

            out[name] = {"codes": codes, "p50": pct(0.5), "p95": pct(0.95), "max": pct(1.0)}
        return out


class Urls:
    """Fill the placeholders of the request URLs."""

    def __init__(self, first: date, span: int) -> None:
        self._counter = itertools.count()
        self._lock = threading.Lock()
        self._first = first
        self._span = span

    def fill(self, template: str) -> str:
        with self._lock:
            n = next(self._counter)
        start = self._first + timedelta(days=n % 365)
        end = start + timedelta(days=self._span - 1)
        return template.format(n=n, start=start.isoformat(), end=end.isoformat())


def name_of(url: str) -> str:
    path, _, query = url.partition("?")
    name = path.rsplit("/", 1)[-1]
    return f"{name}[{query[:40]}]" if query and "{" not in query else name


def target(url: str) -> tuple[str, int, str]:
    parts = urllib.parse.urlsplit(url)
    path = parts.path + ("?" + parts.query if parts.query else "")
    return parts.hostname or HOST, parts.port or 80, path


def fetch(conn: http.client.HTTPConnection, path: str) -> tuple[int, float, bool]:
    """Status, milliseconds and whether the server asked to close the connection."""
    started = time.monotonic()
    conn.request("GET", path, headers={"Accept-Encoding": "gzip"})
    response = conn.getresponse()
    response.read()
    closing = response.getheader("connection", "").lower() == "close"
    return response.status, (time.monotonic() - started) * 1000, closing


def flood(args: argparse.Namespace, results: Results) -> None:
    urls = Urls(args.first, args.span)
    stop_at = time.monotonic() + args.duration

    def worker(offset: int) -> None:
        host, port, _ = target(args.urls[0])
        conn = http.client.HTTPConnection(host, port, timeout=30)
        for k in itertools.count(offset):
            if time.monotonic() >= stop_at:
                break
            template = args.urls[k % len(args.urls)]
            try:
                status, ms, closing = fetch(conn, target(urls.fill(template))[2])
                results.add(name_of(template), str(status), ms)
                if not closing:
                    continue
            except (OSError, http.client.HTTPException) as exc:
                results.add(name_of(template), type(exc).__name__, None)
            conn.close()
            conn = http.client.HTTPConnection(host, port, timeout=30)
        conn.close()

    run_threads(worker, args.connections)


def probe(args: argparse.Namespace, results: Results) -> None:
    stop_at = time.monotonic() + args.duration
    while time.monotonic() < stop_at:
        for url in args.urls:
            host, port, path = target(url)
            conn = http.client.HTTPConnection(host, port, timeout=30)
            try:
                status, ms, _ = fetch(conn, path)
                results.add(name_of(url), str(status), ms)
            except (OSError, http.client.HTTPException) as exc:
                results.add(name_of(url), type(exc).__name__, None)
            finally:
                conn.close()
        time.sleep(args.interval / 1000)


def halfopen(args: argparse.Namespace) -> dict[str, object]:
    sockets: list[socket.socket] = []
    failed: dict[str, int] = {}
    started = time.monotonic()
    for _ in range(args.count):
        try:
            sock = socket.create_connection((args.host, args.port), timeout=5)
            sock.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n")
            sockets.append(sock)
        except OSError as exc:
            failed[type(exc).__name__] = failed.get(type(exc).__name__, 0) + 1
    opened_s = round(time.monotonic() - started, 1)
    alive_per_drip: list[int] = []
    stop_at = time.monotonic() + args.hold
    while time.monotonic() < stop_at:
        time.sleep(min(DRIP_SECONDS, max(stop_at - time.monotonic(), 0)))
        alive = 0
        for sock in sockets:
            try:
                sock.sendall(b"X-a: b\r\n")
                alive += 1
            except OSError:
                pass
        alive_per_drip.append(alive)
    for sock in sockets:
        sock.close()
    return {
        "opened": len(sockets),
        "failed": failed,
        "open_s": opened_s,
        "alive_per_drip": alive_per_drip,
    }


def run_threads(worker: Callable[[int], None], count: int) -> None:
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--first", type=date.fromisoformat, default=date(2025, 1, 1))
    parser.add_argument("--span", type=int, default=138)
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    modes = parser.add_subparsers(dest="mode", required=True)
    p = modes.add_parser("halfopen")
    p.add_argument("count", type=int)
    p.add_argument("hold", type=float)
    p = modes.add_parser("flood")
    p.add_argument("duration", type=float)
    p.add_argument("connections", type=int)
    p.add_argument("urls", nargs="+")
    p = modes.add_parser("probe")
    p.add_argument("duration", type=float)
    p.add_argument("interval", type=float)
    p.add_argument("urls", nargs="+")
    args = parser.parse_args()
    results = Results()
    if args.mode == "halfopen":
        print(json.dumps({"mode": "halfopen", **halfopen(args)}))
        return
    if args.mode == "flood":
        flood(args, results)
    else:
        probe(args, results)
    print(json.dumps({"mode": args.mode, "results": results.summary()}))


if __name__ == "__main__":
    main()
