"""10. Cross-thread recall + Dreaming trigger.

Wraps memory.dreaming (light/REM/deep sleep phases) + active_memory with
an explicit opt-in recall config. No change to default per-user isolation:
recall across threads is off unless CrossThreadRecallConfig.enabled is
True for that agent/user. The trigger helper lazily imports dreaming so
this module stays import-light for Gateway workers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class CrossThreadRecallConfig:
    enabled: bool = False
    max_threads: int = 5
    max_facts: int = 20
    min_confidence: float = 0.7


def build_recall_query(user_message: str, thread_id: str = "") -> dict[str, Any]:
    return {"query": user_message.strip()[:2000], "exclude_thread_id": thread_id}


def trigger_dream_cycle(signals: list[Any] | None = None) -> dict[str, Any]:
    """Run one Dreaming consolidation cycle if the dreaming package exists."""
    try:
        from alpha.memory.dreaming import run_dream_cycle
    except Exception as exc:
        return {"ok": False, "reason": f"dreaming unavailable: {exc.__class__.__name__}"}
    try:
        report = run_dream_cycle(signals or [])
        insights = getattr(report, "insights", []) or []
        return {"ok": True, "insights": len(insights)}
    except TypeError:
        # Older signature without args — try no-arg call.
        try:
            from alpha.memory.dreaming import run_dream_cycle as run_cycle_noarg

            report = run_cycle_noarg()
            insights = getattr(report, "insights", []) or []
            return {"ok": True, "insights": len(insights)}
        except Exception as exc:
            return {"ok": False, "reason": f"{exc.__class__.__name__}"}
    except Exception as exc:
        return {"ok": False, "reason": f"{exc.__class__.__name__}"}
