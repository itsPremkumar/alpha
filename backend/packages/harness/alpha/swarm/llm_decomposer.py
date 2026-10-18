"""Model-backed swarm decomposition (bot-mode master plan, phase 3.2).

``alpha.swarm.decomposer`` chooses among fixed graph shapes and never spends a
token; this module is the opt-in second source behind ``config.yaml ->
swarm.decomposition`` (default ``template`` — the plan §11.3 cost gate).  One
bounded model call proposes candidate task DAGs in a **strict JSON schema**::

    {"candidates": [[{"id": ..., "objective": ..., "deps": [...],
      "capability_tags": [...], "acceptance_criteria": [...],
      "output_artifacts": [...], "estimated_seconds": ...}, ...], ...]}

Every candidate is validated as a plan before it can win — field-exact specs,
bounds refused with the bound named (never clamped), dependencies that exist,
no cycles, and an artifact without acceptance criteria rejected *at the model
boundary* (the same rule ``spec_lint`` refuses on later, so a malformed model
reply falls back to a template plan instead of a refused swarm).  Survivors
are built and scored by the existing ``planner.build_candidate`` /
``planner.select_best_candidate``, so a winning model plan obeys every
invariant a template plan does: declared capability requirements, a measured
critical path, and ``plan.mode == plan.metrics["strategy"]["mode"]`` by
construction.

Four rules hold, each one a plan §3.2 acceptance criterion:

- **No plan claims ``source="model"`` without a real model call.**  Every
  non-success path returns the caller's template plan untouched with
  ``metrics["decomposition"] = {"source": "template", "reason": ...}`` naming
  exactly what happened (mode not selected, no invoke, budget pre-check,
  raising invoke, invalid reply, no valid candidate, selection failure).
- **The budget pre-check runs *before* the call.**  A declared ``max_tokens``
  ceiling of ``0`` is a real ceiling and blocks paid calls; ``auto``
  additionally refuses to spend without an explicitly declared positive
  ``max_tokens`` budget, while the operator's explicit ``model`` mode is its
  own opt-in.
- **The model call is caller-supplied** — ``invoke(system, user)`` — the same
  seam the acceptance judge uses: ``create_swarm`` is synchronous and the
  Gateway reaches it through ``asyncio.to_thread``, so this module never
  constructs or opens a model call itself.  The ``model`` label is the
  caller's, never read from the model's own output, and ``tokens`` is
  ``None`` when the reply reported no usage — never a fabricated ``0``.
- **An explicit topology mode keeps its template.**  A named mode (``DEBATE``
  and friends) is a requirement the template already honours; model
  decomposition only routes ``SwarmMode.AUTO`` requests.

``auto`` vs ``model``: both attempt the call when an invoke exists; ``auto``
is additionally gated on a declared budget so a heuristic default can never
start spending on its own initiative.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from alpha.swarm.models import SwarmMode, SwarmPlan, SwarmTaskNode
from alpha.swarm.planner import MAX_CANDIDATES, PlanCandidate, build_candidate, select_best_candidate
from alpha.swarm.strategy import StrategyResolution, resolve_strategy
from alpha.swarm.topology import compute_dag_features

logger = logging.getLogger(__name__)

#: The recorded source travels with a basis, the same honesty shape
#: ``GROUNDING_BASIS`` and ``JUDGE_BASIS`` use: a reader knows *which*
#: contract produced the record rather than inferring it from prose.
LLM_DECOMPOSITION_BASIS = "llm_decomposition_v1"
DECOMPOSITION_MODES: tuple[str, ...] = ("auto", "template", "model")
#: The free default.  Plan §11.3's cost gate: a fresh install never spends a
#: token decomposing until the operator opts in.
DEFAULT_DECOMPOSITION_MODE = "template"

#: Model-reply bounds.  Every bound is refused with the bound named, never
#: clamped or silently truncated: a shortened DAG would plan work the model
#: was never asked to schedule.
MAX_MODEL_TASKS = 32
MAX_MODEL_CANDIDATES = MAX_CANDIDATES  # the planner's own candidate ceiling
MAX_TASK_ID_CHARS = 64
MAX_OBJECTIVE_CHARS = 2_000
MAX_GOAL_CHARS = 4_000
MAX_PROMPT_ITEMS = 32
MAX_PROMPT_ITEM_CHARS = 400
MAX_DEPS_PER_TASK = 16
MAX_CAPABILITY_TAGS = 8
MAX_CAPABILITY_TAG_CHARS = 40
MAX_ACCEPTANCE_CRITERIA = 8
MAX_CRITERION_CHARS = 500
MAX_OUTPUT_ARTIFACTS = 8
MAX_ARTIFACT_CHARS = 256
MAX_ESTIMATED_SECONDS = 3_600.0
#: How many rejection reasons one record carries before the omission is stated.
MAX_REJECTION_REASONS = 10
MAX_ERROR_CHARS = 200

#: The exact key set a task object may carry — anything else is a schema
#: break, reported as such rather than dropped.
_SPEC_FIELDS: frozenset[str] = frozenset(
    {
        "id",
        "objective",
        "deps",
        "capability_tags",
        "acceptance_criteria",
        "output_artifacts",
        "estimated_seconds",
    }
)
_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]+\Z")

#: ``invoke(system, user)`` — the same seam the acceptance judge takes.  The
#: return may be a bare string or the provider's message object; usage
#: metadata is read when present and reported as ``None`` when absent.
DecomposeInvoke = Callable[[str, str], Any]


class DecompositionRejected(Exception):
    """A model reply that cannot become a plan.

    ``reason`` is data for ``metrics["decomposition"]``, so it names the
    defect precisely enough for an operator to act on it.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ModelTaskSpec:
    """One validated task from a model candidate.

    Constructed only from a mapping that passed :func:`validate_candidate`'s
    field checks in full — never half-built.
    """

    task_id: str
    objective: str
    deps: tuple[str, ...]
    capability_tags: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    output_artifacts: tuple[str, ...]
    estimated_seconds: float

    def to_task_node(self) -> SwarmTaskNode:
        """Project onto the runtime task model.

        ``deps`` becomes ``dependencies`` (the runtime field name).  Each
        criterion carries an empty ``required_evidence_kinds``: no evidence
        *kind* is mandated, but at least one passing evidence item is still
        required — an empty-kinds criterion is never vacuously met, and the
        goal states the done-state rather than a verifier this layer cannot
        name.
        """

        return SwarmTaskNode(
            task_id=self.task_id,
            objective=self.objective,
            dependencies=list(self.deps),
            capability_tags=list(self.capability_tags),
            acceptance_criteria=[{"id": criterion, "description": criterion, "required_evidence_kinds": []} for criterion in self.acceptance_criteria],
            output_artifacts=list(self.output_artifacts),
            estimated_seconds=self.estimated_seconds,
        )


@dataclass(frozen=True)
class ProductionDecomposeSource:
    """What the production wiring could assemble for one decomposition.

    ``model`` is the configured name and survives a construction failure on
    purpose: the record should say *what was tried*, not hide behind an
    anonymous error.
    """

    invoke: DecomposeInvoke | None = None
    model: str | None = None
    error: str | None = None


def normalize_decomposition_mode(value: Any) -> str:
    """Read any config value into one known mode.

    Unknown reads as the free default: a typo must never become a model call.
    """

    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in DECOMPOSITION_MODES:
            return normalized
    return DEFAULT_DECOMPOSITION_MODE


def budget_precheck(mode: str, budget: Any) -> tuple[bool, str]:
    """Decide whether this mode may spend a token — *before* any call.

    A declared ``max_tokens`` of ``0`` is a real ceiling (paid calls
    blocked), never a missing one, and ``auto`` only spends under a
    declared positive budget so a heuristic default cannot start burning
    tokens on its own initiative.  Returns ``(allowed, reason)``; the reason
    is empty exactly when allowed.
    """

    max_tokens = getattr(budget, "max_tokens", None)
    if isinstance(max_tokens, bool):
        return False, f"declared max_tokens={max_tokens!r} is not an integer budget"
    if max_tokens is not None:
        try:
            ceiling = int(max_tokens)
        except (TypeError, ValueError):
            return False, f"declared max_tokens={max_tokens!r} is not an integer budget"
        if ceiling <= 0:
            return False, f"declared max_tokens={ceiling} is a real ceiling and blocks paid model calls"
        return True, ""
    if mode == "auto":
        return False, "auto decomposition requires an explicitly declared positive max_tokens budget before spending"
    return True, ""


def build_decompose_prompt(goal: str, *, items: Sequence[str] | None = None, max_concurrency: int = 1) -> dict[str, str]:
    """Build the bounded decomposition prompt: contract in the system message,
    the goal in the user message, truncation disclosed rather than silent."""

    system = (
        "You decompose one goal into a dependency-ordered task graph for a multi-agent swarm. "
        "You have no tools and no workspace access; propose the plan only.\n"
        "Reply with ONLY one JSON object, no prose and no code fences:\n"
        '{"candidates": [[<task>, ...], ...]}\n'
        f"- candidates: 1 to {MAX_MODEL_CANDIDATES} alternative task lists; a single list is fine.\n"
        "- Every task object has EXACTLY these fields, no more and no fewer:\n"
        f"  id — string, unique inside its candidate, <={MAX_TASK_ID_CHARS} chars, matching [A-Za-z0-9._-]+\n"
        f"  objective — one clear instruction for a single worker, <={MAX_OBJECTIVE_CHARS} chars\n"
        "  deps — ids of OTHER tasks in the same candidate (may be [])\n"
        f"  capability_tags — <= {MAX_CAPABILITY_TAGS} lowercase_snake work types the task needs (e.g. code_generation, web_search)\n"
        f"  acceptance_criteria — <= {MAX_ACCEPTANCE_CRITERIA} checkable sentences, <={MAX_CRITERION_CHARS} chars each\n"
        f"  output_artifacts — <= {MAX_OUTPUT_ARTIFACTS} workspace file paths the task produces, or [] when it produces none\n"
        f"  estimated_seconds — a number in (0, {MAX_ESTIMATED_SECONDS:.0f}] for one worker\n"
        f"- bounds: {MAX_MODEL_TASKS} tasks per candidate, {MAX_DEPS_PER_TASK} deps per task; the graph must be acyclic "
        "and every dep must exist in the same candidate.\n"
        "- a task that declares output_artifacts MUST declare at least one acceptance_criteria: "
        "an artifact nobody checks is refused.\n"
        "- Do not invent fields, do not repeat ids, and do not add commentary outside the JSON."
    )

    goal_text = _clip(str(goal or ""), MAX_GOAL_CHARS)
    lines = [f"Goal: {goal_text}"]
    if str(goal or "") != goal_text:
        lines.append(f"(goal truncated at {MAX_GOAL_CHARS} characters)")
    if items:
        lines.append("Context items:")
        shown = items[:MAX_PROMPT_ITEMS]
        lines.extend(f"- {_clip(str(item), MAX_PROMPT_ITEM_CHARS)}" for item in shown)
        if len(items) > MAX_PROMPT_ITEMS:
            lines.append(f"({len(items) - MAX_PROMPT_ITEMS} further item(s) omitted at the {MAX_PROMPT_ITEMS}-item bound)")
    lines.append(f"Max concurrency: {max_concurrency}")
    lines.append("Produce the JSON object now.")
    return {"system": system, "user": "\n".join(lines)}


def parse_decomposition_response(text: Any) -> list[list[Mapping[str, Any]]]:
    """Extract the envelope from a model reply and check its shape.

    Field-level validation is :func:`validate_candidate`'s job, so one pass
    reports every problem a candidate has; this function refuses the *reply*
    itself (no JSON, wrong envelope, out-of-bound candidate count) with the
    bound named.
    """

    # Function scope: the judge owns model-reply JSON extraction (one owner,
    # fence- and prose-tolerant), and importing it here keeps the module load
    # free of the judge's dependencies.
    from alpha.swarm.judge import _MAX_RESPONSE_CHARS, _extract_json

    if not isinstance(text, str) or not text.strip():
        raise DecompositionRejected("the model returned an empty reply")
    # The extractor clips at its own reply bound; an oversized reply must say
    # so instead of being re-reported as "not JSON" after the clip.
    if len(text) > _MAX_RESPONSE_CHARS:
        raise DecompositionRejected(f"the model reply exceeded the {_MAX_RESPONSE_CHARS}-character extraction bound")
    payload = _extract_json(text)
    if not isinstance(payload, dict):
        raise DecompositionRejected("the model reply was not a JSON object")
    unexpected = sorted(str(key) for key in payload if key != "candidates")
    if unexpected:
        raise DecompositionRejected(f"unexpected top-level field(s): {', '.join(unexpected)}")
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        raise DecompositionRejected('"candidates" must be a list of task lists')
    if not candidates:
        raise DecompositionRejected("the reply carried no candidates")
    if len(candidates) > MAX_MODEL_CANDIDATES:
        raise DecompositionRejected(f"{len(candidates)} candidates exceeds the bound of {MAX_MODEL_CANDIDATES}")
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, list):
            raise DecompositionRejected(f"candidate {index} is not a list of tasks")
        for task_index, raw in enumerate(candidate):
            if not isinstance(raw, Mapping):
                raise DecompositionRejected(f"candidate {index} task {task_index} is not a task object")
    return candidates


def validate_candidate(raw_candidate: Any) -> tuple[list[ModelTaskSpec], list[str]]:
    """Validate one candidate task list as a plan.

    Returns ``(specs, [])`` when the candidate is fully valid, else
    ``([], reasons)`` — every field-level and DAG-level reason found in one
    pass, so the record can carry them together.
    """

    if not isinstance(raw_candidate, Sequence) or isinstance(raw_candidate, (str, bytes)):
        return [], ["candidate is not a list of tasks"]
    if not raw_candidate:
        return [], ["candidate contains no tasks"]
    if len(raw_candidate) > MAX_MODEL_TASKS:
        return [], [f"{len(raw_candidate)} tasks exceeds the bound of {MAX_MODEL_TASKS}"]

    typed: list[ModelTaskSpec] = []
    reasons: list[str] = []
    for index, raw in enumerate(raw_candidate):
        spec, spec_reasons = _spec_from_mapping(raw, where=f"task {index}")
        reasons.extend(spec_reasons)
        if spec is not None:
            typed.append(spec)
    if reasons:
        return [], reasons

    seen: set[str] = set()
    duplicates: set[str] = set()
    for spec in typed:
        if spec.task_id in seen:
            duplicates.add(spec.task_id)
        seen.add(spec.task_id)
    if duplicates:
        return [], [f"duplicate task id(s): {', '.join(sorted(duplicates))}"]

    for spec in typed:
        unknown = [dep for dep in spec.deps if dep not in seen]
        if unknown:
            reasons.append(f"task {spec.task_id}: unknown dependency id(s): {', '.join(unknown)}")
        if spec.task_id in spec.deps:
            reasons.append(f"task {spec.task_id}: depends on itself")
    if reasons:
        return [], reasons

    cycle = _find_cycle(typed)
    if cycle:
        return [], [f"dependency cycle: {' -> '.join(cycle)}"]
    return typed, []


def build_plan_from_specs(
    goal: str,
    specs: Sequence[ModelTaskSpec],
    *,
    swarm_id: str,
    max_concurrency: int,
    items: Sequence[str] | None = None,
    requires_consensus: bool = False,
) -> tuple[SwarmPlan, StrategyResolution]:
    """Build a plan from validated specs, mirroring the template build.

    The route is measured from this DAG's own shape and the plan is built
    with the mode that measurement returned, so ``plan.mode ==
    plan.metrics["strategy"]["mode"]`` holds by construction — the same
    invariant ``_settle`` exists to guarantee on the template path.  The
    decomposer import is lazy: the decomposer imports this module, so the
    reverse edge must stay inside the function (no module-scope cycle).
    """

    from alpha.swarm.decomposer import SwarmTaskDecomposer

    tasks = {spec.task_id: spec.to_task_node() for spec in specs}
    features = compute_dag_features(tasks)
    resolved = resolve_strategy(
        goal,
        items=items,
        mode=SwarmMode.AUTO,
        features=features,
        requires_consensus=requires_consensus,
        max_concurrency=max_concurrency,
    )
    plan = SwarmPlan(
        swarm_id=swarm_id,
        goal=goal,
        mode=resolved.mode,
        tasks=tasks,
        max_concurrency=max_concurrency,
    )
    SwarmTaskDecomposer._declare_capability_requirements(plan)
    SwarmTaskDecomposer._compute_critical_path_and_speedup(plan)
    return plan, resolved


def source_decomposition(
    goal: str,
    *,
    template_plan: SwarmPlan,
    template_resolution: StrategyResolution,
    mode: Any = DEFAULT_DECOMPOSITION_MODE,
    invoke: DecomposeInvoke | None = None,
    model: str | None = None,
    error: str | None = None,
    items: Sequence[str] | None = None,
    max_concurrency: int = 1,
    swarm_id: str = "swarm",
    budget: Any | None = None,
    requested_mode: SwarmMode = SwarmMode.AUTO,
    requires_consensus: bool = False,
) -> tuple[dict[str, Any], SwarmPlan, StrategyResolution]:
    """Decide where the plan comes from and return it with its record.

    Returns ``(decomposition_record, plan, resolution)``.  On any failure the
    caller's template plan and resolution come back untouched with a
    ``source="template"`` record naming why; on success a model-sourced plan
    whose own measured route chose its mode.  At most one model call is made:
    this function never retries, never invents candidates, and never returns
    ``source="model"`` without having called ``invoke``.
    """

    selected = normalize_decomposition_mode(mode)

    def _fallback(reason: str, *, call: bool = False, tokens: int | None = None, **extra: Any) -> tuple[dict[str, Any], SwarmPlan, StrategyResolution]:
        record: dict[str, Any] = {"source": "template", "reason": reason}
        if model is not None:
            record["model"] = model
        if call:
            # On an attempted call the tokens are measured when the reply
            # reported usage and ``None`` when it could not — never invented.
            record["tokens"] = tokens
        record.update(extra)
        return record, template_plan, template_resolution

    # The cost gate runs first: the default mode never reaches a model seam.
    if selected not in ("model", "auto"):
        return _fallback(f"decomposition_mode={selected}")
    if requested_mode != SwarmMode.AUTO:
        # An explicit topology is a requirement the built template honours;
        # model decomposition only routes AUTO shapes, so this never rewrites
        # what the caller asked for.
        return _fallback(f"requested topology mode '{requested_mode.value}' is honored as given; model decomposition only runs under AUTO")
    if invoke is None:
        return _fallback(error or "model_decomposition_not_configured")
    allowed, precheck_reason = budget_precheck(selected, budget)
    if not allowed:
        return _fallback(f"budget_precheck: {precheck_reason}")

    prompt = build_decompose_prompt(goal, items=items, max_concurrency=max_concurrency)
    try:
        response = invoke(prompt["system"], prompt["user"])
    except Exception as exc:
        # A failing decomposition is a template plan with the reason, never a
        # failed swarm: sourcing the plan must not take the run down with it.
        logger.warning("swarm model decomposition failed for %s: %s", swarm_id, exc, exc_info=True)
        return _fallback(_clip(f"model_decomposition_failed: {type(exc).__name__}: {exc}", 64 + MAX_ERROR_CHARS), call=True)
    tokens = _response_tokens(response)
    try:
        offered = parse_decomposition_response(_response_text(response))
    except DecompositionRejected as exc:
        return _fallback(f"model_decomposition_rejected: {exc.reason}", call=True, tokens=tokens)

    viable: list[tuple[int, list[ModelTaskSpec]]] = []
    rejected: list[str] = []
    for index, raw_candidate in enumerate(offered):
        specs, reasons = validate_candidate(raw_candidate)
        if reasons:
            rejected.append(f"candidate {index}: " + "; ".join(reasons))
        else:
            viable.append((index, specs))
    if not viable:
        return _fallback(
            "model_decomposition_no_valid_candidate",
            call=True,
            tokens=tokens,
            candidates=len(offered),
            valid=0,
            rejected_reasons=_bounded_reasons(rejected),
        )

    scored: list[tuple[int, PlanCandidate, SwarmPlan, StrategyResolution]] = []
    for index, specs in viable:
        try:
            plan, resolution = build_plan_from_specs(
                goal,
                specs,
                swarm_id=swarm_id,
                max_concurrency=max_concurrency,
                items=items,
                requires_consensus=requires_consensus,
            )
            candidate = build_candidate(plan, max_concurrency=max_concurrency)
        except Exception as exc:
            logger.warning("swarm model decomposition candidate %s for %s could not be built: %s", index, swarm_id, exc, exc_info=True)
            rejected.append(f"candidate {index}: {type(exc).__name__}: {exc}")
            continue
        scored.append((index, candidate, plan, resolution))
    if not scored:
        return _fallback(
            "model_decomposition_candidates_could_not_be_built",
            call=True,
            tokens=tokens,
            candidates=len(offered),
            valid=len(viable),
            rejected_reasons=_bounded_reasons(rejected),
        )

    winner = select_best_candidate([entry[1] for entry in scored])
    if winner is None:
        # select_best_candidate returns None only for an empty list, which
        # cannot happen here — the guard names itself anyway, because "the
        # winner vanished" must never be read as a model-sourced plan.
        return _fallback(
            "model_decomposition_selection_returned_no_winner",
            call=True,
            tokens=tokens,
            candidates=len(offered),
            rejected_reasons=_bounded_reasons(rejected),
        )
    chosen_index, _, winner_plan, winner_resolution = next(entry for entry in scored if entry[1] is winner)
    # Same recorded shape as the template candidate path: the losing plans and
    # their scores stay auditable after the fact.  The strategy record lands
    # here too, so the returned plan is complete for any direct caller —
    # `decompose`'s tail rewrites the identical value for every plan.
    winner_plan.metrics["strategy_candidates"] = [entry[1].to_dict() for entry in sorted(scored, key=lambda entry: entry[1].mode.value)]
    winner_plan.metrics["strategy"] = winner_resolution.to_dict()
    record = {
        "source": "model",
        "basis": LLM_DECOMPOSITION_BASIS,
        "model": model,
        "candidates": len(offered),
        "valid": len(viable),
        "chosen": chosen_index,
        "rejected_reasons": _bounded_reasons(rejected),
        "tokens": tokens,
    }
    return record, winner_plan, winner_resolution


def resolve_decomposition_mode() -> str:
    """Read ``config.yaml -> swarm.decomposition`` for one decomposition.

    An unreadable config reads as the free default — a config fault must
    never become a model call.  Called from ``create_swarm`` on the caller's
    thread (off the event loop).
    """

    from alpha.config import get_app_config

    try:
        value = get_app_config().swarm.decomposition
    except Exception:
        return DEFAULT_DECOMPOSITION_MODE
    return normalize_decomposition_mode(value)


def build_production_invoke() -> ProductionDecomposeSource:
    """Assemble the default model seam for the opt-in path.

    Called only when the operator selected ``model``/``auto``; a construction
    failure becomes an honest ``error`` the record carries — never a silent
    slip back to templates that would hide why nothing model-shaped happened.
    Config and factory imports stay here (function scope) so importing this
    module never drags in the model factory.
    """

    from alpha.config import get_app_config
    from alpha.models import create_chat_model

    name: str | None = None
    try:
        app_config = get_app_config()
        candidate = str(app_config.default_model_name or "").strip()
        if not candidate:
            return ProductionDecomposeSource(error="no default model is configured")
        name = candidate
        model = create_chat_model(candidate, app_config=app_config)
    except Exception as exc:
        return ProductionDecomposeSource(model=name, error=_clip(f"{type(exc).__name__}: {exc}", MAX_ERROR_CHARS))

    def invoke(system: str, user: str) -> Any:
        return model.invoke([("system", system), ("human", user)])

    return ProductionDecomposeSource(invoke=invoke, model=name)


# -- Internal helpers ------------------------------------------------------


def _spec_from_mapping(raw: Any, *, where: str) -> tuple[ModelTaskSpec | None, list[str]]:
    """Coerce one raw task mapping, collecting every field-level reason.

    Returns the typed spec only when the mapping is fully valid, else the
    reasons: a task the field check failed cannot be half-trusted.
    """

    reasons: list[str] = []
    keys = set(raw)
    missing = [field for field in sorted(_SPEC_FIELDS) if field not in keys]
    unexpected = sorted(str(key) for key in keys - _SPEC_FIELDS)
    if missing:
        reasons.append(f"missing field(s): {', '.join(missing)}")
    if unexpected:
        reasons.append(f"unexpected field(s): {', '.join(unexpected)}")
    if reasons:
        return None, [f"{where}: {reason}" for reason in reasons]

    task_id = raw["id"]
    if not isinstance(task_id, str) or not task_id:
        reasons.append("id must be a non-empty string")
    elif len(task_id) > MAX_TASK_ID_CHARS:
        reasons.append(f"id exceeds the {MAX_TASK_ID_CHARS}-character bound")
    elif not _ID_PATTERN.match(task_id):
        reasons.append("id may only contain letters, digits, '.', '_' and '-'")

    objective = raw["objective"]
    if not isinstance(objective, str) or not objective.strip():
        reasons.append("objective must be a non-empty string")
    elif len(objective) > MAX_OBJECTIVE_CHARS:
        reasons.append(f"objective exceeds the {MAX_OBJECTIVE_CHARS}-character bound")

    deps = _bounded_string_list(raw["deps"], "deps", MAX_DEPS_PER_TASK, MAX_TASK_ID_CHARS, reasons)
    tags = _bounded_string_list(raw["capability_tags"], "capability_tags", MAX_CAPABILITY_TAGS, MAX_CAPABILITY_TAG_CHARS, reasons)
    criteria = _bounded_string_list(raw["acceptance_criteria"], "acceptance_criteria", MAX_ACCEPTANCE_CRITERIA, MAX_CRITERION_CHARS, reasons)
    artifacts = _bounded_string_list(raw["output_artifacts"], "output_artifacts", MAX_OUTPUT_ARTIFACTS, MAX_ARTIFACT_CHARS, reasons)

    seconds = raw["estimated_seconds"]
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        reasons.append("estimated_seconds must be a number")
    elif not 0 < float(seconds) <= MAX_ESTIMATED_SECONDS:
        reasons.append(f"estimated_seconds must be in (0, {MAX_ESTIMATED_SECONDS:.0f}]")

    if artifacts and not criteria:
        # The same rule spec_lint refuses on later — refused at the model
        # boundary instead, so the reply falls back to a template plan rather
        # than reaching create_swarm as a refused swarm.
        reasons.append("declares output_artifacts without acceptance_criteria (an artifact nobody checks is refused)")

    if reasons:
        return None, [f"{where}: {reason}" for reason in reasons]
    return (
        ModelTaskSpec(
            task_id=task_id,
            objective=objective.strip(),
            deps=tuple(dep.strip() for dep in deps),
            capability_tags=tuple(tag.strip() for tag in tags),
            acceptance_criteria=tuple(criterion.strip() for criterion in criteria),
            output_artifacts=tuple(artifact.strip() for artifact in artifacts),
            estimated_seconds=float(seconds),
        ),
        [],
    )


def _bounded_string_list(value: Any, field: str, max_items: int, max_chars: int, reasons: list[str]) -> list[str]:
    """Validate a list-of-strings field, appending every reason it breaks.

    Bounds are refused with the bound named, never truncated: a silently
    shortened list would plan work the model was never asked about.
    """

    if not isinstance(value, list):
        reasons.append(f"{field} must be a list of strings")
        return []
    if len(value) > max_items:
        reasons.append(f"{field} exceeds the {max_items}-item bound ({len(value)} given)")
    out: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            reasons.append(f"{field}[{index}] must be a non-empty string")
            continue
        if len(item) > max_chars:
            reasons.append(f"{field}[{index}] exceeds the {max_chars}-character bound")
            continue
        out.append(item)
    return out


def _find_cycle(specs: Sequence[ModelTaskSpec]) -> list[str] | None:
    """Return one dependency cycle as an id path, or ``None`` for a DAG.

    Depth is bounded by the task bound, so the recursion is safe by
    construction.  Unknown deps are already refused before this runs.
    """

    graph = {spec.task_id: list(spec.deps) for spec in specs}
    state: dict[str, int] = {}  # 1 = on the current path, 2 = settled
    path: list[str] = []

    def visit(node: str) -> list[str] | None:
        state[node] = 1
        path.append(node)
        for dep in graph.get(node, ()):
            if dep not in graph:
                continue
            if state.get(dep) == 1:
                start = path.index(dep) if dep in path else 0
                return [*path[start:], dep]
            if state.get(dep, 0) == 0:
                found = visit(dep)
                if found is not None:
                    return found
        path.pop()
        state[node] = 2
        return None

    for root in graph:
        if state.get(root, 0) == 0:
            found = visit(root)
            if found is not None:
                return found
    return None


def _response_text(response: Any) -> str:
    """Pull text out of the reply, whatever shape it arrived in.

    Raises :class:`DecompositionRejected` when there is no text — a reply
    that carries none cannot be planned from.
    """

    if isinstance(response, str):
        text: Any = response
    else:
        text = getattr(response, "content", None)
    if isinstance(text, list):
        parts: list[str] = []
        for block in text:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, Mapping) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        text = "".join(parts)
    if not isinstance(text, str) or not text.strip():
        raise DecompositionRejected("the model reply carried no text content")
    return text


def _response_tokens(response: Any) -> int | None:
    """Reported token usage, or ``None`` when the reply reported none.

    Unmeasured is ``None``, never ``0``: a zero would claim the call was
    free, and the point of the record is to know what decomposition cost.
    """

    if isinstance(response, str):
        return None
    candidates: list[Any] = []
    metadata = getattr(response, "response_metadata", None)
    if isinstance(metadata, Mapping):
        candidates.append(metadata.get("token_usage"))
    candidates.append(getattr(response, "usage_metadata", None))
    for usage in candidates:
        if not isinstance(usage, Mapping):
            continue
        total = usage.get("total_tokens")
        if isinstance(total, bool) or not isinstance(total, (int, float)) or total < 0:
            continue
        return int(total)
    return None


def _bounded_reasons(reasons: Sequence[str]) -> list[str]:
    """Bound the rejection list, disclosing the omission rather than hiding it."""

    if len(reasons) <= MAX_REJECTION_REASONS:
        return list(reasons)
    omitted = len(reasons) - MAX_REJECTION_REASONS
    return [*reasons[:MAX_REJECTION_REASONS], f"... and {omitted} further reason(s) omitted at the {MAX_REJECTION_REASONS}-reason bound"]


def _clip(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)] + "…"
