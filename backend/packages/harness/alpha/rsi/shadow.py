"""Shadow evaluation harness for RSI (plan WP-C1, feature #6; ``alpha.rsi.shadow``).

Runs a baseline evaluator and a candidate evaluator over the *identical*
fixture-id list and records exactly one :class:`ShadowComparison` — evidence
for the WP-C2 promotion gate, never an action.  Following the
``alpha.models.system_one`` shadow convention (shadow mode records the
decision and then returns ``None`` so every call site keeps its existing
path), this module NEVER takes over: it does not promote, roll back, mutate
its inputs, run commands, or touch any file other than its own record at
``runtime_home()/rsi/shadow/<run_id>.json``.

Integrations (verified against the landed code, not assumed):

- fixture source: ``alpha.benchmarks.runner`` — ``fixture_ids_from_suite``
  extracts ``BenchmarkCase.case_id`` values from a registered
  ``BenchmarkSuite`` (the runner's own ``Evaluator`` returns
  ``tuple[bool, float, str]``; ``run_shadow``'s evaluator contract is
  deliberately ``Callable[[str], dict]`` returning flat metric dicts).
- atomic persistence: ``alpha.evolution.identity.atomic_write_json`` — unique
  tmp name + ``os.replace`` (atomic on Windows and POSIX).
- record location: ``alpha.config.runtime_paths.runtime_home()`` resolved at
  call time (so ``AGENT_WORKSPACE_HOME`` isolation works per test).
- deliberately NOT imported: ``alpha.rsi.workspace`` (WP-B1) — this module
  executes no subprocess, so there is nothing for ``run_checks`` to guard;
  code-touching candidates keep their isolation seam there, and fixture
  side-effect-freedom itself comes from offline fixture design (plan WP-C1:
  Phase C shadows *fixture functions*, live-traffic shadow is out of scope).

Honesty contract (plan section 3 WP-C1 + section 5 guardrails):

- **Disclosure:** every record carries ``disclosure`` = ``DISCLOSURE_NOTE``
  (constructor-validated — it cannot be emptied or rewritten): an
  *offline fixture shadow, not live-traffic shadow*; results are
  ``channel="shadow"`` gate evidence and are never counted as deployment
  traffic.
- **State machine:** exactly three states, ``improved | regressed |
  inconclusive`` (``SHADOW_STATES``; anything else raises ``ValueError``).
  Any per-fixture failure — evaluator exception (real error text recorded
  verbatim: ``fixture <id> raised: <side> evaluator <Error>``), non-dict
  return, missing/non-comparable metric, empty metrics, or an empty fixture
  list — forces ``inconclusive`` fail-closed: never a win for either side,
  even when other fixtures show large improvements *or* regressions.
  Otherwise: any strict regression → ``regressed`` (the fixture is named in
  ``deltas``, never averaged away); zero regressions + at least one strict
  improvement → ``improved``; all comparisons tied → ``inconclusive`` (a tie
  is not an improvement, and inventing "unchanged" as a win would fabricate
  a verdict the plan does not define).
- **Deltas:** only for a fixture that both sides completed with equal metric
  key sets and finite numeric values on both sides; ``delta = candidate -
  baseline`` with lower-is-better semantics (error counts, latencies — the
  plan's comparison rule). A missing metric excludes the whole fixture from
  ``deltas`` with an explanatory note — no delta is ever fabricated. Bools,
  NaN, infinities, non-numbers, and empty metric dicts are non-comparable.
  Metric dicts must be flat and str-keyed; a payload that cannot be
  canonicalized surfaces the real ``TypeError``/``ValueError`` instead of a
  half-record.
- **evidence_kind:** ``measured`` only when every fixture actually executed
  and compared end-to-end; any fail-closed path yields ``unverified``
  (disclosed, never a pass). Values are validated against the section 5.6
  whitelist ``EVIDENCE_KINDS = {measured, simulated, heuristic, unverified}``;
  ``run_shadow`` itself only ever emits ``measured`` or ``unverified`` —
  never ``simulated``/``heuristic``. No pass rates, confidences, scores, or
  "improvement percentages" exist anywhere in this module.
- **run_id:** content-addressed (``shadow-`` + sha256 prefix over the record
  minus ``run_id``) — identical inputs produce identical ids and records; no
  wall clock, no randomness, fully deterministic (no injectable clock is
  needed because the record carries no timestamp field).
- **Side effects:** calling the two evaluators (once per fixture, both sides
  always attempted so the record discloses both) plus one atomic record
  write. A persistence failure surfaces the real exception — ``run_shadow``
  never returns a comparison that failed to persist. ``load_shadow_record``
  re-derives ``run_id`` from the file contents and fails closed with the
  real stored/recomputed/requested values on any mismatch or corruption.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Literal

from alpha.benchmarks.runner import BenchmarkSuite
from alpha.config.runtime_paths import runtime_home
from alpha.evolution.identity import atomic_write_json

__all__ = [
    "DISCLOSURE_NOTE",
    "EVIDENCE_KINDS",
    "SHADOW_STATES",
    "ShadowComparison",
    "fixture_ids_from_suite",
    "load_shadow_record",
    "run_shadow",
    "shadow_record_path",
]

ShadowState = Literal["improved", "regressed", "inconclusive"]

# Plan WP-C1 state machine: only these three values are ever emitted.
SHADOW_STATES: frozenset[str] = frozenset({"improved", "regressed", "inconclusive"})
# Plan section 5.6 evidence whitelist, enforced at construction and in tests.
EVIDENCE_KINDS: frozenset[str] = frozenset({"measured", "simulated", "heuristic", "unverified"})
CHANNEL = "shadow"
DISCLOSURE_NOTE = (
    "offline fixture shadow, not live-traffic shadow: comparison evidence for the gate only "
    "(channel=shadow), never counted as deployment traffic"
)
_SHADOW_RELATIVE_DIR = Path("rsi") / "shadow"
_RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


@dataclass
class ShadowComparison:
    """One baseline-vs-candidate fixture comparison record (plan WP-C1).

    ``baseline`` / ``candidate`` map ``fixture_id -> {metric: value}`` for the
    fixtures each side completed; ``deltas`` maps ``fixture_id -> {metric:
    candidate - baseline}`` for fully comparable fixtures only (lower-is-better
    metrics: negative = improvement, positive = regression). ``notes`` carries
    per-fixture findings with real error text; ``disclosure`` is pinned to
    ``DISCLOSURE_NOTE`` so no record can ship without the offline-fixture
    disclosure; ``channel`` is pinned to ``"shadow"`` (never deployment
    traffic). Construction validates state/evidence whitelist membership.
    """

    run_id: str
    fixture_set: str
    baseline: dict
    candidate: dict
    deltas: dict
    state: ShadowState
    evidence_kind: str
    notes: list[str]
    channel: str = CHANNEL
    disclosure: str = DISCLOSURE_NOTE

    def __post_init__(self) -> None:
        if self.state not in SHADOW_STATES:
            raise ValueError(f"invalid shadow state {self.state!r}; only {sorted(SHADOW_STATES)} are ever emitted")
        if self.evidence_kind not in EVIDENCE_KINDS:
            raise ValueError(f"invalid evidence_kind {self.evidence_kind!r}; section 5.6 whitelist is {sorted(EVIDENCE_KINDS)}")
        if self.channel != CHANNEL:
            raise ValueError(f"invalid channel {self.channel!r}; this module only ever emits channel={CHANNEL!r}")
        if self.disclosure != DISCLOSURE_NOTE:
            raise ValueError(f"invalid disclosure {self.disclosure!r}; the offline-fixture-not-live disclosure may not be emptied or rewritten")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ShadowComparison:
        if not isinstance(data, dict):
            raise TypeError(f"ShadowComparison.from_dict expects a dict, got {type(data).__name__}")
        known = {field.name for field in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in known})


def _compute_run_id(payload: dict[str, Any]) -> str:
    """Content-addressed id: ``shadow-`` + sha256 prefix over the canonical record (minus ``run_id``)."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "shadow-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def shadow_record_path(run_id: str) -> Path:
    """``runtime_home()/rsi/shadow/<run_id>.json``; ids that could escape the directory are rejected."""
    if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id) or ".." in run_id:
        raise ValueError(f"invalid shadow run_id {run_id!r}: expected 1-128 chars of [A-Za-z0-9._-] starting with an alphanumeric, no '..'")
    return runtime_home() / _SHADOW_RELATIVE_DIR / f"{run_id}.json"


def fixture_ids_from_suite(suite: BenchmarkSuite) -> list[str]:
    """Fixture ids for ``run_shadow`` from the benchmark plane (fixture source: ``alpha.benchmarks.runner``)."""
    return [case.case_id for case in suite.cases]


def _is_comparable_number(value: Any) -> bool:
    """Finite int/float only: bools, NaN, infinities, and non-numbers cannot be honestly ordered."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True  # arbitrary-precision ints are always finite and orderable
    if isinstance(value, float):
        return math.isfinite(value)
    return False


def _evaluate_side(evaluator: Callable[[str], dict], fixture_id: str, side: str, notes: list[str]) -> dict | None:
    """Run one evaluator for one fixture; on any failure return ``None`` plus a real-text note (fail-closed)."""
    try:
        result = evaluator(fixture_id)
    except Exception as exc:  # plan WP-C1: the real exception text is recorded verbatim; no score is invented
        notes.append(f"fixture {fixture_id} raised: {side} evaluator {type(exc).__name__}: {exc}")
        return None
    if not isinstance(result, dict):
        notes.append(f"fixture {fixture_id} inconclusive: {side} evaluator returned {type(result).__name__}, expected dict")
        return None
    return dict(result)


def _compare_fixture(fixture_id: str, base_metrics: dict, cand_metrics: dict) -> tuple[dict | None, str | None]:
    """Joint-metric deltas for one fully comparable fixture; else ``(None, note)`` — never a fabricated delta."""
    if not base_metrics or not cand_metrics:
        empty_side = "baseline" if not base_metrics else "candidate"
        return None, f"fixture {fixture_id} inconclusive: empty metric dict on {empty_side} evaluator - nothing to compare, no delta fabricated"
    only_base = sorted(set(base_metrics) - set(cand_metrics))
    only_cand = sorted(set(cand_metrics) - set(base_metrics))
    if only_base or only_cand:
        return None, (
            f"fixture {fixture_id} inconclusive: metric key sets differ (present only on baseline: {only_base!r}; "
            f"present only on candidate: {only_cand!r}) - excluded from deltas, no delta fabricated"
        )
    deltas: dict = {}
    for metric in sorted(base_metrics):
        base_value = base_metrics[metric]
        cand_value = cand_metrics[metric]
        if not _is_comparable_number(base_value) or not _is_comparable_number(cand_value):
            return None, (
                f"fixture {fixture_id} inconclusive: non-comparable value(s) {metric} "
                f"(baseline={base_value!r}, candidate={cand_value!r}; finite numbers required) - "
                "excluded from deltas, no delta fabricated"
            )
        deltas[metric] = cand_value - base_value
    return deltas, None


def run_shadow(
    *,
    baseline_eval: Callable[[str], dict],
    candidate_eval: Callable[[str], dict],
    fixtures: list[str],
    fixture_set: str,
) -> ShadowComparison:
    """Compare baseline and candidate on the identical fixture list; persist and return the record.

    Both callables run for every fixture inside per-side ``try/except``: an
    exception records ``fixture <id> raised: <side> evaluator <Error>`` with
    the real error text and forces ``state="inconclusive"`` fail-closed (never
    a win for either side). ``state="improved"`` requires zero regressions
    AND at least one strict improvement across jointly-completed fixtures;
    any regression yields ``regressed``; anomalies, ties, and an empty
    fixture list yield ``inconclusive``. ``evidence_kind`` is ``measured``
    only when every fixture executed and compared end-to-end, else
    ``unverified``.

    Inputs are never mutated (the fixture list is snapshotted and
    de-duplicated; evaluator dicts are stored as copies). The only side
    effects are the evaluator calls and one atomic record write via
    ``alpha.evolution.identity.atomic_write_json``; a persistence failure
    surfaces the real exception rather than returning an unpersistable
    comparison.

    Raises ``TypeError``/``ValueError`` for contract-violating arguments, and
    propagates the real persistence/serialization error on write.
    """
    if not callable(baseline_eval):
        raise TypeError(f"baseline_eval must be callable, got {type(baseline_eval).__name__}")
    if not callable(candidate_eval):
        raise TypeError(f"candidate_eval must be callable, got {type(candidate_eval).__name__}")
    if not isinstance(fixture_set, str) or not fixture_set.strip():
        raise ValueError(f"fixture_set must be a non-empty string, got {fixture_set!r}")
    if not isinstance(fixtures, list):
        raise TypeError(f"fixtures must be a list of str ids, got {type(fixtures).__name__}")
    non_str = [entry for entry in fixtures if not isinstance(entry, str)]
    if non_str:
        raise TypeError(f"fixtures must contain only str ids, got non-str entries: {non_str!r}")

    notes: list[str] = []
    fixture_ids = list(dict.fromkeys(fixtures))  # snapshot + de-duplicate; the caller's list is never touched
    if len(fixture_ids) != len(fixtures):
        repeated = sorted({entry for entry in fixtures if fixtures.count(entry) > 1})
        notes.append(f"duplicate fixture id(s) evaluated once: {repeated!r}")

    baseline: dict = {}
    candidate: dict = {}
    deltas: dict = {}
    failed = False

    if not fixture_ids:
        failed = True
        notes.append("no fixtures supplied - nothing was executed (fail-closed, no verdict)")
    else:
        for fixture_id in fixture_ids:
            # Both sides always run (even when one failed) so the record discloses each side honestly.
            base_result = _evaluate_side(baseline_eval, fixture_id, "baseline", notes)
            cand_result = _evaluate_side(candidate_eval, fixture_id, "candidate", notes)
            if base_result is not None:
                baseline[fixture_id] = base_result
            if cand_result is not None:
                candidate[fixture_id] = cand_result
            if base_result is None or cand_result is None:
                failed = True
                continue
            fixture_deltas, note = _compare_fixture(fixture_id, base_result, cand_result)
            if note is not None:
                failed = True
                notes.append(note)
            else:
                deltas[fixture_id] = fixture_deltas  # type: ignore[assignment] # note is None ⇒ deltas is a dict

    regressed = sorted(fixture_id for fixture_id, entry in deltas.items() if any(delta > 0 for delta in entry.values()))
    improved = sorted(fixture_id for fixture_id, entry in deltas.items() if any(delta < 0 for delta in entry.values()))
    if failed:
        state: ShadowState = "inconclusive"  # fail-closed: fixture errors outweigh every computed delta
    elif regressed:
        state = "regressed"
        notes.append(f"regression(s) on fixture(s) {regressed!r}: candidate worse than baseline on at least one jointly compared lower-is-better metric")
    elif improved:
        state = "improved"
        notes.append(f"zero regressions and at least one strict improvement across {len(deltas)} jointly compared fixture(s)")
    else:
        state = "inconclusive"
        notes.append("all jointly compared metrics tied (zero regressions, zero strict improvements) - inconclusive, not improved")
    evidence_kind = "measured" if not failed else "unverified"

    payload: dict[str, Any] = {
        "fixture_set": fixture_set,
        "baseline": baseline,
        "candidate": candidate,
        "deltas": deltas,
        "state": state,
        "evidence_kind": evidence_kind,
        "notes": list(notes),
        "channel": CHANNEL,
        "disclosure": DISCLOSURE_NOTE,
    }
    run_id = _compute_run_id(payload)
    comparison = ShadowComparison(run_id=run_id, **payload)
    atomic_write_json(shadow_record_path(run_id), comparison.to_dict())
    return comparison


def load_shadow_record(run_id: str) -> ShadowComparison:
    """Load a persisted comparison and re-derive its content-addressed id (fail-closed integrity read).

    A missing file surfaces ``FileNotFoundError`` and corrupt JSON surfaces
    ``json.JSONDecodeError`` verbatim; if the stored, recomputed, or requested
    ``run_id`` disagree (tampering, truncation, cross-file copy), a
    ``ValueError`` carries all three real values — no partial or untrusted
    record is ever returned.
    """
    path = shadow_record_path(run_id)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError(f"shadow record at {path} is not a JSON object (got {type(raw).__name__})")
    stored = raw.get("run_id")
    recomputed = _compute_run_id({key: value for key, value in raw.items() if key != "run_id"})
    if stored != run_id or recomputed != run_id:
        raise ValueError(f"shadow record integrity check failed for {path}: stored run_id={stored!r}, recomputed run_id={recomputed!r}, requested run_id={run_id!r}")
    return ShadowComparison.from_dict(raw)
