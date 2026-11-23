"""Run the rolling-origin backtest on the local warehouse and write the reports.

Usage: ``uv run python scripts/backtest.py [--days 365] [--last-day YYYY-MM-DD]
[--warehouse data/warehouse.duckdb]``

Writes ``reports/backtest.json`` and ``reports/backtest.md`` (committed, the only
source for error values quoted in the documentation) and the scored rows to
``data/backtest_rows.csv`` for the independent metric cross-check in
``scripts/crosscheck_metrics.py``. The warehouse is opened read-only. Without
``--last-day`` the window ends at the last complete day of load data. The reports
record a fingerprint of the input data, so two runs show whether their data differ.
"""

import argparse
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb

from gridload import timezones as tz
from gridload.forecast import report, service
from gridload.forecast.backtest import run_backtest

ROOT = Path(__file__).resolve().parent.parent


def _day(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC).date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}") from exc


def main(argv: list[str] | None = None, *, today: date | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument(
        "--last-day", type=_day, help="last delivery day, YYYY-MM-DD (default: last complete day)"
    )
    parser.add_argument("--warehouse", type=Path, default=ROOT / "data" / "warehouse.duckdb")
    parser.add_argument("--out", type=Path, default=ROOT / "reports")
    parser.add_argument("--rows", type=Path, default=ROOT / "data" / "backtest_rows.csv")
    args = parser.parse_args(argv)
    today = today or datetime.now(UTC).astimezone(tz.BERLIN).date()

    con = duckdb.connect(str(args.warehouse), read_only=True)
    try:
        con.execute("SET TimeZone = 'UTC'")
        load = service.load_history(con, "load")
        tso = service.load_history(con, "load_forecast_tso")
        try:
            last_day = service.resolve_last_day(load, args.last_day, today)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        revised = service.revised_hours(con, last_day)
    finally:
        con.close()
    result = run_backtest(
        load, tso, last_day, days=args.days, training_start=service.training_start(load)
    )
    digest, hours = service.input_fingerprint({"load": load, "load_forecast_tso": tso}, last_day)
    result.inputs = {"sha256": digest, "hours": hours, "revised_hours": revised}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "backtest.json").write_text(report.to_json(result), encoding="utf-8")
    (args.out / "backtest.md").write_text(report.to_markdown(result), encoding="utf-8")
    report.write_rows(result, args.rows)
    print(report.to_markdown(result).split("## By day type")[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
