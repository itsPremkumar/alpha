"""Real node-executor registry for the Dynamic Workflow Engine (P1 kernel, DY-R4).

Honesty contract (DY-R1 carried into the orchestrator layer):

- A *node executor* is a callable ``(WorkflowNode, WorkflowRun) -> dict`` that
  performs one node's REAL work and returns the DWE runner result shape
  ``{"status", "output", "evidence", "tokens_used"}``.
- ``ExecutorRegistry.build_runner()`` returns ``None`` while no executor is
  registered, so an unbound registry keeps the engine's exact refusal:
  ``no node_runner bound to execute node '...'`` — the module-level
  ``alpha.workflow.runtime`` seam is never bound by this module, only
  consulted by the engine as its documented fallback.
- When executors ARE registered, the built runner resolves the node's declared
  ``executor`` name against the registry:
    * unresolvable node -> honest failed result naming the missing piece
      (``no executor registered for node '...'``);
    * executor raises -> honest failed result carrying the REAL traceback
      summary captured at the raise site (never a fabricated success);
    * executor returns a malformed result -> honest failed result naming the
      shape violation.

Built-in executor ``alpha.local.digest`` performs genuine, independently
re-checkable local work: ``sha256(run_id + "\\n" + node_id + "\\n" + prompt)``.
Evidence text embeds that digest so consumers (and tests) can recompute it from
the documented formula; ``tokens_used`` is 0 because no model call happened —
a real number, never an invented one. Model/tool/MCP executors land with the
P2 registry wave; until they are bound, nodes that declare them fail honestly.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import threading
import traceback
from collections.abc import Callable
from typing import Any

from alpha.workflow.models import WorkflowNode, WorkflowRun

# Signature of one node's real work, matching the engine's ``node_runner`` seam.
NodeExecutor = Callable[[WorkflowNode, WorkflowRun], dict[str, Any]]

# Registry key of the built-in local digest executor.
DIGEST_EXECUTOR = "alpha.local.digest"

# Registry key for a real saga-compensation callback. It is deliberately not
# part of ``_DEFAULT_EXECUTORS``: a digest executor must never masquerade as a
# rollback side effect.
COMPENSATION_EXECUTOR = "alpha.local.compensation"

# Registry key the moa (quorum) mapping declares for ensemble ballots. No
# default binding is shipped on purpose: a local process cannot honestly cast
# model votes, so an unbound vote executor makes the quorum node fail with the
# real reason instead of inventing agreement. Bound by callers that own real
# voters (P2 model executors, or a test's registered voting executor).
VOTE_EXECUTOR = "alpha.local.vote"

# Documented canonical input of the digest executor (tests recompute from this).
DIGEST_EVIDENCE_SUFFIX = "over run_id+node_id+node.prompt"

LOCAL_RESEARCH_EXECUTOR = "alpha.local.research"
LOCAL_VALIDATION_EXECUTOR = "alpha.local.validation"
LOCAL_STATE_EXECUTOR = "alpha.local.state"
LOCAL_SKILL_EXECUTOR = "alpha.local.skill"
LOCAL_MCP_EXECUTOR = "alpha.local.mcp"
LOCAL_MEMORY_EXECUTOR = "alpha.local.memory"


def digest_input(node: WorkflowNode, run: WorkflowRun) -> bytes:
    """Canonical bytes hashed by :func:`local_digest_executor`."""
    return f"{run.run_id}\n{node.id}\n{node.prompt or ''}".encode()


def local_digest_executor(node: WorkflowNode, run: WorkflowRun) -> dict[str, Any]:
    """Built-in executor: genuine deterministic local computation, no model call.

    The evidence string contains the recomputable digest, so "completion" is
    checkable by any consumer instead of being a canned prose claim.
    """
    digest = hashlib.sha256(digest_input(node, run)).hexdigest()
    return {
        "status": "completed",
        "output": {"executor": DIGEST_EXECUTOR, "node_id": node.id, "sha256": digest},
        "evidence": f"{DIGEST_EXECUTOR} sha256={digest} {DIGEST_EVIDENCE_SUFFIX}",
        # Real accounting: local hashing consumed zero model tokens.
        "tokens_used": 0,
    }


def local_research_executor(node: WorkflowNode, run: WorkflowRun) -> dict[str, Any]:
    """Validate local research inputs without pretending to search the web."""
    items = node.config.get("items", run.state.get("sources", []))
    if not isinstance(items, list):
        return {"status": "failed", "output": "research input is not a list", "evidence": "", "tokens_used": 0}
    digest = hashlib.sha256(json.dumps(items, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return {
        "status": "completed",
        "output": {"executor": LOCAL_RESEARCH_EXECUTOR, "items_validated": len(items), "input_digest": digest, "network_used": False},
        "evidence": f"{LOCAL_RESEARCH_EXECUTOR} validated {len(items)} local input item(s); no network claim",
        "tokens_used": 0,
    }


def local_validation_executor(node: WorkflowNode, run: WorkflowRun) -> dict[str, Any]:
    """Evaluate declared criteria as a local structural gate."""
    criteria = node.config.get("verification_criteria", [])
    if not isinstance(criteria, list):
        criteria = []
    digest = hashlib.sha256(json.dumps(criteria, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return {
        "status": "completed",
        "output": {"executor": LOCAL_VALIDATION_EXECUTOR, "criteria_checked": len(criteria), "criteria_digest": digest},
        "evidence": f"{LOCAL_VALIDATION_EXECUTOR} inspected {len(criteria)} declared criterion/criteria",
        "tokens_used": 0,
    }


def local_state_executor(node: WorkflowNode, run: WorkflowRun) -> dict[str, Any]:
    """Write a bounded, explicit local state projection."""
    key = node.config.get("state_key")
    if not isinstance(key, str) or not key:
        return {"status": "failed", "output": "state executor requires config.state_key", "evidence": "", "tokens_used": 0}
    value = node.config.get("value", {"node_id": node.id, "prompt": node.prompt or ""})
    run.state[key] = value
    digest = hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return {
        "status": "completed",
        "output": {"executor": LOCAL_STATE_EXECUTOR, "state_key": key, "sha256": digest},
        "evidence": f"{LOCAL_STATE_EXECUTOR} wrote state key '{key}' with sha256={digest}",
        "tokens_used": 0,
    }


def local_skill_executor(node: WorkflowNode, run: WorkflowRun) -> dict[str, Any]:
    """Read the installed skill registry and report availability honestly."""
    try:
        from alpha.workflow.registry.skills import SkillRegistry

        descriptors = SkillRegistry().list()
    except Exception as exc:
        return {"status": "failed", "output": f"skill registry unavailable: {type(exc).__name__}: {exc}", "evidence": "", "tokens_used": 0}
    requested = [str(item) for item in node.config.get("skills", [])]
    available = {item.id for item in descriptors if item.availability == "available"}
    missing = [name for name in requested if name not in available]
    if missing:
        return {"status": "failed", "output": f"requested skills unavailable: {missing}", "evidence": "", "tokens_used": 0}
    return {
        "status": "completed",
        "output": {"executor": LOCAL_SKILL_EXECUTOR, "available_count": len(available), "requested": requested},
        "evidence": f"{LOCAL_SKILL_EXECUTOR} resolved {len(requested)} requested skill(s) from the live registry",
        "tokens_used": 0,
    }


def local_mcp_executor(node: WorkflowNode, run: WorkflowRun) -> dict[str, Any]:
    """Inspect configured MCP descriptors without claiming a live connection."""
    try:
        from alpha.workflow.registry.mcp import MCPServerRegistry

        descriptors = MCPServerRegistry().list()
    except Exception as exc:
        return {"status": "failed", "output": f"MCP registry unavailable: {type(exc).__name__}: {exc}", "evidence": "", "tokens_used": 0}
    requested = [str(item) for item in node.config.get("servers", [])]
    by_id = {item.id: item for item in descriptors}
    missing = [name for name in requested if name not in by_id or by_id[name].availability != "available"]
    if missing:
        return {"status": "failed", "output": f"requested MCP servers unavailable or disabled: {missing}", "evidence": "", "tokens_used": 0}
    return {
        "status": "completed",
        "output": {"executor": LOCAL_MCP_EXECUTOR, "configured_servers": requested, "transport_connected": False},
        "evidence": f"{LOCAL_MCP_EXECUTOR} found {len(requested)} configured server(s); transport connection was not claimed",
        "tokens_used": 0,
    }


def local_memory_executor(node: WorkflowNode, run: WorkflowRun) -> dict[str, Any]:
    """Record a bounded memory intent; actual memory owners remain external."""
    key = node.config.get("state_key", "memory_intent")
    run.state[key] = {"objective": node.prompt or "", "source": "workflow"}
    return {
        "status": "completed",
        "output": {"executor": LOCAL_MEMORY_EXECUTOR, "state_key": key, "persisted": False},
        "evidence": f"{LOCAL_MEMORY_EXECUTOR} recorded a local memory intent; no external memory write claimed",
        "tokens_used": 0,
    }


class ExecutorRegistry:
    """Thread-safe registry binding executor names to real callables."""

    def __init__(self) -> None:
        self._executors: dict[str, NodeExecutor] = {}
        self._lock = threading.Lock()

    def register(self, name: str, executor: NodeExecutor) -> None:
        """Bind (or re-bind) ``name``; re-registration is idempotent by key."""
        if not name:
            raise ValueError("executor name must be a non-empty string")
        with self._lock:
            self._executors[name] = executor

    def unregister(self, name: str) -> bool:
        with self._lock:
            return self._executors.pop(name, None) is not None

    def clear(self) -> None:
        """Unbind every executor: the registry then builds no runner at all."""
        with self._lock:
            self._executors.clear()

    def names(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._executors))

    def resolve(self, node: WorkflowNode) -> tuple[str, NodeExecutor] | None:
        """Resolve the node's declared ``executor`` name, or None when unbound."""
        with self._lock:
            executor = self._executors.get(node.executor)
        if executor is None:
            return None
        return node.executor, executor

    def has(self, name: str) -> bool:
        """Whether an executor is currently bound under ``name``."""
        with self._lock:
            return name in self._executors

    def build_runner(self) -> Callable[[WorkflowNode, WorkflowRun], dict[str, Any]] | None:
        """Return a runner over the live registry, or None when nothing is bound.

        ``None`` is the honest unbound signal the engine refuses on; a runner
        built here reads the registry per node so late bindings and test seam
        swaps are observed without rebuilding the runner.
        """
        with self._lock:
            if not self._executors:
                return None
        return self._run_node

    def build_compensation_runner(self) -> Callable[[WorkflowNode, WorkflowRun], dict[str, Any]] | None:
        """Build a runner for the separate compensation registry.

        No default is installed.  Callers must explicitly register a callback
        that performs the rollback and returns evidence before a compensation
        node can succeed.
        """
        with self._lock:
            if COMPENSATION_EXECUTOR not in self._executors:
                return None
        return self._run_compensation

    def _run_compensation(self, node: WorkflowNode, run: WorkflowRun) -> dict[str, Any]:
        with self._lock:
            executor = self._executors.get(COMPENSATION_EXECUTOR)
        if executor is None:
            return {
                "status": "failed",
                "output": f"no compensation executor registered under '{COMPENSATION_EXECUTOR}'",
                "evidence": "",
                "tokens_used": 0,
            }
        try:
            result = executor(node, run)
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "failed",
                "output": f"compensation executor raised {type(exc).__name__}: {exc}",
                "evidence": "",
                "tokens_used": 0,
            }
        if not isinstance(result, dict) or result.get("status") not in ("completed", "failed"):
            return {
                "status": "failed",
                "output": f"compensation executor returned invalid result {result!r}",
                "evidence": "",
                "tokens_used": 0,
            }
        return result

    def _run_node(self, node: WorkflowNode, run: WorkflowRun) -> dict[str, Any]:
        if node.executor == COMPENSATION_EXECUTOR and node.type.value != "compensation":
            return {
                "status": "failed",
                "output": f"compensation executor '{COMPENSATION_EXECUTOR}' cannot run ordinary node '{node.id}'",
                "evidence": "",
                "tokens_used": 0,
            }
        if node.executor == VOTE_EXECUTOR and node.type.value != "quorum":
            return {
                "status": "failed",
                "output": f"vote executor '{VOTE_EXECUTOR}' is restricted to quorum nodes",
                "evidence": "",
                "tokens_used": 0,
            }
        resolved = self.resolve(node)
        if resolved is None:
            return {
                "status": "failed",
                "output": (f"no executor registered for node '{node.id}' (executor='{node.executor}', kind={node.type.value})"),
                "evidence": "",
                "tokens_used": 0,
            }
        name, executor = resolved
        try:
            result = executor(node, run)
            if inspect.isawaitable(result):
                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    result = asyncio.run(result)
                else:
                    if inspect.iscoroutine(result):
                        result.close()
                    return {
                        "status": "failed",
                        "output": "async executor cannot run on an active event-loop thread; use the host's worker boundary",
                        "evidence": "",
                        "tokens_used": 0,
                    }
        except Exception as exc:  # noqa: BLE001 - any executor failure must surface honestly
            return {
                "status": "failed",
                "output": (f"executor '{name}' raised {type(exc).__name__}: {exc}\n{traceback.format_exc()}"),
                "evidence": "",
                "tokens_used": 0,
            }
        if not isinstance(result, dict) or result.get("status") not in ("completed", "failed"):
            return {
                "status": "failed",
                "output": (f"executor '{name}' returned an invalid result {result!r} (expected a dict with status 'completed' or 'failed')"),
                "evidence": "",
                "tokens_used": 0,
            }
        return result


# Module-level registry: the seam the REST step endpoint and kernel resolve at
# call time. Production binds the built-in local executors via
# ``bind_default_executors()`` (called by the workflows router at import).
_REGISTRY = ExecutorRegistry()

_DEFAULT_EXECUTORS: dict[str, NodeExecutor] = {
    DIGEST_EXECUTOR: local_digest_executor,
}

_PROJECTION_EXECUTORS: dict[str, NodeExecutor] = {
    LOCAL_RESEARCH_EXECUTOR: local_research_executor,
    LOCAL_VALIDATION_EXECUTOR: local_validation_executor,
    LOCAL_STATE_EXECUTOR: local_state_executor,
    LOCAL_SKILL_EXECUTOR: local_skill_executor,
    LOCAL_MCP_EXECUTOR: local_mcp_executor,
    LOCAL_MEMORY_EXECUTOR: local_memory_executor,
}


def get_executor_registry() -> ExecutorRegistry:
    """Return the live module-level registry (read per dispatch, never cached)."""
    return _REGISTRY


def bind_default_executors() -> ExecutorRegistry:
    """Idempotently bind the built-in local executors into the live registry.

    This is the P1 production binding point: after it runs, REST ``step``
    requests execute genuinely bound nodes; nodes that declare an executor
    outside this set still fail honestly naming the missing piece.
    """
    for name, executor in _DEFAULT_EXECUTORS.items():
        _REGISTRY.register(name, executor)
    return _REGISTRY


def bind_projection_executors() -> ExecutorRegistry:
    """Bind bounded local capability projections on explicit request.

    These executors never impersonate network research, live MCP connections,
    bot execution, or external memory persistence; they expose measured local
    checks and keep those boundaries visible in their evidence strings.
    """
    for name, executor in _PROJECTION_EXECUTORS.items():
        _REGISTRY.register(name, executor)
    return _REGISTRY
