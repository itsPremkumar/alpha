"""Call-site proofs for wave P3 (dormant wiring: alpha.evidence + alpha.ledger).

P3's goal (master plan G5) was to give the two dormant packages REAL consumers:

* ``alpha.ledger`` (ActionLedger/ActionReceipt) — the ESTOP action path now
  records an intent before the mutation and a receipt after it, with the real
  outcome and error category; the pair is readable back from the on-disk ledger.
* ``alpha.evidence`` (EvidenceStore) — the Finish-First verifier middleware now
  records the real violation event as observation evidence.

These tests pin the wiring itself: the bytes land on disk, the records read
back through the real store APIs, and neither call site can break its host when
the side-channel is unavailable (the action still runs, the notice is still
injected).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from alpha.evidence import EvidenceStore
from alpha.ledger import ActionLedger


@pytest.fixture()
def isolated_runtime_home(tmp_path: Path, monkeypatch) -> Path:
    """Point every ``runtime_home()`` consumer at an isolated directory."""
    import alpha.config.runtime_paths as runtime_paths

    monkeypatch.setattr(runtime_paths, "runtime_home", lambda *a, **k: tmp_path)
    return tmp_path


# ── alpha.ledger: ESTOP action receipts ───────────────────────────────


def test_estop_engage_writes_a_real_intent_and_success_receipt(isolated_runtime_home: Path, monkeypatch):
    from alpha.tools.builtins import estop_tool

    engaged: list[str] = []
    fake_manager = type(
        "FakeManager",
        (),
        {
            "get_status": lambda self: {"is_engaged": False},
            "engage": lambda self, reason=None: engaged.append(reason or "") or "C:/state/estop.sentinel",
            "disengage": lambda self: True,
        },
    )()
    monkeypatch.setattr(estop_tool, "get_estop_manager", lambda: fake_manager)

    output = estop_tool.emergency_stop_manage.invoke({"action": "engage", "reason": "runaway loop"})
    assert "successfully engaged" in output
    assert engaged == ["runaway loop"]

    ledger = ActionLedger(isolated_runtime_home / "action-ledger")
    intents = ledger.list_intents("runtime", tool_name="emergency_stop_manage")
    assert len(intents) == 1
    # A receipt closes the intent by setting its status to the receipt's
    # OUTCOME (ActionLedger._index: status=receipt.outcome), not a generic
    # "completed" — so a successful action leaves the intent "succeeded".
    assert intents[0].status == "succeeded"

    receipts = ledger.list_receipts("runtime", intent_id=intents[0].id, outcome="succeeded")
    assert len(receipts) == 1
    assert receipts[0].outcome == "succeeded"
    assert receipts[0].exit_ref is not None
    assert receipts[0].completed_at >= receipts[0].started_at
    # Real bytes on disk, not just in-memory state.
    assert (isolated_runtime_home / "action-ledger").is_dir()


def test_estop_disengage_failure_records_a_failed_receipt(isolated_runtime_home: Path, monkeypatch):
    from alpha.tools.builtins import estop_tool

    fake_manager = type(
        "FakeManager",
        (),
        {
            "get_status": lambda self: {"is_engaged": True},
            "engage": lambda self, reason=None: "sentinel",
            "disengage": lambda self: False,  # filesystem error path
        },
    )()
    monkeypatch.setattr(estop_tool, "get_estop_manager", lambda: fake_manager)

    output = estop_tool.emergency_stop_manage.invoke({"action": "disengage"})
    assert "Failed to disengage" in output

    ledger = ActionLedger(isolated_runtime_home / "action-ledger")
    failed_intents = ledger.list_intents("runtime", tool_name="emergency_stop_manage", status="failed")
    assert len(failed_intents) == 1
    receipts = ledger.list_receipts("runtime", intent_id=failed_intents[0].id, outcome="failed")
    assert len(receipts) == 1
    assert receipts[0].outcome == "failed"
    assert receipts[0].error_category == "internal"


def test_estop_status_writes_no_receipts(isolated_runtime_home: Path, monkeypatch):
    from alpha.tools.builtins import estop_tool

    fake_manager = type(
        "FakeManager",
        (),
        {"get_status": lambda self: {"is_engaged": False}, "engage": lambda self, reason=None: "s", "disengage": lambda self: True},
    )()
    monkeypatch.setattr(estop_tool, "get_estop_manager", lambda: fake_manager)

    status = estop_tool.emergency_stop_manage.invoke({"action": "status"})
    assert json.loads(status)["is_engaged"] is False
    assert ActionLedger(isolated_runtime_home / "action-ledger").list_receipts("runtime") == []


def test_ledger_failure_never_breaks_the_estop_action(isolated_runtime_home: Path, monkeypatch):
    """If the receipt side-channel is broken, the action still runs and still
    reports its real result — bookkeeping never becomes the failure mode."""
    from alpha.ledger import store as ledger_store
    from alpha.tools.builtins import estop_tool

    class BrokenLedger:
        def record_intent(self, *a, **k):
            raise OSError("ledger disk unavailable")

        def record_receipt(self, *a, **k):
            raise OSError("ledger disk unavailable")

    monkeypatch.setattr(ledger_store, "default_action_ledger", lambda: BrokenLedger())
    fake_manager = type(
        "FakeManager",
        (),
        {
            "get_status": lambda self: {"is_engaged": False},
            "engage": lambda self, reason=None: "C:/state/estop.sentinel",
            "disengage": lambda self: True,
        },
    )()
    monkeypatch.setattr(estop_tool, "get_estop_manager", lambda: fake_manager)

    output = estop_tool.emergency_stop_manage.invoke({"action": "engage", "reason": "x"})
    assert "successfully engaged" in output


# ── alpha.evidence: Finish-First evidence ────────────────────────────


def test_finish_first_violation_is_recorded_as_observation_evidence(isolated_runtime_home: Path):
    from langchain_core.messages import AIMessage

    from alpha.agents.middlewares.finish_first_verifier_middleware import (
        _record_finish_first_evidence,
    )

    _record_finish_first_evidence(AIMessage(content="done!", id="msg-1"), code_writes=1)

    store = EvidenceStore(isolated_runtime_home / "evidence")
    records = store.list_evidence("runtime")
    assert len(records) == 1
    assert records[0].kind == "observation"
    assert "finish-first:msg-1" in records[0].ref
    assert "1 code write(s)" in records[0].summary
    assert records[0].created_at > 0
    assert "unverified_completion" in records[0].tags


def test_finish_first_evidence_failure_is_swallowed(isolated_runtime_home: Path, monkeypatch):
    """An evidence-store failure must never propagate into the turn."""
    from langchain_core.messages import AIMessage

    import alpha.evidence.store as evidence_store
    from alpha.agents.middlewares.finish_first_verifier_middleware import (
        _record_finish_first_evidence,
    )

    class BrokenStore:
        def add_evidence(self, *a, **k):
            raise OSError("evidence disk unavailable")

    monkeypatch.setattr(evidence_store, "default_evidence_store", lambda: BrokenStore())
    _record_finish_first_evidence(AIMessage(content="x", id="m"), code_writes=0)  # must not raise
