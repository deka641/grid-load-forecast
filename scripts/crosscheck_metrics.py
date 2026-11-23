"""Cross-check the project's MAE and MAPE against scikit-learn, and its error distribution.

MAE and MAPE are recomputed with scikit-learn; bias, the 95th percentile and the
largest absolute error, and the share of hours above 5 % with the standard library.

Runs in a separate environment so scikit-learn is not a project dependency:

    uv run --isolated --no-project --with scikit-learn \
        python scripts/crosscheck_metrics.py data/backtest_rows.csv reports/backtest.json
"""

import csv
import json
import math
import statistics
import sys

from sklearn.metrics import mean_absolute_error, mean_absolute_percentage_error

MODELS = ("lightgbm", "baseline_week", "tso")
DAY_TYPES = ("workday", "saturday", "sunday_holiday")


def rows_for(rows: list[dict[str, str]], models: tuple[str, ...]) -> list[dict[str, str]]:
    keep = [r for r in rows if all(not math.isnan(float(r[k])) for k in ("actual", *models))]
    return [r for r in keep if float(r["actual"]) != 0]


def check(label: str, rows: list[dict[str, str]], model: str, reported: dict[str, float]) -> bool:
    actual = [float(r["actual"]) for r in rows]
    predicted = [float(r[model]) for r in rows]
    mae = mean_absolute_error(actual, predicted)
    mape = 100 * mean_absolute_percentage_error(actual, predicted)
    ok = (
        abs(mae - reported["mae_mwh"]) <= 0.05
        and abs(mape - reported["mape_percent"]) <= 0.0005
        and len(rows) == reported["n"]
    )
    if not ok or label.endswith("/all"):
        print(
            f"{model:14} {label:22} sklearn MAE {mae:10.2f} MAPE {mape:7.4f} % n {len(rows):5}"
            f"  report MAE {reported['mae_mwh']:10.1f} MAPE {reported['mape_percent']:7.3f} %"
            f"  {'ok' if ok else 'MISMATCH'}"
        )
    return ok


def check_distribution(rows: list[dict[str, str]], model: str, reported: dict[str, float]) -> bool:
    actual = [float(r["actual"]) for r in rows]
    errors = [float(r[model]) - a for r, a in zip(rows, actual, strict=True)]
    absolute = [abs(e) for e in errors]
    bias = statistics.fmean(errors)
    p95 = statistics.quantiles(absolute, n=100, method="inclusive")[94]
    largest = max(absolute)
    above = 100 * sum(e / abs(a) > 0.05 for e, a in zip(absolute, actual, strict=True)) / len(rows)
    ok = (
        abs(bias - reported["bias_mwh"]) <= 0.05
        and abs(p95 - reported["p95_abs_error_mwh"]) <= 0.05
        and abs(largest - reported["max_abs_error_mwh"]) <= 0.05
        and abs(above - reported["hours_above_5_percent"]) <= 0.005
        and len(rows) == reported["n"]
    )
    print(
        f"{model:14} distribution          bias {bias:+9.2f} p95 {p95:9.2f} max {largest:9.2f}"
        f" above 5 % {above:6.2f} % n {len(rows):5}  {'ok' if ok else 'MISMATCH'}"
    )
    return ok


def main(rows_path: str, report_path: str) -> int:
    with open(rows_path, encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    with open(report_path, encoding="utf-8") as handle:
        report = json.load(handle)
    reported = {(m["model"], m["scope"], m["scope_value"]): m for m in report["metrics"]}
    distribution = report.get("error_distribution", {})
    primary = rows_for(rows, ("lightgbm", "baseline_week"))
    with_tso = rows_for(primary, ("tso",))
    checks = 0
    distributions = 0
    failed = False
    for model in MODELS:
        base = with_tso if model == "tso" else primary
        scopes = [("overall", "all", base)]
        scopes += [
            ("hour", str(h), [r for r in base if r["hour_local"] == str(h)]) for h in range(24)
        ]
        scopes += [("day_type", d, [r for r in base if r["day_type"] == d]) for d in DAY_TYPES]
        if model != "tso":
            scopes.append(("overall_tso_hours", "all", with_tso))
        for scope, value, subset in scopes:
            key = (model, scope, value)
            if not subset and key not in reported:
                continue
            checks += 1
            if key not in reported:
                print(f"{model:14} {scope}/{value}: {len(subset)} rows, metric missing  MISMATCH")
                failed = True
                continue
            failed |= not check(f"{scope}/{value}", subset, model, reported[key])
        if base:
            distributions += 1
            if model not in distribution:
                print(f"{model:14} distribution: {len(base)} rows, figures missing  MISMATCH")
                failed = True
            else:
                failed |= not check_distribution(base, model, distribution[model])
    print(
        f"{checks} metrics and {distributions} error distributions compared: "
        f"{'all match' if not failed else 'MISMATCHES found'}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
