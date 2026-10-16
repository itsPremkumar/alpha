from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient
from pydantic import ValidationError

from alpha.runtime.events.store.memory import MemoryRunEventStore
from alpha.runtime.runs.manager import RunManager
from alpha.runtime.runs.schemas import RunStatus
from alpha.runtime.runs.store.memory import MemoryRunStore
from alpha.runtime.runs.verification import verify_acceptance_criteria
from app.gateway.routers import thread_runs
from app.gateway.routers.evolution import GateRequest
from app.gateway.run_models import RunCreateRequest

READBACK_CRITERION = 'Read the file back and verify it contains exactly "expected marker".'
UNSUPPORTED_CRITERION = "The implementation is production ready"
THREAD = "thread-acceptance"
OWNER = "user-acceptance"


def _criteria(*descriptions: str) -> list[dict[str, str]]:
    return [{"id": f"criterion-{index + 1}", "description": description} for index, description in enumerate(descriptions)]


def test_acceptance_criteria_is_validated_and_serializable() -> None:
    request = RunCreateRequest(
        acceptance_criteria=[
            {
                "id": "tests-pass",
                "description": "The targeted test suite passes.",
                "required_evidence_kinds": ["test_result"],
            }
        ]
    )

    assert request.acceptance_criteria[0].model_dump(mode="json") == {
        "id": "tests-pass",
        "description": "The targeted test suite passes.",
        "required_evidence_kinds": ["test_result"],
    }


def test_autonomous_mode_is_opt_in() -> None:
    assert RunCreateRequest().autonomous is False
    assert RunCreateRequest(autonomous=True).autonomous is True
    # Router-level gate request: omission must never grant autonomy.
    assert GateRequest().autonomous_mode is False
    assert GateRequest(autonomous_mode=True).autonomous_mode is True


def test_acceptance_criteria_rejects_duplicate_evidence_kinds() -> None:
    with pytest.raises(ValidationError, match="duplicates"):
        RunCreateRequest(acceptance_criteria=[{"id": "proof", "description": "proof", "required_evidence_kinds": ["test_result", "test_result"]}])


def test_acceptance_criteria_rejects_duplicate_ids() -> None:
    with pytest.raises(ValidationError, match="duplicate ids"):
        RunCreateRequest(
            acceptance_criteria=[
                {"id": "proof", "description": "first"},
                {"id": "proof", "description": "second"},
            ]
        )


def test_verification_requires_passing_evidence_for_every_required_kind() -> None:
    result = verify_acceptance_criteria(
        [{"id": "release", "required_evidence_kinds": ["test_result", "artifact_digest"]}],
        [
            {"criterion_id": "release", "kind": "test_result", "passed": True, "reference": "pytest://targeted"},
            {"criterion_id": "release", "kind": "artifact_digest", "passed": False, "reference": "sha256:bad"},
        ],
    )

    assert result.verified is False
    assert result.verdicts[0].reason == "Missing passing evidence: artifact_digest"


def test_verification_succeeds_with_independent_passing_evidence() -> None:
    result = verify_acceptance_criteria(
        [{"id": "release", "required_evidence_kinds": ["test_result", "artifact_digest"]}],
        [
            {"criterion_id": "release", "kind": "test_result", "passed": True, "reference": "pytest://targeted"},
            {"criterion_id": "release", "kind": "artifact_digest", "passed": True, "reference": "sha256:good"},
        ],
    )

    assert result.verified is True
    assert result.verdicts[0].evidence_references == ("pytest://targeted", "sha256:good")


def test_no_declared_criteria_is_not_a_vacuous_verification() -> None:
    """Nothing declared means nothing verified — never a vacuous ``True``."""
    result = verify_acceptance_criteria(None, None)
    assert result.verified is False
    assert result.verdicts == ()

    empty = verify_acceptance_criteria([], [{"criterion_id": "release", "kind": "test_result", "passed": True}])
    assert empty.verified is False
    assert empty.verdicts == ()
    # The evidence itself is disclosed rather than absorbed by a silent pass.
    assert any("not declared" in note for note in empty.notes)


def test_evidence_for_an_undeclared_criterion_is_disclosed_not_absorbed() -> None:
    """A typo'd criterion id cannot masquerade as coverage, and cannot vanish."""
    result = verify_acceptance_criteria(
        [{"id": "release", "required_evidence_kinds": ["test_result"]}],
        [
            {"criterion_id": "relaese", "kind": "test_result", "passed": True, "reference": "pytest://typo"},
            {"source": "no criterion id"},
        ],
    )

    assert result.verified is False
    assert result.verdicts[0].reason == "Missing passing evidence: test_result"
    assert result.verdicts[0].evidence_references == ()
    notes = " ".join(result.notes)
    assert "relaese" in notes, "evidence for an undeclared criterion must be named"
    assert "names no criterion" in notes


def test_evidence_for_one_criterion_does_not_verify_another() -> None:
    result = verify_acceptance_criteria(
        [
            {"id": "a", "required_evidence_kinds": ["test_result"]},
            {"id": "b", "required_evidence_kinds": ["test_result"]},
        ],
        [{"criterion_id": "a", "kind": "test_result", "passed": True, "reference": "pytest://a"}],
    )

    assert [verdict.verified for verdict in result.verdicts] == [True, False]
    assert result.verified is False
    assert result.verdicts[1].reason == "Missing passing evidence: test_result"


def test_only_an_exact_true_counts_as_passing_evidence() -> None:
    """A truthy non-boolean (``"true"``, ``1``) is not a measurement."""
    result = verify_acceptance_criteria(
        [{"id": "release", "required_evidence_kinds": ["test_result"]}],
        [{"criterion_id": "release", "kind": "test_result", "passed": "true", "reference": "pytest://string"}],
    )

    assert result.verified is False
    assert result.verdicts[0].evidence_references == ()


def test_conflicting_evidence_for_one_kind_fails_closed() -> None:
    """A passing row cannot hide a contradictory measurement for that kind."""
    result = verify_acceptance_criteria(
        [{"id": "release", "required_evidence_kinds": ["test_result"]}],
        [
            {"criterion_id": "release", "kind": "test_result", "passed": True, "reference": "pytest://pass"},
            {"criterion_id": "release", "kind": "test_result", "passed": False, "reference": "pytest://failure"},
        ],
    )

    assert result.verified is False
    assert result.verdicts[0].verified is False
    assert result.verdicts[0].reason == "Conflicting evidence: test_result; missing passing evidence: test_result"
    assert result.verdicts[0].evidence_references == ()


def test_a_reference_is_cited_provenance_not_verified_provenance() -> None:
    """The overlay records where the caller says the proof lives; it cannot
    check that it does. A reference must never substitute for the measurement,
    and a passing item's reference is carried through verbatim."""
    result = verify_acceptance_criteria(
        [{"id": "release", "required_evidence_kinds": ["test_result"]}],
        [
            {
                "criterion_id": "release",
                "kind": "test_result",
                "passed": True,
                "reference": "operator says the suite passed",
            }
        ],
    )

    assert result.verified is True
    assert result.verdicts[0].evidence_references == ("operator says the suite passed",)


def test_model_written_prose_is_not_acceptance_evidence() -> None:
    """A worker's own output rows can never satisfy a criterion.

    The swarm workers emit evidence rows that carry provenance only — the exact
    shape below (``source``/``role``/``model``, tool-receipt and bash-execution
    counts) — plus one row whose ``passed`` flag is False.  None of those is a
    measurement, so the criterion they are offered against stays unverified and
    contributes no reference.  This is the boundary the module docstring claims:
    a model's prose alone is never considered evidence.
    """
    result = verify_acceptance_criteria(
        [{"id": "release", "required_evidence_kinds": ["test_result"]}],
        [
            {"source": "bot:coder", "role": "coder", "model": "default"},
            {"source": "tool_receipts", "count": 3},
            {
                "criterion_id": "release",
                "kind": "test_result",
                "passed": False,
                "reference": "worker summary: the tests looked green",
            },
        ],
    )

    assert result.verified is False
    assert result.verdicts[0].verified is False
    assert result.verdicts[0].reason == "Missing passing evidence: test_result"
    assert result.verdicts[0].evidence_references == ()


def test_every_declared_criterion_receives_exactly_one_verdict() -> None:
    """Coverage is one verdict per declared criterion, in declared order.

    Repeated evidence rows for a kind that is already covered cannot fabricate
    a second verdict, and a criterion nobody supplied evidence for is refused
    individually rather than being averaged away by the aggregate.
    """
    result = verify_acceptance_criteria(
        [
            {"id": "alpha", "required_evidence_kinds": ["test_result"]},
            {"id": "beta", "required_evidence_kinds": ["artifact_digest"]},
        ],
        [
            {"criterion_id": "alpha", "kind": "test_result", "passed": True, "reference": "pytest://alpha"},
            {"criterion_id": "alpha", "kind": "test_result", "passed": True, "reference": "pytest://alpha-again"},
        ],
    )

    assert [verdict.criterion_id for verdict in result.verdicts] == ["alpha", "beta"]
    assert result.verdicts[0].verified is True
    assert result.verdicts[0].evidence_references == ("pytest://alpha", "pytest://alpha-again")
    assert result.verdicts[1].verified is False
    assert result.verdicts[1].reason == "Missing passing evidence: artifact_digest"
    assert result.verified is False


# ---------------------------------------------------------------------------
# End-to-end: one run request carrying acceptance_criteria -> a measurement
# retrievable at GET /api/threads/{thread_id}/runs/{run_id}/verification.
#
# Everything below drives the real pieces the Gateway uses — the typed request
# model, the metadata assembly `services.start_run` performs, the real
# RunManager over a MemoryRunStore, the real event store, and the real router —
# so a regression anywhere on that seam fails here rather than in production.
# ---------------------------------------------------------------------------


def test_run_request_accepts_at_most_32_acceptance_criteria() -> None:
    """32 is a ceiling, not a suggestion; the request must refuse rather than truncate."""
    at_limit = RunCreateRequest(acceptance_criteria=_criteria(*[f"Criterion {index}" for index in range(32)]))
    assert len(at_limit.acceptance_criteria) == 32

    with pytest.raises(ValidationError):
        RunCreateRequest(acceptance_criteria=_criteria(*[f"Criterion {index}" for index in range(33)]))


def _create_run_with_criteria(criteria: list[dict[str, Any]], event_store: MemoryRunEventStore) -> tuple[RunManager, MemoryRunStore, str]:
    """Assemble run metadata exactly as ``services.start_run`` does, then create it.

    The metadata assembly is inlined on purpose: it is the load-bearing step that
    turns a validated request field into the persisted contract the verification
    endpoint reads back. Copying it here is what makes a break in that shape
    visible, and `test_run_metadata_keeps_acceptance_criteria_out_of_caller_metadata`
    pins the other half (arbitrary metadata cannot smuggle criteria in).
    """
    request = RunCreateRequest(acceptance_criteria=criteria)
    run_store = MemoryRunStore()
    manager = RunManager(store=run_store, event_store=event_store)

    async def _create() -> str:
        record = await manager.create(
            thread_id=THREAD,
            metadata={"acceptance_criteria": [criterion.model_dump(mode="json") for criterion in request.acceptance_criteria]},
            user_id=OWNER,
        )
        await manager.set_status(record.run_id, RunStatus.success)
        return record.run_id

    return manager, run_store, asyncio.run(_create())


def _make_app(run_manager: RunManager, event_store: MemoryRunEventStore) -> Any:
    app = make_authed_test_app()
    app.include_router(thread_runs.router)
    app.state.run_manager = run_manager
    app.state.run_event_store = event_store
    return app


def _seed_readback_evidence(event_store: MemoryRunEventStore, run_id: str, *, content: str = "expected marker") -> None:
    """A write followed by a read of the same path, both as real tool events."""

    async def _write() -> None:
        await event_store.put(
            thread_id=THREAD,
            run_id=run_id,
            event_type="llm.ai.response",
            category="message",
            content={"type": "ai", "tool_calls": [{"id": "write-1", "name": "write_file", "args": {"path": "/workspace/result.txt", "content": content}}]},
            metadata={},
        )
        await event_store.put(thread_id=THREAD, run_id=run_id, event_type="llm.tool.result", category="message", content={"name": "write_file", "tool_call_id": "write-1", "status": "success"}, metadata={})
        await event_store.put(
            thread_id=THREAD,
            run_id=run_id,
            event_type="llm.ai.response",
            category="message",
            content={"type": "ai", "tool_calls": [{"id": "read-1", "name": "read_file", "args": {"path": "/workspace/result.txt"}}]},
            metadata={},
        )
        await event_store.put(thread_id=THREAD, run_id=run_id, event_type="llm.tool.result", category="message", content={"name": "read_file", "tool_call_id": "read-1", "status": "success", "content": content}, metadata={})

    asyncio.run(_write())


def _seed_auto_test_evidence(event_store: MemoryRunEventStore, run_id: str, *, status: str = "passed", exit_code: int = 0) -> None:
    report = {
        "status": status,
        "execution": "argv",
        "command": ["python", "-m", "pytest"],
        "exit_code": exit_code,
        "failure_count": 0 if exit_code == 0 else 1,
    }

    async def _write() -> None:
        await event_store.put(
            thread_id=THREAD,
            run_id=run_id,
            event_type="llm.ai.response",
            category="message",
            content={"type": "ai", "tool_calls": [{"id": "tests-1", "name": "auto_test_and_repair", "args": {}}]},
            metadata={},
        )
        await event_store.put(
            thread_id=THREAD,
            run_id=run_id,
            event_type="llm.tool.result",
            category="message",
            content={"name": "auto_test_and_repair", "tool_call_id": "tests-1", "status": "success", "content": json.dumps(report)},
            metadata={},
        )

    asyncio.run(_write())


def _verification(app: Any, run_id: str) -> dict[str, Any]:
    with TestClient(app) as client:
        response = client.get(f"/api/threads/{THREAD}/runs/{run_id}/verification")
    assert response.status_code == 200
    return response.json()


def test_a_run_request_with_readback_criteria_verifies_and_stays_idempotent() -> None:
    event_store = MemoryRunEventStore()
    run_manager, run_store, run_id = _create_run_with_criteria(_criteria(READBACK_CRITERION), event_store)
    _seed_readback_evidence(event_store, run_id)
    app = _make_app(run_manager, event_store)

    first = _verification(app, run_id)
    second = _verification(app, run_id)
    third = _verification(app, run_id)

    assert first["status"] == "verified"
    assert first["report"]["evaluator"] == "run_tool_readback"
    assert first["report"]["criteria"][0]["verdict"] == "met"
    # Idempotent: the first complete measurement is persisted and replayed, so a
    # second and third read are byte-identical rather than a second opinion.
    assert second == first
    assert third == first

    persisted = asyncio.run(event_store.list_events(THREAD, run_id, event_types=["run.verification"], limit=10))
    assert len(persisted) == 1, "the verdict must be measured once and reused, not recomputed per read"
    assert persisted[0]["category"] == "verification"
    assert persisted[0]["metadata"]["status"] == "verified"

    # The contract is durable: the run row itself kept it, so a restart that
    # loses the in-memory registry still has something to verify against.
    rehydrated = RunManager(store=run_store)
    stored = asyncio.run(rehydrated.get(run_id, user_id=OWNER))
    assert stored.metadata["acceptance_criteria"] == [{"id": "criterion-1", "description": READBACK_CRITERION, "required_evidence_kinds": []}]


def test_auto_detected_test_receipt_verifies_at_the_run_endpoint_and_is_persisted() -> None:
    event_store = MemoryRunEventStore()
    criterion = "Auto-detected project test suite passes."
    run_manager, _run_store, run_id = _create_run_with_criteria(_criteria(criterion), event_store)
    _seed_auto_test_evidence(event_store, run_id)
    app = _make_app(run_manager, event_store)

    body = _verification(app, run_id)
    replay = _verification(app, run_id)

    assert body["status"] == "verified"
    assert body["report"]["evaluator"] == "run_auto_test"
    assert "exit_code=0" in body["report"]["criteria"][0]["evidence"]
    assert replay == body
    persisted = asyncio.run(event_store.list_events(THREAD, run_id, event_types=["run.verification"], limit=10))
    assert len(persisted) == 1


def test_auto_detected_test_failure_is_a_failed_run_acceptance_measurement() -> None:
    event_store = MemoryRunEventStore()
    criterion = "Auto-detected project test suite passes."
    run_manager, _run_store, run_id = _create_run_with_criteria(_criteria(criterion), event_store)
    _seed_auto_test_evidence(event_store, run_id, status="failed", exit_code=1)
    app = _make_app(run_manager, event_store)

    body = _verification(app, run_id)

    assert body["status"] == "failed"
    assert body["report"]["criteria"][0]["verdict"] == "not_met"


def test_a_readback_that_does_not_contain_the_marker_fails_instead_of_passing() -> None:
    event_store = MemoryRunEventStore()
    run_manager, _run_store, run_id = _create_run_with_criteria(_criteria(READBACK_CRITERION), event_store)
    _seed_readback_evidence(event_store, run_id, content="something else entirely")
    app = _make_app(run_manager, event_store)

    body = _verification(app, run_id)

    assert body["status"] == "failed"
    assert body["report"]["criteria"][0]["verdict"] == "not_met"


def test_run_endpoint_measures_explicit_artifact_readback_and_persists_digest() -> None:
    criterion = "File artifact at `/workspace/result.txt` exists and is non-empty."
    event_store = MemoryRunEventStore()
    run_manager, _run_store, run_id = _create_run_with_criteria(_criteria(criterion), event_store)
    _seed_readback_evidence(event_store, run_id)
    app = _make_app(run_manager, event_store)

    body = _verification(app, run_id)
    replay = _verification(app, run_id)

    assert body["status"] == "verified"
    assert body["report"]["evaluator"] == "run_artifact_readback"
    assert body["report"]["criteria"][0]["verdict"] == "met"
    assert "sha256=" in body["report"]["criteria"][0]["evidence"]
    assert replay == body

    persisted = asyncio.run(event_store.list_events(THREAD, run_id, event_types=["run.verification"], limit=10))
    assert len(persisted) == 1


def test_an_unsupported_criterion_among_supported_ones_never_reports_verified() -> None:
    """One criterion the auto-verifier cannot measure poisons the whole verdict.

    The alternative is partial reporting: a `verified` for the readback row
    beside a silently dropped prose criterion, which reads as a clean sweep.
    """
    event_store = MemoryRunEventStore()
    run_manager, _run_store, run_id = _create_run_with_criteria(_criteria(READBACK_CRITERION, UNSUPPORTED_CRITERION), event_store)
    _seed_readback_evidence(event_store, run_id)
    app = _make_app(run_manager, event_store)

    body = _verification(app, run_id)

    assert body["status"] == "unverified"
    assert body["report"] is None
    assert "beyond the supported exact-text, explicit artifact readback, or auto-detected project test checks" in body["reason"]


def test_an_unsupported_criterion_alone_stays_unverified_without_evidence() -> None:
    event_store = MemoryRunEventStore()
    run_manager, _run_store, run_id = _create_run_with_criteria(_criteria(UNSUPPORTED_CRITERION), event_store)
    _seed_readback_evidence(event_store, run_id)
    app = _make_app(run_manager, event_store)

    body = _verification(app, run_id)

    assert body["status"] == "unverified"
    assert body["report"] is None


def test_no_criteria_is_reported_as_not_requested_not_as_a_pass() -> None:
    event_store = MemoryRunEventStore()
    run_manager, _run_store, run_id = _create_run_with_criteria([], event_store)
    _seed_readback_evidence(event_store, run_id)
    app = _make_app(run_manager, event_store)

    body = _verification(app, run_id)

    assert body["status"] == "not_requested"
    assert body["report"] is None


def test_a_running_run_is_pending_and_a_failed_run_is_never_verified() -> None:
    """A terminal status is not acceptance, in either direction."""
    event_store = MemoryRunEventStore()
    run_manager, _run_store, run_id = _create_run_with_criteria(_criteria(READBACK_CRITERION), event_store)
    _seed_readback_evidence(event_store, run_id)
    app = _make_app(run_manager, event_store)

    async def _set(status: RunStatus, error: str | None = None) -> None:
        await run_manager.set_status(run_id, status, error=error)

    asyncio.run(_set(RunStatus.running))
    running = _verification(app, run_id)
    assert running["status"] == "pending"
    assert running["report"] is None

    asyncio.run(_set(RunStatus.error, error="the provider refused"))
    failed = _verification(app, run_id)
    assert failed["status"] == "unverified"
    assert failed["report"] is None
    assert "not a successful terminal result" in failed["reason"]


def test_verification_is_owner_scoped_and_thread_scoped() -> None:
    event_store = MemoryRunEventStore()
    run_manager, _run_store, run_id = _create_run_with_criteria(_criteria(READBACK_CRITERION), event_store)
    _seed_readback_evidence(event_store, run_id)
    app = _make_app(run_manager, event_store)

    with TestClient(app) as client:
        other_thread = client.get(f"/api/threads/some-other-thread/runs/{run_id}/verification")
        missing_run = client.get(f"/api/threads/{THREAD}/runs/does-not-exist/verification")

    assert other_thread.status_code == 404
    assert missing_run.status_code == 404
