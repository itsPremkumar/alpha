"""DAG Task Workflow Engine (mass-ulw / omo-dag).

Implements dependency-ordered multi-agent execution:
1. Declarative task graphs with nodes, categories, and dependencies.
2. Topological wave execution (nodes execute in parallel waves as dependencies clear).
3. Disjoint write scopes to prevent parallel git and filesystem race conditions.
4. "TREAT AS FALSE UNTIL YOU PROVE IT" verification protocol: tasks cannot be
   marked complete without registered verification evidence (test outputs, compiler logs).
"""

from __future__ import annotations

from dataclasses import dataclass, field


class WriteScopeCollisionError(ValueError):
    """Raised when two nodes in the same parallel execution wave share overlapping write paths."""
    pass


class UnverifiedNodeCompletionError(RuntimeError):
    """Raised when an attempt is made to mark a node complete without concrete evidence."""
    pass


@dataclass
class DAGNode:
    id: str
    prompt: str
    category: str = "quick"
    depends_on: list[str] = field(default_factory=list)
    write_scope: list[str] = field(default_factory=list)  # Files/directories this node may touch
    status: str = "pending"  # "pending", "running", "completed", "failed"
    evidence: list[str] = field(default_factory=list)
    output: str | None = None


@dataclass
class DAGWorkflow:
    key: str
    name: str
    nodes: dict[str, DAGNode] = field(default_factory=dict)

    def add_node(
        self,
        node_id: str,
        prompt: str,
        category: str = "quick",
        depends_on: list[str] | None = None,
        write_scope: list[str] | None = None,
    ) -> DAGNode:
        if node_id in self.nodes:
            raise ValueError(f"Node '{node_id}' already exists in workflow '{self.key}'")
        node = DAGNode(
            id=node_id,
            prompt=prompt,
            category=category,
            depends_on=depends_on or [],
            write_scope=write_scope or [],
        )
        self.nodes[node_id] = node
        return node

    def get_executable_waves(self) -> list[list[str]]:
        """Compute topological execution waves (nodes that can run concurrently).
        
        Raises ValueError if a cycle is detected.
        """
        # Calculate in-degrees
        in_degree: dict[str, int] = {nid: 0 for nid in self.nodes}
        graph: dict[str, list[str]] = {nid: [] for nid in self.nodes}

        for nid, node in self.nodes.items():
            for dep in node.depends_on:
                if dep not in self.nodes:
                    raise ValueError(f"Node '{nid}' depends on unknown node '{dep}'")
                graph[dep].append(nid)
                in_degree[nid] += 1

        # Kahn's algorithm wave by wave
        waves: list[list[str]] = []
        current_wave = [nid for nid, deg in in_degree.items() if deg == 0]
        processed_count = 0

        while current_wave:
            waves.append(sorted(current_wave))
            processed_count += len(current_wave)
            next_wave = []
            for nid in current_wave:
                for dependent in graph[nid]:
                    in_degree[dependent] -= 1
                    if in_degree[dependent] == 0:
                        next_wave.append(dependent)
            current_wave = next_wave

        if processed_count != len(self.nodes):
            raise ValueError("Dependency cycle detected in DAG workflow!")

        return waves

    def validate_wave_write_scopes(self) -> None:
        """Verify that nodes in each parallel wave have disjoint write scopes."""
        waves = self.get_executable_waves()
        for wave_idx, wave in enumerate(waves):
            seen_scopes: dict[str, str] = {}
            for nid in wave:
                for scope in self.nodes[nid].write_scope:
                    norm = scope.replace("\\", "/").rstrip("/").lower()
                    if norm in seen_scopes:
                        other_nid = seen_scopes[norm]
                        raise WriteScopeCollisionError(
                            f"Parallel wave {wave_idx} write collision: node '{nid}' and node '{other_nid}' "
                            f"both declare write scope '{scope}'."
                        )
                    seen_scopes[norm] = nid

    def record_evidence(self, node_id: str, evidence: str) -> None:
        """Register verifiable evidence for a node's execution."""
        if node_id not in self.nodes:
            raise KeyError(f"Node '{node_id}' not found")
        self.nodes[node_id].evidence.append(evidence)

    def mark_completed(self, node_id: str, output: str | None = None) -> None:
        """Mark node completed, strictly requiring evidence per the OmO doctrine."""
        if node_id not in self.nodes:
            raise KeyError(f"Node '{node_id}' not found")
        node = self.nodes[node_id]
        if not node.evidence:
            raise UnverifiedNodeCompletionError(
                f"Node '{node_id}' cannot be marked completed without verification evidence! "
                f"(TREAT AS FALSE UNTIL YOU PROVE IT)"
            )
        node.status = "completed"
        if output:
            node.output = output

    def is_all_completed(self) -> bool:
        return all(node.status == "completed" for node in self.nodes.values())


class DAGEngine:
    """Registry and manager for active DAG workflows."""

    def __init__(self):
        self._workflows: dict[str, DAGWorkflow] = {}

    def create_workflow(self, key: str, name: str) -> DAGWorkflow:
        if key in self._workflows:
            return self._workflows[key]
        wf = DAGWorkflow(key=key, name=name)
        self._workflows[key] = wf
        return wf

    def get_workflow(self, key: str) -> DAGWorkflow | None:
        return self._workflows.get(key)

    def list_workflows(self) -> list[str]:
        return list(self._workflows.keys())
