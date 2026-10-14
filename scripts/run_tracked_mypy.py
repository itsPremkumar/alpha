"""Run mypy against tracked backend Python sources, excluding local scratch."""

from __future__ import annotations

import os
import sys
from pathlib import Path, PurePosixPath


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from scripts.check_python_health import _python_files

    tracked, _ = _python_files()
    backend_files = [PurePosixPath(path).relative_to("backend").as_posix() for path in tracked if path.startswith("backend/")]
    if not backend_files:
        print("No tracked backend Python files were found.", file=sys.stderr)
        return 2

    try:
        from mypy.api import run
    except ImportError as exc:
        print(f"mypy is not installed in this Python environment: {exc}", file=sys.stderr)
        return 127

    previous_cwd = Path.cwd()
    try:
        os.chdir(root / "backend")
        stdout, stderr, exit_code = run(["--ignore-missing-imports", "--explicit-package-bases", *backend_files])
    finally:
        os.chdir(previous_cwd)
    sys.stdout.write(stdout)
    sys.stderr.write(stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
