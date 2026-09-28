"""A run must record the model that actually served it.

Found by assigning a real task to the agent and reading the persisted run row
afterwards. The run genuinely worked - it wrote a file, read it back, and
answered - and the row recorded every token honestly:

    total_input_tokens  = 150885
    total_output_tokens = 134
    total_tokens        = 151019
    llm_call_count      = 3

but also this:

    model                = null
    token_usage_by_model = {"unknown": {"input_tokens": 150885, ...}}

So the money was counted but attributed to a model literally named "unknown",
which is what the console's per-model cost column reads. The cause is a guard in
`runtime/runs/worker.py`: the model-name sync ran only when the record *already*
had a model name, so it could correct a name but never supply a missing one.

That matters on the primary path, not an edge case. The web client selects a
model with `config.configurable.model_name` (see `ChatView.tsx`), which the
agent honours but which never populated `record.model_name`. Only a caller using
`context.model_name` got attribution.
"""

from __future__ import annotations

import asyncio

import pytest

from alpha.runtime.runs.manager import RunRecord, RunStartOutcome
from alpha.runtime.runs.schemas import DisconnectMode, RunStatus
from alpha.runtime.runs.worker import RunContext, run_agent


class _FakeAgent:
    def __init__(self, metadata: dict) -> None:
        self.metadata = metadata
        self.checkpointer = None
        self.store = None
        self.interrupt_before_nodes: list[str] = []
        self.interrupt_after_nodes: list[str] = []

    async def astream(self, graph_input, *, config, stream_mode, **kwargs):
        return
        yield  # pragma: no cover (makes this an async generator)


class _RecordingRunManager:
    """Records every ``update_model_name`` the worker performs."""

    def __init__(self) -> None:
        self.model_name_updates: list[tuple[str, str | None]] = []

    async def try_start(self, _run_id: str) -> RunStartOutcome:
        return RunStartOutcome.started

    async def wait_for_prior_finalizing(self, *_a, **_k) -> None:
        return None

    async def has_later_run(self, *_a, **_k) -> bool:
        return False

    async def has_later_started_run(self, *_a, **_k) -> bool:
        return False

    async def set_status(self, *_a, **_k) -> None:
        return None

    async def set_status_if_not_cancelled(self, *_a, **_k) -> None:
        return None

    async def update_model_name(self, run_id: str, model_name: str | None) -> None:
        self.model_name_updates.append((run_id, model_name))

    async def update_run_completion(self, *_a, **_k) -> None:
        return None

    async def cleanup(self, *_a, **_k) -> None:
        return None


class _FakeBridge:
    async def publish(self, *_a, **_k) -> None:
        return None

    async def publish_end(self, *_a) -> None:
        return None

    async def cleanup(self, *_a, **_k) -> None:
        return None


async def _run_with(*, record_model_name: str | None, agent_metadata: dict) -> list[tuple[str, str | None]]:
    agent = _FakeAgent(agent_metadata)
    manager = _RecordingRunManager()
    record = RunRecord(
        run_id="run-model-attribution",
        thread_id="thread-model-attribution",
        assistant_id="lead_agent",
        status=RunStatus.pending,
        on_disconnect=DisconnectMode.cancel,
        model_name=record_model_name,
    )
    record.abort_event = asyncio.Event()

    await run_agent(
        _FakeBridge(),
        manager,
        record,
        ctx=RunContext(checkpointer=None),
        agent_factory=lambda **_k: agent,
        graph_input={"messages": []},
        config={"configurable": {"model_name": "alpha-free"}},
    )
    return manager.model_name_updates


class TestTheServedModelIsRecorded:
    @pytest.mark.asyncio
    async def test_a_model_chosen_at_runtime_is_recorded_onto_a_null_row(self) -> None:
        """The web-UI path: `config.configurable.model_name`, no prior record value."""
        updates = await _run_with(record_model_name=None, agent_metadata={"model_name": "alpha-free"})
        assert updates == [("run-model-attribution", "alpha-free")], (
            "a run whose model was selected through the runtime config left `model: null` on its row, so its tokens were attributed to 'unknown' and the console cost column stayed empty"
        )

    @pytest.mark.asyncio
    async def test_a_requested_name_that_resolved_differently_is_corrected(self) -> None:
        """The original purpose of this sync, still working."""
        updates = await _run_with(record_model_name="not-in-allowlist", agent_metadata={"model_name": "union-alpha"})
        assert updates == [("run-model-attribution", "union-alpha")]

    @pytest.mark.asyncio
    async def test_an_already_correct_name_is_not_rewritten(self) -> None:
        updates = await _run_with(record_model_name="alpha-free", agent_metadata={"model_name": "alpha-free"})
        assert updates == [], "a name that already matches must not trigger a durable write"


class TestNoNameIsInvented:
    @pytest.mark.asyncio
    async def test_missing_agent_metadata_records_nothing(self) -> None:
        """An absent answer stays absent; 'unknown' must come from the data, not a guess."""
        updates = await _run_with(record_model_name=None, agent_metadata={})
        assert updates == []

    @pytest.mark.asyncio
    async def test_a_null_metadata_mapping_is_tolerated(self) -> None:
        updates = await _run_with(record_model_name=None, agent_metadata=None)  # type: ignore[arg-type]
        assert updates == []

    @pytest.mark.asyncio
    async def test_a_non_dict_metadata_is_tolerated(self) -> None:
        updates = await _run_with(record_model_name=None, agent_metadata=["not", "a", "dict"])  # type: ignore[arg-type]
        assert updates == []

    @pytest.mark.asyncio
    async def test_a_non_string_model_name_is_ignored(self) -> None:
        updates = await _run_with(record_model_name=None, agent_metadata={"model_name": 42})
        assert updates == [], "a non-string model name must not be persisted as one"
