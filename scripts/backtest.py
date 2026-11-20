"""Run the rolling-origin backtest on the local warehouse and write the reports.

Usage: ``uv run python scripts/backtest.py [--days 365] [--warehouse data/warehouse.duckdb]``

Writes ``reports/backtest.json`` and ``reports/backtest.md`` (committed, the only
source for error values quoted in the documentation) and the scored rows to
``data/backtest_rows.csv`` for the independent metric cross-check in
``scripts/crosscheck_metrics.py``. The warehouse is opened read-only.
"""

import argparse
import sys
from pathlib import Path

import duckdb

from gridload.forecast import report, service
from gridload.forecast.backtest import run_backtest

ROOT = Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--warehouse", type=Path, default=ROOT / "data" / "warehouse.duckdb")
    parser.add_argument("--out", type=Path, default=ROOT / "reports")
    parser.add_argument("--rows", type=Path, default=ROOT / "data" / "backtest_rows.csv")
    args = parser.parse_args(argv)

    con = duckdb.connect(str(args.warehouse), read_only=True)
    try:
        con.execute("SET TimeZone = 'UTC'")
        load = service.load_history(con, "load")
        tso = service.load_history(con, "load_forecast_tso")
    finally:
        con.close()
    last_day = service.last_complete_day(load)
    if last_day is None:
        print("no complete day of load data", file=sys.stderr)
        return 1
    result = run_backtest(
        load, tso, last_day, days=args.days, training_start=service.training_start(load)
    )
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "backtest.json").write_text(report.to_json(result), encoding="utf-8")
    (args.out / "backtest.md").write_text(report.to_markdown(result), encoding="utf-8")
    report.write_rows(result, args.rows)
    print(report.to_markdown(result).split("## By day type")[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
