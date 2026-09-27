"""Pin this worktree's harness package ahead of the editable install.

The shared repo's virtualenv carries a ``.pth`` entry pointing at the MAIN
checkout's ``packages/harness``.  A git worktree has its own copy of the source,
so without this the suite would silently import the main tree and test code that
is not the code under test.  Inserting the worktree path at ``sys.path[0]``
before pytest collects makes the import resolve here.

Run as::

    python _dynwf_pytest.py [pytest args...]
"""

from __future__ import annotations

import sys
from pathlib import Path

_HARNESS = Path(__file__).resolve().parent / "packages" / "harness"
if str(_HARNESS) not in sys.path:
    sys.path.insert(0, str(_HARNESS))

import pytest  # noqa: E402  (import must follow the path pin)

if __name__ == "__main__":
    raise SystemExit(pytest.main(sys.argv[1:]))
