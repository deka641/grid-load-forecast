"""Write the OpenAPI document of the API as deterministic JSON.

Usage: ``python scripts/export_openapi.py [OUTPUT]`` (default: ``openapi.json``
in the repository root). Keys are sorted so the file only changes when the API
changes.
"""

import json
import sys
from pathlib import Path

from gridload.api.app import create_app
from gridload.config import Settings

ROOT = Path(__file__).resolve().parent.parent


def render() -> str:
    """Return the OpenAPI document as sorted, indented JSON with a final newline."""
    # The document does not depend on data, so any data directory will do.
    app = create_app(Settings(data_dir=ROOT / "data"))
    return json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    target = Path(argv[1]) if len(argv) > 1 else ROOT / "openapi.json"
    target.write_text(render(), encoding="utf-8")
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
