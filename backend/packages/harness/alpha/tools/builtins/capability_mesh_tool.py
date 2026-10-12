"""The model-facing capability mesh tool.

The mesh is a **measurement and preview plane**. Every read action reports
what the mesh has actually observed, and every planning action returns a plan
that was computed locally from that evidence. **Nothing here dispatches an
agent run, spends budget, changes a live assignment, or resolves a routing
policy** — ``alpha.capability_mesh`` has no executor wiring, so a routing
plan this tool returns is a proposal the caller may read, not work that
happened. Each of those replies says so in its own text, because a plan that
reads like a completed dispatch is the one way this surface could mislead.

What the tool *does* guarantee:

* **Counts travel with their evidence level.** A number nobody can weigh is
  not a fact, so every count is reported beside the level behind it and
  ``unmeasured`` is captioned ``unmeasured`` rather than smoothed into a
  measurement.
* **A store that could not be read says so.** ``PassportStoreError`` /
  ``MemoryStoreError`` / ``ShadowStoreError`` text reaches the model verbatim:
  an empty answer to "how many passports exist?" and a corrupt store drive
  opposite decisions, and only one of them is true.
* **The one write is validated by the mesh.** ``record`` appends through
  ``CapabilityRecord``, which refuses a ``quality`` without a ``profile_version``.
  The tool lets that refusal through rather than downgrading it.
* **Config is resolved off the event loop**, exactly as ``arena_tool`` does:
  reading the config file stats the filesystem, and this function is ``async``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

from langchain.tools import tool

from alpha.capability_mesh.complementarity import PairMemoryStoreError
from alpha.capability_mesh.config import CapabilityMeshConfig, capability_mesh_config
from alpha.capability_mesh.independence import IndependenceStoreError
from alpha.capability_mesh.memory import MemoryStoreError
from alpha.capability_mesh.service import CapabilityMeshService
from alpha.capability_mesh.shadow import ShadowStoreError
from alpha.capability_mesh.store import PassportStoreError
from alpha.capability_mesh.synthesis import ArtifactStoreError
from alpha.config import get_app_config

#: The tool name. Declared here so tests and the manifest generator can
#: reference it without importing the bound tool function.
CAPABILITY_MESH_TOOL_NAME = "capability_mesh"

_ACTIONS = ("passports", "memory", "calibration", "drift", "policy", "route", "team", "compose", "explain", "independence", "shadow", "record")

#: Every durable document the mesh owns can be unreadable. The store's own
#: refusal text reaches the model verbatim: an empty answer to "how many
#: passports exist?" and a corrupt file drive opposite decisions, and only one
#: of them is true.
_STORE_ERRORS = (PassportStoreError, MemoryStoreError, ShadowStoreError, IndependenceStoreError, ArtifactStoreError, PairMemoryStoreError)


@tool(CAPABILITY_MESH_TOOL_NAME, parse_docstring=False)
async def capability_mesh_tool(  # noqa: PLR0912 - one dispatch, one honest answer per action
    runtime,
    action: Literal[
        "passports",
        "memory",
        "calibration",
        "drift",
        "policy",
        "route",
        "team",
        "compose",
        "explain",
        "independence",
        "shadow",
        "record",
    ],
    *,
    entity_id: str = "",
    entity_a: str = "",
    entity_b: str = "",
    capability: str = "",
    task: str = "",
    task_category: str = "",
    task_fingerprint: str = "",
    required_capabilities: str = "",
    required_tools: str = "",
    policy: str = "",
    profile_version: str = "",
    default_agents: int | None = None,
    budget_usd: float | None = None,
    max_team_size: int | None = None,
    calls: int = 0,
    estimated_cost_usd: float | None = None,
    limit: int = 50,
    artifacts_json: str = "",
    outcome: str = "",
    quality: float | None = None,
    confidence: float | None = None,
    passed_hard_gate: bool | None = None,
    latency_ms: int | None = None,
    cost_usd: float | None = None,
    failure_mode: str = "",
    slot: str = "",
    record_id: str = "",
) -> str:
    """Read what Alpha knows about how well its agents, models, tools and skills actually perform, and preview who *would* be assigned.

    The capability mesh keeps evidence-backed capability records — every
    dispatchable configuration Alpha can route to — and answers three
    questions: what has been measured, how confident that measurement is, and
    who a routing plan *would* pick. It exists because "the agent that usually
    handles this" is a guess, and a guess that reads like a fact is worse than
    no answer.

    **Every planning action returns a PREVIEW computed locally. Nothing is
    dispatched**: no agent run is created, no budget is spent, and no live
    assignment changes. There is no executor wiring behind this tool, so a
    routing plan is a proposal for a human or a caller to act on, never work
    that happened. Treat it accordingly.

    How to read the answers:

    * A count is always reported beside the **evidence level** behind it.
      ``unmeasured`` is captioned ``unmeasured`` — it means nobody measured it,
      not that it scored badly. A capability nobody has observed carries an
      *estimated* score derived from a prior, and the fit explanation says so
      on every row.
    * A store that could not be read is reported with the store's own refusal
      reason. An empty list and a corrupt file are different facts and only
      one of them means "nothing there".
    * A missing store is genuinely empty; absence is not corruption.

    Actions:

    * ``passports`` — every capability passport with its evidence level.
    * ``memory`` — recorded outcomes plus the conservative credit report
      (``selected`` counts fully, ``repaired`` and ``materially_edited``
      partially; an unmeasured attempt is still an attempt).
    * ``calibration`` — does stated confidence mean anything? Below the sample
      floor the verdict is *unresolved*, never "well calibrated".
    * ``drift`` — has performance moved? A pure reader: it reports and never
      acts on anything.
    * ``policy`` — the decision-policy resolution table. A table, not
      intelligence: one inspectable row per policy, and the ``local`` row says
      it declares a locality intent the mesh cannot enforce.
    * ``route`` - fingerprint the task, decompose it, and preview the
      PRIMARY/CHALLENGER/VERIFIER plan a policy would produce.
    * ``team`` — preview the small team a task's required capabilities would
      select, with each member's marginal contribution and the reason
      selection stopped.
    * ``compose`` — preview best-per-slot composition over artifacts you
      supply. Incompatible assumptions are never merged into a synthetic
      "best of both": the composed artifact stays absent while any slot
      conflicts, and the alternatives travel with it.
    * ``explain`` — bounded explanation panels for a plan: who was chosen for
      which step and why, plus confidence captions.
    * ``independence`` — are two entities genuinely independent, or do they
      share a model family and provider? ``known: false`` means nothing was
      measured, which is neither independent nor dependent.
    * ``shadow`` — the shadow router's self-evaluation. Predictions only: it
      never calls a model and never spends budget.
    * ``record`` — append one measured outcome. The ONLY write.

    Scope: ``task`` is used for fingerprinting and the decomposition seam;
    ``required_capabilities`` and ``required_tools`` are comma-separated. The
    decomposition is the deterministic heuristic baseline — no model is
    consulted — and says so. ``record`` stores an opaque task fingerprint,
    never task text.
    """
    config = await _resolve_config(runtime)
    if not config.enabled:
        return "Capability mesh is disabled in config.yaml (capability_mesh.enabled: false). It is a read/preview plane: even enabled it dispatches nothing."

    service = CapabilityMeshService(config)
    arguments = _arguments(locals())
    try:
        payload = await asyncio.to_thread(_dispatch, service, action, arguments)
    except ValueError as error:
        return f"Capability mesh refused: {error}"
    except _STORE_ERRORS as error:
        return f"Capability mesh store error: {error}"
    return _render(action, payload)


async def _resolve_config(runtime) -> CapabilityMeshConfig:
    """Resolve the mesh config, never reading the config file on the loop.

    ``runtime.app_config`` is the already-resolved snapshot and costs nothing;
    only the fallback that actually stats the config file needs the thread
    hop, which is the same split ``arena_tool`` uses.
    """
    app_config = getattr(runtime, "app_config", None)
    if app_config is None:
        app_config = await asyncio.to_thread(get_app_config)
    return capability_mesh_config(app_config)


def _arguments(scope: dict[str, Any]) -> dict[str, Any]:
    """The tool's keyword arguments, minus ``runtime``/``action``/``self``.

    Explicit rather than ``locals()`` wholesale: a future local added above
    this line must not silently become a dispatch argument.
    """
    return {name: value for name, value in scope.items() if name in _ARGUMENT_NAMES}


_ARGUMENT_NAMES = frozenset(
    {
        "entity_id",
        "entity_a",
        "entity_b",
        "capability",
        "task",
        "task_category",
        "task_fingerprint",
        "required_capabilities",
        "required_tools",
        "policy",
        "profile_version",
        "default_agents",
        "budget_usd",
        "max_team_size",
        "calls",
        "estimated_cost_usd",
        "limit",
        "artifacts_json",
        "outcome",
        "quality",
        "confidence",
        "passed_hard_gate",
        "latency_ms",
        "cost_usd",
        "failure_mode",
        "slot",
        "record_id",
    }
)


def _dispatch(service: CapabilityMeshService, action: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """One synchronous body per action, executed off the event loop."""
    capabilities = _csv(arguments["required_capabilities"])
    tools = _csv(arguments["required_tools"])

    if action == "passports":
        return service.passports(entity_id=arguments["entity_id"])
    if action == "memory":
        return service.memory_report(entity_id=arguments["entity_id"], capability=arguments["capability"], task_category=arguments["task_category"], limit=arguments["limit"])
    if action == "calibration":
        return service.calibration_report(profile_version=arguments["profile_version"])
    if action == "drift":
        return service.drift_report(profile_version=arguments["profile_version"])
    if action == "policy":
        return service.policy_table(policy=arguments["policy"], default_agents=arguments["default_agents"])
    if action == "route":
        return service.route_preview(task=arguments["task"], policy=arguments["policy"], profile_version=arguments["profile_version"], required_capabilities=capabilities, required_tools=tools, default_agents=arguments["default_agents"])
    if action == "team":
        return service.team_preview(
            task=arguments["task"],
            policy=arguments["policy"],
            profile_version=arguments["profile_version"],
            required_capabilities=capabilities,
            budget_usd=arguments["budget_usd"],
            max_team_size=arguments["max_team_size"],
        )
    if action == "compose":
        return service.compose_preview(task=arguments["task"], artifacts=_artifacts(arguments["artifacts_json"]), policy=arguments["policy"], profile_version=arguments["profile_version"], required_capabilities=capabilities)
    if action == "explain":
        return service.explain_preview(
            task=arguments["task"],
            policy=arguments["policy"],
            profile_version=arguments["profile_version"],
            required_capabilities=capabilities,
            calls=arguments["calls"],
            estimated_cost_usd=arguments["estimated_cost_usd"],
        )
    if action == "independence":
        return service.independence_report(entity_a=arguments["entity_a"], entity_b=arguments["entity_b"], task_category=arguments["task_category"], profile_version=arguments["profile_version"])
    if action == "shadow":
        return service.shadow_report(profile_version=arguments["profile_version"])
    if action == "record":
        return service.record(
            entity_id=arguments["entity_id"],
            capability=arguments["capability"],
            outcome=arguments["outcome"],
            task_fingerprint=arguments["task_fingerprint"],
            task_text=arguments["task"],
            task_category=arguments["task_category"],
            quality=arguments["quality"],
            confidence=arguments["confidence"],
            passed_hard_gate=arguments["passed_hard_gate"],
            latency_ms=arguments["latency_ms"],
            cost_usd=arguments["cost_usd"],
            failure_mode=arguments["failure_mode"],
            slot=arguments["slot"],
            profile_version=arguments["profile_version"],
            record_id=arguments["record_id"],
        )
    raise ValueError(f"unknown capability mesh action '{action}'; expected one of: {', '.join(_ACTIONS)}")


def _csv(value: str) -> tuple[str, ...]:
    """Comma-separated model input -> a deduplicated, ordered tuple."""
    return tuple(dict.fromkeys(part.strip() for part in (value or "").split(",") if part.strip()))


def _artifacts(raw: str) -> list[dict[str, Any]]:
    """Parse the ``compose`` artifact payload.

    A parse failure is a refusal naming the parameter rather than a swallowed
    exception: a caller whose artifacts silently vanished would read the
    resulting "no conflicts" composition as a clean bill of health.
    """
    if not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"artifacts_json must be a JSON list of artifact objects: {error}") from error
    if not isinstance(parsed, list) or not all(isinstance(entry, dict) for entry in parsed):
        raise ValueError("artifacts_json must be a JSON list of artifact objects")
    return parsed


# ------------------------------------------------------------------ rendering


def _render(action: str, payload: dict[str, Any]) -> str:
    """Render one payload as text the model can quote without over-reading it."""
    header = []
    if payload.get("preview"):
        header.append(f"PREVIEW — {payload['preview_note']}")
    if action in ("route", "team", "compose", "explain"):
        header.append("Nothing was dispatched; this is a locally computed plan over stored evidence.")
    body = _body(action, payload)
    notes = [f"- {note}" for note in payload.get("notes", [])]
    sections = ["\n".join(header), "", body]
    if notes:
        sections += ["", "Honesty notes:", *notes]
    return "\n".join(section for section in sections if section)


def _confidence_caption(row: dict[str, Any]) -> str:
    """Re-derive the library's confidence caption from its two inputs.

    ``ConfidenceView.caption`` is derived and deliberately absent from the
    serialised row, so the surface mirrors the same rule instead of inventing
    its own wording — an unmeasured capability must read "unmeasured (0
    samples)" here too, not "well measured".
    """
    from alpha.capability_mesh.explain import CAPTION_UNMEASURED, WELL_MEASURED_MIN_SAMPLES

    level = row["evidence_level"]
    samples = row["sample_count"]
    if level == "unmeasured":
        return f"{CAPTION_UNMEASURED} ({samples} samples)"
    return f"{level}, {samples} sample(s){' (well measured)' if samples >= WELL_MEASURED_MIN_SAMPLES else ''}"


def _body(action: str, payload: dict[str, Any]) -> str:
    if action == "passports":
        if not payload["count"]:
            return f"No passports recorded in {payload['directory']}. Nothing has been measured yet — that is a different fact from a corrupt store."
        lines = [f"Capability passports ({payload['count']}) in {payload['directory']}:"]
        for passport in payload["passports"]:
            evidence = ", ".join(f"{row['capability']}={row['evidence_level']}({row['sample_count']} samples)" for row in passport["capabilities"]) or "no capabilities recorded"
            lines.append(f"  {passport['entity_id']} [{passport['entity_type']}] v{passport['version']} enabled={passport['enabled']}: {evidence}")
        lines.append(f"  Evidence: {payload['evidence']['ranking']}")
        return "\n".join(lines)

    if action == "memory":
        lines = [f"Capability memory: {payload['record_count']} record(s); showing {payload['returned']}" + (" (TRUNCATED — raise 'limit' for more)." if payload["truncated"] else ".")]
        for record in payload["records"]:
            score = "unmeasured" if record["quality"] is None else f"quality={record['quality']}"
            lines.append(f"  {record['record_id']} {record['entity_id']}/{record['capability']} outcome={record['outcome']} {score} profile={record['profile_version'] or '(none)'}")
        for entity, report in payload["credit"].items():
            total = report["rows"]
            lines.append(f"  Credit {entity}: " + (", ".join(f"{row['capability']}={row['credit']:.2f} over {row['attempts']} attempt(s)" for row in total) if total else "no attempts recorded"))
        return "\n".join(lines)

    if action == "calibration":
        verdict = {True: "well calibrated", False: "NOT well calibrated", None: "unresolved"}[payload["well_calibrated"]]
        return "\n".join(
            [
                f"Calibration under profile_version '{payload['profile_version']}':",
                f"  Verdict: {verdict} — {payload['verdict_note']}",
                f"  Comparable pairs: {payload['sample_count']}; excluded (missing confidence or quality): {payload['excluded_count']}",
                f"  Brier score: {payload['brier_score'] if payload['brier_score'] is not None else 'None — nothing was comparable (never a placeholder 0.0)'}",
            ]
        )

    if action == "drift":
        lines = [f"Drift report under profile_version '{payload['profile_version']}' (checked {payload['checked_at']}):"]
        if not payload["signals"]:
            lines.append("  No (entity, capability) stream has enough evidence to compare — unknown is not unchanged.")
        for signal in payload["signals"]:
            lines.append(f"  {signal['entity_id']}/{signal['capability']}: {signal['direction']} — {signal['evidence']}")
        lines.append(f"  Degraded: {', '.join(payload['degraded_entities']) or 'none'}; stale: {', '.join(payload['stale_entities']) or 'none'}")
        lines.append("  This is a pure reader: it reports and acts on nothing.")
        return "\n".join(lines)

    if action == "policy":
        lines = [f"Decision policy table (agent budget {payload['default_agents']}):"]
        for row in payload["policies"]:
            lines.append(f"  {row['policy']}: {row['agent_count']} agent(s), challenger={row['wants_challenger']}, verifier={row['wants_verifier']}, depth={row['verification_depth']}, recency_required={row['recency_required']}")
            lines.append(f"    {row['rationale']}")
        lines.append(f"  Resolved for your call: {payload['resolved']['policy']} → {payload['resolved']['agent_count']} agent(s)")
        lines.append(f"  Declared exploration budget fraction: {payload['exploration_budget_fraction']} ({payload['exploration_note']})")
        return "\n".join(lines)

    if action == "route":
        plan = payload["plan"]
        marks = plan["fingerprint"]
        lines = [
            f"Situation fingerprint (crude keyword baseline, not understanding): complexity={marks['complexity_band']}, risk={marks['risk_band']}, pressure={marks['context_pressure']}, recency_required={marks['recency_required']}",
            f"Policy '{marks['user_policy']}' under profile_version '{payload['profile_version']}':",
        ]
        decomposition = payload["decomposition"]
        lines.append(f"  Decomposer: {decomposition['decomposer']} — {'; '.join(decomposition['notes'][:2])}")
        for microtask in decomposition["microtasks"]:
            lines.append(f"  {microtask['microtask_id']}: {microtask['title']} (accept: {'; '.join(microtask['acceptance_checks'])})")
        if plan["assignments"]:
            lines.append("Assignments that WOULD be made:")
            for assignment in plan["assignments"]:
                lines.append(f"  {assignment['microtask_id']} → {assignment['passport_id']} as {assignment['role']} (fit {assignment['fit_score']:.3f})")
                lines.append(f"    {assignment['score_explanation'][0] if assignment['score_explanation'] else ''}")
        if plan["refusals"]:
            lines.append("Refusals (these microtasks were NOT assigned):")
            for refusal in plan["refusals"]:
                lines.append(f"  {refusal['microtask_id'] or '(plan-level)'}: {'; '.join(refusal['unmet'])}")
        return "\n".join(lines)

    if action == "team":
        plan = payload["plan"]
        lines = ["Team that WOULD be selected for " + (", ".join(payload["required_capabilities"]) or "this task's fingerprinted capabilities") + ":"]
        if not plan["members"]:
            lines.append("  No eligible entity — the selector refuses rather than inventing a team.")
        for member in plan["members"]:
            lines.append(f"  {member['passport_id']} as {member['role']} (fit {member['fit_score']:.3f}, marginal {member['marginal_value']:.3f}{', SPECULATIVE' if member['speculative'] else ''})")
            lines.append(f"    {member['reason']}")
        lines.append(f"  Stopped because: {plan['stops']}")
        lines.append(f"  Uncovered capabilities: {', '.join(plan['uncovered_capabilities']) or 'none'}")
        cost = plan["total_predicted_cost"]
        lines.append(f"  Predicted cost: {'unknown (a member is unpriced)' if cost is None else f'${cost:.4f}'}")
        lines.append(f"  Independence: {plan['independence_note']}")
        return "\n".join(lines)

    if action == "compose":
        composition = payload["composition"]
        lines = ["Composed deliverable (best-per-slot, conflicts never merged):"]
        if composition["composed_artifact"] is None:
            lines.append("  No composed artifact — at least one slot is unresolved or in conflict, and a synthetic 'best of both' is never produced.")
        else:
            lines.append(f"  Composed artifact:\n{composition['composed_artifact']}")
        for slot in composition["slots"]:
            lines.append(f"  slot {slot['slot_id']}: {slot['verification_status']}/{slot['compatibility_status']} — {slot['selected_reason']}")
        for conflict in composition["conflicts"]:
            lines.append(f"  CONFLICT: {conflict}")
        for alternative in composition["alternatives"]:
            lines.append(f"  alternative: {alternative}")
        if composition["unresolved_capabilities"]:
            lines.append(f"  Unresolved capabilities: {', '.join(composition['unresolved_capabilities'])}")
        return "\n".join(lines)

    if action == "explain":
        bundle = payload["bundle"]
        lines = ["Explanation panels (bounded; there is no leaderboard):"]
        for row in bundle["task_map"]:
            lines.append(f"  step {row['microtask_id']}: {row['status']}")
        for row in bundle["specialist_assignments"]:
            lines.append(f"  {row['microtask_id']} → {row['passport_id']} ({row['role']}): {row['reason']}")
        for row in bundle["capability_confidence"]:
            # The caption is derived in the library and deliberately not
            # serialised, so it is re-derived from the two fields it comes
            # from rather than invented here.
            lines.append(f"  confidence {row['entity_id']}/{row['capability']}: {_confidence_caption(row)}")
        for row in bundle["disagreements"]:
            lines.append(f"  disagreement {row['subject']}: {row['resolution']}")
        meter = bundle["resource_meter"]
        cost = "unknown" if meter["estimated_cost_usd"] is None else meter["estimated_cost_usd"]
        remaining = "unknown" if meter["remaining_budget"] is None else meter["remaining_budget"]
        lines.append(f"  Resources: calls={meter['calls']}, estimated cost={cost}, remaining budget={remaining}")
        for note in bundle["honest_notes"]:
            lines.append(f"  note: {note}")
        return "\n".join(lines)

    if action == "independence":
        return "\n".join(
            [
                f"Independence of {payload['entity_a']} vs {payload['entity_b']} on '{payload['task_category']}':",
                f"  Score: {payload['score']} (known={str(payload['known']).lower()}; unknown is neither independent nor dependent)",
                f"  Shared axes: {', '.join(payload['shared_axes']) or 'none observed — an axis nobody observed is never assumed shared'}",
                f"  Basis: {payload['basis']}",
            ]
        )

    if action == "shadow":
        return "\n".join(
            [
                f"Shadow router report under profile_version '{payload['profile_version']}':",
                f"  Comparisons: {len(payload['comparisons'])}; better={payload['better_calls']}, worse={payload['worse_calls']}, undecided={payload['undecided']}",
                f"  Agreement rate: {payload['agreement_rate'] if payload['agreement_rate'] is not None else 'None — no comparisons, so there is no agreement to measure (never 0.0)'}",
                f"  Limits: {payload['dataset_note']}",
            ]
        )

    if action == "record":
        recorded = payload["recorded"]
        score = "unmeasured" if recorded["quality"] is None else recorded["quality"]
        return "\n".join(
            [
                f"Recorded {recorded['record_id']}: {recorded['entity_id']}/{recorded['capability']} outcome={recorded['outcome']} quality={score} profile={recorded['profile_version']}",
                f"  Estimate for this (entity, capability, category) is now {payload['evidence']} — sample_count {payload['estimate_after']['sample_count']}.",
            ]
        )

    return "Unsupported action."


__all__ = [
    "CAPABILITY_MESH_TOOL_NAME",
    "capability_mesh_tool",
]
