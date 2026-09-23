"""RSI kill-switch / incident freeze switchboard (plan WP-A4, feature #20).

``rsi_frozen()`` combines three operator freeze sources — the fleet kill switch
(``alpha.bots.kill_switch``), the global emergency stop (``alpha.runtime.estop``)
and a local ``runtime_home()/rsi/STOP`` sentinel (spec §52) — into one verdict
consulted at every RSI cycle start.

Binding guardrails:
* §5.6 — a probe that errors *engages* the freeze (fail-closed): the cycle is
  refused with the real error text as the reason, never a fabricated one.
* §5.9 — switchboard failures degrade to *refusing RSI cycles* only and can
  never break ordinary Alpha startup: all operator-module imports are lazy and
  exception-wrapped, and ``rsi_frozen()`` itself never raises.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from alpha.config.runtime_paths import runtime_home

logger = logging.getLogger(__name__)

STOP_FILE = Path("rsi") / "STOP"


def stop_file_path() -> Path:
    """Operator-created local freeze sentinel (existence is the whole check)."""
    return runtime_home() / STOP_FILE


def _kill_switch_state() -> tuple[bool, str]:
    """Fleet kill-switch probe (module seam; tests may monkeypatch this)."""
    from alpha.bots.kill_switch import is_kill_switch_active  # lazy import: startup stays import-light (§5.9)

    return is_kill_switch_active()


def _estop_manager():
    """Global ESTOP manager accessor (module seam; tests may monkeypatch this)."""
    from alpha.runtime.estop import get_estop_manager  # lazy import: startup stays import-light (§5.9)

    return get_estop_manager()


def _estop_state() -> tuple[bool, str]:
    """Emergency-stop probe reading the real sentinel via its manager."""
    manager = _estop_manager()
    if not manager.is_engaged():
        return False, ""
    try:
        detail = manager.get_status().get("reason")
    except Exception:
        detail = None
    if isinstance(detail, str) and detail and detail != "None":
        return True, f"emergency stop engaged: {detail}"
    return True, "emergency stop engaged"


def _stop_file_state() -> tuple[bool, str]:
    """Local ``runtime_home()/rsi/STOP`` probe (existence only, spec §52)."""
    path = stop_file_path()
    try:
        exists = path.exists()
    except OSError as exc:
        return True, f"rsi switchboard failure (stop_file): {exc}"
    if exists:
        return True, f"operator RSI freeze: STOP file present at {path}"
    return False, ""


def _run_probe(label: str, probe: Callable[[], tuple[bool, str]]) -> tuple[bool, str]:
    """Run one probe; a raising probe engages the freeze with the real error (§5.6)."""
    try:
        engaged, reason = probe()
    except Exception as exc:
        logger.error("RSI switchboard %s probe failed; refusing RSI cycles: %s", label, exc)
        return True, f"rsi switchboard failure ({label}): {exc}"
    if engaged:
        return True, reason or f"{label} engaged"
    return False, ""


def rsi_frozen() -> tuple[bool, str]:
    """Return ``(frozen, real_reason)`` — never raises (§5.9). First engaged source wins."""
    # Built from module globals on every call so tests can monkeypatch seams.
    probes: tuple[tuple[str, Callable[[], tuple[bool, str]]], ...] = (
        ("kill_switch", _kill_switch_state),
        ("estop", _estop_state),
        ("stop_file", _stop_file_state),
    )
    for label, probe in probes:
        frozen, reason = _run_probe(label, probe)
        if frozen:
            return True, reason
    return False, ""


def guard_cycle_start() -> None:
    """Raise ``RuntimeError(<real reason>)`` when RSI is frozen; no-op otherwise."""
    frozen, reason = rsi_frozen()
    if frozen:
        raise RuntimeError(reason)
