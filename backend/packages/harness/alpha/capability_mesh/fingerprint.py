"""Situation fingerprints: an honest, deliberately crude situation baseline (plan §25).

A :class:`SituationFingerprint` records *what kind of situation* a task is —
its type, complexity, recency requirement, risk, context pressure, and the
capabilities and tools it requires — so the router (plan §3) can route per
microtask instead of per whole prompt. It is the input to decomposition, fit
scoring and policy resolution; it stores no more than the declared situation
and never the task text itself.

Three rules decide the shape of this module:

* **The honest default for complexity is UNKNOWN.** ``complexity_band``
  defaults to :attr:`Band.UNKNOWN`: a fingerprint that matched nothing says
  so instead of guessing. ``risk_band`` and ``context_pressure`` are
  therefore *required* constructor arguments — plan §25 declares both
  required, and defaulting risk to "low" would guess precisely in the unsafe
  direction.
* **The keyword heuristic is a baseline, not intelligence.**
  :func:`fingerprint_task` matches the pinned :data:`TERMS` table with
  word-boundary regexes: deterministic, inspectable, crude by design, and
  replaceable later by a model-driven decomposer. Never describe its output
  as understanding. ``TERMS`` is module-level data precisely so a test can
  pin every word it rules on.
* **``user_policy`` is a plain string here.** Policy *names* are resolved
  against ``policy.py`` at decision time; this module only carries the
  declared value so a fingerprint round-trips without importing the policy
  table (``alpha.capability_mesh.policy`` validates it later).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Band(StrEnum):
    """Complexity band of a situation (plan §25). UNKNOWN is the honest default."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class Risk(StrEnum):
    """Risk class of a situation (plan §25). Required — never defaulted to low."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Pressure(StrEnum):
    """Context pressure of a situation (plan §25). Required — never guessed."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


#: The keyword rules behind :func:`fingerprint_task`, grouped by the field
#: they decide. This is a *deliberately crude baseline*: word-boundary
#: matches on a pinned table, no model, no understanding. Every group name
#: and word here is test-pinned; changing a word changes routing behaviour,
#: so change it with its test.
TERMS: dict[str, tuple[str, ...]] = {
    "complexity_high": (
        "debug",
        "debugging",
        "bug",
        "stacktrace",
        "stack trace",
        "traceback",
        "exception",
        "race condition",
        "deadlock",
        "memory leak",
        "refactor",
        "migration",
        "migrate",
        "concurrency",
        "threading",
        "algorithm",
        "compiler",
        "regression",
        "root cause",
    ),
    "complexity_low": (
        "summarize",
        "summarise",
        "translate",
        "define",
        "what is",
        "rename",
        "list the",
    ),
    "recency": (
        "latest",
        "recent",
        "recently",
        "up to date",
        "up-to-date",
        "today",
        "yesterday",
        "this week",
        "this month",
        "this year",
        "news",
        "headlines",
        "2024",
        "2025",
        "2026",
    ),
    "risk_critical": (
        "deploy",
        "deploying",
        "deployment",
        "production",
        "prod",
        "irreversible",
        "drop table",
        "delete all",
        "purge",
        "wipe",
    ),
    "risk_high": (
        "push",
        "merge",
        "publish",
        "install",
        "upgrade",
        "delete",
        "remove",
        "revoke",
        "rotate",
    ),
    "pressure_high": (
        "urgent",
        "asap",
        "immediately",
        "right now",
        "deadline",
        "blocking",
        "emergency",
    ),
}

#: Task text at or beyond this many characters is treated as context-pressing
#: by :func:`fingerprint_task` — a long request needs more context gathered
#: before it can be answered.
LONG_TASK_PRESSURE_CHARS = 2000


def _coerce(enum_cls: type[StrEnum], value: Any, field: str) -> StrEnum:
    """Coerce ``value`` into ``enum_cls`` or refuse naming the allowed values."""
    try:
        return enum_cls(str(value))
    except ValueError as error:
        allowed = ", ".join(member.value for member in enum_cls)
        raise ValueError(f"unknown {field} {value!r}; expected one of: {allowed}") from error


@dataclass(frozen=True)
class SituationFingerprint:
    """The declared situation one task stands in (plan §25).

    ``task_type`` and ``subtask_type`` are caller-declared labels;
    ``complexity_band`` defaults to :attr:`Band.UNKNOWN` because an unmatched
    task is *unknown*, not easy. ``required_capabilities``/``required_tools``
    are the requirements routing will gate on — they may be empty, and an
    empty tuple means "nothing declared", never "nothing needed". Each entry
    must be a non-empty string; duplicates are refused at the call site that
    builds them (see :func:`fingerprint_task`), not silently merged here.
    """

    task_type: str
    risk_band: Risk
    context_pressure: Pressure
    subtask_type: str | None = None
    complexity_band: Band = Band.UNKNOWN
    recency_required: bool = False
    required_capabilities: tuple[str, ...] = ()
    required_tools: tuple[str, ...] = ()
    user_policy: str = "balanced"

    def __post_init__(self) -> None:
        if not self.task_type:
            raise ValueError("task_type must be non-empty")
        object.__setattr__(self, "risk_band", _coerce(Risk, self.risk_band, "risk_band"))
        object.__setattr__(self, "context_pressure", _coerce(Pressure, self.context_pressure, "context_pressure"))
        object.__setattr__(self, "complexity_band", _coerce(Band, self.complexity_band, "complexity_band"))
        if self.subtask_type is not None and not self.subtask_type:
            raise ValueError("subtask_type must be a non-empty string or None (an empty label answers nothing)")
        if not isinstance(self.recency_required, bool):
            raise ValueError(f"recency_required must be a bool, got {self.recency_required!r}")
        if not self.user_policy:
            raise ValueError("user_policy must be non-empty (resolved against policy.py at decision time)")
        for field in ("required_capabilities", "required_tools"):
            entries = tuple(getattr(self, field))
            for entry in entries:
                if not entry:
                    raise ValueError(f"{field} entries must be non-empty strings")
            object.__setattr__(self, field, entries)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_type": self.task_type,
            "subtask_type": self.subtask_type,
            "complexity_band": self.complexity_band.value,
            "recency_required": self.recency_required,
            "risk_band": self.risk_band.value,
            "context_pressure": self.context_pressure.value,
            "required_capabilities": list(self.required_capabilities),
            "required_tools": list(self.required_tools),
            "user_policy": self.user_policy,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SituationFingerprint:
        # The constructor re-validates, so a hand-edited document cannot
        # bypass the honesty rules by skipping __post_init__.
        return cls(
            task_type=str(data["task_type"]),
            subtask_type=data.get("subtask_type"),
            # Coerced (and validated with the friendly message) in __post_init__.
            complexity_band=data.get("complexity_band", Band.UNKNOWN),
            recency_required=bool(data.get("recency_required", False)),
            risk_band=data["risk_band"],
            context_pressure=data["context_pressure"],
            required_capabilities=tuple(str(name) for name in data.get("required_capabilities", ())),
            required_tools=tuple(str(name) for name in data.get("required_tools", ())),
            user_policy=str(data.get("user_policy", "balanced")),
        )


def _hits(lowered: str, group: str) -> bool:
    """True when any pinned term of ``group`` occurs in ``lowered`` as a word.

    Word boundaries matter: the risk term "prod" must not fire on
    "produce", and "merge" must not fire on "mergeable prose".
    """
    return any(re.search(rf"\b{re.escape(term)}\b", lowered) for term in TERMS[group])


def fingerprint_task(
    text: str,
    *,
    task_type: str = "general",
    subtask_type: str | None = None,
    required_capabilities: Sequence[str] = (),
    required_tools: Sequence[str] = (),
    user_policy: str = "balanced",
) -> SituationFingerprint:
    """Derive a situation fingerprint from ``text`` with the pinned keyword rules.

    A **deliberately crude, deterministic baseline** — same input, same
    fingerprint — chosen so routing has something inspectable before any
    model is involved. It matches :data:`TERMS` with word boundaries:

    * code/debug vocabulary ("debug", "root cause", …) → ``Band.HIGH``;
      summary vocabulary ("define", …) → ``Band.LOW``; **nothing matched →
      ``Band.UNKNOWN``** — the baseline never guesses a band;
    * recency vocabulary ("latest", "recent", "2026", …) →
      ``recency_required=True``;
    * irreversible-deployment vocabulary ("deploy", "production",
      "irreversible", …) → ``Risk.CRITICAL``; side-effecting vocabulary
      ("push", "merge", …) → ``Risk.HIGH``; nothing matched → ``Risk.LOW``;
    * urgency vocabulary ("urgent", "asap", …) → ``Pressure.HIGH``, else
      task text at least ``LONG_TASK_PRESSURE_CHARS`` long →
      ``Pressure.MEDIUM``, else ``Pressure.LOW``.

    This is keyword matching, not understanding: a sarcastic "what a
    production!" still reads as critical. A model-driven decomposer may
    replace it later; until then, never present its output as intelligent
    situation assessment.
    """
    if not text:
        raise ValueError("fingerprint_task requires non-empty text")
    lowered = text.lower()

    if _hits(lowered, "complexity_high"):
        complexity = Band.HIGH
    elif _hits(lowered, "complexity_low"):
        complexity = Band.LOW
    else:
        complexity = Band.UNKNOWN

    if _hits(lowered, "risk_critical"):
        risk = Risk.CRITICAL
    elif _hits(lowered, "risk_high"):
        risk = Risk.HIGH
    else:
        risk = Risk.LOW

    if _hits(lowered, "pressure_high"):
        pressure = Pressure.HIGH
    elif len(text) >= LONG_TASK_PRESSURE_CHARS:
        pressure = Pressure.MEDIUM
    else:
        pressure = Pressure.LOW

    return SituationFingerprint(
        task_type=task_type,
        subtask_type=subtask_type,
        complexity_band=complexity,
        recency_required=_hits(lowered, "recency"),
        risk_band=risk,
        context_pressure=pressure,
        required_capabilities=tuple(dict.fromkeys(required_capabilities)),
        required_tools=tuple(dict.fromkeys(required_tools)),
        user_policy=user_policy,
    )


__all__ = [
    "LONG_TASK_PRESSURE_CHARS",
    "TERMS",
    "Band",
    "Pressure",
    "Risk",
    "SituationFingerprint",
    "fingerprint_task",
]
