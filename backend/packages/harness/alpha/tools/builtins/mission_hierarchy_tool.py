"""Built-in Mission Hierarchy and Work Queue DAG LangChain Tools."""

from __future__ import annotations

import json

from langchain.tools import tool

from alpha.mission.hierarchy import (
    ExecutionStatus,
    MissionHierarchyTree,
)
from alpha.mission.work_queue import (
    DurableWorkQueue,
    TaskPriority,
)

_GLOBAL_TREE = MissionHierarchyTree()
_GLOBAL_QUEUE = DurableWorkQueue()


@tool("manage_mission_hierarchy", parse_docstring=True)
def manage_mission_hierarchy(
    action: str,
    node_id: str = "",
    parent_id: str = "",
    name: str = "",
    description: str = "",
    level_type: str = "task",
    status: str = "pending",
    tool_name: str = "",
    tool_input_json: str = "{}",
    constraints_csv: str = "",
    desired_outcome: str = "",
    priority: str = "medium",
    dependencies_csv: str = "",
) -> str:
    """Manage the 6-level Goal -> Mission -> Task -> Subtask -> Action -> ToolCall execution hierarchy.

    Args:
        action: 'create_goal', 'create_mission', 'create_task', 'create_subtask', 'create_action', 'create_tool_call', 'update_status', 'get_tree', 'get_progress'.
        node_id: Target node ID for status update, tree fetch, or progress calculation.
        parent_id: Parent node ID when creating missions, tasks, subtasks, actions, or tool calls.
        name: Name / title of the node being created.
        description: Description of the node's intent or requirements.
        level_type: Type of action or task role.
        status: Status value ('pending', 'running', 'completed', 'failed', 'blocked', 'cancelled').
        tool_name: Name of tool when registering a tool call.
        tool_input_json: Tool call argument dictionary in JSON string.
        constraints_csv: Comma-separated constraints for missions.
        desired_outcome: Target state or outcome for missions.
        priority: Priority of task ('low', 'medium', 'high', 'critical').
        dependencies_csv: Comma-separated list of prerequisite task IDs.
    """
    try:
        if action == "create_goal":
            goal = _GLOBAL_TREE.create_goal(name=name, description=description)
            return json.dumps({"status": "created", "goal": goal.to_dict()}, indent=2)

        elif action == "create_mission":
            constraints = [c.strip() for c in constraints_csv.split(",") if c.strip()]
            mission = _GLOBAL_TREE.create_mission(
                parent_goal_id=parent_id,
                name=name,
                description=description,
                desired_outcome=desired_outcome,
                constraints=constraints,
            )
            return json.dumps({"status": "created", "mission": mission.to_dict()}, indent=2)

        elif action == "create_task":
            deps = [d.strip() for d in dependencies_csv.split(",") if d.strip()]
            task = _GLOBAL_TREE.create_task(
                parent_mission_id=parent_id,
                name=name,
                description=description,
                assignee_role=level_type or "general_agent",
                priority=priority,
                dependencies=deps,
            )
            return json.dumps({"status": "created", "task": task.to_dict()}, indent=2)

        elif action == "create_subtask":
            subtask = _GLOBAL_TREE.create_subtask(
                parent_task_id=parent_id,
                name=name,
                description=description,
            )
            return json.dumps({"status": "created", "subtask": subtask.to_dict()}, indent=2)

        elif action == "create_action":
            action_node = _GLOBAL_TREE.create_action(
                parent_subtask_id=parent_id,
                name=name,
                description=description,
                action_type=level_type or "generic",
            )
            return json.dumps({"status": "created", "action": action_node.to_dict()}, indent=2)

        elif action == "create_tool_call":
            try:
                t_input = json.loads(tool_input_json)
            except Exception:
                t_input = {"raw": tool_input_json}
            tc = _GLOBAL_TREE.create_tool_call(
                parent_action_id=parent_id,
                tool_name=tool_name,
                tool_input=t_input,
                description=description,
            )
            return json.dumps({"status": "created", "tool_call": tc.to_dict()}, indent=2)

        elif action == "update_status":
            st_enum = ExecutionStatus(status.lower())
            updated = _GLOBAL_TREE.update_node_status(node_id=node_id, status=st_enum)
            return json.dumps({"status": "updated", "node": updated.to_dict()}, indent=2)

        elif action == "get_progress":
            progress = _GLOBAL_TREE.calculate_progress(node_id)
            return json.dumps({"node_id": node_id, "progress_percent": int(progress * 100)}, indent=2)

        elif action == "get_tree":
            if level_type == "markdown":
                return _GLOBAL_TREE.to_markdown_tree(node_id or None)
            return json.dumps(_GLOBAL_TREE.to_tree_dict(node_id or None), indent=2)

        else:
            return f"Error: Unknown action '{action}'."

    except Exception as exc:
        return f"Error managing mission hierarchy: {exc}"


@tool("schedule_work_queue", parse_docstring=True)
def schedule_work_queue(
    action: str,
    title: str = "",
    description: str = "",
    priority_level: str = "medium",
    dependencies_csv: str = "",
    task_id: str = "",
    result_str: str = "",
    error_str: str = "",
) -> str:
    """Manage the durable DAG task scheduler, execution queue, and budget guardrails.

    Args:
        action: 'add_task', 'dispatch_next', 'complete_task', 'fail_task', 'get_ready', 'topological_sort', 'get_stats'.
        title: Title of work item to enqueue.
        description: Description of work to be performed.
        priority_level: 'low', 'medium', 'high', 'critical'.
        dependencies_csv: Prerequisite task IDs that must complete first.
        task_id: Task ID for completion, failure, or inspection.
        result_str: Result or output payload upon completion.
        error_str: Failure or error reason upon failure.
    """
    try:
        if action == "add_task":
            deps = [d.strip() for d in dependencies_csv.split(",") if d.strip()]
            prio = TaskPriority[priority_level.upper()]
            task = _GLOBAL_QUEUE.add_task(
                title=title,
                description=description,
                priority=prio,
                dependencies=deps,
                task_id=task_id or None,
            )
            return json.dumps({"status": "enqueued", "task": task.to_dict()}, indent=2)

        elif action == "dispatch_next":
            task = _GLOBAL_QUEUE.dispatch_next()
            if not task:
                return json.dumps({"status": "no_tasks_ready"})
            return json.dumps({"status": "dispatched", "task": task.to_dict()}, indent=2)

        elif action == "complete_task":
            task = _GLOBAL_QUEUE.complete_task(task_id, result=result_str)
            return json.dumps({"status": "completed", "task": task.to_dict()}, indent=2)

        elif action == "fail_task":
            task = _GLOBAL_QUEUE.fail_task(task_id, error=error_str)
            return json.dumps({"status": "failed_or_recovering", "task": task.to_dict()}, indent=2)

        elif action == "get_ready":
            ready = _GLOBAL_QUEUE.get_ready_tasks()
            return json.dumps([t.to_dict() for t in ready], indent=2)

        elif action == "topological_sort":
            ordered_ids = _GLOBAL_QUEUE.topological_sort()
            return json.dumps({"execution_sequence": ordered_ids}, indent=2)

        elif action == "get_stats":
            return json.dumps(_GLOBAL_QUEUE.stats(), indent=2)

        else:
            return f"Error: Unknown action '{action}'."

    except Exception as exc:
        return f"Error scheduling work queue: {exc}"
