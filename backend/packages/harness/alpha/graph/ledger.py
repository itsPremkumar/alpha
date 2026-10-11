"""SQLite-backed durable ledger for the graph plane.

What this module is
-------------------
The **authoritative** store for goals, tasks, attempts, artifacts, claims,
events, and approvals. Every status transition, every artifact reference, every
claim lifecycle change is written here — not in LangGraph checkpoint state, not
in the in-memory graph state, not in a JSON file.

Why a separate store from LangGraph checkpoints
-----------------------------------------------
The plan's §12.1 is explicit: "Use separate persistence mechanisms for separate
purposes." LangGraph checkpoints preserve **runnable graph state** — the
messages, the tool-call history, the intermediate values that let a thread
resume mid-execution. The task ledger tracks **user-visible work** — what was
planned, what ran, what succeeded, what failed, what was verified. These have
different lifetimes, different query patterns, and different consistency needs.
Conflating them means a checkpoint compaction (which is routine) can destroy
the audit trail, or a task status write bloats every checkpoint.

Why SQLite
-----------
The plan's §3.7 recommendation: "use SQLite tables for nodes, edges, claims,
evidence, tasks, events, and indexes; use SQLite FTS for lexical search where
available; optionally use embeddings later. This minimizes service count and
works offline." The repository already uses SQLite for the checkpointer
(``AsyncSqliteSaver``) and for the peer network storage, so there is no new
operational surface.

The ledger is **process-local and restart-recoverable**, not cross-process
exactly-once. The AGENTS.md honesty constraint is explicit: "single-process
JSON/JSONL state is never cross-process exactly-once." A second process sharing
the same SQLite file would need advisory locks (like
``alpha.utils.file_lock``) for cross-process coordination, which is a separate
concern from the ledger's own correctness.

Schema
------
The schema is intentionally flat and denormalized where it helps querying.
Each table has a ``created_at`` / ``updated_at`` pair. Foreign keys are
declared but not enforced by default (SQLite's ``PRAGMA foreign_keys`` is
off by default); the ledger enforces referential integrity in code, because a
cascade delete that the caller did not expect is worse than a foreign key
violation that is caught at write time.

Migrations
----------
The schema is created with ``CREATE TABLE IF NOT EXISTS`` on first use, so
there is no separate migration step for the initial schema. Future schema
changes will add a ``schema_version`` table and a migration path, following
the pattern in ``alpha.persistence.migrations``.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alpha.graph.ids import (
    ApprovalId,
    ArtifactId,
    AttemptId,
    ClaimId,
    EventId,
    GoalId,
    GraphId,
    TaskId,
)
from alpha.graph.models import (
    AcceptanceCheck,
    AcceptanceReport,
    ApprovalRequest,
    ApprovalState,
    Artifact,
    ArtifactSensitivity,
    AttemptOutcome,
    CapabilityRequirement,
    ClaimStatus,
    CriterionVerdict,
    EdgeType,
    EventSeverity,
    EventType,
    Goal,
    GoalMode,
    GoalStatus,
    GraphBudget,
    GraphEdge,
    IllegalTransition,
    ModelValidationError,
    RiskLevel,
    RunEvent,
    TaskAttempt,
    TaskNode,
    TaskStatus,
    TaskType,
    now_iso,
)

__all__ = [
    "LedgerError",
    "LedgerConflict",
    "TaskLedger",
    "TaskLedgerConfig",
]


class LedgerError(RuntimeError):
    """A ledger operation failed. The store is in a known state."""


class LedgerConflict(LedgerError):
    """A write was refused because it conflicts with the current state.

    This is the expected outcome of two writers racing: the second one gets a
    ``LedgerConflict`` naming what it tried to do and what the current state
    is, so it can retry or report. It is not an error in the ledger itself.
    """


def _require_reason(reason: str) -> str:
    text = str(reason).strip()
    if not text:
        raise LedgerConflict("a failure or blocked reason must not be empty: an unexplained status is indistinguishable from a task nobody scheduled")
    return text


@dataclass(slots=True)
class TaskLedgerConfig:
    """Configuration for a :class:`TaskLedger` instance.

    ``database_path`` defaults to ``runtime_home()/graph_store/ledger.db``,
    which is the same pattern ``alpha.workflow.leases`` uses for its store.
    The directory is created on first write, not on construction, so a
    read-only probe (``get_goal``) does not create files.
    """

    database_path: Path | None = None
    #: WAL mode allows concurrent readers with a single writer, which is the
    #: access pattern the Gateway has (one writer per run, many readers for
    #: the UI). It is the default for SQLite 3.7+ and is safe to enable here.
    enable_wal: bool = True
    #: Foreign keys are enforced in code, not by SQLite. This flag is for
    #: debugging and should stay False in production.
    enforce_foreign_keys: bool = False


class TaskLedger:
    """The durable store for the graph plane.

    Thread-safe: a single ``threading.Lock`` guards all writes. Reads are
    lock-free (SQLite's own locking handles concurrent readers in WAL mode),
    but a read that is part of a read-modify-write sequence must be inside the
    lock, which is why :meth:`transition_task` takes the lock for the whole
    operation.

    The ledger does **not** enforce the transition table. That is
    :class:`~alpha.graph.models.TaskNode`'s job, and it is enforced there so the
    rule holds even for an in-memory task that never reaches the ledger. The
    ledger *re-checks* the transition on write, because a task object that has
    been mutated in memory and then written back could bypass the model's
    guard.
    """

    def __init__(self, config: TaskLedgerConfig | None = None) -> None:
        self._config = config or TaskLedgerConfig()
        self._lock = threading.Lock()
        self._connection: sqlite3.Connection | None = None

    # -- connection management -------------------------------------------

    @property
    def database_path(self) -> Path:
        if self._config.database_path is not None:
            return self._config.database_path
        from alpha.config.runtime_paths import runtime_home

        return runtime_home() / "graph_store" / "ledger.db"

    def _connect(self) -> sqlite3.Connection:
        if self._connection is not None:
            return self._connection
        path = self.database_path
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        if self._config.enable_wal:
            conn.execute("PRAGMA journal_mode=WAL")
        if self._config.enforce_foreign_keys:
            conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        self._connection = conn
        self._create_schema(conn)
        return conn

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        """Create the schema if it does not exist.

        ``CREATE TABLE IF NOT EXISTS`` is idempotent, so this is safe to call
        on every connection. The schema is intentionally simple: each table
        has a primary key, a ``created_at`` / ``updated_at`` pair, and a
        ``data`` JSON column for the full model dump. The JSON column is the
        source of truth for reads; the typed columns are for querying and
        indexing.
        """
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS goals (
                goal_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                mode TEXT NOT NULL,
                status TEXT NOT NULL,
                risk_level TEXT NOT NULL DEFAULT 'low',
                plan_version INTEGER NOT NULL DEFAULT 1,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                deadline_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                data TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_goals_run_id ON goals(run_id);
            CREATE INDEX IF NOT EXISTS idx_goals_status ON goals(status);

            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                goal_id TEXT NOT NULL,
                title TEXT NOT NULL,
                task_type TEXT NOT NULL DEFAULT 'tool',
                status TEXT NOT NULL,
                risk_level TEXT NOT NULL DEFAULT 'low',
                assigned_worker TEXT NOT NULL DEFAULT '',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 2,
                priority INTEGER NOT NULL DEFAULT 50,
                error_code TEXT,
                failure_summary TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                data TEXT NOT NULL,
                FOREIGN KEY (goal_id) REFERENCES goals(goal_id)
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_goal_id ON tasks(goal_id);
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);

            CREATE TABLE IF NOT EXISTS edges (
                edge_id TEXT PRIMARY KEY,
                graph_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                edge_type TEXT NOT NULL DEFAULT 'depends_on',
                reason TEXT NOT NULL DEFAULT '',
                source_artifact_id TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_edges_graph_id ON edges(graph_id);
            CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
            CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);

            CREATE TABLE IF NOT EXISTS attempts (
                attempt_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                goal_id TEXT NOT NULL,
                attempt_number INTEGER NOT NULL,
                idempotency_key TEXT NOT NULL,
                outcome TEXT,
                worker TEXT NOT NULL DEFAULT '',
                started_at TEXT NOT NULL,
                finished_at TEXT,
                failure_summary TEXT,
                error_code TEXT,
                data TEXT NOT NULL,
                FOREIGN KEY (task_id) REFERENCES tasks(task_id)
            );
            CREATE INDEX IF NOT EXISTS idx_attempts_task_id ON attempts(task_id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_attempts_idempotency ON attempts(idempotency_key);

            CREATE TABLE IF NOT EXISTS artifacts (
                artifact_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                goal_id TEXT,
                task_id TEXT,
                artifact_type TEXT NOT NULL,
                uri TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                mime_type TEXT NOT NULL DEFAULT 'application/octet-stream',
                size_bytes INTEGER,
                created_by_node TEXT NOT NULL DEFAULT '',
                sensitivity TEXT NOT NULL DEFAULT 'internal',
                created_at TEXT NOT NULL,
                data TEXT NOT NULL,
                FOREIGN KEY (goal_id) REFERENCES goals(goal_id),
                FOREIGN KEY (task_id) REFERENCES tasks(task_id)
            );
            CREATE INDEX IF NOT EXISTS idx_artifacts_run_id ON artifacts(run_id);
            CREATE INDEX IF NOT EXISTS idx_artifacts_goal_id ON artifacts(goal_id);
            CREATE INDEX IF NOT EXISTS idx_artifacts_task_id ON artifacts(task_id);

            CREATE TABLE IF NOT EXISTS claims (
                claim_id TEXT PRIMARY KEY,
                subject_entity_id TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object_entity_id TEXT,
                claim_text TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'candidate',
                source_artifact_id TEXT,
                source_locator TEXT NOT NULL DEFAULT '',
                confidence REAL,
                valid_from TEXT,
                valid_to TEXT,
                observed_at TEXT,
                recorded_at TEXT NOT NULL,
                created_by TEXT NOT NULL DEFAULT '',
                verified_by TEXT NOT NULL DEFAULT '',
                failure_summary TEXT,
                contradicts TEXT NOT NULL DEFAULT '[]',
                data TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_claims_subject ON claims(subject_entity_id);
            CREATE INDEX IF NOT EXISTS idx_claims_status ON claims(status);
            CREATE INDEX IF NOT EXISTS idx_claims_predicate ON claims(predicate);

            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                goal_id TEXT,
                task_id TEXT,
                attempt_id TEXT,
                parent_event_id TEXT,
                trace_id TEXT NOT NULL DEFAULT '',
                span_id TEXT NOT NULL DEFAULT '',
                event_type TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'info',
                status TEXT NOT NULL DEFAULT '',
                component TEXT NOT NULL DEFAULT 'alpha.graph',
                operation TEXT NOT NULL DEFAULT '',
                attempt INTEGER,
                duration_ms INTEGER,
                error_code TEXT,
                message_safe TEXT NOT NULL DEFAULT '',
                attributes TEXT NOT NULL DEFAULT '{}',
                artifact_ids TEXT NOT NULL DEFAULT '[]',
                timestamp TEXT NOT NULL,
                is_completion INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_events_run_id ON events(run_id);
            CREATE INDEX IF NOT EXISTS idx_events_goal_id ON events(goal_id);
            CREATE INDEX IF NOT EXISTS idx_events_task_id ON events(task_id);
            CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
            CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);

            CREATE TABLE IF NOT EXISTS approvals (
                approval_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                task_id TEXT,
                operation TEXT NOT NULL,
                target TEXT NOT NULL,
                command TEXT NOT NULL DEFAULT '',
                arguments TEXT NOT NULL DEFAULT '{}',
                expected_side_effects TEXT NOT NULL DEFAULT '[]',
                alternatives TEXT NOT NULL DEFAULT '[]',
                state TEXT NOT NULL DEFAULT 'pending',
                payload_digest TEXT NOT NULL,
                requested_at TEXT NOT NULL,
                decided_at TEXT,
                decided_by TEXT NOT NULL DEFAULT '',
                decision_note TEXT NOT NULL DEFAULT '',
                data TEXT NOT NULL,
                FOREIGN KEY (task_id) REFERENCES tasks(task_id)
            );
            CREATE INDEX IF NOT EXISTS idx_approvals_run_id ON approvals(run_id);
            CREATE INDEX IF NOT EXISTS idx_approvals_state ON approvals(state);
            """
        )
        conn.commit()

    def close(self) -> None:
        """Close the connection. The ledger can be re-opened later."""
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def update_task(self, task: TaskNode) -> TaskNode:
        """Persist a task's full state. The caller already holds the mutated model.

        This is the escape hatch for a caller that has legitimately mutated a
        task in memory (``evaluate``, ``mark_blocked``) and needs that mutation
        durable. Prefer the purpose-built methods below: they do the
        read-modify-write under the lock, so a caller cannot accidentally
        persist a stale copy it read before someone else's write.
        """
        conn = self._connect()
        with self._lock:
            existing = conn.execute("SELECT 1 FROM tasks WHERE task_id = ?", (str(task.task_id),)).fetchone()
            if existing is None:
                raise LedgerError(f"task {task.task_id} does not exist; create it first")
            self._write_task(conn, task)
            conn.commit()
        return task

    def _write_task(self, conn: sqlite3.Connection, task: TaskNode) -> None:
        conn.execute(
            """
            UPDATE tasks SET
                status = ?, attempt_count = ?, error_code = ?, failure_summary = ?,
                started_at = ?, finished_at = ?, data = ?
            WHERE task_id = ?
            """,
            (
                task.status.value,
                task.attempt_count,
                task.error_code,
                task.failure_summary,
                task.started_at,
                task.finished_at,
                json.dumps(task.to_dict(), ensure_ascii=False),
                str(task.task_id),
            ),
        )

    def begin_attempt(self, task_id: TaskId) -> tuple[TaskAttempt, TaskNode]:
        """Atomically open an attempt on a task and persist **both** writes.

        This exists because the obvious composition -- ``task.begin_attempt()``
        followed by ``transition_task(task_id, RUNNING)`` -- silently loses the
        attempt counter. ``transition_task`` re-reads the task from the store,
        so the increment performed on the in-memory object is discarded and the
        ledger records ``attempt_count=0`` on a task that is ``RUNNING``: a row
        claiming no attempt was made while one was in flight. Folding both
        writes into one locked read-modify-write is what makes the counter real.
        """
        conn = self._connect()
        with self._lock:
            task = self._require_task(conn, task_id)
            try:
                attempt = task.begin_attempt()
            except IllegalTransition as exc:
                raise LedgerConflict(str(exc)) from exc
            self._write_task(conn, task)
            conn.execute(
                """
                INSERT INTO attempts (attempt_id, task_id, goal_id, attempt_number, idempotency_key, outcome, worker, started_at, finished_at, failure_summary, error_code, data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt.attempt_id,
                    attempt.task_id,
                    attempt.goal_id,
                    attempt.attempt_number,
                    attempt.idempotency_key,
                    None,
                    attempt.worker,
                    attempt.started_at,
                    attempt.finished_at,
                    attempt.failure_summary,
                    attempt.error_code,
                    json.dumps(attempt.to_dict(), ensure_ascii=False),
                ),
            )
            conn.commit()
            return attempt, task

    def evaluate_acceptance(self, task_id: TaskId, check_name: str, verdict: bool, *, evidence: str, evaluated_by: str) -> str | None:
        """Record one measured acceptance verdict and persist it.

        Returns the task's ``refusal_reason()`` after the evaluation, or ``None``
        when every declared check now holds. Returning the reason rather than a
        bool keeps the single "is this verified?" question in one place
        (``AcceptanceReport.refusal_reason``) instead of restating it here.

        ``verdict`` must be a real ``bool`` -- ``AcceptanceCheck.evaluate``
        refuses a truthy string, because ``"false"`` is truthy in Python and a
        caller passing it would otherwise record a passing check.
        """
        conn = self._connect()
        with self._lock:
            task = self._require_task(conn, task_id)
            matched = [check for check in task.acceptance.criteria if check.check == check_name]
            if not matched:
                raise LedgerConflict(f"task {task_id} declares no acceptance check named {check_name!r}")
            for check in matched:
                check.evaluate(verdict, evidence=evidence, evaluated_by=evaluated_by)
            reason = task.acceptance.refusal_reason()
            self._write_task(conn, task)
            conn.commit()
            return reason

    def mark_task_failed(self, task_id: TaskId, *, reason: str, error_code: str | None = None) -> TaskNode:
        """Fail a task with a named reason and its code, in one locked write."""
        conn = self._connect()
        with self._lock:
            task = self._require_task(conn, task_id)
            task.failure_summary = _require_reason(reason)
            if error_code:
                task.error_code = error_code
            try:
                task.transition_to(TaskStatus.FAILED, reason=task.failure_summary)
            except IllegalTransition as exc:
                raise LedgerConflict(str(exc)) from exc
            self._write_task(conn, task)
            conn.commit()
            return task

    def mark_task_blocked(self, task_id: TaskId, *, reason: str) -> TaskNode:
        """Park a task with a recorded reason, in one locked write."""
        conn = self._connect()
        with self._lock:
            task = self._require_task(conn, task_id)
            reason_text = _require_reason(reason)
            try:
                task.mark_blocked(reason=reason_text)
            except IllegalTransition as exc:
                raise LedgerConflict(str(exc)) from exc
            self._write_task(conn, task)
            conn.commit()
            return task

    def park_task_for_approval(self, task_id: TaskId) -> TaskNode:
        """Move a task to ``WAITING_APPROVAL`` in one locked write.

        ``PENDING -> WAITING_APPROVAL`` is a legal transition precisely because
        approval is a **dispatch** decision, not a readiness one: the task has
        never been dispatched, so there is no attempt to pause.
        """
        conn = self._connect()
        with self._lock:
            task = self._require_task(conn, task_id)
            try:
                task.transition_to(TaskStatus.WAITING_APPROVAL)
            except IllegalTransition as exc:
                raise LedgerConflict(str(exc)) from exc
            self._write_task(conn, task)
            conn.commit()
            return task

    def mark_task_succeeded(self, task_id: TaskId, *, artifact_ids: Sequence[ArtifactId] | None = None) -> TaskNode:
        """Succeed a task, refusing when its acceptance is unmet.

        The acceptance gate is evaluated on the persisted row, so a caller
        cannot succeed a task whose measured verdicts were never written.
        """
        conn = self._connect()
        with self._lock:
            task = self._require_task(conn, task_id)
            try:
                task.mark_succeeded(artifact_ids=artifact_ids)
            except ModelValidationError as exc:
                raise LedgerConflict(str(exc)) from exc
            self._write_task(conn, task)
            conn.commit()
            return task

    def _require_task(self, conn: sqlite3.Connection, task_id: TaskId) -> TaskNode:
        row = conn.execute("SELECT data FROM tasks WHERE task_id = ?", (str(task_id),)).fetchone()
        if row is None:
            raise LedgerError(f"task {task_id} does not exist")
        return self._task_from_dict(json.loads(row["data"]))

    # -- goals -----------------------------------------------------------

    def create_goal(self, goal: Goal) -> Goal:
        """Persist a new goal. Refuses a duplicate goal_id."""
        conn = self._connect()
        with self._lock:
            try:
                conn.execute(
                    """
                    INSERT INTO goals (goal_id, run_id, mode, status, risk_level, plan_version, cancel_requested, deadline_at, created_at, updated_at, data)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        goal.goal_id,
                        goal.run_id,
                        goal.mode.value,
                        goal.status.value,
                        goal.risk_level.value,
                        goal.plan_version,
                        int(goal.cancel_requested),
                        goal.deadline_at,
                        goal.created_at,
                        goal.updated_at,
                        json.dumps(goal.to_dict(), ensure_ascii=False),
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                raise LedgerConflict(f"goal {goal.goal_id} already exists: {exc}") from exc
        return goal

    def get_goal(self, goal_id: GoalId) -> Goal | None:
        """Load a goal by id, or ``None`` if it does not exist."""
        conn = self._connect()
        row = conn.execute("SELECT data FROM goals WHERE goal_id = ?", (str(goal_id),)).fetchone()
        if row is None:
            return None
        return self._goal_from_dict(json.loads(row["data"]))

    def list_goals(self, *, run_id: str | None = None, status: GoalStatus | None = None) -> list[Goal]:
        """List goals, optionally filtered by run or status."""
        conn = self._connect()
        query = "SELECT data FROM goals WHERE 1=1"
        params: list[Any] = []
        if run_id is not None:
            query += " AND run_id = ?"
            params.append(run_id)
        if status is not None:
            query += " AND status = ?"
            params.append(status.value)
        query += " ORDER BY created_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [self._goal_from_dict(json.loads(row["data"])) for row in rows]

    def update_goal(self, goal: Goal) -> Goal:
        """Persist a goal update. Refuses if the goal does not exist."""
        conn = self._connect()
        with self._lock:
            existing = conn.execute("SELECT 1 FROM goals WHERE goal_id = ?", (str(goal.goal_id),)).fetchone()
            if existing is None:
                raise LedgerError(f"goal {goal.goal_id} does not exist; create it first")
            goal.updated_at = now_iso()
            conn.execute(
                """
                UPDATE goals SET
                    status = ?, risk_level = ?, plan_version = ?, cancel_requested = ?,
                    deadline_at = ?, updated_at = ?, data = ?
                WHERE goal_id = ?
                """,
                (
                    goal.status.value,
                    goal.risk_level.value,
                    goal.plan_version,
                    int(goal.cancel_requested),
                    goal.deadline_at,
                    goal.updated_at,
                    json.dumps(goal.to_dict(), ensure_ascii=False),
                    str(goal.goal_id),
                ),
            )
            conn.commit()
        return goal

    def _goal_from_dict(self, data: Mapping[str, Any]) -> Goal:
        return Goal(
            goal_id=GoalId(data["goal_id"]),
            run_id=data["run_id"],
            user_request=data["user_request"],
            normalized_objective=data["normalized_objective"],
            mode=GoalMode(data["mode"]),
            status=GoalStatus(data["status"]),
            acceptance_criteria=list(data.get("acceptance_criteria", [])),
            risk_level=RiskLevel(data.get("risk_level", "low")),
            budget=GraphBudget.from_mapping(data.get("budget", {})),
            policy_profile=data.get("policy_profile", "default"),
            plan_version=int(data.get("plan_version", 1)),
            task_ids=[TaskId(t) for t in data.get("task_ids", [])],
            artifact_ids=[ArtifactId(a) for a in data.get("artifact_ids", [])],
            claim_ids=[ClaimId(c) for c in data.get("claim_ids", [])],
            failure_summary=data.get("failure_summary"),
            final_result=data.get("final_result"),
            cancel_requested=bool(data.get("cancel_requested", False)),
            deadline_at=data.get("deadline_at"),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
        )

    # -- tasks -----------------------------------------------------------

    def create_task(self, task: TaskNode) -> TaskNode:
        """Persist a new task. Refuses a duplicate task_id."""
        conn = self._connect()
        with self._lock:
            try:
                conn.execute(
                    """
                    INSERT INTO tasks (task_id, goal_id, title, task_type, status, risk_level, assigned_worker, attempt_count, max_attempts, priority, error_code, failure_summary, created_at, started_at, finished_at, data)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task.task_id,
                        task.goal_id,
                        task.title,
                        task.task_type.value,
                        task.status.value,
                        task.risk_level.value,
                        task.assigned_worker,
                        task.attempt_count,
                        task.max_attempts,
                        task.priority,
                        task.error_code,
                        task.failure_summary,
                        task.created_at,
                        task.started_at,
                        task.finished_at,
                        json.dumps(task.to_dict(), ensure_ascii=False),
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                raise LedgerConflict(f"task {task.task_id} already exists: {exc}") from exc
        return task

    def get_task(self, task_id: TaskId) -> TaskNode | None:
        """Load a task by id, or ``None`` if it does not exist."""
        conn = self._connect()
        row = conn.execute("SELECT data FROM tasks WHERE task_id = ?", (str(task_id),)).fetchone()
        if row is None:
            return None
        return self._task_from_dict(json.loads(row["data"]))

    def list_tasks(self, *, goal_id: GoalId | None = None, status: TaskStatus | None = None) -> list[TaskNode]:
        """List tasks, optionally filtered by goal or status."""
        conn = self._connect()
        query = "SELECT data FROM tasks WHERE 1=1"
        params: list[Any] = []
        if goal_id is not None:
            query += " AND goal_id = ?"
            params.append(str(goal_id))
        if status is not None:
            query += " AND status = ?"
            params.append(status.value)
        query += " ORDER BY created_at ASC"
        rows = conn.execute(query, params).fetchall()
        return [self._task_from_dict(json.loads(row["data"])) for row in rows]

    def transition_task(self, task_id: TaskId, new_status: TaskStatus, *, reason: str = "", error_code: str | None = None) -> TaskNode:
        """Atomically transition a task's status.

        This is the **only** way to change a task's status in the ledger. The
        transition is validated against the closed table, the current status is
        re-read inside the lock, and the write is a single ``UPDATE``. A caller
        that tries to move a task to a status the table does not permit gets a
        ``LedgerConflict`` with the current status and the attempted target.
        """
        conn = self._connect()
        with self._lock:
            row = conn.execute("SELECT data FROM tasks WHERE task_id = ?", (str(task_id),)).fetchone()
            if row is None:
                raise LedgerError(f"task {task_id} does not exist")
            task = self._task_from_dict(json.loads(row["data"]))
            try:
                task.transition_to(new_status, reason=reason, error_code=error_code)
            except IllegalTransition as exc:
                raise LedgerConflict(str(exc)) from exc
            conn.execute(
                """
                UPDATE tasks SET
                    status = ?, attempt_count = ?, error_code = ?, failure_summary = ?,
                    started_at = ?, finished_at = ?, data = ?
                WHERE task_id = ?
                """,
                (
                    task.status.value,
                    task.attempt_count,
                    task.error_code,
                    task.failure_summary,
                    task.started_at,
                    task.finished_at,
                    json.dumps(task.to_dict(), ensure_ascii=False),
                    str(task_id),
                ),
            )
            conn.commit()
            return task

    def _task_from_dict(self, data: Mapping[str, Any]) -> TaskNode:
        """Rebuild a real ``TaskNode`` from its persisted dict.

        The acceptance report is reconstructed as a real
        :class:`~alpha.graph.models.AcceptanceReport` from the persisted
        criteria, **not** as an ad-hoc stand-in object. An earlier version
        built a throwaway type whose ``assert_passed`` / ``refusal_reason``
        were bare ``lambda`` class attributes, so calling them as methods
        passed ``self`` as a positional argument and raised ``TypeError`` for
        every task read back from the ledger. A model rebuilt from persistence
        must behave exactly like a model built in memory -- that equivalence is
        the whole point of round-tripping, and a stand-in object breaks it.
        """
        acceptance_data = data.get("acceptance", {})
        checks = [
            AcceptanceCheck(
                check=c["check"],
                verdict=CriterionVerdict(c.get("verdict", "unverified")),
                evidence=c.get("evidence", ""),
                evaluated_by=c.get("evaluated_by", ""),
                evaluated_at=c.get("evaluated_at"),
            )
            for c in acceptance_data.get("criteria", [])
        ]
        acceptance = AcceptanceReport(
            criteria=checks,
            evaluator=acceptance_data.get("evaluator", ""),
            generated_at=acceptance_data.get("generated_at", now_iso()),
        )
        capabilities = [
            CapabilityRequirement(
                capability=c["capability"],
                required=bool(c.get("required", True)),
                satisfied_by=c.get("satisfied_by", ""),
                resolved=bool(c.get("resolved", False)),
                reason=c.get("reason", ""),
            )
            for c in data.get("required_capabilities", [])
        ]
        return TaskNode(
            task_id=TaskId(data["task_id"]),
            goal_id=GoalId(data["goal_id"]),
            title=data["title"],
            description=data.get("description", ""),
            task_type=TaskType(data.get("task_type", "tool")),
            status=TaskStatus(data["status"]),
            depends_on=[TaskId(d) for d in data.get("depends_on", [])],
            assigned_worker=data.get("assigned_worker", ""),
            required_capabilities=capabilities,
            acceptance=acceptance,
            output_artifact_ids=[ArtifactId(a) for a in data.get("output_artifact_ids", [])],
            evidence_ids=[ClaimId(e) for e in data.get("evidence_ids", [])],
            risk_level=RiskLevel(data.get("risk_level", "low")),
            attempt_count=int(data.get("attempt_count", 0)),
            max_attempts=int(data.get("max_attempts", 2)),
            priority=int(data.get("priority", 50)),
            error_code=data.get("error_code"),
            failure_summary=data.get("failure_summary"),
            created_at=data["created_at"],
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
        )

    # -- edges -----------------------------------------------------------

    def create_edge(self, edge: GraphEdge) -> GraphEdge:
        """Persist a new edge. Refuses a duplicate edge_id."""
        conn = self._connect()
        with self._lock:
            try:
                conn.execute(
                    """
                    INSERT INTO edges (edge_id, graph_id, source_id, target_id, edge_type, reason, source_artifact_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        edge.edge_id,
                        edge.graph_id,
                        edge.source_id,
                        edge.target_id,
                        edge.edge_type.value,
                        edge.reason,
                        edge.source_artifact_id,
                        edge.created_at,
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                raise LedgerConflict(f"edge {edge.edge_id} already exists: {exc}") from exc
        return edge

    def list_edges(self, *, graph_id: GraphId | None = None) -> list[GraphEdge]:
        """List edges, optionally filtered by graph."""
        conn = self._connect()
        query = "SELECT * FROM edges WHERE 1=1"
        params: list[Any] = []
        if graph_id is not None:
            query += " AND graph_id = ?"
            params.append(str(graph_id))
        query += " ORDER BY created_at ASC"
        rows = conn.execute(query, params).fetchall()
        return [
            GraphEdge(
                edge_id=row["edge_id"],
                graph_id=GraphId(row["graph_id"]),
                source_id=row["source_id"],
                target_id=row["target_id"],
                edge_type=EdgeType(row["edge_type"]),
                reason=row["reason"],
                source_artifact_id=row["source_artifact_id"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    # -- attempts --------------------------------------------------------

    def create_attempt(self, attempt: TaskAttempt) -> TaskAttempt:
        """Persist a new attempt. Refuses a duplicate idempotency_key."""
        conn = self._connect()
        with self._lock:
            try:
                conn.execute(
                    """
                    INSERT INTO attempts (attempt_id, task_id, goal_id, attempt_number, idempotency_key, outcome, worker, started_at, finished_at, failure_summary, error_code, data)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        attempt.attempt_id,
                        attempt.task_id,
                        attempt.goal_id,
                        attempt.attempt_number,
                        attempt.idempotency_key,
                        attempt.outcome.value if attempt.outcome else None,
                        attempt.worker,
                        attempt.started_at,
                        attempt.finished_at,
                        attempt.failure_summary,
                        attempt.error_code,
                        json.dumps(attempt.to_dict(), ensure_ascii=False),
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                raise LedgerConflict(f"attempt {attempt.attempt_id} or idempotency_key {attempt.idempotency_key} already exists: {exc}") from exc
        return attempt

    def get_attempt(self, attempt_id: AttemptId) -> TaskAttempt | None:
        """Load an attempt by id, or ``None`` if it does not exist."""
        conn = self._connect()
        row = conn.execute("SELECT data FROM attempts WHERE attempt_id = ?", (str(attempt_id),)).fetchone()
        if row is None:
            return None
        return self._attempt_from_dict(json.loads(row["data"]))

    def list_attempts(self, *, task_id: TaskId | None = None) -> list[TaskAttempt]:
        """List attempts, optionally filtered by task."""
        conn = self._connect()
        query = "SELECT data FROM attempts WHERE 1=1"
        params: list[Any] = []
        if task_id is not None:
            query += " AND task_id = ?"
            params.append(str(task_id))
        query += " ORDER BY attempt_number ASC"
        rows = conn.execute(query, params).fetchall()
        return [self._attempt_from_dict(json.loads(row["data"])) for row in rows]

    def _attempt_from_dict(self, data: Mapping[str, Any]) -> TaskAttempt:
        return TaskAttempt(
            attempt_id=AttemptId(data["attempt_id"]),
            task_id=TaskId(data["task_id"]),
            goal_id=GoalId(data["goal_id"]),
            attempt_number=int(data["attempt_number"]),
            idempotency_key=data["idempotency_key"],
            outcome=AttemptOutcome(data["outcome"]) if data.get("outcome") else None,
            worker=data.get("worker", ""),
            started_at=data["started_at"],
            finished_at=data.get("finished_at"),
            artifact_ids=[ArtifactId(a) for a in data.get("artifact_ids", [])],
            failure_summary=data.get("failure_summary"),
            error_code=data.get("error_code"),
        )

    # -- artifacts -------------------------------------------------------

    def create_artifact(self, artifact: Artifact) -> Artifact:
        """Persist a new artifact. Refuses a duplicate artifact_id."""
        conn = self._connect()
        with self._lock:
            try:
                conn.execute(
                    """
                    INSERT INTO artifacts (artifact_id, run_id, goal_id, task_id, artifact_type, uri, content_hash, mime_type, size_bytes, created_by_node, sensitivity, created_at, data)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        artifact.artifact_id,
                        artifact.run_id,
                        artifact.goal_id,
                        artifact.task_id,
                        artifact.artifact_type,
                        artifact.uri,
                        artifact.content_hash,
                        artifact.mime_type,
                        artifact.size_bytes,
                        artifact.created_by_node,
                        artifact.sensitivity.value,
                        artifact.created_at,
                        json.dumps(artifact.to_dict(), ensure_ascii=False),
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                raise LedgerConflict(f"artifact {artifact.artifact_id} already exists: {exc}") from exc
        return artifact

    def get_artifact(self, artifact_id: ArtifactId) -> Artifact | None:
        """Load an artifact by id, or ``None`` if it does not exist."""
        conn = self._connect()
        row = conn.execute("SELECT data FROM artifacts WHERE artifact_id = ?", (str(artifact_id),)).fetchone()
        if row is None:
            return None
        return self._artifact_from_dict(json.loads(row["data"]))

    def list_artifacts(self, *, run_id: str | None = None, goal_id: GoalId | None = None, task_id: TaskId | None = None) -> list[Artifact]:
        """List artifacts, optionally filtered by run, goal, or task."""
        conn = self._connect()
        query = "SELECT data FROM artifacts WHERE 1=1"
        params: list[Any] = []
        if run_id is not None:
            query += " AND run_id = ?"
            params.append(run_id)
        if goal_id is not None:
            query += " AND goal_id = ?"
            params.append(str(goal_id))
        if task_id is not None:
            query += " AND task_id = ?"
            params.append(str(task_id))
        query += " ORDER BY created_at ASC"
        rows = conn.execute(query, params).fetchall()
        return [self._artifact_from_dict(json.loads(row["data"])) for row in rows]

    def _artifact_from_dict(self, data: Mapping[str, Any]) -> Artifact:
        return Artifact(
            artifact_id=ArtifactId(data["artifact_id"]),
            run_id=data["run_id"],
            artifact_type=data["artifact_type"],
            uri=data["uri"],
            content_hash=data["content_hash"],
            mime_type=data.get("mime_type", "application/octet-stream"),
            size_bytes=data.get("size_bytes"),
            goal_id=GoalId(data["goal_id"]) if data.get("goal_id") else None,
            task_id=TaskId(data["task_id"]) if data.get("task_id") else None,
            created_by_node=data.get("created_by_node", ""),
            sensitivity=ArtifactSensitivity(data.get("sensitivity", "internal")),
            created_at=data["created_at"],
            metadata=dict(data.get("metadata", {})),
        )

    # -- claims -----------------------------------------------------------

    def create_claim(self, claim: Any) -> Any:
        """Persist a new claim. Refuses a duplicate claim_id."""
        conn = self._connect()
        with self._lock:
            try:
                conn.execute(
                    """
                    INSERT INTO claims (
                        claim_id, subject_entity_id, predicate, object_entity_id, claim_text, status,
                        source_artifact_id, source_locator, confidence, valid_from, valid_to, observed_at,
                        recorded_at, created_by, verified_by, failure_summary, contradicts, data
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        claim.claim_id,
                        claim.subject_entity_id,
                        claim.predicate,
                        claim.object_entity_id,
                        claim.claim_text,
                        claim.status.value,
                        claim.source_artifact_id,
                        claim.source_locator,
                        claim.confidence,
                        claim.valid_from,
                        claim.valid_to,
                        claim.observed_at,
                        claim.recorded_at,
                        claim.created_by,
                        claim.verified_by,
                        claim.failure_summary,
                        json.dumps([str(c) for c in claim.contradicts]),
                        json.dumps(claim.to_dict(), ensure_ascii=False),
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                raise LedgerConflict(f"claim {claim.claim_id} already exists: {exc}") from exc
        return claim

    def get_claim(self, claim_id: ClaimId) -> Any | None:
        """Load a claim by id, or ``None`` if it does not exist."""
        conn = self._connect()
        row = conn.execute("SELECT data FROM claims WHERE claim_id = ?", (str(claim_id),)).fetchone()
        if row is None:
            return None
        return self._claim_from_dict(json.loads(row["data"]))

    def list_claims(self, *, subject_entity_id: str | None = None, status: ClaimStatus | None = None, predicate: str | None = None, limit: int | None = None) -> list[Any]:
        """List claims, optionally filtered by subject, status, or predicate.

        ``limit`` is a row count, not a slice bound: ``LIMIT 0`` means zero
        rows, and ``LIMIT -1`` is not expressible in SQL at all. Callers wanting
        "the most recent N" get exactly N rows back, never the whole table.
        """
        conn = self._connect()
        query = "SELECT data FROM claims WHERE 1=1"
        params: list[Any] = []
        if subject_entity_id is not None:
            query += " AND subject_entity_id = ?"
            params.append(subject_entity_id)
        if status is not None:
            query += " AND status = ?"
            params.append(status.value)
        if predicate is not None:
            query += " AND predicate = ?"
            params.append(predicate)
        if limit is not None:
            if limit < 0:
                raise LedgerError(f"limit must be >= 0, got {limit}")
        query += " ORDER BY recorded_at DESC"
        if limit is not None:
            # LIMIT comes *after* ORDER BY. SQLite parses the clauses in a fixed
            # order, so appending LIMIT first yields "... WHERE 1=1 LIMIT ? ORDER
            # BY recorded_at" -- a syntax error that names a line, not a clause.
            query += " LIMIT ?"
            params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [self._claim_from_dict(json.loads(row["data"])) for row in rows]

    def _claim_from_dict(self, data: Mapping[str, Any]) -> Any:
        from alpha.graph.models import EvidenceClaim

        return EvidenceClaim(
            claim_id=ClaimId(data["claim_id"]),
            subject_entity_id=data["subject_entity_id"],
            predicate=data["predicate"],
            object_entity_id=data.get("object_entity_id"),
            claim_text=data["claim_text"],
            status=ClaimStatus(data.get("status", "candidate")),
            source_artifact_id=ArtifactId(data["source_artifact_id"]) if data.get("source_artifact_id") else None,
            source_locator=data.get("source_locator", ""),
            confidence=data.get("confidence"),
            valid_from=data.get("valid_from"),
            valid_to=data.get("valid_to"),
            observed_at=data.get("observed_at"),
            recorded_at=data["recorded_at"],
            created_by=data.get("created_by", ""),
            verified_by=data.get("verified_by", ""),
            failure_summary=data.get("failure_summary"),
            contradicts=[ClaimId(c) for c in data.get("contradicts", [])],
        )

    # -- events -----------------------------------------------------------

    def append_event(self, event: RunEvent) -> RunEvent:
        """Append an event to the store. Refuses a duplicate event_id."""
        conn = self._connect()
        with self._lock:
            try:
                conn.execute(
                    """
                    INSERT INTO events (
                        event_id, run_id, goal_id, task_id, attempt_id, parent_event_id, trace_id, span_id,
                        event_type, severity, status, component, operation, attempt, duration_ms, error_code,
                        message_safe, attributes, artifact_ids, timestamp, is_completion
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        event.run_id,
                        event.goal_id,
                        event.task_id,
                        event.attempt_id,
                        event.parent_event_id,
                        event.trace_id,
                        event.span_id,
                        event.event_type.value,
                        event.severity.value,
                        event.status,
                        event.component,
                        event.operation,
                        event.attempt,
                        event.duration_ms,
                        event.error_code,
                        event.message_safe,
                        json.dumps(event.attributes, ensure_ascii=False),
                        json.dumps([str(a) for a in event.artifact_ids]),
                        event.timestamp,
                        int(event.is_completion),
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                raise LedgerConflict(f"event {event.event_id} already exists: {exc}") from exc
        return event

    def list_events(self, *, run_id: str | None = None, goal_id: GoalId | None = None, task_id: TaskId | None = None, event_type: EventType | None = None, limit: int | None = None) -> list[RunEvent]:
        """List events, optionally filtered. Ordered by timestamp descending."""
        conn = self._connect()
        query = "SELECT * FROM events WHERE 1=1"
        params: list[Any] = []
        if run_id is not None:
            query += " AND run_id = ?"
            params.append(run_id)
        if goal_id is not None:
            query += " AND goal_id = ?"
            params.append(str(goal_id))
        if task_id is not None:
            query += " AND task_id = ?"
            params.append(str(task_id))
        if event_type is not None:
            query += " AND event_type = ?"
            params.append(event_type.value)
        query += " ORDER BY timestamp DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [
            RunEvent(
                event_id=EventId(row["event_id"]),
                run_id=row["run_id"],
                event_type=EventType(row["event_type"]),
                severity=EventSeverity(row["severity"]),
                status=row["status"],
                goal_id=GoalId(row["goal_id"]) if row["goal_id"] else None,
                task_id=TaskId(row["task_id"]) if row["task_id"] else None,
                attempt_id=AttemptId(row["attempt_id"]) if row["attempt_id"] else None,
                parent_event_id=EventId(row["parent_event_id"]) if row["parent_event_id"] else None,
                trace_id=row["trace_id"],
                span_id=row["span_id"],
                component=row["component"],
                operation=row["operation"],
                attempt=row["attempt"],
                duration_ms=row["duration_ms"],
                error_code=row["error_code"],
                message_safe=row["message_safe"],
                attributes=json.loads(row["attributes"]),
                artifact_ids=[ArtifactId(a) for a in json.loads(row["artifact_ids"])],
                timestamp=row["timestamp"],
            )
            for row in rows
        ]

    # -- approvals --------------------------------------------------------

    def create_approval(self, approval: ApprovalRequest) -> ApprovalRequest:
        """Persist a new approval request. Refuses a duplicate approval_id."""
        conn = self._connect()
        with self._lock:
            try:
                conn.execute(
                    """
                    INSERT INTO approvals (approval_id, run_id, task_id, operation, target, command, arguments, expected_side_effects, alternatives, state, payload_digest, requested_at, decided_at, decided_by, decision_note, data)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        approval.approval_id,
                        approval.run_id,
                        approval.task_id,
                        approval.operation,
                        approval.target,
                        approval.command,
                        json.dumps(approval.arguments, ensure_ascii=False),
                        json.dumps(approval.expected_side_effects),
                        json.dumps(approval.alternatives),
                        approval.state.value,
                        approval.payload_digest,
                        approval.requested_at,
                        approval.decided_at,
                        approval.decided_by,
                        approval.decision_note,
                        json.dumps(approval.to_dict(), ensure_ascii=False),
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                raise LedgerConflict(f"approval {approval.approval_id} already exists: {exc}") from exc
        return approval

    def get_approval(self, approval_id: ApprovalId) -> ApprovalRequest | None:
        """Load an approval by id, or ``None`` if it does not exist."""
        conn = self._connect()
        row = conn.execute("SELECT data FROM approvals WHERE approval_id = ?", (str(approval_id),)).fetchone()
        if row is None:
            return None
        return self._approval_from_dict(json.loads(row["data"]))

    def list_approvals(self, *, run_id: str | None = None, state: ApprovalState | None = None) -> list[ApprovalRequest]:
        """List approvals, optionally filtered by run or state."""
        conn = self._connect()
        query = "SELECT data FROM approvals WHERE 1=1"
        params: list[Any] = []
        if run_id is not None:
            query += " AND run_id = ?"
            params.append(run_id)
        if state is not None:
            query += " AND state = ?"
            params.append(state.value)
        query += " ORDER BY requested_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [self._approval_from_dict(json.loads(row["data"])) for row in rows]

    def _approval_from_dict(self, data: Mapping[str, Any]) -> ApprovalRequest:
        return ApprovalRequest(
            approval_id=ApprovalId(data["approval_id"]),
            run_id=data["run_id"],
            task_id=TaskId(data["task_id"]) if data.get("task_id") else None,
            operation=data["operation"],
            target=data["target"],
            command=data.get("command", ""),
            arguments=dict(data.get("arguments", {})),
            expected_side_effects=list(data.get("expected_side_effects", [])),
            alternatives=list(data.get("alternatives", [])),
            state=ApprovalState(data.get("state", "pending")),
            payload_digest=data["payload_digest"],
            requested_at=data["requested_at"],
            decided_at=data.get("decided_at"),
            decided_by=data.get("decided_by", ""),
            decision_note=data.get("decision_note", ""),
        )
