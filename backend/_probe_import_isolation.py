"""Prove the cold-start probe targets do not import the new behaviour-trace modules.

This is the "must still exit 0 with its budgets UNCHANGED" argument, made
mechanical rather than asserted: if none of the six targets
``scripts/cold_start_probe.py`` measures pulls in
``alpha.observability.{contract,taxonomy,behaviour_config,writer,ambient}`, then
adding those files cannot have changed a single import-graph measurement the gate
makes.

Run from ``backend/``::

    python _probe_import_isolation.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

NEW_MODULES = (
    "alpha.observability.contract",
    "alpha.observability.taxonomy",
    "alpha.observability.behaviour_config",
    "alpha.observability.writer",
    "alpha.observability.ambient",
)

#: The same six targets ``scripts/cold_start_probe.py`` measures.
PROBE_TARGETS = (
    "alpha",
    "alpha.config.memory_config",
    "alpha.memory",
    "alpha.agents.lead_agent.prompt",
    "alpha.tools.builtins",
    "app.gateway.app",
)

#: Inherit the real environment and only override PYTHONPATH. A stripped env makes
#: ``Path.expanduser()`` raise on Windows ("Could not determine home directory"),
#: which is a probe bug that looks exactly like an import failure.
ENV = {**os.environ, "PYTHONPATH": "packages/harness;."}

SCRIPT = (
    "import sys\n"
    "import {target}\n"
    "leaked = [m for m in {new!r} if m in sys.modules]\n"
    "print(','.join(leaked) or 'NONE')\n"
)


def main() -> int:
    failures = 0
    for target in PROBE_TARGETS:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [sys.executable, "-c", SCRIPT.format(target=target, new=list(NEW_MODULES))],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(Path.cwd()),
            env=ENV,
        )
        if completed.returncode != 0:
            failures += 1
            print(f"{target:38} IMPORT FAILED rc={completed.returncode} {completed.stderr[-200:]}")
            continue
        leaked = completed.stdout.strip()
        status = "ok" if leaked == "NONE" else f"LEAKS {leaked}"
        print(f"{target:38} {status}")
    print()
    print("verdict:", "PASS - no probe target imports a behaviour-trace module" if not failures else f"FAIL - {failures} target(s) failed to import")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
