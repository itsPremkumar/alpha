"""Verification gates, ordered so the cheapest check can pre-empt the dearest.

## One rule, enforced in code

**A model gate may raise suspicion. It may never clear a block.**

This is the load-bearing property of the module and it is structural, not a
convention a future edit could quietly reverse: :meth:`GatePipeline.run` reduces
model-layer results to advisory text *before* computing the block. A judge cannot
vote a deterministic failure away, because a judge is the thing most likely to
share the mistake — self-preference, position, and verbosity bias are all
measured, and self-validation specifically has been shown not to improve
adversarial robustness at all.

## Deterministic first, and why the order is not negotiable

Schema, existence, and referential checks are pure stdlib and answer in
microseconds. Running a model gate first means paying for a judgement about
something a set membership test already decided. So gates are ordered by cost,
and :func:`blocked_by` returns the *first* blocker in that order rather than the
first one seen — a later cheap gate must be able to pre-empt an earlier expensive
one.

## Per-step, not per-answer

An end-of-turn check is structurally too late. A deep-research trajectory
averages ~12 semantic spans, and a decision taken wrongly at span 3 has already
propagated through spans 4 to 12 by the time anyone looks at the final answer.
36.9% of *successful* trajectories contain a process error, which is exactly the
population an end-of-turn check reports as fine.

## Side effects get their own gate

Irreversible actions are gated on a corroborated claim, not on the model's
confidence in the sentence that introduced them. The tool named in a plan and the
tool that ran are checked against each other, because "the agent said it would
delete the staging table" is precisely the assertion a hallucinating agent will
make.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from alpha.grounding.models import GateLayer, GateResult, blocked_by

__all__ = [
    "GatePipeline",
    "GateSubject",
    "SideEffectClass",
    "check_claim_authorization",
    "check_output_schema",
    "check_reuse_probe",
    "check_side_effect",
    "check_tool_exists",
]


@dataclass
class GateSubject:
    """Everything a gate may look at for one step.

    One object rather than a function signature per gate, so adding a gate does
    not change every call site and so a gate can be handed the whole step without
    each one re-deriving it.

    Every field is optional and absent means "not declared", never "empty and
    therefore fine" — an undeclared tool name is a gate input a caller forgot,
    and the gates below refuse on it rather than inventing a default.
    """

    #: Tools callable right now. ``None`` means the caller did not resolve them,
    #: which is different from "resolved to none".
    available_tools: frozenset[str] | None = None
    #: Tool names observed running in this step.
    tool_calls: tuple[str, ...] = ()
    #: Named subagents, skills, plugins.
    available_subagents: frozenset[str] | None = None
    available_skills: frozenset[str] | None = None
    #: The structured payload a caller wants shape-checked.
    payload: Mapping[str, Any] | None = None
    #: Required payload keys, optionally as ``name -> type name``.
    required_payload: Mapping[str, str] = field(default_factory=dict)
    #: An enum or closed set the value must come from.
    payload_enum: Mapping[str, frozenset[str]] = field(default_factory=dict)
    #: Claim ids the step depends on. Resolved against ``ledger`` by the caller.
    claim_ids: tuple[str, ...] = ()
    #: The ledger itself, when claim gates are in play.
    ledger: Any | None = None
    #: Tool names the plan said it would call.
    planned_tools: tuple[str, ...] = ()
    #: Did the step search Alpha's own capabilities before producing new work?
    reuse_probe_run: bool = False
    #: Irreversible classes the operator forbids without confirmation.
    forbid_unconfirmed_side_effects: bool = True

    def tools_known(self) -> bool:
        return self.available_tools is not None


class Gate(Protocol):
    """A check that returns a :class:`GateResult`."""

    name: str
    layer: GateLayer

    def __call__(self, subject: GateSubject) -> GateResult: ...


class SideEffectClass(StrEnum):
    """How hard a tool call is to walk back.

    ``SideEffectClass`` lives here rather than in :mod:`alpha.grounding.models`
    because it is only meaningful to a gate; promoting it to the shared vocabulary
    would imply the manifest cares, and it does not.
    """

    READ_ONLY = "read_only"
    #: Local, reversible by re-running.
    REVERSIBLE_WRITE = "reversible_write"
    #: Not reversible: delete, send, pay, publish, push, prod write.
    IRREVERSIBLE = "irreversible"
    #: Unclassified. Treated as irreversible on purpose.
    UNKNOWN = "unknown"


#: Default classification. Deliberately short and conservative: an unlisted tool
#: is :attr:`SideEffectClass.UNKNOWN`, which the gate treats as irreversible, so a
#: tool added to Alpha tomorrow is guarded before anyone has classified it.
DEFAULT_SIDE_EFFECTS: dict[str, SideEffectClass] = {
    "read_file": SideEffectClass.READ_ONLY,
    "grep": SideEffectClass.READ_ONLY,
    "glob": SideEffectClass.READ_ONLY,
    "list_dir": SideEffectClass.READ_ONLY,
    "web_search": SideEffectClass.READ_ONLY,
    "web_fetch": SideEffectClass.READ_ONLY,
    "web_extract": SideEffectClass.READ_ONLY,
    "codebase_search": SideEffectClass.READ_ONLY,
    "write_file": SideEffectClass.REVERSIBLE_WRITE,
    "edit_file": SideEffectClass.REVERSIBLE_WRITE,
    "str_replace": SideEffectClass.REVERSIBLE_WRITE,
    "multi_edit": SideEffectClass.REVERSIBLE_WRITE,
    "execute_command": SideEffectClass.IRREVERSIBLE,
    "bash": SideEffectClass.IRREVERSIBLE,
    "send_email": SideEffectClass.IRREVERSIBLE,
    "delete_file": SideEffectClass.IRREVERSIBLE,
    "git_push": SideEffectClass.IRREVERSIBLE,
}


def check_tool_exists(subject: GateSubject) -> GateResult:
    """Refuse a call to a tool that is not installed.

    Tool-*selection* hallucination — inventing an API, or reaching for a tool this
    deployment does not have — is the most common agent-side error in real
    deployments, and it is exactly what a set membership test catches.
    """
    name = "tool_exists"
    if subject.available_tools is None:
        return GateResult(
            gate=name,
            layer=GateLayer.DETERMINISTIC,
            blocked=True,
            code="toolset_unresolved",
            reason="the available tool set was not resolved for this step",
            remediation="resolve the tool set before dispatching; an unresolved set must not be treated as permissive",
        )
    unknown = sorted({t for t in subject.tool_calls if t not in subject.available_tools})
    if unknown:
        return GateResult(
            gate=name,
            layer=GateLayer.DETERMINISTIC,
            blocked=True,
            code="unknown_tool",
            reason=f"not installed: {', '.join(unknown)}",
            remediation="use a tool from the capability manifest, or escalate rather than substituting an invented one",
            detail={"unknown": unknown, "available_count": len(subject.available_tools)},
        )
    return GateResult.pass_(name, GateLayer.DETERMINISTIC, checked=len(subject.tool_calls))


def check_named_resource_exists(subject: GateSubject) -> GateResult:
    """Refuse a delegation or skill activation naming something absent.

    Same defect class as a missing tool, different registry. Kept as its own gate
    so a caller that resolved tools but not subagents gets an accurate reason
    instead of a confusing "unknown tool".
    """
    results: list[GateResult] = []
    for name, declared, kind, code in (
        ("subagent_exists", subject.available_subagents, "subagent", "unknown_subagent"),
        ("skill_exists", subject.available_skills, "skill", "unknown_skill"),
    ):
        if declared is None:
            continue  # Caller did not resolve this registry; not this gate's business.
        # Nothing names one, so nothing to check.
        if not subject.tool_calls:
            continue
        unknown = sorted({t for t in subject.tool_calls if t.startswith(f"{kind}:") and t.split(":", 1)[1] not in declared})
        if unknown:
            bare = ", ".join(u.split(":", 1)[1] for u in unknown)
            results.append(
                GateResult(
                    gate=name,
                    layer=GateLayer.DETERMINISTIC,
                    blocked=True,
                    code=code,
                    reason=f"no such {kind}: {bare}",
                    remediation=f"pick an installed {kind} from the manifest, or say the step is not doable",
                    detail={"unknown": unknown},
                )
            )
    for result in results:
        return result
    return GateResult.pass_("named_resource_exists", GateLayer.DETERMINISTIC, checked=True)


def check_output_schema(subject: GateSubject) -> GateResult:
    """Constrain the shape of a structured payload.

    Constrained decoding removes a whole class of hallucination by construction,
    and where it is unavailable this is the deterministic substitute: a required
    key that is absent, or a value drawn from a closed set that does not contain
    it, is refused before it can reach a reader as if it were meaningful.
    """
    name = "output_schema"
    if subject.payload is None:
        return GateResult.pass_(name, GateLayer.DETERMINISTIC, skipped="no payload")
    problems: list[str] = []
    for key, expected in subject.required_payload.items():
        if key not in subject.payload:
            problems.append(f"missing required key {key!r}")
            continue
        if expected and not _type_matches(subject.payload[key], expected):
            problems.append(f"{key!r} should be {expected}, got {type(subject.payload[key]).__name__}")
    for key, allowed in subject.payload_enum.items():
        if key in subject.payload and str(subject.payload[key]) not in allowed:
            problems.append(f"{key!r} must be one of {sorted(allowed)}, got {subject.payload[key]!r}")
    if problems:
        return GateResult(
            gate=name,
            layer=GateLayer.DETERMINISTIC,
            blocked=True,
            code="schema_violation",
            reason="; ".join(problems),
            remediation="emit the declared shape; do not invent keys or values outside the closed set",
            detail={"problems": problems},
        )
    return GateResult.pass_(name, GateLayer.DETERMINISTIC, checked=len(subject.required_payload) + len(subject.payload_enum))


def check_claim_authorization(subject: GateSubject) -> GateResult:
    """Refuse a step standing on a claim that cannot support it.

    Reads the ledger rather than the claim's own text, so a confidently-worded
    premise with nothing behind it fails exactly like a hedged one.
    """
    name = "claim_authorization"
    if subject.ledger is None or not subject.claim_ids:
        return GateResult.pass_(name, GateLayer.DETERMINISTIC, skipped="no claims declared")
    unresolved: list[str] = []
    for claim_id in subject.claim_ids:
        claim = subject.ledger.get(claim_id)
        if claim is None:
            unresolved.append(f"{claim_id} (unknown)")
        elif not claim.authorized:
            unresolved.append(f"{claim_id} ({claim.support.value})")
    if unresolved:
        return GateResult(
            gate=name,
            layer=GateLayer.DETERMINISTIC,
            blocked=True,
            code="unsupported_claim",
            reason=f"step depends on claims that cannot support it: {', '.join(unresolved)}",
            remediation="cite an observed source for each premise, resolve the conflict, or say the step is blocked",
            detail={"claims": unresolved},
        )
    return GateResult.pass_(name, GateLayer.DETERMINISTIC, checked=len(subject.claim_ids))


def check_side_effect(subject: GateSubject) -> GateResult:
    """Refuse an unconfirmed irreversible action.

    Two independent conditions must both hold, which is the point:

    1. no unconfirmed irreversible call is being made, and
    2. any irreversible tool that *is* called was named in the plan.

    Without (2), a confident plan sentence is all an agent needs to authorise a
    delete, and that sentence is exactly the kind of text a hallucinating agent
    produces fluently.
    """
    name = "side_effect"
    if not subject.tool_calls:
        return GateResult.pass_(name, GateLayer.DETERMINISTIC, skipped="no calls")
    classes = {tool: DEFAULT_SIDE_EFFECTS.get(tool, SideEffectClass.UNKNOWN) for tool in subject.tool_calls}
    irreversible = sorted(t for t, c in classes.items() if c is SideEffectClass.IRREVERSIBLE)
    unclassified = sorted(t for t, c in classes.items() if c is SideEffectClass.UNKNOWN)
    if not irreversible and not unclassified:
        return GateResult.pass_(name, GateLayer.DETERMINISTIC, checked=len(subject.tool_calls))
    if subject.forbid_unconfirmed_side_effects:
        culprits = sorted(set(irreversible) | set(unclassified))
        return GateResult(
            gate=name,
            layer=GateLayer.DETERMINISTIC,
            blocked=True,
            code="unconfirmed_side_effect",
            reason=f"irreversible or unclassified call(s) without confirmation: {', '.join(culprits)}",
            remediation="name it in the plan and obtain explicit confirmation, or choose a reversible tool",
            detail={"irreversible": irreversible, "unclassified": unclassified},
        )
    unplanned = sorted(t for t in set(irreversible) | set(unclassified) if subject.planned_tools and t not in subject.planned_tools)
    if unplanned:
        return GateResult(
            gate=name,
            layer=GateLayer.DETERMINISTIC,
            blocked=True,
            code="unplanned_side_effect",
            reason=f"irreversible call(s) not present in the plan: {', '.join(unplanned)}",
            remediation="replan, or ask for the action to be confirmed explicitly",
            detail={"unplanned": unplanned},
        )
    return GateResult.pass_(name, GateLayer.DETERMINISTIC, checked=len(subject.tool_calls))


#: Tools that *are* the consultation. Calling one is the probe, so a read/search
#: call can never be blocked for "not having consulted prior work" — it is doing
#: the consulting. Without this the gate would refuse every tool call in a
#: default installation, because nothing outside the gate sets ``reuse_probe_run``.
PROBE_SATISFYING_TOOLS: frozenset[str] = frozenset(
    {
        "read_file",
        "grep",
        "glob",
        "list_dir",
        "ast_grep_search",
        "search_project_docs",
        "codebase_search",
        "session_search",
        "describe_skill",
        "tool_search",
        "tool_describe",
        "list_skills",
        "self_documentation_tool",
    }
)


def check_reuse_probe(subject: GateSubject) -> GateResult:
    """Require that Alpha's own capabilities were consulted before new work.

    This is the gate form of the reuse finding. Repository recall collapses
    across turns and half of long chains end up re-implementing what already
    exists, and the ablation says the fix is not more context but a **map plus a
    habit of checking it**. The gate does not decide whether reuse was *correct* —
    only that the question was asked, because a step that never looked cannot be
    said to have decided not to.

    **Satisfied three ways**, which is what keeps it from being a dead gate:

    1. the step itself calls a read/search tool (:data:`PROBE_SATISFYING_TOOLS`);
    2. the caller set ``reuse_probe_run`` (the run already consulted the manifest);
    3. the step is purely read-only, so it cannot be producing duplication.

    It only blocks a *productive* call — a write, an edit, a shell — with no
    recorded consultation, because that is the exact step where a re-implementation
    becomes a permanent parallel copy.
    """
    name = "reuse_probe"
    if subject.reuse_probe_run:
        return GateResult.pass_(name, GateLayer.DETERMINISTIC, probed=True)
    called = {t for t in subject.tool_calls if t}
    if called and called <= PROBE_SATISFYING_TOOLS:
        return GateResult.pass_(name, GateLayer.DETERMINISTIC, probed="this step is itself a consultation")
    classes = {t: DEFAULT_SIDE_EFFECTS.get(t, SideEffectClass.UNKNOWN) for t in called}
    if called and all(c is SideEffectClass.READ_ONLY for c in classes.values()):
        return GateResult.pass_(name, GateLayer.DETERMINISTIC, probed="read-only step cannot duplicate")
    return GateResult(
        gate=name,
        layer=GateLayer.DETERMINISTIC,
        blocked=True,
        code="reuse_probe_missing",
        reason="this step produces new work without consulting the capability manifest or existing code",
        remediation="read the capability manifest and search the existing implementation before writing new code",
        detail={"tools": sorted(called)},
    )


#: Structural type names a caller may declare. Resolved from this table rather
#: than through ``eval``: a name outside the table is not a type this gate
#: understands, and silently failing open on it would make a misspelled
#: requirement look satisfied.
_TYPE_TABLE: dict[str, type | tuple[type, ...]] = {
    "bool": bool,
    # An int satisfies a float requirement; a float does not satisfy an int one.
    "int": int,
    "float": (int, float),
    "str": str,
    "list": (list, tuple),
    "dict": dict,
}


def _type_matches(value: Any, expected: str) -> bool:
    """Structural type check by declared name.

    ``any``/``""`` matches everything. An unrecognised name fails the check
    rather than passing it, so a typo in a required-type declaration surfaces as
    a refusal instead of a check that never ran.
    """
    if expected in {"any", ""}:
        return True
    if expected not in _TYPE_TABLE:
        return False
    return isinstance(value, _TYPE_TABLE[expected])


@dataclass
class GatePipeline:
    """An ordered set of gates, cheapest first.

    Order is the contract, not an implementation detail: a caller can register a
    model gate ahead of a deterministic one, and the run will still resolve a
    deterministic block first, because :meth:`run` filters model results out of
    the blocking path entirely.
    """

    gates: tuple[Callable[[GateSubject], GateResult], ...] = ()

    def __post_init__(self) -> None:
        normalized = tuple(gates for gates in self.gates if callable(gates))
        object.__setattr__(self, "gates", normalized)

    def run(self, subject: GateSubject) -> tuple[GateResult, ...]:
        """Run every gate and return all verdicts, in registration order.

        Gates do not short-circuit: the agent needs the full picture to act on,
        and a single cheap early exit would hide the second problem behind the
        first.
        """
        results: list[GateResult] = []
        for gate in self.gates:
            try:
                result = gate(subject)
            except Exception as exc:  # noqa: BLE001 - a broken gate must not become a silent pass
                # Fail closed. A gate that crashes has not verified anything, and
                # reporting that as a pass is the exact defect class this package
                # exists to remove.
                result = GateResult(
                    gate=getattr(gate, "__name__", "gate"),
                    layer=GateLayer.DETERMINISTIC,
                    blocked=True,
                    code="gate_error",
                    reason=f"gate raised {type(exc).__name__}: {exc}",
                    remediation="this check is unavailable; do not proceed on an unverified step",
                )
            results.append(result)
        return tuple(results)

    @staticmethod
    def block(results: Sequence[GateResult]) -> GateResult | None:
        """The blocking result, with model-layer verdicts unable to clear one.

        A model gate is only ever reported as advice. Its text is preserved on
        the deterministic blocker so an operator can see what the judge thought,
        but it cannot unblock.
        """
        hard = [r for r in results if r.blocked and r.layer is not GateLayer.MODEL]
        if hard:
            return hard[0]
        return blocked_by([r for r in results if r.blocked])

    @classmethod
    def standard(cls) -> GatePipeline:
        """The default pipeline, cheapest first.

        Tool existence precedes schema because an unknown tool has no schema worth
        checking; existence precedes side effects so a fabricated tool name is not
        reported as an unconfirmed destructive action, which would be a true but
        misleading reason.
        """
        return cls(
            gates=(
                check_tool_exists,
                check_named_resource_exists,
                check_output_schema,
                check_claim_authorization,
                check_side_effect,
            )
        )

    @classmethod
    def with_reuse(cls, *, required: bool = True) -> GatePipeline:
        """The standard pipeline plus the reuse probe.

        ``required=False`` returns a pipeline whose probe only *reports*: the
        verdict is attached, so a run can be measured either way while the default
        for new installations is to require it.
        """
        probe = check_reuse_probe
        if not required:
            probe = _advisory_probe
        return cls(
            gates=(
                check_tool_exists,
                probe,
                check_named_resource_exists,
                check_output_schema,
                check_claim_authorization,
                check_side_effect,
            )
        )


def _advisory_probe(subject: GateSubject) -> GateResult:
    """The reuse probe, reported without blocking."""
    result = check_reuse_probe(subject)
    return GateResult(
        gate=result.gate,
        layer=GateLayer.DETERMINISTIC,
        blocked=False,
        reason=result.reason,
        code=result.code,
        detail=result.detail,
    )


def parse_payload(raw: str | bytes | Mapping[str, Any] | None) -> tuple[Mapping[str, Any] | None, str]:
    """Coerce a candidate payload into a mapping for schema checking.

    Returns ``(payload, problem)``. A malformed payload is a *problem*, never a
    silent ``None`` — dropping unparseable text would let a malformed answer
    through the schema gate untouched, which is the one input it most needs to see.
    """
    if raw is None:
        return None, ""
    if isinstance(raw, Mapping):
        return raw, ""
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            return None, f"payload is not utf-8: {exc}"
    text = raw.strip()
    if not text:
        return None, "payload is empty"
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"payload is not valid JSON: {exc}"
    if not isinstance(parsed, dict):
        return None, f"payload must be a JSON object, got {type(parsed).__name__}"
    return parsed, ""


def summarize(results: Iterable[GateResult]) -> dict[str, Any]:
    """Count verdicts by layer, for run metadata.

    ``model_advisories`` is reported separately from blocks on purpose: a judge
    that flagged something and was overruled is not the same as nothing happening,
    and collapsing the two would make a model's output look inert.
    """
    collected = list(results)
    return {
        "total": len(collected),
        "blocked": sum(1 for r in collected if r.blocked),
        "deterministic_blocks": sum(1 for r in collected if r.blocked and r.layer is GateLayer.DETERMINISTIC),
        "external_blocks": sum(1 for r in collected if r.blocked and r.layer is GateLayer.EXTERNAL),
        "model_advisories": sum(1 for r in collected if r.blocked and r.layer is GateLayer.MODEL),
        "codes": sorted({r.code for r in collected if r.blocked and r.code}),
    }
