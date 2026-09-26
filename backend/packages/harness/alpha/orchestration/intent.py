"""Universal Prompt Perception, Goal Decomposition & Intent Engine (Module A).

Implements the master plan section 2 pipeline as pure, deterministic library
functions (LLMs propose, the deterministic runtime enforces):

1. Domain detection — :class:`PromptDomain` (SDLC, research, security, ops,
   database, GUI automation, self-repair) with an honest ``GENERAL`` fallback
   for low-signal or genuinely ambiguous prompts instead of a forced guess.
2. Complexity mode — :class:`ComplexityMode` (``DIRECT_RESPONSE``,
   ``STRUCTURED_PLAN``, ``AUTONOMOUS_SWARM``, ``RECURRING_AUTOMATION``) with a
   disclosed precedence order: recurrence > multi-agent/long-running >
   multi-step planning > direct.
3. Thinking mode — :class:`ThinkingMode` with a reasoning token-budget hint
   and a model-tier hint (``fast/lightweight`` vs ``deep reasoning``). Budgets
   are *hints* for the model router, never a guarantee of provider behavior.
4. Contextual slash-command auto-resolution —
   :func:`resolve_slash_command` detects prompt context implying ``/boost``,
   ``/goal create``, ``/schedule``, ``/grill-me``, ``/teamwork-preview``,
   ``/learn`` or ``/self-heal`` WITHOUT the user typing ``/``, returning
   ``{command, args, confidence, reason, registered}``. It never forces a
   match: no signal above a rule's threshold is an honest no-resolve.
5. Goal decomposition entry — :class:`IntentGoalEngine` wires perception to
   :class:`alpha.orchestration.goals.GoalDecomposer`.

Honesty contract for the resolver (see also DY-R3 in
docs/IMPLEMENTATION_MATRIX.md, which removed a phantom ``/boost`` suggestion
from the workflow perception layer):

* ``registered`` is a tri-state honest flag against ``alpha.commands.catalog``
  — ``True`` (command exists), ``False`` (spec command the catalog does not
  define yet: the executor seam must register it first or the registry will
  reject invocation with ``not_found``), ``None`` (catalog could not be
  imported, so registration is unverified).
* Five of the seven spec commands (``/boost``, ``/schedule``, ``/grill-me``,
  ``/teamwork-preview``, ``/self-heal``) are currently ``registered=False``;
  ``reason`` says so explicitly. This module never claims they are executable.
* Explicit prompts that already start with ``/`` are left to the command
  executor untouched (no double resolution).

This module is a pure library: no I/O beyond an in-process import of the
command catalog, no event-loop wiring. The executor seam lives in
``alpha/commands/backend_handlers.py`` (owned by a separate batch).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from alpha.orchestration.goals import GoalDecomposer

# ---------------------------------------------------------------------------
# Enums (master plan 2.1)
# ---------------------------------------------------------------------------


class PromptDomain(StrEnum):
    """Detected prompt domain; GENERAL is the honest low-signal fallback."""

    SDLC = "sdlc"
    RESEARCH = "research"
    SECURITY = "security"
    OPS = "ops"
    DATABASE = "database"
    GUI_AUTOMATION = "gui_automation"
    SELF_REPAIR = "self_repair"
    GENERAL = "general"


class ComplexityMode(StrEnum):
    """Execution/complexity mode selected for the prompt (spec 2.1.2)."""

    DIRECT_RESPONSE = "direct_response"
    STRUCTURED_PLAN = "structured_plan"
    AUTONOMOUS_SWARM = "autonomous_swarm"
    RECURRING_AUTOMATION = "recurring_automation"


class ThinkingTier(StrEnum):
    """Model-tier hint: fast/lightweight vs deep reasoning (spec 2.1.3)."""

    FAST = "fast"
    DEEP = "deep"


# Reasoning token-budget *hints* consumed by the model router; not guarantees.
FAST_TOKEN_BUDGET = 1024
DEEP_TOKEN_BUDGET = 8192
MODEL_TIER_HINTS: dict[ThinkingTier, str] = {
    ThinkingTier.FAST: "fast/lightweight",
    ThinkingTier.DEEP: "deep reasoning",
}

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DomainClassification:
    """Domain verdict with honest confidence and evidence."""

    domain: PromptDomain
    confidence: float
    reason: str
    secondary: tuple[PromptDomain, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain.value,
            "confidence": self.confidence,
            "reason": self.reason,
            "secondary": [item.value for item in self.secondary],
        }


@dataclass(frozen=True)
class ComplexityClassification:
    """Complexity verdict with the disclosed matching reason."""

    mode: ComplexityMode
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"mode": self.mode.value, "reason": self.reason}


@dataclass(frozen=True)
class ThinkingMode:
    """Token-budget + model-tier hint with the reasons that selected it."""

    tier: ThinkingTier
    token_budget: int
    model_tier_hint: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier.value,
            "token_budget": self.token_budget,
            "model_tier_hint": self.model_tier_hint,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class CommandResolution:
    """Result of contextual slash-command auto-resolution.

    ``command=None`` is the honest no-resolve. ``registered`` is the tri-state
    catalog-honesty flag described in the module docstring.
    """

    command: str | None
    args: str
    confidence: float
    reason: str
    registered: bool | None = None

    @property
    def resolved(self) -> bool:
        return self.command is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "args": self.args,
            "confidence": self.confidence,
            "reason": self.reason,
            "registered": self.registered,
        }


@dataclass(frozen=True)
class IntentAnalysis:
    """Full perception output: domain + complexity + thinking + slash command."""

    prompt: str
    domain: DomainClassification
    complexity: ComplexityClassification
    thinking: ThinkingMode
    slash_command: CommandResolution

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt,
            "domain": self.domain.to_dict(),
            "complexity": self.complexity.to_dict(),
            "thinking": self.thinking.to_dict(),
            "slash_command": self.slash_command.to_dict(),
        }


# ---------------------------------------------------------------------------
# 1. Domain detection
# ---------------------------------------------------------------------------

_DOMAIN_PATTERNS: dict[PromptDomain, tuple[re.Pattern[str], ...]] = {
    PromptDomain.SDLC: (
        re.compile(r"\b(?:implement\w*|refactor\w*|features?|endpoints?|unit tests?|integration tests?|pull requests?|code reviews?)\b", re.IGNORECASE),
        re.compile(r"\b(?:frontend|backend|fastapi|pytest|typescript|javascript|python|react|api handlers?)\b", re.IGNORECASE),
    ),
    PromptDomain.RESEARCH: (
        re.compile(r"\b(?:research\w*|surveys?|literature|citations?|peer-reviewed|state of the art)\b", re.IGNORECASE),
        re.compile(r"\b(?:sources?|papers|market analyses|investigate)\b", re.IGNORECASE),
    ),
    PromptDomain.SECURITY: (
        re.compile(r"\b(?:secur\w*|vulnerabilit\w*|xss|sql injections?|pentests?|threat models?)\b", re.IGNORECASE),
        re.compile(r"\b(?:cve-\d+|authentication|authorizations?|encrypt\w*|intrusion)\b", re.IGNORECASE),
    ),
    PromptDomain.OPS: (
        re.compile(r"\b(?:deploy\w*|kubernetes|k8s|docker\w*|ci/?cd|pipelines?|nginx|terraform|helm)\b", re.IGNORECASE),
        re.compile(r"\b(?:on-call|incidents?|uptime|infrastructures?|monitor\w*|ingress|autoscal\w*)\b", re.IGNORECASE),
    ),
    PromptDomain.DATABASE: (
        re.compile(r"\b(?:postgres\w*|mysql\w*|sqlite\w*|mongo\w*|databases?|schemas?|sql)\b", re.IGNORECASE),
        re.compile(r"\b(?:queries|query|indexes|indexing|index|tables?|migrations?|columns?)\b", re.IGNORECASE),
    ),
    PromptDomain.GUI_AUTOMATION: (
        re.compile(r"\b(?:playwright|selenium|click(?:ing)?|buttons?|checkboxes?|screenshots?)\b", re.IGNORECASE),
        re.compile(r"\b(?:browser automation|ui automation|selectors?|xpath|web scrap\w*|crawl\w*)\b", re.IGNORECASE),
    ),
    PromptDomain.SELF_REPAIR: (
        re.compile(r"\b(?:tracebacks?|stack traces?|failing tests?|flaky tests?|regressions?|crash(?:es|ed|ing)?)\b", re.IGNORECASE),
        re.compile(r"\b(?:broken builds?|roll ?backs?|self-heal\w*|repairs?|error loops?|bisect\w*)\b", re.IGNORECASE),
    ),
}


def classify_domain(text: str) -> DomainClassification:
    """Score the prompt against every domain bucket and pick the honest winner.

    A tie between top-scoring domains returns ``GENERAL`` with the tie spelled
    out in ``reason`` — never an arbitrary default. No signals also returns
    ``GENERAL`` (confidence 0.0). Confidence is the winner's share of all
    matched signals.
    """
    scores: dict[PromptDomain, int] = {}
    hits: dict[PromptDomain, list[str]] = {}
    for domain, patterns in _DOMAIN_PATTERNS.items():
        matched: list[str] = []
        for pattern in patterns:
            for match in pattern.finditer(text):
                matched.append(match.group(0).lower())
        if matched:
            scores[domain] = len(matched)
            hits[domain] = matched
    if not scores:
        return DomainClassification(PromptDomain.GENERAL, 0.0, "no domain signals matched")

    total = sum(scores.values())
    ranked = sorted(scores, key=lambda item: (-scores[item], item.value))
    top = ranked[0]
    top_score = scores[top]
    tied = [domain for domain in ranked if scores[domain] == top_score]
    if len(tied) > 1:
        confidence = round(top_score / total, 2)
        names = ", ".join(domain.value for domain in tied)
        return DomainClassification(
            PromptDomain.GENERAL,
            confidence,
            f"ambiguous: {names} tied at {top_score} signal(s); no default applied",
            tuple(ranked),
        )
    confidence = round(top_score / total, 2)
    reason = f"matched {top_score} of {total} signal(s): {', '.join(hits[top][:5])}"
    secondary = tuple(domain for domain in ranked[1:] if scores[domain] > 0)
    return DomainClassification(top, confidence, reason, secondary)


# ---------------------------------------------------------------------------
# 2. Complexity mode (precedence: recurrence > swarm > planning > direct)
# ---------------------------------------------------------------------------

_COMPLEXITY_RULES: tuple[tuple[ComplexityMode, str, tuple[re.Pattern[str], ...]], ...] = (
    (
        ComplexityMode.RECURRING_AUTOMATION,
        "recurrence",
        (
            re.compile(r"\bevery \d+ (?:seconds?|minutes?|hours?|days?|weeks?|months?)\b", re.IGNORECASE),
            re.compile(r"\bevery (?:day|hour|morning|night|weekday|weeknight|workday)\b", re.IGNORECASE),
            re.compile(r"\bonce a (?:day|hour|week|month)\b", re.IGNORECASE),
            re.compile(r"\brecurr\w*\b", re.IGNORECASE),
            re.compile(r"\bcron\b", re.IGNORECASE),
            re.compile(r"\bon a schedule\b", re.IGNORECASE),
            re.compile(r"\b(?:run|check|monitor|sync|backup|rotate|prune|refresh|alert|notify)\w*\b.{0,60}\b(?:daily|hourly|nightly|every)\b", re.IGNORECASE),
        ),
    ),
    (
        ComplexityMode.AUTONOMOUS_SWARM,
        "multi-agent/long-running",
        (
            re.compile(r"\b(?:swarms?|multi-?agents?|sub-?agents?|parallel (?:workers|agents|tasks)|team of agents|workgroups?|bot rosters?)\b", re.IGNORECASE),
            re.compile(r"\bovernight\b", re.IGNORECASE),
            re.compile(r"\bmulti[- ]day\b", re.IGNORECASE),
            re.compile(r"\blong[- ]running\b", re.IGNORECASE),
            re.compile(r"\bbuild (?:a |an |the )?(?:complete|entire|whole|full) (?:product|system|application|platform|codebase)\b", re.IGNORECASE),
            re.compile(r"\b(?:deliver|complete|finish|build)\b.{0,80}\bend[- ]to[- ]end\b", re.IGNORECASE),
            re.compile(r"\buntil\b.{0,80}\b(?:done|complete|completed|finished)\b", re.IGNORECASE),
        ),
    ),
    (
        ComplexityMode.STRUCTURED_PLAN,
        "planning",
        (
            re.compile(r"\bstep[- ]by[- ]step\b", re.IGNORECASE),
            re.compile(r"\bmulti[- ]step\b", re.IGNORECASE),
            re.compile(r"\b(?:create|make|draft|write|produce|design) (?:a |an |the )?(?:plan|roadmap|blueprint|strategy)\b", re.IGNORECASE),
            re.compile(r"\bfirst\b.{0,120}\bthen\b", re.IGNORECASE),
        ),
    ),
)

_SEQUENCER_RE = re.compile(r"\b(?:then|after that|finally|next|afterwards)\b", re.IGNORECASE)


def classify_complexity(text: str) -> ComplexityClassification:
    """Select the execution mode; the winning rule and signal are disclosed."""
    for mode, name, patterns in _COMPLEXITY_RULES:
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                return ComplexityClassification(mode, f"matched {name} signal: '{match.group(0)}'")
    words = len(text.split())
    sequencers = len(_SEQUENCER_RE.findall(text))
    if words >= 25 and sequencers >= 2:
        return ComplexityClassification(
            ComplexityMode.STRUCTURED_PLAN,
            f"{words}-word prompt with {sequencers} sequencing markers",
        )
    return ComplexityClassification(ComplexityMode.DIRECT_RESPONSE, "no recurrence, multi-agent, or multi-step planning signals matched")


# ---------------------------------------------------------------------------
# 3. Thinking mode (token budget + model tier hint)
# ---------------------------------------------------------------------------

_DEEP_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:architecture|architectural)\b", re.IGNORECASE),
    re.compile(r"\btrade-?offs?\b", re.IGNORECASE),
    re.compile(r"\b(?:proof|formally|consensus|cryptograph\w*|fault[- ]toleran\w*|distributed systems?)\b", re.IGNORECASE),
    re.compile(r"\bsecurity[- ]critical\b", re.IGNORECASE),
)


def resolve_thinking_mode(text: str, *, domain: DomainClassification, complexity: ComplexityClassification) -> ThinkingMode:
    """Pick fast vs deep tier with a token-budget hint and disclosed reasons."""
    reasons: list[str] = []
    if complexity.mode is ComplexityMode.AUTONOMOUS_SWARM:
        reasons.append("autonomous swarm execution mode")
    if domain.domain is PromptDomain.SECURITY:
        reasons.append("security domain")
    for pattern in _DEEP_PATTERNS:
        match = pattern.search(text)
        if match:
            reasons.append(f"deep-reasoning signal '{match.group(0)}'")
    if reasons:
        return ThinkingMode(ThinkingTier.DEEP, DEEP_TOKEN_BUDGET, MODEL_TIER_HINTS[ThinkingTier.DEEP], "; ".join(reasons))
    return ThinkingMode(
        ThinkingTier.FAST,
        FAST_TOKEN_BUDGET,
        MODEL_TIER_HINTS[ThinkingTier.FAST],
        f"no deep-reasoning signals (complexity={complexity.mode.value}, domain={domain.domain.value})",
    )


# ---------------------------------------------------------------------------
# 4. Contextual slash-command auto-resolver (pure module-level seam)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ResolverRule:
    """One implication rule: which command the matched signals imply."""

    command: str
    patterns: tuple[re.Pattern[str], ...]
    base_confidence: float
    min_signals: int = 1


_RESOLVER_RULES: tuple[_ResolverRule, ...] = (
    # Master plan 2.2 trigger table order.
    _ResolverRule(
        "/boost",
        (
            re.compile(r"\bdeep (?:dive|dives|research|analysis|reasoning|investigation)\b", re.IGNORECASE),
            re.compile(r"\bmulti-?angles?\b", re.IGNORECASE),
            re.compile(r"\bexhaustive(?:ly)?\b", re.IGNORECASE),
            re.compile(r"\bconsider (?:every|all) (?:angle|perspective|viewpoint)s?\b", re.IGNORECASE),
            re.compile(r"\bin-?depth (?:analysis|research|investigation|review)\b", re.IGNORECASE),
        ),
        0.72,
    ),
    _ResolverRule(
        "/goal create",
        (
            re.compile(r"\bovernight\b", re.IGNORECASE),
            re.compile(r"\bmulti[- ]day\b", re.IGNORECASE),
            re.compile(r"\blong[- ]running\b", re.IGNORECASE),
            re.compile(r"\buntil\b.{0,80}\b(?:done|complete|completed|finished)\b", re.IGNORECASE),
        ),
        0.75,
    ),
    _ResolverRule(
        "/schedule",
        (
            re.compile(r"\bevery \d+ (?:seconds?|minutes?|hours?|days?|weeks?|months?)\b", re.IGNORECASE),
            re.compile(r"\bevery (?:day|hour|morning|night|weekday|weeknight|workday)\b", re.IGNORECASE),
            re.compile(r"\bonce a (?:day|hour|week|month)\b", re.IGNORECASE),
            re.compile(r"\brecurr\w*\b", re.IGNORECASE),
            re.compile(r"\bcron\b", re.IGNORECASE),
            re.compile(r"\bon a schedule\b", re.IGNORECASE),
            re.compile(r"\b(?:run|check|monitor|sync|backup|rotate|prune|refresh|alert|notify)\w*\b.{0,60}\b(?:daily|hourly|nightly|every)\b", re.IGNORECASE),
        ),
        0.78,
    ),
    _ResolverRule(
        "/grill-me",
        (
            re.compile(r"\btrade-?offs?\b", re.IGNORECASE),
            re.compile(r"\brisky (?:migration|change|refactor|rewrite|redesign)\b", re.IGNORECASE),
            re.compile(r"\bambiguous (?:requirements?|spec\w*|scope|acceptance criteria)\b", re.IGNORECASE),
            re.compile(r"\bask me (?:some |a few |any )?(?:questions|clarifying)\b", re.IGNORECASE),
            re.compile(r"\b(?:not|isn't|unclear) sure (?:what|which|how|whether)\b", re.IGNORECASE),
        ),
        0.70,
        min_signals=2,  # one weak signal (e.g. an informational "trade-offs" question) must not force an interview
    ),
    _ResolverRule(
        "/teamwork-preview",
        (
            re.compile(r"\bbot rosters?\b", re.IGNORECASE),
            re.compile(r"\bspecialist (?:roles?|team|roster|bots?)\b", re.IGNORECASE),
            re.compile(r"\bteamwork (?:plan|preview|mode)\b", re.IGNORECASE),
            re.compile(r"\bmulti-?agent coordination\b", re.IGNORECASE),
            re.compile(r"\bteam of (?:specialists|experts|bots|agents)\b", re.IGNORECASE),
            re.compile(r"\bwhich specialists\b", re.IGNORECASE),
        ),
        0.72,
    ),
    _ResolverRule(
        "/learn",
        (
            re.compile(r"\bremember (?:this|that|it)\b.{0,60}\b(?:next time|future|lesson|skill)\b", re.IGNORECASE),
            re.compile(r"\b(?:lessons? (?:is|are|learned)|lesson learned)\b", re.IGNORECASE),
            re.compile(r"\b(?:you were|you are|that was) wrong\b", re.IGNORECASE),
            re.compile(r"\bsave (?:this|that|it) as a (?:lesson|skill|rule)\b", re.IGNORECASE),
        ),
        0.75,
    ),
    _ResolverRule(
        "/self-heal",
        (
            re.compile(r"\btests?\b.{0,40}\bfail\w*\b", re.IGNORECASE),
            re.compile(r"\bpytest\b.{0,40}\bfail\w*\b", re.IGNORECASE),
            re.compile(r"\btracebacks?\b", re.IGNORECASE),
            re.compile(r"\bstack traces?\b", re.IGNORECASE),
            re.compile(r"\blint\w*\b.{0,30}\b(?:errors?|fail\w*|breakdown)\b", re.IGNORECASE),
            re.compile(r"\bbroke (?:the |our |these )?tests?\b", re.IGNORECASE),
            re.compile(r"\bregressions?\b.{0,60}\b(?:fail\w*|broken|broke|red|detected)\b", re.IGNORECASE),
            re.compile(r"\bCI (?:is |went |is now )?red\b", re.IGNORECASE),
        ),
        0.75,
    ),
)


def _load_catalog_commands() -> frozenset[str] | None:
    """Real slash-commands from ``alpha.commands.catalog``; None = unavailable.

    Read-only and fail-honest: an import failure means registration cannot be
    verified, never that the command is safe to claim as executable. Follows
    the same pattern as ``alpha.workflow.dynamic_perception``.
    """
    try:
        from alpha.commands.catalog import get_default_catalog_entries

        return frozenset(entry[0] for entry in get_default_catalog_entries())
    except Exception:  # pragma: no cover - defensive: never claim verified registration
        return None


def _no_resolve(reason: str) -> CommandResolution:
    return CommandResolution(command=None, args="", confidence=0.0, reason=reason, registered=None)


def resolve_slash_command(prompt: str) -> CommandResolution:
    """Contextually resolve an implied slash command from natural language.

    Pure and deterministic: same prompt, same resolution. Returns a
    :class:`CommandResolution` whose four mandated fields are ``command``,
    ``args``, ``confidence`` and ``reason``; ``registered`` adds the honest
    catalog status. ``command=None`` is returned for empty prompts, explicit
    ``/``-prefixed prompts (executor passthrough), and prompts with no signal
    above a rule's threshold — never a forced match.
    """
    text = (prompt or "").strip()
    if not text:
        return _no_resolve("empty prompt; nothing to resolve")
    if text.startswith("/"):
        return _no_resolve("prompt already starts with an explicit slash command; contextual auto-resolution skipped (command executor handles it directly)")

    catalog = _load_catalog_commands()
    matches: list[tuple[float, int, _ResolverRule, list[str]]] = []
    for index, rule in enumerate(_RESOLVER_RULES):
        matched_texts: list[str] = []
        for pattern in rule.patterns:
            match = pattern.search(text)
            if match:
                matched_texts.append(match.group(0).lower())
        if len(matched_texts) >= rule.min_signals:
            confidence = min(0.95, round(rule.base_confidence + 0.05 * (len(matched_texts) - 1), 2))
            matches.append((confidence, index, rule, matched_texts))

    if not matches:
        return _no_resolve("no contextual signal matched any resolver rule above its threshold; not resolving (never a forced match)")

    matches.sort(key=lambda item: (-item[0], item[1]))
    confidence, _, rule, matched_texts = matches[0]
    signals = ", ".join(dict.fromkeys(matched_texts))
    reason = f"context implies {rule.command}: matched {len(matched_texts)} signal(s) — {signals}"
    if len(matches) > 1:
        runner_up = matches[1]
        if confidence - runner_up[0] < 0.05:
            reason += f"; near-tie with {runner_up[2].command} — treat as ambiguous context"

    if catalog is None:
        registered: bool | None = None
        reason += "; command catalog unavailable — registration unverified"
    else:
        registered = rule.command in catalog
        if registered:
            reason += "; registered in alpha.commands.catalog"
        else:
            reason += f"; '{rule.command}' is NOT defined in alpha.commands.catalog yet — the executor seam must register it before invocation"

    return CommandResolution(command=rule.command, args=text, confidence=confidence, reason=reason, registered=registered)


# ---------------------------------------------------------------------------
# 5. Composition + capability entrypoint
# ---------------------------------------------------------------------------


def parse_intent(prompt: str) -> IntentAnalysis:
    """Run the full perception pipeline over one prompt (pure function)."""
    text = (prompt or "").strip()
    domain = classify_domain(text)
    complexity = classify_complexity(text)
    thinking = resolve_thinking_mode(text, domain=domain, complexity=complexity)
    slash = resolve_slash_command(text)
    return IntentAnalysis(
        prompt=text,
        domain=domain,
        complexity=complexity,
        thinking=thinking,
        slash_command=slash,
    )


class IntentGoalEngine:
    """Catalog entrypoint for capability ``intent_goal_engine``.

    Thin, stateless facade composing the pure module seams; the capability
    loader (``alpha.capabilities.registry``) returns this class object and the
    caller decides whether to instantiate it.
    """

    def analyze(self, prompt: str) -> IntentAnalysis:
        return parse_intent(prompt)

    def resolve_command(self, prompt: str) -> CommandResolution:
        return resolve_slash_command(prompt)

    def decomposer_for(self, project_id: str, *, home: Path | None = None) -> GoalDecomposer:
        """Open the durable Milestones -> Tasks -> Subtasks decomposer."""
        return GoalDecomposer(project_id, home=home)
