"""Working-tree vs imported-code drift probe for ``orchestrator/replay.py``.

Reports three facts about ``alpha.orchestrator.replay`` in the live process:

1. which file the interpreter actually imported (``replay_mod.__file__``),
2. whether that imported source carries the ``external_wait_parked`` fold,
3. whether the ``replay.py`` sitting beside this probe — the checkout the
   operator believes is running — carries it too.

(2) and (3) disagreeing means a stale install or a shadowing path on
``sys.path`` is answering instead of this checkout; ``same_file`` false
means the import did not even resolve to the sibling. Originally written
with a hard-coded ``C:/...`` path to another machine's checkout, which made
the answer depend on where the file happened to live — the path now derives
from ``__file__``, so it is true for every checkout.

Runnable standalone::

    python -m alpha.orchestrator._oracle_probe    # from backend/, PYTHONPATH=.
    python _oracle_probe.py                       # from this directory

Nothing executes at import time; the probe logic lives in ``probe()`` and
``main()``.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import alpha.orchestrator.replay as replay_mod

FIX_MARKER = "external_wait_parked"


def probe() -> dict[str, Any]:
    """Collect the three readings plus an overall consistency verdict."""
    imported_path = Path(replay_mod.__file__ or "")
    working_tree_path = Path(__file__).resolve().with_name("replay.py")

    try:
        same_file = bool(imported_path) and imported_path.resolve() == working_tree_path
    except OSError:
        same_file = False

    imported_source = inspect.getsource(replay_mod)
    working_tree_source = working_tree_path.read_text(encoding="utf-8") if working_tree_path.is_file() else ""
    run_source = inspect.getsource(replay_mod.replay_run)

    imported_has_fix = FIX_MARKER in imported_source
    working_tree_has_fix = FIX_MARKER in working_tree_source
    replay_run_has_fix = FIX_MARKER in run_source

    return {
        "imported_file": str(imported_path),
        "working_tree_file": str(working_tree_path),
        "same_file": same_file,
        "imported_has_fix": imported_has_fix,
        "working_tree_has_fix": working_tree_has_fix,
        "replay_run_has_fix": replay_run_has_fix,
        "consistent": same_file and imported_has_fix and working_tree_has_fix and replay_run_has_fix,
    }


def main() -> int:
    findings = probe()
    print("PROBE = alpha.orchestrator.replay imported-vs-working-tree drift")
    for key in ("imported_file", "working_tree_file", "same_file", "imported_has_fix", "working_tree_has_fix", "replay_run_has_fix"):
        print(f"{key.upper()} =", findings[key])
    print("RESULT =", "CONSISTENT" if findings["consistent"] else "DRIFT")
    return 0 if findings["consistent"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
