"""Fail if a tracked or new text file contains emojis or em/en dashes."""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DASHES = "".join(chr(c) for c in (0x2012, 0x2013, 0x2014, 0x2015))
EMOJI = "".join(
    (
        f"{chr(0x1F000)}-{chr(0x1FAFF)}",
        f"{chr(0x2600)}-{chr(0x27BF)}",
        f"{chr(0x2300)}-{chr(0x23FF)}",
        f"{chr(0x2B00)}-{chr(0x2BFF)}",
        "".join(chr(c) for c in (0xFE0F, 0x200D, 0x3030, 0x303D, 0x3297, 0x3299)),
    )
)
FORBIDDEN = re.compile(f"[{DASHES}]|[{EMOJI}]")
SKIP_SUFFIXES = {".png", ".jpg", ".ico", ".woff", ".woff2", ".lock"}
SKIP_FILES = {"web/package-lock.json"}


def main() -> int:
    files = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    problems = []
    for name in files:
        if name in SKIP_FILES or Path(name).suffix in SKIP_SUFFIXES:
            continue
        if not (ROOT / name).is_file():  # deleted but not yet committed
            continue
        try:
            text = (ROOT / name).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if FORBIDDEN.search(line):
                problems.append(f"{name}:{number}: {line.strip()[:80]}")
    for problem in problems:
        print(problem)
    print(f"style: {len(files)} files checked, {len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
