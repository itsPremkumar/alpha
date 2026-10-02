"""Importing the config-driven registries must not require ``config.yaml``.

CI runs the whole backend suite from a fresh clone with **no** ``config.yaml``
(it is gitignored), and four test modules import
``alpha.models.free_router.providers`` / ``alpha.models.provider_manager`` at
module scope. Both bind their registry at import time by calling
``get_app_config()``, which raises ``FileNotFoundError`` when no config file
exists anywhere — so every one of the four CI shards died at *collection* and
the 27k-test suite never ran.

The contract those modules publish is "with nothing configured the registry is
**empty**, which is honest" (``models/AGENTS.md``; the ``_providers_from_config``
/ ``_provider_specs_from_catalog`` docstrings both promise it). A missing file
is exactly "nothing configured", so the module-scope binding must degrade to
the empty registry. A config file that *exists* but is invalid must still fail
closed — that path is untouched here.

The check runs in a fresh subprocess with ``ALPHA_CONFIG_PATH`` pointed at a
nonexistent path: that reproduces CI's configless state exactly (resolution
raises the same ``FileNotFoundError`` regardless of which search step missed)
and exercises the real module-scope import path instead of whatever a previous
test in this process cached. Both registries are probed in that one interpreter
and reported per-name, so a failure in the first cannot mask the second.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent

#: name -> import statement + assertion, executed independently inside the
#: subprocess so each reports its own outcome.
PROBES = {
    "free_router_providers": ("import alpha.models.free_router.providers as m; assert m.PROVIDERS == {}, m.PROVIDERS; assert m.PROVIDER_ORDER == (), m.PROVIDER_ORDER"),
    "provider_manager_specs": ("import alpha.models.provider_manager as m; assert m.PROVIDER_SPECS == [], m.PROVIDER_SPECS"),
}


def test_registry_imports_degrade_to_empty_without_config_file() -> None:
    driver = (
        "import json\n"
        "results = {}\n"
        f"probes = {PROBES!r}\n"
        "for name, code in probes.items():\n"
        "    try:\n"
        "        exec(code, {'__name__': '__main__'})\n"
        "        results[name] = None\n"
        "    except BaseException as exc:  # noqa: BLE001 - report, don't swallow\n"
        "        results[name] = f'{type(exc).__name__}: {exc}'\n"
        "print(json.dumps(results))\n"
    )
    env = dict(os.environ)
    # Nonexistent on purpose: resolution must fail closed into "unconfigured"
    # exactly as a fresh clone (no config.yaml at all) does.
    env["ALPHA_CONFIG_PATH"] = str(Path(__file__).parent / "_no_such_config.yaml")
    env.setdefault("PYTHONPATH", str(BACKEND_ROOT))
    proc = subprocess.run(
        [sys.executable, "-c", driver],
        env=env,
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"probe driver crashed:\n{proc.stderr or proc.stdout}"
    results: dict[str, str | None] = json.loads(proc.stdout.strip().splitlines()[-1])
    failed = {name: error for name, error in results.items() if error}
    assert not failed, f"Importing a config-driven registry without any config file must bind the empty registry (nothing configured is empty, per models/AGENTS.md), not raise; failures: {json.dumps(failed, indent=2)}"
