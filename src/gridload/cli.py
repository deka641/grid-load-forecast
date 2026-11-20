"""Command line entry point: ``gridload backfill|ingest|run|status|healthcheck``."""

import argparse
import sys
from collections.abc import Sequence
from datetime import UTC, datetime

from gridload import __version__
from gridload.config import Settings
from gridload.pipeline import jobs


def _date(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gridload", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    backfill = sub.add_parser("backfill", help="load the full history (or from --since)")
    backfill.add_argument("--since", type=_date, help="first day to load, YYYY-MM-DD (UTC)")
    backfill.add_argument("--series", nargs="+", metavar="KEY", help="limit to these series")

    ingest = sub.add_parser("ingest", help="load missing and recent chunks")
    ingest.add_argument("--series", nargs="+", metavar="KEY", help="limit to these series")

    sub.add_parser("run", help="scheduled job: ingest, forecast, publish snapshot")
    sub.add_parser("status", help="print warehouse status as JSON")
    sub.add_parser("healthcheck", help="exit 0 if the pipeline is healthy")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = Settings.from_env()
    if args.command == "status":
        print(jobs.dumps(jobs.status(settings)))
        return 0
    if args.command == "healthcheck":
        return 0 if jobs.healthcheck(settings) else 1
    if args.command == "backfill":
        result = jobs.load(settings, mode="backfill", keys=args.series, since=args.since)
    elif args.command == "ingest":
        result = jobs.load(settings, mode="incremental", keys=args.series)
    else:
        result = jobs.load(settings, mode="incremental", post_ingest=jobs.post_ingest_steps)
    print(
        f"run {result.run_id} {result.status}: {result.loaded} chunks loaded, "
        f"{result.unchanged} unchanged, {result.rows_written} values written, "
        f"{len(result.errors)} errors"
    )
    for error in result.errors:
        print(f"  error: {error}", file=sys.stderr)
    return 0 if result.status == "ok" else 1
