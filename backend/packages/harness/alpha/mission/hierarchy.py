"""Goal -> Mission -> Task -> Subtask -> Action -> ToolCall Hierarchy Engine.

Inspired by Chapter 58 and 59 of the Master Architecture Blueprint:
The fundamental data model:
    GOAL -> MISSION -> TASK -> SUBTASK -> ACTION -> TOOL CALL
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class HierarchyLevel(str, Enum):
    GOAL = "goal"
    MISSION = "mission"
    TASK = "task"
    SUBTASK = "subtask"
    ACTION = "action"
    TOOL_CALL = "tool_call"


class ExecutionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


@dataclass
class HierarchyNode:
    """Base node representing an execution element in the 6-level hierarchy."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    level: HierarchyLevel = HierarchyLevel.GOAL
    name: str = ""
    description: str = ""
    parent_id: str | None = None
    children_ids: list[str] = field(default_factory=list)
    status: ExecutionStatus = ExecutionStatus.PENDING
    result: Any | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["level"] = self.level.value
        data["status"] = self.status.value
        return data


@dataclass
class GoalNode(HierarchyNode):
    """Level 1: Strategic long-term user or enterprise goal."""
    def __post_init__(self):
        self.level = HierarchyLevel.GOAL


@dataclass
class MissionNode(HierarchyNode):
    """Level 2: Scoped operational mission with constraints and desired outcome."""
    constraints: list[str] = field(default_factory=list)
    desired_outcome: str = ""

    def __post_init__(self):
        self.level = HierarchyLevel.MISSION


@dataclass
class TaskNode(HierarchyNode):
    """Level 3: Discrete unit of work with assignees, dependencies, and priority."""
    assignee_role: str = ""
    priority: str = "medium"  # low, medium, high, critical
    dependencies: list[str] = field(default_factory=list)  # list of task IDs that must finish first

    def __post_init__(self):
        self.level = HierarchyLevel.TASK


@dataclass
class SubtaskNode(HierarchyNode):
    """Level 4: Detailed sub-component of a task."""
    def __post_init__(self):
        self.level = HierarchyLevel.SUBTASK


@dataclass
class ActionNode(HierarchyNode):
    """Level 5: Executable plan step (e.g. read_file, run_tests, git_commit)."""
    action_type: str = "generic"

    def __post_init__(self):
        self.level = HierarchyLevel.ACTION


@dataclass
class ToolCallNode(HierarchyNode):
    """Level 6: Lowest level tool invocation with specific arguments and tool output."""
    tool_name: str = ""
    tool_input: dict[str, Any] = field(default_factory=dict)
    tool_output: str | None = None
    exit_code: int = 0

    def __post_init__(self):
        self.level = HierarchyLevel.TOOL_CALL


class MissionHierarchyTree:
    """Manages the hierarchical tree of Goals, Missions, Tasks, Subtasks, Actions, and ToolCalls."""

    def __init__(self, root_goal_id: str | None = None):
        self._nodes: dict[str, HierarchyNode] = {}
        self.root_goal_id: str | None = root_goal_id

    def add_node(self, node: HierarchyNode) -> HierarchyNode:
        """Register a node and link it to its parent."""
        self._nodes[node.id] = node
        if node.parent_id and node.parent_id in self._nodes:
            parent = self._nodes[node.parent_id]
            if node.id not in parent.children_ids:
                parent.children_ids.append(node.id)
        if node.level == HierarchyLevel.GOAL and self.root_goal_id is None:
            self.root_goal_id = node.id
        return node

    def create_goal(self, name: str, description: str = "", metadata: dict[str, Any] | None = None) -> GoalNode:
        goal = GoalNode(
            name=name,
            description=description,
            metadata=metadata or {},
        )
        self.add_node(goal)
        return goal

    def create_mission(
        self,
        parent_goal_id: str,
        name: str,
        description: str = "",
        desired_outcome: str = "",
        constraints: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MissionNode:
        if parent_goal_id not in self._nodes:
            raise KeyError(f"Parent Goal ID {parent_goal_id} not found in tree.")
        mission = MissionNode(
            name=name,
            description=description,
            parent_id=parent_goal_id,
            desired_outcome=desired_outcome,
            constraints=constraints or [],
            metadata=metadata or {},
        )
        self.add_node(mission)
        return mission

    def create_task(
        self,
        parent_mission_id: str,
        name: str,
        description: str = "",
        assignee_role: str = "general_agent",
        priority: str = "medium",
        dependencies: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TaskNode:
        if parent_mission_id not in self._nodes:
            raise KeyError(f"Parent Mission ID {parent_mission_id} not found in tree.")
        task = TaskNode(
            name=name,
            description=description,
            parent_id=parent_mission_id,
            assignee_role=assignee_role,
            priority=priority,
            dependencies=dependencies or [],
            metadata=metadata or {},
        )
        self.add_node(task)
        return task

    def create_subtask(
        self,
        parent_task_id: str,
        name: str,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> SubtaskNode:
        if parent_task_id not in self._nodes:
            raise KeyError(f"Parent Task ID {parent_task_id} not found in tree.")
        subtask = SubtaskNode(
            name=name,
            description=description,
            parent_id=parent_task_id,
            metadata=metadata or {},
        )
        self.add_node(subtask)
        return subtask

    def create_action(
        self,
        parent_subtask_id: str,
        name: str,
        action_type: str = "code_exec",
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> ActionNode:
        if parent_subtask_id not in self._nodes:
            raise KeyError(f"Parent Subtask ID {parent_subtask_id} not found in tree.")
        action = ActionNode(
            name=name,
            description=description,
            parent_id=parent_subtask_id,
            action_type=action_type,
            metadata=metadata or {},
        )
        self.add_node(action)
        return action

    def create_tool_call(
        self,
        parent_action_id: str,
        tool_name: str,
        tool_input: dict[str, Any],
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> ToolCallNode:
        if parent_action_id not in self._nodes:
            raise KeyError(f"Parent Action ID {parent_action_id} not found in tree.")
        tc = ToolCallNode(
            name=f"call:{tool_name}",
            description=description,
            parent_id=parent_action_id,
            tool_name=tool_name,
            tool_input=tool_input,
            metadata=metadata or {},
        )
        self.add_node(tc)
        return tc

    def get_node(self, node_id: str) -> HierarchyNode | None:
        return self._nodes.get(node_id)

    def list_nodes(self, level: HierarchyLevel | None = None) -> list[HierarchyNode]:
        if level is None:
            return list(self._nodes.values())
        return [n for n in self._nodes.values() if n.level == level]

    def update_node_status(
        self,
        node_id: str,
        status: ExecutionStatus,
        result: Any | None = None,
        error: str | None = None,
    ) -> HierarchyNode:
        """Update node status and propagate progress upwards if all children are completed."""
        node = self.get_node(node_id)
        if not node:
            raise KeyError(f"Node ID {node_id} not found.")

        node.status = status
        if result is not None:
            node.result = result
        if error is not None:
            node.error = error

        # Auto-resolve parent status if all siblings have finished
        if node.parent_id and status in (ExecutionStatus.COMPLETED, ExecutionStatus.FAILED):
            self._check_parent_completion(node.parent_id)

        return node

    def _check_parent_completion(self, parent_id: str) -> None:
        parent = self._nodes.get(parent_id)
        if not parent or not parent.children_ids:
            return

        children = [self._nodes[cid] for cid in parent.children_ids if cid in self._nodes]
        if not children:
            return

        all_completed = all(c.status == ExecutionStatus.COMPLETED for c in children)
        any_failed = any(c.status == ExecutionStatus.FAILED for c in children)

        if all_completed:
            parent.status = ExecutionStatus.COMPLETED
            if parent.parent_id:
                self._check_parent_completion(parent.parent_id)
        elif any_failed and all(c.status in (ExecutionStatus.COMPLETED, ExecutionStatus.FAILED, ExecutionStatus.CANCELLED) for c in children):
            parent.status = ExecutionStatus.FAILED
            if parent.parent_id:
                self._check_parent_completion(parent.parent_id)

    def calculate_progress(self, node_id: str) -> float:
        """Recursively calculate completion ratio (0.0 to 1.0) for any node."""
        node = self.get_node(node_id)
        if not node:
            return 0.0

        if node.status == ExecutionStatus.COMPLETED:
            return 1.0

        if not node.children_ids:
            return 0.0

        child_progresses = [
            self.calculate_progress(cid) for cid in node.children_ids if cid in self._nodes
        ]
        if not child_progresses:
            return 0.0

        return sum(child_progresses) / len(child_progresses)

    def to_tree_dict(self, node_id: str | None = None) -> dict[str, Any]:
        """Convert tree starting from node_id (or root) to nested dictionary."""
        target_id = node_id or self.root_goal_id
        if not target_id or target_id not in self._nodes:
            return {}

        root = self._nodes[target_id]
        data = root.to_dict()
        data["progress"] = self.calculate_progress(root.id)
        data["children"] = [
            self.to_tree_dict(cid) for cid in root.children_ids if cid in self._nodes
        ]
        return data

    def to_markdown_tree(self, node_id: str | None = None, indent: int = 0) -> str:
        """Render a clean ASCII/markdown outline of the hierarchy."""
        target_id = node_id or self.root_goal_id
        if not target_id or target_id not in self._nodes:
            return "*(Empty Hierarchy Tree)*"

        node = self._nodes[target_id]
        status_icons = {
            ExecutionStatus.PENDING: "⏳",
            ExecutionStatus.RUNNING: "🔄",
            ExecutionStatus.COMPLETED: "✅",
            ExecutionStatus.FAILED: "❌",
            ExecutionStatus.BLOCKED: "🛑",
            ExecutionStatus.CANCELLED: "🚫",
        }
        icon = status_icons.get(node.status, "•")
        progress_pct = int(self.calculate_progress(node.id) * 100)
        prefix = "  " * indent + f"{icon} **[{node.level.value.upper()}]** {node.name} ({progress_pct}%)"
        if node.description:
            prefix += f": {node.description}"

        lines = [prefix]
        for cid in node.children_ids:
            if cid in self._nodes:
                lines.append(self.to_markdown_tree(cid, indent=indent + 1))

        return "\n".join(lines)
