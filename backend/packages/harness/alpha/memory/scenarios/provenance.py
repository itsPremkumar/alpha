"""Append-only provenance for scenario routing decisions."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alpha.agents.memory.l1.paths import atomic_write_text, safe_segment

from .config import scenario_root
from .models import RoutingPlan

logger = logging.getLogger(__name__)
_APPEND_LOCK = threading.Lock()


def _day_stamp(now: float | None = None) -> str:
    timestamp = time.time() if now is None else float(now)
    return datetime.fromtimestamp(timestamp, tz=UTC).strftime("%Y-%m-%d")


def _path(root: Path, user_id: str | None, day: str) -> Path:
    return root / "users" / safe_segment(user_id or "default") / "scenarios" / "provenance" / f"{day}.jsonl"


def provenance_path(
    user_id_or_root: str | Path | None,
    day_or_user: str,
    day: str | None = None,
    *,
    storage_path: str | Path | None = None,
    root: str | Path | None = None,
) -> Path:
    """Return a per-user/day JSONL path.

    The three-positional-argument form ``provenance_path(root, user, day)`` is
    accepted for parity with the L1 path helpers; the clearer keyword form is
    ``provenance_path(user, day, storage_path=...)``.
    """

    if day is not None:
        base = Path(user_id_or_root)
        user = day_or_user
        return _path(base, user, day)
    base = scenario_root(storage_path if root is None else root)
    return _path(base, user_id_or_root, day_or_user)


def _route_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {
            "name": str(value.get("name") or ""),
            "weight": float(value.get("weight", value.get("base_weight", 0.0))),
            "budget_units": int(value.get("budget_units", value.get("cost_units", 1))),
        }
    if isinstance(value, (tuple, list)) and len(value) == 3:
        return {"name": str(value[0]), "weight": float(value[1]), "budget_units": int(value[2])}
    return {
        "name": str(getattr(value, "name", "")),
        "weight": float(getattr(value, "weight", 0.0)),
        "budget_units": int(getattr(value, "budget_units", 1)),
    }


def _plan_entry(
    plan: RoutingPlan | Mapping[str, Any] | str,
    *,
    thread_id: str | None = None,
    reason: str | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    if isinstance(plan, RoutingPlan):
        payload = plan.to_dict()
    elif isinstance(plan, Mapping):
        payload = dict(plan)
    else:
        payload = {"scenario": str(plan)}
    payload["surfaces"] = [_route_mapping(item) for item in (payload.get("surfaces") or [])]
    payload["chosen_surfaces"] = payload["surfaces"]
    payload.setdefault("scenario", "general")
    payload.setdefault("confidence", 0.0)
    payload.setdefault("reason", reason or "no deciding reason recorded")
    if reason:
        payload["reason"] = reason
    if thread_id is not None:
        payload["thread_id"] = thread_id
    timestamp = time.time() if now is None else float(now)
    payload.setdefault("ts", datetime.fromtimestamp(timestamp, tz=UTC).isoformat())
    payload.setdefault("timestamp", timestamp)
    return payload


def append_routing_decision(
    plan: RoutingPlan | Mapping[str, Any] | str | None = None,
    *,
    scenario: Any | None = None,
    confidence: float = 0.0,
    surfaces: list[Any] | None = None,
    user_id: str | None = None,
    storage_path: str | Path | None = None,
    root: str | Path | None = None,
    now: float | None = None,
    thread_id: str | None = None,
    reason: str | None = None,
) -> Path:
    """Append one decision and return its path; audit failures never raise."""

    if plan is None:
        plan = {
            "scenario": scenario or "general",
            "confidence": confidence,
            "surfaces": surfaces or [],
        }
    elif scenario is not None or confidence or surfaces is not None:
        plan = {
            "scenario": scenario or (plan.scenario if isinstance(plan, RoutingPlan) else "general"),
            "confidence": confidence,
            "surfaces": surfaces or [],
        }
    path = provenance_path(
        user_id,
        _day_stamp(now),
        storage_path=storage_path,
        root=root,
    )
    try:
        payload = _plan_entry(plan, thread_id=thread_id, reason=reason, now=now)
        line = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with _APPEND_LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
    except Exception as exc:  # noqa: BLE001 - provenance is best effort
        logger.warning("Scenario routing provenance append failed for %s: %s", path, exc)
    return path


def append_decision(
    plan: RoutingPlan | Mapping[str, Any] | str | None = None,
    **kwargs: Any,
) -> Path:
    return append_routing_decision(plan, **kwargs)


append_routing_entry = append_routing_decision
append_entry = append_routing_decision


class RoutingProvenance:
    """Small injectable append-only writer used by :class:`ScenarioRouter`."""

    def __init__(
        self,
        storage_path: str | Path | None = None,
        *,
        user_id: str | None = None,
        root: str | Path | None = None,
    ) -> None:
        self.storage_path = storage_path if root is None else root
        self.user_id = user_id
        self._lock = threading.Lock()

    def path(self, *, now: float | None = None) -> Path:
        return provenance_path(self.user_id, _day_stamp(now), storage_path=self.storage_path)

    def append(
        self,
        plan: RoutingPlan | Mapping[str, Any],
        *,
        user_id: str | None = None,
        thread_id: str | None = None,
        now: float | None = None,
        reason: str | None = None,
    ) -> Path:
        effective_user = user_id if user_id is not None else self.user_id
        path = provenance_path(effective_user, _day_stamp(now), storage_path=self.storage_path)
        try:
            payload = _plan_entry(plan, thread_id=thread_id, reason=reason, now=now)
            line = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            with self._lock, _APPEND_LOCK:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
                    handle.flush()
        except Exception as exc:  # noqa: BLE001 - audit logging is non-fatal
            logger.warning("Scenario routing provenance append failed for %s: %s", path, exc)
        return path


def read_entries(
    day: str,
    *,
    user_id: str | None = None,
    storage_path: str | Path | None = None,
    root: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Read valid JSON objects from one day, skipping malformed lines."""

    path = provenance_path(user_id, day, storage_path=storage_path, root=root)
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        logger.warning("Scenario routing provenance read failed for %s: %s", path, exc)
        return []
    entries: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, Mapping):
            entries.append(dict(value))
    return entries


def rewrite_day(
    day: str,
    entries: list[Mapping[str, Any]],
    *,
    user_id: str | None = None,
    storage_path: str | Path | None = None,
    root: str | Path | None = None,
) -> Path:
    """Atomically replace one day for an explicit repair/test workflow."""

    path = provenance_path(user_id, day, storage_path=storage_path, root=root)
    body = "\n".join(json.dumps(dict(entry), ensure_ascii=False, sort_keys=True) for entry in entries)
    atomic_write_text(path, body + ("\n" if body else ""))
    return path


__all__ = [
    "RoutingProvenance",
    "append_decision",
    "append_entry",
    "append_routing_decision",
    "append_routing_entry",
    "provenance_path",
    "read_entries",
    "rewrite_day",
]
