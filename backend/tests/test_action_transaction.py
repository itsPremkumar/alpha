import tempfile
from pathlib import Path

from alpha.action import (
    ActionPrimitive,
    ActionRequest,
    ActionTransaction,
    TransactionStage,
)


def test_action_transaction_successful_lifecycle():
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "config.json"
        test_file.write_text('{"version": 1}', encoding="utf-8")

        tx = ActionTransaction()
        tx.add_action(
            ActionRequest(
                primitive=ActionPrimitive.UPDATE,
                target=str(test_file),
                parameters={"new_content": '{"version": 2}'},
            )
        )

        def commit_fn(actions):
            test_file.write_text('{"version": 2}', encoding="utf-8")
            return True

        def verify_fn():
            return '{"version": 2}' in test_file.read_text(encoding="utf-8")

        success = tx.execute_lifecycle(commit_fn=commit_fn, verify_fn=verify_fn)
        assert success is True
        assert tx.stage == TransactionStage.VERIFIED
        assert '{"version": 2}' in test_file.read_text(encoding="utf-8")


def test_action_transaction_rollback_on_failed_verification():
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = Path(tmpdir) / "critical.py"
        original_code = "def add(a, b): return a + b\n"
        test_file.write_text(original_code, encoding="utf-8")

        tx = ActionTransaction()
        tx.add_action(
            ActionRequest(
                primitive=ActionPrimitive.UPDATE,
                target=str(test_file),
            )
        )

        def commit_fn(actions):
            # Introduce breaking syntax error
            test_file.write_text("def broken(:syntax_error", encoding="utf-8")
            return True

        def failing_verify_fn():
            # Verification gate rejects broken syntax
            return False

        success = tx.execute_lifecycle(commit_fn=commit_fn, verify_fn=failing_verify_fn)
        assert success is False
        assert tx.stage == TransactionStage.ROLLED_BACK
        # The file MUST be restored to its original pristine state
        assert test_file.read_text(encoding="utf-8") == original_code


def test_action_transaction_rollback_on_created_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        new_file = Path(tmpdir) / "temp_payload.txt"
        assert not new_file.exists()

        tx = ActionTransaction()
        tx.add_action(
            ActionRequest(
                primitive=ActionPrimitive.CREATE,
                target=str(new_file),
            )
        )

        def commit_fn(actions):
            new_file.write_text("temporary data", encoding="utf-8")
            return True

        def failing_verify_fn():
            return False

        success = tx.execute_lifecycle(commit_fn=commit_fn, verify_fn=failing_verify_fn)
        assert success is False
        assert tx.stage == TransactionStage.ROLLED_BACK
        # The newly created file must be cleaned up on rollback
        assert not new_file.exists()
