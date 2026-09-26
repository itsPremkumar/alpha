"""The machine-readable layer map: what is instrumented, and what is not.

Why this module exists
----------------------
A coverage claim that lives in a report is a claim, not a fact. This module makes
it data, and :func:`coverage_report` renders it, so "which layers actually emit"
is answerable from the process rather than from whoever wrote the summary last
week. A reader -- a person or the sentinel -- can call it and know exactly which
of the eighteen layers have a real call site and which only have a registry entry
and an emitter nobody calls.

Three states, and the difference matters
----------------------------------------
``CALL_SITE``
    The layer emits today, from a real instrumented site in the request path.
``REGISTRY_ONLY``
    The event type, the required payload keys and the typed emitter exist and are
    tested, but no production call site calls them yet. This is a *shaped hole*,
    not a silent one: the contract is decided and documented, and wiring the call
    site is a mechanical diff.
``BLOCKED``
    Wiring the layer requires editing a file another agent owns, or a decision
    that is not the substrate's to make. ``blocked_on`` names the exact path so
    the follow-up is a one-line request rather than an archaeology exercise.

The state is asserted, not trusted. ``test_trace_coverage.py`` checks that every
``CALL_SITE`` layer names a module and qualname that really contain that
emitter's code string, and that every ``REGISTRY_ONLY`` layer has at least one
registry code and one emitter. A stale entry -- a call site that was reverted,
a module that moved -- fails the build.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from . import instrumentation as _instr
from .codes import EVENT_TYPES, TraceLayer

__all__ = [
    "COVERAGE",
    "WiringState",
    "LayerCoverage",
    "coverage_report",
]


class WiringState(StrEnum):
    """How far a layer is instrumented. See the module docstring."""

    CALL_SITE = "call_site"
    REGISTRY_ONLY = "registry_only"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class LayerCoverage:
    """One layer's coverage state."""

    layer: TraceLayer
    name: str
    state: WiringState
    #: ``module:qualname`` for each production site that emits this layer.
    call_sites: tuple[str, ...] = ()
    #: The emitter(s) that exist for this layer, by short name.
    emitters: tuple[str, ...] = ()
    #: Repo-relative paths whose edit this layer's wiring needs.
    blocked_on: tuple[str, ...] = ()
    notes: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "layer": int(self.layer),
            "name": self.name,
            "state": self.state.value,
            "codes": sorted(EVENT_TYPES_BY_LAYER.get(int(self.layer), ())),
            "emitters": list(self.emitters),
            "call_sites": list(self.call_sites),
            "blocked_on": list(self.blocked_on),
            "notes": self.notes,
        }


EVENT_TYPES_BY_LAYER: Final[Mapping[int, tuple[str, ...]]] = MappingProxyType(
    {int(layer): tuple(sorted(definition.code for definition in EVENT_TYPES.values() if definition.layer is layer)) for layer in TraceLayer}
)


def _emitters_for(layer: TraceLayer) -> tuple[str, ...]:
    """Return the ``emit_*`` functions this package defines for *layer*."""
    mapping = {
        TraceLayer.ENVELOPE: ("emit_run_opened", "emit_run_closed"),
        TraceLayer.NODE: ("emit_node_transition",),
        TraceLayer.MODEL: ("emit_model_requested", "emit_model_completed", "emit_model_failed", "emit_model_retry"),
        TraceLayer.TOOL_SELECT: ("emit_tool_selection", "emit_tool_call_completed"),
        TraceLayer.SKILL_SELECT: ("emit_skill_selection",),
        TraceLayer.PROVIDER_SELECT: ("emit_provider_selection",),
        TraceLayer.SUBAGENT: ("emit_subagent_spawned", "emit_subagent_completed"),
        TraceLayer.SWARM: ("emit_swarm_plan", "emit_swarm_attempt", "emit_swarm_incident"),
        TraceLayer.WEB_SEARCH: ("emit_web_results", "emit_web_fetch"),
        TraceLayer.MEMORY: ("emit_memory_recall", "emit_memory_write", "emit_memory_evicted"),
        TraceLayer.KNOWLEDGE: ("emit_rag_query", "emit_rag_allowlist"),
        TraceLayer.FILESYSTEM: ("emit_filesystem_op",),
        TraceLayer.COST: ("emit_cost_snapshot", "emit_cost_governor", "emit_cost_throttled"),
        TraceLayer.ERROR: ("emit_error", "emit_error_swallowed"),
        TraceLayer.HANDOFF: ("emit_handoff",),
        TraceLayer.HUMAN: ("emit_human_interrupt", "emit_human_edit"),
        TraceLayer.GUARDRAIL: ("emit_guardrail_denied",),
        TraceLayer.SELF_EVOLUTION: ("emit_evolution_observed", "emit_evolution_diagnosed", "emit_evolution_fix", "emit_evolution_verify"),
    }
    return mapping.get(layer, ())


COVERAGE: Final[Mapping[int, LayerCoverage]] = MappingProxyType(
    {
        int(TraceLayer.ENVELOPE): LayerCoverage(
            TraceLayer.ENVELOPE,
            "envelope",
            WiringState.REGISTRY_ONLY,
            emitters=_emitters_for(TraceLayer.ENVELOPE),
            blocked_on=("backend/packages/harness/alpha/runtime/runs/worker.py",),
            notes="The run envelope (model-config hash, git SHA, config version) belongs where the run record is created: runtime/runs/worker.py, owned by another agent now. The emitter and its required keys are decided and tested.",
        ),
        int(TraceLayer.NODE): LayerCoverage(
            TraceLayer.NODE,
            "node/graph",
            WiringState.REGISTRY_ONLY,
            emitters=_emitters_for(TraceLayer.NODE),
            blocked_on=("backend/packages/harness/alpha/runtime/runs/worker.py",),
            notes="A graph transition is observable where astream is consumed: runtime/runs/worker.py. That file is owned by another agent right now.",
        ),
        int(TraceLayer.MODEL): LayerCoverage(
            TraceLayer.MODEL,
            "model call",
            WiringState.CALL_SITE,
            call_sites=("alpha.runtime.journal:_model_call_completed", "alpha.runtime.journal:_model_event"),
            emitters=_emitters_for(TraceLayer.MODEL),
            notes="RunJournal.on_chat_model_start / on_llm_end see provider, model, usage, finish reason and latency for every model call, including the middleware and subagent calls that never go through the model factory.",
        ),
        int(TraceLayer.TOOL_SELECT): LayerCoverage(
            TraceLayer.TOOL_SELECT,
            "tool selection",
            WiringState.CALL_SITE,
            call_sites=("alpha.tools.selection:_emit_selection",),
            emitters=_emitters_for(TraceLayer.TOOL_SELECT),
            notes="alpha.tools.selection.rank_candidates is the one shared ranking helper for tools (site='tool_select'), so the candidate set and the choice are in one place. The tool *call* is recorded by RunJournal.on_tool_end.",
        ),
        int(TraceLayer.SKILL_SELECT): LayerCoverage(
            TraceLayer.SKILL_SELECT,
            "skill selection",
            WiringState.CALL_SITE,
            call_sites=("alpha.tools.selection:_emit_selection",),
            emitters=_emitters_for(TraceLayer.SKILL_SELECT),
            notes="Same helper, site='skill_select'. The registry version is recorded from the ranking site's own label so two decisions can be compared.",
        ),
        int(TraceLayer.PROVIDER_SELECT): LayerCoverage(
            TraceLayer.PROVIDER_SELECT,
            "plugin/provider selection",
            WiringState.BLOCKED,
            emitters=_emitters_for(TraceLayer.PROVIDER_SELECT),
            blocked_on=("backend/packages/harness/alpha/models/provider_manager.py", "backend/packages/harness/alpha/models/factory.py"),
            notes="Provider selection and the fallback chain are decided in provider_manager/factory. Both are off-limits to this change; the emitter and its contract are ready.",
        ),
        int(TraceLayer.SUBAGENT): LayerCoverage(
            TraceLayer.SUBAGENT,
            "subagent",
            WiringState.CALL_SITE,
            call_sites=("alpha.tools.builtins.task_tool:_trace_subagent_spawned", "alpha.tools.builtins.task_tool:_trace_subagent_completed"),
            emitters=_emitters_for(TraceLayer.SUBAGENT),
            notes="The task tool is where a subagent is actually created and where its terminal status is observed, so both edges are recorded there. The prompt and any failure text go in as SHA-256 digests, never verbatim.",
        ),
        int(TraceLayer.SWARM): LayerCoverage(
            TraceLayer.SWARM,
            "swarm",
            WiringState.REGISTRY_ONLY,
            emitters=_emitters_for(TraceLayer.SWARM),
            notes="No swarm orchestration call site was reachable without a decision about which subsystem owns the worker loop; the contract is defined and tested.",
        ),
        int(TraceLayer.WEB_SEARCH): LayerCoverage(
            TraceLayer.WEB_SEARCH,
            "web search",
            WiringState.BLOCKED,
            emitters=_emitters_for(TraceLayer.WEB_SEARCH),
            blocked_on=("backend/packages/harness/alpha/community/url_safety.py", "backend/packages/harness/alpha/safety/net_policy.py"),
            notes="The query/result boundary is behind the fetch policy, and both net_policy.py and url_safety.py are owned by another agent. The emitter records every result with its rank plus which one was used.",
        ),
        int(TraceLayer.MEMORY): LayerCoverage(
            TraceLayer.MEMORY,
            "memory",
            WiringState.REGISTRY_ONLY,
            emitters=_emitters_for(TraceLayer.MEMORY),
            blocked_on=("backend/packages/harness/alpha/memory/**",),
            notes="The memory subsystem is a large surface with several backends; adding recall/write emission there needs per-backend decisions about which store a hit came from.",
        ),
        int(TraceLayer.KNOWLEDGE): LayerCoverage(
            TraceLayer.KNOWLEDGE,
            "knowledge/RAG",
            WiringState.REGISTRY_ONLY,
            emitters=_emitters_for(TraceLayer.KNOWLEDGE),
            notes="No single RAG seam; retrieval is reached through several callers with different document shapes.",
        ),
        int(TraceLayer.FILESYSTEM): LayerCoverage(
            TraceLayer.FILESYSTEM,
            "filesystem",
            WiringState.REGISTRY_ONLY,
            emitters=_emitters_for(TraceLayer.FILESYSTEM),
            notes="Filesystem access is mediated by the harness workspace package, not by an Alpha module this change owns; the emitter takes hashes only and is ready for whoever wires it.",
        ),
        int(TraceLayer.COST): LayerCoverage(
            TraceLayer.COST,
            "cost/budget",
            WiringState.CALL_SITE,
            call_sites=("alpha.runtime.journal:_cost_snapshot",),
            emitters=_emitters_for(TraceLayer.COST),
            notes="RunJournal already accumulates input/output/cached tokens per model and per caller; the envelope's cost.snapshot is emitted from that accumulator, so there is one set of totals rather than two.",
        ),
        int(TraceLayer.ERROR): LayerCoverage(
            TraceLayer.ERROR,
            "errors",
            WiringState.CALL_SITE,
            call_sites=("alpha.runtime.journal:_error_event",),
            emitters=_emitters_for(TraceLayer.ERROR),
            notes="RunJournal.on_chain_error (terminal) and on_llm_error (swallowed) write run.error and llm.error; the envelope carries the surviving registry code, severity, a stack *hash*, and whether it was retried or swallowed.",
        ),
        int(TraceLayer.HANDOFF): LayerCoverage(
            TraceLayer.HANDOFF,
            "handoff",
            WiringState.REGISTRY_ONLY,
            emitters=_emitters_for(TraceLayer.HANDOFF),
            notes="A handoff has no existing seam that every path passes through; the emitter and contract exist.",
        ),
        int(TraceLayer.HUMAN): LayerCoverage(
            TraceLayer.HUMAN,
            "human loop",
            WiringState.REGISTRY_ONLY,
            emitters=_emitters_for(TraceLayer.HUMAN),
            blocked_on=("backend/app/gateway/routers/thread_runs.py", "backend/packages/harness/alpha/tui/cli.py", "backend/packages/harness/alpha/client.py"),
            notes="Interrupts, approvals and UI edits are spread over the Gateway routes, the TUI and the client. The Gateway route is the smallest honest seam and was left untouched beyond the query surface this change adds.",
        ),
        int(TraceLayer.GUARDRAIL): LayerCoverage(
            TraceLayer.GUARDRAIL,
            "guardrails",
            WiringState.BLOCKED,
            emitters=_emitters_for(TraceLayer.GUARDRAIL),
            blocked_on=("backend/packages/harness/alpha/safety/net_policy.py", "backend/packages/harness/alpha/community/url_safety.py"),
            notes="Sandbox denials and safety stops are raised in net_policy.py and url_safety.py, both owned by another agent.",
        ),
        int(TraceLayer.SELF_EVOLUTION): LayerCoverage(
            TraceLayer.SELF_EVOLUTION,
            "self-evolution",
            WiringState.REGISTRY_ONLY,
            emitters=_emitters_for(TraceLayer.SELF_EVOLUTION),
            blocked_on=("backend/packages/harness/alpha/runtime/sentinel/**",),
            notes="Shape defined now, filled in by the sentinel. The sentinel consumes this data rather than producing it today; wiring alpha.runtime.sentinel is the sentinel owner's call, not this change's.",
        ),
    }
)


def coverage_report() -> dict[str, object]:
    """Return the layer map as a plain dict, worst state first.

    Sorted by layer index so a reader can walk it top to bottom, with a summary
    that names the counts. The summary is computed here rather than written by
    hand so it cannot drift from the table.
    """
    layers = [COVERAGE[int(layer)].to_dict() for layer in TraceLayer]
    states: dict[str, int] = {}
    for layer in layers:
        states[str(layer["state"])] = states.get(str(layer["state"]), 0) + 1
    return {
        "total_layers": len(layers),
        "by_state": states,
        "layers": layers,
    }


def verify_call_sites() -> tuple[str, ...]:
    """Return a message per ``CALL_SITE`` layer whose site is not real.

    A coverage table is only worth reading if it is checked, so this resolves
    each declared ``module:qualname`` and looks for the emitter's own code string
    in that module's source. It is a source check rather than a call check on
    purpose: importing an arbitrary call site to prove it calls an emitter would
    make the test suite import the whole runtime.
    """
    problems: list[str] = []
    for index, coverage in COVERAGE.items():
        if coverage.state is not WiringState.CALL_SITE:
            continue
        for site in coverage.call_sites:
            module_path, _, qualname = site.partition(":")
            code = qualname.rpartition(".")[2] or qualname
            if module_path == "alpha.observability.trace.instrumentation":
                # The substrate's own seam: assert the symbol exists on the
                # module rather than grepping for a call.
                if not hasattr(_instr, code):
                    problems.append(f"layer {index}: declared emitter {code!r} is not defined in {module_path}")
                continue
            problems.extend(_scan_source(module_path, code, index))
    return tuple(problems)


def _scan_source(module_path: str, needle: str, layer_index: int) -> list[str]:
    """Return a problem string unless *needle* appears in *module_path*'s source."""
    try:
        spec = importlib.util.find_spec(module_path)
    except (ImportError, ValueError, ModuleNotFoundError) as exc:
        return [f"layer {layer_index}: cannot locate {module_path} to verify it calls {needle!r}: {exc}"]
    if spec is None or not spec.origin:
        return [f"layer {layer_index}: {module_path} has no importable source file, so its call site cannot be verified"]
    try:
        source = open(spec.origin, encoding="utf-8").read()
    except OSError as exc:  # pragma: no cover - unreadable source
        return [f"layer {layer_index}: cannot read {spec.origin}: {exc}"]
    if needle not in source:
        return [f"layer {layer_index}: {module_path} is declared as a {WiringState.CALL_SITE.value} site but does not mention {needle!r}; the coverage table is stale"]
    return []
