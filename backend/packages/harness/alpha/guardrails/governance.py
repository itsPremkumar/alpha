"""Governance-backed guardrail provider: enforces ``tool_governance`` at call time.

The discovery catalog discloses a tool's governed risk class; this
provider is the enforcement half. It plugs into the existing
``GuardrailMiddleware`` seam (``guardrails.enabled`` +
``guardrails.provider.use``), which already provides the fail-closed
handling, the authorization-outcome publication, the RunJournal audit
record, and the error ``ToolMessage`` shape — so this module owns only
the decision.

Authority model, deliberately narrow:

* The middleware passes only the tool *name* to a provider — never the
  tool object — so a tool's self-declared ``governance_*`` metadata
  cannot reach this decision. **Enforcement authority is operator
  configuration alone** (``config.yaml -> tool_governance``, handed to
  the provider at construction through the assembly's ``tool_governance``
  hint, or inline under ``guardrails.provider.config.spec``). A tool
  cannot widen itself by declaring metadata, exactly as it cannot by
  declaring ``discovery_permissions``.
* A tool with no operator entry is **allowed** and reported as
  ``unclassified``. Capability filtering (Layer 1 authorization) and
  skill policy have already gated the toolset; this layer enforces the
  operator's explicit per-tool policy, nothing more.
* ``confirmation: block`` denies with ``governance.blocked``.
* ``confirmation: ask`` denies with ``governance.requires_confirmation``
  unless the request arrived through server-internal dispatch
  (``request.is_internal`` — the only confirmation authority that exists
  today; a model or an external client cannot self-approve). An
  interactive human-approval flow is the follow-up; until it exists,
  ask is fail-closed, which is the safe direction.
* ``confirmation: auto`` allows and records the governed class in the
  decision metadata, so the audit trail shows what class ran.

A malformed declaration raises :class:`GovernanceError` at
**construction** (agent assembly), not at call time: a typo in policy
is a startup error, never a mid-run surprise and never a silent
fallback to allow.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from alpha.guardrails.provider import (
    GuardrailDecision,
    GuardrailReason,
    GuardrailRequest,
)
from alpha.tools.governance import (
    ConfirmationPolicy,
    GovernanceError,
    GovernanceRegistry,
    ToolGovernance,
)


class GovernanceGuardrailProvider:
    """Enforces the operator's per-tool governance policy on every call."""

    name = "governance"
    policy_id = "alpha.tool-governance"
    version = "1"

    def __init__(
        self,
        *,
        tool_governance: Mapping[str, Any] | None = None,
        spec: Mapping[str, Any] | None = None,
        ask_requires_internal: bool = True,
        framework: str | None = None,
    ) -> None:
        """Build the enforcement registry.

        ``tool_governance`` is the top-level config section (injected by
        the middleware assembly's hint mechanism); ``spec`` is the
        provider-local declaration under ``guardrails.provider.config``
        and wins per tool name when both name the same tool. ``framework``
        is the assembly's hint and is accepted for interface compatibility.
        """
        merged: dict[str, Any] = dict(tool_governance or {})
        if spec:
            merged.update(spec)
        # Raises GovernanceError on any malformed entry — construction
        # time is the fail-closed point.
        self._registry = GovernanceRegistry.from_mapping(merged)
        self.ask_requires_internal = ask_requires_internal

    # -- GuardrailProvider contract -------------------------------------

    def evaluate(self, request: GuardrailRequest) -> GuardrailDecision:
        """Decide one tool call against the operator's policy."""
        entry = self._registry.get(request.tool_name)
        if entry is None:
            return GuardrailDecision(
                allow=True,
                policy_id=self.policy_id,
                metadata={"governance": "unclassified", "tool": request.tool_name},
            )
        metadata = self._decision_metadata(entry)
        if entry.confirmation is ConfirmationPolicy.BLOCK:
            return GuardrailDecision(
                allow=False,
                reasons=[
                    GuardrailReason(
                        code="governance.blocked",
                        message=(f"tool '{request.tool_name}' is governed as risk_class={entry.risk_class.value} with confirmation=block (reversibility={entry.reversibility.value}); the operator policy refuses this call"),
                    )
                ],
                policy_id=self.policy_id,
                metadata=metadata,
            )
        if entry.confirmation is ConfirmationPolicy.ASK:
            if self.ask_requires_internal and not request.is_internal:
                return GuardrailDecision(
                    allow=False,
                    reasons=[
                        GuardrailReason(
                            code="governance.requires_confirmation",
                            message=(
                                f"tool '{request.tool_name}' is governed as "
                                f"risk_class={entry.risk_class.value} with "
                                "confirmation=ask; no confirmation channel "
                                "approved this call (only server-internal "
                                "dispatch is an approval authority today)"
                            ),
                        )
                    ],
                    policy_id=self.policy_id,
                    metadata=metadata,
                )
            metadata["approved_by"] = "internal_dispatch"
        return GuardrailDecision(
            allow=True,
            policy_id=self.policy_id,
            metadata=metadata,
        )

    async def aevaluate(self, request: GuardrailRequest) -> GuardrailDecision:
        """Async variant; the decision is pure, so no offload is needed."""
        return self.evaluate(request)

    def release_policy_parameters(self) -> dict[str, object]:
        """Declare the governed surface for assembly identity."""
        return {
            "ask_requires_internal": self.ask_requires_internal,
            "governed_tools": {
                name: {
                    "risk_class": entry.risk_class.value,
                    "confirmation": entry.confirmation.value,
                }
                for name, entry in self._registry.entries
            },
        }

    # -- helpers --------------------------------------------------------

    @staticmethod
    def _decision_metadata(entry: ToolGovernance) -> dict[str, Any]:
        return {
            "governance": "classified",
            "risk_class": entry.risk_class.value,
            "reversibility": entry.reversibility.value,
            "confirmation": entry.confirmation.value,
            "provenance": entry.provenance,
        }


__all__ = ["GovernanceError", "GovernanceGuardrailProvider"]
