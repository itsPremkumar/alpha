"""DAG Dependency Graph Resolver and Auto-Unblock Engine for Kanban Tasks."""

from __future__ import annotations

from collections.abc import Mapping

from alpha.kanban.models import KanbanTask


class DependencyGraph:
    """Validates dependencies and resolves automatic task unblocking."""

    @staticmethod
    def are_dependencies_satisfied(task: KanbanTask, all_tasks: Mapping[str, KanbanTask]) -> bool:
        """Check if all prerequisite dependencies for a task are completed."""
        if not task.dependencies:
            return True
        for dep_id in task.dependencies:
            dep_task = all_tasks.get(dep_id)
            if not dep_task or dep_task.column != "done":
                return False
        return True

    @staticmethod
    def has_cycle(all_tasks: Mapping[str, KanbanTask]) -> bool:
        """Detect circular dependencies using DFS graph traversal."""
        visited: dict[str, int] = {}  # 0: unvisited, 1: visiting, 2: visited

        def _dfs(node_id: str) -> bool:
            visited[node_id] = 1
            task = all_tasks.get(node_id)
            if task and task.dependencies:
                for dep in task.dependencies:
                    state = visited.get(dep, 0)
                    if state == 1:
                        return True
                    if state == 0 and _dfs(dep):
                        return True
            visited[node_id] = 2
            return False

        for task_id in all_tasks:
            if visited.get(task_id, 0) == 0:
                if _dfs(task_id):
                    return True
        return False

    @classmethod
    def resolve_unblockable_tasks(cls, all_tasks: Mapping[str, KanbanTask]) -> list[KanbanTask]:
        """Find all currently blocked tasks that can now transition to 'todo'."""
        unblocked: list[KanbanTask] = []
        for task in all_tasks.values():
            if task.column == "blocked":
                if cls.are_dependencies_satisfied(task, all_tasks):
                    unblocked.append(task)
        return unblocked
