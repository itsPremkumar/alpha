"""Tests for the autonomous one-prompt planner (planning/autonomous.py)."""

import json

from langgraph.prebuilt import ToolRuntime

from alpha.kanban.store import KanbanStore
from alpha.planning.autonomous import AutonomousPlan, AutonomousPlanner, detect_domains, review_wave
from alpha.planning.profiles import (
    install_profiles,
    profile_spec_to_managed_definition,
    profile_spec_to_subagent_config,
)
from alpha.tools.builtins.autoplan_tool import build_autonomous_plan


def test_detect_domains_covers_main_tracks():
    assert "coding" in detect_domains("Fix the null pointer bug in auth parsing")
    assert "research" in detect_domains("Research vector database options and pricing")
    assert "deploy" in detect_domains("Deploy the release to staging")
    assert detect_domains("What is the capital of France?") == []


def test_trivial_prompt_stays_single_track_direct():
    plan = AutonomousPlanner().plan("Fix typo in README")
    assert plan.needs_subagents is False
    assert len(plan.subtasks) == 1
    assert plan.subtasks[0].category == "quick"
    assert plan.new_profiles == []
    assert len(plan.plan_hash) == 16
    assert "nothing else needed" in plan.user_summary_markdown.lower()
    assert plan.kanban_board["tasks"]


def test_huge_prompt_gets_full_breakdown():
    prompt = "Research vector database options and pricing, then design and build a complete platform with a landing page frontend and a Python backend API, migrate the existing dataset, and deploy the full system to staging with docs"
    plan = AutonomousPlanner().plan(prompt)
    assert plan.needs_deep_research is True
    assert plan.needs_subagents is True
    assert len(plan.subtasks) >= 4
    assert any(s.needs_new_profile for s in plan.subtasks)
    assert len(plan.new_profiles) >= 1
    assert "deep-research" in plan.skills_to_use
    assert "frontend-design" in plan.skills_to_use
    assert len(plan.execution_waves) >= 2
    # Kanban mirrors subtasks with assignees and dependencies.
    assert len(plan.kanban_board["tasks"]) == len(plan.subtasks)
    assert all(t["assignee"] for t in plan.kanban_board["tasks"])
    assert plan.goal_statement
    assert plan.acceptance_criteria


def test_dangerous_prompt_is_gated_not_silent():
    plan = AutonomousPlanner().plan("Wipe everything with sudo rm -rf /var/data, no tests needed")
    assert plan.risk_tier == "r5"
    assert plan.autonomy == "gated"
    assert plan.hyperplan_status == "BLOCKED"
    assert "approval" in plan.user_summary_markdown.lower()


def test_empty_prompt_rejected():
    try:
        AutonomousPlanner().plan("   ")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for empty prompt")


def test_trivial_docs_prompt_keeps_true_domain():
    plan = AutonomousPlanner().plan("Write a quickstart guide for contributors")
    assert len(plan.subtasks) == 1
    assert plan.subtasks[0].domain == "docs"


def test_capability_fallbacks_rewrite_unavailable_resources():
    plan = AutonomousPlanner().plan(
        "Implement a user login endpoint with automated tests",
        capabilities={"enabled_skills": [], "bash": False, "browser": False},
    )
    assert plan.skills_to_use == []
    assert "bash" not in plan.tool_groups
    assert all(s.assignee != "bash" for s in plan.subtasks)
    assert any("unavailable" in n or "fallback" in n for n in plan.capability_notes)


def test_capability_partial_unknown_records_not_blocks():
    plan = AutonomousPlanner().plan(
        "Research vector database options",
        capabilities={"enabled_skills": None, "bash": None, "browser": None},
    )
    assert "deep-research" in plan.skills_to_use
    assert any("unknown" in n for n in plan.capability_notes)


def test_kanban_board_persists_when_store_given(tmp_path):
    store = KanbanStore(storage_path=tmp_path / "boards.json")
    plan = AutonomousPlanner().plan("Add a health-check endpoint with tests", kanban_store=store)
    assert plan.kanban_persisted is True
    board_id = plan.kanban_board["board_id"]
    reloaded = store.get_or_create_board(board_id)
    assert len(reloaded.tasks) == len(plan.subtasks)


def test_cost_estimate_flags_over_budget():
    plan = AutonomousPlanner().plan("Add a health-check endpoint with tests", token_budget=100)
    assert plan.cost_estimate["total_tokens"] > 100
    assert plan.cost_estimate["budget_status"] == "over"
    assert "exceed budget" in plan.cost_estimate["budget_note"]
    assert plan.execution_config["budget_status"] == "over"


def test_vague_prompt_lists_unknowns_for_one_shot_correction():
    plan = AutonomousPlanner().plan("Make it better and deploy it soon")
    assert plan.unknowns
    assert any("metric" in u or "environment" in u for u in plan.unknowns)
    assert "Assumptions & unknowns" in plan.user_summary_markdown


def test_review_wave_reports_next_cards_and_replan():
    plan = AutonomousPlanner().plan("Add a health-check endpoint with tests")
    first_wave = plan.execution_waves[0]["task_ids"]
    results = {tid: True for tid in first_wave}
    outcome = review_wave(plan, results, 0, 0, len(plan.subtasks))
    assert outcome["validity"]["decision"] in ("nominal", "local_replan", "global_replan")
    assert outcome["halt"] is False
    bad = review_wave(plan, {tid: False for tid in first_wave}, 0, 0, len(plan.subtasks), consecutive_failures=2)
    assert bad["failed_cards"] == first_wave
    assert bad["halt"] is True


def test_profile_specs_convert_to_runnable_definitions():
    plan = AutonomousPlanner().plan(
        "Research vector database options and pricing, then design and build a complete platform with a landing page frontend and a Python backend API, migrate the existing dataset, and deploy the full system to staging with docs"
    )
    assert plan.new_profiles
    spec = plan.new_profiles[0]
    definition = profile_spec_to_managed_definition(spec)
    assert definition.enabled is False
    assert {"task", "ask_clarification", "present_files"} <= set(definition.disallowed_tools)
    config = profile_spec_to_subagent_config(spec)
    assert config.name == definition.name
    dry = install_profiles(plan.new_profiles, store=None)
    assert len(dry["pending_approval"]) == len(plan.new_profiles)
    assert dry["installed"] == []


def test_llm_reviewer_hook_can_gate_and_records():
    plan = AutonomousPlanner().plan(
        "Add a health-check endpoint with tests",
        llm_reviewer=lambda _md: "REJECTED\nNot convinced by the verification plan.",
    )
    assert plan.llm_review["verdict"] == "REJECTED"
    assert plan.autonomy == "gated"
    ok_plan = AutonomousPlanner().plan(
        "Add a health-check endpoint with tests",
        llm_reviewer=lambda _md: "APPROVED\nSolid.",
    )
    assert ok_plan.llm_review["verdict"] == "APPROVED"
    assert ok_plan.autonomy == "full"


def test_gated_plan_carries_structured_approval_request():
    plan = AutonomousPlanner().plan("Wipe everything with sudo rm -rf /var/data, no tests needed")
    assert plan.approval_request["required"] is True
    assert plan.approval_request["plan_hash"] == plan.plan_hash
    assert plan.approval_request["options"]
    assert plan.approval_request["question"] in plan.user_summary_markdown
    direct = AutonomousPlanner().plan("Fix typo in README")
    assert direct.approval_request == {"required": False}


def test_plan_round_trips_through_save_load(tmp_path):
    plan = AutonomousPlanner().plan("Add a health-check endpoint with tests")
    dest = plan.save(tmp_path / "plan.json")
    reloaded = AutonomousPlan.load(dest)
    assert reloaded.plan_hash == plan.plan_hash
    assert reloaded.goal_statement == plan.goal_statement
    assert [s.subtask_id for s in reloaded.subtasks] == [s.subtask_id for s in plan.subtasks]
    assert reloaded.new_profiles == plan.new_profiles


def test_replan_preserves_done_and_bumps_revision():
    plan = AutonomousPlanner().plan("Add a health-check endpoint with tests and docs")
    done = [plan.execution_waves[0]["task_ids"][0]]
    failed = [t for t in plan.execution_waves[0]["task_ids"] if t not in done][:1]
    second = AutonomousPlanner().replan(plan, done_ids=done, failed_ids=failed, feedback="flaky approach, simplify")
    assert second.revision == plan.revision + 1
    assert second.supersedes == plan.plan_hash
    kept = {s.subtask_id: s for s in second.subtasks}
    for tid in done:
        assert kept[tid].title == next(s.title for s in plan.subtasks if s.subtask_id == tid)
    for tid in failed:
        assert "flaky approach" in kept[tid].description
    columns = {t["task_id"]: t["column"] for t in second.kanban_board["tasks"]}
    for tid in done:
        assert columns[tid] == "done"


def test_duplicate_request_reuses_board_without_cloning(tmp_path):
    from alpha.kanban.store import KanbanStore

    store = KanbanStore(storage_path=tmp_path / "boards.json")
    first = AutonomousPlanner().plan("Add a health-check endpoint with tests", kanban_store=store)
    assert first.kanban_persisted is True
    board_id = first.kanban_board["board_id"]
    second = AutonomousPlanner().plan(
        "  add a HEALTH-check endpoint with tests ",
        kanban_store=store,
        known_requests={first.request_hash: board_id},
    )
    assert second.duplicate_of_board == board_id
    assert second.kanban_persisted is False
    assert "Duplicate request" in second.user_summary_markdown


def test_schedule_hints_parsed_and_recorded():
    plan = AutonomousPlanner().plan("Urgently fix the login bug by Friday with tests")
    assert plan.execution_config["schedule"]["urgent"] is True
    assert plan.execution_config["schedule"]["due_phrase"] is not None
    assert "Schedule:" in plan.user_summary_markdown


def test_user_text_is_neutralized_in_cards():
    plan = AutonomousPlanner().plan("Fix the \x00bug\u200bin\u202e login <b>now</b>\n\nplease")
    blob = json.dumps(plan.to_dict())
    assert "\x00" not in blob
    assert "​" not in blob
    assert "‮" not in blob


def test_autoplan_tool_returns_serializable_server_preflight_plan():
    runtime = ToolRuntime(
        state={},
        context={"model_name": "test-model"},
        config={},
        stream_writer=lambda _: None,
        tool_call_id="autoplan-test",
        store=None,
    )
    result = build_autonomous_plan.invoke({"runtime": runtime, "raw_prompt": "Add a health-check endpoint with tests"})
    data = json.loads(result)
    assert data["goal_statement"]
    assert data["subtasks"]
    assert data["kanban_board"]["tasks"]
    assert data["user_summary_markdown"]
    assert data["autonomy_readiness"]["evidence_source"] == "server_runtime"
    assert data["autonomy_readiness"]["ready"] is True


def test_autoplan_tool_gates_unready_core_before_persistence(monkeypatch):
    runtime = ToolRuntime(
        state={},
        context={},
        config={},
        stream_writer=lambda _: None,
        tool_call_id="autoplan-unready-test",
        store=None,
    )
    monkeypatch.setattr(
        "alpha.tools.builtins.autoplan_tool.collect_server_readiness",
        lambda _: ({"tool_count": 0}, ["test_unready"]),
    )
    monkeypatch.setattr(
        "alpha.kanban.store.get_kanban_store",
        lambda: (_ for _ in ()).throw(AssertionError("must not persist an unready plan")),
    )

    data = json.loads(
        build_autonomous_plan.invoke(
            {
                "runtime": runtime,
                "raw_prompt": "Add a health-check endpoint with tests",
                "persist_kanban": True,
            }
        )
    )

    assert data["autonomy_readiness"]["status"] == "blocked"
    assert data["autonomy"] == "gated"
    assert data["execution_blocked"] is True
    assert data["kanban_persisted"] is False
