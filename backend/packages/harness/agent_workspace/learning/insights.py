"""Session insights: usage aggregates rendered as a readable digest.

Rolls run records (tokens, cost, tools, skills, models, status) into totals,
per-model tables, daily activity bars, and top tools/skills. The record
source is injected, so this works over the console tables, a JSON export,
or synthetic fixtures — and stays fully offline.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any


def _day(ts: float | None) -> str:
    if not ts:
        return "unknown"
    try:
        return datetime.fromtimestamp(float(ts), UTC).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError, OverflowError):
        return "unknown"


def summarize(records: list[dict[str, Any]], *, days: int = 30) -> dict[str, Any]:
    """Aggregate raw run records into an insights report (pure function)."""
    total_in = total_out = 0
    total_cost = 0.0
    included = 0
    per_model: dict[str, dict[str, Any]] = {}
    per_day: Counter[str] = Counter()
    tools: Counter[str] = Counter()
    skills: Counter[str] = Counter()
    statuses: Counter[str] = Counter()

    for r in records or []:
        days_ago = r.get("days_ago")
        if isinstance(days_ago, (int, float)) and days_ago > days:
            continue
        included += 1
        model = str(r.get("model") or "unknown").split("/")[-1]
        in_tok = int(r.get("input_tokens") or 0)
        out_tok = int(r.get("output_tokens") or 0)
        cost = float(r.get("cost_usd") or 0.0)
        total_in += in_tok
        total_out += out_tok
        total_cost += cost
        bucket = per_model.setdefault(model, {"runs": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
        bucket["runs"] += 1
        bucket["input_tokens"] += in_tok
        bucket["output_tokens"] += out_tok
        bucket["cost_usd"] = round(bucket["cost_usd"] + cost, 6)
        per_day[_day(r.get("timestamp"))] += 1
        for t in r.get("tools") or []:
            tools[str(t)] += 1
        for s in r.get("skills") or []:
            skills[str(s)] += 1
        statuses[str(r.get("status") or "unknown")] += 1

    return {
        "runs": included,
        "input_tokens": total_in,
        "output_tokens": total_out,
        "cost_usd": round(total_cost, 6),
        "per_model": per_model,
        "per_day": dict(sorted(per_day.items())),
        "top_tools": tools.most_common(10),
        "top_skills": skills.most_common(10),
        "statuses": dict(statuses),
    }


def format_text(report: dict[str, Any], *, days: int = 30, width: int = 20) -> str:
    """Render a terminal-friendly digest with daily activity bars."""
    lines = [f"Insights (last {days}d): {report['runs']} runs, {report['input_tokens'] + report['output_tokens']} tokens, ${report['cost_usd']:.4f}"]
    per_day = report.get("per_day") or {}
    if per_day:
        peak = max(per_day.values()) or 1
        lines.append("Activity:")
        for day, count in per_day.items():
            bar = "#" * max(1, int(count / peak * width)) if count else ""
            lines.append(f"  {day} {bar} {count}")
    per_model = report.get("per_model") or {}
    if per_model:
        lines.append("Models:")
        for model, stats in sorted(per_model.items(), key=lambda kv: -kv[1]["runs"]):
            lines.append(f"  {model}: {stats['runs']} runs, ${stats['cost_usd']:.4f}")
    for label, key in (("Top tools", "top_tools"), ("Top skills", "top_skills")):
        top = report.get(key) or []
        if top:
            lines.append(f"{label}: " + ", ".join(f"{name}×{count}" for name, count in top[:5]))
    return "\n".join(lines)
