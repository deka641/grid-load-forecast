"""Cross-check the project's MAE and MAPE against scikit-learn.

Runs in a separate environment so scikit-learn is not a project dependency:

    uv run --isolated --no-project --with scikit-learn \
        python scripts/crosscheck_metrics.py data/backtest_rows.csv reports/backtest.json
"""

import csv
import json
import math
import sys

from sklearn.metrics import mean_absolute_error, mean_absolute_percentage_error

MODELS = ("lightgbm", "baseline_week", "tso")


def main(rows_path: str, report_path: str) -> int:
    with open(rows_path, encoding="utf-8") as handle:
        rows = [
            r
            for r in csv.DictReader(handle)
            if all(not math.isnan(float(r[k])) for k in ("actual", *MODELS))
        ]
    with open(report_path, encoding="utf-8") as handle:
        reported = {m["model"]: m for m in json.load(handle)["metrics"] if m["scope"] == "overall"}
    actual = [float(r["actual"]) for r in rows]
    failed = False
    for model in MODELS:
        predicted = [float(r[model]) for r in rows]
        mae = mean_absolute_error(actual, predicted)
        mape = 100 * mean_absolute_percentage_error(actual, predicted)
        ok = (
            abs(mae - reported[model]["mae_mwh"]) <= 0.05
            and abs(mape - reported[model]["mape_percent"]) <= 0.0005
            and len(rows) == reported[model]["n"]
        )
        failed |= not ok
        print(
            f"{model:14} sklearn MAE {mae:10.2f} MAPE {mape:7.4f} % n {len(rows)}"
            f"  report MAE {reported[model]['mae_mwh']:10.1f} MAPE "
            f"{reported[model]['mape_percent']:7.3f} %  {'ok' if ok else 'MISMATCH'}"
        )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
