"""Built-in self_heal_diagnose tool inspired by hermes-agi-asi-harness."""

from __future__ import annotations

import json

from langchain.tools import tool

from alpha.runtime.selfheal.watchdog import SelfHealingWatchdog


@tool("self_heal_diagnose", parse_docstring=True)
def self_heal_diagnose(
    auto_remediate: bool = True,
    workspace_dir: str | None = None,
) -> str:
    """Diagnose runtime health anomalies, stale lock files, or hanging operations and execute self-healing actions.

    Scans workspace for locks (such as .git/index.lock or .pytest_cache/.lock) older than threshold,
    identifies faults, and applies safe remediation to allow autonomous task progression without crashing.

    Args:
        auto_remediate: Whether to automatically clear stale locks and recover healthy state (default True).
        workspace_dir: Workspace directory path to scan (optional).
    """
    watchdog = SelfHealingWatchdog()
    report = watchdog.scan_and_heal(workspace_dir=workspace_dir, auto_remediate=auto_remediate)

    return json.dumps(report.to_dict(), indent=2)
