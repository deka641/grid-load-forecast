"""Fail if the gzip size of the built frontend exceeds the budget."""

import gzip
import sys
from pathlib import Path

DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
BUDGET_KB = 400


def gzip_size(path: Path) -> int:
    """Return the gzip-compressed size of a file in bytes."""
    return len(gzip.compress(path.read_bytes(), compresslevel=9))


def main() -> int:
    """Sum gzip sizes of JS and CSS assets and compare with the budget."""
    assets = sorted(p for p in DIST.glob("assets/*") if p.suffix in {".js", ".css"})
    if not assets:
        print(f"no assets found in {DIST}; run the frontend build first", file=sys.stderr)
        return 1
    total_kb = sum(gzip_size(p) for p in assets) / 1024
    print(f"frontend assets: {total_kb:.1f} kB gzip (budget {BUDGET_KB} kB)")
    return 0 if total_kb <= BUDGET_KB else 1


if __name__ == "__main__":
    sys.exit(main())
