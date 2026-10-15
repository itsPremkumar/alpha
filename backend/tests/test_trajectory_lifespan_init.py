"""Tests for trajectory store initialization in Gateway lifespan.

Verifies that lifespan() initializes app.state.trajectory_store so that
the TrajectoryRecorderMiddleware can record StepRecord entries during
agent execution.
"""

from __future__ import annotations

import asyncio
from contextlib import ExitStack, asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI

from alpha.trajectory.store import TrajectoryStore


@asynccontextmanager
async def _noop_langgraph_runtime(_app, _startup_config):
    yield


def _make_startup_config():
    """Create a minimal startup config mock for lifespan tests."""
    config = MagicMock()
    config.log_level = "INFO"
    config.memory.enabled = False
    config.memory.shutdown_flush_timeout_seconds = 5.0
    config.autonomy.enabled = False
    config.scheduler.enabled = False
    config.subagent_batches.enabled = False
    config.subagent_runtime = MagicMock()
    config.capabilities = MagicMock()
    config.mcp_tasks = MagicMock()
    config.mcp_tasks.enabled = False
    config.scheduler.poll_interval_seconds = 5
    config.scheduler.lease_seconds = 120
    config.scheduler.max_concurrent_runs = 3
    config.scheduler.queue_timeout_seconds = 3600
    config.scheduler.multi_instance = False
    config.run_ownership.grace_seconds = 10
    return config


def _patch_cms(startup_config):
    """Lifespan dependencies patched out for the test, as ready context managers.

    A single ``with`` statement carrying this many parenthesized context
    managers exceeds CPython's compile-time limit of 20 statically nested
    blocks and would fail to even compile. Returning the context managers and
    entering them via ``ExitStack`` preserves each ``patch(...)`` call exactly.
    """
    return [
        patch("app.gateway.app.get_app_config", return_value=startup_config),
        patch("app.gateway.app.get_gateway_config", return_value=MagicMock(host="x", port=0)),
        patch("app.gateway.app.langgraph_runtime", _noop_langgraph_runtime),
        patch("app.gateway.app.setup_monocle_tracing_if_enabled"),
        patch("app.gateway.app.cleanup_stale_upload_staging_files", return_value=0),
        patch("app.gateway.app.configure_logging"),
        patch("app.gateway.app.ensure_browser_runtime_available"),
        patch("app.gateway.app.warn_if_auth_disabled_enabled"),
        patch("app.gateway.app.auth.close_oidc_service", AsyncMock()),
        patch("app.gateway.app._ensure_admin_user", AsyncMock()),
        # Lazy-imported inside lifespan: patch at the source module, since a
        # `from X import Y` inside the function body never consults a
        # `app.gateway.app.Y` attribute.
        patch("alpha.skills.projection.ensure_public_skill_projection"),
        patch("alpha.agents.memory.get_memory_manager", return_value=MagicMock()),
        patch("alpha.subagents.capacity.configure_subagent_execution_capacity"),
        patch("app.gateway.routers.workflows.hydrate_workflow_engine_from_store", return_value={}),
        patch("alpha.evolution.update_engine.get_update_engine"),
        patch("alpha.evolution.update_policy.load_update_policy"),
        patch("app.gateway.system_monitor_service.start_system_monitor"),
        patch("app.gateway.system_monitor_service.stop_system_monitor"),
        patch("app.gateway.routers.capability_honesty.start_wiring_audit", return_value=False),
        patch("alpha.peer_network.get_peer_network_service"),
        patch("alpha.peer_network.storage.NETWORK_OWNER", "test"),
        patch("app.gateway.services.peer_network_agent_dispatcher"),
        patch("app.gateway.services.launch_scheduled_thread_run"),
        patch("app.gateway.services.launch_mcp_task_notification_run"),
        patch("app.scheduler.ScheduledTaskService"),
        patch("app.channels.service.start_channel_service", AsyncMock(return_value=MagicMock())),
        patch("app.channels.service.stop_channel_service", AsyncMock()),
        patch("alpha.mcp.task_tool_caller.McpTaskToolCaller"),
        patch("alpha.mcp.tasks.McpTaskDriverRegistry"),
        patch("alpha.mcp.tasks.OrdinaryMcpTaskDriver"),
        patch("alpha.mcp.tasks.runtime.configured_task_toolset_count", return_value=0),
        patch("alpha.mcp.tasks.runtime.set_mcp_task_submitter"),
        patch("alpha.mcp.tasks.runtime.set_mcp_task_config_snapshot"),
        patch("alpha.mcp.tasks.runtime.validate_mcp_task_runtime_configuration"),
        patch("app.mcp_tasks.McpTaskService"),
        patch("alpha.config.extensions_config.ExtensionsConfig"),
        patch("alpha.config.mcp_tasks_config.McpTasksConfig"),
        patch("alpha.subagents.batch_runtime.set_subagent_batch_submitter"),
        patch("app.subagent_batches.SubagentBatchService"),
        patch("alpha.events.bus.configure_event_bus"),
        patch("app.gateway.autonomy.supervisor.get_autonomy_supervisor"),
        patch("app.gateway.autonomy.loops.apex_execution_tick"),
        patch("alpha.capabilities.load_enabled_capabilities", return_value=[]),
        patch("alpha.swarm.team.register_roster_provider"),
        patch("alpha.swarm.team.unregister_roster_provider"),
        patch("alpha.community.browser_automation.get_browser_session_manager"),
        patch("alpha.extensions.notify.suspend_extension_system_observations"),
    ]


class TestTrajectoryStoreLifespanInit:
    """Tests that lifespan initializes app.state.trajectory_store."""

    def test_lifespan_sets_trajectory_store(self):
        """lifespan() must set app.state.trajectory_store."""
        from app.gateway.app import lifespan

        async def scenario():
            app = FastAPI()
            startup_config = _make_startup_config()
            with ExitStack() as stack:
                for cm in _patch_cms(startup_config):
                    stack.enter_context(cm)
                async with lifespan(app):
                    assert hasattr(app.state, "trajectory_store"), "lifespan() must initialize app.state.trajectory_store"
                    assert isinstance(app.state.trajectory_store, TrajectoryStore), "app.state.trajectory_store must be a TrajectoryStore instance"

        asyncio.run(scenario())

    def test_trajectory_store_is_usable_after_init(self):
        """The trajectory store must be usable (can record steps)."""
        from app.gateway.app import lifespan

        async def scenario():
            app = FastAPI()
            startup_config = _make_startup_config()
            with ExitStack() as stack:
                for cm in _patch_cms(startup_config):
                    stack.enter_context(cm)
                async with lifespan(app):
                    store = app.state.trajectory_store
                    # Must be able to record a step
                    step = store.record_step(
                        goal_id="test_goal",
                        step_index=0,
                        thought="test thought",
                        tool_name="test_tool",
                        tool_input={"key": "value"},
                        tool_output="test output",
                    )
                    assert step.goal_id == "test_goal"
                    assert step.step_index == 0
                    assert step.tool_name == "test_tool"

        asyncio.run(scenario())
