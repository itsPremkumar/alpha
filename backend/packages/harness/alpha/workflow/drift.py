"""Goal-drift detection: does this run still serve the goal it was given?

Why this exists
---------------
A dynamic workflow optimizes for its graph, not for its prompt. Node after
node can succeed — real evidence, real outputs, a COMPLETED run — while the
work as a whole wanders: a research phase that drifts into tooling trivia, an
implementation phase that quietly solves a neighboring problem. The engine
measured *execution* faithfully and had nothing to say about *direction*,
because no seam compared a run's trajectory against its declared goal.

This module is that seam, deliberately built as measurement-with-teeth
rather than judgment:

* **Deterministic, disclosed, and bounded.** Terms come from the declared goal
  by a published stopword rule; per-node scores are term overlap over the
  node's own output text; the trajectory is the trailing mean over a fixed
  window. No model call, no embedding, no hidden state — an operator can
  recompute every number from the run's own outputs.
* **A proposal, never an action.** Nothing here mutates a run, a graph, or a
  node. The verdict is journalled as ``goal_drift_detected`` and projected
  through a read-only route, exactly like ``alpha.workflow.self_improvement``
  proposes and never applies. A drifting run is still a valid run; the
  operator (or a replan they approve) decides what happens next.
* **Unmeasurable is not drifted.** A node whose output carries no extractable
  text scores ``None`` and is excluded from the mean. Counting an unreadable
  node as zero-overlap would make every workflow that logs a hash drift, and
  a detector that cries on healthy runs gets ignored.

Honesty rules
-------------
* The goal is RESOLVED from declared places only — graph metadata, run state,
  definition description — in a fixed precedence that is reported with the
  verdict. A drift score against an invented goal would be a fabricated
  number about a made-up target.
* Too few measurable samples yields ``insufficient_evidence`` with the real
  count, never a confident verdict over one data point.
* The threshold is declared per workflow (graph metadata) or defaulted, and
  the default is published here rather than buried in the engine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "DEFAULT_DRIFT_THRESHOLD",
    "DRIFT_THRESHOLD_KEY",
    "GOAL_DRIFT_KEY",
    "GOAL_DRIFT_SAMPLES_KEY",
    "GOAL_METADATA_KEY",
    "MIN_MEASURABLE_SAMPLES",
    "DriftSample",
    "DriftVerdict",
    "GoalDriftReport",
    "evaluate_trajectory",
    "extract_terms",
    "measure_text_overlap",
    "resolve_goal",
]

#: Graph-metadata key a workflow may use to declare its own threshold.
DRIFT_THRESHOLD_KEY = "goal_drift_threshold"

#: Graph-metadata key holding the declared goal when the run state does not.
GOAL_METADATA_KEY = "goal"

#: Run-metric keys the engine writes and the route reads. They live in
#: ``metrics`` because replay must be able to reconstruct them from the log.
GOAL_DRIFT_KEY = "goal_drift"
GOAL_DRIFT_SAMPLES_KEY = "goal_drift_samples"

#: Default fraction of goal terms a node's output must touch before the run
#: counts as drifting. Deliberately low: this is an alarm for "the work
#: stopped being about the goal at all", not a style critic.
DEFAULT_DRIFT_THRESHOLD = 0.10

#: Measurable nodes required before any verdict is offered. Below this the
#: report says ``insufficient_evidence`` and names the real count.
MIN_MEASURABLE_SAMPLES = 3

#: How many of the most recent measurable samples the verdict weighs.
TRAILING_WINDOW = 3

#: How many per-node samples a run retains in ``metrics``. The trajectory is
#: a trailing-window measurement, so a bounded recent history carries every
#: number the verdict can use — and keeps ``run.metrics``, which replay must
#: reconstruct from the log, from growing without limit.
MAX_DRIFT_SAMPLES = 50

#: Bounds. The term ceiling keeps a 5,000-word prompt from producing a term
#: list whose overlap arithmetic is meaningless; the text ceiling keeps a
#: 10 MB node output from being scanned per node.
MAX_TERMS = 24
MAX_TEXT_CHARS = 4000

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_\-/]{1,}")

_STOPWORDS = frozenset(
    """
    a about above after again against all almost along already also although always am among amongst an
    and another any anyhow anyone anything anyway are around as at available be became because become
    becomes been before beforehand behind being below beside besides between beyond both but by came can
    cannot come could did do does doing done down during each either else elsewhere enough etc even ever
    every everyone everything everywhere few for former formerly from further get gets given gives going
    gone had has have having he hence her here hereafter hereby herein hereupon hers herself him himself
    his how however i if in indeed instead into inward is it its itself just keep kept know last latter
    latterly least less let like likely little made make many may me meanwhile might more moreover most
    mostly much must my myself namely neither never nevertheless next no nobody none nonetheless noone
    nor not nothing now nowhere of off often on once one only onto or other others otherwise our ours
    ourselves out over own per perhaps rather same seem seemed seeming seems several she should since so
    some somehow someone something sometime sometimes somewhat somewhere still such than that the their
    theirs them themselves then thence there thereafter thereby therefore therein thereupon these they
    this those though through throughout thru thus to together too toward towards under until unto upon
    us use used very via was we well were what whatever when whence whenever where whereas whereby
    wherein whereupon wherever whether which while whither who whoever whom whose why will with within
    without would yet you your yours yourself yourselves
    implement implementation create build build-out make write run execute execute add update fix
    test testing verify please need needs using use used able ensure let shall can may might new full
    """.split()
)


class DriftVerdict(str):
    """A verdict string. A plain ``str`` subclass so JSON payloads stay plain."""


@dataclass(frozen=True)
class DriftSample:
    """One node's measured overlap with the goal."""

    node_id: str
    #: Fraction of goal terms present in the node's output text, or ``None``
    #: when the node's output carried no measurable text at all.
    score: float | None
    terms_hit: tuple[str, ...] = ()
    terms_missed: tuple[str, ...] = ()
    text_chars: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "score": self.score,
            "terms_hit": list(self.terms_hit),
            "terms_missed": list(self.terms_missed),
            "text_chars": self.text_chars,
        }


@dataclass(frozen=True)
class GoalDriftReport:
    """The whole trajectory verdict, with every input it used disclosed."""

    has_goal: bool
    goal: str = ""
    goal_source: str = ""
    threshold: float = DEFAULT_DRIFT_THRESHOLD
    verdict: str = "insufficient_evidence"
    reason: str = ""
    trailing_score: float | None = None
    measurable: int = 0
    drifted_nodes: tuple[str, ...] = ()
    samples: tuple[DriftSample, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "has_goal": self.has_goal,
            "goal": self.goal,
            "goal_source": self.goal_source,
            "threshold": self.threshold,
            "verdict": self.verdict,
            "reason": self.reason,
            "trailing_score": self.trailing_score,
            "measurable": self.measurable,
            "drifted_nodes": list(self.drifted_nodes),
            "samples": [sample.to_dict() for sample in self.samples],
        }


def extract_terms(goal: str) -> tuple[str, ...]:
    """The bounded, ordered term list a goal is measured against.

    Splitting, lowercasing, stopword removal and de-duplication are all
    deterministic; the output order is stable so two reports over the same
    goal are byte-identical. A goal with no surviving term yields ``()``, and
    the caller reports ``insufficient_evidence`` rather than scoring against
    an empty target — overlap with nothing is not zero overlap, it is no
    measurement.
    """
    if not isinstance(goal, str):
        return ()
    seen: dict[str, None] = {}
    for token in _TOKEN_RE.findall(goal.lower()):
        if len(token) < 3 or token in _STOPWORDS:
            continue
        seen.setdefault(token)
        if len(seen) >= MAX_TERMS:
            break
    return tuple(seen)


def resolve_goal(*, graph_metadata: dict[str, Any] | None, run_state: dict[str, Any] | None, definition_description: str = "") -> tuple[str, str]:
    """The goal this run is measured against, and where it came from.

    Precedence is fixed and reported: graph metadata, then run state
    (``objective`` before ``goal`` — the dynamic service writes the prompt
    there), then the definition description. Returns ``("", "")` when nothing
    declares a goal: no goal, no drift claim.
    """
    metadata = graph_metadata or {}
    declared = metadata.get(GOAL_METADATA_KEY)
    if isinstance(declared, str) and declared.strip():
        return declared.strip(), f"graph.metadata.{GOAL_METADATA_KEY}"
    state = run_state or {}
    for key in ("objective", "goal"):
        value = state.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip(), f"run.state.{key}"
    if isinstance(definition_description, str) and definition_description.strip():
        return definition_description.strip(), "definition.description"
    return "", ""


def _text_of(output: Any) -> str:
    """Flatten a node output to bounded text, deterministically.

    A dict is flattened by sorted key so the same output always produces the
    same text; anything else is stringified. The flattening is deliberately
    naive — its job is to make term overlap computable, not to parse
    structure, and a smarter reader here would be an unmeasurable
    interpretation layer.
    """
    if isinstance(output, str):
        text = output
    elif isinstance(output, dict):
        text = " ".join(f"{key} {value}" for key, value in sorted((str(k), v) for k, v in output.items()) if value is not None)
    else:
        text = str(output)
    return text[:MAX_TEXT_CHARS]


def measure_text_overlap(text: str, terms: tuple[str, ...]) -> tuple[float, tuple[str, ...], tuple[str, ...]]:
    """Fraction of ``terms`` present in ``text``, with the hit/missed split.

    Substring matching on word boundaries would be the honest ideal; token
    containment is used because the terms were themselves produced by
    tokenization, so a term matches where it occurs as a token. Both the score
    and the split are returned so a consumer can see WHICH terms were missed
    instead of trusting a bare ratio.
    """
    if not terms:
        return 0.0, (), tuple(terms)
    lowered = text.lower()
    tokens = set(_TOKEN_RE.findall(lowered))
    hit = tuple(term for term in terms if term in tokens)
    missed = tuple(term for term in terms if term not in tokens)
    return len(hit) / len(terms), hit, missed


def measure_node(node_id: str, output: Any, terms: tuple[str, ...]) -> DriftSample:
    """One node's drift sample. Unmeasurable text scores ``None``."""
    text = _text_of(output).strip()
    if not terms or len(text) < 3:
        return DriftSample(node_id=node_id, score=None, text_chars=len(text))
    score, hit, missed = measure_text_overlap(text, terms)
    return DriftSample(node_id=node_id, score=score, terms_hit=hit, terms_missed=missed, text_chars=len(text))


def samples_from_dicts(payloads: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> list[DriftSample]:
    """Rebound samples from the run-metrics projection.

    A non-mapping entry is dropped; a mapping whose ``score`` will not parse is
    kept as UNMEASURABLE (``score=None``) rather than dropped, so the node's
    identity survives in the trajectory and only its number is withheld — a
    corrupted projection must not silently delete a measurement.
    """
    rebuilt: list[DriftSample] = []
    for payload in payloads or []:
        if not isinstance(payload, dict):
            continue
        raw_score = payload.get("score")
        score: float | None
        if raw_score is None:
            score = None
        else:
            try:
                score = float(raw_score)
            except (TypeError, ValueError):
                score = None
        try:
            rebuilt.append(
                DriftSample(
                    node_id=str(payload.get("node_id", "")),
                    score=score,
                    terms_hit=tuple(str(term) for term in payload.get("terms_hit", [])),
                    terms_missed=tuple(str(term) for term in payload.get("terms_missed", [])),
                    text_chars=int(payload.get("text_chars", 0) or 0),
                )
            )
        except (TypeError, ValueError):
            continue
    return rebuilt


def evaluate_trajectory(
    goal: str,
    goal_source: str,
    samples: list[DriftSample] | tuple[DriftSample, ...],
    *,
    threshold: float = DEFAULT_DRIFT_THRESHOLD,
) -> GoalDriftReport:
    """The trajectory verdict over ordered ``samples``.

    ``drifting`` requires BOTH at least :data:`MIN_MEASURABLE_SAMPLES`
    measurable samples and a trailing mean below the threshold. Anything less
    is ``insufficient_evidence`` naming the real count — a verdict over one
    data point is a guess wearing a number. A run with no goal at all reports
    ``no_goal`` rather than a score computed against nothing.
    """
    threshold = float(threshold)
    if not goal:
        return GoalDriftReport(has_goal=False, threshold=threshold, verdict="no_goal", reason="no goal is declared for this run; drift is not measurable", samples=tuple(samples))
    measurable = [sample for sample in samples if sample.score is not None]
    drifted_nodes = tuple(sample.node_id for sample in measurable if sample.score is not None and sample.score < threshold)
    if len(measurable) < MIN_MEASURABLE_SAMPLES:
        return GoalDriftReport(
            has_goal=True,
            goal=goal,
            goal_source=goal_source,
            threshold=threshold,
            verdict="insufficient_evidence",
            reason=f"only {len(measurable)} measurable node output(s) so far; at least {MIN_MEASURABLE_SAMPLES} are required before a drift verdict is offered",
            measurable=len(measurable),
            drifted_nodes=drifted_nodes,
            samples=tuple(samples),
        )
    trailing = measurable[-TRAILING_WINDOW:]
    trailing_score = sum(sample.score for sample in trailing if sample.score is not None) / len(trailing)
    if trailing_score < threshold:
        verdict = "drifting"
        reason = (
            f"trailing mean term overlap {trailing_score:.3f} is below the {threshold:.3f} threshold over the last {len(trailing)} measurable node(s); "
            f"most-missed terms: {sorted({term for sample in trailing for term in sample.terms_missed})[:6]}"
        )
    else:
        verdict = "on_goal"
        reason = f"trailing mean term overlap {trailing_score:.3f} meets the {threshold:.3f} threshold over the last {len(trailing)} measurable node(s)"
    return GoalDriftReport(
        has_goal=True,
        goal=goal,
        goal_source=goal_source,
        threshold=threshold,
        verdict=verdict,
        reason=reason,
        trailing_score=trailing_score,
        measurable=len(measurable),
        drifted_nodes=drifted_nodes,
        samples=tuple(samples),
    )
