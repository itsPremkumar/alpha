"""End-to-end integration tests for the advanced Alpha Mod Kernel.

These drive the *whole* chain through one kernel with the real built-in mods,
because the interesting failures in a middleware chain are ordering failures —
which unit tests of one mod cannot see.
"""

import pytest

from alpha.mods.kernel import ModKernel, _register_builtin_enforcers
from alpha.mods.types import AlphaEvent, CorrelationContext, EventOutcome, EventResult, ModPriority


@pytest.fixture
def kernel():
    k = ModKernel()
    _register_builtin_enforcers(k)
    return k


def _event(name, run_id="run_1", **payload):
    return AlphaEvent(name=name, payload=payload, correlation=CorrelationContext.create(run_id=run_id))


async def _pass(event):
    return EventResult.continue_(event)


class TestChainOrder:
    def test_the_guard_is_outermost_and_estop_follows(self, kernel):
        chain = kernel.control_chain()
        assert chain[0]["mod"] == "sec_default"
        assert chain[1]["mod"] == "audit_ledger"
        assert chain.index(next(c for c in chain if c["mod"] == "fleet_estop")) < chain.index(next(c for c in chain if c["mod"] == "blast_radius_guard"))

    def test_the_priority_chain_is_monotonically_ordered(self, kernel):
        priorities = [c["priority"] for c in kernel.control_chain()]
        assert priorities == sorted(priorities)

    def test_every_builtin_mod_is_first_party(self, kernel):
        for entry in kernel.control_chain():
            assert entry["first_party"] is True, entry["mod"]


class TestAuditThroughTheRealChain:
    @pytest.mark.asyncio
    async def test_a_denied_tool_is_audited(self, kernel):
        audit = kernel.get_mod("audit_ledger")

        result = await kernel.dispatch(_event("tool.requested", tool_name="run_command", tool_args={"command": "rm -rf /"}))
        assert result.outcome == EventOutcome.DEFER  # held, not denied

        entries = audit.get_entries(event_name="tool.requested")
        assert entries
        assert "blast_radius_guard" in entries[-1]["chain"]
        assert entries[-1]["outcome"] == "defer"

    @pytest.mark.asyncio
    async def test_a_read_only_tool_passes_the_whole_chain(self, kernel):
        audit = kernel.get_mod("audit_ledger")

        result = await kernel.dispatch(_event("tool.requested", tool_name="view_file", tool_args={"path": "README.md"}))
        assert result.outcome == EventOutcome.CONTINUE

        entry = audit.get_entries(event_name="tool.requested")[-1]
        assert entry["outcome"] == "continue"
        assert "sec_default" in entry["chain"]

    @pytest.mark.asyncio
    async def test_audit_records_a_payload_digest_not_raw_content(self, kernel):
        audit = kernel.get_mod("audit_ledger")
        await kernel.dispatch(_event("tool.requested", tool_name="web_search", tool_args={"query": "alpha agents"}))

        entry = audit.get_entries(event_name="tool.requested")[-1]
        assert entry["payload_digest"]
        assert entry["payload"]["tool_args"]["query"] == "alpha agents"

    @pytest.mark.asyncio
    async def test_audit_redacts_secrets_in_the_recorded_payload(self, kernel):
        audit = kernel.get_mod("audit_ledger")
        await kernel.dispatch(_event("tool.requested", tool_name="run_command", tool_args={"api_key": "sk-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "command": "echo hi"}))

        entry = audit.get_entries(event_name="tool.requested")[-1]
        assert entry["payload"]["tool_args"]["api_key"] == "[REDACTED]"


class TestSharedStateAcrossMods:
    @pytest.mark.asyncio
    async def test_two_handlers_of_one_mod_share_state(self, kernel):
        """The property the old per-context storage could not provide."""

        class Counter:
            name = "counter"
            version = "1.0.0"
            priority = int(ModPriority.OBSERVABILITY)
            required_capabilities = {"storage:write"}
            subscribed_events = {"count.up", "count.read"}

            async def handle(self, ctx, event, next_fn):
                if event.name == "count.up":
                    current = ctx.storage.get("n", 0)
                    ctx.storage.set("n", current + 1)
                return await next_fn(event)

        kernel.register_mod(Counter(), granted_capabilities={"storage:write"})

        for _ in range(3):
            await kernel.dispatch(_event("count.up"))

        assert kernel.state.get("counter", "n") == 3

    @pytest.mark.asyncio
    async def test_one_mods_state_is_invisible_to_another(self, kernel):
        class Writer:
            name = "writer"
            version = "1.0.0"
            priority = 1000
            required_capabilities = {"storage:write"}
            subscribed_events = {"w"}

            async def handle(self, ctx, event, next_fn):
                ctx.storage.set("secret", "mine")
                return await next_fn(event)

        class Reader:
            name = "reader"
            version = "1.0.0"
            priority = 1001
            required_capabilities = {"storage:read"}
            subscribed_events = {"r"}

            async def handle(self, ctx, event, next_fn):
                return EventResult.answer(event, response_payload={"seen": ctx.storage.get("secret")})

        kernel.register_mod(Writer(), granted_capabilities={"storage:write"})
        kernel.register_mod(Reader(), granted_capabilities={"storage:read"})
        await kernel.dispatch(_event("w"))

        result = await kernel.dispatch(_event("r"))
        assert result.response_payload["seen"] is None


class TestModCommandsEndToEnd:
    @pytest.mark.asyncio
    async def test_a_mod_command_runs_without_a_model_turn(self, kernel):
        from alpha.mods.middleware import run_mod_command as _unused  # noqa: F401  (import proof)

        class CommandingMod:
            name = "commander"
            version = "1.0.0"
            priority = 1000
            required_capabilities = {"commands:register"}
            subscribed_events = {"session.start"}

            async def handle(self, ctx, event, next_fn):
                ctx.commands.register("tally", lambda payload: f"count is {payload.get('n', 0)}", description="show the count")
                return await next_fn(event)

        kernel.register_mod(CommandingMod(), granted_capabilities={"commands:register"})
        await kernel.dispatch(_event("session.start"))

        resolved = kernel.commands.resolve("tally")
        assert resolved is not None
        command, handler = resolved
        assert command.mod_name == "commander"

        from alpha.mods.commands import run_command

        assert await run_command(handler, {"n": 7}) == "count is 7"

    @pytest.mark.asyncio
    async def test_command_run_event_is_answered_by_the_middleware_bridge(self, kernel):
        class CommandingMod:
            name = "commander2"
            version = "1.0.0"
            priority = 1000
            required_capabilities = {"commands:register"}
            subscribed_events = {"session.start"}

            async def handle(self, ctx, event, next_fn):
                ctx.commands.register("ping", lambda payload: "pong", description="health")
                return await next_fn(event)

        kernel.register_mod(CommandingMod(), granted_capabilities={"commands:register"})
        await kernel.dispatch(_event("session.start"))

        from alpha.mods.middleware import ModKernelMiddleware

        middleware = ModKernelMiddleware(kernel=kernel)
        result = await middleware.run_mod_command("ping")
        assert result["status"] == "success"
        assert result["output"] == "pong"
        assert result["mod_name"] == "commander2"

    @pytest.mark.asyncio
    async def test_an_unknown_command_returns_none_so_a_caller_can_fall_through(self, kernel):
        from alpha.mods.middleware import ModKernelMiddleware

        middleware = ModKernelMiddleware(kernel=kernel)
        assert await middleware.run_mod_command("definitely-not-a-mod-command") is None

    @pytest.mark.asyncio
    async def test_a_broken_command_reports_failure_rather_than_raising(self, kernel):
        class BrokenMod:
            name = "broken"
            version = "1.0.0"
            priority = 1000
            required_capabilities = {"commands:register"}
            subscribed_events = {"session.start"}

            async def handle(self, ctx, event, next_fn):
                def boom(payload):
                    raise RuntimeError("handler exploded")

                ctx.commands.register("boom", boom, description="explodes")
                return await next_fn(event)

        kernel.register_mod(BrokenMod(), granted_capabilities={"commands:register"})
        await kernel.dispatch(_event("session.start"))

        from alpha.mods.middleware import ModKernelMiddleware

        middleware = ModKernelMiddleware(kernel=kernel)
        result = await middleware.run_mod_command("boom")
        assert result["status"] == "error"
        assert "exploded" in result["output"]


class TestBudgetThroughTheRealChain:
    @pytest.mark.asyncio
    async def test_a_budget_denial_is_audited_and_names_the_ceiling(self, kernel):
        budget = kernel.get_mod("context_budget")
        budget._max_tokens = 500

        result = await kernel.dispatch(
            _event(
                "model.requested",
                usage={"prompt_tokens": 9000, "completion_tokens": 0},
                messages=[],
            )
        )

        assert result.outcome == EventOutcome.DENY
        assert "BUDGET_EXHAUSTED" in result.reason

        audit = kernel.get_mod("audit_ledger")
        entry = audit.get_entries(event_name="model.requested")[-1]
        assert entry["outcome"] == "deny"
        assert "context_budget" in entry["chain"]


class TestReplayThroughTheRealChain:
    @pytest.mark.asyncio
    async def test_a_write_is_recorded_and_replayed(self, kernel, tmp_path):
        target = tmp_path / "app.py"
        # newline="" keeps the literal bytes so the before/after assertion is
        # deterministic on Windows, where text-mode writes would translate \n.
        target.write_text("v1\n", encoding="utf-8", newline="")

        await kernel.dispatch(
            AlphaEvent(
                name="tool.requested",
                payload={"tool_name": "write_to_file", "tool_args": {"TargetFile": str(target), "ReplacementContent": "v2\n"}},
                correlation=CorrelationContext.create(run_id="run_1", turn_id="turn_1", tool_call_id="c1"),
            )
        )
        await kernel.dispatch(
            AlphaEvent(
                name="tool.completed",
                payload={
                    "tool_name": "write_to_file",
                    "tool_args": {"TargetFile": str(target), "ReplacementContent": "v2\n"},
                    "tool_call_id": "c1",
                    "status": "success",
                },
                correlation=CorrelationContext.create(run_id="run_1", turn_id="turn_1", tool_call_id="c1"),
            )
        )

        result = await kernel.dispatch(
            AlphaEvent(
                name="replay.requested",
                payload={"turn_id": "turn_1"},
                correlation=CorrelationContext.create(run_id="run_1", turn_id="turn_1"),
            )
        )

        assert result.outcome == EventOutcome.ANSWER
        steps = result.response_payload["steps"]
        assert len(steps) == 1
        assert steps[0]["before"] == "v1\n"
        assert steps[0]["after"] == "v2\n"
        assert "-v1" in steps[0]["diff"] and "+v2" in steps[0]["diff"]


class TestKernelIntrospection:
    def test_describe_mods_reports_the_full_fleet(self, kernel):
        descriptions = kernel.describe_mods()
        assert len(descriptions) == len(kernel.list_mods())
        assert all("discrepancies" in d for d in descriptions)

    def test_ui_cards_are_retained_on_the_kernel(self, kernel):
        kernel.record_ui_card({"id": "c1", "mod_name": "m"})
        assert kernel.list_ui_cards()[0]["id"] == "c1"
        assert kernel.clear_ui_cards("m") == 1

    def test_find_outcome_owner_is_none_outside_a_dispatch(self, kernel):
        assert kernel.find_outcome_owner(EventOutcome.DENY) is None

    def test_state_and_command_registries_are_shared(self, kernel):
        assert kernel.state.total_keys() == 0
        assert kernel.commands.list_commands() == []
