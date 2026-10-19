"""The harness-improvement REST surface: posture, admission, degraded states.

``alpha.harness_improvement`` can run a full improvement cycle, build an
immutable bundle tree and gate a promotion; until ``app/gateway/routers/harness.py``
existed, none of that was reachable over HTTP. These tests pin the properties the
router's own docstring promises, because each one is a way the surface could
quietly start lying:

1. **Absence is machine-readable, not prose.** ``GET /status`` answers even while
   the feature is disabled and carries a ``harness_capability`` row
   (``state: absent``, ``executes: false``) naming the four wiring points a
   deployment would have to add. Starting a run refuses with the same list
   instead of parking a run that could never progress.
2. **A gate that did not run is not a gate that passed.** ``approve`` refuses a
   candidate whose regression suite was never measured (the production posture),
   and ``activate`` refuses until an explicit operator approval row exists. The
   router never converts a click into a skipped gate.
3. **An unreadable store is a degraded state, never an empty ``200``.** A corrupt
   pointer refuses a start with ``503``; a corrupt bundle manifest answers
   ``503`` on its detail route and is *named* in ``unreadable`` on the list
   (which stays ``200``, because the listing itself succeeded).
4. **Reads outlive the kill switch; the one mutation that must is ``cancel``.**
   Refusing to record a stop request would leave an operator unable to halt a run
   a previous configuration started.

The engine is bound to a temporary store (``ALPHA_HOME``) and the router's
``_engine`` seam is patched, so one ``TestClient`` per test drives the real state
machine against scripted actors — no model call, no network. The engine-level
semantics themselves (checkpointing, contrasts, composition) are pinned by
``test_harness_improvement_engine.py``; this file is about the *HTTP* contract.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alpha.harness_improvement import (
    CandidateScope,
    ComponentDiff,
    ComponentKind,
    ComponentOp,
    DeliverableCheck,
    ExperienceMetadata,
    ExperienceStatus,
    FailureHypothesis,
    HarnessImprovementEngine,
    HarnessImprovementSettings,
    HarnessImprovementStore,
    HarnessProposal,
    ImprovementStage,
    ProbeOutcome,
    ScriptedEditor,
    TaskContract,
    TokenUsage,
    ValidatorResult,
    ValidatorStatus,
    activate,
    build_bundle,
    compute_task_fingerprint,
    save_experience,
)
from alpha.harness_improvement.config import HarnessImprovementConfig
from app.gateway.auth_disabled import AUTH_SOURCE_PAT, AUTH_SOURCE_SESSION
from app.gateway.routers import harness as harness_router

TASK_TEXT = "Create a JSON configuration and confirm it is valid"
TASK_OUTCOME = "write outputs/report.json and verify it parses"

#: ``HarnessImprovementStore()`` resolves exactly here once ``ALPHA_HOME`` points
#: at the test's tmp dir — the read routes construct the store with no arguments.
STORE_DIRNAME = "harness_improvement"

#: Reused by the engine suite for the same reason: a real-world-shaped key the
#: redaction screen recognises, never a live credential.
SECRET = "sk-abcdefgh12345678"

#: A valid run request; the sweep tests only ever need it to be well-formed,
#: because admission must refuse before the body is acted on.
_RUN_BODY: dict[str, Any] = {
    "task": TASK_TEXT,
    "outcome": TASK_OUTCOME,
    "deliverables": ["outputs/report.json"],
    "checks": [{"name": "report.parses", "path": "outputs/report.json", "kind": "parses_json"}],
    "domain_tags": ["artifact"],
    "task_type": "artifact",
    "run_id": "run-http",
}

_MISSING_BUNDLE = "sha256:" + "0" * 64

#: Every declared route with a concrete request. The inventory pins the declared
#: set, and both admission sweeps walk this table so a new route cannot skip it.
_REQUESTS: tuple[tuple[str, str, dict[str, Any] | None], ...] = (
    ("GET", "/api/harness/status", None),
    ("GET", "/api/harness/runs", None),
    ("POST", "/api/harness/runs", _RUN_BODY),
    ("GET", "/api/harness/runs/run-x", None),
    ("GET", "/api/harness/runs/run-x/events", None),
    ("POST", "/api/harness/runs/run-x/resume", None),
    ("POST", "/api/harness/runs/run-x/cancel", {"reason": "stop"}),
    ("GET", "/api/harness/bundles", None),
    ("GET", f"/api/harness/bundles/{_MISSING_BUNDLE}", None),
    ("GET", f"/api/harness/bundles/{_MISSING_BUNDLE}/diff", None),
    ("GET", "/api/harness/experiences", None),
    ("GET", "/api/harness/experiences/exp-1", None),
    ("POST", "/api/harness/experiences/maintain", {"apply": False}),
    ("GET", "/api/harness/promotions", None),
    ("POST", f"/api/harness/candidates/{_MISSING_BUNDLE}/approve", {"reason": "", "approvals": []}),
    ("POST", f"/api/harness/candidates/{_MISSING_BUNDLE}/reject", {"reason": ""}),
    ("POST", f"/api/harness/active/{_MISSING_BUNDLE}/activate", {"reason": ""}),
    ("POST", f"/api/harness/active/{_MISSING_BUNDLE}/rollback", {"reason": ""}),
    ("GET", "/api/harness/budgets", None),
)

#: The mutation routes only — every one is admin-gated.
_MUTATIONS: tuple[tuple[str, str, dict[str, Any] | None], ...] = tuple(row for row in _REQUESTS if row[0] == "POST")

#: The route set exactly as declared (path template, sorted methods). Equality,
#: not containment: an accidentally-added debug route must fail here too.
_EXPECTED_ROUTES: set[tuple[str, tuple[str, ...]]] = {
    ("/api/harness/status", ("GET",)),
    ("/api/harness/runs", ("GET",)),
    ("/api/harness/runs", ("POST",)),
    ("/api/harness/runs/{run_id}", ("GET",)),
    ("/api/harness/runs/{run_id}/events", ("GET",)),
    ("/api/harness/runs/{run_id}/resume", ("POST",)),
    ("/api/harness/runs/{run_id}/cancel", ("POST",)),
    ("/api/harness/bundles", ("GET",)),
    ("/api/harness/bundles/{bundle_id}", ("GET",)),
    ("/api/harness/bundles/{bundle_id}/diff", ("GET",)),
    ("/api/harness/experiences", ("GET",)),
    ("/api/harness/experiences/{experience_id}", ("GET",)),
    ("/api/harness/experiences/maintain", ("POST",)),
    ("/api/harness/promotions", ("GET",)),
    ("/api/harness/candidates/{bundle_id}/approve", ("POST",)),
    ("/api/harness/candidates/{bundle_id}/reject", ("POST",)),
    ("/api/harness/active/{bundle_id}/activate", ("POST",)),
    ("/api/harness/active/{bundle_id}/rollback", ("POST",)),
    ("/api/harness/budgets", ("GET",)),
}


# ------------------------------------------------------------------ scripted actors


def _task() -> TaskContract:
    """The same contract the HTTP body builds, for engine-driven seeding."""
    return TaskContract(
        task=TASK_TEXT,
        outcome=TASK_OUTCOME,
        deliverables=("outputs/report.json",),
        checks=(DeliverableCheck(name="report.parses", path="outputs/report.json", kind="parses_json"),),
        domain_tags=("artifact",),
        task_type="artifact",
        task_fingerprint=compute_task_fingerprint(TASK_TEXT),
    )


def _baseline_components() -> dict[str, tuple[ComponentKind, str]]:
    return {
        "system_prompt/base": (ComponentKind.SYSTEM_PROMPT, "You are Alpha."),
        "tool_guidance/bash": (ComponentKind.TOOL_GUIDANCE, "Use bash carefully."),
    }


class _DemoExecutor:
    """Role-keyed executor writing real artifacts into each probe workspace.

    ``baseline_fails`` decides which arm is broken: the baseline writes
    unparsable JSON, every other role writes valid JSON with identical process
    metrics, so the contrast the engine builds is driven by the script.
    """

    def __init__(self, *, baseline_fails: bool = True) -> None:
        self.baseline_fails = baseline_fails
        self.calls: list[str] = []

    async def execute(self, *, run_id: str, role: str, task: Any, bundle: Any, contents: Any, environment: Any) -> ProbeOutcome:
        self.calls.append(role)
        failed = role == "baseline" and self.baseline_fails
        report = task.workspace.outputs_dir / "report.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("{not-json" if failed else '{"status":"ok"}', encoding="utf-8")
        return ProbeOutcome(
            run_id=f"{run_id}:{role}",
            bundle_id=bundle.bundle_id,
            candidate_id="",
            task_fingerprint="",
            trace_artifact=f"{task.workspace.root}/logs/trace.json",
            deliverable_refs=("outputs/report.json",),
            validator_results=(ValidatorResult(name="report.parses", status=ValidatorStatus.FAILED if failed else ValidatorStatus.PASSED, detail="scripted"),),
            token_usage=TokenUsage(input_tokens=10, output_tokens=5, cost_usd=None),
            elapsed_ms=5,
            tool_error_count=2 if failed else 0,
            model_turns=3,
            tool_calls=4,
        )


def _invariant_proposal(context: Any) -> HarnessProposal:
    return HarnessProposal(
        proposal_id="p-invariant",
        scope=CandidateScope.INVARIANT,
        base_bundle_id=context.base_bundle.bundle_id,
        hypothesis=FailureHypothesis(
            summary="reopen artifacts before announcing completion",
            trace_event_ids=("baseline.completed",),
            expected_behavioral_change="the artifact parses at completion time",
            falsification_condition="the artifact is still malformed when the run finishes",
        ),
        component_diffs=(
            ComponentDiff(
                op=ComponentOp.MODIFY,
                kind=ComponentKind.SYSTEM_PROMPT,
                component_id="system_prompt/base",
                content="Always reopen and parse each deliverable before announcing completion.",
                rationale="baseline announced completion without verifying the file",
                evidence_event_ids=("baseline.completed",),
            ),
        ),
    )


def _domain_proposal(context: Any) -> HarnessProposal:
    return HarnessProposal(
        proposal_id="p-domain",
        scope=CandidateScope.TASK_OR_DOMAIN,
        base_bundle_id=context.base_bundle.bundle_id,
        hypothesis=FailureHypothesis(
            summary="add an on-demand artifact verification skill",
            trace_event_ids=("baseline.completed",),
            expected_behavioral_change="artifact tasks verify their JSON output",
            falsification_condition="the successor still writes unparsable JSON",
        ),
        component_diffs=(
            ComponentDiff(
                op=ComponentOp.ADD,
                kind=ComponentKind.SKILL,
                component_id="skills/artifact-verification",
                content="# Artifact verification\nReopen and parse each deliverable after writing it.\n",
                rationale="deterministic reopen check",
                evidence_event_ids=("baseline.completed",),
            ),
        ),
    )


# ------------------------------------------------------------------------- helpers


def _engine_settings(**overrides: Any) -> HarnessImprovementSettings:
    payload: dict[str, Any] = {"enabled": True, "max_model_calls": 64}
    payload.update(overrides)
    return HarnessImprovementSettings(**payload)


def _build_engine(tmp_path: Path, *, executor: Any = "demo", editor: Any = "default", baseline: bool = True, **settings: Any) -> HarnessImprovementEngine:
    """One engine bound to the tmp store; ``executor=None/'absent'`` pins the absent posture."""
    if executor == "demo":
        executor = _DemoExecutor()
    if editor == "default":
        editor = ScriptedEditor(proposals={CandidateScope.INVARIANT: _invariant_proposal, CandidateScope.TASK_OR_DOMAIN: _domain_proposal})
    return HarnessImprovementEngine(
        store=HarnessImprovementStore(tmp_path / STORE_DIRNAME),
        settings=_engine_settings(**settings),
        executor=executor if executor != "absent" else None,
        editor=editor if editor != "absent" else None,
        baseline_provider=_baseline_components if baseline else None,
    )


class _Holder:
    """A mutable principal, so one app + one client can wear every identity."""

    def __init__(self) -> None:
        self.user: object | None = None
        self.auth_source: str | None = None


def _app(holder: _Holder) -> FastAPI:
    """A bare app: the router plus the principal injection AuthMiddleware does."""
    app = FastAPI()

    @app.middleware("http")
    async def _inject(request: Any, call_next: Any) -> Any:
        if holder.user is not None:
            request.state.user = holder.user
        if holder.auth_source is not None:
            request.state.auth_source = holder.auth_source
        return await call_next(request)

    app.include_router(harness_router.router)
    return app


def _member(uid: str) -> SimpleNamespace:
    # The shape production sends: ``system_role``, never an ``is_admin`` flag.
    return SimpleNamespace(id=uid, system_role="user")


def _admin(uid: str = "ops-admin") -> SimpleNamespace:
    return SimpleNamespace(id=uid, system_role="admin")


class _Lab:
    """One temporary store plus the two seams the router reads config through.

    The read routes build ``HarnessImprovementStore()`` with no arguments, so
    ``ALPHA_HOME`` is pointed at ``tmp_path`` and the injected engine uses the
    same directory. Config access is shimmed because ``get_app_config()`` stats
    the real ``config.yaml`` — and because the test wants to flip ``enabled``
    mid-test the way an operator edits the file.
    """

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tmp_path = tmp_path
        self.monkeypatch = monkeypatch
        self.enabled = True
        self.holder = _Holder()
        self.holder.user = _admin()
        self.holder.auth_source = AUTH_SOURCE_SESSION
        self.engine = _build_engine(tmp_path)
        monkeypatch.setenv("ALPHA_HOME", str(tmp_path))
        monkeypatch.setattr(harness_router, "_engine", lambda: self.engine)
        monkeypatch.setattr(harness_router, "get_app_config", self._app_config)

    def _app_config(self) -> SimpleNamespace:
        return SimpleNamespace(harness_improvement=HarnessImprovementConfig(enabled=self.enabled, max_model_calls=64))

    @property
    def store(self) -> HarnessImprovementStore:
        return HarnessImprovementStore(self.tmp_path / STORE_DIRNAME)

    @contextlib.contextmanager
    def client(self) -> Iterator[TestClient]:
        with TestClient(_app(self.holder)) as client:
            yield client


@pytest.fixture()
def lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Lab:
    return _Lab(tmp_path, monkeypatch)


def _start_cycle(client: TestClient, run_id: str = "run-http") -> dict[str, Any]:
    """POST one improvement run and return its durable record."""
    response = client.post("/api/harness/runs", json={**_RUN_BODY, "run_id": run_id})
    assert response.status_code == 200, response.text
    return response.json()["run"]


# ------------------------------------------------------------------ route inventory


class TestRouteInventory:
    def test_router_owns_its_own_prefix(self) -> None:
        assert harness_router.router.prefix == "/api/harness"

    def test_every_declared_route_is_present_and_nothing_else(self) -> None:
        declared = {(route.path, tuple(sorted(route.methods))) for route in harness_router.router.routes if hasattr(route, "methods")}
        assert declared == _EXPECTED_ROUTES

    def test_the_request_table_covers_every_route(self) -> None:
        assert len(_REQUESTS) == len(_EXPECTED_ROUTES)

    def test_router_is_mounted_in_the_gateway_app(self) -> None:
        source = Path("app/gateway/app.py").read_text(encoding="utf-8")
        assert "app.include_router(harness.router)" in source

    def test_router_is_declared_in_the_manifest(self) -> None:
        from alpha.workflow.registry.manifest_source import load_feature_manifest

        ids = {entry["id"] for entry in load_feature_manifest().get("routers", [])}
        assert "app.gateway.routers.harness" in ids


# ------------------------------------------------------------------------- admission


class TestAdmission:
    def test_every_route_declares_the_authentication_dependency(self) -> None:
        routes = [route for route in harness_router.router.routes if hasattr(route, "dependant")]
        assert routes, "the router declares no routes"
        for route in routes:
            names = {getattr(dependency.call, "__name__", None) for dependency in route.dependant.dependencies}
            assert "get_current_user_from_request" in names, route.path

    def test_an_unauthenticated_caller_is_refused_everywhere(self, lab: _Lab) -> None:
        lab.holder.user = None
        lab.holder.auth_source = None
        with lab.client() as client:
            for method, path, body in _REQUESTS:
                response = client.request(method, path, json=body)
                assert response.status_code == 401, f"{method} {path} -> {response.status_code}"

    def test_a_member_can_read_but_cannot_mutate_anything(self, lab: _Lab) -> None:
        lab.holder.user = _member("user-1")
        with lab.client() as client:
            for path in ("/api/harness/status", "/api/harness/runs", "/api/harness/bundles", "/api/harness/experiences", "/api/harness/promotions", "/api/harness/budgets"):
                assert client.get(path).status_code == 200, path
            for method, path, body in _MUTATIONS:
                response = client.request(method, path, json=body)
                assert response.status_code == 403, f"{method} {path} -> {response.status_code}"
                assert "administrator" in response.json()["detail"], path

    def test_a_pat_administrator_is_not_an_administrator(self, lab: _Lab) -> None:
        """The repository's one admin predicate suppresses admin for a PAT."""
        lab.holder.user = _admin()
        lab.holder.auth_source = AUTH_SOURCE_PAT
        with lab.client() as client:
            for method, path, body in _MUTATIONS:
                response = client.request(method, path, json=body)
                assert response.status_code == 403, f"{method} {path} -> {response.status_code}"
            # Reads still work: the PAT narrows admin capability, not reads.
            assert client.get("/api/harness/status").status_code == 200

    def test_an_administrator_without_an_id_cannot_mutate(self, lab: _Lab) -> None:
        """An improvement decision is never anonymous, even for an admin role."""
        lab.holder.user = SimpleNamespace(id="", system_role="admin")
        with lab.client() as client:
            response = client.post("/api/harness/runs/run-x/cancel", json={"reason": "stop"})
        assert response.status_code == 401
        assert response.json()["code"] == "unattributed_caller"


# ---------------------------------------------------------------- disabled posture


class TestDisabledPosture:
    def test_status_answers_while_disabled_and_names_the_missing_wiring(self, lab: _Lab) -> None:
        lab.enabled = False
        with lab.client() as client:
            payload = client.get("/api/harness/status").json()
        assert payload["enabled"] is False
        assert "harness_improvement.enabled" in payload["enabled_note"]
        capability = payload["harness_capability"]
        assert capability["state"] == "absent"
        assert capability["executes"] is False
        assert len(capability["missing_wiring"]) == 4
        points = " ".join(row["wiring_point"] for row in capability["missing_wiring"])
        for needle in ("HarnessExecutor", "HarnessEditor", "baseline_provider", "regression_suite"):
            assert needle in points, needle
        assert payload["store"]["readable"] is True
        assert str(lab.tmp_path) in payload["store"]["root"]

    def test_starting_and_resuming_refuse_by_name_while_disabled(self, lab: _Lab) -> None:
        lab.enabled = False
        with lab.client() as client:
            started = client.post("/api/harness/runs", json=_RUN_BODY)
            resumed = client.post("/api/harness/runs/run-x/resume")
        for response in (started, resumed):
            assert response.status_code == 503
            assert response.json()["code"] == "harness_improvement_disabled"
            assert "harness_improvement.enabled" in response.json()["detail"]

    def test_an_invalid_check_kind_is_a_client_error_even_while_disabled(self, lab: _Lab) -> None:
        """Input validation precedes the feature gate: a malformed request is 422, not 503."""
        lab.enabled = False
        with lab.client() as client:
            response = client.post("/api/harness/runs", json={**_RUN_BODY, "checks": [{"name": "c", "path": "outputs/x", "kind": "teleport"}]})
        assert response.status_code == 422
        assert response.json()["code"] == "invalid_check_kind"
        assert "parses_json" in response.json()["valid_kinds"]

    def test_cancel_is_reachable_while_disabled(self, lab: _Lab) -> None:
        """The one mutation the kill switch must not block: stopping a run a
        previous configuration started."""
        parked = _build_engine(lab.tmp_path, executor="absent", editor="absent")
        run = asyncio.run(parked.start(task=_task(), run_id="run-parked", created_by="ops-admin"))
        assert run.stage is ImprovementStage.BLOCKED
        lab.engine = parked
        lab.enabled = False

        with lab.client() as client:
            response = client.post("/api/harness/runs/run-parked/cancel", json={"reason": "halt"})
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["cancel_requested"] is True
            assert body["applied_at_next_boundary"] is True
            assert body["run"]["stage"] == "blocked", "cancel is a request, applied at the next boundary"
            # Resuming it is a *drive*, so it refuses while disabled.
            assert client.post("/api/harness/runs/run-parked/resume").status_code == 503
            # And the cancel is durable: the read route still reports it.
            assert client.get("/api/harness/runs/run-parked").json()["run"]["cancel_requested"] is True

    def test_approve_and_activate_refuse_by_name_while_disabled(self, lab: _Lab) -> None:
        """Approving and activating *advance* a candidate, so the kill switch
        blocks them exactly as it blocks a start — nothing may move the ladder
        while the subsystem is off."""
        with lab.client() as client:
            run = _start_cycle(client)
            lab.enabled = False
            approved = client.post(f"/api/harness/candidates/{run['successor_bundle_id']}/approve", json={"reason": "looks good"})
            activated = client.post(f"/api/harness/active/{run['successor_bundle_id']}/activate", json={})
        for response in (approved, activated):
            assert response.status_code == 503, response.text
            assert response.json()["code"] == "harness_improvement_disabled"
            assert "harness_improvement.enabled" in response.json()["detail"]
        assert lab.store.read_promotions().items == (), "a refused advance records nothing"

    def test_reject_and_rollback_stay_reachable_while_disabled(self, lab: _Lab) -> None:
        """Stop-or-restore writes survive the kill switch: an operator must be
        able to reject a candidate and restore a known-good bundle with the
        subsystem off, or disabling the feature traps a bad active bundle."""
        with lab.client() as client:
            run = _start_cycle(client)
            base_id = run["base_bundle_id"]
            successor_id = run["successor_bundle_id"]
            stored = {row["bundle_id"] for row in client.get("/api/harness/bundles").json()["bundles"]}
            candidate_id = next((bid for bid in sorted(stored) if bid not in {base_id, successor_id}), None)
            assert candidate_id is not None, "the cycle stores the accepted candidate bundles it validated"

            # Seed the pointer pair the way two operator activations would, then
            # flip the kill switch.
            assert activate(lab.engine.store, bundle_id=successor_id, actor="seed", reason="ship", decision=None)["activated"] is True
            assert activate(lab.engine.store, bundle_id=base_id, actor="seed", reason="second", decision=None)["activated"] is True
            lab.enabled = False

            rejected = client.post(f"/api/harness/candidates/{candidate_id}/reject", json={"reason": "not now"})
            assert rejected.status_code == 200, rejected.text
            assert rejected.json()["promotion_state"] == "rejected"

            rolled = client.post(f"/api/harness/active/{base_id}/rollback", json={"reason": "restore"})
            assert rolled.status_code == 200, rolled.text
            assert rolled.json()["restored_bundle_id"] == successor_id

            promotions = client.get("/api/harness/promotions").json()
            assert [row["event"] for row in promotions["events"]] == ["activated", "activated", "rejected", "rolled_back"]
            assert promotions["pointer"]["bundle_id"] == successor_id

    def test_reads_stay_reachable_while_disabled(self, lab: _Lab) -> None:
        lab.enabled = False
        with lab.client() as client:
            for path in ("/api/harness/runs", "/api/harness/bundles", "/api/harness/experiences", "/api/harness/promotions", "/api/harness/budgets"):
                assert client.get(path).status_code == 200, path
            assert client.get("/api/harness/runs/nope").status_code == 404


# ------------------------------------------------------------- enabled start refusals


def _seed_experience(
    store: HarnessImprovementStore,
    experience_id: str,
    *,
    created_at: str,
    component_ids: tuple[str, ...] = ("tool_guidance/bash",),
    finding: str = "benefit",
    expires_at: str | None = None,
) -> None:
    """Write one library entry directly; maintenance routes read what is stored."""
    save_experience(
        store,
        ExperienceMetadata(
            experience_id=experience_id,
            title=experience_id,
            lesson=experience_id,
            finding=finding,
            scope=CandidateScope.TASK_OR_DOMAIN,
            task_type="general",
            domain_tags=(),
            trigger_symptoms=(),
            applicable_conditions=(),
            not_applicable_to=(),
            component_ids=component_ids,
            component_kinds=(),
            evidence_refs=("run-1",),
            run_ids=("run-1",),
            benefit="",
            counter_evidence="",
            status=ExperienceStatus.ACTIVE,
            created_at=created_at,
            last_reviewed_at=created_at,
            review_condition="",
            expires_at=expires_at,
            compatibility={},
        ),
    )


class TestExperienceMaintenance:
    """The library-maintenance pass over HTTP (guide §16 Phase 7).

    Analysis is a read-like report; applying is the one route outside
    stop-or-restore that rewrites library records, so the kill switch blocks
    it with the run/advance writes.
    """

    def test_analysis_reports_the_plan_and_writes_nothing(self, lab: _Lab) -> None:
        _seed_experience(lab.store, "exp-old", created_at="2026-01-01T00:00:00Z")
        _seed_experience(lab.store, "exp-new", created_at="2026-06-01T00:00:00Z")
        with lab.client() as client:
            response = client.post("/api/harness/experiences/maintain", json={"apply": False})
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["apply"] is False
            assert body["applied"] == []
            assert any(row["experience_id"] == "exp-old" and row["action"] == "supersede" for row in body["plan"]["actions"])
            assert client.get("/api/harness/experiences/exp-old").json()["metadata"]["status"] == "active", "analysis is read-like"

    def test_apply_marks_entries_and_never_deletes_one(self, lab: _Lab) -> None:
        _seed_experience(lab.store, "exp-old", created_at="2026-01-01T00:00:00Z")
        _seed_experience(lab.store, "exp-new", created_at="2026-06-01T00:00:00Z")
        with lab.client() as client:
            response = client.post("/api/harness/experiences/maintain", json={"apply": True})
            assert response.status_code == 200, response.text
            applied = {row["experience_id"]: row["status"] for row in response.json()["applied"]}
            assert applied == {"exp-old": "applied"}
            statuses = {row["experience_id"]: row["status"] for row in client.get("/api/harness/experiences").json()["experiences"]}
            assert statuses == {"exp-new": "active", "exp-old": "superseded"}
            assert client.get("/api/harness/experiences/exp-old").json()["metadata"]["superseded_by"] == "exp-new"
        kept = sorted(path.name for path in (lab.tmp_path / STORE_DIRNAME / "experiences").iterdir())
        assert kept == ["exp-new", "exp-old"], "maintenance rewrites metadata; it never removes an entry"

    def test_analysis_stays_reachable_while_disabled_but_apply_refuses_by_name(self, lab: _Lab) -> None:
        _seed_experience(lab.store, "exp-expired", created_at="2026-01-01T00:00:00Z", expires_at="2026-01-02T00:00:00Z")
        lab.enabled = False
        with lab.client() as client:
            report = client.post("/api/harness/experiences/maintain", json={"apply": False})
            assert report.status_code == 200, report.text
            body = report.json()
            assert body["reviewed"] == 1
            assert any(row["action"] == "stale" for row in body["plan"]["actions"])
            applied = client.post("/api/harness/experiences/maintain", json={"apply": True})
        assert applied.status_code == 503
        assert applied.json()["code"] == "harness_improvement_disabled"
        assert "harness_improvement.enabled" in applied.json()["detail"]


class TestEnabledRefusals:
    def test_no_baseline_and_no_provider_is_409_with_the_wiring_list(self, lab: _Lab) -> None:
        lab.engine = _build_engine(lab.tmp_path, baseline=False)
        with lab.client() as client:
            response = client.post("/api/harness/runs", json=_RUN_BODY)
        assert response.status_code == 409, response.text
        body = response.json()
        assert body["code"] == "harness_baseline_absent"
        assert "refusing to invent a baseline" in body["detail"]
        assert len(body["missing_wiring"]) == 4
        assert lab.store.list_runs().items == (), "the refusal happens before any durable write"

    def test_a_run_parks_naming_the_missing_executor_and_resumes_once_wired(self, lab: _Lab) -> None:
        lab.engine = _build_engine(lab.tmp_path, executor="absent", editor="absent")
        with lab.client() as client:
            started = client.post("/api/harness/runs", json={**_RUN_BODY, "run_id": "run-park"})
            assert started.status_code == 200, started.text
            body = started.json()
            assert body["run"]["stage"] == "blocked"
            assert "HarnessExecutor" in body["run"]["blocked_reason"]
            assert body["resumable"] is True

            # Wire the collaborators the way a deployment would, then resume.
            lab.engine.executor = _DemoExecutor()
            lab.engine.editor = ScriptedEditor(proposals={CandidateScope.INVARIANT: _invariant_proposal, CandidateScope.TASK_OR_DOMAIN: _domain_proposal})
            resumed = client.post("/api/harness/runs/run-park/resume")
            assert resumed.status_code == 200, resumed.text
            assert resumed.json()["run"]["stage"] == "completed"
            assert resumed.json()["next_stage"] == "completed"

    def test_unknown_run_refusals(self, lab: _Lab) -> None:
        with lab.client() as client:
            detail = client.get("/api/harness/runs/run-ghost")
            resume = client.post("/api/harness/runs/run-ghost/resume")
            events = client.get("/api/harness/runs/run-ghost/events")
        assert detail.status_code == 404 and detail.json()["code"] == "harness_run_not_found"
        assert resume.status_code == 404 and resume.json()["code"] == "harness_run_not_found"
        assert events.status_code == 404 and events.json()["code"] == "harness_run_not_found"


# -------------------------------------------------------------------- full cycle


class TestFullCycleOverHttp:
    def test_a_run_completes_and_every_view_renders_it(self, lab: _Lab) -> None:
        with lab.client() as client:
            run = _start_cycle(client)
            assert run["stage"] == "completed"
            assert run["error"] is None
            assert len(run["checkpoints"]) == 12
            assert run["successor_bundle_id"]

            timeline = client.get("/api/harness/runs").json()
            assert timeline["complete"] is True
            assert [row["run_id"] for row in timeline["runs"]] == ["run-http"]
            row = timeline["runs"][0]
            assert row["stage"] == "completed"
            assert row["resumable"] is False
            assert row["counts"] == {"checkpoints": 12, "proposals": 2, "validations": 2, "accepted_validations": 2, "probes": 4, "contrasts": 2, "experiences": 2}

            detail = client.get("/api/harness/runs/run-http").json()
            assert detail["run"]["stage"] == "completed"
            assert detail["resumable"] is False
            assert detail["next_stage"] == "completed"
            assert detail["unreadable_event_lines"] == []
            assert detail["events"][-1]["event"] == "run.completed"

            events = client.get("/api/harness/runs/run-http/events").json()
            assert events["run_record_present"] is True
            assert events["events"][0]["event"] == "run.created"
            assert events["events"][-1]["event"] == "run.completed"
            assert events["unreadable"] == []

            status = client.get("/api/harness/status").json()
            assert status["enabled"] is True
            assert status["store"]["counts"]["runs"] == 1
            assert status["harness_capability"]["executes"] is False, "the production posture does not change because a test wired actors"
            assert status["active_bundle"] is None, "a completing run never moves the active pointer"
            assert status["settings"]["max_model_calls"] == 64
            assert status["config_hash"].startswith("sha256:")

            # A terminal run refuses both controls with its own words.
            cancelled = client.post("/api/harness/runs/run-http/cancel", json={})
            resumed = client.post("/api/harness/runs/run-http/resume")
            assert cancelled.status_code == 409 and cancelled.json()["code"] == "harness_run_not_cancellable"
            assert resumed.status_code == 409 and resumed.json()["code"] == "harness_run_not_resumable"

    def test_bundles_diff_experiences_budgets_and_promotions_render(self, lab: _Lab) -> None:
        with lab.client() as client:
            run = _start_cycle(client)
            base_id = run["base_bundle_id"]
            successor_id = run["successor_bundle_id"]

            listing = client.get("/api/harness/bundles").json()
            assert listing["complete"] is True
            assert listing["active_bundle"] is None
            ids = {row["bundle_id"] for row in listing["bundles"]}
            assert {base_id, successor_id} <= ids
            successor_row = next(row for row in listing["bundles"] if row["bundle_id"] == successor_id)
            assert successor_row["parent_bundle_id"] == base_id
            assert successor_row["promotion_state"] == "draft"

            bundle = client.get(f"/api/harness/bundles/{successor_id}").json()
            assert bundle["bundle"]["parent_bundle_id"] == base_id
            skill = next(row for row in bundle["components"] if row["component_id"] == "skills/artifact-verification")
            assert "Artifact verification" in skill["content"]
            assert skill["missing"] is False
            assert bundle["redactions"] == []

            diff = client.get(f"/api/harness/bundles/{successor_id}/diff").json()
            assert "skills/artifact-verification" in diff["diff"]["added"]
            assert [row["component_id"] for row in diff["diff"]["modified"]] == ["system_prompt/base"]
            assert "Artifact verification" in diff["contents"]["added"]["skills/artifact-verification"]
            assert diff["contents"]["modified"]["system_prompt/base"]["before"] != diff["contents"]["modified"]["system_prompt/base"]["after"]

            experiences = client.get("/api/harness/experiences").json()
            assert experiences["complete"] is True
            assert experiences["total"] == 2
            assert {row["finding"] for row in experiences["experiences"]} == {"benefit"}
            assert all(isinstance(row["staleness"], list) for row in experiences["experiences"])
            entry_id = experiences["experiences"][0]["experience_id"]
            entry = client.get(f"/api/harness/experiences/{entry_id}").json()
            assert "## Evidence" in entry["markdown"]
            assert entry["metadata"]["status"] == "proposed"

            budgets = client.get("/api/harness/budgets").json()
            assert budgets["ceilings"]["max_model_calls"] == 64
            assert [row["stage"] for row in budgets["ledger"]] == ["baseline_running", "proposing_invariant", "proposing_task_or_domain", "probe_invariant", "probe_task_or_domain", "successor_running"]
            assert all(row["cost_usd"] is None for row in budgets["ledger"]), "an unreported dimension is null, never zero"
            assert budgets["measured_model_calls"] == 6
            assert budgets["unreadable"] == []

            promotions = client.get("/api/harness/promotions").json()
            assert promotions["events"] == [], "a completing run promotes nothing by itself"
            assert promotions["pointer"] is None
            assert promotions["ladder"]["draft"] == "statically_validated"

            missing = client.get(f"/api/harness/bundles/{_MISSING_BUNDLE}")
            assert missing.status_code == 404 and missing.json()["code"] == "harness_bundle_not_found"


# ------------------------------------------------------------------ promotions


class TestPromotionOverHttp:
    def test_approving_is_refused_while_the_regression_suite_is_unmeasured(self, lab: _Lab) -> None:
        with lab.client() as client:
            run = _start_cycle(client)
            response = client.post(f"/api/harness/candidates/{run['successor_bundle_id']}/approve", json={"approvals": []})
            assert response.status_code == 409, response.text
            body = response.json()
            assert body["code"] == "harness_promotion_gates_unmet"
            assert any("regression suite has not been run" in reason for reason in body["gates"]["reasons"])
            assert body["evidence"]["basis"] == "composed_successor"
            assert body["evidence"]["source_run_id"] == "run-http"
            assert client.get("/api/harness/promotions").json()["events"] == [], "a refused approval records nothing"

    def test_an_approval_for_a_kind_that_is_not_pending_is_refused_by_name(self, lab: _Lab) -> None:
        with lab.client() as client:
            run = _start_cycle(client)
            response = client.post(f"/api/harness/candidates/{run['successor_bundle_id']}/approve", json={"approvals": ["middleware"]})
        assert response.status_code == 422, response.text
        body = response.json()
        assert body["code"] == "harness_unexpected_approvals"
        assert body["pending"] == []
        assert "middleware" in body["detail"]

    def test_activating_refuses_until_an_operator_approval_is_recorded(self, lab: _Lab) -> None:
        # The regression gate is lifted here so the *only* remaining reason is
        # the deliberate operator step — which approval, not activation, is for.
        lab.engine = _build_engine(lab.tmp_path, require_regression_suite=False)
        with lab.client() as client:
            run = _start_cycle(client)
            response = client.post(f"/api/harness/active/{run['successor_bundle_id']}/activate", json={})
            assert response.status_code == 409, response.text
            body = response.json()
            assert body["code"] == "harness_promotion_gates_unmet"
            assert "no operator approval is recorded" in body["detail"]
            assert any("automatic_global_promotion" in reason for reason in body["configured_gates"]["reasons"])
            assert client.get("/api/harness/promotions").json()["pointer"] is None

    def test_approve_activate_and_rollback_over_the_api(self, lab: _Lab) -> None:
        lab.engine = _build_engine(lab.tmp_path, require_regression_suite=False)
        with lab.client() as client:
            run = _start_cycle(client)
            base_id = run["base_bundle_id"]
            successor_id = run["successor_bundle_id"]

            approved = client.post(f"/api/harness/candidates/{successor_id}/approve", json={"reason": "measured", "approvals": []})
            assert approved.status_code == 200, approved.text
            assert approved.json()["approved"] is True
            assert approved.json()["gates"]["allowed"] is True

            activated = client.post(f"/api/harness/active/{successor_id}/activate", json={"reason": "ship it"})
            assert activated.status_code == 200, activated.text
            body = activated.json()
            assert body["activated"] is True
            assert body["bundle_id"] == successor_id
            assert body["previous_bundle_id"] is None, "the bootstrap never moved the pointer, so the first activation has no rollback target"

            promotions = client.get("/api/harness/promotions").json()
            assert [row["event"] for row in promotions["events"]] == ["approved", "activated"]
            assert promotions["pointer"]["bundle_id"] == successor_id
            assert promotions["pointer_error"] is None

            status = client.get("/api/harness/status").json()
            assert status["active_bundle"]["bundle_id"] == successor_id
            assert status["active_bundle"]["manifest"]["promotion_state"] == "active"

            # A rollback with no recorded previous bundle refuses rather than
            # clearing the pointer.
            no_target = client.post(f"/api/harness/active/{successor_id}/rollback", json={})
            assert no_target.status_code == 409
            assert no_target.json()["code"] == "harness_rollback_refused"
            assert "no previous known-good bundle" in no_target.json()["detail"]

            # Seed a real rollback target the way a second operator activation
            # would, then pin both refusal and success shapes.
            seeded = activate(lab.engine.store, bundle_id=base_id, actor="seed", reason="second activation", decision=None)
            assert seeded["activated"] is True

            foreign = client.post(f"/api/harness/active/{successor_id}/rollback", json={})
            assert foreign.status_code == 409
            assert foreign.json()["code"] == "harness_rollback_refused"
            assert foreign.json()["active_bundle_id"] == base_id

            rolled = client.post(f"/api/harness/active/{base_id}/rollback", json={})
            assert rolled.status_code == 200, rolled.text
            rolled_body = rolled.json()
            assert rolled_body["rolled_back"] is True
            assert rolled_body["from_bundle_id"] == base_id
            assert rolled_body["restored_bundle_id"] == successor_id
            assert client.get("/api/harness/status").json()["active_bundle"]["bundle_id"] == successor_id

    def test_rollback_without_a_pointer_refuses(self, lab: _Lab) -> None:
        with lab.client() as client:
            response = client.post(f"/api/harness/active/{_MISSING_BUNDLE}/rollback", json={})
        assert response.status_code == 409
        assert response.json()["code"] == "harness_rollback_refused"
        assert "no active bundle pointer exists" in response.json()["detail"]

    def test_rejecting_records_an_event_and_the_pointer_stays_put(self, lab: _Lab) -> None:
        with lab.client() as client:
            run = _start_cycle(client)
            successor_id = run["successor_bundle_id"]

            rejected = client.post(f"/api/harness/candidates/{successor_id}/reject", json={"reason": "not now"})
            assert rejected.status_code == 200, rejected.text
            assert rejected.json()["rejected"] is True
            assert rejected.json()["promotion_state"] == "rejected"

            promotions = client.get("/api/harness/promotions").json()
            assert [row["event"] for row in promotions["events"]] == ["rejected"]
            assert promotions["pointer"] is None

            # A rejected candidate can no longer be approved, with the reason.
            refused = client.post(f"/api/harness/candidates/{successor_id}/approve", json={})
            assert refused.status_code == 409
            assert any("cannot be promoted from that state" in reason for reason in refused.json()["gates"]["reasons"])

            missing = client.post(f"/api/harness/candidates/{_MISSING_BUNDLE}/reject", json={})
            assert missing.status_code == 404 and missing.json()["code"] == "harness_bundle_not_found"


# -------------------------------------------------------------------- degraded store


class TestDegradedStore:
    def test_an_absent_store_is_an_empty_answer_not_a_degraded_one(self, lab: _Lab) -> None:
        with lab.client() as client:
            bundles = client.get("/api/harness/bundles").json()
            runs = client.get("/api/harness/runs").json()
            experiences = client.get("/api/harness/experiences").json()
            promotions = client.get("/api/harness/promotions").json()
        assert bundles["bundles"] == [] and bundles["complete"] is True
        assert runs["runs"] == [] and runs["complete"] is True
        assert experiences["experiences"] == [] and experiences["total"] == 0
        assert promotions["events"] == [] and promotions["pointer"] is None

    def test_a_corrupt_pointer_refuses_a_start_instead_of_reading_as_absent(self, lab: _Lab) -> None:
        lab.store.active_path.parent.mkdir(parents=True, exist_ok=True)
        lab.store.active_path.write_text("{not json", encoding="utf-8")
        with lab.client() as client:
            started = client.post("/api/harness/runs", json=_RUN_BODY)
            status = client.get("/api/harness/status").json()
            listed = client.get("/api/harness/bundles")
        assert started.status_code == 503
        assert started.json()["code"] == "harness_store_unreadable"
        assert status["store"]["readable"] is False
        assert "malformed" in status["store"]["reason"]
        assert listed.status_code == 503
        assert listed.json()["code"] == "harness_store_unreadable"

    def test_a_corrupt_bundle_manifest_is_503_on_its_route_and_named_on_the_list(self, lab: _Lab) -> None:
        corrupt_id = "sha256:" + "a" * 64
        path = lab.store.bundle_path(corrupt_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not json", encoding="utf-8")
        with lab.client() as client:
            detail = client.get(f"/api/harness/bundles/{corrupt_id}")
            listing = client.get("/api/harness/bundles").json()
            status = client.get("/api/harness/status").json()
        assert detail.status_code == 503
        assert detail.json()["code"] == "harness_store_unreadable"
        assert listing["complete"] is False
        assert any("malformed" in row for row in listing["unreadable"])
        # The listing succeeded; one manifest did not parse. That is visible in
        # `unreadable`, not hidden by flipping the whole store to unreadable.
        assert status["store"]["readable"] is True
        assert status["store"]["unreadable"]

    def test_a_torn_journal_line_is_disclosed_and_the_journal_survives(self, lab: _Lab) -> None:
        parked = _build_engine(lab.tmp_path, executor="absent", editor="absent")
        asyncio.run(parked.start(task=_task(), run_id="run-parked", created_by="ops-admin"))
        with parked.store.run_events_path("run-parked").open("a", encoding="utf-8", newline="") as handle:
            handle.write('{"event": "run.completed"\n')
        with lab.client() as client:
            body = client.get("/api/harness/runs/run-parked/events").json()
        assert body["run_record_present"] is True
        assert body["unreadable"] and body["unreadable"][0].startswith("line ")
        assert body["events"], "one torn line must not drop the journal"

    def test_a_component_body_is_redacted_on_the_way_out(self, lab: _Lab) -> None:
        bundle, contents = build_bundle(
            components={"system_prompt/base": (ComponentKind.SYSTEM_PROMPT, f"deploy key {SECRET} stays out of responses")},
            parent_bundle_id=None,
            created_by="test",
        )
        lab.store.save_bundle(bundle, contents.items())
        with lab.client() as client:
            response = client.get(f"/api/harness/bundles/{bundle.bundle_id}")
            payload = response.json()
        assert response.status_code == 200, response.text
        assert SECRET not in response.text
        assert "[REDACTED:api_key]" in payload["components"][0]["content"]
        assert any("api_key" in finding for finding in payload["redactions"])
