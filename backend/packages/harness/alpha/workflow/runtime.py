"""Deterministic runtime engine for Alpha Dynamic Workflows.

Coordinates scheduling, dynamic routing, graph patching, fan-out (Map/Reduce),
races, quorums, bounded loops, human approvals, and saga compensations.

DY-R1 honesty contract: every node action that performs real work runs through
the ``node_runner`` seam - the per-call argument of ``execute_step`` or the
module-level default bound with ``set_node_runner``. When no runner is bound,
the node fails with the real reason; outputs and evidence are never fabricated.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from alpha.workflow.events import get_event_dispatcher
from alpha.workflow.expressions import evaluate_condition
from alpha.workflow.models import (
    NodeStatus,
    NodeType,
    WorkflowDefinition,
    WorkflowGraph,
    WorkflowNode,
    WorkflowPatch,
    WorkflowRun,
    WorkflowRunStatus,
)
from alpha.workflow.patch import WorkflowPatchEngine
from alpha.workflow.replanner import RuntimeReplanner
from alpha.workflow.router import DynamicRouter
from alpha.workflow.scheduler import WorkflowScheduler

# Module-level node-runner seam: the single default executor binding shared by
# every DynamicWorkflowEngine. ``None`` means NO executor is bound, and nodes
# that require real execution must fail with the honest reason.
_NODE_RUNNER: Callable[[WorkflowNode, WorkflowRun], dict[str, Any]] | None = None

# Key under which a run records which state entries were written from real
# executor results (used to refuse reductions over non-executor values).
EXECUTOR_STATE_KEYS = "executor_state_keys"


def get_node_runner() -> Callable[[WorkflowNode, WorkflowRun], dict[str, Any]] | None:
    """Return the bound module-level node-runner seam, or None when unbound."""
    return _NODE_RUNNER


def set_node_runner(runner: Callable[[WorkflowNode, WorkflowRun], dict[str, Any]] | None) -> None:
    """Bind (or unbind by passing None) the module-level node-runner seam."""
    global _NODE_RUNNER
    _NODE_RUNNER = runner


def _no_runner_reason(node: WorkflowNode) -> str:
    """The honest failure reason when no executor is bound for this node."""
    return f"no node_runner bound to execute node '{node.id}' (kind={node.type.value})"


def _record_executor_state(run: WorkflowRun, key: str) -> None:
    """Record that ``run.state[key]`` was written from real executor results."""
    produced = run.metrics.get(EXECUTOR_STATE_KEYS)
    if not isinstance(produced, list):
        produced = []
        run.metrics[EXECUTOR_STATE_KEYS] = produced
    if key not in produced:
        produced.append(key)


def _is_executor_state(run: WorkflowRun, key: str) -> bool:
    """True when ``run.state[key]`` was written from real executor results."""
    produced = run.metrics.get(EXECUTOR_STATE_KEYS)
    return isinstance(produced, list) and key in produced


class DynamicWorkflowError(RuntimeError):
    """Base error for dynamic workflow execution."""
    pass


class UnverifiedNodeCompletionError(DynamicWorkflowError):
    """Raised when an attempt is made to mark a node complete without concrete evidence."""
    pass


class DynamicWorkflowEngine:
    """Production-grade Dynamic Workflow Engine."""

    def __init__(self) -> None:
        self.definitions: dict[str, WorkflowDefinition] = {}
        self.graphs: dict[str, WorkflowGraph] = {}  # key: f"{workflow_id}:v{version}"
        self.runs: dict[str, WorkflowRun] = {}
        self.scheduler = WorkflowScheduler()
        self.patch_engine = WorkflowPatchEngine()
        self.router = DynamicRouter()
        self.replanner = RuntimeReplanner()
        self.events = get_event_dispatcher()

    def register_definition(self, definition: WorkflowDefinition) -> None:
        self.definitions[definition.id] = definition
        self.graphs[f"{definition.id}:v{definition.graph.version}"] = definition.graph

    def get_definition(self, workflow_id: str) -> WorkflowDefinition | None:
        return self.definitions.get(workflow_id)

    def get_run(self, run_id: str) -> WorkflowRun | None:
        return self.runs.get(run_id)

    def start_run(
        self,
        workflow_id: str,
        initial_state: dict[str, Any] | None = None,
        run_id: str | None = None,
    ) -> WorkflowRun:
        definition = self.get_definition(workflow_id)
        if not definition:
            raise KeyError(f"Workflow definition '{workflow_id}' not found.")

        rid = run_id or f"run_{uuid.uuid4().hex[:12]}"
        run = WorkflowRun(
            run_id=rid,
            workflow_id=workflow_id,
            graph_version=definition.graph.version,
            status=WorkflowRunStatus.RUNNING,
            state=initial_state or dict(definition.variables),
            node_states={nid: NodeStatus.PENDING for nid in definition.graph.nodes},
        )
        self.runs[rid] = run
        self.events.emit("workflow_started", rid, workflow_id=workflow_id, state=run.state)
        return run

    def execute_step(
        self,
        run_id: str,
        node_runner: Callable[[WorkflowNode, WorkflowRun], dict[str, Any]] | None = None,
    ) -> WorkflowRun:
        """Execute one scheduling wave of ready nodes.

        ``node_runner`` executes one node and returns a result dict with real
        ``status``/``output``/``evidence``/``tokens_used`` entries. When the
        per-call argument is None the module-level seam bound via
        ``set_node_runner`` is used; when neither is bound, nodes that require
        real execution fail with the honest reason instead of fabricating
        output.
        """
        run = self.get_run(run_id)
        if not run:
            raise KeyError(f"Run '{run_id}' not found.")

        if run.status in (
            WorkflowRunStatus.COMPLETED,
            WorkflowRunStatus.FAILED,
            WorkflowRunStatus.CANCELLED,
            WorkflowRunStatus.WAITING_APPROVAL,
            WorkflowRunStatus.WAITING_EVENT,
            WorkflowRunStatus.SUSPENDED,
        ):
            return run

        runner = node_runner if node_runner is not None else get_node_runner()

        graph_key = f"{run.workflow_id}:v{run.graph_version}"
        graph = self.graphs.get(graph_key)
        if not graph:
            # Fall back to latest known or definition graph
            defn = self.get_definition(run.workflow_id)
            graph = defn.graph if defn else None
            if not graph:
                raise RuntimeError(f"Workflow graph '{graph_key}' not found.")

        ready = self.scheduler.compute_ready_nodes(graph, run)

        # Check completion
        if not ready:
            all_done = all(
                run.node_states.get(nid) in (NodeStatus.SUCCEEDED, NodeStatus.SKIPPED)
                for nid in graph.nodes
            )
            if all_done:
                run.status = WorkflowRunStatus.COMPLETED
                run.updated_at = datetime.now(UTC).isoformat()
                self.events.emit("workflow_completed", run.run_id, state=run.state)
                return run

            # If no ready nodes and not all done, check if waiting or deadlocked
            if run.waiting_nodes:
                return run

            # Attempt replan if deadlocked
            patch = self.replanner.propose_evidence_remediation_patch(
                "stagnation_recovery",
                "No progress achievable with current graph dependencies.",
                graph,
                run,
            )
            new_graph, validation = self.patch_engine.apply(run, graph, patch)
            if validation.allowed:
                self.graphs[f"{run.workflow_id}:v{new_graph.version}"] = new_graph
                return run

            run.status = WorkflowRunStatus.FAILED
            run.updated_at = datetime.now(UTC).isoformat()
            self.events.emit("workflow_failed", run.run_id, reason="Deadlock: no nodes ready to execute.")
            return run

        waves = self.scheduler.partition_into_waves(graph, ready)
        wave_nodes = waves[0] if waves else []

        for nid in wave_nodes:
            self._execute_single_node(nid, graph, run, runner)

        # Check if all non-compensation nodes are completed
        all_done = all(
            run.node_states.get(k) in (NodeStatus.SUCCEEDED, NodeStatus.SKIPPED)
            for k, n in graph.nodes.items()
            if n.type != NodeType.COMPENSATION
        )
        if all_done and run.status == WorkflowRunStatus.RUNNING:
            run.status = WorkflowRunStatus.COMPLETED
            self.events.emit("workflow_completed", run.run_id, state=run.state)

        run.updated_at = datetime.now(UTC).isoformat()
        return run

    def _fail_node(self, run: WorkflowRun, node: WorkflowNode, reason: str, **extra: Any) -> None:
        """Mark a node honestly failed: real reason in output and event log."""
        node.status = NodeStatus.FAILED
        run.node_states[node.id] = NodeStatus.FAILED
        if node.id not in run.failed_nodes:
            run.failed_nodes.append(node.id)
        node.output = {"status": "failed", "reason": reason, **extra}
        self.events.emit("node_failed", run.run_id, node_id=node.id, reason=reason)

    def _succeed_node(self, run: WorkflowRun, node: WorkflowNode, output: Any, **event_payload: Any) -> None:
        """Mark a node succeeded; the caller must have attached real evidence first."""
        node.status = NodeStatus.SUCCEEDED
        run.node_states[node.id] = NodeStatus.SUCCEEDED
        if node.id not in run.completed_nodes:
            run.completed_nodes.append(node.id)
        node.output = output
        if not event_payload:
            event_payload = {"output": output}
        self.events.emit("node_completed", run.run_id, node_id=node.id, **event_payload)

    def _charge_node_tokens(self, run: WorkflowRun, node: WorkflowNode, tokens: Any) -> bool:
        """Charge real runner-reported tokens against the node budget.

        Returns True when the budget is spent and the node has been failed
        accordingly (never reported as succeeded).
        """
        node.tokens_consumed += max(0, int(tokens or 0))
        if node.budget and node.tokens_consumed > node.budget:
            node.status = NodeStatus.FAILED
            run.node_states[node.id] = NodeStatus.FAILED
            run.status = WorkflowRunStatus.BUDGET_EXHAUSTED
            self.events.emit("node_failed", run.run_id, node_id=node.id, reason="Node budget exhausted.")
            return True
        return False

    @staticmethod
    def _fanout_child(node: WorkflowNode, child_id: str, overrides: dict[str, Any]) -> WorkflowNode:
        """Build the transient per-item node handed to the runner seam."""
        child = node.model_copy(deep=True)
        child.id = child_id
        child.config = {**node.config, **overrides}
        child.status = NodeStatus.PENDING
        child.evidence = []
        child.output = None
        return child

    def _execute_single_node(
        self,
        nid: str,
        graph: WorkflowGraph,
        run: WorkflowRun,
        node_runner: Callable[[WorkflowNode, WorkflowRun], dict[str, Any]] | None,
    ) -> None:
        node = graph.nodes[nid]

        # 1. Human-in-the-loop gate
        if node.requires_approval and not node.approval_request_id:
            appr_id = f"appr_{uuid.uuid4().hex[:8]}"
            node.approval_request_id = appr_id
            node.status = NodeStatus.WAITING
            run.node_states[nid] = NodeStatus.WAITING
            run.status = WorkflowRunStatus.WAITING_APPROVAL
            run.approval_request_id = appr_id
            run.waiting_reason = f"Node '{nid}' requires human approval before execution."
            self.events.emit(
                "approval_requested",
                run.run_id,
                node_id=nid,
                approval_id=appr_id,
                prompt=node.prompt,
            )
            return

        # 2. Loop Policy & Stagnation Check
        if node.loop_policy:
            count = run.iteration_counts.get(nid, 0)
            if node.loop_policy.stop_condition:
                context = {"state": run.state, "metrics": run.metrics}
                if evaluate_condition(node.loop_policy.stop_condition, context):
                    # Stop condition satisfied
                    node.status = NodeStatus.SUCCEEDED
                    run.node_states[nid] = NodeStatus.SUCCEEDED
                    run.completed_nodes.append(nid)
                    return

            if count >= node.loop_policy.max_iterations:
                # Max loop iterations reached
                node.status = NodeStatus.SUCCEEDED
                run.node_states[nid] = NodeStatus.SUCCEEDED
                run.completed_nodes.append(nid)
                return
            run.iteration_counts[nid] = count + 1

        # Mark Running
        node.status = NodeStatus.RUNNING
        run.node_states[nid] = NodeStatus.RUNNING
        self.events.emit("node_started", run.run_id, node_id=nid, type=node.type)

        try:
            # 3. Dynamic Node Type Handlers
            if node.type == NodeType.CONDITION:
                context = {"state": run.state, "metrics": run.metrics}
                cond_result = evaluate_condition(node.condition, context)
                run.state[f"{nid}_result"] = cond_result
                node.output = cond_result
                node.evidence.append(f"Condition '{node.condition}' evaluated to {cond_result}")
                node.status = NodeStatus.SUCCEEDED
                run.node_states[nid] = NodeStatus.SUCCEEDED
                run.completed_nodes.append(nid)
                self.events.emit("node_completed", run.run_id, node_id=nid, output=cond_result)
                return

            elif node.type == NodeType.ROUTER:
                decisions = self.router.resolve_next_nodes(nid, graph, run)
                node.output = [d.model_dump() for d in decisions]
                node.evidence.append(f"Router selected: {[d.target for d in decisions]}")
                node.status = NodeStatus.SUCCEEDED
                run.node_states[nid] = NodeStatus.SUCCEEDED
                run.completed_nodes.append(nid)
                self.events.emit("node_completed", run.run_id, node_id=nid, decisions=node.output)
                return

            elif node.type == NodeType.MAP:
                # Dynamic fan-out: every item is executed through the real runner seam.
                if node_runner is None:
                    self._fail_node(run, node, _no_runner_reason(node))
                    return
                items_key = node.config.get("items_key", "items")
                items = run.state.get(items_key)
                if not isinstance(items, list):
                    self._fail_node(run, node, f"map input '{items_key}' not present as a list in run state")
                    return
                if not items:
                    self._fail_node(run, node, f"map input '{items_key}' contains no items; nothing to execute")
                    return
                results: list[Any] = []
                for idx, item in enumerate(items):
                    child = self._fanout_child(node, f"{nid}[{idx}]", {"item": item, "item_index": idx})
                    res = node_runner(child, run)
                    if self._charge_node_tokens(run, node, res.get("tokens_used", 0)):
                        return
                    if res.get("status", "completed") != "completed":
                        self._fail_node(run, node, f"map item {idx}: node_runner reported failure: {res.get('output')!r}")
                        return
                    item_evidence = res.get("evidence")
                    if not item_evidence:
                        self._fail_node(run, node, f"map item {idx}: node_runner returned no evidence; completion refused")
                        return
                    results.append(res.get("output"))
                    node.evidence.append(f"map[{idx}] item={item!r}: {item_evidence}")
                run.state[f"{nid}_mapped"] = results
                _record_executor_state(run, f"{nid}_mapped")
                self._succeed_node(run, node, results)
                return

            elif node.type == NodeType.REDUCE:
                # Dynamic fan-in: fold only over real executor-produced child results.
                if node_runner is None:
                    self._fail_node(run, node, _no_runner_reason(node))
                    return
                input_key = node.config.get("input_key")
                if not isinstance(input_key, str) or not input_key:
                    self._fail_node(run, node, f"reduce node '{nid}' has no input_key configured")
                    return
                children = run.state.get(input_key)
                if not isinstance(children, list):
                    self._fail_node(run, node, f"reduce input '{input_key}' not present as a list in run state")
                    return
                if not _is_executor_state(run, input_key):
                    self._fail_node(run, node, f"reduce input '{input_key}' is not recorded as executor-produced child results")
                    return
                if not children:
                    self._fail_node(run, node, f"reduce input '{input_key}' has no executor-produced child results to reduce")
                    return
                accumulator: Any = node.config.get("initial_value")
                for idx, child_value in enumerate(children):
                    child_overrides: dict[str, Any] = {"item": child_value, "item_index": idx, "accumulator": accumulator}
                    child = self._fanout_child(node, f"{nid}[{idx}]", child_overrides)
                    res = node_runner(child, run)
                    if self._charge_node_tokens(run, node, res.get("tokens_used", 0)):
                        return
                    if res.get("status", "completed") != "completed":
                        self._fail_node(run, node, f"reduce step {idx}: node_runner reported failure: {res.get('output')!r}")
                        return
                    child_evidence = res.get("evidence")
                    if not child_evidence:
                        self._fail_node(run, node, f"reduce step {idx}: node_runner returned no evidence; completion refused")
                        return
                    accumulator = res.get("output")
                    node.evidence.append(f"reduce[{idx}]: {child_evidence}")
                run.state[f"{nid}_reduced"] = accumulator
                _record_executor_state(run, f"{nid}_reduced")
                self._succeed_node(run, node, accumulator)
                return

            elif node.type == NodeType.RACE:
                # Race: bounded first-real-success-wins, every candidate actually run.
                if node_runner is None:
                    self._fail_node(run, node, _no_runner_reason(node))
                    return
                candidates = node.config.get("candidates")
                if not isinstance(candidates, list) or not candidates:
                    self._fail_node(run, node, f"race node '{nid}' has no candidates configured")
                    return
                attempts: list[dict[str, Any]] = []
                for idx, candidate in enumerate(candidates):
                    child = self._fanout_child(node, f"{nid}[{idx}]", {"candidate": candidate, "candidate_index": idx})
                    res = node_runner(child, run)
                    if self._charge_node_tokens(run, node, res.get("tokens_used", 0)):
                        return
                    candidate_status = res.get("status", "completed")
                    candidate_output = res.get("output")
                    attempts.append({"candidate": candidate, "status": candidate_status, "output": candidate_output})
                    if candidate_status != "completed":
                        continue
                    candidate_evidence = res.get("evidence")
                    if not candidate_evidence:
                        raise UnverifiedNodeCompletionError(f"Race candidate '{candidate}' completed without evidence.")
                    node.evidence.append(f"race winner candidate={candidate!r}: {candidate_evidence}")
                    self._succeed_node(run, node, {"winner": candidate, "attempts": attempts})
                    return
                fail_summary = f"race node '{nid}': no candidate succeeded after {len(candidates)} candidate attempt(s)"
                self._fail_node(run, node, fail_summary, attempts=attempts)
                return

            elif node.type == NodeType.QUORUM:
                # Quorum: real votes come from executor results; config-supplied
                # votes are allowed only with an explicit vote_source disclosure.
                voters = node.config.get("voters")
                config_votes = node.config.get("votes")
                votes: list[str] | None = None
                vote_source: str | None = None
                if isinstance(voters, list) and voters and node_runner is not None:
                    vote_source = "executor"
                    votes = []
                    for idx, voter in enumerate(voters):
                        child = self._fanout_child(node, f"{nid}[{idx}]", {"voter": voter, "voter_index": idx})
                        res = node_runner(child, run)
                        if self._charge_node_tokens(run, node, res.get("tokens_used", 0)):
                            return
                        if res.get("status", "completed") != "completed":
                            self._fail_node(run, node, f"quorum voter '{voter}': node_runner reported failure: {res.get('output')!r}")
                            return
                        voter_evidence = res.get("evidence")
                        if not voter_evidence:
                            self._fail_node(run, node, f"quorum voter '{voter}': node_runner returned no evidence; completion refused")
                            return
                        raw_vote = res.get("output")
                        if isinstance(raw_vote, dict):
                            raw_vote = raw_vote.get("vote")
                        vote = str(raw_vote).strip().lower() if raw_vote is not None else ""
                        if vote not in ("agree", "disagree"):
                            invalid = f"quorum voter '{voter}' returned invalid vote {raw_vote!r} (expected 'agree' or 'disagree')"
                            self._fail_node(run, node, invalid)
                            return
                        votes.append(vote)
                        node.evidence.append(f"quorum voter {voter!r}: vote={vote} evidence={voter_evidence}")
                elif isinstance(config_votes, list) and config_votes:
                    # Config-supplied votes are disclosed as config, never implied to be agent votes.
                    vote_source = "config"
                    votes = [str(v).strip().lower() for v in config_votes]
                    node.evidence.append(f"provenance: votes supplied via node config (vote_source=config), not agent votes: {votes}")
                else:
                    if node_runner is None:
                        self._fail_node(run, node, _no_runner_reason(node))
                    else:
                        self._fail_node(run, node, f"quorum node '{nid}' has no voters configured and no config-supplied votes")
                    return
                required_votes = int(node.config.get("required_votes", 2))
                agrees = sum(1 for v in votes if v == "agree")
                passed = agrees >= required_votes
                payload = {
                    "agreed": passed,
                    "votes": votes,
                    "vote_source": vote_source,
                    "agrees": agrees,
                    "required_votes": required_votes,
                }
                if passed:
                    node.evidence.append(f"Quorum reached: {agrees}/{required_votes} votes agreed (vote_source={vote_source}).")
                    self._succeed_node(run, node, payload)
                    return
                unmet = f"quorum not reached: {agrees}/{required_votes} votes agreed (vote_source={vote_source})"
                self._fail_node(run, node, unmet, **payload)
                return

            elif node.type == NodeType.COMPENSATION:
                # Saga compensation: a real callback through the runner seam; with
                # no runner bound the node records compensated=false honestly.
                if node_runner is None:
                    self._fail_node(run, node, f"{_no_runner_reason(node)}: no compensation callback executed", compensated=False)
                    return
                target = node.config.get("target_rollback_node")
                res = node_runner(node, run)
                if self._charge_node_tokens(run, node, res.get("tokens_used", 0)):
                    return
                if res.get("status", "completed") != "completed":
                    comp_failed = f"compensation callback failed for target '{target}': {res.get('output')!r}"
                    self._fail_node(run, node, comp_failed, compensated=False, target=target)
                    return
                comp_evidence = res.get("evidence")
                if not comp_evidence:
                    no_ev = f"compensation callback for target '{target}' returned no evidence; completion refused"
                    self._fail_node(run, node, no_ev, compensated=False, target=target)
                    return
                node.evidence.append(f"compensation target={target!r}: {comp_evidence}")
                self._succeed_node(run, node, {"compensated": True, "target": target, "result": res.get("output")})
                return

            elif node.type == NodeType.BOT:
                # Bot nodes execute through the real seam. The cloning registry is
                # only consulted when an executor exists to run the bot's task; a
                # clone without a model/tool call is never reported as execution.
                if node_runner is None:
                    self._fail_node(run, node, f"{_no_runner_reason(node)}: no bot executor performed a model/tool call")
                    return
                if node.config.get("clone_bot", False):
                    from alpha.bots.cloning import get_bot_clone_engine

                    specialist_directive = node.config.get("specialist_directive", node.prompt)
                    bot_profile = get_bot_clone_engine().clone_bot(
                        source_name=node.config.get("bot_name", "developer"),
                        specialist_directive=specialist_directive,
                        skills_to_add=node.config.get("skills"),
                        tools_to_add=node.config.get("tools"),
                        ttl_seconds=node.config.get("ttl_seconds", 3600),
                    )
                    # Real registry side effect, logged with the true clone name.
                    self.events.emit("bot_cloned", run.run_id, node_id=nid, bot_name=bot_profile.name)
                # Fall through: the bot's task itself runs through node_runner below.

            # Default / Agent / Tool execution (and prepared BOT tasks above)
            if node_runner is not None:
                res = node_runner(node, run)
                status = res.get("status", "completed")
                output = res.get("output")
                evidence = res.get("evidence")
                tokens = res.get("tokens_used", 0)

                if self._charge_node_tokens(run, node, tokens):
                    return

                if status == "completed":
                    if not evidence and not node.evidence:
                        raise UnverifiedNodeCompletionError(f"Node '{nid}' completed without evidence.")
                    if evidence:
                        node.evidence.append(evidence)
                    node.output = output
                    if node.loop_policy:
                        stop_met = False
                        if node.loop_policy.stop_condition:
                            context = {"state": run.state, "metrics": run.metrics}
                            stop_met = evaluate_condition(node.loop_policy.stop_condition, context)
                        count = run.iteration_counts.get(nid, 0)
                        if stop_met or count >= node.loop_policy.max_iterations:
                            node.status = NodeStatus.SUCCEEDED
                            run.node_states[nid] = NodeStatus.SUCCEEDED
                            if nid not in run.completed_nodes:
                                run.completed_nodes.append(nid)
                        else:
                            node.status = NodeStatus.READY
                            run.node_states[nid] = NodeStatus.READY
                    else:
                        node.status = NodeStatus.SUCCEEDED
                        run.node_states[nid] = NodeStatus.SUCCEEDED
                        if nid not in run.completed_nodes:
                            run.completed_nodes.append(nid)
                    self.events.emit("node_completed", run.run_id, node_id=nid, output=output)
                else:
                    raise RuntimeError(f"Runner reported failure for node '{nid}': {output}")
            else:
                # No executor is bound: fail honestly, never fabricate success.
                self._fail_node(run, node, _no_runner_reason(node))

        except Exception as exc:
            node.status = NodeStatus.FAILED
            run.node_states[nid] = NodeStatus.FAILED
            run.failed_nodes.append(nid)
            self.events.emit("node_failed", run.run_id, node_id=nid, error=str(exc))

            # Trigger Saga Compensation if defined
            if node.compensation_node_id and node.compensation_node_id in graph.nodes:
                comp_node = graph.nodes[node.compensation_node_id]
                comp_node.status = NodeStatus.COMPENSATING
                run.node_states[comp_node.id] = NodeStatus.COMPENSATING
                self.events.emit("compensation_triggered", run.run_id, node_id=comp_node.id)

    def apply_patch(self, run_id: str, patch: WorkflowPatch) -> tuple[WorkflowGraph, Any]:
        run = self.get_run(run_id)
        if not run:
            raise KeyError(f"Run '{run_id}' not found.")

        graph_key = f"{run.workflow_id}:v{run.graph_version}"
        graph = self.graphs.get(graph_key)
        if not graph:
            defn = self.get_definition(run.workflow_id)
            graph = defn.graph

        new_graph, validation = self.patch_engine.apply(run, graph, patch)
        if validation.allowed:
            self.graphs[f"{run.workflow_id}:v{new_graph.version}"] = new_graph
            for nid, node in new_graph.nodes.items():
                if nid not in run.node_states:
                    run.node_states[nid] = node.status
        return new_graph, validation

    def resolve_approval(self, run_id: str, node_id: str, approved: bool, feedback: str = "") -> WorkflowRun:
        run = self.get_run(run_id)
        if not run:
            raise KeyError(f"Run '{run_id}' not found.")

        graph = self.graphs.get(f"{run.workflow_id}:v{run.graph_version}")
        if not graph or node_id not in graph.nodes:
            raise KeyError(f"Node '{node_id}' not found in graph.")

        node = graph.nodes[node_id]
        if approved:
            node.status = NodeStatus.READY
            run.node_states[node_id] = NodeStatus.READY
            run.status = WorkflowRunStatus.RUNNING
            run.approval_request_id = None
            run.waiting_reason = None
            self.events.emit("approval_granted", run_id, node_id=node_id, feedback=feedback)
        else:
            node.status = NodeStatus.FAILED
            run.node_states[node_id] = NodeStatus.FAILED
            run.status = WorkflowRunStatus.FAILED
            run.approval_request_id = None
            run.waiting_reason = f"Human rejected node '{node_id}': {feedback}"
            self.events.emit("approval_denied", run_id, node_id=node_id, feedback=feedback)

        return run
