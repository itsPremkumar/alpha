"""Builtin tool registry assembly.

``BUILTIN_TOOLS`` and ``SUBAGENT_TOOLS`` are built on first access (PEP 562)
instead of at import time. Building them at import time pulled in every builtin
tool module and, through each one, the subsystem behind it: measured at 97.4s on
a loaded Windows host, which by itself was enough to keep the Gateway from
booting inside the recovery watchdog's budget. Nothing about the registries
changed — same tools, same order, same de-duplication, same names — only the
moment they are constructed.

Two consequences the next reader must not undo:

* Every ``alpha.tools.builtins`` import lives inside the builder or inside the
  assembly function that uses it, never at module scope. A module-level import
  here is what put 3000+ modules back on the import path.
* The registries resolve through the module namespace, so module-level callers
  are unaffected: ``from alpha.tools.tools import BUILTIN_TOOLS``,
  ``alpha.tools.tools.BUILTIN_TOOLS`` and
  ``patch("alpha.tools.tools.BUILTIN_TOOLS", ...)`` all read the same cached
  list that ``get_available_tools`` reads, and a patched registry is honoured.

``BaseTool`` is lazy for the same reason — ``langchain.tools`` measured 24s of
import on this host — but it is still reachable as ``alpha.tools.tools.BaseTool``
and as ``from alpha.tools.tools import BaseTool``, so code that relied on this
module importing LangChain transitively keeps working.
"""

from __future__ import annotations

import logging
import threading
from types import ModuleType
from typing import TYPE_CHECKING

from alpha.config import get_app_config
from alpha.config.app_config import AppConfig
from alpha.reflection import resolve_variable
from alpha.sandbox.security import is_host_bash_allowed, is_in_process_repl_allowed

if TYPE_CHECKING:
    from langchain.tools import BaseTool

logger = logging.getLogger(__name__)

# Tool assembly may run on worker threads concurrently (the registries are
# process-wide singletons), so the first resolution is made single-shot. The
# wrapped objects themselves are guarded separately by _sync_invocable_tool_lock.
_registry_lock = threading.RLock()


def _deduplicate_tools(tools: list[BaseTool]) -> list[BaseTool]:
    """Return ``tools`` with repeated declarations removed, preserving order.

    Several builtin modules export backwards-compatible aliases (for example
    ``run_nvidia_avo_step`` re-exports ``run_variation_operator_step``), so the
    registry literal can name a single tool object more than once.
    ``get_available_tools`` de-duplicates by name before binding, but keeping the
    registry itself unique makes the reported built-in count accurate and
    protects any caller that consumes ``BUILTIN_TOOLS`` directly.
    """
    seen: set = set()
    unique: list[BaseTool] = []
    for item in tools:
        key = getattr(item, "name", None) or id(item)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _resolve_module_export(entry: BaseTool) -> BaseTool:
    """Return the tool an entry names, unwrapping a shadowed submodule.

    ``alpha.tools.builtins`` is a PEP-562 lazy facade whose ``__getattr__``
    resolves a tool name to the *tool* on first access. But a direct submodule
    import (``from alpha.tools.builtins.cognitive_memory_tool import
    cognitive_memory_tool``) binds the package attribute to the *module*, so the
    facade's ``__getattr__`` never fires for that name and the ``from
    alpha.tools.builtins import <name>`` inside ``_build_registries`` hands back
    the module instead of the tool. Registry consumers then crash on ``t.name``.

    This normalises that one failure mode without touching the registry literal,
    its order, or ``_deduplicate_tools`` — so ``contracts/feature_manifest.json``
    and the capability counts are unaffected. A real tool is returned unchanged;
    a self-named submodule is replaced by its own same-named tool export.
    """
    if isinstance(entry, ModuleType):
        resolved = getattr(entry, entry.__name__.rsplit(".", 1)[-1], None)
        if resolved is not None and not isinstance(resolved, ModuleType):
            return resolved
    return entry


def _build_registries() -> tuple[list[BaseTool], list[BaseTool]]:
    """Construct ``BUILTIN_TOOLS`` and ``SUBAGENT_TOOLS``.

    This function is the only place the builtin tool modules — and the heavy
    subsystems behind them — are imported. It runs at most once per process, and
    only when a registry is actually read, so an import of
    ``alpha.tools.tools`` no longer pays for 3000+ modules nobody asked for.
    """
    from alpha.tools.builtins import (
        agent_observe_tool,
        alpha_capability,
        alpha_peer_network_tool,
        analyze_semantic_git_delta,
        ask_clarification_tool,
        ask_oracle,
        ast_grep_rewrite,
        ast_grep_search,
        astra_security_manage,
        audit_finish_first_evidence,
        auto_test_and_repair,
        autonomy_control,
        await_task_event,
        blackboard_query,
        blackboard_record_evidence,
        boulder_checkpoint_manage,
        browser_navigate_and_inspect,
        build_autonomous_plan,
        catalog_tool_call,
        catalog_tool_describe,
        catalog_tool_search,
        check_metacognitive_health,
        check_or_set_autonomy_profile,
        cognitive_plan,
        compile_cognitive_plan,
        compile_five_pass_search,
        compile_mission,
        compile_problem_model,
        compute_program_slice,
        consolidate_cognitive_memory,
        consolidate_memory_dream,
        consult_experience,
        consult_plan_gap_analysis,
        create_workflow_checkpoint,
        cronjob_manage,
        deep_research,
        deep_web_search,
        delegate_to_deep_agent,
        deliberate_artifact_quality,
        desktop_inspect_ui_tree_tool,
        desktop_keyboard_action_tool,
        desktop_mouse_action_tool,
        # Free local-first OS computer use (Module C):
        desktop_screenshot_tool,
        desktop_system_one_action_tool,
        desktop_window_manage_tool,
        diagnose_and_heal_environment,
        dispatch_discipline_worker,
        emergency_stop_manage,
        # Frontier autonomous software engineering engines:
        emit_stigmergic_event,
        enterprise_security_manage,
        evaluate_agent_competence,
        evaluate_epistemic_claim,
        execute_sandboxed_computer_action,
        execute_slash_command_tool,
        execute_transactional_action,
        forge_skill_from_trace,
        generate_curriculum_plan,
        generate_repo_map,
        hashline_edit,
        hashline_read,
        hyperplan_review_manage,
        identify_autonomous_command_tool,
        inspect_deep_agent_telemetry,
        inspect_repo_twin,
        invoke_python_skill_tool,
        keyless_web_search,
        kibitzer_nudge_manage,
        learning_graph_manage,
        list_available_deep_agents,
        list_dynamic_tools,
        manage_code_checkpoint,
        manage_context_data,
        manage_durable_orchestration,
        manage_mission_hierarchy,
        manage_model_performance_registry,
        manage_reflexion_memory,
        mission_memory,
        moa_multi_model_reasoning,
        query_contrastive_memory,
        query_knowledge_graph,
        query_language_server_symbol,
        query_stigmergic_traces,
        ralph_loop_tool,
        recall_agent_memory,
        reconcile_structural_ast_conflicts,
        record_trajectory_outcome,
        reproduce_and_verify,
        request_secure_credential,
        resolve_builtin_tool,
        reversible_delete,
        review_plan_invariant_gate,
        review_skill_package,
        run_autonomous_benchmark_eval,
        run_avo_variation,
        run_differential_regression_oracle,
        run_introspective_tree_search,
        run_nvidia_avo_step,
        run_rsi_cycle,
        run_task_evaluation_benchmark,
        run_variation_operator_step,
        schedule_work_queue,
        search_project_docs,
        # P4: register the canonical past-session memory search (was defined in
        # code_agentic_core but never exported/registered, so unreachable).
        search_session_memory,
        self_heal_diagnose,
        self_improvement_loop_tool,
        simulate_consequences,
        skills_hub_manage,
        subagent_control,
        synthesize_reusable_skill,
        synthesize_runtime_tool,
        tom_consult,
        trace_artifact_lineage,
        update_progress_card,
        verify_command_approval,
        verify_web_ui_visual_regression,
        visual_verify_artifact,
        workflow_dag_manage,
    )
    from alpha.tools.builtins.harness_refine_tool import harness_refine_tool

    # Several exports share their submodule names. If a test or caller imported
    # that submodule first, Python leaves the module on the package attribute
    # and ``from alpha.tools.builtins import a2a_tool`` binds the module. Always
    # restore the mapped public export before constructing registry entries.
    # Resolve every name collision in the imported registry surface. Resolving
    # only the export that first failed would leave the same order-dependent
    # failure waiting behind whichever test module imports next.
    agent_message_tool = resolve_builtin_tool("agent_message_tool")
    arena_tool = resolve_builtin_tool("arena_tool")
    bot_roster_tool = resolve_builtin_tool("bot_roster_tool")
    canvas_widget_tool = resolve_builtin_tool("canvas_widget_tool")
    capability_mesh_tool = resolve_builtin_tool("capability_mesh_tool")
    code_mode_tool = resolve_builtin_tool("code_mode_tool")
    cognitive_memory_tool = resolve_builtin_tool("cognitive_memory_tool")
    company_tool = resolve_builtin_tool("company_tool")
    deliberation_tool = resolve_builtin_tool("deliberation_tool")
    goal_engine_tool = resolve_builtin_tool("goal_engine_tool")
    goal_integrity_tool = resolve_builtin_tool("goal_integrity_tool")
    group_chat_tool = resolve_builtin_tool("group_chat_tool")
    job_tool = resolve_builtin_tool("job_tool")
    kanban_board_tool = resolve_builtin_tool("kanban_board_tool")
    present_file_tool = resolve_builtin_tool("present_file_tool")
    process_handle_tool = resolve_builtin_tool("process_handle_tool")
    propose_skill_tool = resolve_builtin_tool("propose_skill_tool")
    python_repl_tool = resolve_builtin_tool("python_repl_tool")
    session_search_tool = resolve_builtin_tool("session_search_tool")
    subagent_registry_tool = resolve_builtin_tool("subagent_registry_tool")
    supervision_tool = resolve_builtin_tool("supervision_tool")
    swarm_tool = resolve_builtin_tool("swarm_tool")
    trajectory_audit_tool = resolve_builtin_tool("trajectory_audit_tool")
    war_room_tool = resolve_builtin_tool("war_room_tool")
    a2a_tool = resolve_builtin_tool("a2a_tool")

    # This export has the same name as its submodule. If the submodule was
    # imported first, Python exposes the module on the package object and a
    # package-level import returns that module instead of the LangChain tool.
    from alpha.tools.builtins.task_tool import task_tool

    builtin_tools = _deduplicate_tools(
        [
            present_file_tool,
            propose_skill_tool,
            ask_clarification_tool,
            arena_tool,
            # Alpha Capability Mesh: read/preview only. It has no executor wiring, so
            # every planning action is a locally computed preview and nothing dispatches.
            capability_mesh_tool,
            review_skill_package,
            # Cross-thread recall: lead-only (denied for subagents by default, see
            # SubagentConfig/CustomSubagentConfig) because it crosses thread
            # boundaries by design.
            session_search_tool,
            # Prime Agent RLM & Continual Harness extensions:
            python_repl_tool,
            harness_refine_tool,
            process_handle_tool,
            invoke_python_skill_tool,
            # Ultra-Advanced Bot Mode & Collaborative Kanban extensions:
            bot_roster_tool,
            group_chat_tool,
            alpha_peer_network_tool,
            kanban_board_tool,
            # Autonomous Continuous Goal, Canvas & Trajectory extensions:
            goal_engine_tool,
            canvas_widget_tool,
            trajectory_audit_tool,
            code_mode_tool,
            catalog_tool_search,
            catalog_tool_describe,
            catalog_tool_call,
            cognitive_memory_tool,
            consolidate_memory_dream,
            update_progress_card,
            synthesize_reusable_skill,
            request_secure_credential,
            await_task_event,
            # Bounded Recursive Self-Improvement Loop:
            self_improvement_loop_tool,
            ralph_loop_tool,
            # Multi-Agent Swarm Intelligence & Continuous Skills Hub:
            learning_graph_manage,
            verify_command_approval,
            moa_multi_model_reasoning,
            emergency_stop_manage,
            skills_hub_manage,
            subagent_registry_tool,
            cronjob_manage,
            browser_navigate_and_inspect,
            # Resilient Multi-Session Workflow & Boulder Checkpointing Engine:
            hashline_read,
            hashline_edit,
            workflow_dag_manage,
            boulder_checkpoint_manage,
            kibitzer_nudge_manage,
            hyperplan_review_manage,
            ast_grep_search,
            ast_grep_rewrite,
            build_autonomous_plan,
            # Autonomous Organization & Advanced Agentic OS Builtins:
            cognitive_plan,
            deliberation_tool,
            company_tool,
            a2a_tool,
            goal_integrity_tool,
            job_tool,
            supervision_tool,
            swarm_tool,
            self_heal_diagnose,
            blackboard_record_evidence,
            blackboard_query,
            # Frontier Autonomous Software Engineering Engines:
            query_language_server_symbol,
            emit_stigmergic_event,
            query_stigmergic_traces,
            synthesize_runtime_tool,
            list_dynamic_tools,
            run_autonomous_benchmark_eval,
            analyze_semantic_git_delta,
            consolidate_cognitive_memory,
            recall_agent_memory,
            # High-End Cognitive, Deliberation, Epistemics & Evolution Engines:
            execute_transactional_action,
            evaluate_epistemic_claim,
            simulate_consequences,
            evaluate_agent_competence,
            run_rsi_cycle,
            run_variation_operator_step,
            run_nvidia_avo_step,
            inspect_repo_twin,
            check_metacognitive_health,
            deliberate_artifact_quality,
            compile_five_pass_search,
            forge_skill_from_trace,
            # Declared by the ``deep-research`` subagent category in
            # alpha.subagents.categories and named in that category's prompt. Without
            # registration here the subagent allowlist filters it out silently (see
            # ``_filter_tools``), so the subagent is told to use a tool it cannot reach.
            deep_research,
            # Core Code Agentic Tools:
            generate_repo_map,
            auto_test_and_repair,
            manage_code_checkpoint,
            # P4: canonical past-session memory search. The historical duplicate was a
            # bash-based memory tool; this Python implementation (SQLite FTS5 + BM25
            # with cron demotion, in code_agentic_core) is the canonical one and was
            # previously defined but never registered, leaving it unreachable. Nothing
            # is deleted — the bash name stays an alias wherever it still exists.
            search_session_memory,
            # Enterprise Security Enclave & Multi-Modal Boundary Enforcement:
            enterprise_security_manage,
            astra_security_manage,
            reversible_delete,
            # Strategic Discipline Council & Invariant Gates:
            consult_plan_gap_analysis,
            review_plan_invariant_gate,
            dispatch_discipline_worker,
            # Mission Hierarchy & Universal Work Queue:
            manage_mission_hierarchy,
            schedule_work_queue,
            # Durable mission memory -- the re-read spec/plan/status/scratchpad that stops
            # long-horizon drift; records + verifies the per-thread objective.
            mission_memory,
            trace_artifact_lineage,
            # Knowledge, Benchmark & Performance Evaluation:
            query_knowledge_graph,
            search_project_docs,
            # Self-knowledge: one bounded call reports the whole capability inventory
            # (tools, skills, MCP, models, bots, commands, engines, wiring, identity)
            # and its configuration gaps. Keep this adjacent to search_project_docs —
            # both answer "what is true about this system", from registries and from docs
            # respectively, and a reader looking for one should see the other.
            alpha_capability,
            autonomy_control,
            run_task_evaluation_benchmark,
            manage_model_performance_registry,
            # Durable Replay, Computer Fabric & Context Control:
            manage_durable_orchestration,
            execute_sandboxed_computer_action,
            manage_context_data,
            check_or_set_autonomy_profile,
            audit_finish_first_evidence,
            # Epistemic Oracle, Experience & Curiosity:
            tom_consult,
            ask_oracle,
            reproduce_and_verify,
            consult_experience,
            compile_mission,
            compile_cognitive_plan,
            run_avo_variation,
            generate_curriculum_plan,
            create_workflow_checkpoint,
            visual_verify_artifact,
            # Structural visual-regression diff over parsed accessibility trees
            # (real element-parity math in alpha.verification.visual_e2e_engine,
            # covered by tests/test_visual_e2e_engine.py). Defined in
            # code_agentic_core but never exported or registered until now, which
            # made it unreachable.
            verify_web_ui_visual_regression,
            manage_reflexion_memory,
            compile_problem_model,
            # SOTA Frontier Agentic Software Engineering Tools:
            run_introspective_tree_search,
            compute_program_slice,
            run_differential_regression_oracle,
            diagnose_and_heal_environment,
            reconcile_structural_ast_conflicts,
            query_contrastive_memory,
            record_trajectory_outcome,
            # Deep Agent Fleet with clean-context isolation:
            delegate_to_deep_agent,
            list_available_deep_agents,
            inspect_deep_agent_telemetry,
            # Master Slash Command catalog (418 commands across 28 categories) and the
            # lifecycle-phase router that picks one. Both were implemented, exported
            # from ``alpha.tools.builtins`` and imported above, but never named here —
            # so the registry dropped them and no agent could reach either one.
            execute_slash_command_tool,
            identify_autonomous_command_tool,
            # Keyless web search ported from AgentEye: keyless_web_search runs the
            # httpx-first fallback chain; deep_web_search runs multi-query research on
            # top of it. Honest ok/no_results/failed statuses; optional ddgs absence
            # names the real package instead of fabricating results.
            keyless_web_search,
            deep_web_search,
            # Free local-first OS computer use & laptop automation (Module C):
            # zero-token accessibility grounding + sentinel-guarded desktop input.
            desktop_screenshot_tool,
            desktop_inspect_ui_tree_tool,
            desktop_mouse_action_tool,
            desktop_keyboard_action_tool,
            desktop_window_manage_tool,
            desktop_system_one_action_tool,
            # War room: staged, clock-bounded group deliberation, plus the bot
            # lifecycle (hire/clone/rescope/archive) and the fenced capability
            # acquisition pipeline. Present in BUILTIN_TOOLS so every agent can open a
            # room; the governance and authority checks live inside the tool, not here.
            #
            # The deep-agent working plane tool is deliberately NOT in this literal. It
            # is appended by ``append_deepagent_tools`` inside ``get_available_tools``
            # when ``deepagent.enabled`` is true, exactly as ``task_note`` is gated on
            # ``task_continuity.enabled``. Listing it here would make it unconditional.
            war_room_tool,
        ]
    )

    subagent_tools = [
        task_tool,
        subagent_control,
        # Direct agent-to-agent communication (Prime Agent roster & messaging):
        agent_message_tool,
        agent_observe_tool,
        group_chat_tool,
        kanban_board_tool,
        a2a_tool,
        swarm_tool,
        company_tool,
    ]
    # A direct submodule import can shadow a facade export with its module (see
    # ``_resolve_module_export``); unwrap those so the registry always holds tools.
    builtin_tools = [_resolve_module_export(tool) for tool in builtin_tools]
    subagent_tools = [_resolve_module_export(tool) for tool in subagent_tools]
    return builtin_tools, subagent_tools


def _builtin_tools() -> list[BaseTool]:
    """Return ``BUILTIN_TOOLS``, building and caching it on first access.

    The lookup goes through ``globals()`` on purpose: a test or an operator that
    assigns ``alpha.tools.tools.BUILTIN_TOOLS`` (directly, or through
    ``unittest.mock.patch``) must be the registry ``get_available_tools`` reads,
    not a value this accessor keeps re-deriving behind their back.
    """
    tools = globals().get("BUILTIN_TOOLS")
    if tools is not None:
        return tools
    with _registry_lock:
        tools = globals().get("BUILTIN_TOOLS")
        if tools is None:
            tools = _build_registries()[0]
            globals()["BUILTIN_TOOLS"] = tools
    return tools


def _subagent_tools() -> list[BaseTool]:
    """Return ``SUBAGENT_TOOLS``, building and caching it on first access.

    Resolved independently of ``BUILTIN_TOOLS`` so that overriding one registry
    never silently rebuilds or discards the other.
    """
    tools = globals().get("SUBAGENT_TOOLS")
    if tools is not None:
        return tools
    with _registry_lock:
        tools = globals().get("SUBAGENT_TOOLS")
        if tools is None:
            tools = _build_registries()[1]
            globals()["SUBAGENT_TOOLS"] = tools
    return tools


def _langchain_base_tool() -> type:
    """Return ``langchain.tools.BaseTool``, importing it on first access.

    The module's annotations are why the import can be deferred at all
    (``from __future__ import annotations`` plus the TYPE_CHECKING import); this
    resolver is why the name is still a real module attribute rather than a
    missing one.
    """
    from langchain.tools import BaseTool

    return BaseTool


_LAZY_ATTRIBUTES = {"BUILTIN_TOOLS": _builtin_tools, "SUBAGENT_TOOLS": _subagent_tools, "BaseTool": _langchain_base_tool}


def __getattr__(name: str) -> object:
    """Resolve the lazy attributes on first access (PEP 562).

    Anything else still raises ``AttributeError`` with the standard message, so a
    typo is an error here rather than a ``None`` that fails somewhere else.
    """
    resolver = _LAZY_ATTRIBUTES.get(name)
    if resolver is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = resolver()
    # Cache in the module namespace so the next read is a plain global lookup,
    # and so unittest.mock.patch can save and restore what it replaced.
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """List the lazy attributes too, so ``dir()`` does not under-report them."""
    return sorted({*globals(), *_LAZY_ATTRIBUTES})


def _is_host_bash_tool(tool: object) -> bool:
    """Return True if the tool config represents a host-bash execution surface."""
    group = getattr(tool, "group", None)
    use = getattr(tool, "use", None)
    if group == "bash":
        return True
    if use == "alpha.sandbox.tools:bash_tool":
        return True
    return False


_sync_invocable_tool_lock = threading.Lock()


def _ensure_sync_invocable_tool(tool: BaseTool) -> BaseTool:
    """Attach a sync wrapper to async-only tools used by sync agent callers.

    The wrapped objects are process-wide singletons (BUILTIN_TOOLS /
    SUBAGENT_TOOLS / MCP cache entries) and tool assembly may now run on
    worker threads concurrently; double-checked locking makes the in-place
    ``tool.func`` wrap explicitly single-shot instead of incidental.
    """
    from alpha.tools.sync import make_sync_tool_wrapper

    if getattr(tool, "func", None) is not None or getattr(tool, "coroutine", None) is None:
        return tool
    with _sync_invocable_tool_lock:
        if getattr(tool, "func", None) is None:
            tool.func = make_sync_tool_wrapper(tool.coroutine, tool.name)
    return tool


def get_available_tools(
    groups: list[str] | None = None,
    include_mcp: bool = True,
    model_name: str | None = None,
    subagent_enabled: bool = False,
    *,
    include_upload_tool: bool = True,
    app_config: AppConfig | None = None,
) -> list[BaseTool]:
    """Get all available tools from config.

    Note: MCP tools should be initialized at application startup using
    `initialize_mcp_tools()` from alpha.mcp module.

    Args:
        groups: Optional list of tool groups to filter by.
        include_mcp: Whether to include tools from MCP servers (default: True).
        model_name: Optional model name to determine if vision tools should be included.
        subagent_enabled: Whether to include subagent tools (task, task_status).
        include_upload_tool: Whether to include ``list_uploaded_files`` (default: True).
            Ordinary task subagents enable it only after snapshotting the
            parent's current-run upload state. Durable batch and non-standard
            subagent callers without that state keep it disabled.

    Returns:
        List of available tools.
    """
    # Conditional builtins are imported here rather than at module scope: they
    # are bound only on some configurations, so nothing outside this function
    # needs them, and importing them eagerly would put their subsystems back on
    # the import path of every process that touches alpha.tools.tools. The same
    # goes for the runtime probes and the MCP tagger: all three are read only
    # while assembling a tool list, and alpha.mcp alone measured 15.7s.
    from langchain.tools import BaseTool

    from alpha.mcp.tasks.runtime import is_mcp_task_runtime_available
    from alpha.subagents.batch_runtime import is_subagent_batch_runtime_available
    from alpha.tools.builtins import (
        batch_status,
        batch_task,
        cancel_background_task,
        cancel_batch,
        list_background_tasks,
        list_uploaded_files,
        resolve_builtin_tool,
    )

    view_image_tool = resolve_builtin_tool("view_image_tool")
    from alpha.tools.builtins.deepagent_tool import append_deepagent_tools
    from alpha.tools.mcp_metadata import tag_mcp_tool

    config = app_config or get_app_config()
    tool_configs = [tool for tool in config.tools if groups is None or tool.group in groups]

    # Do not expose host bash by default when LocalSandboxProvider is active.
    if not is_host_bash_allowed(config):
        tool_configs = [tool for tool in tool_configs if not _is_host_bash_tool(tool)]

    loaded_tools_raw = [(cfg, resolve_variable(cfg.use, BaseTool)) for cfg in tool_configs]

    # Warn when the config ``name`` field and the tool object's ``.name``
    # attribute diverge — this mismatch is the root cause of issue #1803 where
    # the LLM receives one name in its tool schema but the runtime router
    # recognises a different name, producing "not a valid tool" errors.
    for cfg, loaded in loaded_tools_raw:
        if cfg.name != loaded.name:
            logger.warning(
                "Tool name mismatch: config name %r does not match tool .name %r (use: %s). The tool's own .name will be used for binding.",
                cfg.name,
                loaded.name,
                cfg.use,
            )

    loaded_tools = [_ensure_sync_invocable_tool(t) for _, t in loaded_tools_raw]

    # Conditionally add tools based on config
    builtin_tools = _builtin_tools().copy()
    if is_mcp_task_runtime_available():
        builtin_tools.extend((list_background_tasks, cancel_background_task))
    if include_upload_tool:
        builtin_tools.append(list_uploaded_files)
    skill_evolution_config = getattr(config, "skill_evolution", None)
    if getattr(skill_evolution_config, "enabled", False):
        from alpha.tools.skill_manage_tool import skill_manage_tool

        builtin_tools.append(skill_manage_tool)

    # python_repl is NOT a host-bash tool, so the allow_host_bash filter above
    # never saw it: it lives in BUILTIN_TOOLS, and that filter is not applied to
    # BUILTIN_TOOLS at all. It exec()s the cell inside the Gateway process with
    # os preloaded, so it grants process-level execution and full environment
    # read access with no sandbox boundary whatsoever. Leaving it reachable made
    # allow_host_bash: false an incomplete kill switch. It now has its own
    # default-off switch and is dropped here so the model is never offered it.
    if not is_in_process_repl_allowed(config):
        builtin_tools = [t for t in builtin_tools if getattr(t, "name", None) != "python_repl"]

    # The deep-agent working plane is gated on ``deepagent.enabled``, the same
    # way task_continuity gates ``task_note`` below. The tool is imported where
    # it is bound and never bound here while the flag is off, so the tool
    # registry and its reported count describe what the deployment actually has.
    append_deepagent_tools(builtin_tools, config)

    # Add subagent tools only if enabled via runtime parameter
    if subagent_enabled:
        builtin_tools.extend(_subagent_tools())
        if is_subagent_batch_runtime_available():
            builtin_tools.extend((batch_task, batch_status, cancel_batch))
        logger.info("Including native subagent tools")

    # If no model_name specified, use the first model (default)
    if model_name is None and config.models:
        model_name = config.models[0].name

    # Add view_image_tool only if the model supports vision
    model_config = config.get_model_config(model_name) if model_name else None
    if model_config is not None and model_config.supports_vision:
        builtin_tools.append(view_image_tool)
        logger.info(f"Including view_image_tool for model '{model_name}' (supports_vision=True)")

    # Get cached MCP tools if enabled
    # NOTE: We use ExtensionsConfig.from_file() instead of config.extensions
    # to always read the latest configuration from disk. This ensures that changes
    # made through the Gateway API (which runs in a separate process) are immediately
    # reflected when loading MCP tools.
    mcp_tools = []
    if include_mcp:
        try:
            from alpha.config.extensions_config import ExtensionsConfig
            from alpha.mcp.cache import get_cached_mcp_tools

            extensions_config = ExtensionsConfig.from_file()
            if extensions_config.get_enabled_mcp_servers():
                mcp_tools = get_cached_mcp_tools()
                if mcp_tools:
                    logger.info(f"Using {len(mcp_tools)} cached MCP tool(s)")

                    # Tag MCP-sourced tools so deferred-tool assembly at each
                    # agent construction site can identify them. Lead agents
                    # assemble their full configured MCP catalog and apply active
                    # skill policy at runtime; subagents may pass an already
                    # policy-filtered list because their skills load at startup.
                    for t in mcp_tools:
                        tag_mcp_tool(t)
        except ImportError:
            logger.warning("MCP module not available. Install 'langchain-mcp-adapters' package to enable MCP tools.")
        except Exception as e:
            logger.error(f"Failed to get cached MCP tools: {e}")

    # Add invoke_acp_agent tool if any ACP agents are configured
    acp_tools: list[BaseTool] = []
    try:
        from alpha.tools.builtins.invoke_acp_agent_tool import build_invoke_acp_agent_tool

        if app_config is None:
            from alpha.config.acp_config import get_acp_agents

            acp_agents = get_acp_agents()
        else:
            acp_agents = getattr(config, "acp_agents", {}) or {}
        if acp_agents:
            acp_tools.append(build_invoke_acp_agent_tool(acp_agents))
            logger.info(f"Including invoke_acp_agent tool ({len(acp_agents)} agent(s): {list(acp_agents.keys())})")
    except Exception as e:
        logger.warning(f"Failed to load ACP tool: {e}")

    # The breakdown is logged *after* dedup below, because "total tools loaded"
    # must mean what the model is actually offered. Logging `len(loaded_tools)`
    # as the total reported "Total tools loaded: 11" on a run whose agent
    # received 143 tools — `loaded_tools` is only the `tools:` block from
    # config.yaml. An operator reading tool-usage logs to work out why a tool
    # was never called was reading the wrong number.

    # Deduplicate by tool name — config-loaded tools take priority, followed by
    # built-ins, MCP tools, and ACP tools.  Duplicate names cause the LLM to
    # receive ambiguous or concatenated function schemas (issue #1803).
    all_tools = [_ensure_sync_invocable_tool(t) for t in loaded_tools + builtin_tools + mcp_tools + acp_tools]
    seen_names: set[str] = set()
    unique_tools: list[BaseTool] = []
    for t in all_tools:
        if t.name not in seen_names:
            unique_tools.append(t)
            seen_names.add(t.name)
        else:
            logger.warning(
                "Duplicate tool name %r detected and skipped — check your config.yaml and MCP server registrations (issue #1803).",
                t.name,
            )

    # Report what the model is actually offered, not one component of it.
    logger.info(
        "Tools offered to the model: %d total (config: %d, built-in: %d, MCP: %d, ACP: %d) after dedup",
        len(unique_tools),
        len(loaded_tools),
        len(builtin_tools),
        len(mcp_tools),
        len(acp_tools),
    )
    return unique_tools
