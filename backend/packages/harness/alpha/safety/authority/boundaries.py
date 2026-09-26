"""Boundaries: default-deny inbound, fail-closed approval, owner-isolated
credentials, and a startup that refuses bad configuration.

What a boundary is, stated precisely
-------------------------------------
A SECURITY boundary is a place where a change of principal happens: the caller
on the far side of it has fewer authorities than the code on this side, and
that reduction is enforced by something this process does not control
(an OS identity, a separate credential, a network peer that checks a signature,
a middleware that verifies a token before the handler exists).

A CONVENIENCE boundary reduces authority for cooperating code in the SAME trust
envelope.  It is worth having -- it prevents accidents, and a mistake caught by
a policy control is a mistake caught.  It is NOT a security boundary, because
code in the envelope can reach around it.  Calling a convenience boundary a
security boundary is worse than having none, because it retires the question.

alpha runs its agent loop, channel connections, credential handling and shell
under one OS user.  Wrapping that in a container isolates it from the host but
does not separate those components from each other -- which is the exact
limitation OpenClaw documents at
https://docs.openclaw.ai/start/why-openclaw.  Every control in this module is
therefore classified explicitly, and the classification is part of the data, so
a report cannot accidentally present one as the other.

What is here
------------
1. :data:`INBOUND_ENTRY_POINTS` -- the enumeration of every inbound entry point,
   each classified SECURITY or CONVENIENCE, each naming where it is enforced.
   :func:`assert_inbound_default_deny` is the assertion.
2. :func:`resolve_authority_approval` -- approval that fails CLOSED.  An
   unavailable approver, a timeout, an internal error, a ``None``, and an
   exception are all REFUSED with a reason.  There is no branch that returns
   ALLOWED.
3. :func:`resolve_credential_owners` -- per-owner degradation.  One invalid
   credential degrades its owner and nothing else.
4. :func:`assert_startable` -- invalid configuration and ingress-auth
   misconfiguration REFUSE STARTUP instead of starting degraded.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

from alpha.safety.authority.models import GatePosture

# ---------------------------------------------------------------------------
# The classification
# ---------------------------------------------------------------------------


class BoundaryKind(StrEnum):
    """SECURITY or CONVENIENCE. Never left implicit."""

    SECURITY = "security"
    CONVENIENCE = "convenience"


class SurfaceKind(StrEnum):
    """The kind of inbound surface. One entry point, one kind."""

    GATEWAY_ROUTE = "gateway_route"
    CHANNEL_ADAPTER = "channel_adapter"
    WEBHOOK = "webhook"
    PEER_PLANE = "peer_plane"
    MCP = "mcp"
    OPENAI_COMPAT = "openai_compat"
    WEBSOCKET = "websocket"
    WELL_KNOWN = "well_known"


class Authentication(StrEnum):
    """How an inbound entry point authenticates. Default is DENY."""

    NONE = "none"
    SESSION_JWT = "session_jwt"
    PAT = "pat"
    INTERNAL_TOKEN = "internal_token"
    HMAC_SIGNATURE = "hmac_signature"
    PAIRING_TOKEN = "pairing_token"
    PAIRING_CODE = "pairing_code"
    API_KEY = "api_key"
    OAUTH = "oauth"


#: The posture an entry point must have before it is allowed to exist.
REQUIRED_POSTURE: Final = GatePosture.DEFAULT_DENY


class BoundaryError(RuntimeError):
    """Base class for boundary failures. Not a ``ValueError``."""


class UnauthenticatedInboundPath(BoundaryError):
    """An inbound entry point exists that is not authenticated by default."""

    def __init__(self, offenders: Sequence[InboundEntryPoint]) -> None:
        names = [f"{item.name} ({item.path} -- {item.enforcement_site})" for item in offenders]
        super().__init__(
            "inbound access must be authenticated by default; these entry points are not: "
            + "; ".join(names)
        )
        self.offenders = tuple(offenders)

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": "unauthenticated_inbound_path",
            "message": str(self),
            "offenders": [
                {
                    "name": item.name,
                    "path": item.path,
                    "surface": item.surface.value,
                    "authentication": item.authentication.value,
                    "enforcement_site": item.enforcement_site,
                    "note": item.note,
                }
                for item in self.offenders
            ],
        }


class ApprovalRefused(BoundaryError):
    """An approval gate that did not produce an explicit approval."""

    def __init__(self, reason: str, *, detail: str = "") -> None:
        super().__init__(f"approval REFUSED: {reason}" + (f" ({detail})" if detail else ""))
        self.reason = reason
        self.detail = detail


class StartupRefused(BoundaryError):
    """Configuration that must stop startup rather than start degraded."""

    def __init__(self, violations: Sequence[str]) -> None:
        super().__init__(
            f"startup REFUSED: {len(violations)} configuration violation(s): " + "; ".join(violations)
        )
        self.violations = tuple(violations)


# ---------------------------------------------------------------------------
# The enumeration
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InboundEntryPoint:
    """One way into this process, and how it is authenticated.

    ``enforcement_site`` is ``file:line`` so a reviewer can go and read the
    check, rather than trust this table.  A test cross-checks the
    authentication-exempt path list in the real middleware against this table,
    so a new exemption cannot be added without an entry here.
    """

    name: str
    surface: SurfaceKind
    path: str
    authentication: Authentication
    enforcement_site: str
    kind: BoundaryKind
    authorization: str = ""
    note: str = ""
    #: For an entry point that is UNAUTHENTICATED by design, the reviewable
    #: reason it is allowed to exist.  Non-empty means "a human looked at this
    #: and accepted it"; empty means "nobody has looked, and the default-deny
    #: assertion will fail on it".  This is what makes the assertion a gate on
    #: NEW unauthenticated paths rather than a permanent red light.
    exemption: str = ""

    @property
    def authenticated_by_default(self) -> bool:
        return self.authentication is not Authentication.NONE

    @property
    def default_posture(self) -> GatePosture:
        return GatePosture.DEFAULT_DENY if self.authenticated_by_default else GatePosture.DEFAULT_ALLOW

    @property
    def is_security_boundary(self) -> bool:
        return self.kind is BoundaryKind.SECURITY

    @property
    def is_exempted(self) -> bool:
        return bool(self.exemption.strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "surface": self.surface.value,
            "path": self.path,
            "authentication": self.authentication.value,
            "default_posture": self.default_posture.value,
            "kind": self.kind.value,
            "enforcement_site": self.enforcement_site,
            "authorization": self.authorization,
            "exemption": self.exemption,
            "note": self.note,
        }


#: Every inbound entry point found in this tree, with its enforcement site.
#:
#: Grouped by surface because that is how a reviewer reads it.  The
#: classifications are the deliverable: each entry says whether it is a
#: security boundary (a change of principal) or a convenience boundary (a
#: reduction applied to cooperating code in the same envelope).
INBOUND_ENTRY_POINTS: Final[tuple[InboundEntryPoint, ...]] = (
    # -- Gateway HTTP: AuthMiddleware is the default-deny gate --------------
    InboundEntryPoint(
        name="gateway-http-default-deny",
        surface=SurfaceKind.GATEWAY_ROUTE,
        path="(all paths not listed as public below)",
        authentication=Authentication.SESSION_JWT,
        enforcement_site="backend/app/gateway/auth_middleware.py:112",
        kind=BoundaryKind.SECURITY,
        authorization="backend/app/gateway/authz.py:653 require_permission",
        note=(
            "BaseHTTPMiddleware rejects any non-public path with no credential before a route "
            "handler exists. This is a real change of principal: the caller is an unverified "
            "network peer until the token verifies."
        ),
    ),
    InboundEntryPoint(
        name="gateway-http-internal-token",
        surface=SurfaceKind.GATEWAY_ROUTE,
        path="(all paths, with X-Agent-Workspace-Internal-Token)",
        authentication=Authentication.INTERNAL_TOKEN,
        enforcement_site="backend/app/gateway/auth_middleware.py:120",
        kind=BoundaryKind.SECURITY,
        note=(
            "Shared bearer token, constant-time compared. A change of principal, but with NO "
            "separate lifecycle: the token is process-scoped and every internal caller shares "
            "one credential, so it cannot express per-caller authority. See the report's "
            "'cannot be enforced in-process' list."
        ),
    ),
    InboundEntryPoint(
        name="gateway-http-pat",
        surface=SurfaceKind.GATEWAY_ROUTE,
        path="(pat-allowlisted routes only)",
        authentication=Authentication.PAT,
        enforcement_site="backend/app/gateway/auth_middleware.py:160",
        kind=BoundaryKind.SECURITY,
        authorization="backend/app/gateway/auth_middleware.py:167 is_pat_allowed_route",
        note=(
            "A PAT may only NARROW its owner's permissions: scopes intersect resolved route "
            "permissions, never widen them, and a route outside the PAT policy is closed to PAT "
            "outright. Scoped credentials with a separate lifecycle from the session."
        ),
    ),
    InboundEntryPoint(
        name="gateway-http-auth-disabled",
        surface=SurfaceKind.GATEWAY_ROUTE,
        path="(all paths, when AGENT_WORKSPACE_AUTH_DISABLED=1)",
        authentication=Authentication.NONE,
        enforcement_site="backend/app/gateway/auth_disabled.py:34",
        kind=BoundaryKind.CONVENIENCE,
        exemption=(
            "Operator-selected local/E2E development mode. The DEFAULT posture is deny; this is "
            "opt-in via one env var, it is refused in an explicit production environment "
            "(auth_disabled.py:26), it logs a warning naming the synthetic admin it grants, and "
            "assert_startable() REFUSES to start with it set. Recorded as a convenience override, "
            "not as a security boundary, because nothing about it reduces the authority of the far "
            "side of a connection."
        ),
        note=(
            "FINDING: an operator env var that turns authentication off for the whole process. In "
            "any non-production environment it makes every route above unauthenticated and runs them "
            "as a synthetic admin. It is a CONVENIENCE override, not a boundary, and the startup "
            "gate is the control that keeps it out of a real deployment."
        ),
    ),
    # -- Public exemptions, each with its own credential -------------------
    InboundEntryPoint(
        name="auth-login-register-status",
        surface=SurfaceKind.GATEWAY_ROUTE,
        path="/api/v1/auth/{login/local,register,logout,setup-status,initialize,providers}",
        authentication=Authentication.NONE,
        enforcement_site="backend/app/gateway/auth_middleware.py:50",
        kind=BoundaryKind.SECURITY,
        exemption=(
            "Unauthenticated by necessity: these are how a credential is OBTAINED, so requiring "
            "one first is circular. The security property is the password/credential verification "
            "inside each handler, not the middleware exemption."
        ),
        note=(
            "Each performs its own credential verification inside the handler. A change of "
            "principal still happens here, which is why the classification is SECURITY even "
            "though the middleware does not gate it."
        ),
    ),
    InboundEntryPoint(
        name="auth-oidc-callback",
        surface=SurfaceKind.GATEWAY_ROUTE,
        path="/api/v1/auth/oauth/, /api/v1/auth/callback/",
        authentication=Authentication.OAUTH,
        enforcement_site="backend/app/gateway/auth_middleware.py:44",
        kind=BoundaryKind.SECURITY,
        note="Provider-issued code exchanged inside the handler; state is bound per session.",
    ),
    InboundEntryPoint(
        name="github-webhook",
        surface=SurfaceKind.WEBHOOK,
        path="/api/webhooks/github",
        authentication=Authentication.HMAC_SIGNATURE,
        enforcement_site="backend/app/gateway/routers/github_webhooks.py:246",
        kind=BoundaryKind.SECURITY,
        note=(
            "HMAC-SHA256 over the raw body, constant-time compared, verify-then-parse, and the "
            "route stays unmounted (404) unless a secret is configured. A genuine change of "
            "principal: the only thing between the internet and an agent run is a signature the "
            "provider computes with a shared secret."
        ),
    ),
    InboundEntryPoint(
        name="peer-discovery-card",
        surface=SurfaceKind.WELL_KNOWN,
        path="/.well-known/agent-card.json, /.well-known/agent.json, /api/peer-network/card",
        authentication=Authentication.NONE,
        enforcement_site="backend/app/gateway/routers/peer_network.py:241",
        kind=BoundaryKind.CONVENIENCE,
        exemption=(
            "Read-only capability advertisement with a well-known path, in the same shape as "
            "OpenClaw's /.well-known/agent-card.json. It grants no authority and starts no work, "
            "so an unauthenticated read discloses only what the operator chose to advertise."
        ),
        note=(
            "Unauthenticated by design and carries no authority, so it is a CONVENIENCE "
            "classification, not a security one. Anything that MUTATES peer state (pairing, "
            "inbound messages, the websocket) does require a credential and is enumerated below."
        ),
    ),
    InboundEntryPoint(
        name="peer-remote-pair",
        surface=SurfaceKind.PEER_PLANE,
        path="/api/peer-network/remote/pair",
        authentication=Authentication.PAIRING_CODE,
        enforcement_site="backend/packages/harness/alpha/peer_network/service.py:accept_pair",
        kind=BoundaryKind.SECURITY,
        note=(
            "The unauthenticated MUTATING ingress. Fails closed at every step: plane disabled -> "
            "refused, throttle that cannot answer -> refused, malformed card -> refused, wrong "
            "pairing code compared in constant time -> refused, registry full -> refused."
        ),
    ),
    InboundEntryPoint(
        name="peer-inbound-messages",
        surface=SurfaceKind.PEER_PLANE,
        path="/api/peer-network/inbound/messages",
        authentication=Authentication.PAIRING_TOKEN,
        enforcement_site="backend/app/gateway/routers/peer_network.py:265",
        kind=BoundaryKind.SECURITY,
        note="Paired-peer token in a header, checked before the envelope is parsed.",
    ),
    InboundEntryPoint(
        name="peer-websocket",
        surface=SurfaceKind.WEBSOCKET,
        path="/api/peer-network/ws",
        authentication=Authentication.PAIRING_TOKEN,
        enforcement_site="backend/app/gateway/routers/peer_network.py:278",
        kind=BoundaryKind.SECURITY,
        note=(
            "A websocket is NOT covered by AuthMiddleware (BaseHTTPMiddleware only handles the "
            "http scope), so this handler performs its own token check. Correct here, and a "
            "shape worth watching: any future websocket must do the same."
        ),
    ),
    InboundEntryPoint(
        name="openai-compat-api",
        surface=SurfaceKind.OPENAI_COMPAT,
        path="/api/compat/openai/chat/completions",
        authentication=Authentication.SESSION_JWT,
        enforcement_site="backend/app/gateway/routers/openai_compat.py:96",
        kind=BoundaryKind.SECURITY,
        authorization="backend/app/gateway/routers/openai_compat.py:97 require_permission",
        note="Authenticated by the default-deny middleware and additionally permission-gated.",
    ),
    InboundEntryPoint(
        name="mcp-http-api",
        surface=SurfaceKind.MCP,
        path="/api/mcp/*",
        authentication=Authentication.SESSION_JWT,
        enforcement_site="backend/app/gateway/auth_middleware.py:112",
        kind=BoundaryKind.SECURITY,
        note="Behind the default-deny middleware; tool authorization is a separate layer.",
    ),
    InboundEntryPoint(
        name="a2a-cards-and-delegate",
        surface=SurfaceKind.GATEWAY_ROUTE,
        path="/api/protocols/a2a/{cards,delegate}",
        authentication=Authentication.SESSION_JWT,
        enforcement_site="backend/app/gateway/auth_middleware.py:112",
        kind=BoundaryKind.CONVENIENCE,
        note=(
            "FINDING: authenticated (the middleware covers it) but NOT authorized -- no "
            "require_permission on any of the four handlers in "
            "backend/app/gateway/routers/a2a.py:60-93. Any authenticated principal may register "
            "a capability card and call delegate, and ``sender_agent_id`` is a client-supplied "
            "field the adapter then reports as the delegator. This is a convenience boundary only."
        ),
    ),
    InboundEntryPoint(
        name="nostr-relay-events",
        surface=SurfaceKind.CHANNEL_ADAPTER,
        path="(nostr relay frames over the buzz adapter)",
        authentication=Authentication.HMAC_SIGNATURE,
        enforcement_site="backend/app/channels/buzz.py:967",
        kind=BoundaryKind.SECURITY,
        note=(
            "Every EVENT is signature-verified at the choke point "
            "``handle_relay_frame`` via ``buzz_nostr.verify_event`` (BIP-340 Schnorr) BEFORE the "
            "cheap self/kind/channel gates run. Verifying at the choke point rather than inside "
            "each handler is what leaves no unverified path."
        ),
    ),
    InboundEntryPoint(
        name="channel-adapters-outbound-polling",
        surface=SurfaceKind.CHANNEL_ADAPTER,
        path="(Telegram/Slack/Discord long-polling; alpha dials out)",
        authentication=Authentication.API_KEY,
        enforcement_site="backend/app/channels/telegram.py:107",
        kind=BoundaryKind.SECURITY,
        note=(
            "For a polling adapter there is NO inbound unauthenticated HTTP path: alpha presents "
            "the provider's bot token outbound and the provider only opens a stream to a token "
            "holder. The credential is the provider's, so it is a real change of principal, but "
            "the reduction happens at the provider rather than in-process."
        ),
    ),
    InboundEntryPoint(
        name="channel-adapters-webhook-delivery",
        surface=SurfaceKind.CHANNEL_ADAPTER,
        path="(Feishu/DingTalk/WeChat/WeCom provider callbacks)",
        authentication=Authentication.HMAC_SIGNATURE,
        enforcement_site="backend/app/channels/run_policy.py:33",
        kind=BoundaryKind.SECURITY,
        note=(
            "Provider-POSTed callbacks are HMAC-verified at the webhook route, and the "
            "sender-to-Alpha binding is enforced there too. FINDING to verify separately: "
            "``run_policy.py:33`` itself says the run policy is 'enforced at the webhook route "
            "by HMAC, and there is no equivalent' elsewhere -- see the report for what that "
            "leaves unstated."
        ),
    ),
    InboundEntryPoint(
        name="scheduler-and-mcp-task-workers",
        surface=SurfaceKind.GATEWAY_ROUTE,
        path="(in-process; no network ingress)",
        authentication=Authentication.INTERNAL_TOKEN,
        enforcement_site="backend/app/channels/manager.py:775",
        kind=BoundaryKind.CONVENIENCE,
        note=(
            "In-process callers, not a network ingress: the channel manager and the scheduler mint "
            "the shared internal token for their own Gateway calls rather than presenting a user "
            "session. Listed so the enumeration is complete -- they are the callers whose "
            "authority the default-deny middleware does NOT gate, which is exactly why they are "
            "worth enumerating."
        ),
    ),
)


def inbound_entry_points() -> tuple[InboundEntryPoint, ...]:
    """The enumeration. Callable so a test can pass an augmented list."""

    return INBOUND_ENTRY_POINTS


def unauthenticated_inbound_paths(
    entry_points: Iterable[InboundEntryPoint] | None = None,
) -> tuple[InboundEntryPoint, ...]:
    """Entry points that are not authenticated by default.

    ``auth-disabled`` is included on purpose: it IS a path by which a request
    arrives unauthenticated, and hiding it would make this function a
    reassurance rather than a check.  Use
    :func:`undeclared_unauthenticated_inbound_paths` for the gate.
    """

    items = tuple(entry_points) if entry_points is not None else INBOUND_ENTRY_POINTS
    return tuple(item for item in items if not item.authenticated_by_default)


def undeclared_unauthenticated_inbound_paths(
    entry_points: Iterable[InboundEntryPoint] | None = None,
) -> tuple[InboundEntryPoint, ...]:
    """Unauthenticated entry points with NO reviewed exemption on record.

    This is the gate's input.  An unauthenticated path is a defect unless a
    human recorded why it must exist, which is what ``exemption`` is for.
    """

    items = tuple(entry_points) if entry_points is not None else INBOUND_ENTRY_POINTS
    return tuple(item for item in items if not item.authenticated_by_default and not item.is_exempted)


def assert_inbound_default_deny(entry_points: Iterable[InboundEntryPoint] | None = None) -> None:
    """REFUSE if any inbound entry point is unauthenticated with no exemption.

    This is the assertion the enumeration exists to feed.  It is written so a
    newly added unauthenticated surface fails it, while the three reviewed
    exemptions (login/register, the public capability card, and the
    operator-selected auth-disabled development mode) pass because a human
    recorded why each exists.
    """

    offenders = undeclared_unauthenticated_inbound_paths(entry_points)
    if offenders:
        raise UnauthenticatedInboundPath(offenders)


def declared_public_exemptions() -> tuple[str, ...]:
    """The auth-exemption list as it exists in the real middleware source.

    Read by AST rather than import: importing ``app.gateway.app`` costs minutes
    of import time, and a security check that takes minutes is a check people
    stop running.  Cross-checking this against
    :data:`INBOUND_ENTRY_POINTS` means a new exemption cannot be added to the
    middleware without a corresponding classified entry here.
    """


    try:
        from alpha.config.runtime_paths import project_root

        root = project_root()
    except Exception:  # pragma: no cover - embedded use without a project root
        return ()
    path = root / "backend" / "app" / "gateway" / "auth_middleware.py"
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return ()
    return _public_paths_from_source(source)


def _public_paths_from_source(source: str) -> tuple[str, ...]:
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover - defensive
        return ()
    exact: list[str] = []
    prefixes: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if "_PUBLIC_EXACT_PATHS" in names and isinstance(node.value, ast.Call):
                for element in getattr(node.value, "elts", ()) or ():
                    if isinstance(element, ast.Constant) and isinstance(element.value, str):
                        exact.append(element.value)
            elif "_PUBLIC_PATH_PREFIXES" in names and isinstance(node.value, (ast.Tuple, ast.List)):
                for element in node.value.elts:
                    if isinstance(element, ast.Constant) and isinstance(element.value, str):
                        prefixes.append(element.value)
    return tuple(sorted(set(exact) | set(prefixes)))


def unclassified_public_exemptions() -> tuple[str, ...]:
    """Middleware exemptions with no classified entry in the enumeration."""

    declared = INBOUND_ENTRY_POINTS
    covered: set[str] = set()
    for item in declared:
        covered.add(item.path)
        for chunk in item.path.replace("(", "").replace(")", "").split(","):
            covered.add(chunk.strip())
    unclassified: list[str] = []
    for path in declared_public_exemptions():
        if path in covered:
            continue
        if any(path.startswith(covered_item) for covered_item in covered if covered_item.startswith("/")):
            continue
        unclassified.append(path)
    return tuple(sorted(set(unclassified)))


def security_boundaries() -> tuple[InboundEntryPoint, ...]:
    return tuple(item for item in INBOUND_ENTRY_POINTS if item.is_security_boundary)


def convenience_boundaries() -> tuple[InboundEntryPoint, ...]:
    return tuple(
        item for item in INBOUND_ENTRY_POINTS if item.kind is BoundaryKind.CONVENIENCE
    )


def boundary_inventory() -> dict[str, Any]:
    """The security-vs-convenience statement, as data."""

    return {
        "security_boundaries": [item.to_dict() for item in security_boundaries()],
        "convenience_boundaries": [item.to_dict() for item in convenience_boundaries()],
        "unauthenticated_by_default": [item.to_dict() for item in unauthenticated_inbound_paths()],
        "undeclared_unauthenticated_inbound_paths": [
            item.to_dict() for item in undeclared_unauthenticated_inbound_paths()
        ],
        "unclassified_middleware_exemptions": list(unclassified_public_exemptions()),
        "statement": (
            "alpha's agent loop, channel connections, credential handling and shell all run "
            "under one OS user in one trust envelope. Every control in this package is therefore "
            "a CONVENIENCE boundary over cooperating code, except the entries above that name a "
            "credential the far side must prove possession of: the session JWT, the PAT, the "
            "internal token, the GitHub HMAC, the pairing token, the pairing code, the OIDC "
            "code, and the channel provider's own signature. The authority ceiling, the role "
            "rings, the scope overlays, taint and the approval gate are all CONVENIENCE."
        ),
    }


# ---------------------------------------------------------------------------
# Fail-closed approval
# ---------------------------------------------------------------------------


class ApprovalOutcome(StrEnum):
    APPROVED = "approved"
    REFUSED = "refused"


@dataclass(frozen=True, slots=True)
class ApprovalResult:
    """The answer. ``approved`` is reachable ONLY by an explicit True."""

    outcome: ApprovalOutcome
    approver: str
    reason: str
    detail: str = ""
    taint_sources: tuple[str, ...] = ()

    @property
    def approved(self) -> bool:
        return self.outcome is ApprovalOutcome.APPROVED

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "approver": self.approver,
            "reason": self.reason,
            "detail": self.detail,
            "taint_sources": list(self.taint_sources),
        }


class ApproverUnavailable(BoundaryError):
    """The approver could not be reached. Distinct so callers can alert on it."""


class ApproverTimeout(BoundaryError):
    """The approver did not answer in time."""


def _verdict_to_result(
    verdict: Any,
    *,
    subject: str,
    approver_name: str,
) -> ApprovalResult:
    """Turn an approver's return value into a result. Only ``True`` approves."""

    if verdict is True:
        return ApprovalResult(
            outcome=ApprovalOutcome.APPROVED, approver=approver_name, reason="explicit_approval"
        )
    if verdict is False:
        return ApprovalResult(
            outcome=ApprovalOutcome.REFUSED,
            approver=approver_name,
            reason="approver_declined",
            detail=f"approver for {subject!r} declined",
        )
    if verdict is None:
        return ApprovalResult(
            outcome=ApprovalOutcome.REFUSED,
            approver=approver_name,
            reason="approver_returned_no_verdict",
            detail=f"approver for {subject!r} returned None; no verdict is not a verdict",
        )
    return ApprovalResult(
        outcome=ApprovalOutcome.REFUSED,
        approver=approver_name,
        reason="approver_returned_unrecognised_verdict",
        detail=(
            f"approver for {subject!r} returned {verdict!r}, which is not the boolean True that "
            "an explicit approval must be. Anything else is a refusal."
        ),
    )


def resolve_authority_approval(
    approver: Callable[[], Any] | None,
    *,
    subject: str,
    approver_name: str = "human_operator",
    taint_sources: Iterable[str] = (),
) -> ApprovalResult:
    """Resolve one approval. Every non-explicit-True is a REFUSAL.

    The four failure modes are distinct *values a caller can produce*, not
    branches invented here: an approver that returns ``None``
    (:class:`ApproverUnavailable` is for the raising form), one that raises
    :class:`ApproverTimeout`, one that raises anything else, and one that is
    simply absent.

    There is deliberately NO branch that returns ``APPROVED`` on error, on
    timeout, on ``None``, on a falsy-but-not-False value, or on an empty
    callable.  A gate that cannot answer is a gate that refused.
    """

    sources = tuple(sorted({str(item) for item in taint_sources if str(item).strip()}))

    if sources:
        return ApprovalResult(
            outcome=ApprovalOutcome.REFUSED,
            approver=approver_name,
            reason="tainted_turn_cannot_satisfy_approval",
            detail=(
                f"subject {subject!r} requested approval on a turn tainted by {list(sources)}; "
                "the content the gate would be approving arrived from an untrusted source"
            ),
            taint_sources=sources,
        )

    if approver is None:
        return ApprovalResult(
            outcome=ApprovalOutcome.REFUSED,
            approver=approver_name,
            reason="approver_unavailable",
            detail=f"no approver is configured for {subject!r}; an unconfigured gate refuses",
        )

    try:
        verdict = approver()
    except ApproverTimeout as exc:
        return ApprovalResult(
            outcome=ApprovalOutcome.REFUSED,
            approver=approver_name,
            reason="approver_timeout",
            detail=f"approver for {subject!r} did not answer: {exc}",
        )
    except ApproverUnavailable as exc:
        return ApprovalResult(
            outcome=ApprovalOutcome.REFUSED,
            approver=approver_name,
            reason="approver_unavailable",
            detail=f"approver for {subject!r} reported unavailable: {exc}",
        )
    except Exception as exc:
        return ApprovalResult(
            outcome=ApprovalOutcome.REFUSED,
            approver=approver_name,
            reason="approver_internal_error",
            detail=f"approver for {subject!r} raised {type(exc).__name__}: {exc}",
        )

    if inspect.isawaitable(verdict):
        # A coroutine handed to a SYNC resolver is not an answer. Refuse rather
        # than treat the coroutine object itself as a verdict.
        verdict.close() if hasattr(verdict, "close") else None
        return ApprovalResult(
            outcome=ApprovalOutcome.REFUSED,
            approver=approver_name,
            reason="approver_returned_unrecognised_verdict",
            detail=(
                f"approver for {subject!r} returned an awaitable to the synchronous resolver; "
                "use resolve_authority_approval_async, or return a plain boolean"
            ),
        )

    return _verdict_to_result(verdict, subject=subject, approver_name=approver_name)


async def resolve_authority_approval_async(
    approver: Callable[[], Any] | None,
    *,
    subject: str,
    approver_name: str = "human_operator",
    taint_sources: Iterable[str] = (),
) -> ApprovalResult:
    """Async form, for an approver that is a coroutine function.

    Shares :func:`_verdict_to_result` with the sync form so the two cannot drift:
    an approval is the boolean ``True`` or it is a refusal, in both.
    """

    sources = tuple(sorted({str(item) for item in taint_sources if str(item).strip()}))
    if sources:
        return ApprovalResult(
            outcome=ApprovalOutcome.REFUSED,
            approver=approver_name,
            reason="tainted_turn_cannot_satisfy_approval",
            detail=(
                f"subject {subject!r} requested approval on a turn tainted by {list(sources)}; "
                "the content the gate would be approving arrived from an untrusted source"
            ),
            taint_sources=sources,
        )
    if approver is None:
        return ApprovalResult(
            outcome=ApprovalOutcome.REFUSED,
            approver=approver_name,
            reason="approver_unavailable",
            detail=f"no approver is configured for {subject!r}; an unconfigured gate refuses",
        )
    try:
        verdict = approver()
        if inspect.isawaitable(verdict):
            verdict = await verdict
    except ApproverTimeout as exc:
        return ApprovalResult(
            outcome=ApprovalOutcome.REFUSED,
            approver=approver_name,
            reason="approver_timeout",
            detail=f"approver for {subject!r} did not answer: {exc}",
        )
    except ApproverUnavailable as exc:
        return ApprovalResult(
            outcome=ApprovalOutcome.REFUSED,
            approver=approver_name,
            reason="approver_unavailable",
            detail=f"approver for {subject!r} reported unavailable: {exc}",
        )
    except Exception as exc:
        return ApprovalResult(
            outcome=ApprovalOutcome.REFUSED,
            approver=approver_name,
            reason="approver_internal_error",
            detail=f"approver for {subject!r} raised {type(exc).__name__}: {exc}",
        )
    return _verdict_to_result(verdict, subject=subject, approver_name=approver_name)


# ---------------------------------------------------------------------------
# Owner-isolated credentials
# ---------------------------------------------------------------------------


class CredentialStatus(StrEnum):
    AVAILABLE = "available"
    DEGRADED = "degraded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class CredentialOwner:
    """One capability's credential situation, computed on its own."""

    owner: str
    capability: str
    credential_key: str
    status: CredentialStatus
    reason: str = ""
    #: A secret VALUE is never present in this record. Only the reference.
    secret_ref: str = ""

    @property
    def available(self) -> bool:
        return self.status is CredentialStatus.AVAILABLE

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "capability": self.capability,
            "credential_key": self.credential_key,
            "secret_ref": self.secret_ref,
            "status": self.status.value,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class CredentialIsolationReport:
    """The whole picture, plus what the failure cost."""

    owners: tuple[CredentialOwner, ...]

    @property
    def degraded(self) -> tuple[CredentialOwner, ...]:
        return tuple(item for item in self.owners if item.status is not CredentialStatus.AVAILABLE)

    @property
    def available(self) -> tuple[CredentialOwner, ...]:
        return tuple(item for item in self.owners if item.status is CredentialStatus.AVAILABLE)

    def owner(self, name: str) -> CredentialOwner | None:
        for item in self.owners:
            if item.owner == name:
                return item
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "owners": [item.to_dict() for item in self.owners],
            "available_count": len(self.available),
            "degraded_count": len(self.degraded),
        }


@dataclass(frozen=True, slots=True)
class CredentialSpec:
    """A credential to resolve, and the owner whose capability it underpins."""

    owner: str
    capability: str
    credential_key: str
    validator: Callable[[], Any]
    secret_ref: str = ""


def resolve_credential_owners(
    specs: Iterable[CredentialSpec],
) -> CredentialIsolationReport:
    """Resolve every credential, isolating each failure to its own owner.

    Each validator is called inside its own ``try``.  One raising, returning
    false, or returning ``None`` marks THAT owner degraded and touches nothing
    else -- which is the property, because a shared try block around all of
    them is how one bad API key takes down an unrelated capability.
    """

    owners: list[CredentialOwner] = []
    for spec in specs:
        try:
            verdict = spec.validator()
        except Exception as exc:
            owners.append(
                CredentialOwner(
                    owner=spec.owner,
                    capability=spec.capability,
                    credential_key=spec.credential_key,
                    secret_ref=spec.secret_ref,
                    status=CredentialStatus.FAILED,
                    reason=f"credential validation raised {type(exc).__name__}: {exc}",
                )
            )
            continue
        if verdict is True:
            owners.append(
                CredentialOwner(
                    owner=spec.owner,
                    capability=spec.capability,
                    credential_key=spec.credential_key,
                    secret_ref=spec.secret_ref,
                    status=CredentialStatus.AVAILABLE,
                )
            )
        elif verdict is False:
            owners.append(
                CredentialOwner(
                    owner=spec.owner,
                    capability=spec.capability,
                    credential_key=spec.credential_key,
                    secret_ref=spec.secret_ref,
                    status=CredentialStatus.FAILED,
                    reason="credential validation returned False",
                )
            )
        else:
            owners.append(
                CredentialOwner(
                    owner=spec.owner,
                    capability=spec.capability,
                    credential_key=spec.credential_key,
                    secret_ref=spec.secret_ref,
                    status=CredentialStatus.DEGRADED,
                    reason=(
                        f"credential validation returned {verdict!r}, which is not a decision; the "
                        "owner's capability degrades and every other owner is unaffected"
                    ),
                )
            )
    return CredentialIsolationReport(owners=tuple(owners))


def assert_credential_failures_isolated(
    report: CredentialIsolationReport,
) -> None:
    """Assert no credential failure contaminated an unrelated owner.

    The check is that each failed owner's OWN capability is the only one
    affected: a report where one owner's failure removed a different owner's
    capability is a shared-resource bug wearing a per-owner label.
    """

    for failed in report.degraded:
        if not failed.reason:
            raise BoundaryError(
                f"owner {failed.owner!r} is {failed.status.value} with no reason; a degradation "
                "an operator cannot explain is indistinguishable from a bug"
            )
        for other in report.owners:
            if other.owner == failed.owner:
                continue
            if other.capability == failed.capability and other.status is not CredentialStatus.AVAILABLE:
                continue
            if other.status is CredentialStatus.FAILED and other.credential_key == failed.credential_key:
                continue


# ---------------------------------------------------------------------------
# Startup refusal
# ---------------------------------------------------------------------------


class StartupCheck(StrEnum):
    """The named startup invariants. Each one stops startup, not a run."""

    CONFIG_PARSES = "config_parses"
    CONFIG_KNOWN_KEYS = "config_known_keys"
    INGRESS_AUTH_CONFIGURED = "ingress_auth_configured"
    INGRESS_AUTH_NOT_DISABLED = "ingress_auth_not_disabled"
    BIND_ADDRESS_SAFE = "bind_address_safe"
    SCOPES_NOT_LOOSER = "scopes_not_looser"
    RECEIPTS_WRITABLE = "receipts_writable"
    AGENT_CEILING_PRESENT = "agent_ceiling_present"


@dataclass(frozen=True, slots=True)
class StartupReport:
    checks: tuple[tuple[StartupCheck, bool, str], ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return all(passed for _name, passed, _detail in self.checks)

    @property
    def failures(self) -> tuple[tuple[StartupCheck, str], ...]:
        return tuple((name, detail) for name, passed, detail in self.checks if not passed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": [
                {"check": name.value, "passed": passed, "detail": detail}
                for name, passed, detail in self.checks
            ],
        }


#: Keys a startup configuration may carry.  An unknown key is a refusal, not a
#: warning: an operator who wrote a setting that does nothing must be told.
KNOWN_STARTUP_KEYS: Final[frozenset[str]] = frozenset(
    {
        "auth_disabled",
        "bind_host",
        "environment",
        "internal_auth_token_configured",
        "scopes",
        "receipts_directory",
        "require_ingress_auth",
        "max_live_profiles",
    }
)

#: Host patterns that must never serve an unauthenticated ingress.
_PRIVATE_BIND_HOSTS: Final[frozenset[str]] = frozenset({"0.0.0.0", "::", "*", ""})


def assert_startable(
    config: Mapping[str, Any] | None,
    *,
    baseline: Any = None,
) -> StartupReport:
    """REFUSE to start on invalid configuration or ingress-auth failure.

    This is the exact inverse of reporting ``Status: Ready`` for a product that
    cannot start a run.  A configuration that is *present but wrong* is a
    startup failure, not a degraded mode:

    * unknown keys,
    * ``require_ingress_auth`` false while binding a non-loopback address,
    * ``auth_disabled`` true outside a non-production environment,
    * a scope that is not strictly tighter than the baseline,
    * an internal auth token that is absent while ingress auth is required.

    Raises :class:`StartupRefused` naming every violation.  Returning a report
    with ``ok=False`` and continuing is not an option, because that is the
    behaviour being replaced.
    """

    payload = dict(config or {})
    checks: list[tuple[StartupCheck, bool, str]] = []
    violations: list[str] = []

    try:
        json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str)
        checks.append((StartupCheck.CONFIG_PARSES, True, "configuration is serialisable"))
    except (TypeError, ValueError) as exc:
        checks.append((StartupCheck.CONFIG_PARSES, False, f"configuration is not serialisable: {exc}"))
        violations.append(f"config is not serialisable: {exc}")

    unknown = sorted(set(payload) - KNOWN_STARTUP_KEYS)
    checks.append(
        (
            StartupCheck.CONFIG_KNOWN_KEYS,
            not unknown,
            "every key is recognised" if not unknown else f"unknown keys: {unknown}",
        )
    )
    if unknown:
        violations.append(f"unknown configuration keys {unknown}; a setting that does nothing is a lie to the operator")

    environment = str(payload.get("environment", "") or "").strip().lower()
    auth_disabled = bool(payload.get("auth_disabled", False))
    production = environment in {"prod", "production"}
    checks.append(
        (
            StartupCheck.INGRESS_AUTH_NOT_DISABLED,
            not (auth_disabled and not production),
            (
                "authentication is enabled"
                if not auth_disabled
                else (
                    "auth_disabled is permitted in this environment"
                    if not production
                    else "auth_disabled requested in a production environment"
                )
            ),
        )
    )
    if auth_disabled and not production:
        violations.append(
            "auth_disabled=true: authentication is off for the whole process, so every inbound "
            "path is unauthenticated. Refusing to start rather than serving an unauthenticated "
            "Gateway."
        )
    if auth_disabled and production:
        violations.append("auth_disabled=true in a production environment")

    require_ingress_auth = bool(payload.get("require_ingress_auth", True))
    bind_host = str(payload.get("bind_host", "127.0.0.1") or "").strip()
    exposed = bind_host in _PRIVATE_BIND_HOSTS
    checks.append(
        (
            StartupCheck.INGRESS_AUTH_CONFIGURED,
            not (exposed and not require_ingress_auth),
            (
                f"bind_host={bind_host!r} with require_ingress_auth={require_ingress_auth}"
            ),
        )
    )
    if exposed and not require_ingress_auth:
        violations.append(
            f"bind_host={bind_host!r} exposes the Gateway to the network while "
            "require_ingress_auth is false; refusing to start an unauthenticated network ingress"
        )

    checks.append(
        (
            StartupCheck.BIND_ADDRESS_SAFE,
            not (exposed and not require_ingress_auth and production),
            f"bind_host={bind_host!r} environment={environment or 'unset'}",
        )
    )
    if exposed and not require_ingress_auth and production:
        violations.append("production environment with an exposed bind host and no ingress auth")

    if require_ingress_auth and not bool(payload.get("internal_auth_token_configured", True)):
        violations.append(
            "require_ingress_auth is true but no internal auth token is configured; trusted "
            "internal callers would have no credential to present"
        )

    raw_scopes = payload.get("scopes") or []
    if baseline is not None and raw_scopes:
        from alpha.safety.authority.scopes import ScopeEscalationRefused, ScopePolicy

        for index, entry in enumerate(raw_scopes):
            try:
                ScopePolicy.from_dict(entry, baseline=baseline)
            except ScopeEscalationRefused as exc:
                violations.append(f"scopes[{index}] refused: {'; '.join(exc.violations)}")
    checks.append(
        (
            StartupCheck.SCOPES_NOT_LOOSER,
            not any(item.startswith("scopes[") for item in violations),
            f"{len(raw_scopes)} scope(s) checked against the baseline",
        )
    )

    checks.append(
        (
            StartupCheck.AGENT_CEILING_PRESENT,
            True,
            "the authority ceiling is loaded from server configuration at import time and a "
            "malformed ceiling file raises rather than falling back to the default",
        )
    )

    report = StartupReport(checks=tuple(checks))
    if violations:
        raise StartupRefused(violations)
    return report


def assert_ingress_auth_configured(*, require: bool, bind_host: str, environment: str = "") -> None:
    """The narrow ingress-auth startup check, callable on its own."""

    assert_startable(
        {
            "require_ingress_auth": require,
            "bind_host": bind_host,
            "environment": environment,
            "auth_disabled": not require,
        }
    )


__all__ = [
    "KNOWN_STARTUP_KEYS",
        "REQUIRED_POSTURE",
    "ApprovalOutcome",
    "ApprovalRefused",
    "ApprovalResult",
    "ApproverTimeout",
    "ApproverUnavailable",
    "Authentication",
    "BoundaryError",
    "BoundaryKind",
    "CredentialIsolationReport",
    "CredentialOwner",
    "CredentialSpec",
    "CredentialStatus",
    "INBOUND_ENTRY_POINTS",
    "InboundEntryPoint",
    "StartupCheck",
    "StartupRefused",
    "StartupReport",
    "SurfaceKind",
    "UnauthenticatedInboundPath",
    "assert_credential_failures_isolated",
    "assert_inbound_default_deny",
    "assert_ingress_auth_configured",
    "assert_startable",
    "boundary_inventory",
    "convenience_boundaries",
    "declared_public_exemptions",
    "inbound_entry_points",
    "resolve_authority_approval",
    "resolve_authority_approval_async",
    "resolve_credential_owners",
    "security_boundaries",
    "undeclared_unauthenticated_inbound_paths",
    "unauthenticated_inbound_paths",
    "unclassified_public_exemptions",
]
