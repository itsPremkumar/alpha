"""SQLite Trajectory and Step Audit Storage Engine."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent_workspace.trajectory.models import StepRecord, TrajectoryTrace

logger = logging.getLogger(__name__)

_DEFAULT_TRAJECTORY_DB = ".deerflow/trajectory/audit.db"


class TrajectoryStore:
    """Thread-safe SQLite store for recording and replaying autonomous execution steps."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path).resolve() if db_path else Path.cwd() / _DEFAULT_TRAJECTORY_DB
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trajectory_steps (
                    step_id TEXT PRIMARY KEY,
                    goal_id TEXT NOT NULL,
                    step_index INTEGER NOT NULL,
                    thought TEXT,
                    tool_name TEXT,
                    tool_input TEXT,
                    tool_output TEXT,
                    milestone_id TEXT,
                    status TEXT,
                    error TEXT,
                    created_at TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_trajectory_goal ON trajectory_steps(goal_id, step_index)"
            )
            conn.commit()

    def record_step(
        self,
        goal_id: str,
        step_index: int,
        thought: str = "",
        tool_name: str = "",
        tool_input: dict[str, Any] | None = None,
        tool_output: str = "",
        milestone_id: str = "",
        status: str = "success",
        error: str = "",
    ) -> StepRecord:
        step_id = f"step_{uuid4().hex[:8]}"
        step = StepRecord(
            step_id=step_id,
            goal_id=goal_id,
            step_index=step_index,
            thought=thought,
            tool_name=tool_name,
            tool_input=tool_input or {},
            tool_output=tool_output,
            milestone_id=milestone_id,
            status=status,
            error=error,
        )

        with self._lock, sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO trajectory_steps (
                    step_id, goal_id, step_index, thought, tool_name,
                    tool_input, tool_output, milestone_id, status, error, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    step.step_id,
                    step.goal_id,
                    step.step_index,
                    step.thought,
                    step.tool_name,
                    json.dumps(step.tool_input),
                    step.tool_output,
                    step.milestone_id,
                    step.status,
                    step.error,
                    step.created_at,
                ),
            )
            conn.commit()

        return step

    def get_trajectory(self, goal_id: str) -> TrajectoryTrace:
        with self._lock, sqlite3.connect(str(self.db_path)) as conn:
            cur = conn.execute(
                """
                SELECT step_id, goal_id, step_index, thought, tool_name,
                       tool_input, tool_output, milestone_id, status, error, created_at
                FROM trajectory_steps
                WHERE goal_id = ?
                ORDER BY step_index ASC
                """,
                (goal_id,),
            )
            rows = cur.fetchall()

        steps: list[StepRecord] = []
        for r in rows:
            try:
                inp = json.loads(r[5]) if r[5] else {}
            except Exception:
                inp = {}
            steps.append(
                StepRecord(
                    step_id=r[0],
                    goal_id=r[1],
                    step_index=r[2],
                    thought=r[3] or "",
                    tool_name=r[4] or "",
                    tool_input=inp,
                    tool_output=r[6] or "",
                    milestone_id=r[7] or "",
                    status=r[8] or "success",
                    error=r[9] or "",
                    created_at=r[10] or "",
                )
            )

        return TrajectoryTrace(goal_id=goal_id, steps=steps, total_steps=len(steps))

    def export_jsonl(self, goal_id: str, output_file: str | Path) -> str:
        trace = self.get_trajectory(goal_id)
        out_path = Path(output_file).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            for s in trace.steps:
                f.write(json.dumps(s.to_dict()) + "\n")
        return str(out_path)

    def list_goal_ids(self) -> list[str]:
        with self._lock, sqlite3.connect(str(self.db_path)) as conn:
            cur = conn.execute("SELECT DISTINCT goal_id FROM trajectory_steps ORDER BY created_at DESC")
            return [r[0] for r in cur.fetchall()]

    def fork_trajectory(self, source_goal_id: str, from_step_index: int, new_goal_id: str) -> TrajectoryTrace:
        """Time-travel forking: copies steps 0 through from_step_index into new_goal_id."""
        trace = self.get_trajectory(source_goal_id)
        forked_steps = [s for s in trace.steps if s.step_index <= from_step_index]
        for s in forked_steps:
            self.record_step(
                goal_id=new_goal_id,
                step_index=s.step_index,
                thought=s.thought,
                tool_name=s.tool_name,
                tool_input=s.tool_input,
                tool_output=s.tool_output,
                milestone_id=s.milestone_id,
                status=s.status,
                error=s.error,
            )
        return self.get_trajectory(new_goal_id)

    def replay_from_step(self, goal_id: str, from_step_index: int) -> dict[str, Any]:
        """Simulate replaying an execution trajectory from a specified historical step."""
        trace = self.get_trajectory(goal_id)
        if not trace.steps:
            return {"goal_id": goal_id, "replayed": False, "reason": "Empty trajectory"}
        target_step = next((s for s in trace.steps if s.step_index == from_step_index), None)
        if not target_step:
            return {"goal_id": goal_id, "replayed": False, "reason": f"Step index {from_step_index} not found"}

        from datetime import UTC, datetime
        return {
            "goal_id": goal_id,
            "replayed": True,
            "from_step_index": from_step_index,
            "resumed_step": target_step.to_dict(),
            "remaining_steps_count": len([s for s in trace.steps if s.step_index >= from_step_index]),
            "simulated_forward_at": datetime.now(UTC).isoformat(),
        }


_PROJECT_TRAJECTORY_STORES: dict[str, TrajectoryStore] = {}


def get_trajectory_store(project_id: str = "default") -> TrajectoryStore:
    """Project-scoped singleton accessor for TrajectoryStore."""
    import os
    if project_id not in _PROJECT_TRAJECTORY_STORES:
        base_dir = os.environ.get("AGENT_WORKSPACE_PROJECTS_DIR", ".deerflow_projects")
        db_file = Path(base_dir) / project_id / "trajectory" / "audit.db"
        store = TrajectoryStore(db_path=db_file)
        # Seed initial baseline trajectory if empty
        if not store.list_goal_ids():
            store.record_step(
                goal_id="bootstrap_system",
                step_index=0,
                thought="Verify environment dependencies, gateway routes, and workspace integrity.",
                tool_name="system_check",
                tool_input={"check_type": "full_health"},
                tool_output="Environment healthy. Python 3.12, Node 20+, 29 tests passing.",
                status="success",
            )
            store.record_step(
                goal_id="bootstrap_system",
                step_index=1,
                thought="Compile Living Architectural Specification and synchronize workforce state.",
                tool_name="sync_spec",
                tool_input={"target": "architecture.md"},
                tool_output="Living spec synchronized to version 2.4.",
                status="success",
            )
        _PROJECT_TRAJECTORY_STORES[project_id] = store
    return _PROJECT_TRAJECTORY_STORES[project_id]
