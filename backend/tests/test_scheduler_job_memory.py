"""Round-trip and honesty tests for durable scheduled-job memory (Part B).

Covers the JobMemoryStore journal (bytes, corruption, concurrency,
retention/TTL disclosure) and its ScheduledTaskService integration (prior
context injected on trigger, real journaled outcomes, fail-closed reads,
disclosed-and-never-faked write failures).
"""

import json
import logging
import threading
from datetime import UTC, datetime, timedelta

import pytest

from alpha.runtime import ConflictError, RunStatus
from alpha.runtime.runs.manager import RunRecord
from alpha.runtime.runs.schemas import DisconnectMode
from app.scheduler import JobMemoryStore, ScheduledTaskService, default_job_memory_store
from app.scheduler.job_memory import JobMemoryCorruptError, JobMemoryReadError, JobMemoryWriteError

# --- local dummies (mirrors test_scheduled_task_service.py, self-contained) ---


class DummyTaskRepo:
    def __init__(self, rows):
        self.rows = rows
        self.claimed = False
        self.updated = None
        self.completions = []

    async def cancel_stuck_once_tasks(self, *, error):
        return 0

    async def reconcile_stuck_once_tasks(self, **kwargs):
        return 0

    async def claim_dispatch_lease(self, task_id, **_kwargs):
        return next((dict(row) for row in self.rows if row["id"] == task_id), None)

    async def release_queued_admission_lease(self, task_id):
        return False

    async def release_dispatch_lease(self, task_id, **kwargs):
        return True

    async def claim_due_tasks(self, **_kwargs):
        if self.claimed:
            return []
        self.claimed = True
        return self.rows

    async def update_after_launch(self, *args, **kwargs):
        self.updated = (args, kwargs)

    async def complete_run(self, task_id, **kwargs):
        self.completions.append((task_id, kwargs))
        return True

    async def get(self, task_id: str, *, user_id: str):
        row = next((item for item in self.rows if item["id"] == task_id and item["user_id"] == user_id), None)
        return dict(row) if row is not None else None

    async def get_internal(self, task_id: str):
        row = next((item for item in self.rows if item["id"] == task_id), None)
        return dict(row) if row is not None else None

    async def update(self, task_id: str, *, user_id: str, updates):
        row = next((item for item in self.rows if item["id"] == task_id and item["user_id"] == user_id), None)
        if row is None:
            return None
        row.update(updates)
        return dict(row)


class DummyRunRepo:
    def __init__(self, *, active=False, active_count=0):
        self.created = None
        self.updated = []
        self.active = active
        self.active_count = active_count

    async def count_active_runs(self):
        return self.active_count

    async def list_queued_runs(self, *, limit):
        return []

    async def expire_queued_runs(self, **_kwargs):
        return []

    async def recover_expired_launch_claims(self, **_kwargs):
        return 0

    async def get_active_run(self, task_id):
        if not self.active:
            return None
        return {"id": "task-run-active", "task_id": task_id, "thread_id": "thread-active", "status": "running"}

    async def claim_queued_run(self, run_record_id, *, global_max_concurrent_runs, **_kwargs):
        if self.active_count >= global_max_concurrent_runs:
            return None
        return {"id": run_record_id, "status": "launching"}

    async def requeue_claimed_run(self, run_record_id, **kwargs):
        self.updated.append((run_record_id, {"status": "queued", **kwargs}))
        return True

    async def create(self, **kwargs):
        self.created = kwargs
        return {"id": kwargs["run_record_id"]}

    async def update_status(self, run_record_id, **kwargs):
        self.updated.append((run_record_id, kwargs))
        return True

    async def reconcile_launched_run(self, run_record_id, **kwargs):
        self.updated.append((run_record_id, {"reconciled": True, **kwargs}))
        return True

    async def fail_launching_run(self, run_record_id, **kwargs):
        self.updated.append((run_record_id, {"status": "failed", **kwargs}))
        return True

    async def has_active_runs(self, task_id):
        return self.active

    async def mark_stale_active_runs(self, *, error):
        return 0

    async def reconcile_active_runs(self, **kwargs):
        return 0


def _task_row(task_id="task-rt", prompt="Summarize thread"):
    return {
        "id": task_id,
        "user_id": "user-1",
        "thread_id": None,
        "context_mode": "fresh_thread_per_run",
        "assistant_id": "lead_agent",
        "prompt": prompt,
        "schedule_type": "cron",
        "schedule_spec": {"cron": "0 9 * * *"},
        "timezone": "UTC",
        "status": "enabled",
        "overlap_policy": "enqueue",
    }


def _service(task_repo, run_repo, launch_run, job_memory=None):
    return ScheduledTaskService(
        task_repo=task_repo,
        task_run_repo=run_repo,
        launch_run=launch_run,
        poll_interval_seconds=5,
        lease_seconds=120,
        max_concurrent_runs=3,
        job_memory=job_memory,
    )


def _completion(task_id, task_run_id, *, run_id="run-1", status=RunStatus.success, error=None):
    return RunRecord(
        run_id=run_id,
        thread_id="thread-x",
        assistant_id="lead_agent",
        status=status,
        on_disconnect=DisconnectMode.continue_,
        metadata={"scheduled_task_id": task_id, "scheduled_task_run_id": task_run_id},
        user_id="user-1",
        error=error,
    )


def _dispatched_entry(**overrides):
    entry = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "event": "dispatched",
        "task_id": "task-1",
        "task_run_id": "task-run-1",
        "trigger": "scheduled",
    }
    entry.update(overrides)
    return entry


# --- JobMemoryStore: bytes, corruption, concurrency ---


def test_store_roundtrip_preserves_exact_bytes(tmp_path):
    store = JobMemoryStore(tmp_path / "jobmem")
    first = _dispatched_entry()
    second = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "event": "outcome",
        "task_id": "task-1",
        "task_run_id": "task-run-1",
        "run_id": "run-1",
        "status": "success",
        "error": None,
        "started_at": None,
        "finished_at": datetime.now(UTC).isoformat(),
        "duration_seconds": 1.25,
        "duration_note": None,
    }
    store.append("task-1", first)
    store.append("task-1", second)

    path = tmp_path / "jobmem" / "job-task-1.jsonl"
    expected = (
        json.dumps(first, ensure_ascii=False, sort_keys=True)
        + "\n"
        + json.dumps(second, ensure_ascii=False, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    assert path.read_bytes() == expected
    assert store.read("task-1") == [first, second]


def test_missing_journal_reads_empty_and_discloses_fresh_start(tmp_path):
    store = JobMemoryStore(tmp_path / "jobmem")
    assert store.read("task-none") == []
    block = store.prior_context_block("task-none")
    assert "No prior runs recorded" in block
    assert "retention: not configured" in block
    assert "ttl: not configured" in block


def test_corrupt_line_raises_named_typed_error_and_never_repairs(tmp_path):
    store = JobMemoryStore(tmp_path / "jobmem")
    valid = _dispatched_entry()
    store.append("task-c", valid)
    path = tmp_path / "jobmem" / "job-task-c.jsonl"
    with open(path, "ab") as handle:
        handle.write(b"{this is not json\n")
    before = path.read_bytes()

    with pytest.raises(JobMemoryCorruptError) as read_exc:
        store.read("task-c")
    message = str(read_exc.value)
    assert "job-task-c.jsonl" in message
    assert "line 2" in message

    with pytest.raises(JobMemoryReadError):
        store.prior_context_block("task-c")
    # A failed read must leave the corrupt bytes untouched: no repair, no
    # truncation, no skipping of the bad line.
    assert path.read_bytes() == before


def test_schema_violating_entry_is_corrupt_not_skipped(tmp_path):
    store = JobMemoryStore(tmp_path / "jobmem")
    path = tmp_path / "jobmem" / "job-task-s.jsonl"
    path.parent.mkdir(parents=True)
    path.write_bytes(b'{"not_event": true}\n')
    with pytest.raises(JobMemoryCorruptError) as exc:
        store.read("task-s")
    assert "line 1" in str(exc.value)
    assert "'event'" in str(exc.value)


def test_non_utf8_line_is_corrupt(tmp_path):
    store = JobMemoryStore(tmp_path / "jobmem")
    path = tmp_path / "jobmem" / "job-task-u.jsonl"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff\xfe not utf-8\n")
    with pytest.raises(JobMemoryCorruptError) as exc:
        store.read("task-u")
    assert "not valid UTF-8" in str(exc.value)


def test_thread_concurrent_appends_all_lines_parse(tmp_path):
    store = JobMemoryStore(tmp_path / "jobmem")
    errors = []
    barrier = threading.Barrier(8)

    def worker(worker_index):
        try:
            barrier.wait(timeout=10)
            for item in range(25):
                store.append(
                    "task-conc",
                    _dispatched_entry(task_run_id=f"run-{worker_index}-{item}"),
                )
        except Exception as exc:  # pragma: no cover - surfaced via assertion
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    entries = store.read("task-conc")
    assert len(entries) == 200
    # Every line must be schema-valid (read() would raise otherwise), and no
    # entry may be lost or fabricated.
    seen = {entry["task_run_id"] for entry in entries}
    assert len(seen) == 200


def test_retention_and_ttl_prune_at_read_time_only_with_disclosure(tmp_path):
    store = JobMemoryStore(tmp_path / "jobmem", retention_runs=1, ttl_seconds=60)
    old = datetime.now(UTC) - timedelta(hours=5)
    fresh = datetime.now(UTC)
    store.append("task-r", _dispatched_entry(recorded_at=old.isoformat(), task_run_id="task-run-old", run_id="run-old"))
    store.append(
        "task-r",
        _dispatched_entry(recorded_at=fresh.isoformat(), task_run_id="task-run-fresh", run_id="run-fresh"),
    )
    path = tmp_path / "jobmem" / "job-task-r.jsonl"
    before = path.read_bytes()

    block = store.prior_context_block("task-r")
    assert "run-fresh" in block
    assert "run-old" not in block
    assert "retention: only the most recent 1 run(s) are shown" in block
    assert "ttl: runs journaled more than 60 second(s) ago are pruned at read time" in block
    assert "the journal file is never rewritten" in block
    # Read-time pruning only: disk history is untouched and still readable.
    assert path.read_bytes() == before
    assert len(store.read("task-r")) == 2


def test_dispatched_without_outcome_renders_no_outcome_recorded(tmp_path):
    store = JobMemoryStore(tmp_path / "jobmem")
    store.append("task-n", _dispatched_entry(task_id="task-n"))
    block = store.prior_context_block("task-n")
    assert "dispatched:" in block
    assert "NO OUTCOME RECORDED" in block


def test_unparseable_timestamp_is_never_silently_dropped_by_ttl(tmp_path):
    store = JobMemoryStore(tmp_path / "jobmem", ttl_seconds=1)
    store.append("task-t", _dispatched_entry(recorded_at="not-a-timestamp", task_id="task-t"))
    block = store.prior_context_block("task-t")
    assert "not-a-timestamp" in block
    assert "NO OUTCOME RECORDED" in block


def test_default_store_root_follows_runtime_home(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    store = default_job_memory_store()
    assert store.root == tmp_path / "scheduled-job-memory"


# --- ScheduledTaskService integration ---


@pytest.mark.asyncio
async def test_service_without_store_keeps_prompt_byte_identical(tmp_path):
    launched = []

    async def fake_launch(**kwargs):
        launched.append(kwargs)
        return {"run_id": "run-1", "thread_id": kwargs["thread_id"]}

    task_repo = DummyTaskRepo([_task_row()])
    run_repo = DummyRunRepo()
    service = _service(task_repo, run_repo, fake_launch, job_memory=None)

    result = await service.dispatch_task(task_repo.rows[0], now=datetime.now(UTC), trigger="scheduled")

    assert result["outcome"] == "launched"
    assert launched[0]["prompt"] == "Summarize thread"
    assert "<memory>" not in launched[0]["prompt"]


@pytest.mark.asyncio
async def test_dispatch_completion_second_dispatch_sees_real_prior_context(tmp_path):
    store = JobMemoryStore(tmp_path / "jobmem")
    prompts = []

    async def fake_launch(**kwargs):
        prompts.append(kwargs["prompt"])
        return {"run_id": f"run-{len(prompts)}", "thread_id": kwargs["thread_id"]}

    task_repo = DummyTaskRepo([_task_row(task_id="task-rt")])
    run_repo = DummyRunRepo()
    service = _service(task_repo, run_repo, fake_launch, job_memory=store)
    row = task_repo.rows[0]

    first = await service.dispatch_task(row, now=datetime.now(UTC), trigger="scheduled")
    assert first["outcome"] == "launched"
    # First run: honest fresh-start disclosure, wrapped for prompt injection.
    assert prompts[0].startswith("Summarize thread")
    assert "\n<memory>\n" in prompts[0]
    assert prompts[0].endswith("</memory>")
    assert "No prior runs recorded" in prompts[0]

    await service.handle_run_completion(_completion("task-rt", first["task_run_id"], run_id=first["run_id"]))

    entries = store.read("task-rt")
    assert [entry["event"] for entry in entries] == ["dispatched", "outcome"]
    outcome = entries[1]
    assert outcome["status"] == "success"
    assert outcome["run_id"] == first["run_id"]
    assert isinstance(outcome["duration_seconds"], float)
    assert outcome["duration_seconds"] >= 0
    assert outcome["duration_note"] is None

    second = await service.dispatch_task(row, now=datetime.now(UTC), trigger="scheduled")
    assert second["outcome"] == "launched"
    prior_prompt = prompts[1]
    assert "\n<memory>\n" in prior_prompt
    assert "Scheduled job memory for job task-rt" in prior_prompt
    assert "trigger='scheduled'" in prior_prompt
    assert "outcome: success" in prior_prompt
    assert f"run_id={first['run_id']}" in prior_prompt
    assert "duration=0." in prior_prompt  # measured, not "unknown"
    assert "NO OUTCOME RECORDED" not in prior_prompt


@pytest.mark.asyncio
async def test_read_failure_fails_closed_before_launch(tmp_path, caplog):
    store = JobMemoryStore(tmp_path / "jobmem")
    path = tmp_path / "jobmem" / "job-task-corrupt.jsonl"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"garbage line\n")
    launched = []

    async def fake_launch(**kwargs):
        launched.append(kwargs)
        return {"run_id": "run-1", "thread_id": kwargs["thread_id"]}

    task_repo = DummyTaskRepo([_task_row(task_id="task-corrupt")])
    run_repo = DummyRunRepo()
    service = _service(task_repo, run_repo, fake_launch, job_memory=store)

    with caplog.at_level(logging.ERROR, logger="app.scheduler.service"):
        result = await service.dispatch_task(task_repo.rows[0], now=datetime.now(UTC), trigger="scheduled")

    assert result["outcome"] == "failed"
    assert "corrupt journal" in result["error"]
    assert "line 1" in result["error"]
    # Fail closed: no run was launched and the occurrence slot was released.
    assert launched == []
    assert run_repo.updated[-1][1]["status"] == "failed"
    # The corrupt bytes were not repaired or rewritten by the failed read.
    assert path.read_bytes() == b"garbage line\n"


@pytest.mark.asyncio
async def test_outcome_write_failure_is_disclosed_never_faked(tmp_path, caplog):
    real = JobMemoryStore(tmp_path / "jobmem")

    class OutcomeFailingStore:
        """Real store whose outcome writes fail (dispatch writes succeed)."""

        def prior_context_block(self, job_id):
            return real.prior_context_block(job_id)

        def append(self, job_id, entry):
            if entry.get("event") == "outcome":
                raise JobMemoryWriteError("simulated disk failure on outcome append")
            real.append(job_id, entry)

    prompts = []

    async def fake_launch(**kwargs):
        prompts.append(kwargs["prompt"])
        return {"run_id": f"run-{len(prompts)}", "thread_id": kwargs["thread_id"]}

    task_repo = DummyTaskRepo([_task_row(task_id="task-wf")])
    run_repo = DummyRunRepo()
    service = _service(task_repo, run_repo, fake_launch, job_memory=OutcomeFailingStore())
    row = task_repo.rows[0]

    first = await service.dispatch_task(row, now=datetime.now(UTC), trigger="scheduled")
    assert first["outcome"] == "launched"

    with caplog.at_level(logging.ERROR, logger="app.scheduler.service"):
        await service.handle_run_completion(_completion("task-wf", first["task_run_id"], run_id=first["run_id"]))

    # The failure is logged at ERROR (not swallowed) ...
    assert any("journal write failed" in record.message for record in caplog.records)
    # ... and NO fake outcome entry was written in its place: the journal
    # still holds exactly the durable dispatch, with no terminal line.
    entries = real.read("task-wf")
    assert [entry["event"] for entry in entries] == ["dispatched"]
    # The first prompt could not have disclosed a failure that had not
    # happened yet; the second prompt must carry the disclosure verbatim.
    assert "journal write failed" not in prompts[0]

    second = await service.dispatch_task(row, now=datetime.now(UTC), trigger="scheduled")
    assert second["outcome"] == "launched"

    assert "Disclosures from scheduled job memory journal writes" in prompts[1]
    assert "journal write failed for occurrence task-run-" in prompts[1]
    assert "NO OUTCOME RECORDED" in prompts[1]


@pytest.mark.asyncio
async def test_outcome_without_observed_start_journals_null_duration(tmp_path):
    store = JobMemoryStore(tmp_path / "jobmem")
    store.append("task-ns", _dispatched_entry(task_id="task-ns", task_run_id="task-run-ns"))

    async def fake_launch(**_kwargs):
        raise AssertionError("launch must not be called")

    task_repo = DummyTaskRepo([_task_row(task_id="task-ns")])
    run_repo = DummyRunRepo()
    # Fresh service: it never observed the launch (e.g. after a restart).
    service = _service(task_repo, run_repo, fake_launch, job_memory=store)

    await service.handle_run_completion(_completion("task-ns", "task-run-ns", run_id="run-ns"))

    entries = store.read("task-ns")
    outcome = entries[1]
    assert outcome["event"] == "outcome"
    assert outcome["duration_seconds"] is None
    assert outcome["started_at"] is None
    assert "not observed by this process" in outcome["duration_note"]

    block = store.prior_context_block("task-ns")
    assert "duration=unknown (run start time was not observed by this process)" in block
    assert "launched before a restart" in block


@pytest.mark.asyncio
async def test_overlap_requeue_is_journaled_not_left_dangling(tmp_path):
    store = JobMemoryStore(tmp_path / "jobmem")

    async def conflict_launch(**_kwargs):
        raise ConflictError("Thread thread-x already has an active run")

    task_repo = DummyTaskRepo([_task_row(task_id="task-req")])
    run_repo = DummyRunRepo()
    service = _service(task_repo, run_repo, conflict_launch, job_memory=store)

    result = await service.dispatch_task(task_repo.rows[0], now=datetime.now(UTC), trigger="scheduled")

    assert result["outcome"] == "queued"
    entries = store.read("task-req")
    assert [entry["event"] for entry in entries] == ["dispatched", "outcome"]
    assert entries[1]["status"] == "requeued"
    block = store.prior_context_block("task-req")
    assert "outcome: requeued" in block
    assert "NO OUTCOME RECORDED" not in block


@pytest.mark.asyncio
async def test_pre_launch_failure_is_journaled_as_launch_failed(tmp_path):
    store = JobMemoryStore(tmp_path / "jobmem")
    launched = []

    async def broken_launch(**kwargs):
        launched.append(kwargs)
        raise RuntimeError("runtime refused to start the run")

    task_repo = DummyTaskRepo([_task_row(task_id="task-lf")])
    run_repo = DummyRunRepo()
    service = _service(task_repo, run_repo, broken_launch, job_memory=store)

    result = await service.dispatch_task(task_repo.rows[0], now=datetime.now(UTC), trigger="scheduled")

    assert result["outcome"] == "failed"
    assert len(launched) == 1
    entries = store.read("task-lf")
    assert [entry["event"] for entry in entries] == ["dispatched", "outcome"]
    assert entries[1]["status"] == "launch_failed"
    assert entries[1]["error"] == "runtime refused to start the run"
    assert entries[1]["duration_seconds"] is None


@pytest.mark.asyncio
async def test_dispatch_write_failure_fails_closed_before_launch(tmp_path):
    real = JobMemoryStore(tmp_path / "jobmem")

    class DispatchFailingStore:
        """Reads fine; every append fails (e.g. the disk went read-only)."""

        def prior_context_block(self, job_id):
            return real.prior_context_block(job_id)

        def append(self, job_id, entry):
            raise JobMemoryWriteError("simulated failure: dispatch could not be journaled")

    launched = []

    async def fake_launch(**kwargs):
        launched.append(kwargs)
        return {"run_id": "run-1", "thread_id": kwargs["thread_id"]}

    task_repo = DummyTaskRepo([_task_row(task_id="task-dw")])
    run_repo = DummyRunRepo()
    service = _service(task_repo, run_repo, fake_launch, job_memory=DispatchFailingStore())

    result = await service.dispatch_task(task_repo.rows[0], now=datetime.now(UTC), trigger="scheduled")

    # Fail closed while no run exists: the dispatch could not be journaled,
    # so the occurrence must not launch with unrecorded prior context.
    assert result["outcome"] == "failed"
    assert "dispatch could not be journaled" in result["error"]
    assert launched == []
    assert run_repo.updated[-1][1]["status"] == "failed"


def test_store_write_failure_on_unwritable_root_raises_typed_error(tmp_path):
    blocked = tmp_path / "file-in-the-way"
    blocked.write_text("not a directory", encoding="utf-8")
    store = JobMemoryStore(blocked / "jobmem")
    with pytest.raises(JobMemoryWriteError) as exc:
        store.append("task-dw", _dispatched_entry())
    assert "failed to durably append journal entry" in str(exc.value)


def test_path_hostile_job_id_rejected_not_traversed(tmp_path):
    store = JobMemoryStore(tmp_path / "jobmem")
    with pytest.raises(JobMemoryReadError):
        store.read("..")
    with pytest.raises(JobMemoryWriteError):
        store.append("", _dispatched_entry())
    # No journal file was created for either rejected id, inside or outside
    # the journal root.
    root = tmp_path / "jobmem"
    assert not (root / "job-...jsonl").exists()
    assert not (root / "job-.jsonl").exists()
    assert list(root.glob("job-*.jsonl")) == [] if root.exists() else True
