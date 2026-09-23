"""Unified global execution mode: one authoritative plan-vs-code switch.

WorkSwarm gap 7 collapses the four previously uncoordinated mode mechanisms
(``plan_mode``/``is_plan_mode`` flags, the ``/api/plan-mode`` router,
``code_mode``, policy side-effect verdicts) around one persisted state with
four modes:

- ``work.normal`` (DEFAULT) — normal work execution; policy decides as before.
- ``work.plan``   — plan mode for the work track: side effects are gated.
- ``code.normal`` — normal code execution; policy decides as before.
- ``code.plan``   — plan mode for the code track: side effects are gated.

Honesty invariants (non-negotiable, pinned by ``backend/tests/test_execution_mode.py``):

- Missing **or corrupt** persisted mode file falls back to ``DEFAULT_MODE``
  with the disclosed note ``"default: no persisted mode found"`` — ``load_mode``
  never raises, never invents a previous mode, timestamp, or actor.
- ``set_mode`` reports persistence honestly: ``persisted=False`` carries the
  real write error instead of swallowing it, and a success is only reported
  after the written file has been re-read and proven to contain the mode.
- In ``*.plan`` modes, side-effecting actions get an explicit decision
  ``("approval" | "deny", "plan-mode:<mode>: side effects require exiting plan
  mode")``. Read-only prefixes (``read:``/``search:``/``test:``/``lint:``) keep
  the underlying policy verdict. The mode only ever *constrains*: it never
  grants a permission the policy engine would not grant (a policy ``deny`` on a
  read-only action stays ``deny`` in plan mode), and in normal modes the policy
  verdict passes through untouched.

The module persists runtime state only (``runtime_home()/execution_mode.json``)
— no YAML config schema change, therefore no ``config_version`` bump.

Policy seam: ``plan_side_effect_decision`` reaches the policy engine through the
module-level indirection ``_evaluate`` so tests can stub
``alpha.runtime.execution_mode._evaluate``.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from alpha.config.runtime_paths import runtime_home
from alpha.policy.engine import get_policy_engine

logger = logging.getLogger(__name__)

Verdict = Literal["allow", "deny", "approval"]


class ExecutionMode(StrEnum):
    """The four unified execution modes (WorkSwarm gap 7).

    ``StrEnum`` keeps values usable as plain strings (the plan's
    ``(str, Enum)`` sketch) while satisfying ruff UP042; members always
    serialize via ``.value``.
    """

    WORK_NORMAL = "work.normal"
    WORK_PLAN = "work.plan"
    CODE_NORMAL = "code.normal"
    CODE_PLAN = "code.plan"


DEFAULT_MODE: ExecutionMode = ExecutionMode.WORK_NORMAL
#: Honest note returned whenever no valid persisted mode record could be read.
DEFAULT_NOTE = "default: no persisted mode found"
#: Read-only action prefixes that stay allowed in ``*.plan`` modes (policy still decides).
READ_ONLY_PREFIXES: tuple[str, ...] = ("read:", "search:", "test:", "lint:")
#: The exact plan-mode side-effect reason string mandated by the plan.
PLAN_SIDE_EFFECT_REASON_TMPL = "plan-mode:{mode}: side effects require exiting plan mode"
#: Note written into the file by :func:`save_mode`.
SAVE_NOTE = "set via set_mode"

# Request/run-scoped override; ``None`` means "no override, read persisted state".
_mode_ctx: ContextVar[ExecutionMode | None] = ContextVar("alpha_execution_mode", default=None)


def mode_path() -> Path:
    """Absolute path of the persisted mode file (``runtime_home()/execution_mode.json``)."""
    return runtime_home() / "execution_mode.json"


def _coerce_mode(value: ExecutionMode | str) -> ExecutionMode:
    """Coerce *value* to :class:`ExecutionMode`; raise ``ValueError`` with the real reason."""
    if isinstance(value, ExecutionMode):
        return value
    try:
        return ExecutionMode(str(value))
    except ValueError:
        valid = ", ".join(m.value for m in ExecutionMode)
        raise ValueError(f"unknown execution mode {value!r}; expected one of: {valid}") from None


def _evaluate(action: str):
    """Module-level indirection to the policy engine (tests stub this seam)."""
    return get_policy_engine().evaluate(action)


def _default_record(note: str = DEFAULT_NOTE) -> dict:
    """Honest default record: empty timestamp/actor — nothing is fabricated."""
    return {
        "mode": DEFAULT_MODE.value,
        "updated_at": "",
        "actor": "",
        "note": note,
        "persisted": False,
    }


def load_mode_record() -> dict:
    """Read the persisted mode file as an honest record.

    Returns ``{mode, updated_at, actor, note, persisted}``. Missing or corrupt
    files fall back to :data:`DEFAULT_MODE` with the disclosed note — never
    raises, never invents a last-mode, timestamp, or actor. ``persisted`` is
    ``True`` only when a valid record was actually read from disk.
    """
    path = mode_path()
    try:
        raw_text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _default_record()
    except OSError as exc:
        return _default_record(f"{DEFAULT_NOTE}; persisted file could not be read: {exc}")
    try:
        data = json.loads(raw_text)
        mode = _coerce_mode(data["mode"])
        updated_at = str(data.get("updated_at", ""))
        actor = str(data.get("actor", ""))
        note = str(data.get("note", ""))
    except Exception as exc:  # corrupt JSON, non-dict payload, or unknown mode value
        return _default_record(f"{DEFAULT_NOTE}; persisted file is corrupt: {exc}")
    return {
        "mode": mode.value,
        "updated_at": updated_at,
        "actor": actor,
        "note": note,
        "persisted": True,
    }


def load_mode() -> ExecutionMode:
    """Return the persisted mode; missing/corrupt file -> :data:`DEFAULT_MODE` (never raises)."""
    return ExecutionMode(load_mode_record()["mode"])


def save_mode(mode: ExecutionMode | str, *, actor: str = "") -> Path:
    """Atomically persist *mode* (tmp file + ``os.replace``) and return the path written.

    The on-disk shape is ``{"mode", "updated_at", "actor", "note"}``. Any write
    error propagates to the caller (never swallowed) and the tmp file is cleaned
    up so no ``.tmp`` residue remains.
    """
    active = _coerce_mode(mode)
    path = mode_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {
            "mode": active.value,
            "updated_at": datetime.now(UTC).isoformat(),
            "actor": str(actor),
            "note": SAVE_NOTE,
        },
        indent=2,
    )
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                logger.warning("Could not remove temp file %s after save_mode", tmp, exc_info=True)
    return path


def get_mode() -> ExecutionMode:
    """Active mode: context override -> persisted file -> :data:`DEFAULT_MODE`."""
    ctx = _mode_ctx.get()
    if ctx is not None:
        return ctx
    return load_mode()


def set_mode(mode: ExecutionMode | str, *, actor: str = "") -> dict:
    """Persist *mode* and report the outcome honestly.

    Returns ``{mode, previous, persisted, note}``. ``persisted=True`` is only
    reported after the file has been re-read and proven to contain the mode;
    on any write failure ``persisted`` is ``False`` and ``note`` carries the
    real error. Raises ``ValueError`` for an unknown mode (invalid input, no
    write attempted).
    """
    new = _coerce_mode(mode)
    previous = get_mode()
    try:
        save_mode(new, actor=actor)
    except Exception as exc:
        logger.warning("execution mode write failed for %s: %s", new.value, exc, exc_info=True)
        return {
            "mode": new.value,
            "previous": previous.value,
            "persisted": False,
            "note": f"persist failed: {type(exc).__name__}: {exc}",
        }
    record = load_mode_record()
    if not record["persisted"] or record["mode"] != new.value:
        return {
            "mode": new.value,
            "previous": previous.value,
            "persisted": False,
            "note": f"persist failed: {mode_path()} does not contain {new.value!r} after write",
        }
    return {
        "mode": new.value,
        "previous": previous.value,
        "persisted": True,
        "note": f"persisted to {mode_path()}",
    }


@contextmanager
def mode_context(mode: ExecutionMode | str) -> Iterator[ExecutionMode]:
    """Scope *mode* to the current context; restores the prior value even on exception.

    This is a non-persisting override — leaving the context restores the
    previous (or persisted) mode and the on-disk file is untouched.
    """
    active = _coerce_mode(mode)
    token = _mode_ctx.set(active)
    try:
        yield active
    finally:
        _mode_ctx.reset(token)


def is_plan_mode(mode: ExecutionMode | str | None = None) -> bool:
    """Whether *mode* (default: the active mode) is one of the ``*.plan`` modes."""
    active = get_mode() if mode is None else _coerce_mode(mode)
    return active in (ExecutionMode.WORK_PLAN, ExecutionMode.CODE_PLAN)


def side_effects_allowed(mode: ExecutionMode | str | None = None) -> bool:
    """Whether the mode itself imposes no side-effect gate (normal modes).

    This is a mode-level statement only: ``True`` means "the mode leaves the
    existing policy untouched" — it never means the policy engine would allow a
    given action. :func:`plan_side_effect_decision` remains authoritative.
    """
    active = get_mode() if mode is None else _coerce_mode(mode)
    return active in (ExecutionMode.WORK_NORMAL, ExecutionMode.CODE_NORMAL)


def plan_side_effect_decision(action: str, mode: ExecutionMode | str | None = None) -> tuple[Verdict, str]:
    """Decide *action* under *mode* using the policy engine vocabulary.

    - Normal modes: the policy engine verdict passes through unchanged (the
      mode only constrains; it never grants).
    - Plan modes: read-only prefixes (``read:``/``search:``/``test:``/``lint:``)
      keep the policy verdict; every other (side-effecting) action becomes
      ``approval``, or ``deny`` when policy already denies it, with the reason
      ``plan-mode:<mode>: side effects require exiting plan mode``.
    """
    active = get_mode() if mode is None else _coerce_mode(mode)
    decision = _evaluate(action)
    if active in (ExecutionMode.WORK_NORMAL, ExecutionMode.CODE_NORMAL):
        return decision.verdict, decision.reason
    if action.startswith(READ_ONLY_PREFIXES):
        # Read-only stays subject to the real policy — plan mode grants nothing.
        return decision.verdict, decision.reason
    reason = PLAN_SIDE_EFFECT_REASON_TMPL.format(mode=active.value)
    if decision.verdict == "deny":
        return "deny", reason
    return "approval", reason


def mode_status() -> dict:
    """Honest status payload for ``GET /api/plan-mode/mode``.

    Reports what the active mode actually is, where it came from
    (``source``: ``context`` | ``persisted`` | ``default``), and the real note
    from disk (or the default/corruption note). Empty ``updated_at``/``actor``
    on default records are preserved — never fabricated.
    """
    record = load_mode_record()
    active = get_mode()
    ctx = _mode_ctx.get()
    if ctx is not None and ctx.value != record["mode"]:
        source = "context"
        note = f"context override active ({ctx.value}); file record: {record['mode']} ({record['note']})"
    elif record["persisted"]:
        source = "persisted"
        note = record["note"]
    else:
        source = "default"
        note = record["note"]
    return {
        "mode": active.value,
        "source": source,
        "note": note,
        "persisted": record["persisted"] and record["mode"] == active.value,
        "updated_at": record["updated_at"],
        "actor": record["actor"],
        "path": str(mode_path()),
    }
