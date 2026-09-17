from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .uap import ActionPrimitive, ActionRequest

logger = logging.getLogger("agent_workspace.action.transaction")


class TransactionStage(str, Enum):
    IDLE = "idle"
    PREPARED = "prepared"
    VALIDATED = "validated"
    COMMITTED = "committed"
    VERIFIED = "verified"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


@dataclass
class CompensationStep:
    """A registered rollback procedure to undo a committed mutation."""
    description: str
    undo_fn: Callable[[], Any]


class ActionTransaction:
    """
    ACID-like 4-Stage Transaction Protocol for Agent Actions:
      PREPARE  -->  VALIDATE  -->  COMMIT  -->  VERIFY
         |             |            |          |
         +--- Fail ----+--- Fail ---+--- Fail -+---> ROLLBACK (Compensation)
    """

    def __init__(self, transaction_id: str | None = None) -> None:
        self.transaction_id = transaction_id or f"tx_{uuid.uuid4().hex[:10]}"
        self.stage = TransactionStage.IDLE
        self.actions: list[ActionRequest] = []
        self.shadow_snapshots: dict[str, str | None] = {}
        self.compensations: list[CompensationStep] = []
        self.error: str | None = None
        self.created_at = time.time()
        self.updated_at = self.created_at

    def add_action(self, action: ActionRequest) -> None:
        if self.stage != TransactionStage.IDLE:
            raise RuntimeError(f"Cannot add actions in stage {self.stage}")
        self.actions.append(action)
        self.updated_at = time.time()

    def register_compensation(self, description: str, undo_fn: Callable[[], Any]) -> None:
        self.compensations.append(CompensationStep(description=description, undo_fn=undo_fn))

    def prepare(self) -> bool:
        """Stage 1: PREPARE - snapshot targeted files and state."""
        try:
            for act in self.actions:
                if act.primitive in (ActionPrimitive.CREATE, ActionPrimitive.UPDATE, ActionPrimitive.DELETE):
                    target_path = Path(act.target)
                    if target_path.exists() and target_path.is_file():
                        self.shadow_snapshots[str(target_path)] = target_path.read_text(encoding="utf-8", errors="replace")
                    else:
                        self.shadow_snapshots[str(target_path)] = None  # Did not exist previously

            self.stage = TransactionStage.PREPARED
            self.updated_at = time.time()
            return True
        except Exception as e:
            self.error = f"Prepare failed: {e}"
            self.stage = TransactionStage.FAILED
            return False

    def validate(self, safety_check: Callable[[list[ActionRequest]], bool] | None = None) -> bool:
        """Stage 2: VALIDATE - evaluate safety envelopes and AST checks."""
        if self.stage != TransactionStage.PREPARED:
            self.error = f"Cannot validate from stage {self.stage}"
            return False

        try:
            if safety_check and not safety_check(self.actions):
                self.error = "Safety policy check failed"
                self.stage = TransactionStage.FAILED
                return False

            self.stage = TransactionStage.VALIDATED
            self.updated_at = time.time()
            return True
        except Exception as e:
            self.error = f"Validation exception: {e}"
            self.stage = TransactionStage.FAILED
            return False

    def commit(self, commit_fn: Callable[[list[ActionRequest]], bool] | None = None) -> bool:
        """Stage 3: COMMIT - apply the actual modifications."""
        if self.stage != TransactionStage.VALIDATED:
            self.error = f"Cannot commit from stage {self.stage}"
            return False

        try:
            # Register default file restoration compensations
            for path_str, orig_content in self.shadow_snapshots.items():
                p = Path(path_str)
                if orig_content is None:
                    # File didn't exist before commit -> remove it on rollback
                    self.register_compensation(
                        description=f"Remove created file {path_str}",
                        undo_fn=lambda target_p=p: target_p.unlink(missing_ok=True),
                    )
                else:
                    # File existed -> restore original content
                    self.register_compensation(
                        description=f"Restore original content of {path_str}",
                        undo_fn=lambda target_p=p, content=orig_content: target_p.write_text(content, encoding="utf-8"),
                    )

            if commit_fn and not commit_fn(self.actions):
                self.error = "Commit function returned False"
                self.rollback()
                return False

            self.stage = TransactionStage.COMMITTED
            self.updated_at = time.time()
            return True
        except Exception as e:
            self.error = f"Commit exception: {e}"
            self.rollback()
            return False

    def verify(self, verify_fn: Callable[[], bool] | None = None) -> bool:
        """Stage 4: VERIFY - post-action test and invariant verification."""
        if self.stage != TransactionStage.COMMITTED:
            self.error = f"Cannot verify from stage {self.stage}"
            return False

        try:
            if verify_fn and not verify_fn():
                self.error = "Verification failed post-commit"
                self.rollback()
                return False

            self.stage = TransactionStage.VERIFIED
            self.updated_at = time.time()
            return True
        except Exception as e:
            self.error = f"Verification exception: {e}"
            self.rollback()
            return False

    def rollback(self) -> bool:
        """Execute all registered compensation procedures in reverse order."""
        logger.warning("Rolling back transaction %s", self.transaction_id)
        rollback_success = True
        for comp in reversed(self.compensations):
            try:
                comp.undo_fn()
            except Exception as e:
                logger.error("Compensation '%s' failed during rollback: %s", comp.description, e)
                rollback_success = False

        self.stage = TransactionStage.ROLLED_BACK
        self.updated_at = time.time()
        return rollback_success

    def execute_lifecycle(
        self,
        commit_fn: Callable[[list[ActionRequest]], bool],
        verify_fn: Callable[[], bool] | None = None,
        safety_check: Callable[[list[ActionRequest]], bool] | None = None,
    ) -> bool:
        """Executes the full 4-stage lifecycle atomically."""
        if not self.prepare():
            return False
        if not self.validate(safety_check=safety_check):
            return False
        if not self.commit(commit_fn=commit_fn):
            return False
        if not self.verify(verify_fn=verify_fn):
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "transaction_id": self.transaction_id,
            "stage": self.stage.value,
            "actions_count": len(self.actions),
            "shadow_snapshots_count": len(self.shadow_snapshots),
            "compensations_count": len(self.compensations),
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
