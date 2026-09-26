"""Explicit, reviewable classification rules for authority capabilities.

No model is allowed to decide whether an action is reversible or leaves the
host.  The rules below are ordered, small, and stable enough for a human to
review a diff.  Every result carries the rule id that produced it; a shape the
rules do not recognise becomes :class:`~alpha.safety.authority.models.Verdict`
``UNKNOWN`` rather than being optimistically treated as safe.

The ordering is intentional.  A payment or secret operation is classified by
its highest-impact meaning before a generic ``delete``/``write`` token is
considered.  This mirrors least-privilege reviews: classify the authority the
action grants, not the syntax that happens to implement it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .models import Capability, Externality, Gate, GatePosture, Reversibility, Verdict

# Rule ids are part of the audit contract.  Do not renumber an existing rule:
# a baseline diff and a human review both rely on the id being stable.
RULE_UNKNOWN = "UNK-001"
RULE_UNRECOGNISED = "UNK-002"
RULE_REVERSIBLE_READ = "REV-001"
RULE_REVERSIBLE_WRITE = "REV-002"
RULE_LOCAL_DELETE = "REV-003"
RULE_LOCAL_EXEC = "REV-004"
RULE_DATABASE_MUTATION = "REV-005"
RULE_NETWORK_EGRESS = "EXT-001"
RULE_EXTERNAL_MESSAGE = "EXT-002"
RULE_DEPLOYMENT = "EXT-003"
RULE_SECRET_ACCESS = "PRIV-001"
RULE_FINANCIAL_ACTION = "FIN-001"
RULE_DYNAMIC_OPERATION = "UNK-003"

RULE_VERDICT_UNKNOWN = "VERDICT-UNK-001"
RULE_VERDICT_REVERSIBLE = "VERDICT-001"
RULE_VERDICT_OPERATOR = "VERDICT-002"
RULE_VERDICT_APPROVAL_REPLACEABLE = "VERDICT-003"
RULE_VERDICT_BLOCKED = "VERDICT-004"

#: Human-facing descriptions used by the report and the configuration reader.
RULE_DOCUMENTATION: Mapping[str, str] = {
    RULE_UNKNOWN: "Dynamic registration/reflection or an unparseable source shape; answer UNKNOWN.",
    RULE_UNRECOGNISED: "No explicit action rule matched; answer UNKNOWN and require triage.",
    RULE_REVERSIBLE_READ: "Read/list/search/inspect/status action with no mutation token.",
    RULE_REVERSIBLE_WRITE: "Local create/update/write action whose effect is presumed recoverable.",
    RULE_LOCAL_DELETE: "Delete/remove/drop/truncate/force/branch-delete action in the local execution boundary.",
    RULE_LOCAL_EXEC: "Shell, subprocess, eval/exec, or arbitrary code execution action.",
    RULE_DATABASE_MUTATION: "Database/session mutation (insert/update/delete/drop/truncate/commit).",
    RULE_NETWORK_EGRESS: "HTTP, socket, fetch, web, DNS, or other outbound network action.",
    RULE_EXTERNAL_MESSAGE: "Send/publish/notify/post action that can affect another principal or system.",
    RULE_DEPLOYMENT: "Deploy/release/push/promote action that changes a deployed or shared surface.",
    RULE_SECRET_ACCESS: "Read/access/use of credentials, tokens, private data, or personal information.",
    RULE_FINANCIAL_ACTION: "Payment, charge, refund, transfer, purchase, billing, or pricing action.",
    RULE_DYNAMIC_OPERATION: "Operation reached through a dynamic name/registration path; answer UNKNOWN.",
    RULE_VERDICT_UNKNOWN: "Unknown classification is always blocking and is never safe unattended.",
    RULE_VERDICT_REVERSIBLE: "Reversible action with no effective deny gate may run unattended.",
    RULE_VERDICT_OPERATOR: "Irreversible, external, privacy, or financial action requires an operator token.",
    RULE_VERDICT_APPROVAL_REPLACEABLE: "A human approval pause on a reversible action may be replaced by policy.",
    RULE_VERDICT_BLOCKED: "A default-allow or non-failing gate does not authorize a risky action.",
}

#: Public alias for hosts that want to render the reviewable rule catalogue.
CLASSIFICATION_RULES: Mapping[str, str] = RULE_DOCUMENTATION
RULES = CLASSIFICATION_RULES

_TOKEN_RE = re.compile(r"[a-z][a-z0-9_]*")


@dataclass(frozen=True, slots=True)
class Classification:
    """One explicit classification decision and its audit trail."""

    reversibility: Reversibility
    externality: Externality
    rule_id: str
    reason: str
    verdict: Verdict
    verdict_rule_id: str
    verdict_reason: str


def _tokens(action: str) -> tuple[str, ...]:
    return tuple(_TOKEN_RE.findall(action.lower().replace("-", "_")))


def _has_any(tokens: Iterable[str], words: set[str]) -> bool:
    """Match a rule token across compound identifiers, never by substring alone."""

    return any(token in words or token.startswith(f"{word}_") or token.endswith(f"_{word}") for token in tokens for word in words)


def _custom_rule(action: str, custom_rules: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Return a normalised custom keyword rule, if one exactly matches a token."""

    if not custom_rules:
        return None
    for key in sorted(custom_rules, key=str.casefold):
        if key.casefold() in action.casefold():
            value = custom_rules[key]
            return dict(value) if isinstance(value, Mapping) else None
    return None


def _validate_custom_rule(rule: Mapping[str, Any]) -> tuple[Reversibility, Externality, str, str]:
    reversibility = Reversibility(str(rule.get("reversibility", Reversibility.IRREVERSIBLE.value)))
    externality = Externality(str(rule.get("externality", Externality.UNKNOWN.value)))
    rule_id = str(rule.get("rule_id") or RULE_UNRECOGNISED)
    reason = str(rule.get("reason") or "operator-supplied classification rule")
    return reversibility, externality, rule_id, reason


def classify_action(
    action: str,
    *,
    currently_gated: bool = False,
    gate: Gate | None = None,
    dynamic: bool = False,
    custom_rules: Mapping[str, Any] | None = None,
) -> Classification:
    """Classify one concrete action using ordered, auditable rules.

    ``dynamic`` is an explicit signal from the census that the call was found
    through reflection/registration.  It cannot be downgraded to a safe class.
    The documented default for a non-empty but unrecognised action is
    ``UNK-002``; callers must triage it.
    """

    text = str(action or "").strip()
    tokens = _tokens(text)
    if dynamic:
        return Classification(
            reversibility=Reversibility.IRREVERSIBLE,
            externality=Externality.UNKNOWN,
            rule_id=RULE_DYNAMIC_OPERATION,
            reason="dynamic registration/reflection hides the concrete authority",
            verdict=Verdict.UNKNOWN,
            verdict_rule_id=RULE_VERDICT_UNKNOWN,
            verdict_reason="dynamic authority is a blocking unknown",
        )

    custom = _custom_rule(text, custom_rules)
    if custom is not None:
        reversibility, externality, rule_id, reason = _validate_custom_rule(custom)
    elif not text or not tokens:
        reversibility, externality, rule_id = Reversibility.IRREVERSIBLE, Externality.UNKNOWN, RULE_UNKNOWN
        reason = "the census could not resolve a concrete action"
    elif "route" in tokens and "read" in tokens:
        reversibility, externality, rule_id = Reversibility.REVERSIBLE, Externality.LOCAL, RULE_REVERSIBLE_READ
        reason = "read-only HTTP route has no declared side effect"
    elif "route" in tokens and ({"mutation", "update", "post", "put", "patch"} & set(tokens)):
        reversibility, externality, rule_id = Reversibility.REVERSIBLE, Externality.LOCAL, RULE_REVERSIBLE_WRITE
        reason = "HTTP route mutation is a local write pending its permission/owner gate"
    elif "re" in tokens and "compile" in tokens:
        reversibility, externality, rule_id = Reversibility.REVERSIBLE, Externality.LOCAL, RULE_REVERSIBLE_READ
        reason = "regular-expression compilation is a local, non-authoritative transformation"
    elif "release" in tokens and _has_any(tokens, {"reservation", "lease", "lock", "resource"}):
        reversibility, externality, rule_id = Reversibility.REVERSIBLE, Externality.LOCAL, RULE_REVERSIBLE_WRITE
        reason = "local resource release is treated as a bounded cleanup operation"
    elif _has_any(tokens, {"payment", "charge", "refund", "transfer", "purchase", "billing", "checkout", "stripe", "spend"}):
        reversibility, externality, rule_id = Reversibility.FINANCIAL, Externality.FINANCIAL, RULE_FINANCIAL_ACTION
        reason = "action can move money or create a financial obligation"
    elif _has_any(tokens, {"secret", "credential", "password", "api_key", "apikey", "private_key", "token", "pii", "personal_data", "environ", "getenv"}):
        reversibility, externality, rule_id = Reversibility.PRIVACY, Externality.PRIVACY, RULE_SECRET_ACCESS
        reason = "action touches credentials or private/personal data"
    elif _has_any(tokens, {"approval", "approve", "clarify", "clarification", "ask", "human", "confirm", "operator"}):
        reversibility, externality, rule_id = Reversibility.EXTERNAL_WORLD, Externality.EXTERNAL, RULE_EXTERNAL_MESSAGE
        reason = "action pauses for or records a human/operator decision"
    elif _has_any(tokens, {"http", "https", "httpx", "requests", "urllib", "socket", "curl", "wget", "fetch", "web", "dns", "egress", "download"}):
        reversibility, externality, rule_id = Reversibility.EXTERNAL_WORLD, Externality.EXTERNAL, RULE_NETWORK_EGRESS
        reason = "action can reach a host outside the process boundary"
    elif _has_any(tokens, {"deploy", "promote", "git_push", "kubectl", "terraform", "npm_publish", "docker_push"}):
        reversibility, externality, rule_id = Reversibility.EXTERNAL_WORLD, Externality.EXTERNAL, RULE_DEPLOYMENT
        reason = "action changes a deployed or shared surface"
    elif _has_any(tokens, {"send", "message", "notify", "post", "publish", "email", "slack", "telegram", "feishu", "discord"}):
        reversibility, externality, rule_id = Reversibility.EXTERNAL_WORLD, Externality.EXTERNAL, RULE_EXTERNAL_MESSAGE
        reason = "action can affect another principal or external system"
    elif _has_any(tokens, {"exec", "eval", "subprocess", "os_system", "popen", "shell", "bash", "spawn"}):
        reversibility, externality, rule_id = Reversibility.IRREVERSIBLE, Externality.LOCAL, RULE_LOCAL_EXEC
        reason = "arbitrary code or shell execution is not provably reversible"
    elif _has_any(tokens, {"drop", "truncate", "delete", "remove", "unlink", "rmtree", "destroy", "force", "branch", "kill", "revoke", "wipe"}):
        reversibility, externality, rule_id = Reversibility.DESTRUCTIVE_LOCAL, Externality.LOCAL, RULE_LOCAL_DELETE
        reason = "action removes or overwrites state in the local execution boundary"
    elif _has_any(tokens, {"database", "db", "insert", "commit", "rollback", "update", "upsert", "execute_sql", "execute", "begin"}):
        reversibility, externality, rule_id = Reversibility.DESTRUCTIVE_LOCAL, Externality.LOCAL, RULE_DATABASE_MUTATION
        reason = "action mutates durable state whose rollback cannot be proven statically"
    elif _has_any(tokens, {"write", "create", "update", "edit", "append", "mkdir", "save", "install"}):
        reversibility, externality, rule_id = Reversibility.REVERSIBLE, Externality.LOCAL, RULE_REVERSIBLE_WRITE
        reason = "local write is presumed recoverable only when a gate supplies a rollback path"
    elif _has_any(tokens, {"read", "list", "search", "inspect", "status", "get", "show", "view", "grep", "glob", "ls", "health"}):
        reversibility, externality, rule_id = Reversibility.REVERSIBLE, Externality.LOCAL, RULE_REVERSIBLE_READ
        reason = "read/observation action has no declared side effect"
    else:
        reversibility, externality, rule_id = Reversibility.IRREVERSIBLE, Externality.UNKNOWN, RULE_UNRECOGNISED
        reason = "no explicit rule matched the concrete action"

    verdict, verdict_rule_id, verdict_reason = _verdict_for(
        reversibility,
        currently_gated=currently_gated,
        gate=gate,
    )
    return Classification(
        reversibility=reversibility,
        externality=externality,
        rule_id=rule_id,
        reason=reason,
        verdict=verdict,
        verdict_rule_id=verdict_rule_id,
        verdict_reason=verdict_reason,
    )


def _verdict_for(
    reversibility: Reversibility,
    *,
    currently_gated: bool,
    gate: Gate | None,
) -> tuple[Verdict, str, str]:
    if gate is not None and (not gate.can_fail or gate.default_posture is GatePosture.DEFAULT_ALLOW):
        if reversibility is Reversibility.REVERSIBLE:
            return Verdict.AUTO_REPLACEABLE, RULE_VERDICT_APPROVAL_REPLACEABLE, "non-failing gate cannot justify a high-risk autonomous action"
        return Verdict.BLOCKED_BY_DESIGN, RULE_VERDICT_BLOCKED, "gate posture is default-allow or the check cannot fail"
    if reversibility is Reversibility.REVERSIBLE:
        if gate is not None and gate.kind.value == "approval":
            return Verdict.AUTO_REPLACEABLE, RULE_VERDICT_APPROVAL_REPLACEABLE, "reversible action is paused by a replaceable human gate"
        return Verdict.SAFE_UNATTENDED, RULE_VERDICT_REVERSIBLE, "reversible action; no unresolved authority boundary was found"
    return Verdict.REQUIRES_OPERATOR_TOKEN, RULE_VERDICT_OPERATOR, "high-impact or irreversible action is not safe without an operator token"


def classification_rule_documentation() -> dict[str, str]:
    """Return a copy of the rule catalogue for reports and documentation."""

    return dict(sorted(RULE_DOCUMENTATION.items()))


def capability_from_classification(
    *,
    capability_id: str,
    description: str,
    source_file: str,
    source_line: int,
    action: str,
    classification: Classification,
    currently_gated: bool,
    gating: str | None,
    tags: Iterable[str] = (),
    metadata: Mapping[str, Any] | None = None,
) -> Capability:
    """Build a :class:`Capability` without dropping the rule provenance."""

    return Capability(
        id=capability_id,
        description=description,
        source_file=source_file,
        source_line=source_line,
        concrete_action=action,
        reversibility_class=classification.reversibility,
        currently_gated=currently_gated,
        gating=gating,
        externality=classification.externality,
        classification_rule_id=classification.rule_id,
        tags=tuple(sorted({str(tag) for tag in tags})),
        metadata=dict(metadata or {}),
    )


__all__ = [
    "CLASSIFICATION_RULES",
    "RULES",
    "Classification",
    "RULE_DATABASE_MUTATION",
    "RULE_DEPLOYMENT",
    "RULE_DYNAMIC_OPERATION",
    "RULE_EXTERNAL_MESSAGE",
    "RULE_FINANCIAL_ACTION",
    "RULE_LOCAL_DELETE",
    "RULE_LOCAL_EXEC",
    "RULE_NETWORK_EGRESS",
    "RULE_REVERSIBLE_READ",
    "RULE_REVERSIBLE_WRITE",
    "RULE_SECRET_ACCESS",
    "RULE_UNKNOWN",
    "RULE_UNRECOGNISED",
    "RULE_VERDICT_APPROVAL_REPLACEABLE",
    "RULE_VERDICT_BLOCKED",
    "RULE_VERDICT_OPERATOR",
    "RULE_VERDICT_REVERSIBLE",
    "RULE_VERDICT_UNKNOWN",
    "capability_from_classification",
    "classification_rule_documentation",
    "classify_action",
]
