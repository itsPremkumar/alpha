"""Command-line code critic.

Usage:  python critique_scan.py <path> [<path> ...]
Exit code: 1 if any HIGH finding is present, else 0.
"""

from __future__ import annotations

import sys
from pathlib import Path

HARNESS = Path(__file__).resolve().parent / "backend" / "packages" / "harness"
sys.path.insert(0, str(HARNESS))

from alpha.critique import critique_path  # noqa: E402


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: python critique_scan.py <path> [<path> ...]")
        return 2
    high = 0
    for path in argv:
        report = critique_path(path)
        print(report.render_markdown())
        print()
        high += report.high_count
    return 1 if high else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
