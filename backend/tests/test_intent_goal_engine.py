"""Module A: universal prompt perception, slash-command resolver, goal decomposer.

Covers:
* domain / complexity / thinking classification incl. ambiguous prompts;
* contextual slash-command resolver: triggers, honest non-triggers, tri-state
  catalog registration flag;
* deterministic task state machine incl. COMPENSATING on injected failure;
* durable persistence round-trip under ``AGENT_WORKSPACE_HOME`` (temp dir).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from alpha.capabilities.catalog import CAPABILITY_CATALOG
from alpha.orchestration import intent as intent_module
from alpha.orchestration.goals import (
    ALLOWED_TRANSITIONS,
    CorruptGoalStateError,
    DecompositionError,
    EvidenceRequiredError,
    GoalDecomposer,
    InvalidTaskTransitionError,
    TaskState,
    UnmetDependencyError,
)
from alpha.orchestration.intent import (
    CommandResolution,
    ComplexityMode,
    IntentGoalEngine,
    PromptDomain,
    ThinkingTier,
    classify_complexity,
    classify_domain,
    parse_intent,
    resolve_slash_command,
)

# ---------------------------------------------------------------------------
# Domain classification
# ---------------------------------------------------------------------------

_DOMAIN_CASES = [
    ("Implement a REST endpoint for uploading files with unit tests.", PromptDomain.SDLC),
    ("Research the current state of vector databases and cite peer-reviewed sources.", PromptDomain.RESEARCH),
    ("Audit this FastAPI handler for SQL injection and XSS vulnerabilities.", PromptDomain.SECURITY),
    ("Deploy the stack to Kubernetes and monitor the nginx ingress health.", PromptDomain.OPS),
    ("Design a PostgreSQL schema migration that adds an index on the users table.", PromptDomain.DATABASE),
    ("Use Playwright to click the login button and take a screenshot of the result.", PromptDomain.GUI_AUTOMATION),
    ("The build crashed with a traceback - roll back and repair the error loop.", PromptDomain.SELF_REPAIR),
]


@pytest.mark.parametrize(("prompt", "expected"), _DOMAIN_CASES)
def test_domain_classification(prompt: str, expected: PromptDomain) -> None:
    result = classify_domain(prompt)
    assert result.domain is expected
    assert result.confidence > 0.0
    assert result.reason


def test_domain_ambiguous_tie_falls_back_to_general() -> None:
    # "secure" (security) and "database" score equally: no forced default.
    result = classify_domain("Secure the database before launch.")
    assert result.domain is PromptDomain.GENERAL
    assert "ambiguous" in result.reason
    assert 0.0 < result.confidence < 1.0
    assert set(result.secondary) >= {PromptDomain.SECURITY, PromptDomain.DATABASE}


def test_domain_no_signal_is_honest_general() -> None:
    result = classify_domain("Hello, how are you today?")
    assert result.domain is PromptDomain.GENERAL
    assert result.confidence == 0.0
    assert "no domain signals" in result.reason


# ---------------------------------------------------------------------------
# Complexity mode
# ---------------------------------------------------------------------------

_COMPLEXITY_CASES = [
    ("What time is it in Tokyo?", ComplexityMode.DIRECT_RESPONSE),
    ("Plan the migration step by step: first audit the schema, then rewrite the queries, then verify with tests.", ComplexityMode.STRUCTURED_PLAN),
    ("Assemble a multi-agent swarm to build the entire product end-to-end and keep going until it is done.", ComplexityMode.AUTONOMOUS_SWARM),
    ("Run a health check every 15 minutes and alert on failure.", ComplexityMode.RECURRING_AUTOMATION),
]


@pytest.mark.parametrize(("prompt", "expected"), _COMPLEXITY_CASES)
def test_complexity_classification(prompt: str, expected: ComplexityMode) -> None:
    result = classify_complexity(prompt)
    assert result.mode is expected
    assert result.reason


# ---------------------------------------------------------------------------
# Thinking mode
# ---------------------------------------------------------------------------


def test_thinking_mode_fast_for_direct_prompt() -> None:
    analysis = parse_intent("What time is it in Tokyo?")
    assert analysis.thinking.tier is ThinkingTier.FAST
    assert analysis.thinking.model_tier_hint == "fast/lightweight"
    assert analysis.thinking.token_budget == 1024
    assert analysis.thinking.reason


def test_thinking_mode_deep_for_swarm() -> None:
    analysis = parse_intent("Assemble a multi-agent swarm to build the entire product end-to-end.")
    assert analysis.thinking.tier is ThinkingTier.DEEP
    assert analysis.thinking.model_tier_hint == "deep reasoning"
    assert analysis.thinking.token_budget == 8192


def test_thinking_mode_deep_for_security_domain() -> None:
    analysis = parse_intent("Audit this FastAPI handler for SQL injection and XSS vulnerabilities.")
    assert analysis.thinking.tier is ThinkingTier.DEEP
    assert "security domain" in analysis.thinking.reason


def test_parse_intent_exposes_all_dimensions() -> None:
    analysis = parse_intent("Research the current state of vector databases and cite peer-reviewed sources.")
    payload = analysis.to_dict()
    assert set(payload) == {"prompt", "domain", "complexity", "thinking", "slash_command"}
    assert payload["domain"]["domain"] == PromptDomain.RESEARCH.value
    assert payload["complexity"]["mode"] in {mode.value for mode in ComplexityMode}
    # Parsing is a pure function: identical prompt, identical result.
    assert analysis == parse_intent("Research the current state of vector databases and cite peer-reviewed sources.")


# ---------------------------------------------------------------------------
# Contextual slash-command resolver
# ---------------------------------------------------------------------------

_TRIGGER_CASES = [
    # (prompt, implied command, registered in alpha.commands.catalog?)
# P5 landed real handlers for the five Module-A spec commands, so their honest
# `registered` flag flipped False -> True (the resolver computes it from the
# catalog; the disclosure lives in docs/TASK_LIST.md). Resolver behaviour is
# otherwise unchanged: same command, same confidence window, same reason shape.
    ("Do a deep, multi-angle analysis of this failure mode before proposing fixes.", "/boost", True),
    ("Keep working on this overnight until the whole migration is done.", "/goal create", True),
    ("Back up the database every day at 3am.", "/schedule", True),
    ("There are real trade-offs in this risky migration; ask me clarifying questions before writing code.", "/grill-me", True),
    ("Draft a bot roster and a multi-agent coordination plan for this enterprise project.", "/teamwork-preview", True),
    ("Remember that for next time: the fix was pinning the dependency version.", "/learn", True),
    ("The test suite is failing with a traceback after the last change.", "/self-heal", True),
]


@pytest.mark.parametrize(("prompt", "command", "registered"), _TRIGGER_CASES)
def test_resolver_triggers_without_typed_slash(prompt: str, command: str, registered: bool) -> None:
    result = resolve_slash_command(prompt)
    assert isinstance(result, CommandResolution)
    assert result.command == command
    assert result.registered is registered  # honest tri-state flag
    assert 0.65 <= result.confidence <= 1.0
    assert result.reason
    assert "alpha.commands.catalog" in result.reason  # registration status always disclosed
    assert result.args == prompt.strip()
    assert result.to_dict()["command"] == command


_NON_TRIGGER_CASES = [
    "Hello, how are you today?",
    "Write a function that reverses a list in Python.",
    "Explain the CAP trade-offs.",  # single weak signal must not force /grill-me (min_signals=2)
    "What is the capital of France?",
]


@pytest.mark.parametrize("prompt", _NON_TRIGGER_CASES)
def test_resolver_honest_non_triggers(prompt: str) -> None:
    result = resolve_slash_command(prompt)
    assert result.command is None
    assert result.registered is None
    assert result.confidence == 0.0
    assert result.resolved is False
    assert result.reason


def test_resolver_empty_prompt_no_resolve() -> None:
    result = resolve_slash_command("   ")
    assert result.command is None
    assert "empty prompt" in result.reason


def test_resolver_explicit_slash_is_executor_passthrough() -> None:
    result = resolve_slash_command("/boost now")
    assert result.command is None
    assert "explicit slash command" in result.reason


def test_resolver_unavailable_catalog_is_unverified(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(intent_module, "_load_catalog_commands", lambda: None)
    result = resolve_slash_command("Do a deep dive analysis of this failure mode.")
    assert result.command == "/boost"
    assert result.registered is None
    assert "unverified" in result.reason


# ---------------------------------------------------------------------------
# Capability catalog registration (orphan-module invariant)
# ---------------------------------------------------------------------------


def test_intent_goal_engine_capability_registered() -> None:
    spec = CAPABILITY_CATALOG["intent_goal_engine"]
    assert spec.module == "alpha.orchestration.intent"
    assert spec.target == "IntentGoalEngine"
    engine = IntentGoalEngine()
    analysis = engine.analyze("hello")
    assert analysis.domain.domain is PromptDomain.GENERAL
    assert engine.resolve_command("hello").command is None
    assert isinstance(spec.dotted_target, str)


# ---------------------------------------------------------------------------
# Goal decomposer: hierarchy + deterministic state machine
# ---------------------------------------------------------------------------


def _decomposer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, project_id: str = "module-a-test") -> GoalDecomposer:
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    return GoalDecomposer(project_id)


def test_hierarchy_creation_milestones_tasks_subtasks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    engine = _decomposer(monkeypatch, tmp_path)
    goal = engine.create_goal("Ship the migration", success_criteria=("tests green",), milestones=("Audit", "Execute"))
    assert goal.id in engine.goals
    assert len(goal.milestone_ids) == 2
    milestone = engine.milestones[goal.milestone_ids[0]]
    task = engine.add_task(milestone.id, "audit schema", assignee="db_bot")
    subtask = engine.add_subtask(task.id, "check indexes")
    assert subtask.parent_id == task.id
    assert subtask.id in milestone.task_ids
    # Hierarchy depth is Milestone -> Task -> Subtask: no third level.
    with pytest.raises(DecompositionError):
        engine.add_subtask(subtask.id, "too deep")


def test_state_machine_edges_and_dependency_enforcement(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    engine = _decomposer(monkeypatch, tmp_path)
    goal = engine.create_goal("g", milestones=("M",))
    milestone = engine.milestones[goal.milestone_ids[0]]
    task_a = engine.add_task(milestone.id, "A")
    task_b = engine.add_task(milestone.id, "B", depends_on=[task_a.id])

    # Dependency-free task promotes to READY deterministically; dependent waits.
    assert task_a.state is TaskState.READY
    assert task_b.state is TaskState.PENDING

    # Enforcement: READY is blocked while dependencies are unmet.
    with pytest.raises(UnmetDependencyError):
        engine.transition(task_b.id, TaskState.READY)
    with pytest.raises(InvalidTaskTransitionError):
        engine.transition(task_b.id, TaskState.IN_PROGRESS)

    # Dependent promotes after its dependency completes.
    engine.start(task_a.id)
    assert engine.tasks[task_a.id].state is TaskState.IN_PROGRESS
    engine.complete(task_a.id, evidence="pytest run #42: 118 passed")
    assert engine.tasks[task_a.id].state is TaskState.COMPLETED
    assert engine.tasks[task_a.id].evidence == ["pytest run #42: 118 passed"]
    assert engine.tasks[task_b.id].state is TaskState.READY
    assert engine.refresh_ready() == []  # nothing left to promote

    # COMPLETED requires non-empty evidence (spec 3.1).
    engine.start(task_b.id)
    with pytest.raises(EvidenceRequiredError):
        engine.complete(task_b.id, evidence="  ")
    assert engine.tasks[task_b.id].state is TaskState.IN_PROGRESS
    engine.complete(task_b.id, evidence=["lint clean", "tests pass"])
    assert engine.tasks[task_b.id].state is TaskState.COMPLETED


def test_injected_failure_enters_compensating(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    engine = _decomposer(monkeypatch, tmp_path)
    goal = engine.create_goal("g", milestones=("M",))
    milestone = engine.milestones[goal.milestone_ids[0]]
    task = engine.add_task(milestone.id, "risky step")
    engine.start(task.id)
    engine.fail(task.id, "injected failure: migration aborted")
    failed = engine.tasks[task.id]
    assert failed.state is TaskState.COMPENSATING
    assert failed.failure_reason == "injected failure: migration aborted"
    # COMPENSATING is terminal: no resurrection without a new task.
    with pytest.raises(InvalidTaskTransitionError):
        engine.transition(task.id, TaskState.READY)
    with pytest.raises(InvalidTaskTransitionError):
        engine.transition(task.id, TaskState.IN_PROGRESS)
    # A compensation without a reason is rejected up front.
    task2 = engine.add_task(milestone.id, "another step")
    engine.start(task2.id)
    with pytest.raises(DecompositionError):
        engine.transition(task2.id, TaskState.COMPENSATING, reason="  ")


def _states_fixture(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[TaskState, str]:
    """One engine with a task parked in every state (PENDING via unmet dep)."""
    engine = _decomposer(monkeypatch, tmp_path, project_id="state-matrix")
    goal = engine.create_goal("g", milestones=("M",))
    milestone = engine.milestones[goal.milestone_ids[0]]
    running = engine.add_task(milestone.id, "in-progress")
    engine.start(running.id)
    pending = engine.add_task(milestone.id, "pending", depends_on=[running.id])
    ready = engine.add_task(milestone.id, "ready")
    completed = engine.add_task(milestone.id, "completed")
    engine.start(completed.id)
    engine.complete(completed.id, evidence="verified")
    compensating = engine.add_task(milestone.id, "compensating")
    engine.start(compensating.id)
    engine.fail(compensating.id, "boom")
    assert engine.tasks[pending.id].state is TaskState.PENDING
    return {
        TaskState.PENDING: pending.id,
        TaskState.READY: ready.id,
        TaskState.IN_PROGRESS: running.id,
        TaskState.COMPLETED: completed.id,
        TaskState.COMPENSATING: compensating.id,
    }


_ILLEGAL_TRANSITIONS = [
    (TaskState.PENDING, TaskState.COMPLETED),
    (TaskState.PENDING, TaskState.IN_PROGRESS),
    (TaskState.PENDING, TaskState.COMPENSATING),
    (TaskState.READY, TaskState.COMPLETED),
    (TaskState.READY, TaskState.READY),
    (TaskState.IN_PROGRESS, TaskState.READY),
    (TaskState.COMPLETED, TaskState.IN_PROGRESS),
    (TaskState.COMPLETED, TaskState.READY),
    (TaskState.COMPENSATING, TaskState.PENDING),
    (TaskState.COMPENSATING, TaskState.IN_PROGRESS),
]


@pytest.mark.parametrize(("from_state", "to_state"), _ILLEGAL_TRANSITIONS)
def test_illegal_transitions_rejected(from_state: TaskState, to_state: TaskState, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    states = _states_fixture(monkeypatch, tmp_path)
    # State persists durably, so a fresh instance sees the same parked tasks.
    rebuilt = GoalDecomposer("state-matrix")
    with pytest.raises(InvalidTaskTransitionError):
        rebuilt.transition(states[from_state], to_state)


def test_transition_table_shape_is_the_spec_machine() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(TaskState)
    assert ALLOWED_TRANSITIONS[TaskState.PENDING] == frozenset({TaskState.READY})
    assert ALLOWED_TRANSITIONS[TaskState.READY] == frozenset({TaskState.IN_PROGRESS})
    assert ALLOWED_TRANSITIONS[TaskState.IN_PROGRESS] == frozenset({TaskState.COMPLETED, TaskState.COMPENSATING})
    assert ALLOWED_TRANSITIONS[TaskState.COMPLETED] == frozenset()
    assert ALLOWED_TRANSITIONS[TaskState.COMPENSATING] == frozenset()


def test_unknown_state_and_ids_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    engine = _decomposer(monkeypatch, tmp_path, project_id="rejects")
    goal = engine.create_goal("g", milestones=("M",))
    milestone = engine.milestones[goal.milestone_ids[0]]
    task = engine.add_task(milestone.id, "t")
    with pytest.raises(DecompositionError):
        engine.transition("nope", TaskState.READY)  # unknown task: lookup fails first
    with pytest.raises(InvalidTaskTransitionError):
        engine.transition(task.id, "bogus-state")  # known task, unknown state name
    with pytest.raises(DecompositionError):
        engine.start("nope")
    with pytest.raises(DecompositionError):
        engine.add_task("missing-milestone", "t")
    with pytest.raises(DecompositionError):
        engine.add_task(milestone.id, "t2", depends_on=["missing-task"])


def test_goal_progress_is_deterministically_derived(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    engine = _decomposer(monkeypatch, tmp_path, project_id="progress")
    goal = engine.create_goal("g", milestones=("M",))
    milestone = engine.milestones[goal.milestone_ids[0]]
    task_a = engine.add_task(milestone.id, "A")
    task_b = engine.add_task(milestone.id, "B")
    # No tasks completed yet: honestly 0%, active.
    assert engine.goal_progress(goal.id)["completion_percent"] == 0.0
    engine.start(task_a.id)
    engine.complete(task_a.id, evidence="ev")
    progress = engine.goal_progress(goal.id)
    assert progress["completion_percent"] == 50.0
    assert progress["status"] == "active"
    engine.start(task_b.id)
    engine.complete(task_b.id, evidence="ev")
    progress = engine.goal_progress(goal.id)
    assert progress["completion_percent"] == 100.0
    assert progress["status"] == "complete"
    assert engine.goals[goal.id].status == "complete"


# ---------------------------------------------------------------------------
# Durable persistence round-trip (invariant 3 + 4)
# ---------------------------------------------------------------------------


def test_persistence_round_trip_in_temp_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    engine = _decomposer(monkeypatch, tmp_path, project_id="roundtrip")
    goal = engine.create_goal("Ship it", success_criteria=("green tests",), milestones=("Build",))
    milestone = engine.milestones[goal.milestone_ids[0]]
    task = engine.add_task(milestone.id, "build", assignee="ci_bot")
    engine.record_decision("storage engine", "use postgres", rationale="team convention", goal_id=goal.id)
    engine.start(task.id)
    engine.complete(task.id, evidence="run #7 passed")

    root = tmp_path / "projects" / "roundtrip"
    assert (root / "goals.json").is_file()
    assert (root / "tasks.json").is_file()
    assert (root / "decisions.json").is_file()
    for name in ("goals.json", "tasks.json", "decisions.json"):
        payload = json.loads((root / name).read_text(encoding="utf-8"))
        assert payload["schema_version"] == 1
        assert payload["project_id"] == "roundtrip"

    # Fresh instance over the same home sees identical durable state.
    reloaded = GoalDecomposer("roundtrip")
    assert set(reloaded.goals) == set(engine.goals)
    reloaded_task = reloaded.tasks[task.id]
    assert reloaded_task.state is TaskState.COMPLETED
    assert reloaded_task.evidence == ["run #7 passed"]
    assert reloaded_task.assignee == "ci_bot"
    assert len(reloaded.decisions) == 1
    assert reloaded.decisions[0].decision == "use postgres"
    assert reloaded.goals[goal.id].completion_percent == 100.0
    assert reloaded.goals[goal.id].status == "complete"


def test_corrupt_state_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    engine = _decomposer(monkeypatch, tmp_path, project_id="corrupt")
    engine.create_goal("g", milestones=("M",))
    (tmp_path / "projects" / "corrupt" / "goals.json").write_text("{not valid json", encoding="utf-8")
    with pytest.raises(CorruptGoalStateError):
        GoalDecomposer("corrupt")


def test_invalid_project_id_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    for bad in ("../evil", "has/slash", "has\\backslash", "", ".hidden.."):
        with pytest.raises(DecompositionError):
            GoalDecomposer(bad)


def test_intent_engine_decomposer_factory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    decomposer = IntentGoalEngine().decomposer_for("factory")
    goal = decomposer.create_goal("g")
    assert goal.id in decomposer.goals
