"""WP-D1 observe-only opportunity miner (RSI spec feature #19).

Turns telemetry failures, user feedback and benchmark regressions into
structured :class:`Opportunity` records by exact-string clustering over a
normalized signature (no LLM scoring).  Observe-only: the miner reads signals
and can append records to an append-only JSONL ledger; it never dispatches
work, never creates work items, never mutates the company KPI / strategy
subsystem, and never triggers an RSI cycle.

Honesty contract (implementation plan sections 2.4-#5, 5.6 and 3 WP-D1):

- ``confidence`` is ``measured`` only when a real denominator exists
  (``cluster_hits / population`` computed from recorded inputs); otherwise it
  is the ``0.5`` neutral value with ``confidence_kind="unverified"``.  Fixed
  spec-style confidence numbers are never fabricated.
- ``heuristic`` carries an explicit formula string if and only if
  ``confidence_kind == "heuristic"``.
- A cluster with no concrete evidence reference is discarded with an honest
  log line (spec section 93 rule 2: never invent a failure without evidence).
- Empty inputs return an honest empty list with a log note.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from alpha.config.runtime_paths import runtime_home

logger = logging.getLogger(__name__)

OPPORTUNITY_CATEGORIES: tuple[str, ...] = ("reliability", "performance", "capability", "maintenance", "research")
CONFIDENCE_KINDS: tuple[str, ...] = ("measured", "heuristic", "unverified")

# Missing/unknown measurement => 0.5 neutral, disclosed, never a pass.
UNVERIFIED_CONFIDENCE = 0.5

_NEUTRAL_IMPACT = 2.0  # severity-map fallback when severity is unknown (disclosed)
_NEUTRAL_REPRODUCIBILITY = 1.0  # not measured in the observe-only phase (disclosed)
_NEUTRAL_RESOURCE_COST = 1.0  # not measured in the observe-only phase (disclosed)
_NEUTRAL_RISK_PENALTY = 1.0  # not measured in the observe-only phase (disclosed)
_DEFAULT_SEVERITY = "medium"

# Disclosed severity -> impact table used by prioritization().
_SEVERITY_IMPACT: dict[str, float] = {"low": 1.0, "medium": 2.0, "high": 3.0, "critical": 4.0}

_SOURCE_LABELS = {"error": "error-event", "feedback": "feedback", "benchmark": "benchmark-regression"}

# Fixed per-category objectives (defaults, not measurements).
_OBJECTIVES: dict[str, str] = {
    "reliability": "restore reliable operation without duplicate side effects",
    "performance": "restore measured performance to baseline without regressions",
    "capability": "close the reported capability gap without regressing existing behavior",
    "maintenance": "reduce recurring maintenance burden without behavior change",
    "research": "verify the pattern with a concrete evaluator before proposing changes",
}

# Deterministic keyword table used only when feedback carries no explicit category.
_FEEDBACK_CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("reliability", ("crash", "fail", "error", "flaky", "broken", "timeout")),
    ("performance", ("slow", "latency", "sluggish", "speed", "degrad")),
    ("capability", ("missing", "cannot", "can't", "unsupported", "lack", "wish", "feature", "unable")),
    ("maintenance", ("stale", "deprecated", "duplicate", "cleanup", "outdated")),
    ("research", ("unclear", "unknown", "investigate", "unexplained")),
)

_ERROR_MESSAGE_KEYS = ("signature", "message", "error", "description", "detail")
_FEEDBACK_MESSAGE_KEYS = ("signature", "text", "message", "comment", "correction", "description")
_BENCHMARK_MESSAGE_KEYS = ("signature", "detail")

_MAX_TITLE_CHARS = 120


@dataclass
class Opportunity:
    """Structured opportunity record (spec section 8 schema, honest rewrite).

    ``confidence_kind`` is the score method: ``measured`` (real denominator
    from recorded inputs), ``heuristic`` (explicit formula string in
    ``heuristic``), or ``unverified`` (0.5 neutral; ``heuristic`` is None).
    """

    id: str
    category: Literal["reliability", "performance", "capability", "maintenance", "research"]
    title: str
    description: str
    evidence_refs: list[str]
    affected_components: list[str]
    severity: str
    frequency: int
    confidence: float
    confidence_kind: Literal["measured", "heuristic", "unverified"]
    heuristic: str | None
    objective: str
    created_at: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Opportunity:
        category = str(data["category"])
        confidence_kind = str(data["confidence_kind"])
        if category not in OPPORTUNITY_CATEGORIES:
            raise ValueError(f"unknown opportunity category: {category!r}")
        if confidence_kind not in CONFIDENCE_KINDS:
            raise ValueError(f"unknown confidence kind: {confidence_kind!r}")
        heuristic = data["heuristic"]
        if heuristic is not None and not isinstance(heuristic, str):
            raise ValueError("heuristic must be a string or None")
        refs = data["evidence_refs"]
        components = data["affected_components"]
        if not isinstance(refs, list) or not isinstance(components, list):
            raise ValueError("evidence_refs and affected_components must be lists")
        return cls(
            id=str(data["id"]),
            category=category,  # type: ignore[arg-type]
            title=str(data["title"]),
            description=str(data["description"]),
            evidence_refs=[str(ref) for ref in refs],
            affected_components=[str(component) for component in components],
            severity=str(data["severity"]),
            frequency=int(data["frequency"]),
            confidence=float(data["confidence"]),
            confidence_kind=confidence_kind,  # type: ignore[arg-type]
            heuristic=heuristic,
            objective=str(data["objective"]),
            created_at=float(data["created_at"]),
        )


def _normalize_signature(text: str) -> str:
    """Whitespace-collapsed, casefolded text - the exact-string clustering key."""
    return " ".join(text.split()).casefold()


def _first_text(event: dict, keys: Iterable[str]) -> str:
    for key in keys:
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _signature(event: dict, source_kind: str) -> tuple[str, str]:
    """Return (raw title text, normalized signature) for one signal event."""
    if source_kind == "feedback":
        raw = _first_text(event, _FEEDBACK_MESSAGE_KEYS)
    elif source_kind == "benchmark":
        raw = _first_text(event, _BENCHMARK_MESSAGE_KEYS)
        if not raw:
            suite = _first_text(event, ("suite",))
            case = _first_text(event, ("case_id",))
            if suite or case:
                raw = f"{suite}:{case}"
    else:
        raw = _first_text(event, _ERROR_MESSAGE_KEYS)
    return raw, _normalize_signature(raw)


def _evidence_refs(event: dict) -> list[str]:
    """Concrete evidence references carried by one event (deduped, order-preserving)."""
    refs: list[str] = []
    for key in ("evidence_refs", "evidence", "ref"):
        value = event.get(key)
        items = value if isinstance(value, list) else [value]
        for item in items:
            if isinstance(item, str) and item.strip() and item.strip() not in refs:
                refs.append(item.strip())
    return refs


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if int(value) != value or int(value) < 1:
        return None
    return int(value)


def _occurrences(event: dict) -> int:
    """Occurrences this event stands for: an aggregate ``count`` (valid, >= 1) or 1."""
    if "count" in event:
        parsed = _positive_int(event["count"])
        if parsed is not None:
            return parsed
    return 1


def _explicit_total(event: dict) -> int | None:
    """Stated population denominator (``total`` / ``total_events``) if present."""
    for key in ("total", "total_events"):
        parsed = _positive_int(event.get(key))
        if parsed is not None:
            return parsed
    return None


def _denominator(cluster_events: list[dict], source_events: list[dict]) -> int | None:
    """Population for ``cluster_hits / population``, or None when honest math is impossible.

    - an explicit ``total`` counts only when the stated populations agree and
      cover the cluster hits (cluster-level totals, else source-level);
    - a plain list of single-occurrence events is its own population;
    - aggregate ``count`` events without a consistent stated population, or
      conflicting/inconsistent totals, leave no honest denominator -> None,
      which the caller records as 0.5 neutral + ``unverified``.
    """
    hits = sum(_occurrences(event) for event in cluster_events)
    explicit = {total for total in map(_explicit_total, cluster_events) if total is not None}
    if not explicit:
        explicit = {total for total in map(_explicit_total, source_events) if total is not None}
    if explicit:
        if len(explicit) > 1:
            return None
        population = next(iter(explicit))
        return population if population >= hits else None
    if any(_occurrences(event) > 1 for event in source_events):
        return None
    return len(source_events)


def _components(event: dict, source_kind: str) -> list[str]:
    components: list[str] = []
    component = event.get("component")
    if isinstance(component, str) and component.strip():
        components.append(component.strip())
    for key in ("components", "affected_components"):
        value = event.get(key)
        if isinstance(value, list):
            components.extend(str(item).strip() for item in value if str(item).strip())
    if source_kind == "benchmark":
        suite = event.get("suite")
        if isinstance(suite, str) and suite.strip():
            components.append(suite.strip())
    return components


def _category(event: dict, signature: str, source_kind: str) -> str:
    explicit = str(event.get("category") or "").strip().lower()
    if explicit in OPPORTUNITY_CATEGORIES:
        return explicit
    if source_kind == "error":
        return "reliability"
    if source_kind == "benchmark":
        return "performance"
    for candidate, keywords in _FEEDBACK_CATEGORY_KEYWORDS:
        if any(keyword in signature for keyword in keywords):
            return candidate
    return "capability"


def _severity(cluster_events: list[dict]) -> tuple[str, bool]:
    """First stated severity; ``used_default`` is disclosed when no signal exists."""
    for event in cluster_events:
        value = event.get("severity")
        if isinstance(value, str) and value.strip():
            return value.strip().lower(), False
    return _DEFAULT_SEVERITY, True


def _opportunity_id(source_kind: str, category: str, signature: str) -> str:
    """Deterministic content-addressed id so re-mining never duplicates ledger rows."""
    digest = hashlib.sha256(f"{source_kind}\n{category}\n{signature}".encode()).hexdigest()
    return f"opp_{digest[:16]}"


def _build_opportunity(source_kind: str, signature: str, cluster: list[dict], source_events: list[dict]) -> Opportunity | None:
    evidence_refs: list[str] = []
    components: list[str] = []
    for event in cluster:
        for ref in _evidence_refs(event):
            if ref not in evidence_refs:
                evidence_refs.append(ref)
        for component in _components(event, source_kind):
            if component not in components:
                components.append(component)
    if not evidence_refs:
        logger.warning(
            "insufficient evidence — not recorded: %s cluster %r (%d event(s)) carries no concrete evidence_refs",
            source_kind,
            signature,
            len(cluster),
        )
        return None

    raw_title, _ = _signature(cluster[0], source_kind)
    title = " ".join((raw_title or signature).split())
    if len(title) > _MAX_TITLE_CHARS:
        title = title[: _MAX_TITLE_CHARS - 3].rstrip() + "..."

    label = _SOURCE_LABELS[source_kind]
    frequency = sum(_occurrences(event) for event in cluster)
    description = (
        f"{frequency} {label} occurrence(s) share the exact normalized signature {signature!r}; "
        f"cluster formed from {len(cluster)} of {len(source_events)} {label} input(s)."
    )

    denominator = _denominator(cluster, source_events)
    if denominator is not None:
        confidence = round(frequency / denominator, 6)
        confidence_kind: Literal["measured", "heuristic", "unverified"] = "measured"
        description += f" [confidence=measured: {frequency}/{denominator} from recorded inputs]"
    else:
        confidence = UNVERIFIED_CONFIDENCE
        confidence_kind = "unverified"
        description += " [confidence=unverified: no honest denominator in inputs; recorded at 0.5 neutral]"
        logger.warning(
            "mine(): confidence unverified (no honest denominator) for %s cluster %r — recorded at 0.5 neutral",
            source_kind,
            signature,
        )

    category = _category(cluster[0], signature, source_kind)
    severity, used_default = _severity(cluster)
    if used_default:
        description += " [severity=medium default: inputs carried no severity signal]"

    return Opportunity(
        id=_opportunity_id(source_kind, category, signature),
        category=category,  # type: ignore[arg-type]
        title=title,
        description=description,
        evidence_refs=evidence_refs,
        affected_components=components,
        severity=severity,
        frequency=frequency,
        confidence=confidence,
        confidence_kind=confidence_kind,
        heuristic=None,
        objective=_OBJECTIVES[category],
        created_at=time.time(),
    )


def mine(*, error_events: list[dict], feedback: list[dict], benchmark_regressions: list[dict]) -> list[Opportunity]:
    """Cluster signals into observe-only opportunity records.

    Deterministic exact-string clustering on the normalized signature (no LLM
    scoring).  A cluster without at least one concrete evidence reference is
    discarded with an honest log line; empty inputs yield an honest empty
    list.  Side effects: log lines only - nothing is written, dispatched or
    mutated (persistence is ``record_opportunities``).
    """
    sources: tuple[tuple[str, list[dict]], ...] = (
        ("error", error_events or []),
        ("feedback", feedback or []),
        ("benchmark", benchmark_regressions or []),
    )
    if not any(events for _, events in sources):
        logger.warning("mine(): no signals supplied — honest empty opportunity list, nothing recorded")
        return []

    opportunities: list[Opportunity] = []
    for source_kind, events in sources:
        clusters: dict[str, list[dict]] = {}
        for event in events:
            if not isinstance(event, dict):
                logger.warning("mine(): non-dict %s signal skipped — not recorded", type(event).__name__)
                continue
            _, signature = _signature(event, source_kind)
            if not signature:
                logger.warning("mine(): %s signal without usable signature — not recorded", source_kind)
                continue
            clusters.setdefault(signature, []).append(event)
        for signature, cluster in clusters.items():
            opportunity = _build_opportunity(source_kind, signature, cluster, events)
            if opportunity is not None:
                opportunities.append(opportunity)
    return opportunities


def prioritization(opportunity: Opportunity) -> tuple[float, str]:
    """Spec section 8 priority from the record's own inputs; the string discloses the method.

    ``priority = (severity_impact * frequency * evidence_confidence * reproducibility)
    / (resource_cost + risk_penalty)``.  Reproducibility, resource cost and risk
    penalty stay neutral 1.0 until measured; the returned string states the
    score_method (heuristic), every input, and that this is not a measurement.
    """
    impact = _SEVERITY_IMPACT.get(opportunity.severity.casefold(), _NEUTRAL_IMPACT)
    score = round(
        (impact * opportunity.frequency * opportunity.confidence * _NEUTRAL_REPRODUCIBILITY)
        / (_NEUTRAL_RESOURCE_COST + _NEUTRAL_RISK_PENALTY),
        6,
    )
    disclosure = (
        "score_method=heuristic (spec section 8 decomposed-input formula): "
        "priority = (severity_impact * frequency * evidence_confidence * reproducibility) / (resource_cost + risk_penalty); "
        f"severity={opportunity.severity!r} -> impact={impact} via disclosed map {_SEVERITY_IMPACT} "
        f"(unknown severity -> neutral {_NEUTRAL_IMPACT}); "
        f"frequency={opportunity.frequency}; evidence_confidence={opportunity.confidence} (kind={opportunity.confidence_kind}); "
        f"reproducibility={_NEUTRAL_REPRODUCIBILITY} (unmeasured -> neutral); "
        f"resource_cost={_NEUTRAL_RESOURCE_COST} (unmeasured -> neutral); "
        f"risk_penalty={_NEUTRAL_RISK_PENALTY} (unmeasured -> neutral); "
        f"score={score}; heuristic only - not a measurement"
    )
    return score, disclosure


def opportunities_ledger_path() -> Path:
    """Append-only opportunity ledger: ``runtime_home()/rsi/opportunities.jsonl``."""
    return runtime_home() / "rsi" / "opportunities.jsonl"


def _read_ledger_ids(ledger: Path) -> set[str]:
    ids: set[str] = set()
    if not ledger.is_file():
        return ids
    with ledger.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                ids.add(str(json.loads(line)["id"]))
            except (ValueError, KeyError, TypeError):
                continue
    return ids


def record_opportunities(opportunities: Iterable[Opportunity], *, path: Path | None = None) -> dict[str, int]:
    """Append opportunities to the JSONL ledger (one JSON object per line).

    Append-only with id dedupe: a record whose id already exists is skipped, so
    re-mining identical signals never duplicates rows.  Persistence failures
    log a warning and are counted - they never raise (precedent:
    ``EvolutionEngine._record_ledger_event``).  This is the only side effect
    this module can perform: one ledger file, nothing else.
    """
    ledger = path if path is not None else opportunities_ledger_path()
    appended = 0
    skipped = 0
    failed = 0
    try:
        existing_ids = _read_ledger_ids(ledger)
    except OSError as exc:
        logger.warning("Could not read existing opportunity ledger %s: %s", ledger, exc)
        existing_ids = set()
    for opportunity in opportunities:
        if opportunity.id in existing_ids:
            skipped += 1
            logger.info("Opportunity %s already recorded — not re-appended", opportunity.id)
            continue
        try:
            line = json.dumps(opportunity.to_dict(), ensure_ascii=False, separators=(",", ":"))
            ledger.parent.mkdir(parents=True, exist_ok=True)
            with ledger.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except (OSError, TypeError, ValueError) as exc:
            failed += 1
            logger.warning("Could not persist opportunity %s to %s: %s", opportunity.id, ledger, exc)
            continue
        existing_ids.add(opportunity.id)
        appended += 1
    return {"appended": appended, "skipped": skipped, "failed": failed}


def load_opportunities(*, path: Path | None = None) -> list[Opportunity]:
    """Load the ledger, skipping corrupt or unrecognized lines with an honest warning count."""
    ledger = path if path is not None else opportunities_ledger_path()
    records: list[Opportunity] = []
    skipped = 0
    if not ledger.is_file():
        return records
    try:
        with ledger.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(Opportunity.from_dict(json.loads(line)))
                except (ValueError, KeyError, TypeError):
                    skipped += 1
    except OSError as exc:
        logger.warning("Could not read opportunity ledger %s: %s", ledger, exc)
    if skipped:
        logger.warning("Skipped %d corrupt/unrecognized opportunity ledger line(s) in %s", skipped, ledger)
    return records


def signals_from_watchdog(watchdog: Any) -> list[dict]:
    """Read-only bridge: ``DeterministicWatchdog.inspect_anomalies()`` outputs -> error events.

    Only the inspect read is performed (observe-only); no watchdog method that
    records, clears or overrides anything is ever called.
    """
    signals: list[dict] = []
    for report in watchdog.inspect_anomalies():
        details = getattr(report, "details", None)
        details = details if isinstance(details, dict) else {}
        component = details.get("component")
        if not isinstance(component, str) or not component.strip():
            component = str(getattr(report, "worker_id", "") or "")
        anomaly_type = str(getattr(report, "anomaly_type", "") or "")
        signals.append(
            {
                "message": str(getattr(report, "description", "") or ""),
                "severity": str(getattr(report, "severity", "") or ""),
                # high latency is a performance signal; every other anomaly is a reliability signal.
                "category": "performance" if anomaly_type == "high_latency" else "reliability",
                "component": component,
                "ref": f"anomaly:{getattr(report, 'anomaly_id', '') or 'unknown'}",
            }
        )
    return signals


def benchmark_regressions(benchmark_results: Iterable[dict]) -> list[dict]:
    """Filter ``BenchmarkRunner.recent_results()`` dicts to confirmed regressions.

    Honest filter: only ``passed is False`` results qualify; a result that does
    not state ``passed`` cannot confirm a regression and is dropped, never guessed.
    """
    regressions: list[dict] = []
    for result in benchmark_results:
        if not isinstance(result, dict) or result.get("passed") is not False:
            continue
        regressions.append(
            {
                "detail": str(result.get("detail") or ""),
                "suite": str(result.get("suite") or ""),
                "case_id": str(result.get("case_id") or ""),
                "ref": f"benchmark:{result.get('result_id') or 'unknown'}",
            }
        )
    return regressions
