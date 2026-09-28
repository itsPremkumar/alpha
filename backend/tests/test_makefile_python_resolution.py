"""`make` must not require a system-wide Python on Windows.

Windows has no `python` on PATH by default, and this checkout's own launcher
scripts already account for that: `scripts/serve.sh::_pick_python` tries
`backend/.venv` before falling back. The Makefile did not, so after `install.bat`
had successfully created the virtualenv, the very first recipe of every target
still failed:

    $ make dev-daemon
    make : process_begin: CreateProcess(NULL, python ./scripts/check.py, ...) failed.
    make (e=2): The system cannot find the file specified.

That is the worst possible place for it: the one-click install succeeds, tells
the user they are set up, and the documented next command dies immediately for a
reason that has nothing to do with what they did.

So the Windows branch resolves `PYTHON` to the project interpreter when the
virtualenv exists, while staying overridable (`?=`) and still falling back to the
PATH lookup for a checkout that has not been installed yet.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = REPO_ROOT / "Makefile"

pytestmark = pytest.mark.skipif(os.name != "nt", reason="the Windows branch of the Makefile is the subject")


def _text() -> str:
    return MAKEFILE.read_text(encoding="utf-8")


def _windows_branch() -> str:
    """The body of the `ifeq ($(OS),Windows_NT)` branch.

    Cut on a *line* `else`, not the first occurrence of the substring: the
    Windows branch now contains its own `else` (the no-venv fallback), and
    slicing at the first "else" truncated the branch in the middle.
    """
    text = _text()
    start = text.index("ifeq ($(OS),Windows_NT)")
    end = text.index("\nelse\n", start)
    return text[start:end]


def test_the_windows_branch_prefers_the_project_virtualenv() -> None:
    branch = _windows_branch()
    # wildcard so the assignment is conditional on the file actually existing.
    assert re.search(r"ALPHA_VENV_PYTHON\s*:=\s*\$\(wildcard\s+backend\\?\.venv", branch), branch
    assert re.search(r"ifneq\s*\(\$\(ALPHA_VENV_PYTHON\),\)", branch), branch


def test_python_stays_overridable() -> None:
    """`?=` throughout: `make PYTHON=... dev` must still work.

    `:=` would be a silent behaviour change for anyone already overriding it. The
    assertion is on the *operator*, not the value - the value is the venv path by
    design, so requiring the value to start with "?" would be nonsense.
    """
    branch = _windows_branch()
    assignments = re.findall(r"^\s*PYTHON\s*([:?]?=)\s*(.*)$", branch, re.MULTILINE)
    assert assignments, f"no PYTHON assignment found in the Windows branch:\n{branch}"
    for operator, _value in assignments:
        assert operator == "?=", f"PYTHON uses {operator!r} instead of '?=', so it stops being overridable"


def test_a_missing_virtualenv_still_falls_back_to_the_path() -> None:
    branch = _windows_branch()
    # A checkout that has not been installed yet must still find a PATH python.
    assert re.search(r"else\s*\n\s*PYTHON\s*\?=\s*python\b", branch), branch


@pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")
def test_make_actually_resolves_the_virtualenv_interpreter(tmp_path: Path) -> None:
    """The source pin above is only worth something if make agrees with it.

    Run in the real checkout (not a copy) because the resolution depends on
    `backend/.venv/Scripts/python.exe` existing relative to the Makefile.
    """
    if not (REPO_ROOT / "backend" / ".venv" / "Scripts" / "python.exe").exists():
        pytest.skip("no project virtualenv in this checkout")

    dry_run = subprocess.run(
        ["make", "-n", "--no-print-directory", "dev-daemon"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    assert dry_run.returncode == 0, dry_run.stderr

    recipes = [line.strip() for line in dry_run.stdout.splitlines() if line.strip()]
    python_recipes = [line for line in recipes if "scripts/check.py" in line or "scripts/rotate_logs.py" in line]
    assert python_recipes, f"expected the dev-daemon recipes to be visible in the dry run:\n{dry_run.stdout}"
    for recipe in python_recipes:
        assert recipe.startswith("backend\\.venv\\Scripts\\python.exe") or recipe.startswith("backend/.venv/Scripts/python.exe"), f"recipe does not use the project interpreter: {recipe}"
        assert not recipe.startswith("python "), f"recipe still calls a bare `python`: {recipe}"
