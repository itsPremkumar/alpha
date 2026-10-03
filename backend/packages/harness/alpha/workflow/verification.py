"""Honest execution of a node's declared ``verification_cmd``.

Before this module existed, ``alpha.workflow.dynamic_decomposer`` wrote a
``verification_cmd`` onto every task it produced and
``alpha.workflow.dynamic_bridge`` copied it into ``WorkflowNode.config`` — and
nothing in the tree ever read it back. A run could therefore report success
while the plan it was executing still named the check that was supposed to
prove that success. This module is the consumer that was missing.

**The declaration is client-supplied input.** ``POST /api/workflows`` accepts
``body.graph`` verbatim and ``update_node_config`` writes straight into
``node.config``, so *any authenticated caller* can put any string they like in
``verification_cmd``. Shelling that string out would be authenticated remote
code execution, so the engine never spawns a process and never imports an
arbitrary module. A declaration only ever resolves to one of three things:

1. a verifier the **host registered** (:meth:`alpha.workflow.runtime.DynamicWorkflowEngine.register_verifier`);
2. a dotted path inside the **allowlisted** ``alpha.`` prefix — the codebase the
   Gateway already imports, never ``os.system`` or a third-party module;
3. a shell-style command, which is handed to a **host-bound executor** and is
   *not run at all* when no executor is bound.

Everything else resolves to nothing and is reported ``unresolved``.

**Status semantics.** ``failed`` and ``unresolved`` **block completion** — a
verifier that said no, or a declaration naming something that does not exist,
can never be folded into a success. ``not_run`` does **not** block: it is the
honest "this did not execute" state, mirroring the compensation contract where
no callback bound means ``executed: False`` on a node that still completes. It
is emitted as ``not_run`` and never as ``passed``.

**Verdict contract.** A verifier takes no arguments and returns a ``bool``. A
``dict`` carrying a boolean ``passed``/``ok``/``success``/``verdict`` key and
the usual pass/fail strings are coerced; anything else — including ``None``,
which Python functions return by default — yields ``not_run`` rather than a
guess. An exception raised while running is ``not_run`` too: no verdict was
obtained, so none is claimed.

The blocking rule is deliberately narrow so that a *host limitation* (no
executor bound) never fails a node, while a *broken declaration* does.
"""

from __future__ import annotations

import importlib
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, NamedTuple

__all__ = [
    "ALLOWED_VERIFIER_PREFIX",
    "BLOCKING_STATUSES",
    "VerificationExecutor",
    "VerificationOutcome",
    "VerificationStatus",
    "Verifier",
    "declared_command",
    "run_verification",
]

#: Only a dotted path under this prefix may be imported and called. It is the
#: package the Gateway already trusts, so a declaration can reach reviewed code
#: but never ``os``, ``subprocess`` or an operator-installed module.
ALLOWED_VERIFIER_PREFIX = "alpha."


class VerificationStatus(StrEnum):
    """What actually happened to a declared verification."""

    #: No ``verification_cmd`` on this node — nothing to do, nothing claimed.
    NOT_DECLARED = "not_declared"
    #: Resolved, executed, and reported a truthy verdict.
    PASSED = "passed"
    #: Resolved, executed, and reported a falsy verdict. Blocks.
    FAILED = "failed"
    #: Declared but naming nothing that exists. Blocks: a typo must not become
    #: "no gate".
    UNRESOLVED = "unresolved"
    #: Resolved to something real but could not be executed here (no host
    #: executor, the callable needs arguments, or it produced no interpretable
    #: verdict). Never blocks, never claims a pass.
    NOT_RUN = "not_run"


BLOCKING_STATUSES = frozenset({VerificationStatus.FAILED, VerificationStatus.UNRESOLVED})

#: A host-provided command runner. It receives the declared command string and
#: returns a verdict using the same contract as a verifier callable. Binding
#: one is an explicit operator/host decision, exactly like
#: ``bind_domain_executors`` — the engine itself never spawns a process.
VerificationExecutor = Callable[[str], Any]

#: A verifier takes no arguments and returns a bool-ish verdict.
Verifier = Callable[[], Any]

# A dotted ``pkg.mod.attr`` declaration. Anything with shell syntax in it does
# not match and falls through to the command branch instead.
_DOTTED_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+$")
# Shell-ish characters: a declaration containing any of these is a *command*,
# not a symbol name, and therefore needs a bound executor to run.
_COMMAND_RE = re.compile(r"[\s|;&><$`\\\"'(){}\[\]*?!~#]")
_VERDICT_TRUE = frozenset({"pass", "passed", "ok", "true", "yes", "success"})
_VERDICT_FALSE = frozenset({"fail", "failed", "false", "no", "error", "reject", "rejected"})
_VERDICT_KEYS = ("passed", "ok", "success", "verdict")


@dataclass(frozen=True)
class VerificationOutcome:
    """The result of resolving and, where possible, running a declaration."""

    status: VerificationStatus
    command: str | None
    reason: str
    resolved_via: str | None = None
    evidence: str | None = None
    duration_ms: float | None = None

    @property
    def declared(self) -> bool:
        """Whether a command was declared at all."""
        return self.command is not None

    @property
    def blocks_completion(self) -> bool:
        """True only when the node must not be allowed to complete."""
        return self.status in BLOCKING_STATUSES

    def to_dict(self) -> dict[str, Any]:
        """Serialise every disclosure field — no consumer has to guess."""
        return {
            "status": self.status.value,
            "command": self.command,
            "reason": self.reason,
            "resolved_via": self.resolved_via,
            "evidence": self.evidence,
            "duration_ms": self.duration_ms,
        }


class _Resolution(NamedTuple):
    kind: str | None  # "callable" | "command" | None
    target: Any
    via: str | None
    reason: str


def declared_command(config: Mapping[str, Any] | None) -> str | None:
    """Read ``verification_cmd`` off a node's config, or ``None``.

    Non-strings are treated as absent rather than stringified: a list of argv
    or a number is not something this contract defines, and guessing at it
    would be the first step toward executing something nobody declared.
    """
    if not config:
        return None
    raw = config.get("verification_cmd")
    if not isinstance(raw, str):
        return None
    return raw.strip() or None


def _resolve(command: str, registry: Mapping[str, Verifier] | None) -> _Resolution:
    """Decide what a declaration refers to. Never executes anything."""
    if registry:
        registered = registry.get(command)
        if registered is not None:
            if not callable(registered):
                return _Resolution(None, None, None, f"the registered verifier for '{command}' is not callable")
            return _Resolution("callable", registered, "registry", "registered verifier")

    if _DOTTED_RE.match(command):
        if not command.startswith(ALLOWED_VERIFIER_PREFIX):
            # The load-bearing refusal: ``os.system`` and friends are dotted
            # and importable, and would otherwise be reachable from a request body.
            return _Resolution(
                None,
                None,
                None,
                f"resolved to nothing: the dotted path is outside the allowlisted '{ALLOWED_VERIFIER_PREFIX}' prefix",
            )
        module_name, _, attr = command.rpartition(".")
        try:
            target = getattr(importlib.import_module(module_name), attr)
        except Exception as exc:  # noqa: BLE001 - every failure mode is disclosed, none swallowed
            return _Resolution(None, None, None, f"resolved to nothing: '{command}' could not be loaded ({type(exc).__name__}: {exc})")
        if not callable(target):
            return _Resolution(None, None, None, f"resolved to '{command}' but it is not callable")
        return _Resolution("callable", target, "dotted", "imported allowlisted path")

    if _COMMAND_RE.search(command):
        return _Resolution("command", command, "executor", "host-executed command")

    return _Resolution(
        None,
        None,
        None,
        f"resolved to nothing: '{command}' is not a registered verifier, an '{ALLOWED_VERIFIER_PREFIX}'-prefixed path, or a command",
    )


def _coerce_verdict(result: Any) -> bool | None:
    """Interpret a verifier's return value, or admit we cannot."""
    if isinstance(result, bool):
        return result
    if isinstance(result, dict):
        for key in _VERDICT_KEYS:
            value = result.get(key)
            if isinstance(value, bool):
                return value
        return None
    if isinstance(result, str):
        token = result.strip().lower()
        if token in _VERDICT_TRUE:
            return True
        if token in _VERDICT_FALSE:
            return False
    return None


def _elapsed_ms(started: float) -> float:
    return round((time.monotonic() - started) * 1000, 3)


def run_verification(
    command: str | None,
    *,
    registry: Mapping[str, Verifier] | None = None,
    executor: VerificationExecutor | None = None,
) -> VerificationOutcome:
    """Resolve ``command`` and run it when — and only when — it is safe to.

    This function is the entire trust boundary: it imports no module outside
    the allowlisted prefix, invokes no subprocess, and reports ``not_run``
    rather than guessing whenever execution or interpretation did not happen.
    """
    if not isinstance(command, str) or not command.strip():
        return VerificationOutcome(
            status=VerificationStatus.NOT_DECLARED,
            command=None,
            reason="no verification command is declared on this node",
        )

    text = command.strip()
    started = time.monotonic()
    resolution = _resolve(text, registry)

    if resolution.kind is None:
        return VerificationOutcome(
            status=VerificationStatus.UNRESOLVED,
            command=text,
            reason=resolution.reason,
            duration_ms=_elapsed_ms(started),
        )

    if resolution.kind == "command" and executor is None:
        # Resolvable in principle; this host simply has no runner bound. The
        # compensation contract's precedent: disclose, do not fail.
        return VerificationOutcome(
            status=VerificationStatus.NOT_RUN,
            command=text,
            resolved_via="executor",
            reason="the command was not run: no verification executor is bound in this host",
            duration_ms=_elapsed_ms(started),
        )

    try:
        raw = executor(text) if resolution.kind == "command" else resolution.target()
    except Exception as exc:  # noqa: BLE001 - the real failure is the disclosure
        return VerificationOutcome(
            status=VerificationStatus.NOT_RUN,
            command=text,
            resolved_via=resolution.via,
            reason=f"the declared verifier could not be run ({type(exc).__name__}: {exc})",
            duration_ms=_elapsed_ms(started),
        )

    verdict = _coerce_verdict(raw)
    if verdict is None:
        return VerificationOutcome(
            status=VerificationStatus.NOT_RUN,
            command=text,
            resolved_via=resolution.via,
            reason=f"the declared verifier ran but produced no interpretable verdict (got {type(raw).__name__})",
            duration_ms=_elapsed_ms(started),
        )

    if verdict:
        return VerificationOutcome(
            status=VerificationStatus.PASSED,
            command=text,
            resolved_via=resolution.via,
            reason="the declared verifier reported a passing verdict",
            evidence=f"verification '{text}' passed via {resolution.via} in {_elapsed_ms(started)}ms",
            duration_ms=_elapsed_ms(started),
        )

    return VerificationOutcome(
        status=VerificationStatus.FAILED,
        command=text,
        resolved_via=resolution.via,
        reason=f"the declared verifier '{text}' reported a failing verdict",
        duration_ms=_elapsed_ms(started),
    )
