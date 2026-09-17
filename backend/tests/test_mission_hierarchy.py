from agent_workspace.mission.hierarchy import (
    ExecutionStatus,
    HierarchyLevel,
    MissionHierarchyTree,
)


def test_hierarchy_tree_construction_and_linkage():
    tree = MissionHierarchyTree()

    # 1. Goal
    goal = tree.create_goal(name="Launch E-Commerce Platform", description="Global launch Q4")
    assert goal.level == HierarchyLevel.GOAL
    assert goal.id == tree.root_goal_id

    # 2. Mission
    mission = tree.create_mission(
        parent_goal_id=goal.id,
        name="Build Checkout Service",
        desired_outcome="PCI-compliant checkout pipeline",
        constraints=["Stripe only", "Zero latency spikes"],
    )
    assert mission.level == HierarchyLevel.MISSION
    assert mission.parent_id == goal.id
    assert mission.id in goal.children_ids

    # 3. Task
    task = tree.create_task(
        parent_mission_id=mission.id,
        name="Implement Payment Webhook",
        assignee_role="backend_developer",
        priority="high",
    )
    assert task.level == HierarchyLevel.TASK
    assert task.parent_id == mission.id
    assert task.id in mission.children_ids

    # 4. Subtask
    subtask = tree.create_subtask(
        parent_task_id=task.id,
        name="Verify Signature",
        description="Verify Stripe-Signature HMAC-SHA256 header",
    )
    assert subtask.level == HierarchyLevel.SUBTASK
    assert subtask.parent_id == task.id

    # 5. Action
    action = tree.create_action(
        parent_subtask_id=subtask.id,
        name="Run Signature Unit Tests",
        action_type="unit_test",
    )
    assert action.level == HierarchyLevel.ACTION

    # 6. Tool Call
    tc = tree.create_tool_call(
        parent_action_id=action.id,
        tool_name="python_repl_tool",
        tool_input={"code": "assert verify_sig(...) == True"},
    )
    assert tc.level == HierarchyLevel.TOOL_CALL
    assert tc.parent_id == action.id

    # Check tree metrics
    assert len(tree.list_nodes()) == 6
    assert len(tree.list_nodes(HierarchyLevel.TOOL_CALL)) == 1


def test_hierarchy_progress_and_auto_completion():
    tree = MissionHierarchyTree()
    goal = tree.create_goal(name="Refactor Database")
    mission = tree.create_mission(parent_goal_id=goal.id, name="Run Migrations")
    task1 = tree.create_task(parent_mission_id=mission.id, name="Apply Schema")
    task2 = tree.create_task(parent_mission_id=mission.id, name="Verify Indexes")

    # Initially progress is 0.0
    assert tree.calculate_progress(goal.id) == 0.0

    # Complete task 1
    tree.update_node_status(task1.id, ExecutionStatus.COMPLETED)
    assert tree.calculate_progress(mission.id) == 0.5
    assert tree.calculate_progress(goal.id) == 0.5

    # Complete task 2 -> mission and goal should auto-complete
    tree.update_node_status(task2.id, ExecutionStatus.COMPLETED)
    assert tree.calculate_progress(mission.id) == 1.0
    assert tree.calculate_progress(goal.id) == 1.0
    assert mission.status == ExecutionStatus.COMPLETED
    assert goal.status == ExecutionStatus.COMPLETED


def test_hierarchy_markdown_tree_output():
    tree = MissionHierarchyTree()
    goal = tree.create_goal(name="Deploy Production Release")
    mission = tree.create_mission(parent_goal_id=goal.id, name="Run Canary Test")
    tree.create_task(parent_mission_id=mission.id, name="Sample 5% Traffic")

    md = tree.to_markdown_tree()
    assert "[GOAL]" in md
    assert "Deploy Production Release" in md
    assert "[MISSION]" in md
    assert "Run Canary Test" in md
    assert "[TASK]" in md
    assert "Sample 5% Traffic" in md
