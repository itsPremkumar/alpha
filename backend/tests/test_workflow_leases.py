"""Durable, fenced node-lease coverage.

Honesty pins in this suite:

* **a lease outlives the process** — a manager constructed fresh over the same
  store sees the claim, which is the whole point of it not being a dict;
* **a fence is monotonic and persisted**, so a superseded attempt cannot be
  mistaken for a current one after a restart;
* **reclaiming an expired lease advances the fence** — otherwise the dead
  worker's late result would still verify as ``accepted`` and write through;
* **an unverifiable key is refused, never accepted** (``unknown_lease``);
* **an orphaned RUNNING node is FAILED, not silently reset** — we cannot know
  whether its side effects landed;
* **a node executing in this process is never reconciled away**, whatever its
  wall-clock lease says;
* **a dry run never writes a claim into the real store.**
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from alpha.workflow.leases import (
    LeaseManager,
    LeaseStoreError,
    ResultVerdict,
)
from alpha.workflow.models import (
    NodeStatus,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
    WorkflowRun,
)
from alpha.workflow.runtime import DynamicWorkflowEngine
from alpha.workflow.time_travel import simulate_run

WORKER_A = "gateway-pid-1"
WORKER_B = "gateway-pid-2"


# --------------------------------------------------------------------- helpers


def _store(tmp_path) -> LeaseManager:
    return LeaseManager(tmp_path / "leases")


def _engine(tmp_path, monkeypatch) -> DynamicWorkflowEngine:
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path))
    engine = DynamicWorkflowEngine()
    # Isolated store: these tests must not share a lease file with anything.
    engine.leases = LeaseManager(tmp_path / "engine_lease_store")
    return engine


def _definition(wf_id: str, node_ids: list[str]) -> WorkflowDefinition:
    graph = WorkflowGraph(
        version=1,
        nodes={nid: WorkflowNode(id=nid) for nid in node_ids},
        edges=[WorkflowEdge(source=a, target=b) for a, b in zip(node_ids, node_ids[1:], strict=False)],
    )
    return WorkflowDefinition(id=wf_id, name=wf_id, graph=graph)


def _runner(node, run):  # noqa: ANN001 - mirrors the engine's seam signature
    return {"status": "completed", "output": f"ran:{node.id}", "evidence": f"evidence:{node.id}"}


def _force_running(engine: DynamicWorkflowEngine, run: WorkflowRun, node_id: str) -> None:
    """Put a node into the state a crash leaves behind: RUNNING, no completer."""
    graph = engine._run_graphs[run.run_id]
    with engine.state():
        graph.nodes[node_id].status = NodeStatus.RUNNING
        run.node_states[node_id] = NodeStatus.RUNNING


# ------------------------------------------------------------------- durability


class TestDurability:
    def test_lease_survives_a_new_manager_over_the_same_store(self, tmp_path) -> None:
        first = _store(tmp_path)
        lease = first.acquire_lease("run-1", "build", WORKER_A, graph_version=3)
        assert lease is not None
        assert lease.fence_token == 1
        assert lease.graph_version == 3

        # "Process restarted": a brand-new manager, nothing carried over.
        revived = _store(tmp_path)
        restored = revived.get_lease("run-1", "build")
        assert restored is not None
        assert restored.worker_id == WORKER_A
        assert restored.graph_version == 3
        assert restored.is_expired is False

    def test_process_local_manager_creates_no_file(self, tmp_path) -> None:
        manager = LeaseManager()
        assert manager.store_path is None
        manager.acquire_lease("run-1", "build", WORKER_A)
        assert list(tmp_path.iterdir()) == []

    def test_store_file_is_written_atomically_and_is_valid_json(self, tmp_path) -> None:
        manager = _store(tmp_path)
        manager.acquire_lease("run-1", "build", WORKER_A, attempt_id="att-1")
        path = tmp_path / "leases" / "leases.json"
        assert path.exists()
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["schema_version"] == 1
        assert payload["leases"]["run-1:build"]["attempt_id"] == "att-1"
        assert payload["fences"]["run-1:build"] == 1
        assert not (tmp_path / "leases" / "leases.json.tmp").exists(), "a temp file must not be left behind"

    def test_corrupt_store_fails_closed_instead_of_starting_empty(self, tmp_path) -> None:
        path = tmp_path / "leases"
        path.mkdir()
        (path / "leases.json").write_text("{not json", encoding="utf-8")
        with pytest.raises(LeaseStoreError) as exc:
            LeaseManager(path)
        assert "not valid JSON" in str(exc.value)

    def test_unsupported_schema_version_fails_closed(self, tmp_path) -> None:
        path = tmp_path / "leases"
        path.mkdir()
        (path / "leases.json").write_text(json.dumps({"schema_version": 99, "leases": {}, "fences": {}}), encoding="utf-8")
        with pytest.raises(LeaseStoreError) as exc:
            LeaseManager(path)
        assert "schema_version" in str(exc.value)

    def test_missing_store_is_simply_an_empty_registry(self, tmp_path) -> None:
        assert LeaseManager(tmp_path / "never-created").get_lease("r", "n") is None


# ----------------------------------------------------------------------- claiming


class TestClaiming:
    def test_a_live_lease_from_another_worker_is_refused(self, tmp_path) -> None:
        manager = _store(tmp_path)
        assert manager.acquire_lease("run-1", "build", WORKER_A) is not None
        assert manager.acquire_lease("run-1", "build", WORKER_B) is None

    def test_the_same_worker_may_reacquire_its_own_lease(self, tmp_path) -> None:
        manager = _store(tmp_path)
        assert manager.acquire_lease("run-1", "build", WORKER_A) is not None
        assert manager.acquire_lease("run-1", "build", WORKER_A) is not None

    def test_an_expired_lease_is_taken_over_and_the_fence_advances(self, tmp_path) -> None:
        manager = _store(tmp_path)
        first = manager.acquire_lease("run-1", "build", WORKER_A, ttl_seconds=0.001)
        assert first is not None

        time.sleep(0.005)  # well past a 1ms TTL; a clock comparison, not a timing assertion

        second = manager.acquire_lease("run-1", "build", WORKER_B, ttl_seconds=60.0)
        assert second is not None, "an expired lease must not block recovery"
        assert second.fence_token > first.fence_token, "a takeover must advance the fence"

    def test_non_positive_ttl_is_refused(self, tmp_path) -> None:
        with pytest.raises(ValueError, match="must be positive"):
            _store(tmp_path).acquire_lease("run-1", "build", WORKER_A, ttl_seconds=0)

    def test_heartbeat_requires_ownership_and_the_current_fence(self, tmp_path) -> None:
        manager = _store(tmp_path)
        lease = manager.acquire_lease("run-1", "build", WORKER_A)
        assert lease is not None

        assert manager.heartbeat("run-1", "build", WORKER_A, fence_token=lease.fence_token) is True
        assert manager.heartbeat("run-1", "build", WORKER_B) is False, "another worker cannot extend our lease"
        assert manager.heartbeat("run-1", "build", WORKER_A, fence_token=lease.fence_token + 99) is False

    def test_a_stale_worker_cannot_release_a_newer_claim(self, tmp_path) -> None:
        manager = _store(tmp_path)
        original = manager.acquire_lease("run-1", "build", WORKER_A, ttl_seconds=0.001)
        assert original is not None
        time.sleep(0.005)
        replacement = manager.acquire_lease("run-1", "build", WORKER_B)
        assert replacement is not None

        manager.release_lease("run-1", "build", WORKER_A, fence_token=original.fence_token)
        assert manager.get_lease("run-1", "build") is not None, "a stale release must not steal the newer claim"

        manager.release_lease("run-1", "build", WORKER_B, fence_token=replacement.fence_token)
        assert manager.get_lease("run-1", "build") is None


# ----------------------------------------------------------------------- fencing


class TestFencing:
    def test_current_fence_is_accepted(self, tmp_path) -> None:
        manager = _store(tmp_path)
        lease = manager.acquire_lease("run-1", "build", WORKER_A, graph_version=1)
        assert lease is not None
        verdict = manager.check_result("run-1", "build", worker_id=WORKER_A, fence_token=lease.fence_token, graph_version=1)
        assert verdict is ResultVerdict.ACCEPTED

    def test_a_never_issued_key_is_unknown_not_accepted(self, tmp_path) -> None:
        manager = _store(tmp_path)
        verdict = manager.check_result("run-1", "never-ran", worker_id=WORKER_A, fence_token=1)
        assert verdict is ResultVerdict.UNKNOWN_LEASE, "an unverifiable attempt must never read as accepted"

    def test_a_superseded_attempt_is_stale(self, tmp_path) -> None:
        manager = _store(tmp_path)
        old = manager.acquire_lease("run-1", "build", WORKER_A, ttl_seconds=0.001)
        assert old is not None
        time.sleep(0.005)
        new = manager.acquire_lease("run-1", "build", WORKER_B)
        assert new is not None

        verdict = manager.check_result("run-1", "build", worker_id=WORKER_A, fence_token=old.fence_token)
        assert verdict is ResultVerdict.STALE_LEASE

    def test_a_result_from_an_older_graph_revision_is_superseded(self, tmp_path) -> None:
        manager = _store(tmp_path)
        lease = manager.acquire_lease("run-1", "build", WORKER_A, graph_version=1)
        assert lease is not None
        verdict = manager.check_result("run-1", "build", worker_id=WORKER_A, fence_token=lease.fence_token, graph_version=2)
        assert verdict is ResultVerdict.SUPERSEDED_REVISION

    def test_reclaiming_an_expired_lease_fences_out_its_late_result(self, tmp_path) -> None:
        """The bug this guards: reclaiming without bumping the fence would let
        a dead worker's result still verify as ACCEPTED."""
        manager = _store(tmp_path)
        lease = manager.acquire_lease("run-1", "build", WORKER_A, ttl_seconds=0.001)
        assert lease is not None
        old_fence = lease.fence_token
        time.sleep(0.005)

        reclaimed = manager.reclaim_expired()
        assert [item.key for item in reclaimed] == ["run-1:build"]
        verdict = manager.check_result("run-1", "build", worker_id=WORKER_A, fence_token=old_fence)
        assert verdict is ResultVerdict.STALE_LEASE

    def test_forgetting_a_run_makes_late_results_unknown_not_accepted(self, tmp_path) -> None:
        manager = _store(tmp_path)
        lease = manager.acquire_lease("run-1", "build", WORKER_A)
        assert lease is not None
        assert manager.forget_run("run-1") == 1
        verdict = manager.check_result("run-1", "build", worker_id=WORKER_A, fence_token=lease.fence_token)
        assert verdict is ResultVerdict.UNKNOWN_LEASE

    def test_reclaim_is_idempotent_for_an_absent_lease(self, tmp_path) -> None:
        manager = _store(tmp_path)
        assert manager.reclaim("run-1", "build") is None


# ------------------------------------------------------------- engine integration


class TestEngineWiring:
    def test_dispatch_acquires_and_then_releases_a_durable_lease(self, tmp_path, monkeypatch) -> None:
        engine = _engine(tmp_path, monkeypatch)
        engine.register_definition(_definition("wf_lease", ["a", "b"]))
        run = engine.start_run("wf_lease")
        observed: dict[str, object] = {}

        def watching_runner(node, run_):  # noqa: ANN001
            lease = engine.leases.get_lease(run_.run_id, node.id)
            observed["held_during_execution"] = lease is not None
            observed["fence"] = None if lease is None else lease.fence_token
            return _runner(node, run_)

        engine.execute_step(run.run_id, node_runner=watching_runner)

        assert observed["held_during_execution"] is True, "the attempt must be claimed while it executes"
        assert observed["fence"] == 1
        assert engine.leases.get_lease(run.run_id, "a") is None, "the claim must be released when the attempt ends"
        assert (tmp_path / "engine_lease_store" / "leases.json").exists(), "the claim must be durable, not memory-only"

    def test_a_lease_refusal_fails_the_node_honestly_and_does_not_loop(self, tmp_path, monkeypatch) -> None:
        engine = _engine(tmp_path, monkeypatch)
        engine.register_definition(_definition("wf_held", ["a"]))
        run = engine.start_run("wf_held")

        # Another worker holds a live claim this process cannot take over.
        engine.leases.acquire_lease(run.run_id, "a", WORKER_B, ttl_seconds=600.0)

        calls: list[str] = []

        def counting_runner(node, run_):  # noqa: ANN001
            calls.append(node.id)
            return _runner(node, run_)

        engine.execute_step(run.run_id, node_runner=counting_runner)

        assert calls == [], "a node we could not claim must not be dispatched"
        assert run.node_states["a"] is NodeStatus.FAILED
        output = engine._run_graphs[run.run_id].nodes["a"].output
        assert isinstance(output, dict)
        assert "could not be claimed" in output["reason"]
        assert WORKER_B in output["reason"]

    def test_a_step_recovers_an_orphaned_node_instead_of_hanging(self, tmp_path, monkeypatch) -> None:
        engine = _engine(tmp_path, monkeypatch)
        engine.register_definition(_definition("wf_orphan", ["a", "b"]))
        run = engine.start_run("wf_orphan")
        _force_running(engine, run, "a")

        seen: list[str] = []

        def listener(event) -> None:  # noqa: ANN001 - WorkflowEvent
            seen.append(event.event_type)

        engine.events.subscribe(listener)
        try:
            engine.execute_step(run.run_id, node_runner=_runner)
        finally:
            engine.events.unsubscribe(listener)

        # The bug this closes: RUNNING with no completer is never admitted
        # again (the scheduler takes only PENDING/READY), so the run hung on
        # work nobody owns — forever, and across a restart too.
        assert run.node_states["a"] is not NodeStatus.RUNNING, "an orphaned node must never stay RUNNING"
        assert "orphaned_nodes_reconciled" in seen, "recovery must be journalled, not silent"

        # Recovery is not a reset: the node is failed honestly first (which is
        # what the event above records), and the engine's own bounded
        # remediation policy then reopens it as a fresh attempt.
        assert run.node_states["a"] is NodeStatus.READY

        # ...and the run really progresses on the next step rather than
        # sitting on a node nobody will ever dispatch again.
        for _ in range(4):
            if run.node_states["a"] is NodeStatus.SUCCEEDED:
                break
            engine.execute_step(run.run_id, node_runner=_runner)
        assert run.node_states["a"] is NodeStatus.SUCCEEDED

    def test_reconcile_reports_an_expired_lease_and_names_its_worker(self, tmp_path, monkeypatch) -> None:
        engine = _engine(tmp_path, monkeypatch)
        engine.register_definition(_definition("wf_expired", ["a"]))
        run = engine.start_run("wf_expired")
        _force_running(engine, run, "a")
        engine.leases.acquire_lease(run.run_id, "a", WORKER_B, ttl_seconds=0.001)
        time.sleep(0.005)

        report = engine.reconcile_orphaned_nodes(run)

        assert len(report) == 1
        entry = report[0]
        assert entry["action"] == "failed"
        assert entry["failure_class"] == "worker_lost"
        assert entry["superseded_attempt_id"]
        assert WORKER_B in entry["reason"]
        assert run.node_states["a"] is NodeStatus.FAILED
        assert engine.leases.get_lease(run.run_id, "a") is None

    def test_a_running_node_with_a_live_foreign_lease_is_left_alone(self, tmp_path, monkeypatch) -> None:
        engine = _engine(tmp_path, monkeypatch)
        engine.register_definition(_definition("wf_live", ["a"]))
        run = engine.start_run("wf_live")
        _force_running(engine, run, "a")
        engine.leases.acquire_lease(run.run_id, "a", WORKER_B, ttl_seconds=600.0)

        report = engine.reconcile_orphaned_nodes(run)

        assert [entry["action"] for entry in report] == ["held"]
        assert run.node_states["a"] is NodeStatus.RUNNING, "a node somebody still owns must not be failed"
        assert engine.leases.get_lease(run.run_id, "a") is not None

    def test_a_node_executing_here_is_never_reconciled_away(self, tmp_path, monkeypatch) -> None:
        """A wall-clock TTL lapsing on a long node is not evidence it stopped."""
        engine = _engine(tmp_path, monkeypatch)
        engine.register_definition(_definition("wf_local", ["a"]))
        run = engine.start_run("wf_local")
        _force_running(engine, run, "a")
        engine.leases.acquire_lease(run.run_id, "a", engine.worker_id, ttl_seconds=0.001)
        engine._in_flight.add(engine._lease_key(run.run_id, "a"))
        time.sleep(0.005)

        assert engine.reconcile_orphaned_nodes(run) == []
        assert run.node_states["a"] is NodeStatus.RUNNING

        engine._in_flight.discard(engine._lease_key(run.run_id, "a"))

    def test_reconcile_is_silent_when_nothing_is_running(self, tmp_path, monkeypatch) -> None:
        engine = _engine(tmp_path, monkeypatch)
        engine.register_definition(_definition("wf_quiet", ["a"]))
        run = engine.start_run("wf_quiet")
        assert engine.reconcile_orphaned_nodes(run) == []
        assert not (tmp_path / "engine_lease_store" / "leases.json").exists(), "no claim means no store write"

    def test_a_result_from_a_superseded_attempt_is_discarded(self, tmp_path, monkeypatch) -> None:
        """Fencing in the live path: reclaim + takeover mid-execution must
        stop the result being folded in as a success."""
        engine = _engine(tmp_path, monkeypatch)
        engine.register_definition(_definition("wf_fenced", ["a"]))
        run = engine.start_run("wf_fenced")

        def late_worker(node, run_):  # noqa: ANN001
            # The claim lapses while we were running and somebody else takes it.
            engine.leases.reclaim(run_.run_id, node.id)
            engine.leases.acquire_lease(run_.run_id, node.id, WORKER_B)
            return _runner(node, run_)

        engine.execute_step(run.run_id, node_runner=late_worker)

        assert run.node_states["a"] is NodeStatus.FAILED
        output = engine._run_graphs[run.run_id].nodes["a"].output
        assert isinstance(output, dict)
        assert output.get("lease_verdict") == ResultVerdict.STALE_LEASE
        assert "result discarded" in output["reason"]


# --------------------------------------------------------------------- dry runs


class TestDryRunIsolation:
    def test_a_simulation_never_writes_a_claim_into_the_real_store(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("ALPHA_HOME", str(tmp_path))
        engine = DynamicWorkflowEngine()
        real_store = tmp_path / "real_store"
        engine.leases = LeaseManager(real_store)
        engine.register_definition(_definition("wf_sim", ["a", "b"]))

        result = simulate_run(engine, "wf_sim")

        assert result is not None
        assert real_store.exists() is False, "a dry run must not touch the real lease store"
        assert engine.leases.get_lease("wf_sim", "a") is None


# ------------------------------------------------------------------ concurrency


class TestConcurrency:
    """A wave dispatches nodes on a bounded pool, so the lease path is concurrent.

    Both tests here pin a bug the regression sweep found and the isolated runs
    did not: nothing in the engine's lease handling was synchronised, and the
    failures were *silent* — good results discarded as ``unknown_lease``, or a
    node's ``finally`` raising out of a store write.
    """

    def test_a_claim_recorded_racing_first_access_is_still_verifiable(self, tmp_path, monkeypatch) -> None:
        """The verdict must survive whoever won the lazy-construction race.

        A manager loads the store into memory once, at construction. Without a
        guard, wave threads racing this property each built a manager over an
        empty file: a node acquired through the manager it happened to get, then
        checked through the manager the engine finally held — a manager that
        never saw the claim. Every verdict then read ``unknown_lease`` and
        discarded work that had actually succeeded, with its tokens already
        charged. This is the invariant that broke; the isolated runs never hit
        it because they never raced the first read.
        """
        monkeypatch.setenv("ALPHA_HOME", str(tmp_path))
        engine = DynamicWorkflowEngine()
        workers = 8
        barrier = threading.Barrier(workers)
        verdicts: list[ResultVerdict] = []
        managers: list[LeaseManager] = []
        errors: list[BaseException] = []
        lock = threading.Lock()

        def racer(node_index: int) -> None:
            node_id = f"n{node_index}"
            try:
                barrier.wait(timeout=10)
                # Acquire through whatever the engine hands out, then verify
                # through a FRESH read — exactly as the engine does on the
                # other side of the executor call.
                lease = engine.leases.acquire_lease("run-race", node_id, WORKER_A, ttl_seconds=60.0)
                assert lease is not None, "no competing worker exists, so the claim must succeed"
                with lock:
                    managers.append(engine.leases)
                    verdicts.append(
                        engine.leases.check_result(
                            "run-race",
                            node_id,
                            worker_id=WORKER_A,
                            fence_token=lease.fence_token,
                        )
                    )
            except BaseException as exc:  # noqa: BLE001 - reported below, never swallowed
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=racer, args=(index,)) for index in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        assert not errors, f"racing threads raised: {errors}"
        assert len(verdicts) == workers
        assert {id(manager) for manager in managers} == {id(engine.leases)}, "every access must resolve to the one manager"
        assert all(verdict is ResultVerdict.ACCEPTED for verdict in verdicts), f"claims were recorded then disowned: {verdicts}"

    def test_concurrent_claims_and_releases_keep_the_store_valid(self, tmp_path) -> None:
        """Claiming and releasing from several threads must never corrupt the store.

        Two things broke without synchronisation: ``_persist`` built its
        snapshot outside the lock, so a concurrent ``del`` landed mid-iteration
        and raised out of a node's ``finally``; and acquire/release did an
        unlocked read-modify-write on the fence, so two attempts could mint the
        same token — a fence two attempts share is no fence at all.
        """
        manager = _store(tmp_path)
        threads_per_node = 6
        rounds = 15
        errors: list[BaseException] = []
        lock = threading.Lock()

        def churn(node_index: int) -> None:
            node_id = f"n{node_index}"
            try:
                for _ in range(rounds):
                    lease = manager.acquire_lease("run-c", node_id, WORKER_A, ttl_seconds=60.0)
                    assert lease is not None, "no competing worker exists, so the claim must succeed"
                    manager.release_lease("run-c", node_id, WORKER_A, fence_token=lease.fence_token)
            except BaseException as exc:  # noqa: BLE001 - reported below, never swallowed
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=churn, args=(index,)) for index in range(threads_per_node)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        assert not errors, f"concurrent lease churn raised: {errors}"

        payload = json.loads((tmp_path / "leases" / "leases.json").read_text(encoding="utf-8"))
        assert payload["leases"] == {}, "every claim was released, so the store must hold none"
        # One fence advance per successful acquire, persisted rather than lost.
        # Each thread owns a distinct node key, so its counter is its own rounds.
        for key, fence in payload["fences"].items():
            assert fence == rounds, f"{key} advanced to {fence}, expected {rounds}"
