"""The behaviour-trace taxonomy: 18 layers, one closed event set, one error set.

Why a registry and not an enum
-----------------------------
An enum cannot carry the metadata a consumer actually needs, which is
"which of the 18 layers is this", "what is the minimum payload that makes the
event meaningful", and "what does this correspond to in the pre-existing
vocabularies". So the taxonomy is a table of :class:`EventTypeDefinition`
records and the closed set is *derived* from it, exactly the way
:data:`alpha.observability.events.EVENT_NAMES` is derived from its own
vocabulary. A reader can ask :func:`event_types` and get the whole set; a
writer can ask :func:`definition_for` and get everything it needs to build and
route an event.

One taxonomy, not five
----------------------
An earlier audit found four disjoint error taxonomies describing the same
failures four ways. They were consolidated into
:mod:`alpha.errors.registry`, whose docstring states it is the *only* place a
stable error code is defined. This module therefore **defines no error codes at
all**: :data:`ERROR_CODE_TAXONOMY` names the single source, and
:func:`classify_error_code` validates a caller's code against it.

That validation is **lazy and defensive**, for two reasons that are both real:

1. *Cost.* ``alpha.errors.__init__`` imports ``alpha.errors.report``, which is
   not cheap, and this module sits on the import path of anything that traces.
   The cold-start budget is a gate with a real tolerance and no reason to spend
   part of it on an import this module may not need.
2. *Availability.* A tracing path that raises because a *different* module is
   mid-edit, or missing, or on a reduced deployment, is a tracing path that
   breaks the run it is tracing. :func:`error_taxonomy_status` reports
   ``"available"`` or ``"unavailable"``, and an event recorded while it is
   unavailable carries ``error_code_source="unverified_registry_unavailable"``
   plus the caller's own code verbatim. It is never silently upgraded to a code
   this module made up -- inventing one would be exactly the fifth taxonomy the
   consolidation removed.

Severity is *derived*, never restated
------------------------------------
For a registered code the envelope's ``severity`` is taken from the
:class:`~alpha.errors.registry.ErrorDefinition`, so the same code cannot be
``warning`` in a log and ``critical`` in a trace. The closed
:data:`~alpha.observability.contract.SEVERITIES` vocabulary is the registry's
:class:`~alpha.errors.registry.ErrorSeverity`, and a test asserts the two sets
are equal so the local restatement cannot fork.

Correspondence, not duplication
-------------------------------
:attr:`EventTypeDefinition.spine_equivalent` and
:attr:`EventTypeDefinition.run_event_equivalent` name the pre-existing
vocabulary each event corresponds to. They are **cross-reference data only**:
this module deliberately does not add its names to
:data:`alpha.observability.events.EVENT_NAMES` (whose closed set is asserted by
its own tests) nor to
:data:`alpha.runtime.events.catalog.FIXED_RUN_EVENT_DEFINITIONS` (which is
outside this package's ownership). What *is* unified is the **store**: the
writer fans out into the same sinks as the existing ``event``/``span`` records,
so one trace file carries both vocabularies side by side, and the query layer
can union them using these fields.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final

__all__ = [
    "ERROR_CODE_TAXONOMY",
    "EVENT_TYPE_CODES",
    "EVENT_TYPE_NAMES",
    "LAYERS",
    "EventTypeDefinition",
    "ErrorCodeResolution",
    "ErrorTaxonomyUnavailable",
    "classify_error_code",
    "definition_for",
    "error_taxonomy_status",
    "event_types",
    "layer_names",
    "parse_event_type",
    "registered_error_codes",
]


class ErrorTaxonomyUnavailable(RuntimeError):
    """Raised by :func:`require_error_taxonomy` when the registry cannot load.

    Never raised on the writer's hot path -- :func:`classify_error_code`
    degrades instead. It exists so a *startup* check can be strict, and so a
    test can assert that the degradation is real rather than accidental.
    """


#: The one module in this repository that defines stable error codes. Named as
#: data so a reader (and a test) can assert the tracing layer never invents one.
ERROR_CODE_TAXONOMY: Final[str] = "alpha.errors.registry"

#: The 18 instrumentation layers, in order. The number is part of the event's
#: stable code, so a code alone tells you which layer produced it and an
#: aggregate can be grouped by layer without parsing a dotted name.
LAYERS: Final[tuple[tuple[int, str], ...]] = (
    (0, "envelope"),
    (1, "node"),
    (2, "model"),
    (3, "tool"),
    (4, "skill"),
    (5, "provider"),
    (6, "subagent"),
    (7, "swarm"),
    (8, "search"),
    (9, "memory"),
    (10, "knowledge"),
    (11, "filesystem"),
    (12, "cost"),
    (13, "error"),
    (14, "handoff"),
    (15, "human"),
    (16, "guardrail"),
    (17, "evolution"),
)

#: The one error code every unregistered or unavailable classification falls
#: back to. It is the registry's own total fallback
#: (:data:`alpha.errors.registry.INTERNAL_ERROR` is defined in it), restated as a
#: literal here only so this module can name the fallback without importing the
#: registry; a test asserts the string is a registered code when the registry is
#: importable, so the two cannot drift.
TOTAL_FALLBACK_ERROR_CODE: Final[str] = "INTERNAL_ERROR"


@dataclass(frozen=True, slots=True)
class EventTypeDefinition:
    """One event type's whole published contract."""

    event_type: str
    code: str
    layer: int
    description: str
    #: Payload keys without which the event cannot answer its own question --
    #: e.g. a tool selection that does not say which candidates were considered
    #: is not a selection record, it is a call record. The writer's typed
    #: emitters build these keys, and the generic :meth:`emit` path refuses an
    #: event that is missing one.
    required_fields: tuple[str, ...] = ()
    #: The :class:`alpha.observability.events.TraceEvent` name this corresponds
    #: to, or ``None``. Cross-reference only; see the module docstring.
    spine_equivalent: str | None = None
    #: The :mod:`alpha.runtime.events.catalog` definition this corresponds to, or
    #: ``None``. Same caveat.
    run_event_equivalent: str | None = None
    #: Default severity for an event of this type. A caller may raise it; only
    #: an error-layer event's severity is *derived* from the registry.
    default_severity: str = "info"
    #: Whether the sentinel's repair loop is expected to act on this type. Pure
    #: annotation today -- the field exists so the eventual consumer can select
    #: its inputs from data rather than from a hand-written list.
    actionable: bool = False

    @property
    def layer_name(self) -> str:
        return dict(LAYERS)[self.layer]

    def to_metadata(self) -> dict[str, Any]:
        """The published shape, for an API response or a support bundle."""
        return {
            "event_type": self.event_type,
            "code": self.code,
            "layer": self.layer,
            "layer_name": self.layer_name,
            "description": self.description,
            "required_fields": list(self.required_fields),
            "spine_equivalent": self.spine_equivalent,
            "run_event_equivalent": self.run_event_equivalent,
            "default_severity": self.default_severity,
            "actionable": self.actionable,
        }


def _e(
    event_type: str,
    code: str,
    layer: int,
    description: str,
    *,
    required_fields: tuple[str, ...] = (),
    spine_equivalent: str | None = None,
    run_event_equivalent: str | None = None,
    default_severity: str = "info",
    actionable: bool = False,
) -> EventTypeDefinition:
    return EventTypeDefinition(
        event_type=event_type,
        code=code,
        layer=layer,
        description=description,
        required_fields=required_fields,
        spine_equivalent=spine_equivalent,
        run_event_equivalent=run_event_equivalent,
        default_severity=default_severity,
        actionable=actionable,
    )


# --------------------------------------------------------------------------
# The registry. Append-only: adding a layer, an event or a required field is a
# contract change and belongs in review. Renumbering a layer is NOT -- the
# number is inside every stable code.
# --------------------------------------------------------------------------
_DEFINITIONS: Final[tuple[EventTypeDefinition, ...]] = (
    # -- layer 0: envelope ------------------------------------------------
    _e(
        "run.envelope",
        "ALPHA_TRACE_L00_RUN_ENVELOPE",
        0,
        "Run identity and the build facts a reader needs to interpret every later event: run/trace/parent ids, model-config hash, git SHA, config version.",
        required_fields=("config_version", "model_config_sha256"),
        run_event_equivalent="run.start",
    ),
    # -- layer 1: node / graph --------------------------------------------
    _e(
        "node.transition",
        "ALPHA_TRACE_L01_NODE_TRANSITION",
        1,
        "A graph node transition: from -> to, the state delta, and the chosen next node WITH THE REASON the router chose it.",
        required_fields=("from_node", "to_node", "reason"),
        actionable=True,
    ),
    # -- layer 2: model call ----------------------------------------------
    _e(
        "model.call.requested",
        "ALPHA_TRACE_L02_MODEL_CALL_REQUESTED",
        2,
        "A model call about to be issued: provider, model, temperature and the messages sent (redacted at write time).",
        required_fields=("provider", "model"),
    ),
    _e(
        "model.call.completed",
        "ALPHA_TRACE_L02_MODEL_CALL_COMPLETED",
        2,
        "A model call that returned: reasoning (nullable), token breakdown, cost, finish_reason, TTFB and total latency, retry count, circuit-breaker state.",
        required_fields=("provider", "model", "finish_reason"),
        actionable=True,
    ),
    _e(
        "model.call.failed",
        "ALPHA_TRACE_L02_MODEL_CALL_FAILED",
        2,
        "A model call that raised, after the retries and the fallback chain were exhausted.",
        required_fields=("provider", "model"),
        default_severity="error",
        actionable=True,
    ),
    # -- layer 3: tool selection ------------------------------------------
    _e(
        "tool.selection",
        "ALPHA_TRACE_L03_TOOL_SELECTION",
        3,
        "Which tools were CANDIDATES, which was CHOSEN, and the score and reason behind that choice -- not merely that a call happened.",
        required_fields=("candidate_count", "chosen", "reason"),
        actionable=True,
    ),
    # -- layer 4: skill selection -----------------------------------------
    _e(
        "skill.selection",
        "ALPHA_TRACE_L04_SKILL_SELECTION",
        4,
        "Which skills were CANDIDATES, which was activated, the score and reason, and the skill registry version the decision was made against.",
        required_fields=("candidate_count", "chosen", "reason", "registry_version"),
        spine_equivalent=None,
        actionable=True,
    ),
    # -- layer 5: plugin / provider selection ------------------------------
    _e(
        "provider.selection",
        "ALPHA_TRACE_L05_PROVIDER_SELECTION",
        5,
        "The provider chosen, the fallback chain traversed to reach it, its health, and the circuit-breaker state at decision time.",
        required_fields=("provider", "reason"),
        actionable=True,
    ),
    # -- layer 6: subagent -------------------------------------------------
    _e(
        "subagent.spawn",
        "ALPHA_TRACE_L06_SUBAGENT_SPAWN",
        6,
        "A subagent was created: spawn reason, parent, assigned model, prompt, depth and the siblings it competes with.",
        required_fields=("spawn_reason", "parent_agent", "depth"),
        spine_equivalent="subagent.spawned",
        run_event_equivalent="subagent.start",
        actionable=True,
    ),
    _e(
        "subagent.lifecycle",
        "ALPHA_TRACE_L06_SUBAGENT_LIFECYCLE",
        6,
        "A subagent lifecycle edge: started, completed or failed, with the terminal status.",
        required_fields=("phase",),
        spine_equivalent="subagent.completed",
        run_event_equivalent="subagent.step",
    ),
    _e(
        "subagent.usage",
        "ALPHA_TRACE_L06_SUBAGENT_USAGE",
        6,
        "A subagent's cumulative token and cost accounting at a lifecycle edge.",
        required_fields=("input_tokens", "output_tokens"),
        run_event_equivalent="subagent.end",
    ),
    # -- layer 7: swarm ----------------------------------------------------
    _e(
        "swarm.plan",
        "ALPHA_TRACE_L07_SWARM_PLAN",
        7,
        "A swarm plan was created or replanned, with its node set and budget snapshot.",
        required_fields=("plan_id", "node_count"),
        actionable=True,
    ),
    _e(
        "swarm.node",
        "ALPHA_TRACE_L07_SWARM_NODE",
        7,
        "One swarm node attempt: node, attempt number, worker, incident, and the budget snapshot at the time.",
        required_fields=("plan_id", "node_id", "attempt"),
        actionable=True,
    ),
    # -- layer 8: web search ----------------------------------------------
    _e(
        "search.query",
        "ALPHA_TRACE_L08_SEARCH_QUERY",
        8,
        "A web search was issued: the query and the provider that served it.",
        required_fields=("query", "provider"),
    ),
    _e(
        "search.results",
        "ALPHA_TRACE_L08_SEARCH_RESULTS",
        8,
        "Every result returned (url/title/snippet/rank), which one was subsequently USED, the fetch/extract outcome, and a content hash.",
        required_fields=("query", "result_count", "used_rank"),
        actionable=True,
    ),
    # -- layer 9: memory ---------------------------------------------------
    _e(
        "memory.recall",
        "ALPHA_TRACE_L09_MEMORY_RECALL",
        9,
        "A memory recall: the query, and every hit with its score.",
        required_fields=("query", "hit_count"),
        spine_equivalent="memory.recall",
    ),
    _e(
        "memory.write",
        "ALPHA_TRACE_L09_MEMORY_WRITE",
        9,
        "A memory write: what kind of memory, and under which key/scope. Never the stored content verbatim beyond the redacted payload.",
        required_fields=("memory_kind",),
        spine_equivalent="memory.capture",
    ),
    _e(
        "memory.evict",
        "ALPHA_TRACE_L09_MEMORY_EVICT",
        9,
        "A memory entry was evicted, and why.",
        required_fields=("memory_kind", "reason"),
        actionable=True,
    ),
    # -- layer 10: knowledge / RAG ----------------------------------------
    _e(
        "knowledge.query",
        "ALPHA_TRACE_L10_KNOWLEDGE_QUERY",
        10,
        "A retrieval over the knowledge store: query, documents and scores, the digest of the assembled context, and the allowlist decision.",
        required_fields=("query", "document_count", "allowlist_decision"),
        actionable=True,
    ),
    # -- layer 11: filesystem ----------------------------------------------
    _e(
        "fs.operation",
        "ALPHA_TRACE_L11_FS_OPERATION",
        11,
        "A filesystem operation: path, operation, byte count, and the content hash before and after. Hash only -- never file content.",
        required_fields=("operation", "path"),
    ),
    # -- layer 12: cost / budget -------------------------------------------
    _e(
        "budget.check",
        "ALPHA_TRACE_L12_BUDGET_CHECK",
        12,
        "A budget governor decision, with the running totals it was taken against.",
        required_fields=("decision",),
        spine_equivalent="budget.consumed",
    ),
    _e(
        "budget.throttle",
        "ALPHA_TRACE_L12_BUDGET_THROTTLE",
        12,
        "The governor throttled the run: which budget, and by how much.",
        required_fields=("budget_kind", "action"),
        default_severity="warning",
        actionable=True,
    ),
    # -- layer 13: errors --------------------------------------------------
    _e(
        "error.observed",
        "ALPHA_TRACE_L13_ERROR_OBSERVED",
        13,
        "A failure was observed: the code from the one taxonomy, its severity, the message, a stack hash, and whether it was retried, SWALLOWED or escalated.",
        required_fields=("error_code",),
        default_severity="error",
        actionable=True,
    ),
    # -- layer 14: handoff -------------------------------------------------
    _e(
        "handoff.delegated",
        "ALPHA_TRACE_L14_HANDOFF_DELEGATED",
        14,
        "Control was handed from one actor to another: from, to, reason and attempt number.",
        required_fields=("from_agent", "to_agent", "reason", "attempt"),
    ),
    # -- layer 15: human loop ----------------------------------------------
    _e(
        "human.interrupt",
        "ALPHA_TRACE_L15_HUMAN_INTERRUPT",
        15,
        "The run was interrupted for a human, and (optionally) resumed.",
        required_fields=("phase",),
    ),
    _e(
        "human.approval",
        "ALPHA_TRACE_L15_HUMAN_APPROVAL",
        15,
        "A human approved or denied an action that required approval.",
        required_fields=("decision",),
        actionable=True,
    ),
    _e(
        "human.action",
        "ALPHA_TRACE_L15_HUMAN_ACTION",
        15,
        "A UI action or a manual edit performed by a human during the run.",
        required_fields=("action",),
    ),
    # -- layer 16: guardrails ----------------------------------------------
    _e(
        "guardrail.denied",
        "ALPHA_TRACE_L16_GUARDRAIL_DENIED",
        16,
        "A guardrail stopped the run: a sandbox denial, a safety stop or a budget stop.",
        required_fields=("guardrail", "reason"),
        default_severity="warning",
        spine_equivalent="gate.refused",
        actionable=True,
    ),
    # -- layer 17: self-evolution ------------------------------------------
    _e(
        "evolution.observed",
        "ALPHA_TRACE_L17_EVOLUTION_OBSERVED",
        17,
        "The sentinel observed a symptom in this trace: what it noticed, and the events that evidence it.",
        required_fields=("observation",),
        actionable=True,
    ),
    _e(
        "evolution.diagnosed",
        "ALPHA_TRACE_L17_EVOLUTION_DIAGNOSED",
        17,
        "The sentinel formed a diagnosis from observations: the cause it believes, and its confidence.",
        required_fields=("diagnosis",),
        actionable=True,
    ),
    _e(
        "evolution.fix",
        "ALPHA_TRACE_L17_EVOLUTION_FIX",
        17,
        "A repair was attempted: what was changed and where.",
        required_fields=("fix",),
        actionable=True,
    ),
    _e(
        "evolution.verified",
        "ALPHA_TRACE_L17_EVOLUTION_VERIFIED",
        17,
        "The outcome of verifying an attempted fix. This is the event that closes the self-improving loop.",
        required_fields=("outcome",),
        actionable=True,
    ),
)

_BY_NAME: Final[Mapping[str, EventTypeDefinition]] = MappingProxyType({item.event_type: item for item in _DEFINITIONS})
_BY_CODE: Final[Mapping[str, EventTypeDefinition]] = MappingProxyType({item.code: item for item in _DEFINITIONS})

#: The closed event-type set, derived from the registry rather than restated.
EVENT_TYPE_NAMES: Final[frozenset[str]] = frozenset(_BY_NAME)
#: The closed stable-code set, derived the same way.
EVENT_TYPE_CODES: Final[frozenset[str]] = frozenset(_BY_CODE)


def event_types() -> tuple[str, ...]:
    """Return every event type, sorted. The closed set, as a tuple."""
    return tuple(sorted(EVENT_TYPE_NAMES))


def layer_names() -> Mapping[int, str]:
    """Return ``layer number -> name`` for the 18 layers."""
    return MappingProxyType(dict(LAYERS))


def definition_for(event_type: str) -> EventTypeDefinition:
    """Return the definition for *event_type*.

    Raises:
        KeyError: for a name outside the closed set. A writer that accepts an
            unknown name is a writer that will store a fact no consumer knows
            how to read, and a typo is indistinguishable from a subsystem that
            never fired.
    """
    definition = _BY_NAME.get(event_type)
    if definition is None:
        raise KeyError(f"unknown behaviour event type {event_type!r}; expected one of {sorted(EVENT_TYPE_NAMES)}")
    return definition


def parse_event_type(event_type: str) -> str:
    """Return *event_type* if known, else raise :class:`KeyError`."""
    return definition_for(event_type).event_type


# --------------------------------------------------------------------------
# Error-code resolution against the single taxonomy
# --------------------------------------------------------------------------

#: Cache for the registry's code set. Populated on first use; ``None`` means
#: "not looked up yet", and a failed lookup is cached too so a broken registry
#: costs one import attempt, not one per event.
_error_codes: frozenset[str] | None = None
_error_lookup_failed = False
_error_lock = threading.Lock()


def error_taxonomy_status() -> str:
    """Return ``"available"`` or ``"unavailable"`` for the error taxonomy.

    ``"unavailable"`` is a first-class, reportable state: it means
    :data:`ERROR_CODE_TAXONOMY` could not be imported, and every error event
    recorded in that state carries an unverified code plus
    ``error_code_source="unverified_registry_unavailable"``.
    """
    global _error_codes, _error_lookup_failed
    with _error_lock:
        if _error_codes is None and not _error_lookup_failed:
            try:
                from alpha.errors.registry import ERROR_CODES as _REGISTRY_CODES  # noqa: PLC0415 - lazy on purpose; see module docstring
            except Exception:  # noqa: BLE001 - any import failure degrades, never propagates
                _error_lookup_failed = True
            else:
                _error_codes = frozenset(_REGISTRY_CODES)
        return "available" if _error_codes is not None else "unavailable"


def registered_error_codes() -> frozenset[str] | None:
    """Return the registry's code set, or ``None`` when it cannot be loaded.

    ``None`` is deliberately not ``frozenset()``: an empty set would say "the
    registry has no codes", which is a different and wrong claim.
    """
    if error_taxonomy_status() != "available":
        return None
    assert _error_codes is not None  # noqa: S101 - narrowed by the status check above
    return _error_codes


def require_error_taxonomy() -> frozenset[str]:
    """Return the registry's code set or raise :class:`ErrorTaxonomyUnavailable`.

    For startup checks and tests, where a hard failure is better than a
    degraded trace. The writer deliberately does not use this.
    """
    codes = registered_error_codes()
    if codes is None:
        raise ErrorTaxonomyUnavailable(f"{ERROR_CODE_TAXONOMY} could not be imported; error codes in behaviour events are unverifiable until it loads")
    return codes


@dataclass(frozen=True, slots=True)
class ErrorCodeResolution:
    """What :func:`classify_error_code` decided about one supplied code."""

    code: str
    source: str
    severity: str | None = None
    retryable: bool | None = None
    recovery: str | None = None

    def as_attributes(self) -> dict[str, Any]:
        """Return the payload fragment recording the decision.

        The source is always recorded. A reader that cannot tell a verified code
        from an unverified one will trust both equally, and that is precisely the
        distinction the consolidation exists to preserve.
        """
        attributes: dict[str, Any] = {"error_code_source": self.source}
        if self.severity is not None:
            attributes["error_severity_registry"] = self.severity
        if self.retryable is not None:
            attributes["error_retryable"] = self.retryable
        if self.recovery is not None:
            attributes["error_recovery"] = self.recovery
        return attributes


def classify_error_code(supplied: str | None) -> ErrorCodeResolution:
    """Resolve *supplied* against the single taxonomy, degrading honestly.

    Three outcomes, and no fourth:

    ``registry``
        The code is registered. ``severity``/``retryable``/``recovery`` come
        from its :class:`~alpha.errors.registry.ErrorDefinition`, so the trace
        cannot disagree with the log about how serious a code is.
    ``registry_fallback``
        The registry loaded but does not know the code. The registry's own total
        fallback is used, so an unregistered code never becomes a new
        vocabulary entry.
    ``unverified_registry_unavailable``
        The registry could not be imported. The supplied code is carried
        **verbatim** and marked unverified. No code is invented.
    """
    if error_taxonomy_status() != "available":
        return ErrorCodeResolution(
            code=str(supplied) if supplied else TOTAL_FALLBACK_ERROR_CODE,
            source="unverified_registry_unavailable",
        )
    from alpha.errors.registry import get_definition as _get_definition  # noqa: PLC0415 - lazy; see module docstring

    definition = _get_definition(str(supplied)) if supplied else None
    if definition is None:
        fallback = _get_definition(TOTAL_FALLBACK_ERROR_CODE)
        return ErrorCodeResolution(
            code=TOTAL_FALLBACK_ERROR_CODE,
            source="registry_fallback",
            severity=fallback.severity.value if fallback else None,
            retryable=fallback.retryable if fallback else None,
            recovery=fallback.recovery.value if fallback else None,
        )
    return ErrorCodeResolution(
        code=definition.code,
        source="registry",
        severity=definition.severity.value,
        retryable=definition.retryable,
        recovery=definition.recovery.value,
    )
