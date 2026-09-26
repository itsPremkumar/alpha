#!/usr/bin/env python3
"""Portable launcher for Alpha's guarded source update engine.

This file is intentionally stdlib-only at the edge: it adds the monorepo
source paths and delegates to ``alpha.evolution.update_cli``.  It can be
invoked by a Windows Task Scheduler, a POSIX cron job, or a detached Gateway
helper without depending on a shell.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("AGENT_WORKSPACE_PROJECT_ROOT", str(ROOT))
for source_path in (ROOT / "backend", ROOT / "backend" / "packages" / "harness"):
    text = str(source_path)
    if text not in sys.path:
        sys.path.insert(0, text)

from alpha.evolution.update_cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
