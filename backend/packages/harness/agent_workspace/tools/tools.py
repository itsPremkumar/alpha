import logging
import threading

from langchain.tools import BaseTool

from agent_workspace.config import get_app_config
from agent_workspace.config.app_config import AppConfig
from agent_workspace.mcp.tasks.runtime import is_mcp_task_runtime_available
from agent_workspace.reflection import resolve_variable
from agent_workspace.sandbox.security import is_host_bash_allowed
from agent_workspace.subagents.batch_runtime import is_subagent_batch_runtime_available
from agent_workspace.tools.builtins import (
    a2a_tool,
    agent_message_tool,
    agent_observe_tool,
    ask_clarification_tool,
    ask_oracle,
    ast_grep_rewrite,
    ast_grep_search,
    astra_security_manage,
    enterprise_security_manage,
    audit_finish_first_evidence,
    auto_test_and_repair,
    batch_status,
    batch_task,
    blackboard_query,
    blackboard_record_evidence,
    # Frontier autonomous software engineering engines:
    emit_stigmergic_event,
    query_stigmergic_traces,
    query_language_server_symbol,
    synthesize_runtime_tool,
    list_dynamic_tools,
    run_autonomous_benchmark_eval,
    analyze_semantic_git_delta,
    consolidate_cognitive_memory,
    recall_agent_memory,
    bot_roster_tool,
    boulder_checkpoint_manage,
    browser_navigate_and_inspect,
    build_autonomous_plan,
    cancel_background_task,
    cancel_batch,
    canvas_widget_tool,
    catalog_tool_call,
    catalog_tool_describe,
    catalog_tool_search,
    check_metacognitive_health,
    check_or_set_autonomy_profile,
    code_mode_tool,
    cognitive_memory_tool,
    cognitive_plan,
    company_tool,
    compile_cognitive_plan,
    compile_five_pass_search,
    compile_mission,
    compile_problem_model,
    consolidate_memory_dream,
    consult_experience,
    consult_plan_gap_analysis,
    create_workflow_checkpoint,
    cronjob_manage,
    delegate_to_deep_agent,
    deliberation_tool,
    deliberate_artifact_quality,
    dispatch_discipline_worker,
    emergency_stop_manage,
    evaluate_agent_competence,
    evaluate_epistemic_claim,
    execute_sandboxed_computer_action,
    execute_slash_command_tool,
    execute_transactional_action,
    identify_autonomous_command_tool,
    forge_skill_from_trace,
    generate_curriculum_plan,
    generate_repo_map,
    goal_engine_tool,
    goal_integrity_tool,
    group_chat_tool,
    harness_refine_tool,
    hashline_edit,
    hashline_read,
    hyperplan_review_manage,
    inspect_deep_agent_telemetry,
    invoke_python_skill_tool,
    inspect_repo_twin,
    job_tool,
    kanban_board_tool,
    kibitzer_nudge_manage,
    learning_graph_manage,
    list_available_deep_agents,
    list_background_tasks,
    list_uploaded_files,
    manage_code_checkpoint,
    manage_context_data,
    manage_durable_orchestration,
    manage_mission_hierarchy,
    manage_model_performance_registry,
    moa_multi_model_reasoning,
    present_file_tool,
    process_handle_tool,
    propose_skill_tool,
    python_repl_tool,
    query_knowledge_graph,
    ralph_loop_tool,
    self_improvement_loop_tool,
    reproduce_and_verify,
    review_plan_invariant_gate,
    review_skill_package,
    run_avo_variation,
    run_nvidia_avo_step,
    run_variation_operator_step,
    run_rsi_cycle,
    run_task_evaluation_benchmark,
    schedule_work_queue,
    self_heal_diagnose,
    simulate_consequences,
    session_search_tool,
    skills_hub_manage,
    subagent_control,
    supervision_tool,
    swarm_tool,
    task_tool,
    tom_consult,
    trace_artifact_lineage,
    trajectory_audit_tool,
    update_progress_card,
    verify_command_approval,
    view_image_tool,
    visual_verify_artifact,
    manage_reflexion_memory,
    workflow_dag_manage,
    synthesize_reusable_skill,
    request_secure_credential,
    await_task_event,
    run_introspective_tree_search,
    compute_program_slice,
    run_differential_regression_oracle,
    diagnose_and_heal_environment,
    reconcile_structural_ast_conflicts,
    query_contrastive_memory,
    record_trajectory_outcome,
)
from agent_workspace.tools.mcp_metadata import tag_mcp_tool
from agent_workspace.tools.sync import make_sync_tool_wrapper

logger = logging.getLogger(__name__)

BUILTIN_TOOLS = [
    present_file_tool,
    propose_skill_tool,
    ask_clarification_tool,
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
    # Core Code Agentic Tools:
    generate_repo_map,
    auto_test_and_repair,
    manage_code_checkpoint,
    # Enterprise Security Enclave & Multi-Modal Boundary Enforcement:
    enterprise_security_manage,
    astra_security_manage,
    # Strategic Discipline Council & Invariant Gates:
    consult_plan_gap_analysis,
    review_plan_invariant_gate,
    dispatch_discipline_worker,
    # Mission Hierarchy & Universal Work Queue:
    manage_mission_hierarchy,
    schedule_work_queue,
    trace_artifact_lineage,
    # Knowledge, Benchmark & Performance Evaluation:
    query_knowledge_graph,
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
]


def _deduplicate_tools(tools: list[BaseTool]) -> list[BaseTool]:
    """Return ``tools`` with repeated declarations removed, preserving order.

    Several builtin modules export backwards-compatible aliases (for example
    ``run_nvidia_avo_step`` re-exports ``run_variation_operator_step``), so the
    literal above can name a single tool object more than once.
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


BUILTIN_TOOLS = _deduplicate_tools(BUILTIN_TOOLS)

SUBAGENT_TOOLS = [
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


def _is_host_bash_tool(tool: object) -> bool:
    """Return True if the tool config represents a host-bash execution surface."""
    group = getattr(tool, "group", None)
    use = getattr(tool, "use", None)
    if group == "bash":
        return True
    if use == "agent_workspace.sandbox.tools:bash_tool":
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
    `initialize_mcp_tools()` from agent_workspace.mcp module.

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
    builtin_tools = BUILTIN_TOOLS.copy()
    if is_mcp_task_runtime_available():
        builtin_tools.extend((list_background_tasks, cancel_background_task))
    if include_upload_tool:
        builtin_tools.append(list_uploaded_files)
    skill_evolution_config = getattr(config, "skill_evolution", None)
    if getattr(skill_evolution_config, "enabled", False):
        from agent_workspace.tools.skill_manage_tool import skill_manage_tool

        builtin_tools.append(skill_manage_tool)

    # Add subagent tools only if enabled via runtime parameter
    if subagent_enabled:
        builtin_tools.extend(SUBAGENT_TOOLS)
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
            from agent_workspace.config.extensions_config import ExtensionsConfig
            from agent_workspace.mcp.cache import get_cached_mcp_tools

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
        from agent_workspace.tools.builtins.invoke_acp_agent_tool import build_invoke_acp_agent_tool

        if app_config is None:
            from agent_workspace.config.acp_config import get_acp_agents

            acp_agents = get_acp_agents()
        else:
            acp_agents = getattr(config, "acp_agents", {}) or {}
        if acp_agents:
            acp_tools.append(build_invoke_acp_agent_tool(acp_agents))
            logger.info(f"Including invoke_acp_agent tool ({len(acp_agents)} agent(s): {list(acp_agents.keys())})")
    except Exception as e:
        logger.warning(f"Failed to load ACP tool: {e}")

    logger.info(f"Total tools loaded: {len(loaded_tools)}, built-in tools: {len(builtin_tools)}, MCP tools: {len(mcp_tools)}, ACP tools: {len(acp_tools)}")

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
    return unique_tools
