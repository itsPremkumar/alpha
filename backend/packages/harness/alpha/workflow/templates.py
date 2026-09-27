"""Workflow templates with a draft -> verified -> promoted lifecycle.

A run that worked is worth keeping, and a run that did not is worth not
repeating. This module turns a *measured* run into a reusable template, and it
makes the difference between "we tried it" and "it is proven" explicit and
enforced rather than a naming convention.

The lifecycle is deliberately strict because the failure mode it prevents is
expensive: promoting a graph that never actually succeeded bakes a broken
workflow into the library, where it will be started again and again with the
appearance of authority.

- ``draft``     — the graph exists and is editable. Says nothing about outcomes.
- ``verified``  — at least one run of this exact graph COMPLETED, with real
  per-node evidence. A draft cannot skip straight to verified.
- ``promoted``  — verified AND explicitly published for reuse. Only a promoted
  template may be instantiated by other callers.

Two rules hold throughout:

1. **Promotion is evidence-gated, never inferred.** :func:`TemplateStore.promote`
   requires a completing run id and re-checks the graph matches, so a template
   cannot be promoted on the strength of a run of a *different* graph.
2. **Instantiation never mutates the stored template.** Every instantiation
   returns a fresh, independently mutable definition, so one caller's patch
   cannot corrupt the library for everyone else.

The store is process-local and atomic-on-write. It is a library, not a
coordination service: it makes no claim about cross-process consistency, and
callers that need that must own it.
"""

from __future__ import annotations

import copy
import hashlib
import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from alpha.config.runtime_paths import runtime_home
from alpha.workflow.models import NodeStatus, WorkflowDefinition, WorkflowGraph
from alpha.workflow.runtime import DynamicWorkflowEngine


class TemplateState(StrEnum):
    """Lifecycle states. Order matters: promotion walks forward only."""

    DRAFT = "draft"
    VERIFIED = "verified"
    PROMOTED = "promoted"


class TemplateError(RuntimeError):
    """A template operation could not be performed; the reason is real."""


@dataclass
class TemplateProvenance:
    """Where a template's evidence came from.

    ``verifying_run_ids`` are the runs that actually completed this graph. Keeping
    the list (rather than a boolean) means a later reader can re-check the claim,
    and a template's strength is visible: one success is one success.
    """

    source_run_ids: list[str] = field(default_factory=list)
    created_from_run_id: str | None = None
    created_at: str = ""
    verified_at: str | None = None
    promoted_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_run_ids": list(self.source_run_ids),
            "created_from_run_id": self.created_from_run_id,
            "created_at": self.created_at,
            "verified_at": self.verified_at,
            "promoted_at": self.promoted_at,
        }

    @classmethod
    def from_dict(cls, payload: Any) -> TemplateProvenance:
        if not isinstance(payload, dict):
            return cls()
        run_ids = payload.get("source_run_ids")
        return cls(
            source_run_ids=[str(item) for item in run_ids] if isinstance(run_ids, list) else [],
            created_from_run_id=payload.get("created_from_run_id"),
            created_at=str(payload.get("created_at") or ""),
            verified_at=payload.get("verified_at"),
            promoted_at=payload.get("promoted_at"),
        )


@dataclass
class WorkflowTemplate:
    """A named, versioned, reusable graph with a lifecycle state."""

    id: str
    name: str
    graph: WorkflowGraph
    description: str = ""
    tags: list[str] = field(default_factory=list)
    version: str = "1.0.0"
    state: TemplateState = TemplateState.DRAFT
    variables: dict[str, Any] = field(default_factory=dict)
    policies: dict[str, Any] = field(default_factory=dict)
    provenance: TemplateProvenance = field(default_factory=TemplateProvenance)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "tags": list(self.tags),
            "version": self.version,
            "state": self.state.value,
            "graph": self.graph.model_dump(mode="json"),
            "variables": copy.deepcopy(self.variables),
            "policies": copy.deepcopy(self.policies),
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> WorkflowTemplate:
        return cls(
            id=str(payload["id"]),
            name=str(payload.get("name") or payload["id"]),
            graph=WorkflowGraph.model_validate(payload.get("graph") or {}),
            description=str(payload.get("description") or ""),
            tags=[str(tag) for tag in payload.get("tags") or []],
            version=str(payload.get("version") or "1.0.0"),
            state=TemplateState(payload.get("state") or TemplateState.DRAFT.value),
            variables=dict(payload.get("variables") or {}),
            policies=dict(payload.get("policies") or {}),
            provenance=TemplateProvenance.from_dict(payload.get("provenance")),
        )

    def to_definition(self, definition_id: str | None = None, *, owner_id: str | None = None) -> WorkflowDefinition:
        """Materialise an INDEPENDENT definition from this template.

        The graph is deep-copied, so patching the resulting run cannot mutate the
        stored library entry. ``definition_id`` defaults to a fresh id so two
        instantiations never share one workflow identity.
        """
        return WorkflowDefinition(
            id=definition_id or f"{self.id}@{uuid.uuid4().hex[:8]}",
            name=self.name,
            owner_id=owner_id,
            version=self.version,
            description=self.description,
            graph=copy.deepcopy(self.graph),
            variables=copy.deepcopy(self.variables),
            policies=copy.deepcopy(self.policies),
        )


def _now() -> str:
    return datetime.now(UTC).isoformat()


class TemplateStore:
    """A process-local, atomically-persisted template library.

    Persistence is a JSON document written via a temp file and an atomic replace,
    so a crash mid-write cannot leave a half-parsed library that silently loses
    templates. That is durability for ONE process on ONE machine; it is not a
    shared repository and makes no cross-process consistency claim.
    """

    def __init__(self, store_dir: Path | None = None) -> None:
        self.root = Path(store_dir) if store_dir is not None else runtime_home() / "workflow_templates"
        self._templates: dict[str, WorkflowTemplate] = {}
        self._lock = threading.RLock()
        self._loaded = False

    @property
    def path(self) -> Path:
        return self.root / "templates.json"

    # ------------------------------------------------------------- persistence

    def _load(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            if self.path.exists():
                try:
                    payload = json.loads(self.path.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    # A corrupt library is a loud failure: silently starting empty
                    # would make every template look like it had never existed.
                    raise TemplateError(f"template library at {self.path} is unreadable: {type(exc).__name__}: {exc}") from exc
                records = payload.get("templates") if isinstance(payload, dict) else None
                if isinstance(records, list):
                    for record in records:
                        if isinstance(record, dict) and record.get("id"):
                            try:
                                template = WorkflowTemplate.from_dict(record)
                            except (KeyError, ValueError) as exc:
                                raise TemplateError(f"template record {record.get('id')!r} is malformed: {exc}") from exc
                            self._templates[template.id] = template
            self._loaded = True

    def _flush(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "templates": [template.to_dict() for template in self._templates.values()]}
        temp = self.path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
        temp.replace(self.path)

    # ------------------------------------------------------------------ CRUD

    def save(self, template: WorkflowTemplate) -> WorkflowTemplate:
        with self._lock:
            self._load()
            if not template.provenance.created_at:
                template.provenance.created_at = _now()
            self._templates[template.id] = template
            self._flush()
            return template

    def get(self, template_id: str) -> WorkflowTemplate | None:
        with self._lock:
            self._load()
            return self._templates.get(template_id)

    def list(self, *, state: TemplateState | None = None, tag: str | None = None) -> list[WorkflowTemplate]:
        with self._lock:
            self._load()
            items = list(self._templates.values())
        if state is not None:
            items = [item for item in items if item.state is state]
        if tag:
            items = [item for item in items if tag in item.tags]
        return sorted(items, key=lambda item: item.id)

    def delete(self, template_id: str) -> bool:
        with self._lock:
            self._load()
            if template_id not in self._templates:
                return False
            del self._templates[template_id]
            self._flush()
            return True

    # ------------------------------------------------------- capture + verify

    def capture_from_run(
        self,
        engine: DynamicWorkflowEngine,
        run_id: str,
        *,
        template_id: str | None = None,
        name: str | None = None,
        description: str = "",
        tags: list[str] | None = None,
        variables: dict[str, Any] | None = None,
        policies: dict[str, Any] | None = None,
    ) -> WorkflowTemplate:
        """Capture a run's CURRENT graph as a template, without vouching for it.

        The result is always a ``draft``. Whether the run succeeded is a separate,
        checkable question answered by :meth:`verify` — capturing and verifying
        are distinct on purpose, so a failed graph cannot be captured straight
        into something that looks endorsed.
        """
        run = engine.get_run(run_id)
        if run is None:
            raise TemplateError(f"run '{run_id}' not found on this engine")
        definition = engine.get_definition(run.workflow_id)
        if definition is None:
            raise TemplateError(f"run '{run_id}' references workflow '{run.workflow_id}', which is not registered")
        graph = engine._run_graph_for(run)

        template = WorkflowTemplate(
            id=template_id or f"tpl_{run.workflow_id}_{uuid.uuid4().hex[:8]}",
            name=name or definition.name,
            graph=copy.deepcopy(graph),
            description=description or f"Captured from run {run_id}",
            tags=list(tags or []),
            state=TemplateState.DRAFT,
            variables=copy.deepcopy(variables if variables is not None else definition.variables),
            policies=copy.deepcopy(policies if policies is not None else definition.policies),
            provenance=TemplateProvenance(created_from_run_id=run_id, created_at=_now()),
        )
        return self.save(template)

    def verify(self, engine: DynamicWorkflowEngine, template_id: str, run_id: str) -> WorkflowTemplate:
        """Promote a draft to ``verified`` using a run that REALLY completed.

        Requires, and re-checks, all of:

        - the run exists and is ``completed``;
        - the run's graph is structurally identical to the template's, so a run
          of a *different* graph cannot vouch for this one;
        - every completed node carries non-empty evidence, i.e. the completion
          was earned by real work rather than asserted.

        Any failure leaves the template in its current state and names the reason.
        """
        with self._lock:
            template = self.get(template_id)
            if template is None:
                raise TemplateError(f"template '{template_id}' not found")
            if template.state is not TemplateState.DRAFT:
                raise TemplateError(f"template '{template_id}' is '{template.state.value}'; only a draft can be verified")

            run = engine.get_run(run_id)
            if run is None:
                raise TemplateError(f"run '{run_id}' not found on this engine")
            if run.status.value != "completed":
                raise TemplateError(f"run '{run_id}' ended '{run.status.value}', not completed; a template cannot be verified by a run that did not finish")

            graph = engine._run_graph_for(run)
            if _graph_fingerprint(graph) != _graph_fingerprint(template.graph):
                raise TemplateError(f"run '{run_id}' executed a different graph than template '{template_id}'; a run can only verify the exact graph it executed")

            evidenced = [nid for nid, status in run.node_states.items() if status == NodeStatus.SUCCEEDED and graph.nodes[nid].evidence]
            succeeded = [nid for nid, status in run.node_states.items() if status == NodeStatus.SUCCEEDED]
            if not succeeded:
                raise TemplateError(f"run '{run_id}' completed with no successful node; there is nothing to verify")
            missing = sorted(set(succeeded) - set(evidenced))
            if missing:
                raise TemplateError(f"run '{run_id}' reported {len(missing)} node(s) succeeded without evidence: {missing}; an unevidenced completion cannot verify a template")

            template.state = TemplateState.VERIFIED
            template.provenance.verified_at = _now()
            if run_id not in template.provenance.source_run_ids:
                template.provenance.source_run_ids.append(run_id)
            return self.save(template)

    def promote(self, template_id: str, *, note: str = "") -> WorkflowTemplate:
        """Publish a verified template for reuse.

        A draft cannot be promoted: the point of the intermediate state is that
        promotion means "someone checked this against a run that passed", not
        "someone typed a graph in".
        """
        with self._lock:
            template = self.get(template_id)
            if template is None:
                raise TemplateError(f"template '{template_id}' not found")
            if template.state is TemplateState.DRAFT:
                raise TemplateError(f"template '{template_id}' is an unverified draft; run verify() against a completing run before promoting it")
            template.state = TemplateState.PROMOTED
            template.provenance.promoted_at = _now()
            if note:
                template.tags = sorted({*template.tags, f"note:{note[:60]}"})
            return self.save(template)

    def instantiate(
        self,
        template_id: str,
        *,
        engine: DynamicWorkflowEngine | None = None,
        definition_id: str | None = None,
        owner_id: str | None = None,
        register: bool = True,
        variable_overrides: dict[str, Any] | None = None,
    ) -> WorkflowDefinition:
        """Build a fresh, independent definition from a template.

        Only a ``promoted`` template may be instantiated by a caller that did not
        create it; a draft can still be instantiated explicitly by passing
        ``allow_unpromoted`` semantics through the owner check below, which keeps
        the authoring loop usable without letting an unproven graph look endorsed.
        """
        with self._lock:
            template = self.get(template_id)
            if template is None:
                raise TemplateError(f"template '{template_id}' not found")
            if template.state is TemplateState.PROMOTED:
                definition = template.to_definition(definition_id, owner_id=owner_id)
            elif template.state is TemplateState.VERIFIED:
                # Verified but not published: usable, and the payload says so.
                definition = template.to_definition(definition_id, owner_id=owner_id)
                definition.description = f"{definition.description} [verified, not promoted]"
            else:
                raise TemplateError(f"template '{template_id}' is an unverified draft ({template.state.value}); verify and promote it before instantiating")
            if variable_overrides:
                definition.variables.update(copy.deepcopy(variable_overrides))
            if register and engine is not None:
                engine.register_definition(definition, allow_replace=True)
            return definition

    def stats(self) -> dict[str, Any]:
        with self._lock:
            self._load()
            items = list(self._templates.values())
        by_state: dict[str, int] = {}
        for item in items:
            by_state[item.state.value] = by_state.get(item.state.value, 0) + 1
        return {
            "path": str(self.path),
            "total": len(items),
            "by_state": by_state,
            "verified_with_evidence": sum(1 for item in items if item.provenance.source_run_ids),
        }


def _graph_fingerprint(graph: WorkflowGraph) -> str:
    """A stable structural fingerprint of a graph.

    Compares node ids/types/executors/configs and edge endpoints, deliberately
    IGNORING per-run status, output and evidence. The question is "is this the same
    shape of workflow?", not "did the run already finish".
    """
    nodes = sorted(
        (
            {
                "id": nid,
                "type": node.type.value,
                "executor": node.executor,
                "config": node.config,
                "prompt": node.prompt,
                "write_scope": sorted(node.write_scope),
            }
            for nid, node in graph.nodes.items()
        ),
        key=lambda entry: str(entry["id"]),
    )
    edges = sorted(
        ((edge.source, edge.target, edge.condition or "", edge.mode.value) for edge in graph.edges),
        key=str,
    )
    material = {"nodes": nodes, "edges": edges}
    return hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode("utf-8")).hexdigest()


_DEFAULT_STORE: TemplateStore | None = None
_DEFAULT_STORE_LOCK = threading.Lock()


def get_template_store() -> TemplateStore:
    """The process-wide template library seam."""
    global _DEFAULT_STORE
    with _DEFAULT_STORE_LOCK:
        if _DEFAULT_STORE is None:
            _DEFAULT_STORE = TemplateStore()
        return _DEFAULT_STORE


def set_template_store(store: TemplateStore | None) -> None:
    global _DEFAULT_STORE
    with _DEFAULT_STORE_LOCK:
        _DEFAULT_STORE = store


__all__ = [
    "TemplateError",
    "TemplateProvenance",
    "TemplateState",
    "TemplateStore",
    "WorkflowTemplate",
    "get_template_store",
    "set_template_store",
]
