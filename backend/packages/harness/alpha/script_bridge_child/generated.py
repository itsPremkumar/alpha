"""GENERATED FILE - DO NOT EDIT.

Regenerate with::

    python -m alpha.tools.script_bridge.stubgen --write

Generated from the live tool registry by
``alpha.tools.script_bridge.stubgen``.  Every function below is a thin RPC shim:
it sends the call over the bridge socket and returns **only** what the tool
returned.  The parent-side dispatcher re-derives the allowlist, the limits and
the authorisation decision for every call, so nothing here is trusted.
"""

from __future__ import annotations

from .client import get_client, verify_stub_fingerprint

#: Fingerprint of the tool registry this file was generated from.
STUB_REGISTRY_SHA256 = "85bb11cfdab63a13756f263f18b782517eb4c0a12dcec04f6e57bc8896c79cd5"

#: Registry snapshot the generator saw: name -> {"description": str}
STUB_TOOL_INDEX = {
    "a2a_protocol": {
        "description": "Discover agents, inspect Google A2A capability cards, and delegate tasks."
    },
    "alpha_peer_network": {
        "description": "Use the free Alpha-to-Alpha network. Actions:"
    },
    "analyze_semantic_git_delta": {
        "description": "Convert a git line diff into structured semantic AST transformations and risk. Rather than reporting which lines changed, this tool reports what meaning"
    },
    "ask_clarification": {
        "description": "Ask the user for clarification when you need more information to proceed. Use this tool when you encounter situations where you cannot proceed without user input: - **Missing information**: Required d"
    },
    "ask_oracle": {
        "description": "Consult the isolated Oracle advisor on technical standards, API contracts, or architecture best practices. Uses an isolated subagent interface to retrieve authoritative guidance without polluting the "
    },
    "ast_grep_rewrite": {
        "description": "Rewrite code using structural AST pattern and template."
    },
    "ast_grep_search": {
        "description": "Search code using structural pattern matching."
    },
    "audit_finish_first_evidence": {
        "description": "Track, corroborate, and audit physical execution proof for task claims. Implements the MYTHOS Finish-First Doctrine: tasks cannot be declared 'done'"
    },
    "auto_test_and_repair": {
        "description": "Execute the project's test suite and parse failures into structured diagnostic traces. Enables tight 'Read -> Act -> Test -> Auto-Repair' feedback loops. If tests fail, returns"
    },
    "autonomy_control": {
        "description": "Inspect server-observed autonomy readiness, failures, or activity. This free, offline decision layer never calls a model, provider, network, or"
    },
    "await_task_event": {
        "description": "Register an event wake gate for one or more asynchronous background tasks. Instead of repeatedly checking task status in a loop and wasting context tokens,"
    },
    "blackboard_query": {
        "description": "Query verified evidence and active states from the 20-Plane Shared Blackboard."
    },
    "blackboard_record_evidence": {
        "description": "Record an evidence item into the 20-Plane Shared Blackboard."
    },
    "bot_roster": {
        "description": "Create, configure, inspect, and monitor autonomous AI agent profiles and fleet operations."
    },
    "boulder_checkpoint_manage": {
        "description": "Manage persistent multi-session task checkpoints. Actions: 'create', 'status', 'update_step', 'append_session', 'complete', 'clear'."
    },
    "browser_navigate_and_inspect": {
        "description": "Automate headless browser navigation, DOM inspection, and System One driven interaction."
    },
    "build_autonomous_plan": {
        "description": "Turn one raw user prompt into a fully decided autonomous execution plan. Decides research need, subagent delegation, subtask split with waves,"
    },
    "canvas_widget": {
        "description": "Render and manage live, interactive HTML5/JS Canvas widgets for chat and dashboards."
    },
    "catalog_tool_describe": {
        "description": "Retrieve full schema, parameter definitions, and documentation for a catalog tool. Call this once you have found a tool with `catalog_tool_search` to inspect its parameters."
    },
    "catalog_tool_search": {
        "description": "Search available tools in the universal catalog by name, category, or description keywords. This provides deferred tool discovery: instead of loading dozens of tool definitions into"
    },
    "check_metacognitive_health": {
        "description": "Assess metacognitive health, detect cognitive biases, and calibrate confidence. Monitors for confirmation bias, premature convergence, plan stagnation,"
    },
    "check_or_set_autonomy_profile": {
        "description": "Inspect or configure the user autonomy profile and evaluate action authorization. Enforces the 4-tier autonomy governance model:"
    },
    "cognitive_memory_tool": {
        "description": "Access, search, and update the agent's Multi-Tier Cognitive Memory System. Actions:"
    },
    "cognitive_plan": {
        "description": "Evaluate strategic execution parameters and autonomously trigger execution across subagents, swarms, or bots."
    },
    "company_os": {
        "description": "Manage autonomous AI companies, perpetual collectives, work discovery, and self-improvement."
    },
    "compile_cognitive_plan": {
        "description": "Compile a mission using the P0-P21 Cognitive Compiler. Generates competing Strategy Candidates (A/B/C) with trade-off scoring,"
    },
    "compile_five_pass_search": {
        "description": "Compile a natural language question into 5 parallel search facets, including adversarial contradiction discovery. Funnels search across: Discovery, Specific Evidence, Adversarial Contradiction, Fact V"
    },
    "compile_mission": {
        "description": "Compile an ambiguous user prompt into a formal, structured Mission contract with R0-R6 risk classification. Extracts core intent, latent needs, hard/soft/forbidden constraints, acceptance criteria,"
    },
    "compile_problem_model": {
        "description": "Compile a raw goal or user prompt into a structured 12-factor AVO Problem Model. Analyzes objective, domain, entities, explicit constraints, underlying assumptions,"
    },
    "compute_program_slice": {
        "description": "Compute backward or forward program slices across Python code using AST and PDG graphs. Enables surgical debugging and blast-radius analysis:"
    },
    "consolidate_cognitive_memory": {
        "description": "Manage the three-tier cognitive memory system and run the consolidation daemon. Working memory is an ephemeral capacity-bounded scratchpad for intermediate"
    },
    "consolidate_memory_dream": {
        "description": "Run a 3-Phase Dreaming Memory Consolidation cycle (Light, REM, Deep Sleep). Synthesizes short-term execution notes and failure logs, extracts durable insights,"
    },
    "consult_experience": {
        "description": "Consult or update the Episodic Experience Memory to retrieve past lessons or record new ones. Action 'query': Retrieves relevant past failure modes, lessons learned, and guidelines for the current tas"
    },
    "consult_plan_gap_analysis": {
        "description": "Run pre-planning gap analysis (powered by Enterprise Strategic Consultant profile) to catch edge cases and UI requirements."
    },
    "create_workflow_checkpoint": {
        "description": "Create a durable, cryptographic SHA256-verified workflow checkpoint. Supports 'full', 'incremental', and 'delta' snapshots with state rollback and historical verification."
    },
    "cronjob_manage": {
        "description": "Manage autonomous background cron tasks and periodic health/lint checks."
    },
    "deep_research": {
        "description": "Conduct bounded multi-lane deep research with explicit evidence status. Compiles a 5-pass search plan (Discovery, Specific Evidence, Adversarial Contradiction,"
    },
    "deep_web_search": {
        "description": "Research a question across the keyless web-search chain and return findings with citations. Ports AgentEye's research engine: expands the question into related"
    },
    "deliberate": {
        "description": "Consult multiple independent AI models via anonymous council or debate before deciding."
    },
    "deliberate_artifact_quality": {
        "description": "Deliberate the quality and safety of an artifact using the 5-Deliberator Council. Applies the Agent Prime rule: 'Never let one worker both produce and certify high-risk output.'"
    },
    "desktop_inspect_ui_tree": {
        "description": "Inspect a window's clickable buttons, inputs, and checkboxes via the OS accessibility tree -- zero tokens. Returns each element as {name, type, bbox: [left, top, right, bottom],"
    },
    "desktop_keyboard_action": {
        "description": "Type text, press a key, or execute a keyboard hotkey -- sentinel-guard checked. Destructive hotkey combinations (Win+L, desktop Alt+F4, Shift+Delete,"
    },
    "desktop_mouse_action": {
        "description": "Dispatch a guarded mouse click, move, drag, or scroll on the desktop. Every call passes the sentinel guard before any backend loads: the"
    },
    "desktop_screenshot": {
        "description": "Capture a free local screenshot of the desktop or one window (PNG, base64). Uses the mss backend (target <10ms) -- no cloud vision API, no tokens. When"
    },
    "desktop_system_one_action": {
        "description": "Choose and safely execute one indexed desktop action using System One/Laya. The tool observes the operating-system accessibility tree, presents only a"
    },
    "desktop_window_manage": {
        "description": "List or focus windows, launch desktop software, or bind/unbind the window-boundary lock. Actions:"
    },
    "diagnose_and_heal_environment": {
        "description": "Scan build manifests, error logs, and virtualenv health to automatically repair environments. Inspects pyproject.toml, requirements.txt, package.json, and Cargo.toml. Autonomously"
    },
    "dispatch_discipline_worker": {
        "description": "Dispatch work by category to specialized discipline models (e.g. visual engineering, algorithmic ultrabrain, plan reviewer)."
    },
    "emergency_stop_manage": {
        "description": "Manage the runtime Emergency Stop (ESTOP) global pause state. Allows operators or supervisor agents to immediately suspend all autonomous background tasks,"
    },
    "emit_stigmergic_event": {
        "description": "Deposit or release a stigmergic pheromone signal on a code entity. Stigmergic coordination lets a swarm of agents coordinate without direct"
    },
    "enterprise_security_manage": {
        "description": "Manage enterprise security controls, enclave boundaries, spatio-temporal memory, and autonomous goal pursuit."
    },
    "evaluate_agent_competence": {
        "description": "Evaluate statistical competence using 95% Wilson confidence intervals and curiosity scoring."
    },
    "evaluate_epistemic_claim": {
        "description": "Register, evaluate, or update epistemic truth claims with Bayesian calibration and falsification tests. Action 'register': Adds a new working hypothesis/assumption with an explicit falsification test."
    },
    "execute_sandboxed_computer_action": {
        "description": "Evaluate a desktop/terminal command against the 3-tier blast-radius safety gate. The gate classifies and validates; it never runs anything. ``SAFE`` commands"
    },
    "execute_slash_command": {
        "description": "Execute a Master Slash Command from the catalog across 28 categories."
    },
    "execute_transactional_action": {
        "description": "Execute a file action within an ACID-like 4-stage transaction envelope with automatic rollback. Stages: PREPARE (shadow snapshot) -> VALIDATE -> COMMIT -> VERIFY."
    },
    "external_job": {
        "description": "Manage asynchronous background OS jobs decoupled from reasoning loops."
    },
    "forge_skill_from_trace": {
        "description": "Forge a tested, parameterized SKILL.md tool from a sequence of successful execution steps. Extracts parameters, dedups via content hash, and registers the skill for autonomous reuse."
    },
    "generate_curriculum_plan": {
        "description": "Analyze agent capability gaps and generate an autonomous training curriculum. Computes urgency priority = (Target - Current) / Difficulty and schedules targeted exercises."
    },
    "generate_repo_map": {
        "description": "Generate a token-efficient structural map of the repository with AST symbol signatures. Provides high-level codebase awareness (directory layout, key classes, functions, and methods)"
    },
    "goal_engine": {
        "description": "Manage continuous, goal-driven autonomous execution loops with self-healing."
    },
    "goal_integrity": {
        "description": "Audit plans and proposed subtasks for goal drift, scope creep, and overengineering."
    },
    "group_chat": {
        "description": "Collaborate in multi-agent group chat rooms with adaptive speaker modes and voting. Vastly expands on Hermes Bot Mode with 5 speaker selection strategies (mention,"
    },
    "harness_refine": {
        "description": "Manage self-improving Continual Harness state and trigger online refinement. Inspired by Prime Agent's /refine and durable harness state. Entries record"
    },
    "hashline_edit": {
        "description": "Edit a file using verified content-hash line references (e.g. start_ref='12#VK', end_ref='15#MB'). Eliminates whitespace and line drift errors."
    },
    "hashline_read": {
        "description": "Read a file with content-hashed lines (LINE#HASH| content). Used to get stable line references before editing."
    },
    "hyperplan_review_manage": {
        "description": "Run hostile multi-agent audit on an execution plan across 4 orthogonal dimensions (gaps, architecture, security, testability). Returns plan_hash for execution pinning; BLOCKED plans must not execute."
    },
    "identify_autonomous_command": {
        "description": "Identify which Master Slash Command to execute for the current task, error, or lifecycle stage."
    },
    "inspect_deep_agent_telemetry": {
        "description": "Inspect bounded telemetry for an isolated deep agent session."
    },
    "inspect_repo_twin": {
        "description": "Inspect repository digital twin, AST symbol dependencies, and blast radius. Discovers build systems (pip, npm, cargo), test frameworks (pytest, jest), CI/CD workflows,"
    },
    "kanban_board": {
        "description": "Manage tasks on the collaborative Kanban board with review gates and DAG unblocking. Tasks progress across columns: backlog -> todo -> in_progress -> in_review -> done."
    },
    "keyless_web_search": {
        "description": "Search the web without any API key by falling back across keyless engines until one answers. Ports AgentEye's keyless chain (DuckDuckGo HTML, DuckDuckGo via Jina Reader,"
    },
    "kibitzer_nudge_manage": {
        "description": "Manage resident memory hints (Kibitzer sidecar). Actions: 'add_memory', 'observe', 'reset'."
    },
    "learning_graph_manage": {
        "description": "Manage and query the self-evolving Knowledge & Learning Graph. Maintains topological connections across learned user preferences, codebase invariants,"
    },
    "list_available_deep_agents": {
        "description": "List available deep specialist agents and their capabilities. Returns compact descriptors for every autonomous deep specialist without"
    },
    "list_dynamic_tools": {
        "description": "Inspect the registry of runtime-synthesized agent tools."
    },
    "manage_code_checkpoint": {
        "description": "Create a lightweight rollback checkpoint or restore the repository to a previous clean state. Allows safe refactoring with instant rollback guarantees before risky code mutations."
    },
    "manage_context_data": {
        "description": "Manage and transform massive context variables programmatically without prompt bloat. Implements the Prime Agent Context-as-Data pattern: contexts are stored as immutable"
    },
    "manage_durable_orchestration": {
        "description": "Manage durable event journals, task checkpointing, and crash-resilient replay."
    },
    "manage_mission_hierarchy": {
        "description": "Manage the 6-level Goal -> Mission -> Task -> Subtask -> Action -> ToolCall execution hierarchy."
    },
    "manage_model_performance_registry": {
        "description": "Manage empirical model benchmarks and dynamically route tasks to Pareto-optimal models."
    },
    "manage_reflexion_memory": {
        "description": "Manage Reflexion failure memory."
    },
    "moa_multi_model_reasoning": {
        "description": "Execute a Mixture-of-Agents (MoA) parallel multi-LLM reasoning round. Dispatches complex questions to multiple candidate models simultaneously, redacts"
    },
    "present_files": {
        "description": "Make files visible to the user for viewing and rendering in the client interface. When to use the present_files tool: - Making any file available for the user to view, download, or interact with"
    },
    "process_handle": {
        "description": "Manage asynchronous background processes, check handles, and tail output. Inspired by Prime Agent's rlm/bash.py background execution model. Allows long-running"
    },
    "propose_skill": {
        "description": "Propose a new skill for human review and installation. Use this when repeated work deserves a reusable skill, or the user asks"
    },
    "query_contrastive_memory": {
        "description": "Retrieve negative constraints and failed reasoning paths from episodic memory. Searches past task executions to find similar failure signatures, erroneous hypotheses,"
    },
    "query_knowledge_graph": {
        "description": "Manage and query the cross-enterprise Knowledge Graph for multi-hop dependencies and blast radius."
    },
    "query_language_server_symbol": {
        "description": "Query compiler-grade symbol intelligence for a workspace via LSP, LSIF or SCIP. Use this tool to jump to definitions, enumerate references, read document"
    },
    "query_stigmergic_traces": {
        "description": "Read the shared stigmergic pheromone field of the agent swarm."
    },
    "ralph_loop": {
        "description": "Run a task through bounded self-improvement rounds until a promise holds. Use when an outcome is objectively checkable and a first attempt often"
    },
    "recall_agent_memory": {
        "description": "Recall memories across working, episodic and semantic tiers with hybrid ranking. Retrieval blends Okapi BM25 lexical relevance with cosine vector similarity"
    },
    "reconcile_structural_ast_conflicts": {
        "description": "Reconcile multi-agent concurrent modifications via semantic 3-way AST node merging. Replaces line-based git conflict markers with structural AST reconciliation. Merges"
    },
    "record_trajectory_outcome": {
        "description": "Record an episodic trajectory outcome into contrastive memory. Stores tuples of (Failure Signature, Erroneous Hypothesis, Failed Patch, Winning Resolution)"
    },
    "reproduce_and_verify": {
        "description": "Execute test-driven autonomous bug reproduction and verification. Phase 'prepare': Synthesizes reproduction script and verifies that it FAILS before code modification."
    },
    "reversible_delete": {
        "description": "Plan, request approval for, execute, or restore reversible file quarantine. This free, local safety boundary never hard-deletes data. ``plan`` creates a"
    },
    "review_plan_invariant_gate": {
        "description": "Rigorous plan invariant review (powered by Enterprise Invariant Gatekeeper profile) to prevent rubber-stamping and defects."
    },
    "review_skill_package": {
        "description": "Inspect a skill package without activating, installing, executing, or editing it. Use this tool only for skill review workflows. The target package is"
    },
    "run_autonomous_benchmark_eval": {
        "description": "Evaluate agent code patches against coding benchmarks and compute pass@k. Each problem is executed in an isolated git workspace: the repository is"
    },
    "run_avo_variation": {
        "description": "Run an Autonomous Value Optimization (AVO) variation step. Applies the strict matches-or-improves commit policy against the lineage tree."
    },
    "run_differential_regression_oracle": {
        "description": "Execute differential invariant validation and regression testing between code revisions. Synthesizes property-based fuzz tests and executes dual shadow sandboxes with randomized"
    },
    "run_introspective_tree_search": {
        "description": "Execute tree-guided multi-path search (I-MCTS / CodeTree) to resolve complex bugs. Coordinates Monte Carlo Tree Search with introspective node expansion, pruning invalid"
    },
    "run_rsi_cycle": {
        "description": "Execute an autonomous Recursive Self-Improvement (RSI) cycle: Bottleneck -> Hypothesis -> Candidate -> A/B Test -> Holdout -> Promote. Analyzes agent performance bottlenecks, generates an optimization"
    },
    "run_task_evaluation_benchmark": {
        "description": "Run standard agentic benchmarks (research, coding, browser, computer, multi-agent) and report scores."
    },
    "run_variation_operator_step": {
        "description": "Execute an Autonomous Agentic Variation Operator (AVO) step. Supports multi-dimensional vector evaluation, Pareto dominance tracking,"
    },
    "schedule_work_queue": {
        "description": "Manage the durable DAG task scheduler, execution queue, and budget guardrails."
    },
    "search_project_docs": {
        "description": "Search, read, or inspect the current project's documentation offline. Use this for questions about Alpha configuration, commands, architecture,"
    },
    "search_session_memory": {
        "description": "Search long-term conversation history using SQLite FTS5 full-text indexing with BM25 ranking. Applies cron session demotion to prevent vocabulary starvation ('recall blindness') and"
    },
    "self_heal_diagnose": {
        "description": "Diagnose runtime health anomalies, stale lock files, or hanging operations and execute self-healing actions. Scans workspace for locks (such as .git/index.lock or .pytest_cache/.lock) older than thres"
    },
    "session_search": {
        "description": "Search past conversations or read one thread's history. Discovery: session_search(query=\"router password\") searches every thread"
    },
    "simulate_consequences": {
        "description": "Simulate potential environmental side-effects, blast radius, and cascading failures before execution. Probes the host environment affordances, checks if the workspace is git-tracked, analyzes package"
    },
    "skills_hub_manage": {
        "description": "Manage Skills Hub discovery, AST security audits, and skill installation."
    },
    "supervisor_watchdog": {
        "description": "Monitor agent health, report heartbeats, inspect anomalies, and trigger recovery."
    },
    "swarm": {
        "description": "Evaluate, spawn, run, communicate with, and monitor bounded agent swarms."
    },
    "synthesize_reusable_skill": {
        "description": "Synthesize a structured and validated SKILL.md package from execution steps."
    },
    "synthesize_runtime_tool": {
        "description": "Synthesize, security-verify, self-test and register a new agent tool at runtime. The source is first screened by a static AST security validator that blocks"
    },
    "tom_consult": {
        "description": "Consult the Theory of Mind cognitive model to infer unstated user expectations, invariants, and pitfalls. Before executing high-impact, destructive, or ambiguous modifications, this tool models the hu"
    },
    "trace_artifact_lineage": {
        "description": "Manage artifact provenance, track causal derivation, and audit the ancestry graph of any output."
    },
    "trajectory_audit": {
        "description": "Inspect and export step-by-step reasoning and tool execution trajectories."
    },
    "update_progress_card": {
        "description": "Publish or update an in-place streaming progress draft card. Instead of polluting the chat with repetitive conversational status messages,"
    },
    "verify_command_approval": {
        "description": "Evaluate whether a shell command is safe to execute using Smart Approvals guardian. Strips shell comments to prevent injection bypasses, checks against dangerous destruction"
    },
    "verify_web_ui_visual_regression": {
        "description": "Verify web UI visual integrity and accessibility tree parity between HTML snapshots. Extracts semantic interactive elements and detects visual regression, missing"
    },
    "visual_verify_artifact": {
        "description": "Verify visual, structural, and layout integrity of an HTML/SVG/Canvas artifact."
    },
    "workflow_dag_manage": {
        "description": "Manage dependency-ordered and dynamic task graphs (DAG / DWE)."
    }
}

_DRIFT = verify_stub_fingerprint()
if _DRIFT is not None:  # pragma: no cover - fires only on real registry drift
    raise RuntimeError(_DRIFT)


def available_tool_names() -> list[str]:
    """Every tool this stub can name.  Whether a given call is *permitted* is
    decided parent-side; this list is a naming convenience only."""
    return sorted(STUB_TOOL_INDEX)


def tool(name: str, **kwargs) -> object:
    """Call any allowed tool by name.

    The allowlist is enforced parent-side, so a name outside it is refused with
    a reason rather than silently ignored.
    """
    return get_client().call(name, dict(kwargs))



def a2a_protocol(**kwargs) -> object:
    """Discover agents, inspect Google A2A capability cards, and delegate tasks.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``a2a_protocol`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("a2a_protocol", dict(kwargs))


def alpha_peer_network(**kwargs) -> object:
    """Use the free Alpha-to-Alpha network. Actions:

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``alpha_peer_network`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("alpha_peer_network", dict(kwargs))


def analyze_semantic_git_delta(**kwargs) -> object:
    """Convert a git line diff into structured semantic AST transformations and risk. Rather than reporting which lines changed, this tool reports what meaning

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``analyze_semantic_git_delta`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("analyze_semantic_git_delta", dict(kwargs))


def ask_clarification(**kwargs) -> object:
    """Ask the user for clarification when you need more information to proceed. Use this tool when you encounter situations where you cannot proceed without user input: - **Missing information**: Required d

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``ask_clarification`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("ask_clarification", dict(kwargs))


def ask_oracle(**kwargs) -> object:
    """Consult the isolated Oracle advisor on technical standards, API contracts, or architecture best practices. Uses an isolated subagent interface to retrieve authoritative guidance without polluting the 

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``ask_oracle`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("ask_oracle", dict(kwargs))


def ast_grep_rewrite(**kwargs) -> object:
    """Rewrite code using structural AST pattern and template.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``ast_grep_rewrite`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("ast_grep_rewrite", dict(kwargs))


def ast_grep_search(**kwargs) -> object:
    """Search code using structural pattern matching.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``ast_grep_search`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("ast_grep_search", dict(kwargs))


def audit_finish_first_evidence(**kwargs) -> object:
    """Track, corroborate, and audit physical execution proof for task claims. Implements the MYTHOS Finish-First Doctrine: tasks cannot be declared 'done'

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``audit_finish_first_evidence`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("audit_finish_first_evidence", dict(kwargs))


def auto_test_and_repair(**kwargs) -> object:
    """Execute the project's test suite and parse failures into structured diagnostic traces. Enables tight 'Read -> Act -> Test -> Auto-Repair' feedback loops. If tests fail, returns

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``auto_test_and_repair`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("auto_test_and_repair", dict(kwargs))


def autonomy_control(**kwargs) -> object:
    """Inspect server-observed autonomy readiness, failures, or activity. This free, offline decision layer never calls a model, provider, network, or

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``autonomy_control`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("autonomy_control", dict(kwargs))


def await_task_event(**kwargs) -> object:
    """Register an event wake gate for one or more asynchronous background tasks. Instead of repeatedly checking task status in a loop and wasting context tokens,

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``await_task_event`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("await_task_event", dict(kwargs))


def blackboard_query(**kwargs) -> object:
    """Query verified evidence and active states from the 20-Plane Shared Blackboard.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``blackboard_query`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("blackboard_query", dict(kwargs))


def blackboard_record_evidence(**kwargs) -> object:
    """Record an evidence item into the 20-Plane Shared Blackboard.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``blackboard_record_evidence`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("blackboard_record_evidence", dict(kwargs))


def bot_roster(**kwargs) -> object:
    """Create, configure, inspect, and monitor autonomous AI agent profiles and fleet operations.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``bot_roster`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("bot_roster", dict(kwargs))


def boulder_checkpoint_manage(**kwargs) -> object:
    """Manage persistent multi-session task checkpoints. Actions: 'create', 'status', 'update_step', 'append_session', 'complete', 'clear'.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``boulder_checkpoint_manage`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("boulder_checkpoint_manage", dict(kwargs))


def browser_navigate_and_inspect(**kwargs) -> object:
    """Automate headless browser navigation, DOM inspection, and System One driven interaction.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``browser_navigate_and_inspect`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("browser_navigate_and_inspect", dict(kwargs))


def build_autonomous_plan(**kwargs) -> object:
    """Turn one raw user prompt into a fully decided autonomous execution plan. Decides research need, subagent delegation, subtask split with waves,

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``build_autonomous_plan`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("build_autonomous_plan", dict(kwargs))


def canvas_widget(**kwargs) -> object:
    """Render and manage live, interactive HTML5/JS Canvas widgets for chat and dashboards.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``canvas_widget`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("canvas_widget", dict(kwargs))


def catalog_tool_describe(**kwargs) -> object:
    """Retrieve full schema, parameter definitions, and documentation for a catalog tool. Call this once you have found a tool with `catalog_tool_search` to inspect its parameters.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``catalog_tool_describe`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("catalog_tool_describe", dict(kwargs))


def catalog_tool_search(**kwargs) -> object:
    """Search available tools in the universal catalog by name, category, or description keywords. This provides deferred tool discovery: instead of loading dozens of tool definitions into

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``catalog_tool_search`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("catalog_tool_search", dict(kwargs))


def check_metacognitive_health(**kwargs) -> object:
    """Assess metacognitive health, detect cognitive biases, and calibrate confidence. Monitors for confirmation bias, premature convergence, plan stagnation,

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``check_metacognitive_health`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("check_metacognitive_health", dict(kwargs))


def check_or_set_autonomy_profile(**kwargs) -> object:
    """Inspect or configure the user autonomy profile and evaluate action authorization. Enforces the 4-tier autonomy governance model:

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``check_or_set_autonomy_profile`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("check_or_set_autonomy_profile", dict(kwargs))


def cognitive_memory_tool(**kwargs) -> object:
    """Access, search, and update the agent's Multi-Tier Cognitive Memory System. Actions:

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``cognitive_memory_tool`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("cognitive_memory_tool", dict(kwargs))


def cognitive_plan(**kwargs) -> object:
    """Evaluate strategic execution parameters and autonomously trigger execution across subagents, swarms, or bots.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``cognitive_plan`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("cognitive_plan", dict(kwargs))


def company_os(**kwargs) -> object:
    """Manage autonomous AI companies, perpetual collectives, work discovery, and self-improvement.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``company_os`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("company_os", dict(kwargs))


def compile_cognitive_plan(**kwargs) -> object:
    """Compile a mission using the P0-P21 Cognitive Compiler. Generates competing Strategy Candidates (A/B/C) with trade-off scoring,

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``compile_cognitive_plan`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("compile_cognitive_plan", dict(kwargs))


def compile_five_pass_search(**kwargs) -> object:
    """Compile a natural language question into 5 parallel search facets, including adversarial contradiction discovery. Funnels search across: Discovery, Specific Evidence, Adversarial Contradiction, Fact V

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``compile_five_pass_search`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("compile_five_pass_search", dict(kwargs))


def compile_mission(**kwargs) -> object:
    """Compile an ambiguous user prompt into a formal, structured Mission contract with R0-R6 risk classification. Extracts core intent, latent needs, hard/soft/forbidden constraints, acceptance criteria,

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``compile_mission`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("compile_mission", dict(kwargs))


def compile_problem_model(**kwargs) -> object:
    """Compile a raw goal or user prompt into a structured 12-factor AVO Problem Model. Analyzes objective, domain, entities, explicit constraints, underlying assumptions,

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``compile_problem_model`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("compile_problem_model", dict(kwargs))


def compute_program_slice(**kwargs) -> object:
    """Compute backward or forward program slices across Python code using AST and PDG graphs. Enables surgical debugging and blast-radius analysis:

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``compute_program_slice`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("compute_program_slice", dict(kwargs))


def consolidate_cognitive_memory(**kwargs) -> object:
    """Manage the three-tier cognitive memory system and run the consolidation daemon. Working memory is an ephemeral capacity-bounded scratchpad for intermediate

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``consolidate_cognitive_memory`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("consolidate_cognitive_memory", dict(kwargs))


def consolidate_memory_dream(**kwargs) -> object:
    """Run a 3-Phase Dreaming Memory Consolidation cycle (Light, REM, Deep Sleep). Synthesizes short-term execution notes and failure logs, extracts durable insights,

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``consolidate_memory_dream`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("consolidate_memory_dream", dict(kwargs))


def consult_experience(**kwargs) -> object:
    """Consult or update the Episodic Experience Memory to retrieve past lessons or record new ones. Action 'query': Retrieves relevant past failure modes, lessons learned, and guidelines for the current tas

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``consult_experience`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("consult_experience", dict(kwargs))


def consult_plan_gap_analysis(**kwargs) -> object:
    """Run pre-planning gap analysis (powered by Enterprise Strategic Consultant profile) to catch edge cases and UI requirements.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``consult_plan_gap_analysis`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("consult_plan_gap_analysis", dict(kwargs))


def create_workflow_checkpoint(**kwargs) -> object:
    """Create a durable, cryptographic SHA256-verified workflow checkpoint. Supports 'full', 'incremental', and 'delta' snapshots with state rollback and historical verification.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``create_workflow_checkpoint`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("create_workflow_checkpoint", dict(kwargs))


def cronjob_manage(**kwargs) -> object:
    """Manage autonomous background cron tasks and periodic health/lint checks.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``cronjob_manage`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("cronjob_manage", dict(kwargs))


def deep_research(**kwargs) -> object:
    """Conduct bounded multi-lane deep research with explicit evidence status. Compiles a 5-pass search plan (Discovery, Specific Evidence, Adversarial Contradiction,

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``deep_research`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("deep_research", dict(kwargs))


def deep_web_search(**kwargs) -> object:
    """Research a question across the keyless web-search chain and return findings with citations. Ports AgentEye's research engine: expands the question into related

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``deep_web_search`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("deep_web_search", dict(kwargs))


def deliberate(**kwargs) -> object:
    """Consult multiple independent AI models via anonymous council or debate before deciding.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``deliberate`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("deliberate", dict(kwargs))


def deliberate_artifact_quality(**kwargs) -> object:
    """Deliberate the quality and safety of an artifact using the 5-Deliberator Council. Applies the Agent Prime rule: 'Never let one worker both produce and certify high-risk output.'

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``deliberate_artifact_quality`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("deliberate_artifact_quality", dict(kwargs))


def desktop_inspect_ui_tree(**kwargs) -> object:
    """Inspect a window's clickable buttons, inputs, and checkboxes via the OS accessibility tree -- zero tokens. Returns each element as {name, type, bbox: [left, top, right, bottom],

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``desktop_inspect_ui_tree`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("desktop_inspect_ui_tree", dict(kwargs))


def desktop_keyboard_action(**kwargs) -> object:
    """Type text, press a key, or execute a keyboard hotkey -- sentinel-guard checked. Destructive hotkey combinations (Win+L, desktop Alt+F4, Shift+Delete,

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``desktop_keyboard_action`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("desktop_keyboard_action", dict(kwargs))


def desktop_mouse_action(**kwargs) -> object:
    """Dispatch a guarded mouse click, move, drag, or scroll on the desktop. Every call passes the sentinel guard before any backend loads: the

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``desktop_mouse_action`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("desktop_mouse_action", dict(kwargs))


def desktop_screenshot(**kwargs) -> object:
    """Capture a free local screenshot of the desktop or one window (PNG, base64). Uses the mss backend (target <10ms) -- no cloud vision API, no tokens. When

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``desktop_screenshot`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("desktop_screenshot", dict(kwargs))


def desktop_system_one_action(**kwargs) -> object:
    """Choose and safely execute one indexed desktop action using System One/Laya. The tool observes the operating-system accessibility tree, presents only a

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``desktop_system_one_action`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("desktop_system_one_action", dict(kwargs))


def desktop_window_manage(**kwargs) -> object:
    """List or focus windows, launch desktop software, or bind/unbind the window-boundary lock. Actions:

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``desktop_window_manage`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("desktop_window_manage", dict(kwargs))


def diagnose_and_heal_environment(**kwargs) -> object:
    """Scan build manifests, error logs, and virtualenv health to automatically repair environments. Inspects pyproject.toml, requirements.txt, package.json, and Cargo.toml. Autonomously

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``diagnose_and_heal_environment`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("diagnose_and_heal_environment", dict(kwargs))


def dispatch_discipline_worker(**kwargs) -> object:
    """Dispatch work by category to specialized discipline models (e.g. visual engineering, algorithmic ultrabrain, plan reviewer).

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``dispatch_discipline_worker`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("dispatch_discipline_worker", dict(kwargs))


def emergency_stop_manage(**kwargs) -> object:
    """Manage the runtime Emergency Stop (ESTOP) global pause state. Allows operators or supervisor agents to immediately suspend all autonomous background tasks,

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``emergency_stop_manage`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("emergency_stop_manage", dict(kwargs))


def emit_stigmergic_event(**kwargs) -> object:
    """Deposit or release a stigmergic pheromone signal on a code entity. Stigmergic coordination lets a swarm of agents coordinate without direct

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``emit_stigmergic_event`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("emit_stigmergic_event", dict(kwargs))


def enterprise_security_manage(**kwargs) -> object:
    """Manage enterprise security controls, enclave boundaries, spatio-temporal memory, and autonomous goal pursuit.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``enterprise_security_manage`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("enterprise_security_manage", dict(kwargs))


def evaluate_agent_competence(**kwargs) -> object:
    """Evaluate statistical competence using 95% Wilson confidence intervals and curiosity scoring.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``evaluate_agent_competence`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("evaluate_agent_competence", dict(kwargs))


def evaluate_epistemic_claim(**kwargs) -> object:
    """Register, evaluate, or update epistemic truth claims with Bayesian calibration and falsification tests. Action 'register': Adds a new working hypothesis/assumption with an explicit falsification test.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``evaluate_epistemic_claim`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("evaluate_epistemic_claim", dict(kwargs))


def execute_sandboxed_computer_action(**kwargs) -> object:
    """Evaluate a desktop/terminal command against the 3-tier blast-radius safety gate. The gate classifies and validates; it never runs anything. ``SAFE`` commands

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``execute_sandboxed_computer_action`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("execute_sandboxed_computer_action", dict(kwargs))


def execute_slash_command(**kwargs) -> object:
    """Execute a Master Slash Command from the catalog across 28 categories.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``execute_slash_command`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("execute_slash_command", dict(kwargs))


def execute_transactional_action(**kwargs) -> object:
    """Execute a file action within an ACID-like 4-stage transaction envelope with automatic rollback. Stages: PREPARE (shadow snapshot) -> VALIDATE -> COMMIT -> VERIFY.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``execute_transactional_action`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("execute_transactional_action", dict(kwargs))


def external_job(**kwargs) -> object:
    """Manage asynchronous background OS jobs decoupled from reasoning loops.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``external_job`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("external_job", dict(kwargs))


def forge_skill_from_trace(**kwargs) -> object:
    """Forge a tested, parameterized SKILL.md tool from a sequence of successful execution steps. Extracts parameters, dedups via content hash, and registers the skill for autonomous reuse.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``forge_skill_from_trace`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("forge_skill_from_trace", dict(kwargs))


def generate_curriculum_plan(**kwargs) -> object:
    """Analyze agent capability gaps and generate an autonomous training curriculum. Computes urgency priority = (Target - Current) / Difficulty and schedules targeted exercises.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``generate_curriculum_plan`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("generate_curriculum_plan", dict(kwargs))


def generate_repo_map(**kwargs) -> object:
    """Generate a token-efficient structural map of the repository with AST symbol signatures. Provides high-level codebase awareness (directory layout, key classes, functions, and methods)

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``generate_repo_map`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("generate_repo_map", dict(kwargs))


def goal_engine(**kwargs) -> object:
    """Manage continuous, goal-driven autonomous execution loops with self-healing.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``goal_engine`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("goal_engine", dict(kwargs))


def goal_integrity(**kwargs) -> object:
    """Audit plans and proposed subtasks for goal drift, scope creep, and overengineering.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``goal_integrity`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("goal_integrity", dict(kwargs))


def group_chat(**kwargs) -> object:
    """Collaborate in multi-agent group chat rooms with adaptive speaker modes and voting. Vastly expands on Hermes Bot Mode with 5 speaker selection strategies (mention,

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``group_chat`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("group_chat", dict(kwargs))


def harness_refine(**kwargs) -> object:
    """Manage self-improving Continual Harness state and trigger online refinement. Inspired by Prime Agent's /refine and durable harness state. Entries record

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``harness_refine`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("harness_refine", dict(kwargs))


def hashline_edit(**kwargs) -> object:
    """Edit a file using verified content-hash line references (e.g. start_ref='12#VK', end_ref='15#MB'). Eliminates whitespace and line drift errors.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``hashline_edit`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("hashline_edit", dict(kwargs))


def hashline_read(**kwargs) -> object:
    """Read a file with content-hashed lines (LINE#HASH| content). Used to get stable line references before editing.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``hashline_read`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("hashline_read", dict(kwargs))


def hyperplan_review_manage(**kwargs) -> object:
    """Run hostile multi-agent audit on an execution plan across 4 orthogonal dimensions (gaps, architecture, security, testability). Returns plan_hash for execution pinning; BLOCKED plans must not execute.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``hyperplan_review_manage`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("hyperplan_review_manage", dict(kwargs))


def identify_autonomous_command(**kwargs) -> object:
    """Identify which Master Slash Command to execute for the current task, error, or lifecycle stage.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``identify_autonomous_command`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("identify_autonomous_command", dict(kwargs))


def inspect_deep_agent_telemetry(**kwargs) -> object:
    """Inspect bounded telemetry for an isolated deep agent session.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``inspect_deep_agent_telemetry`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("inspect_deep_agent_telemetry", dict(kwargs))


def inspect_repo_twin(**kwargs) -> object:
    """Inspect repository digital twin, AST symbol dependencies, and blast radius. Discovers build systems (pip, npm, cargo), test frameworks (pytest, jest), CI/CD workflows,

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``inspect_repo_twin`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("inspect_repo_twin", dict(kwargs))


def kanban_board(**kwargs) -> object:
    """Manage tasks on the collaborative Kanban board with review gates and DAG unblocking. Tasks progress across columns: backlog -> todo -> in_progress -> in_review -> done.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``kanban_board`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("kanban_board", dict(kwargs))


def keyless_web_search(**kwargs) -> object:
    """Search the web without any API key by falling back across keyless engines until one answers. Ports AgentEye's keyless chain (DuckDuckGo HTML, DuckDuckGo via Jina Reader,

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``keyless_web_search`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("keyless_web_search", dict(kwargs))


def kibitzer_nudge_manage(**kwargs) -> object:
    """Manage resident memory hints (Kibitzer sidecar). Actions: 'add_memory', 'observe', 'reset'.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``kibitzer_nudge_manage`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("kibitzer_nudge_manage", dict(kwargs))


def learning_graph_manage(**kwargs) -> object:
    """Manage and query the self-evolving Knowledge & Learning Graph. Maintains topological connections across learned user preferences, codebase invariants,

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``learning_graph_manage`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("learning_graph_manage", dict(kwargs))


def list_available_deep_agents(**kwargs) -> object:
    """List available deep specialist agents and their capabilities. Returns compact descriptors for every autonomous deep specialist without

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``list_available_deep_agents`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("list_available_deep_agents", dict(kwargs))


def list_dynamic_tools(**kwargs) -> object:
    """Inspect the registry of runtime-synthesized agent tools.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``list_dynamic_tools`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("list_dynamic_tools", dict(kwargs))


def manage_code_checkpoint(**kwargs) -> object:
    """Create a lightweight rollback checkpoint or restore the repository to a previous clean state. Allows safe refactoring with instant rollback guarantees before risky code mutations.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``manage_code_checkpoint`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("manage_code_checkpoint", dict(kwargs))


def manage_context_data(**kwargs) -> object:
    """Manage and transform massive context variables programmatically without prompt bloat. Implements the Prime Agent Context-as-Data pattern: contexts are stored as immutable

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``manage_context_data`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("manage_context_data", dict(kwargs))


def manage_durable_orchestration(**kwargs) -> object:
    """Manage durable event journals, task checkpointing, and crash-resilient replay.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``manage_durable_orchestration`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("manage_durable_orchestration", dict(kwargs))


def manage_mission_hierarchy(**kwargs) -> object:
    """Manage the 6-level Goal -> Mission -> Task -> Subtask -> Action -> ToolCall execution hierarchy.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``manage_mission_hierarchy`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("manage_mission_hierarchy", dict(kwargs))


def manage_model_performance_registry(**kwargs) -> object:
    """Manage empirical model benchmarks and dynamically route tasks to Pareto-optimal models.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``manage_model_performance_registry`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("manage_model_performance_registry", dict(kwargs))


def manage_reflexion_memory(**kwargs) -> object:
    """Manage Reflexion failure memory.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``manage_reflexion_memory`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("manage_reflexion_memory", dict(kwargs))


def moa_multi_model_reasoning(**kwargs) -> object:
    """Execute a Mixture-of-Agents (MoA) parallel multi-LLM reasoning round. Dispatches complex questions to multiple candidate models simultaneously, redacts

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``moa_multi_model_reasoning`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("moa_multi_model_reasoning", dict(kwargs))


def present_files(**kwargs) -> object:
    """Make files visible to the user for viewing and rendering in the client interface. When to use the present_files tool: - Making any file available for the user to view, download, or interact with

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``present_files`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("present_files", dict(kwargs))


def process_handle(**kwargs) -> object:
    """Manage asynchronous background processes, check handles, and tail output. Inspired by Prime Agent's rlm/bash.py background execution model. Allows long-running

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``process_handle`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("process_handle", dict(kwargs))


def propose_skill(**kwargs) -> object:
    """Propose a new skill for human review and installation. Use this when repeated work deserves a reusable skill, or the user asks

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``propose_skill`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("propose_skill", dict(kwargs))


def query_contrastive_memory(**kwargs) -> object:
    """Retrieve negative constraints and failed reasoning paths from episodic memory. Searches past task executions to find similar failure signatures, erroneous hypotheses,

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``query_contrastive_memory`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("query_contrastive_memory", dict(kwargs))


def query_knowledge_graph(**kwargs) -> object:
    """Manage and query the cross-enterprise Knowledge Graph for multi-hop dependencies and blast radius.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``query_knowledge_graph`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("query_knowledge_graph", dict(kwargs))


def query_language_server_symbol(**kwargs) -> object:
    """Query compiler-grade symbol intelligence for a workspace via LSP, LSIF or SCIP. Use this tool to jump to definitions, enumerate references, read document

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``query_language_server_symbol`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("query_language_server_symbol", dict(kwargs))


def query_stigmergic_traces(**kwargs) -> object:
    """Read the shared stigmergic pheromone field of the agent swarm.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``query_stigmergic_traces`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("query_stigmergic_traces", dict(kwargs))


def ralph_loop(**kwargs) -> object:
    """Run a task through bounded self-improvement rounds until a promise holds. Use when an outcome is objectively checkable and a first attempt often

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``ralph_loop`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("ralph_loop", dict(kwargs))


def recall_agent_memory(**kwargs) -> object:
    """Recall memories across working, episodic and semantic tiers with hybrid ranking. Retrieval blends Okapi BM25 lexical relevance with cosine vector similarity

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``recall_agent_memory`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("recall_agent_memory", dict(kwargs))


def reconcile_structural_ast_conflicts(**kwargs) -> object:
    """Reconcile multi-agent concurrent modifications via semantic 3-way AST node merging. Replaces line-based git conflict markers with structural AST reconciliation. Merges

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``reconcile_structural_ast_conflicts`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("reconcile_structural_ast_conflicts", dict(kwargs))


def record_trajectory_outcome(**kwargs) -> object:
    """Record an episodic trajectory outcome into contrastive memory. Stores tuples of (Failure Signature, Erroneous Hypothesis, Failed Patch, Winning Resolution)

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``record_trajectory_outcome`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("record_trajectory_outcome", dict(kwargs))


def reproduce_and_verify(**kwargs) -> object:
    """Execute test-driven autonomous bug reproduction and verification. Phase 'prepare': Synthesizes reproduction script and verifies that it FAILS before code modification.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``reproduce_and_verify`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("reproduce_and_verify", dict(kwargs))


def reversible_delete(**kwargs) -> object:
    """Plan, request approval for, execute, or restore reversible file quarantine. This free, local safety boundary never hard-deletes data. ``plan`` creates a

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``reversible_delete`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("reversible_delete", dict(kwargs))


def review_plan_invariant_gate(**kwargs) -> object:
    """Rigorous plan invariant review (powered by Enterprise Invariant Gatekeeper profile) to prevent rubber-stamping and defects.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``review_plan_invariant_gate`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("review_plan_invariant_gate", dict(kwargs))


def review_skill_package(**kwargs) -> object:
    """Inspect a skill package without activating, installing, executing, or editing it. Use this tool only for skill review workflows. The target package is

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``review_skill_package`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("review_skill_package", dict(kwargs))


def run_autonomous_benchmark_eval(**kwargs) -> object:
    """Evaluate agent code patches against coding benchmarks and compute pass@k. Each problem is executed in an isolated git workspace: the repository is

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``run_autonomous_benchmark_eval`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("run_autonomous_benchmark_eval", dict(kwargs))


def run_avo_variation(**kwargs) -> object:
    """Run an Autonomous Value Optimization (AVO) variation step. Applies the strict matches-or-improves commit policy against the lineage tree.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``run_avo_variation`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("run_avo_variation", dict(kwargs))


def run_differential_regression_oracle(**kwargs) -> object:
    """Execute differential invariant validation and regression testing between code revisions. Synthesizes property-based fuzz tests and executes dual shadow sandboxes with randomized

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``run_differential_regression_oracle`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("run_differential_regression_oracle", dict(kwargs))


def run_introspective_tree_search(**kwargs) -> object:
    """Execute tree-guided multi-path search (I-MCTS / CodeTree) to resolve complex bugs. Coordinates Monte Carlo Tree Search with introspective node expansion, pruning invalid

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``run_introspective_tree_search`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("run_introspective_tree_search", dict(kwargs))


def run_rsi_cycle(**kwargs) -> object:
    """Execute an autonomous Recursive Self-Improvement (RSI) cycle: Bottleneck -> Hypothesis -> Candidate -> A/B Test -> Holdout -> Promote. Analyzes agent performance bottlenecks, generates an optimization

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``run_rsi_cycle`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("run_rsi_cycle", dict(kwargs))


def run_task_evaluation_benchmark(**kwargs) -> object:
    """Run standard agentic benchmarks (research, coding, browser, computer, multi-agent) and report scores.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``run_task_evaluation_benchmark`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("run_task_evaluation_benchmark", dict(kwargs))


def run_variation_operator_step(**kwargs) -> object:
    """Execute an Autonomous Agentic Variation Operator (AVO) step. Supports multi-dimensional vector evaluation, Pareto dominance tracking,

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``run_variation_operator_step`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("run_variation_operator_step", dict(kwargs))


def schedule_work_queue(**kwargs) -> object:
    """Manage the durable DAG task scheduler, execution queue, and budget guardrails.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``schedule_work_queue`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("schedule_work_queue", dict(kwargs))


def search_project_docs(**kwargs) -> object:
    """Search, read, or inspect the current project's documentation offline. Use this for questions about Alpha configuration, commands, architecture,

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``search_project_docs`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("search_project_docs", dict(kwargs))


def search_session_memory(**kwargs) -> object:
    """Search long-term conversation history using SQLite FTS5 full-text indexing with BM25 ranking. Applies cron session demotion to prevent vocabulary starvation ('recall blindness') and

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``search_session_memory`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("search_session_memory", dict(kwargs))


def self_heal_diagnose(**kwargs) -> object:
    """Diagnose runtime health anomalies, stale lock files, or hanging operations and execute self-healing actions. Scans workspace for locks (such as .git/index.lock or .pytest_cache/.lock) older than thres

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``self_heal_diagnose`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("self_heal_diagnose", dict(kwargs))


def session_search(**kwargs) -> object:
    """Search past conversations or read one thread's history. Discovery: session_search(query="router password") searches every thread

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``session_search`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("session_search", dict(kwargs))


def simulate_consequences(**kwargs) -> object:
    """Simulate potential environmental side-effects, blast radius, and cascading failures before execution. Probes the host environment affordances, checks if the workspace is git-tracked, analyzes package

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``simulate_consequences`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("simulate_consequences", dict(kwargs))


def skills_hub_manage(**kwargs) -> object:
    """Manage Skills Hub discovery, AST security audits, and skill installation.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``skills_hub_manage`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("skills_hub_manage", dict(kwargs))


def supervisor_watchdog(**kwargs) -> object:
    """Monitor agent health, report heartbeats, inspect anomalies, and trigger recovery.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``supervisor_watchdog`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("supervisor_watchdog", dict(kwargs))


def swarm(**kwargs) -> object:
    """Evaluate, spawn, run, communicate with, and monitor bounded agent swarms.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``swarm`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("swarm", dict(kwargs))


def synthesize_reusable_skill(**kwargs) -> object:
    """Synthesize a structured and validated SKILL.md package from execution steps.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``synthesize_reusable_skill`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("synthesize_reusable_skill", dict(kwargs))


def synthesize_runtime_tool(**kwargs) -> object:
    """Synthesize, security-verify, self-test and register a new agent tool at runtime. The source is first screened by a static AST security validator that blocks

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``synthesize_runtime_tool`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("synthesize_runtime_tool", dict(kwargs))


def tom_consult(**kwargs) -> object:
    """Consult the Theory of Mind cognitive model to infer unstated user expectations, invariants, and pitfalls. Before executing high-impact, destructive, or ambiguous modifications, this tool models the hu

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``tom_consult`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("tom_consult", dict(kwargs))


def trace_artifact_lineage(**kwargs) -> object:
    """Manage artifact provenance, track causal derivation, and audit the ancestry graph of any output.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``trace_artifact_lineage`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("trace_artifact_lineage", dict(kwargs))


def trajectory_audit(**kwargs) -> object:
    """Inspect and export step-by-step reasoning and tool execution trajectories.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``trajectory_audit`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("trajectory_audit", dict(kwargs))


def update_progress_card(**kwargs) -> object:
    """Publish or update an in-place streaming progress draft card. Instead of polluting the chat with repetitive conversational status messages,

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``update_progress_card`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("update_progress_card", dict(kwargs))


def verify_command_approval(**kwargs) -> object:
    """Evaluate whether a shell command is safe to execute using Smart Approvals guardian. Strips shell comments to prevent injection bypasses, checks against dangerous destruction

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``verify_command_approval`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("verify_command_approval", dict(kwargs))


def verify_web_ui_visual_regression(**kwargs) -> object:
    """Verify web UI visual integrity and accessibility tree parity between HTML snapshots. Extracts semantic interactive elements and detects visual regression, missing

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``verify_web_ui_visual_regression`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("verify_web_ui_visual_regression", dict(kwargs))


def visual_verify_artifact(**kwargs) -> object:
    """Verify visual, structural, and layout integrity of an HTML/SVG/Canvas artifact.

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``visual_verify_artifact`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("visual_verify_artifact", dict(kwargs))


def workflow_dag_manage(**kwargs) -> object:
    """Manage dependency-ordered and dynamic task graphs (DAG / DWE).

    Generated shim.  ``kwargs`` are forwarded verbatim to the ``workflow_dag_manage`` tool
    through the script bridge; authorisation is evaluated parent-side.
    """
    return get_client().call("workflow_dag_manage", dict(kwargs))

