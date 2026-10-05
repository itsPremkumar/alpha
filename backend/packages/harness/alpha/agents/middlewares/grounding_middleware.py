"""Middleware that puts the grounding layer on the agent's actual path.

## Why this file exists at all

Everything in :mod:`alpha.grounding` could have been a library that nothing
called, and this repository has an entire module
(:mod:`alpha.capabilities.honesty`) dedicated to the fact that this has happened
before: capability imported cleanly, passed unit tests, was documented as a
feature, and had no production caller. So the wiring is the deliverable, and this
is the wiring point.

## The prompt-layer trust boundary, applied

`agents/AGENTS.md` requires every string entering a model context to declare a
source, and that the source's trust level decide its channel. The capability
manifest is **framework-owned authority** — it is derived from registries the
process actually holds — so it rides the system channel. Anything
model-influenceable in the grounding block (a claim's text, a reason string) does
**not**, and would not be allowed to, even though it is convenient: a claim the
model itself wrote is exactly the content that must not be able to grant itself
authority. :meth:`GroundingMiddleware._build_manifest_reminder` therefore renders
only registry-derived content, and the ledger's blocking claims ride a hidden
``HumanMessage`` data block where the model can read them without being able to
treat them as policy.

## Inject once, not every turn

The base system prompt is kept fully static for prefix-cache reuse, and the
manifest changes only when a registry changes. So it is injected once per
conversation and carried in history thereafter, using the same
``additional_kwargs`` marker discipline as ``DynamicContextMiddleware``. A
per-turn injection would put ~12 capability lines in every request forever.

## Gates run at tool dispatch, not at turn end

An end-of-turn check is structurally too late: a wrong decision at span 3 has
already propagated by the time an answer exists, and 36.9% of *successful*
trajectories contain a process error. ``wrap_tool_call`` is the last point where a
refusal can still prevent the step rather than describe it.

## Fail-open on the *infrastructure*, fail-closed on the *check*

These are different failures and are handled differently on purpose:

* The middleware cannot build a manifest (a registry raised, config is missing) →
  warn and continue. A broken context enrichment must not take every run down.
* The manifest is built and a check says a tool does not exist → block. Reporting
  that as unverified would be the exact defect this package removes.

A crashing gate is treated as a **block**, never as a pass. A check that did not
run has verified nothing, and "no result" is not "no problem".
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, override

from alpha_extension_api import ContentKind, provenance_kwargs
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langgraph.runtime import Runtime

from alpha.grounding.effort import WorkOutcome
from alpha.grounding.gates import GatePipeline, GateSubject
from alpha.grounding.manifest import CapabilityManifest, build_manifest
from alpha.grounding.models import GateLayer, GateResult
from alpha.grounding.service import GroundingService

if TYPE_CHECKING:
    from langchain.agents.middleware import ToolCallRequest

    from alpha.config.app_config import AppConfig

logger = logging.getLogger(__name__)

#: Server-owned marker on the injected manifest, so a later turn recognises its
#: own injection instead of re-injecting. Client input is stripped of these keys
#: at the Gateway boundary, so this cannot be forged from a request.
GROUNDING_MANIFEST_KEY = "grounding_manifest_reminder"
#: Marker for the hidden claim block. Separate from the manifest marker because
#: the two are refreshed on different schedules.
GROUNDING_CLAIMS_KEY = "grounding_claims_block"

__all__ = ["GroundingMiddleware", "GROUNDING_CLAIMS_KEY", "GROUNDING_MANIFEST_KEY"]


class GroundingMiddleware(AgentMiddleware):
    """Injects the capability manifest and gates every tool call.

    The service is constructed per run rather than per process: the claim ledger
    and the effort controller are trajectory state, and a process-wide instance
    would let two threads argue about the same premise.
    """

    def __init__(
        self,
        *,
        app_config: AppConfig | None = None,
        manifest: CapabilityManifest | None = None,
        service: GroundingService | None = None,
        available_tools: frozenset[str] | None = None,
    ) -> None:
        super().__init__()
        self._app_config = app_config
        # A manifest or service passed in is the caller's assertion about what is
        # installed. Tests use it; production builds one per run below.
        self._manifest = manifest
        self._service = service
        self._available_tools = available_tools
        self._manifest_unavailable_reason: str = ""
        #: Set once the manifest has actually been put in front of the model. This
        #: is the run-level "prior work was consulted" signal the reuse probe
        #: needs: the probe guards *productive* steps, and consulting the map once
        #: at task start is what makes a later write legitimate rather than a
        #: blind re-implementation.
        self._consulted = False

    # -- identity --------------------------------------------------------

    def release_policy_parameters(self) -> dict[str, object]:
        """Declare the grounding policy as this middleware's behaviour identity.

        The policy is JSON-serialisable and excludes any live registry content:
        an assembly-identity hash must not change every time a skill is enabled.
        """
        try:
            from alpha.config.grounding_config import resolve_grounding_config

            config = resolve_grounding_config()
        except Exception:
            # Assembly must not fail because a policy section is unreadable, but the
            # degradation is disclosed rather than silent: a middleware whose policy
            # is unknown is a middleware whose behaviour identity is unknown, and that
            # is worth a log line rather than a quiet sentinel.
            logger.warning("GroundingMiddleware: policy unreadable; declaring an unresolved assembly identity", exc_info=True)
            return {"grounding": "unresolved"}
        return {
            "grounding": {
                "enabled": config.enabled,
                "inject_manifest": config.inject_manifest,
                "require_reuse_probe": config.require_reuse_probe,
                "max_steps": config.max_steps,
                "max_consecutive_failures": config.max_consecutive_failures,
                "max_identical_retries": config.max_identical_retries,
                "max_claims": config.max_claims,
                "memory_require_structural_pass": config.memory_require_structural_pass,
            }
        }

    # -- layer 1: manifest assembly --------------------------------------

    def _build_manifest(self) -> CapabilityManifest:
        """Assemble the live manifest, degrading to empty rather than raising.

        An empty manifest renders nothing and the run proceeds with today's
        behaviour. That is the correct degradation for *enrichment*: losing the
        manifest must not cost the run its ability to work. It is emphatically
        **not** the correct behaviour for a gate, which is why the two concerns
        live in different methods with different failure handling.
        """
        if self._manifest is not None:
            return self._manifest
        try:
            from alpha.config.grounding_config import resolve_grounding_config

            config = resolve_grounding_config()
            if not config.enabled:
                self._manifest = CapabilityManifest(gaps=("grounding layer is disabled in config",))
                return self._manifest

            from alpha.capabilities.catalog import CAPABILITY_CATALOG
            from alpha.tools import get_available_tools

            tools: dict[str, Any] = {}
            for tool in get_available_tools():
                name = getattr(tool, "name", None) or getattr(tool, "__name__", None)
                if name:
                    description = (getattr(tool, "description", "") or "").strip()
                    tools[str(name)] = description.splitlines()[0][:160] if description else ""

            skills: dict[str, Any] = {}
            try:
                from alpha.skills import list_skills

                for skill in list_skills() or ():
                    skill_name = getattr(skill, "name", None)
                    if skill_name:
                        skills[str(skill_name)] = str(getattr(skill, "description", "") or "")
            except Exception:
                logger.debug("GroundingMiddleware: skill registry unavailable", exc_info=True)

            gaps: list[str] = []
            if not skills:
                gaps.append("no skills are installed")
            if not tools:
                gaps.append("no tools resolved for this run")

            self._manifest = build_manifest(
                tools=tools,
                skills=skills,
                catalog=CAPABILITY_CATALOG,
                gaps=gaps,
            )
        except Exception as exc:
            self._manifest_unavailable_reason = f"{type(exc).__name__}: {exc}"
            logger.warning("GroundingMiddleware: manifest assembly failed (%s); continuing without it", self._manifest_unavailable_reason)
            self._manifest = CapabilityManifest()
        return self._manifest

    def _service_for(self) -> GroundingService:
        if self._service is not None:
            return self._service
        try:
            from alpha.config.grounding_config import resolve_grounding_config

            config = resolve_grounding_config()
            self._service = GroundingService(
                manifest=self._build_manifest(),
                pipeline=GatePipeline.with_reuse(required=config.require_reuse_probe),
                inject_manifest=config.enabled and config.inject_manifest,
            )
            self._service.effort.max_steps = config.max_steps
            self._service.effort.max_consecutive_failures = config.max_consecutive_failures
            self._service.effort.max_identical_retries = config.max_identical_retries
            self._service.ledger.max_claims = config.max_claims
            self._service.memory.require_structural_pass = config.memory_require_structural_pass
            self._service.memory.decay_window_seconds = config.decay_window_seconds
        except Exception:
            logger.warning("GroundingMiddleware: service construction failed; gates degrade to pass-through", exc_info=True)
            self._service = GroundingService(manifest=self._manifest or CapabilityManifest())
        return self._service

    @property
    def service(self) -> GroundingService:
        """The per-run service. Public so tests and the ops route can read it."""
        return self._service_for()

    # -- injection -------------------------------------------------------

    def _build_manifest_reminder(self, objective: str) -> str | None:
        """Render the manifest for the system channel.

        Registry-derived content only. A claim the model wrote never reaches this
        string, so a premise cannot grant itself authority by being asserted.
        """
        manifest = self._build_manifest()
        if len(manifest) == 0 and not manifest.gaps:
            return None
        rendered = manifest.render_for_prompt(objective)
        return rendered or None

    def _claims_block(self) -> str | None:
        """Render blocking claims for the **untrusted** channel.

        Model-influenceable content, so it is delivered as a hidden
        ``HumanMessage`` data block rather than as system authority. The agent can
        read that it is standing on something unsupported; it cannot read the
        system prompt as granting permission to continue doing so.
        """
        service = self._service_for()
        return service.ledger.render_for_prompt() or None

    @staticmethod
    def _has_marker(messages: list[Any], key: str) -> bool:
        return any(isinstance(m, (SystemMessage, HumanMessage)) and bool(m.additional_kwargs.get(key)) for m in messages)

    def _inject(self, state: dict, runtime: Runtime | None = None) -> dict | None:
        """Build the injection update, or ``None`` when already present.

        Returns messages in a single update rather than mutating state, matching
        the ``before_agent`` contract every other lead middleware follows.
        """
        messages = list(state.get("messages", []) or [])
        if not messages:
            return None
        service = self._service_for()
        if not service.inject_manifest:
            return None

        # The objective is the last human message, which is what the manifest is
        # selected against. Taking it from state rather than the runtime keeps
        # this callable in tests without a Runtime.
        objective = ""
        for message in reversed(messages):
            content = getattr(message, "content", None)
            if isinstance(content, str) and content.strip():
                objective = content.strip()
                break

        new_messages: list[Any] = []
        if not self._has_marker(messages, GROUNDING_MANIFEST_KEY):
            reminder = self._build_manifest_reminder(objective)
            if reminder:
                self._consulted = True
                new_messages.append(
                    SystemMessage(
                        content=reminder,
                        additional_kwargs={
                            "hide_from_ui": True,
                            GROUNDING_MANIFEST_KEY: True,
                            **provenance_kwargs(ContentKind.MIDDLEWARE_INJECTION, "grounding_manifest"),
                        },
                    )
                )
        else:
            # The manifest is ALREADY in the conversation -- a continued thread,
            # a checkpoint reloaded after a restart, or a second run on the same
            # thread. The marker check above short-circuits the injection, which
            # used to leave `_consulted` at its default False, so every later
            # productive step was refused by `check_reuse_probe` while the
            # manifest sat in context and its remediation ("read the capability
            # manifest") could never re-inject one. Observed live on 2026-10-04:
            # an agent looped six refusals deep and reported, correctly, "the
            # gate is still refusing, despite both conditions now being met".
            # The signal is "the map has been put in front of the model", not
            # "the map was sent on this particular turn".
            self._consulted = True
        claims = self._claims_block()
        if claims and not self._has_marker(messages, GROUNDING_CLAIMS_KEY):
            new_messages.append(
                HumanMessage(
                    content=claims,
                    additional_kwargs={
                        "hide_from_ui": True,
                        GROUNDING_CLAIMS_KEY: True,
                        **provenance_kwargs(ContentKind.MIDDLEWARE_INJECTION, "grounding_claims"),
                    },
                )
            )
        return {"messages": new_messages} if new_messages else None

    @override
    def before_agent(self, state: Any, runtime: Runtime) -> dict | None:
        return self._inject(state if isinstance(state, dict) else {"messages": getattr(state, "messages", [])}, runtime)

    @override
    async def abefore_agent(self, state: Any, runtime: Runtime) -> dict | None:
        # No I/O beyond what _build_manifest already did synchronously during
        # assembly; kept async for the contract, not for offloading.
        return self._inject(state if isinstance(state, dict) else {"messages": getattr(state, "messages", [])}, runtime)

    # -- layer 5: gates --------------------------------------------------

    def _subject_for(self, tool_call: dict) -> GateSubject:
        """Build the gate subject for one tool call.

        ``available_tools`` must answer one question: **is this tool installed?**
        The only set that can answer it is the toolset the model was actually
        given, so ``self._available_tools`` -- the assembled runtime set -- is the
        authority, and the manifest is unioned in rather than preferred.

        Observed live on 2026-10-05. A run created with ``autonomous: true`` has
        the delegation tools assembled, the model called ``task``, and the gate
        refused it:

            Grounding gate refused this call: not installed: task use a tool
            from the capability manifest, or escalate rather than substituting
            an invented one

            {"gate": "tool_exists", "code": "unknown_tool"}

        Cause: ``_build_manifest`` calls ``get_available_tools()`` with **no
        arguments**, so ``subagent_enabled`` defaults to False and every
        delegation tool is absent from the manifest -- while the agent's real
        toolset had them. Preferring the manifest therefore answered "installed"
        for a narrower set than the one the model was offered, and the gate
        refused a tool that was demonstrably present.

        The manifest is an interface map for reuse (see
        ``alpha/grounding/AGENTS.md``); it is not an inventory, and an inventory
        built with default flags is not an inventory of a configured run. Unioning
        cannot weaken tool-selection hallucination detection: a fabricated tool is
        in neither set, so it is still refused.
        """
        manifest = self._build_manifest()
        names: set[str] = set()
        if len(manifest):
            names.update(e.name for e in manifest.by_kind("tool"))
        if self._available_tools:
            names.update(self._available_tools)
        return GateSubject(
            available_tools=frozenset(names) if names else None,
            tool_calls=(str(tool_call.get("name") or ""),),
            ledger=self._service_for().ledger,
            reuse_probe_run=self._consulted,
        )

    @staticmethod
    def _blocked_result(tool_call: dict, result: GateResult) -> ToolMessage:
        """Turn a gate block into an error ``ToolMessage``.

        Content carries the reason **and** the remediation. A block that says only
        "not allowed" produces a retry of the same action; a block that says what
        to do instead produces a different next step.
        """
        tool_name = str(tool_call.get("name") or "unknown_tool")
        parts = [f"Grounding gate refused this call: {result.reason or result.code or result.gate}"]
        if result.remediation:
            parts.append(result.remediation)
        if result.layer.value == "model":
            parts.append("note: this was a model-layer advisory and is reported, not enforced.")
        return ToolMessage(
            content=" ".join(parts),
            tool_call_id=str(tool_call.get("id", "") or ""),
            name=tool_name,
            status="error",
            additional_kwargs={"alpha_grounding_block": {"gate": result.gate, "code": result.code, "layer": result.layer.value}},
        )

    def _gate(self, tool_call: dict) -> GateResult | None:
        """Run the pipeline and return the blocking result, if any."""
        service = self._service_for()
        subject = self._subject_for(tool_call)
        try:
            results = service.pipeline.run(subject)
        except Exception:
            # Fail closed. An unrun check has verified nothing.
            logger.exception("GroundingMiddleware: gate pipeline failed; refusing the call")
            return GateResult(
                gate="grounding_pipeline",
                layer=GateLayer.DETERMINISTIC,
                blocked=True,
                code="gate_error",
                reason="the grounding checks could not run",
                remediation="retry, or report that this step is unverifiable",
            )
        # One dispatched call is one unit of effort. Recorded so the brake has a
        # real step count to reason about rather than an always-zero one.
        service.effort.record(WorkOutcome.SUCCESS)
        return service.pipeline.block(results)

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Any],
    ) -> ToolMessage | Any:
        result = self._gate(dict(request.tool_call))
        if result is not None:
            return self._blocked_result(dict(request.tool_call), result)
        return handler(request)

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Any]],
    ) -> ToolMessage | Any:
        result = self._gate(dict(request.tool_call))
        if result is not None:
            return self._blocked_result(dict(request.tool_call), result)
        return await handler(request)
