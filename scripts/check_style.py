"""Fail if a versioned text file contains emojis or em/en dashes."""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DASHES = chr(0x2013) + chr(0x2014)
EMOJI = f"{chr(0x1F000)}-{chr(0x1FAFF)}{chr(0x2600)}-{chr(0x27BF)}{chr(0xFE0F)}"
FORBIDDEN = re.compile(f"[{DASHES}]|[{EMOJI}]")
SKIP_SUFFIXES = {".png", ".jpg", ".ico", ".woff", ".woff2", ".lock"}
SKIP_FILES = {"web/package-lock.json"}


def main() -> int:
    files = subprocess.run(
        ["git", "ls-files"],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    problems = []
    for name in files:
        if name in SKIP_FILES or Path(name).suffix in SKIP_SUFFIXES:
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
