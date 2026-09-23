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

import hashlib
import threading
import traceback
from collections.abc import Callable
from typing import Any

from alpha.workflow.models import WorkflowNode, WorkflowRun

# Signature of one node's real work, matching the engine's ``node_runner`` seam.
NodeExecutor = Callable[[WorkflowNode, WorkflowRun], dict[str, Any]]

# Registry key of the built-in local digest executor.
DIGEST_EXECUTOR = "alpha.local.digest"

# Registry key the moa (quorum) mapping declares for ensemble ballots. No
# default binding is shipped on purpose: a local process cannot honestly cast
# model votes, so an unbound vote executor makes the quorum node fail with the
# real reason instead of inventing agreement. Bound by callers that own real
# voters (P2 model executors, or a test's registered voting executor).
VOTE_EXECUTOR = "alpha.local.vote"

# Documented canonical input of the digest executor (tests recompute from this).
DIGEST_EVIDENCE_SUFFIX = "over run_id+node_id+node.prompt"


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

    def _run_node(self, node: WorkflowNode, run: WorkflowRun) -> dict[str, Any]:
        resolved = self.resolve(node)
        if resolved is None:
            return {
                "status": "failed",
                "output": (
                    f"no executor registered for node '{node.id}' "
                    f"(executor='{node.executor}', kind={node.type.value})"
                ),
                "evidence": "",
                "tokens_used": 0,
            }
        name, executor = resolved
        try:
            result = executor(node, run)
        except Exception as exc:  # noqa: BLE001 - any executor failure must surface honestly
            return {
                "status": "failed",
                "output": (
                    f"executor '{name}' raised {type(exc).__name__}: {exc}\n"
                    f"{traceback.format_exc()}"
                ),
                "evidence": "",
                "tokens_used": 0,
            }
        if not isinstance(result, dict) or result.get("status") not in ("completed", "failed"):
            return {
                "status": "failed",
                "output": (
                    f"executor '{name}' returned an invalid result {result!r} "
                    "(expected a dict with status 'completed' or 'failed')"
                ),
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
