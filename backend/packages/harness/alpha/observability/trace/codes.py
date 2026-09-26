"""The one registry of trace event types, and the one place the four error
taxonomies are reconciled.

Why a registry at all
---------------------
The behaviour-trace substrate is a single source of truth, so "what happened"
must have exactly one spelling. A dotted string invented at a call site is a
taxonomy: it cannot be filtered, cannot be aggregated, cannot be alerted on, and
a typo is indistinguishable from a subsystem that never fired. :data:`EVENT_TYPES`
is the closed table; :func:`require_event_type` refuses anything outside it.

Three deliberate constraints make the table usable rather than merely declared:

1. **Every code fits the durable store.** ``RUN_EVENT_TYPE_MAX_LENGTH`` is 32 and
   ``RUN_EVENT_CATEGORY_MAX_LENGTH`` is 16
   (:mod:`alpha.constants`); :data:`DurableRunEventStore` refuses a longer one.
   A trace code that could not be persisted would be a code that works in a test
   and disappears in production, so the bound is asserted at import time here and
   again in a test.
2. **The layer is data.** Every code names its :class:`TraceLayer`, so "which
   of the eighteen instrumented layers emitted this" is a dict lookup rather than
   a prefix convention, and :data:`LAYER_EVENT_CODES` can prove no layer is empty.
3. **Required payload keys are declared.** A code whose payload omits a key the
   sentinel needs (a model call without ``finish_reason``, a tool selection
   without the chosen tool) is rejected at write time, so an incomplete event
   cannot enter the log and then be diagnosed as a missing implementation.

The four error taxonomies, reconciled here and not extended
-----------------------------------------------------------
:mod:`alpha.errors.registry` states, in its own docstring, that before it four
disjoint taxonomies described the same failures four different ways:

1. run event types (:mod:`alpha.runtime.events.catalog`),
2. the trace event taxonomy (:mod:`alpha.observability.events`),
3. the Gateway auth enum (``app.gateway.auth.errors.AuthErrorCode``),
4. the config self-tuning validation enum
   (:class:`alpha.config.self_tuning.models.ValidationErrorCode`).

This module does **not** add a fifth. It points all four at the surviving
registry, and the proof is :func:`unresolved_error_codes`: it walks the three
legacy *code* vocabularies (1 is a type vocabulary and is mapped as types) and
returns whatever has no surviving counterpart. The test asserts that set is
empty, so a value added to a legacy enum without a mapping fails the build
instead of quietly becoming a fifth taxonomy.

The mapping is data, not logic, for the same reason the surviving registry is:
an alias table can be diffed, reviewed and counted. ``invalid_credentials`` is
an alias of ``AUTH_INVALID_CREDENTIALS`` -- and note *which* legacy auth values
collapse onto it, because ``user_not_found`` collapsing there is deliberate: the
mapping is the anti-enumeration decision, and a reader who later wants a
distinct code has to make that trade explicitly.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from types import MappingProxyType
from typing import Final

__all__ = [
    "DURABLE_CATEGORY",
    "EVENT_CODES",
    "EVENT_TYPES",
    "LAYER_EVENT_CODES",
    "LEGACY_ERROR_CODE_ALIASES",
    "LEGACY_TAXONOMIES",
    "MAX_EVENT_CODE_LENGTH",
    "STATUS_SEVERITY_ALIASES",
    "DURABLE_EVENT_TYPE_ALIASES",
    "EventTypeDef",
    "LegacyTaxonomy",
    "TraceLayer",
    "TraceSeverity",
    "event_type",
    "layer_of",
    "require_event_type",
    "resolve_error_code",
    "unresolved_durable_types",
    "unresolved_error_codes",
    "unresolved_statuses",
]


class TraceSeverity(StrEnum):
    """How loud one trace event is.

    Deliberately the *same four values* as
    :class:`alpha.errors.registry.ErrorSeverity`, re-declared rather than
    imported so that this module stays importable from the runtime package
    without dragging the error fan-out (``alpha.errors.report``) behind it.
    ``test_trace_contract.py`` asserts the value sets are identical, so the two
    cannot drift; a run's loudest event and its loudest error are the same scale.
    """

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class TraceLayer(IntEnum):
    """The eighteen instrumented layers, numbered as the design states them.

    The number is the contract: it is what ``alpha/observability/trace`` uses in
    its coverage report, what the query API groups by, and what a caller
    pins when it only cares about one slice of a run. An ``IntEnum`` rather than
    a ``StrEnum`` because it is an index, not a wire value -- the wire value is
    the event ``code``, and this must not become a second spelling of it.
    """

    ENVELOPE = 0
    NODE = 1
    MODEL = 2
    TOOL_SELECT = 3
    SKILL_SELECT = 4
    PROVIDER_SELECT = 5
    SUBAGENT = 6
    SWARM = 7
    WEB_SEARCH = 8
    MEMORY = 9
    KNOWLEDGE = 10
    FILESYSTEM = 11
    COST = 12
    ERROR = 13
    HANDOFF = 14
    HUMAN = 15
    GUARDRAIL = 16
    SELF_EVOLUTION = 17


#: The category the durable :class:`alpha.runtime.events.store.base.RunEventStore`
#: files a trace event under. ``"trace"`` is already the established category for
#: non-message diagnostic rows (see ``DbRunEventStore._truncate_trace``), so
#: trace rows inherit that store's existing content bound rather than needing a
#: second one.
DURABLE_CATEGORY: Final[str] = "trace"

#: ``alpha.constants.RUN_EVENT_TYPE_MAX_LENGTH``. Duplicated as a literal on
#: purpose: importing ``alpha.constants`` here would put a heavyweight
#: constants module on the trace package's import path, and the bound is a
#: property of the durable store's contract that this table must satisfy. A test
#: asserts the two agree.
MAX_EVENT_CODE_LENGTH: Final[int] = 32


@dataclass(frozen=True, slots=True)
class EventTypeDef:
    """One code's whole published contract.

    ``required_payload`` is enforced by the envelope, not by convention. A
    sentinel that queries for ``model.call.completed`` can then trust
    ``finish_reason`` and ``latency_ms`` to be present, instead of discovering
    the gap when an instrumented call site forgets one.
    """

    code: str
    layer: TraceLayer
    summary: str
    severity: TraceSeverity = TraceSeverity.INFO
    required_payload: tuple[str, ...] = ()
    #: Free-form notes for the reader of the table, not a contract.
    notes: str = field(default="")

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "layer": int(self.layer),
            "summary": self.summary,
            "severity": self.severity.value,
            "required_payload": list(self.required_payload),
        }


def _e(code: str, layer: TraceLayer, summary: str, severity: TraceSeverity = TraceSeverity.INFO, *required: str, notes: str = "") -> EventTypeDef:
    return EventTypeDef(code=code, layer=layer, summary=summary, severity=severity, required_payload=tuple(required), notes=notes)


# ---------------------------------------------------------------------------
# The table. Append-only: never repurpose or renumber a code.
# ---------------------------------------------------------------------------
_DEFINITIONS: Final[tuple[EventTypeDef, ...]] = (
    # -- layer 0: envelope / run identity -----------------------------------
    _e(
        "env.run.opened",
        TraceLayer.ENVELOPE,
        "Run envelope opened: trace, parent span, model-config hash, git SHA, config version.",
        TraceSeverity.INFO,
        "model_config_sha256",
        "config_version",
        notes="model_config_sha256 is a hash of the resolved model config, not of a secret; git_sha256 is 'unknown' outside a checkout and says so.",
    ),
    _e("env.run.closed", TraceLayer.ENVELOPE, "Run envelope closed with a terminal status.", TraceSeverity.INFO, "outcome"),
    # -- layer 1: node / graph transition ------------------------------------
    _e("node.transition", TraceLayer.NODE, "Graph node transition with the state delta either side and the chosen next node with its reason.", TraceSeverity.INFO, "from_node", "to_node", "reason"),
    # -- layer 2: model call ---------------------------------------------------
    _e("model.call.requested", TraceLayer.MODEL, "A model call is about to be issued.", TraceSeverity.INFO, "provider", "model"),
    _e("model.call.completed", TraceLayer.MODEL, "A model call returned. `reasoning` is null for providers that report none.", TraceSeverity.INFO, "provider", "model", "finish_reason", "latency_ms"),
    _e("model.call.failed", TraceLayer.MODEL, "A model call failed after its retries.", TraceSeverity.ERROR, "provider", "model", "retry_count", "breaker_state"),
    _e("model.call.retry", TraceLayer.MODEL, "A model call is being retried, with the reason.", TraceSeverity.WARNING, "provider", "model", "attempt", "reason"),
    # -- layer 3: tool selection ----------------------------------------------
    _e("tool.select.decided", TraceLayer.TOOL_SELECT, "Tool selection: the candidate set, the chosen tool, and the score/reason for the choice.", TraceSeverity.INFO, "candidates", "chosen", "reason"),
    _e("tool.call.completed", TraceLayer.TOOL_SELECT, "A tool call finished. Args and result are redacted at write time and hash-only where noted.", TraceSeverity.INFO, "tool", "status"),
    # -- layer 4: skill selection ---------------------------------------------
    _e(
        "skill.select.decided",
        TraceLayer.SKILL_SELECT,
        "Skill selection: candidate set, chosen skill, score/reason, and the registry version the choice was made against.",
        TraceSeverity.INFO,
        "candidates",
        "chosen",
        "reason",
        "registry_version",
    ),
    # -- layer 5: plugin / provider selection ---------------------------------
    _e("provider.select.decided", TraceLayer.PROVIDER_SELECT, "Provider/plugin selection: chosen provider, the fallback chain traversed, health and breaker state.", TraceSeverity.INFO, "provider", "chain", "breaker_state"),
    # -- layer 6: subagent -----------------------------------------------------
    _e("sub.spawned", TraceLayer.SUBAGENT, "A subagent was spawned, with the reason and its parent span.", TraceSeverity.INFO, "reason", "depth"),
    _e("sub.completed", TraceLayer.SUBAGENT, "A subagent finished; carries its tokens, cost, depth and siblings.", TraceSeverity.INFO, "depth", "outcome"),
    # -- layer 7: swarm ---------------------------------------------------------
    _e("swarm.plan", TraceLayer.SWARM, "A swarm plan was formed.", TraceSeverity.INFO, "nodes"),
    _e("swarm.attempt", TraceLayer.SWARM, "One swarm worker attempt, with the worker, attempt number and budget snapshot.", TraceSeverity.INFO, "worker", "attempt", "budget_snapshot"),
    _e("swarm.incident", TraceLayer.SWARM, "A swarm-level incident observed on a node.", TraceSeverity.WARNING, "node", "detail"),
    # -- layer 8: web search ------------------------------------------------------
    _e("web.search.results", TraceLayer.WEB_SEARCH, "Web search results: every result with url/title/snippet/rank and which one was subsequently used.", TraceSeverity.INFO, "query", "provider", "results"),
    _e("web.fetch.attempted", TraceLayer.WEB_SEARCH, "A URL was fetched/extracted, with outcome and content hash. Content itself is never stored.", TraceSeverity.INFO, "url", "content_sha256"),
    # -- layer 9: memory -----------------------------------------------------------
    _e("mem.recall", TraceLayer.MEMORY, "Memory recall: the query, each hit and its score.", TraceSeverity.INFO, "query", "hits"),
    _e("mem.write", TraceLayer.MEMORY, "A memory write.", TraceSeverity.INFO, "kind"),
    _e("mem.evicted", TraceLayer.MEMORY, "A memory eviction.", TraceSeverity.INFO, "reason"),
    # -- layer 10: knowledge / RAG ---------------------------------------------------
    _e("rag.query", TraceLayer.KNOWLEDGE, "A knowledge/RAG query with documents, scores and digest.", TraceSeverity.INFO, "query", "documents"),
    _e("rag.allowlist", TraceLayer.KNOWLEDGE, "An allowlist decision on a retrieved document.", TraceSeverity.WARNING, "decision", "reason"),
    # -- layer 11: filesystem ----------------------------------------------------------
    _e("fs.op", TraceLayer.FILESYSTEM, "A filesystem operation: path, operation, bytes, hash before/after. Content is never stored.", TraceSeverity.INFO, "path", "operation", "content_sha256_after"),
    # -- layer 12: cost / budget ---------------------------------------------------------
    _e("cost.snapshot", TraceLayer.COST, "Running token/cost totals.", TraceSeverity.INFO),
    _e("cost.governor", TraceLayer.COST, "A budget governor decision.", TraceSeverity.WARNING, "decision", "reason"),
    _e("cost.throttled", TraceLayer.COST, "A throttle event fired.", TraceSeverity.WARNING, "reason"),
    # -- layer 13: errors ------------------------------------------------------------------
    # ``severity`` is deliberately NOT a required payload key: the envelope has a
    # top-level severity that defaults to this code's declared value, and a
    # required payload key would make every call site restate it. When a provider
    # or the surviving error registry knows a *different* severity, the emitter
    # puts it in the payload and the envelope prefers it.
    _e("err.raised", TraceLayer.ERROR, "A failure with a code from alpha.errors.registry, its severity, the stack hash, and whether it was retried or swallowed.", TraceSeverity.ERROR, "error_code", "message"),
    _e("err.swallowed", TraceLayer.ERROR, "A caught failure that was not re-raised and not reported anywhere else. `escalated` says whether anything acted on it.", TraceSeverity.WARNING, "error_code", "escalated"),
    # -- layer 14: handoff ---------------------------------------------------------------------
    _e("handoff.done", TraceLayer.HANDOFF, "A handoff from one agent to another, with the reason and attempt number.", TraceSeverity.INFO, "from_agent", "to_agent", "reason", "attempt"),
    # -- layer 15: human loop --------------------------------------------------------------------
    _e("human.interrupt", TraceLayer.HUMAN, "A run was interrupted, or an approval was requested.", TraceSeverity.INFO, "action", "reason"),
    _e("human.edit", TraceLayer.HUMAN, "A human edited the run's state. `before`/`after` are hashes or redacted text, never raw content.", TraceSeverity.WARNING, "target", "after"),
    # -- layer 16: guardrails ----------------------------------------------------------------------
    _e("guard.denied", TraceLayer.GUARDRAIL, "A sandbox denial, safety stop or budget stop.", TraceSeverity.WARNING, "guard", "reason"),
    # -- layer 17: self-evolution -------------------------------------------------------------------------
    _e("evo.observed", TraceLayer.SELF_EVOLUTION, "The sentinel observed a fault in this trace. Shape defined now; the sentinel fills it in.", TraceSeverity.INFO, "fingerprint", "source"),
    _e("evo.diagnosed", TraceLayer.SELF_EVOLUTION, "The sentinel produced a diagnosis for a fingerprint.", TraceSeverity.INFO, "fingerprint", "diagnosis"),
    _e("evo.fix.attempted", TraceLayer.SELF_EVOLUTION, "The sentinel attempted a fix.", TraceSeverity.INFO, "fingerprint", "attempt"),
    _e("evo.verify.outcome", TraceLayer.SELF_EVOLUTION, "The verification result for a fix. A non-`verified` outcome must never be committed.", TraceSeverity.INFO, "fingerprint", "outcome"),
)


def _index() -> dict[str, EventTypeDef]:
    table: dict[str, EventTypeDef] = {}
    for definition in _DEFINITIONS:
        if definition.code in table:
            raise ValueError(f"duplicate trace event code in registry: {definition.code!r}")
        if len(definition.code) > MAX_EVENT_CODE_LENGTH:
            raise ValueError(f"trace event code {definition.code!r} is {len(definition.code)} characters; the durable run event store refuses more than {MAX_EVENT_CODE_LENGTH}, so this code could never be persisted")
        if definition.severity not in _SEVERITIES:
            raise ValueError(f"trace event {definition.code!r} has undeclared severity {definition.severity!r}")
        table[definition.code] = definition
    return table


_SEVERITIES: Final[frozenset[str]] = frozenset(member.value for member in TraceSeverity)

#: The single source of truth. Read it; do not shadow it.
EVENT_TYPES: Final[Mapping[str, EventTypeDef]] = MappingProxyType(_index())

#: The closed code set, for a membership test at a hot call site.
EVENT_CODES: Final[frozenset[str]] = frozenset(EVENT_TYPES)

#: Layer index -> its codes, so "is any layer empty" is answerable and asserted.
LAYER_EVENT_CODES: Final[Mapping[int, tuple[str, ...]]] = MappingProxyType(
    {int(layer): tuple(sorted(d.code for d in EVENT_TYPES.values() if d.layer is layer)) for layer in TraceLayer}
)


class UnknownTraceEventTypeError(ValueError):
    """Raised when an event code is outside :data:`EVENT_CODES`."""


def event_type(code: object) -> EventTypeDef:
    """Return the definition for *code*, or raise.

    The typed entry point an instrumentation call site uses. It raises rather
    than returning ``None`` because a call site that cannot name its own event
    has a bug, and the substrate's job is to make that bug loud here rather than
    invisible in a log nobody reads.
    """
    if isinstance(code, str):
        definition = EVENT_TYPES.get(code)
        if definition is not None:
            return definition
    raise UnknownTraceEventTypeError(f"unknown trace event code {code!r}; expected one of {sorted(EVENT_CODES)}")


def require_event_type(code: object) -> str:
    """Return *code* validated against the registry."""
    return event_type(code).code


def layer_of(code: object) -> TraceLayer:
    """Return the layer that owns *code*."""
    return event_type(code).layer


# ---------------------------------------------------------------------------
# Error-taxonomy reconciliation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LegacyTaxonomy:
    """One of the four disjoint vocabularies this table reconciles.

    ``import_path`` is a string rather than a live import because two of the four
    live in layers this package must not depend on -- ``app.gateway`` is the
    host application's own module, and importing it from ``alpha`` inverts the
    dependency direction. :func:`unresolved_error_codes` resolves the path and
    reads the vocabulary, so the declaration stays inert and the *check* stays
    real.

    ``member_attribute`` is what to read off each member: ``"value"`` for the
    ``StrEnum`` vocabularies, ``"event_type"`` for the durable run-event
    definitions, which are frozen dataclasses and not an enum at all.
    """

    key: str
    import_path: str
    symbol: str
    kind: str
    resolution: str
    member_attribute: str = "value"
    #: Sibling vocabularies in the same module that belong to this same taxonomy.
    #: The config self-tuning *taxonomy* is one vocabulary with four enum classes
    #: (``ValidationErrorCode``, ``ValidationWarningCode``, ``RefusalCode``, and
    #: ``ProvenanceAction``'s failure sibling); listing only one of them would
    #: leave three unreconciled while claiming the taxonomy is closed.
    extra_symbols: tuple[str, ...] = ()
    notes: str = ""

    def _values_of(self, symbol_name: str) -> tuple[str, ...]:
        try:
            module = __import__(self.import_path, fromlist=[symbol_name])
        except Exception:  # noqa: BLE001 - a taxonomy that cannot load has no values to reconcile
            return ()
        symbol = getattr(module, symbol_name, None)
        if symbol is None:
            return ()
        try:
            members = tuple(symbol)
        except TypeError:
            return ()
        return tuple(str(getattr(member, self.member_attribute, member)) for member in members)

    def values(self) -> tuple[str, ...]:
        """Return the live vocabulary's values, or ``()`` when the module is absent.

        Absent means the host application is not on the path (a worker-only
        process, a library import). That is a fact about the deployment, not a
        failure, so the check degrades to "nothing to reconcile here" and says so
        in its return value.
        """
        collected: list[str] = list(self._values_of(self.symbol))
        for extra in self.extra_symbols:
            collected.extend(self._values_of(extra))
        return tuple(collected)


#: The four. Names and paths are exactly the ones
#: :mod:`alpha.errors.registry` lists in its own docstring, so this table is a
#: machine-checkable restatement of that claim rather than a fifth opinion.
LEGACY_TAXONOMIES: Final[tuple[LegacyTaxonomy, ...]] = (
    LegacyTaxonomy(
        key="run_event_type",
        import_path="alpha.runtime.events.catalog",
        symbol="FIXED_RUN_EVENT_DEFINITIONS",
        kind="type",
        resolution="DURABLE_EVENT_TYPE_ALIASES",
        member_attribute="event_type",
        notes="A vocabulary of *types*, not codes: frozen definitions, not an enum. Each durable type is mapped to the trace code carrying the same fact.",
    ),
    LegacyTaxonomy(
        key="trace_event_status",
        import_path="alpha.observability.events",
        symbol="EventStatus",
        kind="status",
        resolution="STATUS_SEVERITY_ALIASES",
        notes="A vocabulary of *statuses*. Each maps onto a severity on the same four-value scale as alpha.errors.registry.ErrorSeverity.",
    ),
    LegacyTaxonomy(
        key="gateway_auth_code",
        import_path="app.gateway.auth.errors",
        symbol="AuthErrorCode",
        kind="code",
        resolution="LEGACY_ERROR_CODE_ALIASES",
        notes="Collapsed onto the surviving registry; see the note on user_not_found in LEGACY_ERROR_CODE_ALIASES.",
    ),
    LegacyTaxonomy(
        key="config_validation_code",
        import_path="alpha.config.self_tuning.models",
        symbol="ValidationErrorCode",
        kind="code",
        resolution="LEGACY_ERROR_CODE_ALIASES",
        extra_symbols=("ValidationWarningCode", "RefusalCode"),
        notes="One taxonomy, three enum classes. All three are reconciled through the same alias table; listing only ValidationErrorCode would leave two unreconciled while claiming closure.",
    ),
)


#: Legacy code -> surviving code in :data:`alpha.errors.registry.ERROR_CODES`.
#:
#: Read the entries that look lossy before changing them:
#:
#: * ``user_not_found`` -> ``AUTH_INVALID_CREDENTIALS`` is the **anti-enumeration**
#:   decision. A login that says "no such user" is a user-enumeration oracle, so
#:   the two legacy values must produce the same surviving code. Re-splitting them
#:   is a security change, not a cleanup.
#: * ``token_expired`` and ``token_invalid`` both land on ``AUTH_REQUIRED``
#:   because the client's correct response is identical for each: authenticate
#:   again. The distinction is a server-side log concern and stays in the message.
#: * the self-tuning validation codes are *configuration* failures. They map onto
#:   ``CONFIG_INVALID``/``INVALID_INPUT``/``DEPENDENCY_UNAVAILABLE`` rather than
#:   onto persistence codes, because what a supervisor must do with them is
#:   different: refuse the change, do not retry the write.
LEGACY_ERROR_CODE_ALIASES: Final[Mapping[str, str]] = MappingProxyType(
    {
        # -- app.gateway.auth.errors.AuthErrorCode -------------------------------
        "invalid_credentials": "AUTH_INVALID_CREDENTIALS",
        "token_expired": "AUTH_REQUIRED",
        "token_invalid": "AUTH_REQUIRED",
        "user_not_found": "AUTH_INVALID_CREDENTIALS",
        "email_already_exists": "PERSISTENCE_CONFLICT",
        "provider_not_found": "CONFIG_INVALID",
        "not_authenticated": "AUTH_REQUIRED",
        "system_already_initialized": "PERSISTENCE_CONFLICT",
        "registration_disabled": "AUTH_FORBIDDEN",
        # -- alpha.config.self_tuning.models.ValidationErrorCode -----------------
        "stale_previous_value": "PERSISTENCE_CONFLICT",
        "invalid_domain": "INVALID_INPUT",
        "outside_bounds": "INVALID_INPUT",
        "owner_schema_invalid": "CONFIG_INVALID",
        "full_config_invalid": "CONFIG_INVALID",
        "not_reversible": "CONFIG_INVALID",
        "rollback_value_mismatch": "PERSISTENCE_CORRUPT",
        "blast_radius_mismatch": "CONFIG_INVALID",
        "validation_unavailable": "DEPENDENCY_UNAVAILABLE",
        # -- alpha.config.self_tuning.models.ValidationWarningCode ---------------
        "close_to_bound": "CONFIG_INVALID",
        "high_blast_radius": "CONFIG_INVALID",
        "scoped_canary_required": "CONFIG_INVALID",
        # -- alpha.config.self_tuning.models.RefusalCode -------------------------
        "undeclared_path": "SECURITY_POLICY_VIOLATION",
        "protected_path": "SECURITY_POLICY_VIOLATION",
        "operator_authorization_required": "AUTH_FORBIDDEN",
        "operator_authorization_unverifiable": "AUTH_FORBIDDEN",
        "operator_authorization_denied": "AUTH_FORBIDDEN",
    }
)

#: The old trace ``EventStatus`` -> a :class:`TraceSeverity`. The two vocabularies
#: are not the same shape: a status describes an *instant* (``refused``,
#: ``skipped``), a severity describes how loud it is. The mapping is the one place
#: that decision is made, so a ``refused`` sandbox denial cannot be filed as
#: ``info`` by one call site and ``warning`` by another.
STATUS_SEVERITY_ALIASES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "ok": TraceSeverity.INFO.value,
        "skipped": TraceSeverity.INFO.value,
        "refused": TraceSeverity.WARNING.value,
        "cancelled": TraceSeverity.WARNING.value,
        "timeout": TraceSeverity.ERROR.value,
        "error": TraceSeverity.ERROR.value,
    }
)

#: Durable run event type -> the trace code that carries the same fact. The
#: durable types are the *published* wire vocabulary the Gateway already serves at
#: ``GET /runs/{id}/events``; the trace codes are the substrate's internal
#: vocabulary. A reader holding only a durable type can find the trace code, and
#: a reader holding a trace code can tell whether the fact has a durable twin.
DURABLE_EVENT_TYPE_ALIASES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "run.start": "env.run.opened",
        "run.end": "env.run.closed",
        "run.error": "err.raised",
        "llm.error": "model.call.failed",
        "llm.ai.response": "model.call.completed",
        "llm.human.input": "env.run.opened",
        "llm.tool.result": "tool.call.completed",
        "context:memory": "mem.recall",
        "workspace_changes": "fs.op",
        "subagent.start": "sub.spawned",
        "subagent.end": "sub.completed",
        "subagent.step": "sub.completed",
    }
)


def resolve_error_code(value: object) -> str:
    """Return the surviving error code for *value*.

    Three cases, in order: already a surviving code, a legacy alias, or a value
    that is neither. The third raises :class:`UnknownTraceErrorCode` rather than
    falling back to ``INTERNAL_ERROR`` -- a silent fallback would erase the
    difference between "this code is unregistered" and "this code is registered
    and means something else", which is the exact confusion the four-way split
    caused. Use :func:`alpha.errors.registry.classify` on the *exception* when
    there is one; this function is for values that already came from somewhere.
    """
    from alpha.errors.registry import is_registered

    if isinstance(value, str) and value:
        if is_registered(value):
            return value
        alias = LEGACY_ERROR_CODE_ALIASES.get(value)
        if alias is not None:
            return alias
    raise UnknownTraceErrorCode(f"error code {value!r} is neither registered in alpha.errors.registry nor declared in LEGACY_ERROR_CODE_ALIASES; add the mapping rather than a fifth taxonomy")


class UnknownTraceErrorCode(ValueError):
    """Raised when an error code has no surviving counterpart."""


def unresolved_error_codes() -> tuple[str, ...]:
    """Return every legacy error value with no surviving counterpart.

    The closure check. ``test_trace_contract.py`` asserts this is empty, so
    adding a member to ``AuthErrorCode`` or ``ValidationErrorCode`` without a
    mapping fails the build rather than producing an error this substrate
    cannot classify -- which is how the fourth taxonomy appeared in the first
    place.

    Returns sorted, deduplicated ``"<taxonomy>:<value>"`` strings so the message
    names the taxonomy that grew.
    """
    missing: set[str] = set()
    for taxonomy in LEGACY_TAXONOMIES:
        if taxonomy.kind != "code":
            continue
        for value in taxonomy.values():
            if value not in LEGACY_ERROR_CODE_ALIASES:
                missing.add(f"{taxonomy.key}:{value}")
    return tuple(sorted(missing))


def unresolved_statuses() -> tuple[str, ...]:
    """Return every legacy trace status with no severity mapping."""
    missing: set[str] = set()
    for taxonomy in LEGACY_TAXONOMIES:
        if taxonomy.kind != "status":
            continue
        for value in taxonomy.values():
            if value not in STATUS_SEVERITY_ALIASES:
                missing.add(f"{taxonomy.key}:{value}")
    return tuple(sorted(missing))


def unresolved_durable_types() -> tuple[str, ...]:
    """Return every durable run event type with no trace code, and vice versa.

    The two-way check. A durable type with no trace code is a fact the behaviour
    trace cannot see; a trace code with no durable type is a fact a reader of the
    existing ``GET /runs/{id}/events`` route cannot reach. Both are gaps, and the
    point of reusing that route is that neither direction has one.
    """
    missing: set[str] = set()
    durable_types: set[str] = set()
    for taxonomy in LEGACY_TAXONOMIES:
        if taxonomy.kind != "type":
            continue
        for value in taxonomy.values():
            durable_types.add(value)
            if value not in DURABLE_EVENT_TYPE_ALIASES:
                missing.add(f"no_trace_code:{value}")
            elif DURABLE_EVENT_TYPE_ALIASES[value] not in EVENT_CODES:
                missing.add(f"unknown_trace_code:{value}->{DURABLE_EVENT_TYPE_ALIASES[value]}")
    for durable_type, code in DURABLE_EVENT_TYPE_ALIASES.items():
        if durable_type not in durable_types:
            missing.add(f"unknown_durable_type:{durable_type}")
    return tuple(sorted(missing))
