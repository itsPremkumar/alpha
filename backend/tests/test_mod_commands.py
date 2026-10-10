"""Unit tests for mod-registered commands — a /command with no model turn."""

import pytest

from alpha.mods.commands import (
    MAX_OUTPUT_CHARS,
    ModCommandError,
    ModCommandRegistry,
    normalize_command_name,
    run_command,
)
from alpha.mods.kernel import ModKernel


@pytest.fixture
def registry():
    return ModCommandRegistry()


def _noop(**kwargs):
    return None


async def _async_handler(payload):
    return f"ran with {payload.get('q')}"


class TestNameNormalization:
    def test_a_leading_slash_is_stripped(self):
        assert normalize_command_name("/tally") == "tally"

    def test_case_is_folded(self):
        assert normalize_command_name("Tally") == "tally"

    def test_an_empty_name_is_refused(self):
        with pytest.raises(ModCommandError):
            normalize_command_name("")

    def test_a_name_with_a_space_is_refused(self):
        with pytest.raises(ModCommandError, match="Invalid mod command name"):
            normalize_command_name("goal status")

    def test_a_name_with_a_dot_is_refused(self):
        with pytest.raises(ModCommandError):
            normalize_command_name("goal.status")

    def test_a_name_that_starts_with_a_digit_is_refused(self):
        with pytest.raises(ModCommandError):
            normalize_command_name("1tally")

    def test_a_long_name_is_refused(self):
        with pytest.raises(ModCommandError):
            normalize_command_name("a" * 49)


class TestRegistration:
    def test_a_mod_registers_its_own_command(self, registry):
        command = registry.register("mod_a", "tally", lambda **kw: 1, description="count")
        assert command.name == "tally"
        assert command.mod_name == "mod_a"
        assert registry.resolve("tally") is not None

    def test_a_mod_cannot_shadow_another_mods_command(self, registry):
        registry.register("mod_a", "tally", lambda **kw: 1)
        with pytest.raises(ModCommandError, match="already registered by mod 'mod_a'"):
            registry.register("mod_b", "tally", lambda **kw: 2)

    def test_a_mod_may_re_register_its_own_command(self, registry):
        registry.register("mod_a", "tally", lambda **kw: 1)
        registry.register("mod_a", "tally", lambda **kw: 2)
        assert len(registry.list_commands("mod_a")) == 1

    def test_an_anonymous_mod_cannot_register(self, registry):
        with pytest.raises(ModCommandError, match="must be named"):
            registry.register("", "tally", lambda **kw: 1)

    def test_a_non_callable_handler_is_refused(self, registry):
        with pytest.raises(ModCommandError, match="callable handler"):
            registry.register("mod_a", "tally", "not callable")

    def test_only_the_owner_may_unregister(self, registry):
        registry.register("mod_a", "tally", lambda **kw: 1)
        assert registry.unregister("mod_b", "tally") is False
        assert registry.unregister("mod_a", "tally") is True

    def test_unregistering_a_mod_drops_every_command_it_owned(self, registry):
        registry.register("mod_a", "one", lambda **kw: 1)
        registry.register("mod_a", "two", lambda **kw: 2)
        registry.register("mod_b", "three", lambda **kw: 3)

        assert registry.unregister_mod("mod_a") == 2
        assert [c.name for c in registry.list_commands()] == ["three"]

    def test_resolving_an_unknown_name_is_none(self, registry):
        assert registry.resolve("nope") is None

    def test_listing_is_scoped_and_sorted(self, registry):
        registry.register("mod_b", "zeta", lambda **kw: 1)
        registry.register("mod_a", "alpha", lambda **kw: 1)
        registry.register("mod_a", "beta", lambda **kw: 1)

        assert [c.name for c in registry.list_commands()] == ["alpha", "beta", "zeta"]
        assert [c.name for c in registry.list_commands("mod_a")] == ["alpha", "beta"]

    def test_clear_empties_the_registry(self, registry):
        registry.register("mod_a", "one", lambda **kw: 1)
        assert registry.clear() == 1
        assert registry.list_commands() == []


class TestExecution:
    @pytest.mark.asyncio
    async def test_a_sync_handler_runs(self):
        assert await run_command(lambda: "done", {}) == "done"

    @pytest.mark.asyncio
    async def test_an_async_handler_runs(self):
        assert await run_command(_async_handler, {"q": "x"}) == "ran with x"

    @pytest.mark.asyncio
    async def test_a_zero_argument_handler_is_called_without_a_payload(self):
        calls = []

        def handler():
            calls.append(True)
            return "ok"

        assert await run_command(handler, {"unused": 1}) == "ok"
        assert calls == [True]


class TestKernelIntegration:
    def test_a_kernel_owns_the_registry(self):
        kernel = ModKernel()
        assert kernel.commands.list_commands() == []

    def test_unregistering_a_mod_drops_its_commands(self):
        kernel = ModKernel()

        class _Mod:
            name = "cmd_mod"
            version = "1.0.0"
            priority = 2000
            required_capabilities = {"commands:register"}
            subscribed_events = None

            async def handle(self, ctx, event, next_fn):
                return await next_fn(event)

        kernel.register_mod(_Mod(), granted_capabilities={"commands:register"})
        kernel.commands.register("cmd_mod", "hello", lambda **kw: "hi")

        assert len(kernel.commands.list_commands("cmd_mod")) == 1
        kernel.unregister_mod("cmd_mod")
        assert kernel.commands.list_commands("cmd_mod") == []


class TestOutputBound:
    def test_output_bound_is_generous_but_finite(self):
        assert 1000 < MAX_OUTPUT_CHARS <= 10_000
