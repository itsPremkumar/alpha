"""Built-in action_transaction tool inspired by hermes-agi-asi-harness."""

from __future__ import annotations

import json
from pathlib import Path

from langchain.tools import tool

from alpha.action import ActionPrimitive, ActionRequest, ActionTransaction


@tool("execute_transactional_action", parse_docstring=True)
def execute_transactional_action(
    primitive: str,
    target_path: str,
    content: str = "",
) -> str:
    """Execute a file action within an ACID-like 4-stage transaction envelope with automatic rollback.

    Stages: PREPARE (shadow snapshot) -> VALIDATE -> COMMIT -> VERIFY.
    If post-commit verification fails, all mutations are automatically reverted.

    Args:
        primitive: The UAP action primitive ('create', 'update', 'delete').
        target_path: Absolute or workspace-relative path to the targeted file.
        content: The text content for create or update operations.
    """
    prim_map = {
        "create": ActionPrimitive.CREATE,
        "update": ActionPrimitive.UPDATE,
        "delete": ActionPrimitive.DELETE,
    }
    prim = prim_map.get(primitive.lower().strip())
    if not prim:
        return json.dumps({"status": "failed", "error": f"Unsupported primitive: {primitive}"})

    p = Path(target_path)
    tx = ActionTransaction()
    tx.add_action(ActionRequest(primitive=prim, target=str(p)))

    def commit_fn(actions):
        p.parent.mkdir(parents=True, exist_ok=True)
        if prim == ActionPrimitive.DELETE:
            p.unlink(missing_ok=True)
        else:
            p.write_text(content, encoding="utf-8")
        return True

    def verify_fn():
        if prim == ActionPrimitive.DELETE:
            return not p.exists()
        return p.exists() and p.read_text(encoding="utf-8") == content

    success = tx.execute_lifecycle(commit_fn=commit_fn, verify_fn=verify_fn)

    return json.dumps({
        "status": "success" if success else "rolled_back",
        "transaction_id": tx.transaction_id,
        "stage": tx.stage.value,
        "error": tx.error,
    }, indent=2)
