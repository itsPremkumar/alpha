"""§42 promotion cooldown: the stabilization window after a real promotion.

Architecture spec §42 (``references/RSI_AGENT_ARCHITECTURE.md``)::

    Store: source_change_id, cycle_id, promotion_timestamp, cooldown_until
    After a promotion, use a stabilization window before starting another
    high-risk mutation of the same subsystem.

This module owns that window end to end:

* :func:`record_promotion` writes the §42 store — EXACTLY the four spec
  fields, nothing else — to ``runtime_home()/rsi/promotion_cooldown.json``
  via ``alpha.evolution.identity.atomic_write_json`` (unique tmp file +
  ``os.replace``: readers see the old record or the new one, never a torn
  write). The caller (``alpha.rsi.promotion.decide``) writes it ONLY when a
  decision actually promotes; a failed decision never starts a cooldown.
* :func:`cooldown_state` computes the REAL state from the REAL record and an
  injected clock — ``never_promoted`` (no record), ``cooling_down``
  (``cooldown_until`` strictly in the future, remaining seconds > 0), or
  ``ready`` (window elapsed). Arithmetic is plain epoch-seconds ``float``
  against :data:`RSI_COOLDOWN_CLOCK` (the module-level §5 clock seam,
  ``time.time`` by default): nothing is hardcoded, nothing is rounded into
  existence, and a test drives the whole lifecycle by patching that one seam.
* :func:`evaluate_cooldown` is the gate view: ``(ok, reason)`` with the real
  record values in the text — timestamps and remaining seconds are displayed
  as INTEGERS (remaining rounded UP via ``ceil`` so the window is never
  understated), so a decision reason can never pick up a stray fractional
  decimal. An unreadable or corrupt record fails CLOSED with the real error
  text: never repaired, never re-created, never ignored.

Window length: :data:`DEFAULT_COOLDOWN_SECONDS` (3600), overridable per call
via ``cooldown_seconds`` or per environment via ``RSI_COOLDOWN_SECONDS`` —
operator-visible configuration resolved at call time and validated as a
finite non-negative number; an invalid value raises ``ValueError`` carrying
the real text and NEVER silently falls back to the default.

Honesty contract (plan §5): every stored second comes from the injected
clock (no fabricated timestamps); failure handling is fail-closed or the
explicit ``never_promoted`` state (FileNotFound only — every other read
error propagates as the real error); no bare ``except`` anywhere; no
confidence/score/rating field exists in the record.
"""

from __future__ import annotations

import json
import math
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from alpha.config.runtime_paths import runtime_home
from alpha.evolution.identity import atomic_write_json

__all__ = [
    "COOLDOWN_FILE_NAME",
    "DEFAULT_COOLDOWN_SECONDS",
    "RSI_COOLDOWN_CLOCK",
    "cooldown_state",
    "evaluate_cooldown",
    "record_promotion",
]

#: Default stabilization window (seconds) after a promotion — spec §42's
#: mandated window made explicit, operator-visible via ``RSI_COOLDOWN_SECONDS``.
DEFAULT_COOLDOWN_SECONDS = 3600.0

#: Store file: ``runtime_home()/rsi/promotion_cooldown.json``.
COOLDOWN_FILE_NAME = "promotion_cooldown.json"

#: §5 injectable clock seam: the ONLY time source this module reads.
#: ``time.time`` by default; tests monkeypatch it to drive the window.
RSI_COOLDOWN_CLOCK: Callable[[], float] = time.time


def _record_path() -> Path:
    """``runtime_home()/rsi/promotion_cooldown.json`` (env resolved at call time)."""
    return runtime_home() / "rsi" / COOLDOWN_FILE_NAME


def _read_record() -> dict[str, Any]:
    """The persisted §42 record.

    ``FileNotFoundError`` means the store was never written (the honest
    ``never_promoted`` case — callers handle it explicitly). Every other
    problem raises the REAL error: ``OSError`` for an unreadable store and
    ``ValueError`` for corrupt content or a field outside the spec's shape.
    Nothing here is ever repaired or re-created.
    """
    path = _record_path()
    raw = path.read_text(encoding="utf-8")
    try:
        record = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"corrupt cooldown record at {path}: {exc}") from exc
    if not isinstance(record, dict):
        raise ValueError(f"corrupt cooldown record at {path}: expected a JSON object, got {type(record).__name__}")
    for field in ("source_change_id", "cycle_id"):
        value = record.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError(f"corrupt cooldown record at {path}: §42 field {field!r} must be a non-empty string, got {value!r}")
    for field in ("promotion_timestamp", "cooldown_until"):
        value = record.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"corrupt cooldown record at {path}: §42 field {field!r} must be an epoch-seconds number, got {value!r}")
    return record


def _now(now_fn: Callable[[], float] | None = None) -> float:
    """Current epoch seconds from ``now_fn`` or the injected module clock (finite, else ``ValueError``)."""
    clock = RSI_COOLDOWN_CLOCK if now_fn is None else now_fn
    current = float(clock())
    if not math.isfinite(current):
        raise ValueError(f"injected cooldown clock returned a non-finite time: {current!r}")
    return current


def cooldown_state(*, now_fn: Callable[[], float] | None = None) -> tuple[str, float]:
    """Real ``(state, remaining_seconds)`` for the persisted window.

    ``never_promoted`` and ``ready`` both report ``0.0`` (no window is
    running); ``cooling_down`` reports the REAL positive seconds left,
    computed as ``cooldown_until - now`` on the injected clock.
    ``OSError``/``ValueError`` from a corrupt store propagate — the caller
    (the promotion gate) fails closed on them with the real text.
    """
    try:
        record = _read_record()
    except FileNotFoundError:
        return "never_promoted", 0.0
    remaining = float(record["cooldown_until"]) - _now(now_fn)
    if remaining > 0:
        return "cooling_down", remaining
    return "ready", 0.0


def evaluate_cooldown(*, now_fn: Callable[[], float] | None = None) -> tuple[bool, str]:
    """§42 gate view: ``ok`` only when no stabilization window is active.

    The reason carries the REAL record values (integer-formatted: ``ceil``ed
    remaining seconds, timestamps as whole epoch seconds); a corrupt or
    unreadable record returns ``(False, "cooldown unavailable: <real text>")``
    — fail-closed, never repaired, never re-created.
    """
    current = _now(now_fn)
    try:
        record = _read_record()
    except FileNotFoundError:
        return True, f"cooldown ok: never promoted (no cooldown record at {_record_path()}) — no stabilization window applies"
    except ValueError as exc:
        return False, f"cooldown unavailable: {exc}"
    except OSError as exc:
        return False, f"cooldown unavailable: cannot read cooldown record at {_record_path()}: {type(exc).__name__}: {exc}"
    promoted_at = float(record["promotion_timestamp"])
    until = float(record["cooldown_until"])
    remaining = until - current
    context = f"source_change_id={record['source_change_id']!r}, cycle_id={record['cycle_id']!r}"
    if remaining > 0:
        return False, (
            f"cooldown active: {context} promoted at {promoted_at:.0f}, window ends {until:.0f} ({math.ceil(remaining)}s remaining — "
            "spec §42 stabilization window: wait it out before the next high-risk mutation)"
        )
    return True, f"cooldown ok: {context} stabilization window elapsed (promoted at {promoted_at:.0f}, window ended {until:.0f}, checked at {current:.0f})"


def _resolve_window_seconds(cooldown_seconds: float | None) -> float:
    """Explicit arg -> ``RSI_COOLDOWN_SECONDS`` env -> default; validated, never silently defaulted."""
    if cooldown_seconds is None:
        raw = os.environ.get("RSI_COOLDOWN_SECONDS")
        if raw is None or not raw.strip():
            return DEFAULT_COOLDOWN_SECONDS
        try:
            cooldown_seconds = float(raw)
        except ValueError as exc:
            raise ValueError(f"RSI_COOLDOWN_SECONDS is not a number: {raw!r} (unset it to use the default {DEFAULT_COOLDOWN_SECONDS:.0f}s window)") from exc
    value = float(cooldown_seconds)
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"cooldown window must be a finite, non-negative number of seconds, got {cooldown_seconds!r}")
    return value


def record_promotion(*, source_change_id: str, cycle_id: str, cooldown_seconds: float | None = None, now_fn: Callable[[], float] | None = None) -> dict[str, Any]:
    """Persist the §42 store for a REAL promotion; return the written record.

    The record carries EXACTLY ``source_change_id``, ``cycle_id``,
    ``promotion_timestamp``, ``cooldown_until`` — the spec's four fields, no
    more. ``cycle_id`` must be the caller's real cycle identity (the lineage
    store's ``"unknown"`` precedent when no cycle concept applies), never
    blank; both arguments and the window are validated with honest
    ``ValueError`` text. Write errors propagate (real ``OSError``/``TypeError``)
    so the promotion path discloses them fail-closed.
    """
    if not isinstance(source_change_id, str) or not source_change_id.strip():
        raise ValueError(f"source_change_id must be a non-empty string, got {source_change_id!r} (the promoted change's identity — never blank)")
    if not isinstance(cycle_id, str) or not cycle_id.strip():
        raise ValueError(f"cycle_id must be a non-empty string, got {cycle_id!r} (use 'unknown' when no cycle applies — lineage precedent, never blank)")
    window = _resolve_window_seconds(cooldown_seconds)
    current = _now(now_fn)
    record: dict[str, Any] = {
        "source_change_id": source_change_id,
        "cycle_id": cycle_id,
        "promotion_timestamp": current,
        "cooldown_until": current + window,
    }
    atomic_write_json(_record_path(), record)
    return record
