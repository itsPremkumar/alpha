"""Built-in Dreaming Memory Consolidation tool inspired by OpenClaw."""

from __future__ import annotations

import json

from langchain.tools import tool

from agent_workspace.memory.dreaming.phases import MemorySignal, run_dream_cycle
from agent_workspace.memory.dreaming.store import get_dream_store


@tool("consolidate_memory_dream", parse_docstring=True)
def consolidate_memory_dream(
    signals_json: str,
    threshold: float = 0.70,
) -> str:
    """Run a 3-Phase Dreaming Memory Consolidation cycle (Light, REM, Deep Sleep).

    Synthesizes short-term execution notes and failure logs, extracts durable insights,
    appends dream records to DREAMS.md, and promotes high-scoring principles (score >= threshold)
    directly into MEMORY.md.

    Args:
        signals_json: JSON list of signal objects, e.g. [{"content": "...", "category": "...", "source": "..."}].
        threshold: Score threshold (0.0 - 1.0) required to promote insights into MEMORY.md. Default: 0.70.
    """
    try:
        raw_items = json.loads(signals_json)
        if not isinstance(raw_items, list):
            return "Error: signals_json must be a JSON array of signal objects."
    except Exception as e:
        return f"Error parsing signals_json: {e}"

    signals = [
        MemorySignal(
            content=item.get("content", ""),
            source=item.get("source", "user"),
            category=item.get("category", "general"),
        )
        for item in raw_items
        if isinstance(item, dict) and item.get("content")
    ]

    if not signals:
        return "No valid memory signals to consolidate."

    store = get_dream_store()
    report = run_dream_cycle(signals, store=store, threshold=threshold)

    return (
        f"Consolidation Dream Cycle Completed [{report.cycle_id}]:\n"
        f"- Processed {report.signals_processed} deduplicated signals\n"
        f"- Generated {len(report.insights_generated)} insights\n"
        f"- Promoted {report.promoted_count} high-value rules to MEMORY.md\n"
        f"- Sleep journal recorded in DREAMS.md\n\n"
        f"Updates:\n" + "\n".join(f"+ {u}" for u in report.memory_updates)
    )
