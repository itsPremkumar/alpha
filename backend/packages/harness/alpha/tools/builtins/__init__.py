from .a2a_tool import a2a_tool
from .action_transaction_tool import execute_transactional_action
from .adaptive_autonomy_tool import check_or_set_autonomy_profile
from .agency_competence_tool import evaluate_agent_competence
from .agent_message_tool import agent_message_tool, agent_observe_tool
from .artifact_lineage_tool import trace_artifact_lineage
from .ask_oracle_tool import ask_oracle
from .ast_grep_tool import ast_grep_rewrite, ast_grep_search
from .autonomous_benchmark_tool import run_autonomous_benchmark_eval
from .autonomous_command_tool import execute_slash_command_tool, identify_autonomous_command_tool
from .autonomy_control_tool import autonomy_control

# Autonomous one-prompt planner: raw prompt -> fully decided execution plan
from .autoplan_tool import build_autonomous_plan
from .avo_lineage_tool import run_avo_variation
from .background_tasks_tool import cancel_background_task, list_background_tasks
from .batch_task_tool import batch_status, batch_task, cancel_batch

# Enterprise Operating System & Cognitive Collaboration Tools
from .blackboard_tool import blackboard_query, blackboard_record_evidence
from .bot_roster_tool import bot_roster_tool
from .boulder_checkpoint_tool import boulder_checkpoint_manage
from .browser_supervisor_tool import browser_navigate_and_inspect
from .canvas_widget_tool import canvas_widget_tool
from .clarification_tool import ask_clarification_tool
from .code_agentic_core import (
    auto_test_and_repair,
    generate_repo_map,
    manage_code_checkpoint,
    # P4: canonical past-session memory search (FTS5 + BM25 with cron demotion).
    # It was defined here but never re-exported, which left the tool
    # unreachable; the historical bash-based memory tool stays an alias.
    search_session_memory,
    verify_web_ui_visual_regression,
)
from .code_mode_tool import code_mode_tool
from .cognitive_compiler_tool import compile_cognitive_plan
from .cognitive_memory_tiering_tool import (
    consolidate_cognitive_memory,
    recall_agent_memory,
)
from .cognitive_memory_tool import cognitive_memory_tool

# Cognitive Plan Mode: 8-Dimensional Strategic Evaluation & Autonomous Dispatch
from .cognitive_plan_tool import cognitive_plan
from .company_tool import company_tool

# Strategic Autonomous Control Plane Tools
from .compile_mission_tool import compile_mission
from .computer_system_one_tool import desktop_system_one_action_tool
from .computer_worker_tool import execute_sandboxed_computer_action
from .consequence_tool import simulate_consequences
from .context_as_data_tool import manage_context_data
from .contrastive_trajectory_replay_tool import (
    query_contrastive_memory,
    record_trajectory_outcome,
)
from .credential_request_tool import request_secure_credential
from .cronjob_manage_tool import cronjob_manage
from .curriculum_tool import generate_curriculum_plan
from .deep_agent_tool import delegate_to_deep_agent, inspect_deep_agent_telemetry, list_available_deep_agents
from .deep_research_tool import deep_research

# Keyless web search ported from AgentEye: httpx-first fallback chain with an
# optional ddgs fallback whose absence is reported honestly (never faked).
from .deep_web_search_tool import deep_web_search
from .deliberation_tool import deliberation_tool
from .delta_checkpoint_tool import create_workflow_checkpoint
from .differential_invariant_fuzzer_tool import run_differential_regression_oracle

# Strategic Discipline Council (Multi-Perspective Governance Profile)
from .discipline_team_tool import (
    consult_plan_gap_analysis,
    dispatch_discipline_worker,
    review_plan_invariant_gate,
)
from .dreaming_tool import consolidate_memory_dream
from .durable_replay_tool import manage_durable_orchestration
from .dynamic_tool_synthesizer_tool import list_dynamic_tools, synthesize_runtime_tool

# Enterprise Security Enclave & Telemetry Plane
from .enclave_security_tool import astra_security_manage, enterprise_security_manage
from .environment_auto_healer_tool import diagnose_and_heal_environment
from .epistemic_belief_tool import evaluate_epistemic_claim
from .estop_tool import emergency_stop_manage
from .evaluation_benchmark_tool import run_task_evaluation_benchmark
from .evidence_matrix_tool import audit_finish_first_evidence
from .executable_skill_tool import invoke_python_skill_tool
from .experience_tool import consult_experience
from .five_pass_search_tool import compile_five_pass_search
from .goal_engine_tool import goal_engine_tool
from .goal_integrity_tool import goal_integrity_tool
from .group_chat_tool import group_chat_tool
from .harness_refine_tool import harness_refine_tool

# Persistent Task Execution & Multi-Session Boulder Tools
from .hashline_tool import hashline_edit, hashline_read
from .hyperplan_tool import hyperplan_review_manage

# Frontier Autonomous Software Engineering Suite Tools
from .introspective_tree_search_tool import run_introspective_tree_search
from .job_tool import job_tool
from .kanban_board_tool import kanban_board_tool
from .keyless_web_search_tool import keyless_web_search
from .kibitzer_tool import kibitzer_nudge_manage
from .knowledge_graph_tool import query_knowledge_graph
from .learning_graph_tool import learning_graph_manage
from .list_uploaded_files_tool import list_uploaded_files
from .lsp_intelligence_tool import query_language_server_symbol
from .metacognitive_tool import check_metacognitive_health

# Mission Hierarchy, Work Queue DAG & Universal Artifact Lineage
from .mission_hierarchy_tool import manage_mission_hierarchy, schedule_work_queue
from .moa_reasoning_tool import moa_multi_model_reasoning

# Free local-first OS computer use & laptop automation (Module C): guarded
# desktop screenshot / UI-tree / mouse / keyboard / window tools.
from .os_computer_tool import (
    desktop_inspect_ui_tree_tool,
    desktop_keyboard_action_tool,
    desktop_mouse_action_tool,
    desktop_screenshot_tool,
    desktop_window_manage_tool,
)
from .peer_network_tool import alpha_peer_network_tool

# Enterprise Harness Expansion
from .performance_registry_tool import manage_model_performance_registry
from .present_file_tool import present_file_tool
from .problem_model_tool import compile_problem_model
from .process_handle_tool import process_handle_tool
from .program_slicing_tool import compute_program_slice
from .progress_card_tool import update_progress_card
from .propose_skill_tool import propose_skill_tool
from .python_repl_tool import python_repl_tool

# Frontier Governance, Context Superintelligence & Truth Engine
from .quality_council_tool import deliberate_artifact_quality
from .reflexion_tool import manage_reflexion_memory
from .repo_twin_tool import inspect_repo_twin

# Autonomous Reproduction and Experience Memory Tools
from .reproduction_tool import reproduce_and_verify
from .reversible_delete_tool import reversible_delete
from .review_skill_package_tool import review_skill_package
from .rsi_engine_tool import run_rsi_cycle
from .self_documentation_tool import search_project_docs
from .self_heal_tool import self_heal_diagnose

# Bounded Recursive Self-Improvement Loop
from .self_improvement_tool import ralph_loop_tool, self_improvement_loop_tool
from .semantic_git_delta_tool import analyze_semantic_git_delta
from .session_search_tool import session_search_tool
from .setup_agent_tool import setup_agent

# Autonomous Skill Synthesis & Swarm Orchestration Tools
from .skill_forge_tool import forge_skill_from_trace
from .skill_workshop_tool import synthesize_reusable_skill
from .skills_hub_tool import skills_hub_manage
from .smart_approval_tool import verify_command_approval

# Frontier autonomous engineering engines: stigmergic mesh, LSP intelligence,
# dynamic tool metacompiler, autonomous benchmarking, semantic git delta and
# tiered cognitive memory.
from .stigmergic_mesh_tool import emit_stigmergic_event, query_stigmergic_traces
from .structural_ast_reconciler_tool import reconcile_structural_ast_conflicts
from .subagent_control_tool import subagent_control
from .supervision_tool import supervision_tool
from .swarm_tool import swarm_tool
from .task_tool import task_tool

# Environment Interaction & Perception Fabric
from .tom_consult_tool import tom_consult
from .tool_search_tool import catalog_tool_call, catalog_tool_describe, catalog_tool_search
from .trajectory_audit_tool import trajectory_audit_tool
from .update_agent_tool import update_agent

# Autonomous Agentic Variation Operators (AVO) Optimization Plane
from .variation_operator_tool import run_nvidia_avo_step, run_variation_operator_step
from .view_image_tool import view_image_tool
from .visual_verification_tool import visual_verify_artifact
from .wake_gate_tool import await_task_event
from .workflow_dag_tool import workflow_dag_manage

__all__ = [
    "setup_agent",
    "update_agent",
    "present_file_tool",
    "propose_skill_tool",
    "review_skill_package",
    "session_search_tool",
    "ask_clarification_tool",
    "view_image_tool",
    "task_tool",
    "batch_task",
    "batch_status",
    "cancel_batch",
    "list_uploaded_files",
    "list_background_tasks",
    "cancel_background_task",
    "python_repl_tool",
    "harness_refine_tool",
    "agent_message_tool",
    "agent_observe_tool",
    "alpha_peer_network_tool",
    "process_handle_tool",
    "invoke_python_skill_tool",
    "bot_roster_tool",
    "swarm_tool",
    "group_chat_tool",
    "kanban_board_tool",
    "canvas_widget_tool",
    "goal_engine_tool",
    "trajectory_audit_tool",
    "code_mode_tool",
    "catalog_tool_search",
    "catalog_tool_describe",
    "catalog_tool_call",
    "consolidate_memory_dream",
    "update_progress_card",
    "learning_graph_manage",
    "verify_command_approval",
    "moa_multi_model_reasoning",
    "emergency_stop_manage",
    "skills_hub_manage",
    "cronjob_manage",
    "browser_navigate_and_inspect",
    "hashline_read",
    "hashline_edit",
    "workflow_dag_manage",
    "boulder_checkpoint_manage",
    "kibitzer_nudge_manage",
    "hyperplan_review_manage",
    "ast_grep_search",
    "ast_grep_rewrite",
    "tom_consult",
    "ask_oracle",
    "reproduce_and_verify",
    "consult_experience",
    "compile_mission",
    "evaluate_epistemic_claim",
    "run_rsi_cycle",
    "simulate_consequences",
    "self_heal_diagnose",
    "blackboard_record_evidence",
    "blackboard_query",
    "emit_stigmergic_event",
    "query_stigmergic_traces",
    "query_language_server_symbol",
    "synthesize_runtime_tool",
    "list_dynamic_tools",
    "run_autonomous_benchmark_eval",
    "analyze_semantic_git_delta",
    "consolidate_cognitive_memory",
    "recall_agent_memory",
    "compile_cognitive_plan",
    "execute_transactional_action",
    "run_avo_variation",
    "inspect_repo_twin",
    "check_metacognitive_health",
    "forge_skill_from_trace",
    "generate_curriculum_plan",
    "evaluate_agent_competence",
    "compile_five_pass_search",
    "create_workflow_checkpoint",
    "build_autonomous_plan",
    "ralph_loop_tool",
    "self_improvement_loop_tool",
    "run_nvidia_avo_step",
    "run_variation_operator_step",
    "deliberate_artifact_quality",
    "manage_context_data",
    "check_or_set_autonomy_profile",
    "audit_finish_first_evidence",
    "astra_security_manage",
    "enterprise_security_manage",
    "manage_mission_hierarchy",
    "schedule_work_queue",
    "trace_artifact_lineage",
    "manage_model_performance_registry",
    "query_knowledge_graph",
    "search_project_docs",
    "autonomy_control",
    "reversible_delete",
    "run_task_evaluation_benchmark",
    "execute_sandboxed_computer_action",
    "manage_durable_orchestration",
    "consult_plan_gap_analysis",
    "review_plan_invariant_gate",
    "dispatch_discipline_worker",
    "cognitive_plan",
    "subagent_control",
    "deliberation_tool",
    "job_tool",
    "supervision_tool",
    "goal_integrity_tool",
    "a2a_tool",
    "company_tool",
    "generate_repo_map",
    "auto_test_and_repair",
    "manage_code_checkpoint",
    "visual_verify_artifact",
    "verify_web_ui_visual_regression",
    "search_session_memory",
    "manage_reflexion_memory",
    "compile_problem_model",
    "execute_slash_command_tool",
    "identify_autonomous_command_tool",
    "cognitive_memory_tool",
    "deep_research",
    "synthesize_reusable_skill",
    "request_secure_credential",
    "await_task_event",
    "run_introspective_tree_search",
    "compute_program_slice",
    "run_differential_regression_oracle",
    "diagnose_and_heal_environment",
    "reconcile_structural_ast_conflicts",
    "query_contrastive_memory",
    "record_trajectory_outcome",
    "delegate_to_deep_agent",
    "list_available_deep_agents",
    "inspect_deep_agent_telemetry",
    "keyless_web_search",
    "deep_web_search",
    "desktop_screenshot_tool",
    "desktop_inspect_ui_tree_tool",
    "desktop_mouse_action_tool",
    "desktop_keyboard_action_tool",
    "desktop_window_manage_tool",
    "desktop_system_one_action_tool",
]
