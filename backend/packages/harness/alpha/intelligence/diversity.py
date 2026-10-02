"""Phase C — behavioural diversity: did the action space survive the change?

The risk this measures
----------------------
*Self-Improvements in Modern Agentic Systems: A Survey* (arXiv 2607.13104) lists
the primary risks of self-improvement as reinforcing errors, **narrowing the
range of behaviours the agent produces**, and overwriting earlier competence.

Alpha has **score** overfitting detection (train↑ while held-out flat → reject).
It had **nothing** for the second-listed risk. The failure that leaves is the
insidious one: an agent discovers one strategy scores well, suppresses
everything else, and its aggregate score holds steady or improves while its
capability surface quietly collapses. Every existing signal looks healthy.

This is the one module in the plan that can reject a candidate that **scored
better**, and that is deliberate. A narrowed agent holding the same score is
worse than one that can still do something else.

Detection requires BOTH conditions
----------------------------------
``collapse_detected`` needs an absolute coverage floor **and** a drop exceeding
the measured noise floor from :mod:`alpha.intelligence.evaluator_stability`.
Either alone fires constantly: an absolute threshold alone is wrong whenever the
baseline was already narrow, and a relative threshold alone hides a collapse
that started from a low base. Two conditions, both required.

Normalising argument shape
--------------------------
Action space is measured over ``(tool_name, normalised_arg_shape)``, not raw
arguments. A retry that passes a different timestamp is the **same** behaviour;
counting it as a new action would make an agent look more diverse the more
noisily it retried, which is the opposite of the truth.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "ActionSignature",
    "normalize_action",
    "BehaviorTrace",
    "DiversityReport",
    "collapse_detected",
    "compare_diversity",
]

#: Keys whose value is an identity or a clock reading, and therefore never a
#: behavioural difference. Matched as substrings so ``run_id`` and
#: ``parent_run_id`` are both covered.
_VOLATILE_KEYS = (
    "id",
    "uuid",
    "token",
    "nonce",
    "timestamp",
    "time",
    "date",
    "created",
    "updated",
    "elapsed",
    "duration",
    "seed",
    "hash",
    "checksum",
    "path",
)

#: Value shapes that vary run to run.
_VOLATILE_VALUE = re.compile(
    r"""^(?:
          [0-9a-fA-F]{8,}(?:-[0-9a-fA-F]{4,}){0,4}      # uuid / hex digest
        | \d{4}-\d{2}-\d{2}[T ]?\d{0,2}:?\d{0,2}:?\d{0,2}.*   # ISO timestamp
        | \d{10,}                                       # epoch seconds/ms
        | 0x[0-9a-fA-F]+                                # hex literal
        | .*/.*                                         # a path
        | .{60,}                                        # long blob (documents, traces)
        )$""",
    re.VERBOSE,
)

#: Longest meaningful value retained in a signature.
_MAX_VALUE_CHARS = 48


@dataclass(frozen=True)
class ActionSignature:
    """One distinct behaviour: a tool plus the shape of how it was called."""

    tool: str
    arg_shape: str

    def __str__(self) -> str:
        return f"{self.tool}({self.arg_shape})"


def _normalize_value(key: str, value: Any) -> str:
    """Reduce one argument value to its behavioural content."""
    lowered = str(key).lower()
    if any(marker in lowered for marker in _VOLATILE_KEYS):
        return "<v>"
    if value is None or isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return "<num>"
    text = str(value)
    if _VOLATILE_VALUE.match(text):
        return "<v>"
    if len(text) > _MAX_VALUE_CHARS:
        return f"<str:{len(text)}>"
    return text


def normalize_action(tool: Any, args: Any) -> ActionSignature:
    """Reduce one call to its behavioural signature.

    Values are normalised **per key**, not just their names: keeping only the key
    would make ``bash(cmd="ls")`` and ``bash(cmd="rm -rf")`` the same behaviour,
    which is the opposite of what an action-space measure is for. What collapses
    is only what genuinely varies run to run — request ids, timestamps, epochs,
    paths, long blobs — so a retry with a fresh id is the same behaviour while two
    different commands are two.
    """
    name = str(getattr(tool, "name", tool) or "unknown").strip() or "unknown"
    if isinstance(args, dict):
        shape = ",".join(f"{key}={_normalize_value(key, args[key])}" for key in sorted(args, key=str))
    elif isinstance(args, (list, tuple)):
        shape = ",".join(_normalize_value(f"_{index}", item) for index, item in enumerate(args))
    elif args is None:
        shape = ""
    else:
        shape = _normalize_value("", args)
    return ActionSignature(tool=name, arg_shape=shape)


@dataclass(frozen=True)
class BehaviorTrace:
    """A sequence of observed actions, with enough context to compare it."""

    actions: tuple[ActionSignature, ...] = ()
    label: str = ""
    context: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_calls(cls, calls: Iterable[tuple[Any, Any]], *, label: str = "", context: dict[str, Any] | None = None) -> BehaviorTrace:
        """Build from ``(tool, args)`` pairs."""
        return cls(
            actions=tuple(normalize_action(tool, args) for tool, args in calls),
            label=label,
            context=dict(context or {}),
        )

    @property
    def action_space(self) -> int:
        """Distinct behaviours observed."""
        return len(set(self.actions))

    def signatures(self) -> set[str]:
        return {str(action) for action in self.actions}

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for action in self.actions:
            key = str(action)
            out[key] = out.get(key, 0) + 1
        return out


@dataclass(frozen=True)
class DiversityReport:
    """Whether the behaviour distribution survived a change."""

    action_space_before: int
    action_space_after: int
    coverage_before: float | None
    coverage_after: float | None
    coverage_delta: float | None
    policy_entropy_before: float | None
    policy_entropy_after: float | None
    collapsed: bool
    reasons: list[str] = field(default_factory=list)
    retained: tuple[str, ...] = ()
    lost: tuple[str, ...] = ()
    gained: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_space_before": self.action_space_before,
            "action_space_after": self.action_space_after,
            "coverage_before": self.coverage_before,
            "coverage_after": self.coverage_after,
            "coverage_delta": self.coverage_delta,
            "policy_entropy_before": self.policy_entropy_before,
            "policy_entropy_after": self.policy_entropy_after,
            "collapsed": self.collapsed,
            "reasons": list(self.reasons),
            "retained": list(self.retained),
            "lost": list(self.lost),
            "gained": list(self.gained),
        }


def _entropy(counts: Sequence[int]) -> float | None:
    """Shannon entropy of a behaviour distribution, or ``None`` when unknowable.

    ``None`` — not ``0.0`` — when there are no observations or only one. A
    single action has zero entropy *as a fact about that sample*, but reporting
    it as a measured zero would claim the agent is deterministic when it may
    simply have been observed once.
    """
    total = sum(counts)
    if total <= 0:
        return None
    if len(counts) <= 1:
        return None
    entropy = 0.0
    for count in counts:
        if count <= 0:
            continue
        probability = count / total
        entropy -= probability * math.log(probability)
    return entropy


def collapse_detected(
    *,
    coverage_before: float | None,
    coverage_after: float | None,
    absolute_floor: float,
    min_drop: float,
) -> tuple[bool, list[str]]:
    """Both conditions required; returns ``(collapsed, reasons)``.

    ``coverage_before is None`` means the baseline was never measured, which is
    **not** evidence of no collapse. That case returns ``False`` with the reason
    recorded, so an unmeasured baseline cannot be used to wave a change through.
    """
    reasons: list[str] = []
    if coverage_before is None or coverage_after is None:
        return False, ["coverage was not measured on both sides, so a collapse cannot be asserted or ruled out"]
    drop = coverage_before - coverage_after
    below_floor = coverage_after < absolute_floor
    material = drop >= min_drop
    if below_floor:
        reasons.append(f"coverage {coverage_after:.4f} is below the absolute floor {absolute_floor:.4f}")
    else:
        reasons.append(f"coverage {coverage_after:.4f} stayed at or above the absolute floor {absolute_floor:.4f}")
    if material:
        reasons.append(f"coverage dropped {drop:.4f}, at or beyond the minimum material drop {min_drop:.4f}")
    else:
        reasons.append(f"coverage dropped only {drop:.4f}, short of the minimum material drop {min_drop:.4f}")
    if below_floor and material:
        reasons.append("both clauses hold, so this is a collapse")
    else:
        missing = []
        if not below_floor:
            missing.append("the absolute floor was not breached")
        if not material:
            missing.append("the drop was not material")
        reasons.append("not a collapse because " + " and ".join(missing))
    return (below_floor and material), reasons


def compare_diversity(
    before: BehaviorTrace,
    after: BehaviorTrace,
    *,
    absolute_floor: float = 0.5,
    min_drop: float = 0.1,
    noise_floor: float = 0.0,
) -> DiversityReport:
    """Compare two behaviour traces and decide whether the space collapsed.

    Args:
        noise_floor: The measured evaluator noise from
            :mod:`alpha.intelligence.evaluator_stability`. It is **added** to
            ``min_drop``, never subtracted, so it can only make a collapse
            *harder* to declare. That direction is deliberate: a coverage figure
            measured on a noisy evaluator should not license a claim on a small
            observed drop. The trade-off is that a genuine but small narrowing can
            be missed until the evaluator is steadier — the conservative error.
    """
    before_sigs, after_sigs = before.signatures(), after.signatures()
    retained = tuple(sorted(before_sigs & after_sigs))
    lost = tuple(sorted(before_sigs - after_sigs))
    gained = tuple(sorted(after_sigs - before_sigs))

    # Coverage is measured against the BASELINE space, not against the trace's
    # own size. Dividing a trace's distinct-action count by its own distinct set
    # is `|S| / |S|`, which is identically 1.0 and would make every comparison
    # report perfect coverage. The question a reviewer actually asks is "does
    # the new agent still do everything the old one did?", so:
    #
    #   coverage_before = 1.0                    (it trivially covers itself)
    #   coverage_after  = |after INTERSECT before| / |before|
    #
    # An empty baseline makes both undefined, which is reported as None so the
    # comparison becomes inconclusive rather than falsely reassuring.
    coverage_before: float | None = 1.0 if before_sigs else None
    coverage_after: float | None = (len(after_sigs & before_sigs) / len(before_sigs)) if before_sigs else None

    delta: float | None = None
    if coverage_before is not None and coverage_after is not None:
        delta = coverage_after - coverage_before

    effective_drop = max(min_drop, noise_floor)
    collapsed, reasons = collapse_detected(
        coverage_before=coverage_before,
        coverage_after=coverage_after,
        absolute_floor=absolute_floor,
        min_drop=effective_drop,
    )
    if noise_floor > 0:
        reasons.append(f"minimum material drop raised to {effective_drop:.4f} by the measured evaluator noise floor {noise_floor:.4f}")

    return DiversityReport(
        action_space_before=before.action_space,
        action_space_after=after.action_space,
        coverage_before=coverage_before,
        coverage_after=coverage_after,
        coverage_delta=delta,
        policy_entropy_before=_entropy(list(before.counts().values())),
        policy_entropy_after=_entropy(list(after.counts().values())),
        collapsed=collapsed,
        reasons=reasons,
        retained=retained,
        lost=lost,
        gained=gained,
    )
