"""Unit tests for SecDefaultMod — the first-loaded control-chain guard."""

import pytest

from alpha.mods.kernel import ModKernel, _register_builtin_enforcers
from alpha.mods.manifest import GUARDED_PRIORITY_CEILING
from alpha.mods.sec_default import SecDefaultMod
from alpha.mods.types import AlphaEvent, CorrelationContext, EventOutcome, EventResult, ModPriority


class ExternalMod:
    """A user-installed mod: its module path is not ``alpha.mods.*``."""

    name = "external_example"
    version = "1.0.0"
    priority = int(ModPriority.USER_EXTENSIONS)
    required_capabilities: set[str] = {"evidence:record"}
    subscribed_events = {"tool.requested"}

    async def handle(self, ctx, event, next_fn):
        return await next_fn(event)


class SecurityTierExternalMod(ExternalMod):
    """An external mod that wants to outrank the security triad."""

    name = "external_security_tier"
    priority = int(ModPriority.SECURITY)


def _event(name="tool.requested", **payload):
    return AlphaEvent(name=name, payload=payload, correlation=CorrelationContext.create(run_id="run_1"))


def test_external_mod_registers_above_the_guarded_ceiling():
    kernel = ModKernel()
    _register_builtin_enforcers(kernel)
    kernel.register_mod(ExternalMod(), granted_capabilities={"evidence:record"})

    assert kernel.get_mod("external_example") is not None


def test_external_mod_cannot_take_a_security_tier_seat():
    kernel = ModKernel()
    _register_builtin_enforcers(kernel)

    with pytest.raises(Exception, match="PRIORITY_CEILING"):
        kernel.register_mod(SecurityTierExternalMod(), granted_capabilities=set())
    assert kernel.get_mod("external_security_tier") is None


def test_guard_ceiling_is_the_security_priority():
    assert GUARDED_PRIORITY_CEILING == int(ModPriority.SECURITY)


def test_external_mod_cannot_be_granted_an_undeclared_capability():
    kernel = ModKernel()
    _register_builtin_enforcers(kernel)

    with pytest.raises(Exception, match="UNDECLARED_GRANT"):
        kernel.register_mod(ExternalMod(), granted_capabilities={"evidence:record", "tools:read"})


def test_external_mod_cannot_hold_the_estop_authority():
    kernel = ModKernel()
    _register_builtin_enforcers(kernel)

    class EstopMod(ExternalMod):
        name = "external_estop"
        required_capabilities = {"estop:control"}

    with pytest.raises(Exception, match="RESERVED_CAPABILITY"):
        kernel.register_mod(EstopMod(), granted_capabilities={"estop:control"})


def test_first_party_mods_are_exempt_from_the_ceiling():
    kernel = ModKernel()
    _register_builtin_enforcers(kernel)
    guard = kernel.get_mod("sec_default")

    assert guard.priority <= GUARDED_PRIORITY_CEILING
    assert kernel.control_chain()[0]["mod"] == "sec_default"


def test_refusals_are_recorded_for_operator_inspection():
    kernel = ModKernel()
    _register_builtin_enforcers(kernel)
    guard = kernel.get_mod("sec_default")

    with pytest.raises(Exception):
        kernel.register_mod(SecurityTierExternalMod(), granted_capabilities=set())

    refusals = guard.refusals()
    assert refusals
    assert refusals[-1]["code"] == "PRIORITY_CEILING"
    assert guard.status()["refusal_count"] >= 1


@pytest.mark.asyncio
async def test_external_answer_on_a_gating_event_is_denied():
    kernel = ModKernel()
    _register_builtin_enforcers(kernel)

    class AnsweringExternalMod(ExternalMod):
        name = "external_answerer"
        priority = int(ModPriority.USER_EXTENSIONS)

        async def handle(self, ctx, event, next_fn):
            return EventResult.answer(event, response_payload={"output": "fake result"})

    kernel.register_mod(AnsweringExternalMod(), granted_capabilities=set())
    result = await kernel.dispatch(_event("tool.requested", tool_name="run_command", tool_args={"command": "ls"}))

    assert result.outcome == EventOutcome.DENY
    assert "SEC_DEFAULT_GATING" in result.reason
    assert result.metadata.get("external_answerer") == "external_answerer"


@pytest.mark.asyncio
async def test_first_party_answer_on_a_gating_event_is_allowed():
    """TaskRouterMod is first-party and answers ``task.routed``; the guard must not block it."""
    kernel = ModKernel()
    _register_builtin_enforcers(kernel)

    result = await kernel.dispatch(_event("task.routed", objective="write a haiku about tests"))

    # Either answered by the router or passed through — never denied by the guard.
    assert result.outcome != EventOutcome.DENY or "SEC_DEFAULT_GATING" not in (result.reason or "")


@pytest.mark.asyncio
async def test_non_gating_answer_from_external_code_is_allowed():
    """A mod answering its own feature event is a feature, not a policy decision."""
    kernel = ModKernel()
    _register_builtin_enforcers(kernel)

    class ReplayExternalMod(ExternalMod):
        name = "external_replay"
        subscribed_events = {"replay.requested"}

        async def handle(self, ctx, event, next_fn):
            return EventResult.answer(event, response_payload={"steps": []})

    kernel.register_mod(ReplayExternalMod(), granted_capabilities=set())
    result = await kernel.dispatch(_event("replay.requested", turn_id="t1"))

    assert result.outcome == EventOutcome.ANSWER


def test_status_projects_the_control_chain():
    kernel = ModKernel()
    _register_builtin_enforcers(kernel)
    guard = kernel.get_mod("sec_default")

    status = guard.status()
    assert status["installed"] is True
    assert status["first_mod"] == "sec_default"
    assert status["control_chain_length"] == len(kernel.list_mods())
    assert "tool.requested" in status["gating_events"]


def test_unregistering_a_mod_drops_its_commands_and_state():
    kernel = ModKernel()
    _register_builtin_enforcers(kernel)

    class CommandMod(ExternalMod):
        name = "external_commands"
        priority = int(ModPriority.USER_EXTENSIONS)
        # Declared, so the grant the test passes is a grant the mod asked for.
        required_capabilities = {"commands:register"}
        subscribed_events = {"session.start"}

        async def handle(self, ctx, event, next_fn):
            return await next_fn(event)

    mod = CommandMod()
    kernel.register_mod(mod, granted_capabilities={"commands:register"})
    kernel.commands.register("external_commands", "hello", lambda: "hi", description="say hi")

    assert len(kernel.commands.list_commands("external_commands")) == 1
    kernel.unregister_mod("external_commands")
    assert kernel.commands.list_commands("external_commands") == []


def test_bare_guard_has_no_kernel_and_still_reports_status():
    guard = SecDefaultMod()
    status = guard.status()
    assert status["installed"] is False
    assert status["control_chain_length"] == 0
    assert status["first_mod"] is None
