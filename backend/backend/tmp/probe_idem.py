import hashlib
import json

from alpha.workflow.models import WorkflowNode, WorkflowRun
from alpha.workflow.runtime import _STATE_LOCK, IDEMPOTENT_OUTPUTS_KEY, set_node_runner

set_node_runner(lambda node, run: {"status": "completed", "output": {"ok": True}, "evidence": "e", "tokens_used": 0})
n = WorkflowNode(id="n1", idempotency_key="charge_42")
r = WorkflowRun(run_id="r1", workflow_id="wf1", state={"x": 1}, node_states={"n1": "pending"}, graph_version=1)
with _STATE_LOCK:
    r.metrics.setdefault("completed_idempotency_keys", [])
    r.metrics.setdefault("idempotent_outputs", {})
print("A dedupe before record:", "charge_42" in r.metrics.get("completed_idempotency_keys", []))
r.metrics["completed_idempotency_keys"] = r.metrics.get("completed_idempotency_keys", []) + ["charge_42"]
print("A dedupe after record:", "charge_42" in r.metrics.get("completed_idempotency_keys", []))
print("C attempt_hash_key:", "r1:n1:charge_42:" + hashlib.sha256(json.dumps({"node": "n1", "key": "charge_42", "state": {"x": 1}}, sort_keys=True, default=str).encode()).hexdigest())
print("A outputs recorded:", r.metrics.get(IDEMPOTENT_OUTPUTS_KEY))
