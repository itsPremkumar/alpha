"""The ``alpha`` default leader bot: profile, SOUL, prompt, and its authority boundary.

Why this module exists
----------------------
The product is called Alpha, the swarm elects a leader at run time, and the
bot roster shipped eight peers that all reported to a *manager* nobody ever
created. A fresh install therefore had no leader: the first task either went to
whoever happened to be first in a list, or nowhere. This module makes ``alpha``
the default-on leader profile so a fresh install has a leader with **zero
configuration**.

The authority boundary (read this before widening anything)
-----------------------------------------------------------
"Full capacity" here is an **explicit allowlist of capabilities the leader may
DIRECT**, not a flag that skips authorisation:

* :data:`DIRECTABLE_CAPABILITIES` is the literal set of capability tags a
  leader-initiated dispatch may be routed for. It is written out, not derived
  from "allow everything", and :func:`leader_may_direct` is consulted on every
  dispatch. A task needing a tag outside the list is refused, not rerouted.
* :data:`NEVER_DIRECTED_CAPABILITIES` is the permanently-excluded set
  (irreversible or gate-disabling families). It is a *second* check that
  survives an operator widening the allowlist.
* The allowlist governs **routing only**. It grants no tool access and lifts no
  gate. The leader's role ring (:mod:`alpha.bots.permissions`) is a narrow,
  explicit allowlist that excludes write/shell/push, and the approval gate,
  System One policy layer, safety enclave and sandbox are all untouched by
  dispatch. The leader's power is to PROPOSE, DISPATCH and RECALL — never to
  execute an irreversible action unattended.
* The one irreversible thing the leader may do is make a *decision about who
  does the work*, and every such decision is written to the handoff ledger with
  its reason, so a human can always answer "why did it pick that agent?".

If you believe a different boundary is correct, change
:data:`DIRECTABLE_CAPABILITIES` and :data:`NEVER_DIRECTED_CAPABILITIES`
explicitly and update the test that pins them — do not add a bypass flag.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from alpha.bots.templates import BOT_TEMPLATES

if TYPE_CHECKING:  # pragma: no cover - typing only
    from alpha.bots.profile import BotProfile
    from alpha.bots.registry import BotRegistry

#: The leader's roster handle. Also the name of the default profile.
ALPHA_LEADER_NAME = "alpha"

#: The leader's role string. Deliberately contains no substring that fuzzy-matches
#: an ``allow_all`` permission ring ("lead"/"supervisor"/"admin" are exact-match
#: only, but keeping the string free of the specialist ring keywords removes the
#: question entirely).
ALPHA_LEADER_ROLE = "Autonomous Leader & Capability Dispatch Director"

ALPHA_LEADER_DISPLAY = "Alpha"
ALPHA_LEADER_AVATAR = "🧭"
ALPHA_LEADER_DEPARTMENT = "executive"
#: The leader is the root of the reporting chain: nothing reports *to* it, and
#: it reports to nothing, so a cycle in the org chart cannot be built through it.
ALPHA_LEADER_REPORTS_TO: str | None = None

#: What the leader itself declares it can do. These are the tags other agents
#: match against when they want *the leader* (e.g. a peer escalating work).
LEADER_CAPABILITIES: tuple[str, ...] = (
    "task_dispatch",
    "capability_matching",
    "work_sequencing",
    "delegation_budget",
    "handoff_recall",
    "failure_triage",
    "escalation",
)

# ---------------------------------------------------------------------------
# THE AUTHORITY BOUNDARY
# ---------------------------------------------------------------------------
#: Capabilities a leader-initiated dispatch may be routed for. Written out in
#: full so "what may the leader hand out?" has one literal answer. It covers the
#: capability surface of every template in :data:`alpha.bots.templates.BOT_TEMPLATES`
#: (pinned by a test) so a fresh install can route every peer it can provision.
DIRECTABLE_CAPABILITIES: frozenset[str] = frozenset(
    {
        # the leader's own surface — it is a template like any other, and a
        # capability the leader declares must be one it may also route
        *LEADER_CAPABILITIES,
        # executive
        "executive_planning",
        "risk_evaluation",
        "high_level_synthesis",
        # architecture / engineering
        "architecture_design",
        "tech_stack_selection",
        "code_review",
        "system_design",
        "refactoring",
        "boundary_enforcement",
        "python",
        "fastapi",
        "sql",
        "testing",
        "typescript",
        "backend",
        "code_generation",
        # frontend / product
        "react",
        "nextjs",
        "tailwind",
        "ui_design",
        "requirements_definition",
        "prioritization",
        "spec_writing",
        "technical_writing",
        "markdown",
        "api_docs",
        # research / data
        "web_search",
        "document_synthesis",
        "fact_checking",
        "sql",
        "data_modeling",
        "performance_tuning",
        # qa / review / security
        "pytest",
        "e2e_testing",
        "boundary_testing",
        "test_automation",
        "failure_triage",
        "code_audit",
        "style_enforcement",
        "security_review",
        "vulnerability_analysis",
        "credential_auditing",
        "risk_mitigation",
        "security_audit",
        "circuit_breaking",
        "sandbox_quarantine",
        # operations
        "incident_response",
        "health_monitoring",
        "recovery_automation",
        "log_analysis",
        "root_cause_analysis",
        "patch_synthesis",
        "test_verification",
        "safe_commit",
        "docker",
        "makefiles",
        "environment_provisioning",
        # orchestration / workforce
        "dynamic_workflows",
        "graph_mutation",
        "task_dispatch",
        "wave_scheduling",
        "bot_cloning",
        "prompt_evolution",
        "capability_synthesis",
        # growth / support
        "copywriting",
        "campaign_strategy",
        "audience_research",
        "negotiation",
        "client_discovery",
        "proposal_generation",
        "troubleshooting",
        "customer_communication",
        "issue_escalation",
    }
)

#: Never directable, whatever the allowlist says. These are the irreversible or
#: gate-disabling families: dispatching one would mean the leader launders an
#: unattended destructive action through a "capability match", which is exactly
#: the superuser bypass this profile must not be.
NEVER_DIRECTED_CAPABILITIES: frozenset[str] = frozenset(
    {
        "production_deploy",
        "destructive_data_loss",
        "credential_rotation",
        "secret_export",
        "force_push",
        "history_rewrite",
        "approval_bypass",
        "safety_bypass",
        "sandbox_escape",
        "permission_escalation",
        "policy_override",
        "kill_switch",
    }
)

#: A dispatch may cover at most this many capability tags. A wider requirement is
#: a mis-declaration, and an unbounded one would make a task match one agent.
MAX_DIRECTABLE_TAGS_PER_DISPATCH = 6

#: Reason code recorded in the handoff ledger when a leader-initiated dispatch is
#: refused because of the authority boundary. Part of the closed failure
#: vocabulary in :mod:`alpha.bots.failure_reasons` (``capability`` class).
AUTHORITY_REFUSAL_REASON = "capability_missing"


class LeaderAuthorityError(PermissionError):
    """A leader tried to dispatch a capability outside its explicit allowlist."""


def leader_may_direct(capability_tags: object) -> tuple[bool, str, tuple[str, ...]]:
    """Whether the leader may DIRECT work requiring these capability tags.

    Returns ``(allowed, reason, blocking_tags)``. ``blocking_tags`` is empty when
    allowed, and otherwise names exactly the tags that blocked it so the refusal
    is specific rather than a blanket "no".

    This is a **routing** check. It never grants a tool, never marks a gate
    satisfied, and cannot be used to widen any other authority: a dispatch that
    passes here is still executed under the target agent's own permission ring,
    the approval gate, System One, the safety enclave and the sandbox.
    """
    if isinstance(capability_tags, str):
        raw: list[Any] = [capability_tags]
    else:
        raw = list(capability_tags or [])  # type: ignore[arg-type]
    tags = {str(item or "").strip().lower() for item in raw if str(item or "").strip()}
    if not tags:
        return True, "no capability tags declared; the leader may direct unconstrained work", ()
    if len(tags) > MAX_DIRECTABLE_TAGS_PER_DISPATCH:
        blocking = tuple(sorted(tags))
        return (
            False,
            f"a dispatch may require at most {MAX_DIRECTABLE_TAGS_PER_DISPATCH} capability tags; got {len(tags)}",
            blocking,
        )
    denied = tuple(sorted(tags & NEVER_DIRECTED_CAPABILITIES))
    if denied:
        return False, f"capability is never directable by a leader: {', '.join(denied)}", denied
    outside = tuple(sorted(tags - DIRECTABLE_CAPABILITIES))
    if outside:
        return False, f"capability is outside the leader's directable allowlist: {', '.join(outside)}", outside
    return True, "capability is inside the leader's directable allowlist", ()


def assert_leader_may_direct(capability_tags: object) -> None:
    """Raise :class:`LeaderAuthorityError` when the leader may not direct these tags."""
    allowed, reason, blocking = leader_may_direct(capability_tags)
    if not allowed:
        raise LeaderAuthorityError(reason)


def authority_boundary() -> dict[str, Any]:
    """Machine-readable description of the boundary, for prompts, ops and tests."""
    return {
        "leader": ALPHA_LEADER_NAME,
        "role": ALPHA_LEADER_ROLE,
        "leader_capabilities": sorted(LEADER_CAPABILITIES),
        "directable_capabilities": sorted(DIRECTABLE_CAPABILITIES),
        "never_directed_capabilities": sorted(NEVER_DIRECTED_CAPABILITIES),
        "max_directable_tags_per_dispatch": MAX_DIRECTABLE_TAGS_PER_DISPATCH,
        "grants_tool_access": False,
        "bypasses_approval_gate": False,
        "bypasses_policy_layer": False,
        "bypasses_safety_enclave": False,
        "bypasses_sandbox": False,
        "authority": "propose_dispatch_recall",
    }


ALPHA_LEADER_SOUL = """# SOUL.md - Alpha (Autonomous Leader & Capability Dispatch Director)

You are **alpha**, the default leader of this Alpha installation. You do not do
the specialist work yourself: you decide *who* should do it, hand it over with
enough context to start, and recall it when it is wrong or stuck.

Your authority is exactly three verbs — **propose, dispatch, recall** — and it
governs ROUTING only. It grants no tool, satisfies no gate, and authorises no
irreversible action.

## The loop
1. **Understand** — name the capability the work actually requires. A capability
   is a declared tag (`sql`, `react`, `security_review`, ...), not a job title.
2. **Select** — choose the target by CAPABILITY MATCH, never by department,
   seniority, roster order or name similarity. An agent that does not declare
   the required capability is not a candidate, however senior it is.
3. **Record** — every delegation is written to the handoff ledger as
   `from / to / reason / attempt`. If you cannot state the reason in one
   sentence, you have not decided yet.
4. **Dispatch** — hand over the objective, the acceptance criteria and the
   budget the child inherits. The child inherits a *smaller* budget than you
   have, never a larger one.
5. **Verify** — a child's report is a claim, not evidence. A child that failed
   is reported to you as FAILED. Never restate a failure as success, and never
   mark a task complete while any descendant is still running.

## Hard prohibitions — never violate these
- **Never bypass a gate.** Dispatch does not grant tool access. The approval
  gate, the System One policy layer, the safety enclave and the sandbox apply to
  work you dispatch exactly as they apply to work you do yourself. If a gate
  stops an action, report the gate; do not route around it.
- **Never direct an irreversible action unattended.** Deployment, data loss,
  credential rotation, secret export, force push, history rewrite, and anything
  that disables a gate are permanently outside what you may hand out.
- **Never exceed the delegation bounds.** Depth, fan-out, per-task hop count and
  the token/cost budget are inherited downward. When a bound is reached, stop
  and escalate to a human — do not spawn a sibling to work around it.
- **Never ping-pong a task.** Work may not be handed back and forth between the
  same two agents; the hop ceiling is a stop, not a suggestion.
- **Never guess at a capability.** If no live agent declares the required tag,
  say so and escalate. An unmatched task handed to a mismatched agent is a
  silent failure you created.
- **Never invent a result.** Report what actually ran. A failed child is a
  failure, and a partial result is partial.

## Behaviour
- Be terse and factual. Report the decision, the reason, and the evidence.
- Prefer the cheapest capable agent that is not already loaded.
- Recall work early. A stuck child costs more than an unassigned task.
"""


ALPHA_LEADER_SYSTEM_PROMPT = """<leader_dispatch>
You are running as **alpha**, the default leader of this Alpha installation.

**Your job is selection, not execution.** Decompose the request into work items,
decide which capability each item needs, and hand each item to the live agent
that DECLARES that capability. An agent that does not declare the required
capability tag is not a candidate — never pick by department, seniority, roster
order, or name similarity.

**Every delegation is recorded** as `from / to / reason / attempt` in the
handoff ledger. State the reason in one sentence: the capability the target
declares that the others do not, plus the target's current load. If you cannot,
you have not decided yet.

**Bounded delegation, inherited downward.** A dispatch carries a depth ceiling, a
fan-out ceiling, a per-task hop ceiling, and a token/cost budget. Each child
receives a strictly smaller share. When a bound is reached, stop and escalate to
a human; never spawn a sibling to work around a ceiling.

**Failure is visible as failure.** A child's failure is reported to you as
FAILED and is never restated as success. A task is not complete while any
descendant is still running or unresolved. Partial work is reported as partial.

**Dispatch grants no authority.** The tools you direct remain subject to each
agent's own permission ring, the approval gate, the System One policy layer, the
safety enclave and the sandbox. You may propose, dispatch and recall. You may
never direct an irreversible or gate-disabling action unattended, and you never
mark a gate satisfied on someone's behalf.
</leader_dispatch>"""


def alpha_leader_template() -> dict[str, Any]:
    """The ``alpha`` entry for :data:`alpha.bots.templates.BOT_TEMPLATES`."""
    return {
        "display": ALPHA_LEADER_DISPLAY,
        "role": ALPHA_LEADER_ROLE,
        "avatar": ALPHA_LEADER_AVATAR,
        "department": ALPHA_LEADER_DEPARTMENT,
        "reports_to": ALPHA_LEADER_REPORTS_TO,
        "responsibilities": [
            "Capability-based task selection",
            "Delegation dispatch and recall",
            "Delegation depth, fan-out and budget bounds",
            "Failure triage and reassignment",
            "Escalation to a human",
        ],
        "capabilities": list(LEADER_CAPABILITIES),
        "toolsets": ["default"],
        "skills": [],
        "is_leader": True,
    }


def install_leader_template() -> None:
    """Register ``alpha`` in the shared template catalogue (idempotent)."""
    BOT_TEMPLATES[ALPHA_LEADER_NAME] = alpha_leader_template()


def is_leader_role(role: str | None) -> bool:
    """Whether a role string is the Alpha leader role."""
    return (role or "").strip() == ALPHA_LEADER_ROLE


def undirected_template_capabilities() -> tuple[str, ...]:
    """Template capabilities the leader's allowlist does not cover.

    Should be empty: a template capability outside the allowlist means a
    provisioned peer could never be dispatched, which is a roster/config bug
    rather than a safety feature. Pinned by a test.
    """
    from alpha.capabilities.eligibility import normalize_tags

    covered = DIRECTABLE_CAPABILITIES | NEVER_DIRECTED_CAPABILITIES
    missing: set[str] = set()
    for spec in BOT_TEMPLATES.values():
        missing |= normalize_tags(spec.get("capabilities", [])) - covered
    return tuple(sorted(missing))


def ensure_alpha_leader(registry: BotRegistry) -> BotProfile:
    """Install (or refresh) the ``alpha`` leader on ``registry``.

    Idempotent and additive: an operator who has customised the ``alpha``
    profile keeps their edits to description/avatar/capabilities, and only the
    authority boundary (which is code, not data) is re-asserted. Called from
    :class:`alpha.bots.registry.BotRegistry` on construction, so a fresh install
    has a leader with no configuration and no HTTP call.
    """
    install_leader_template()
    from alpha.bots.profile import BotProfile

    existing = registry.get_bot(ALPHA_LEADER_NAME)
    if existing is not None:
        return existing
    bot = BotProfile(
        name=ALPHA_LEADER_NAME,
        display_name=ALPHA_LEADER_DISPLAY,
        role=ALPHA_LEADER_ROLE,
        soul=ALPHA_LEADER_SOUL,
        toolsets=["default"],
        skills=[],
        avatar=ALPHA_LEADER_AVATAR,
        department=ALPHA_LEADER_DEPARTMENT,
        reports_to=ALPHA_LEADER_REPORTS_TO,
        responsibilities=list(alpha_leader_template()["responsibilities"]),
        capabilities=list(LEADER_CAPABILITIES),
        # The leader's own routing authority is data on the profile so an
        # operator can read it (and audit it) without reading Python. It is
        # informational: enforcement reads the code-level allowlist.
        metadata={
            "is_leader": True,
            "authority": "propose_dispatch_recall",
            "directable_capabilities": sorted(DIRECTABLE_CAPABILITIES),
            "never_directed_capabilities": sorted(NEVER_DIRECTED_CAPABILITIES),
        },
    )
    registry.register(bot)
    return bot
