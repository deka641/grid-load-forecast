"""Enforce per-directory line and branch coverage from ``coverage.json``.

The core packages (data source, pipeline, forecast, API) must be fully covered;
the overall threshold is configured in ``pyproject.toml``.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIRED = {
    "src/gridload/sources/": 100.0,
    "src/gridload/pipeline/": 100.0,
    "src/gridload/forecast/": 100.0,
    "src/gridload/api/": 100.0,
}


def main(report: Path = ROOT / "coverage.json") -> int:
    files = json.loads(report.read_text(encoding="utf-8"))["files"]
    failed = False
    for prefix, threshold in REQUIRED.items():
        covered = total = 0
        for name, data in files.items():
            if name.startswith(prefix):
                summary = data["summary"]
                covered += summary["covered_lines"] + summary.get("covered_branches", 0)
                total += summary["num_statements"] + summary.get("num_branches", 0)
        percent = 100.0 if total == 0 else 100.0 * covered / total
        ok = percent >= threshold
        failed |= not ok
        print(f"{prefix:28} {percent:6.2f} % (required {threshold:.0f} %){'' if ok else '  FAIL'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
