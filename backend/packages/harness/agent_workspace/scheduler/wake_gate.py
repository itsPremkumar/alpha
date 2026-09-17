"""Cron wake gate: cheap preflight decides whether an occurrence wakes the agent.

A gate check runs first; only its last non-empty stdout line matters. JSON
``{"wakeAgent": false}`` skips the LLM run entirely — no model call, no
delivery (the ledger records ``skipped``). Anything else wakes normally.
Fail-open by design: unparseable output never blocks scheduled work.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any


def should_wake(check_output: str | None) -> bool:
    """Parse a gate-check output. False only on explicit opt-out."""
    lines = [line for line in (check_output or "").splitlines() if line.strip()]
    if not lines:
        return True
    try:
        gate = json.loads(lines[-1].strip())
    except (json.JSONDecodeError, ValueError):
        return True
    return not isinstance(gate, dict) or gate.get("wakeAgent", True) is not False


def wrap_executor_with_gate(
    executor_fn: Callable[[Any], str],
    gate_fn: Callable[[Any], str] | None,
) -> Callable[[Any], tuple[str, bool]]:
    """Wrap an occurrence executor with a wake gate.

    Returns (output, woke): ``woke=False`` means the run was skipped before
    any expensive work. A gate exception fails open to waking.
    """

    def _run(job: Any) -> tuple[str, bool]:
        if gate_fn is None:
            return executor_fn(job), True
        try:
            if not should_wake(gate_fn(job)):
                name = getattr(job, "name", "?")
                return f"Skipped scheduled job '{name}' (wake gate declined).", False
        except Exception:
            pass
        return executor_fn(job), True

    return _run
