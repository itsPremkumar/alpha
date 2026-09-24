"""Typed, versioned serialization contracts for persisted DWE state (P1 / W-N1).

Everything the durable layer writes to disk crosses one of these contracts:

- :class:`PersistedEventRecord` — one append-only event-log line
  (``workflow_store/events/{run_id}.jsonl``): the ``WorkflowEvent`` fields
  plus the writer-assigned monotonic ``seq`` and the DOC-A §13.3
  ``idempotency_key`` when one is applicable.
- :class:`PersistedRunSnapshot` — the materialized per-run projection
  (``workflow_store/runs/{run_id}.json``): the journaled ``WorkflowRun``
  (status, node states, completed/failed/waiting nodes, history, iteration
  counts, metrics), the workflow definition, every graph revision of that
  workflow (node evidence/output/status travel with the graphs), and the
  provenance of the event the projection was taken at.
- :class:`HydrationReport` — exactly what a from-disk hydration did: every
  run it installed and every corrupt / stale / missing piece it refused to
  pretend about.

Versioning rules (additive only):

- Every persisted record carries ``schema_version`` (current value:
  :data:`SCHEMA_VERSION`). Readers accept ``schema_version`` <= the current
  one and treat a MISSING ``schema_version`` as ``1`` (the model default, so
  hand-written or legacy v1 lines still parse); anything newer raises an
  honest unsupported-schema error instead of guessing at fields a future
  writer intended.
- Unknown extra keys on read are ignored (``extra="ignore"``): a future
  ADDITIVE field degrades to its default rather than crashing an old reader.
- This wave introduces NO ``config_version`` changes anywhere: these are
  persisted-data contracts, not configuration.

``PlanVersion`` (graph-revision records plus their optimistic-concurrency
store) lives in ``alpha.workflow.plan_graph`` beside the OCC logic it serves;
it shares :data:`SCHEMA_VERSION` from this module.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from alpha.workflow.models import WorkflowDefinition, WorkflowGraph, WorkflowRun

# Current persisted-contract version. Bump ONLY for incompatible changes and
# record the migration with the reader that learns it; additive fields must
# never bump this.
SCHEMA_VERSION = 1


class PersistedEventRecord(BaseModel):
    """One durable event-log line, validated on every read."""

    model_config = ConfigDict(extra="ignore")

    schema_version: int = Field(default=SCHEMA_VERSION)
    seq: int = Field(..., ge=1, description="Monotonic per-run sequence number (1..N).")
    event_id: str
    workflow_run_id: str
    event_type: str
    timestamp: str
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(
        default=None,
        description=(
            "DOC-A §13.3 {run}:{graph_version}:{node}:{attempt}:{input_hash} key for node "
            "attempt events; None when not applicable or a component is unavailable (never guessed)."
        ),
    )
    recorded_at: str = Field(description="UTC ISO timestamp assigned by the writer at append time.")


class PersistedRunSnapshot(BaseModel):
    """Materialized per-run projection: enough to hydrate a fresh engine."""

    model_config = ConfigDict(extra="ignore")

    schema_version: int = Field(default=SCHEMA_VERSION)
    run: WorkflowRun
    definition: WorkflowDefinition | None = Field(
        default=None,
        description="Workflow definition as known when the projection was taken (None only if it was absent).",
    )
    graphs: dict[str, WorkflowGraph] = Field(
        default_factory=dict,
        description='All graph revisions of the run\'s workflow, keyed "{workflow_id}:v{version}".',
    )
    last_seq: int = Field(default=0, description="Durable event seq this projection includes (writer-assigned).")
    event_count: int = Field(default=0, description="Events included when this projection was written.")
    source_event: dict[str, str] = Field(
        default_factory=dict,
        description="Provenance: the event_id/event_type/timestamp the projection was taken at.",
    )


class HydrationReport(BaseModel):
    """Honest outcome of hydrating an engine from persisted projections.

    ``status`` is never ``ok`` when anything was corrupt, stale, or missing:
    those runs are named here and were NOT installed — abstaining beats
    fabricating state or re-executing committed work.
    """

    model_config = ConfigDict(extra="ignore")

    schema_version: int = Field(default=SCHEMA_VERSION)
    status: Literal["empty", "ok", "degraded"]
    store_dir: str
    hydrated_runs: list[str] = Field(default_factory=list)
    skipped_existing: list[str] = Field(default_factory=list)
    corrupt_runs: list[dict[str, str]] = Field(
        default_factory=list,
        description='{"run_id", "error"} for projections or logs that failed validation — not installed.',
    )
    stale_projections: list[dict[str, str]] = Field(
        default_factory=list,
        description='{"run_id", "detail"} where durable events prove the projection is behind — not installed.',
    )
    missing_graphs: list[str] = Field(
        default_factory=list,
        description="Run ids whose graph_version has no graph in the projection (continuation may refuse honestly).",
    )
    missing_definitions: list[str] = Field(
        default_factory=list,
        description="Run ids whose projection carried no workflow definition.",
    )
    disclosures: list[str] = Field(default_factory=list)
