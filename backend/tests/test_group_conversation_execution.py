"""End-to-end group-chat and group-run execution against a stub model.

Everything here drives the *real* group machinery -- ``GroupChatService``,
``GroupOrchestrator``, every orchestration mode, and ``GroupRunService._execute``
admission / per-member retry / fan-in / moderator synthesis -- with only the
model layer replaced by :class:`StubModel`. The stub is not a canned reply: it
reads the system prompt and the user prompt the runner actually built, keys its
answer off the persona it finds there, and records every prompt it was handed.
That is what makes "what each member was told" an observation rather than an
assumption.

Three things are exercised for real:

* a **round-robin** room, driven turn by turn until the rotation closes, with
  three distinct personas;
* a **fan-out** room (``parallel`` mode), where one message summons every
  other member at once;
* an **autonomous group run**, the fan-out + synthesis path, with three
  members and a moderator, checking who spoke, what each was told, and what
  the moderator was handed to merge.

Plus the honesty invariants that no happy path can show: a synthesis pass that
completes with no deliverable, and a member whose result cannot be recorded
because the room receipt itself fails.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
from test_sibling_survival import (  # noqa: F401  (re-exported harness)
    _Status,
    _tool_provider,
    install_tools_stub,
    isolate_group_singletons,
    member_receipts,
    wait_for_terminal,
)

pytestmark = pytest.mark.asyncio

#: Three deliberately different personas, so "different personas" is checkable
#: rather than a claim: each one's deliverable must be traceable to the SOUL
#: and role the runner put in front of it.
PERSONAS: dict[str, dict[str, str]] = {
    "architect": {"role": "Systems Architect", "soul": "You are Axiom. You design interfaces and name the tradeoffs."},
    "coder": {"role": "Implementation Engineer", "soul": "You are Forge. You write concrete code and name the files."},
    "auditor": {"role": "Reliability Auditor", "soul": "You are Ledger. You list what could break and how it would be noticed."},
}

#: How long a wedged component is given before the runner gives up on it. It
#: replaces the real per-component execution budget (900s for a member, 900s for
#: the moderator) so the wedge tests run in milliseconds. The grace is pinned
#: to 0 alongside it, so the wait is exactly this.
_WEDGE_BUDGET_SECONDS = 0.15


# ---------------------------------------------------------------------------
# the stub model
# ---------------------------------------------------------------------------


class StubModel:
    """A persona-aware stand-in for the model behind ``SubagentExecutor``.

    Reads the real prompts, answers in the persona it finds there, and keeps a
    transcript of every call so a test can assert on what each member was
    actually told.
    """

    def __init__(self, *, empty_synthesis: bool = False) -> None:
        self.empty_synthesis = empty_synthesis
        #: ``{"member": [(system_prompt, prompt), ...]}`` in call order.
        self.calls: list[dict] = []
        #: ``{execution_id: call}``, so a concurrent poller can find *its own*
        #: submission rather than whichever member happened to go last.
        self.by_execution: dict[str, dict] = {}

    # -- prompt introspection ------------------------------------------------

    @staticmethod
    def persona_of(system_prompt: str) -> str:
        """Recover the speaker's handle from the system prompt the runner built."""
        match = re.search(r"You are @([A-Za-z0-9_-]+)", system_prompt)
        if match:
            return match.group(1)
        for name in PERSONAS:
            if f"You are @{name}" in system_prompt:
                return name
        return "unknown"

    @property
    def synthesis_calls(self) -> list[dict]:
        return [call for call in self.calls if call["member"] == "moderator"]

    @property
    def member_calls(self) -> dict[str, dict]:
        return {call["member"]: call for call in self.calls if call["member"] != "moderator"}

    # -- answering -----------------------------------------------------------

    def answer(self, system_prompt: str, prompt: str) -> str:
        if "moderating" in system_prompt:
            # Moderator synthesis pass: merge whatever the members produced.
            merged = re.findall(r"--- @([A-Za-z0-9_-]+) ---\n(.*?)(?=\n--- @|\Z)", prompt, re.S)
            parts = [f"{name}: {(body or '').strip()}" for name, body in merged]
            return "MERGED DELIVERABLE\n" + "\n".join(parts)
        member = self.persona_of(system_prompt)
        persona = PERSONAS.get(member, {"role": "Specialist", "soul": ""})
        if "Your slice as" in prompt:
            # Autonomous group run: each member gets the objective + its slice.
            objective = prompt.split("Team objective:", 1)[-1].split("\n\n", 1)[0].strip()
            return f"{persona['role']} slice of [{objective}] by @{member}"
        # Group chat: a conversational turn.
        return f"{persona['role']} reply as @{member} to: {prompt.strip()[:60]}"


class _StubExecutor:
    """``SubagentExecutor`` stand-in backed by :class:`StubModel`."""

    def __init__(self, model: StubModel, *, config=None, **_kwargs) -> None:
        self._model = model
        self._config = config

    def execute_async(self, prompt, task_id=None, **_kwargs):
        system_prompt = getattr(self._config, "system_prompt", "") or ""
        record = {
            "task_id": task_id,
            "member": "moderator" if "moderating" in system_prompt else StubModel.persona_of(system_prompt),
            "system_prompt": system_prompt,
            "prompt": prompt,
        }
        self._model.calls.append(record)
        self._model.by_execution[task_id] = record
        return task_id


def _install_stub_model(
    monkeypatch,
    *,
    empty_synthesis: bool = False,
) -> StubModel:
    """Wire the stub model into the runner and shrink its clock.

    Reuses the fault harness's boundary stubs (tool assembly, model config,
    runner clocks) and swaps only the executor so every prompt is observed.
    """
    from test_sibling_survival import _FAST_POLL_SECONDS, _FAST_RETRY_BACKOFF_CAP

    import alpha.groups.runner as runner
    import alpha.subagents.executor as executor_mod

    monkeypatch.setattr(runner, "_POLL_SECONDS", _FAST_POLL_SECONDS)
    monkeypatch.setattr(runner, "_RETRY_BACKOFF_CAP", _FAST_RETRY_BACKOFF_CAP)

    import alpha.config as agent_workspace_config
    import alpha.utils.assembly_io as assembly_io
    from alpha.subagents import config as subagent_config

    monkeypatch.setattr(agent_workspace_config, "get_app_config", lambda: SimpleNamespace(models=["stub-model"]))
    monkeypatch.setattr(subagent_config, "resolve_subagent_model_name", lambda *args, **kwargs: "stub-model")

    async def _no_tools(*_args, **_kwargs):
        return []

    monkeypatch.setattr(assembly_io, "run_assembly", _no_tools)
    install_tools_stub(monkeypatch)
    monkeypatch.setattr(executor_mod, "SubagentStatus", _Status)

    model = StubModel(empty_synthesis=empty_synthesis)
    monkeypatch.setattr(executor_mod, "SubagentExecutor", lambda **kwargs: _StubExecutor(model, **kwargs))
    monkeypatch.setattr(executor_mod, "get_background_task_result", _served_completion(model))
    return model


def _served_completion(model: StubModel):
    """A poller that hands back the stub's answer for whatever was submitted.

    Keyed by execution id, not by "the most recent call": with three members
    fanned out concurrently, "most recent" would hand a member another
    member's answer.
    """

    def _result(execution_id: str):
        record = model.by_execution.get(execution_id)
        if record is None:
            raise AssertionError(f"runner polled an execution id it never submitted: {execution_id!r}")
        answer = model.answer(record["system_prompt"], record["prompt"])
        if model.empty_synthesis and answer.startswith("MERGED DELIVERABLE"):
            # A moderator pass that "completed" without producing a deliverable.
            answer = ""
        return SimpleNamespace(status=_Status.COMPLETED, result=answer, error=None)

    return _result


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    isolate_group_singletons(monkeypatch, tmp_path)


def _persona_room(service, *, name: str, mode: str) -> str:
    """Create a room whose three members each carry a distinct persona.

    The registry's constructor pre-provisions the canonical roster
    (``architect``, ``coder``, ...) and ``get_or_create`` returns an existing
    bot untouched, so the role/SOUL are stamped onto the profile directly.
    Keeping the canonical names is deliberate: a persona has to reach the
    system prompt even when a catalog default already exists for that name.
    """
    from alpha.bots.registry import get_bot_registry

    registry = get_bot_registry()
    for member, spec in PERSONAS.items():
        profile = registry.get_or_create(member)
        profile.role = spec["role"]
        profile.soul = spec["soul"]
    room = service.get_or_create_room(
        name,
        topic="Incident review",
        members=list(PERSONAS),
        mode=mode,
        moderator="architect",
    )
    return room.name


def _speak(service, room_name: str, sender: str, content: str, *, intent: str = "discussion") -> list[str]:
    _, next_speakers = service.post_message(room_name, sender, content, intent=intent)
    return next_speakers


# ---------------------------------------------------------------------------
# 1. round-robin conversation
# ---------------------------------------------------------------------------


async def test_round_robin_room_rotates_through_every_persona(monkeypatch) -> None:
    """A round_robin room really rotates, and each reply is that persona's own.

    The rotation is driven message by message: each turn's speaker is the
    previous turn's ``next_speakers``, exactly as a channel adapter would, so
    the transcript is a genuine conversation rather than three unrelated posts.
    """
    from alpha.groups.service import get_group_chat_service

    model = _install_stub_model(monkeypatch)
    service = get_group_chat_service()
    room_name = _persona_room(service, name="incident-review", mode="round_robin")

    # A human opens the room; with no previous member turn, the orchestrator
    # hands the floor to the first member.
    speakers = _speak(service, room_name, "user", "Checkout latency tripled after the 14:02 deploy.")
    assert speakers == ["architect"], speakers

    spoken: list[str] = []
    for turn in range(6):
        speaker = speakers[0]
        system_prompt = f"You are @{speaker} ({PERSONAS[speaker]['role']}) in the 'incident-review' team room."
        answer = model.answer(system_prompt, f"turn {turn}")
        speakers = _speak(service, room_name, speaker, answer)
        spoken.append(speaker)
        assert len(speakers) == 1, f"round_robin must schedule exactly one speaker, got {speakers}"

    # Six turns over three members: the rotation is strict and it wraps.
    assert spoken == ["architect", "coder", "auditor", "architect", "coder", "auditor"], spoken

    room = service.get_room(room_name)
    assert room is not None
    # Every member's message was parsed for mentions by the real parser and
    # landed in the room log with its own sender.
    assert [m.sender for m in room.log] == ["user"] + spoken
    # A round_robin room does not fan out: one message schedules one reply.
    assert all(m.metadata == {} for m in room.log)


async def test_parallel_room_fans_one_message_out_to_every_other_member(monkeypatch) -> None:
    """``parallel`` mode summons every other member from a single message.

    This is the fan-out half of the conversation feature: one human turn, three
    independent specialist answers, no moderator in between.
    """
    from alpha.groups.service import get_group_chat_service

    model = _install_stub_model(monkeypatch)
    service = get_group_chat_service()
    room_name = _persona_room(service, name="incident-brainstorm", mode="parallel")

    speakers = _speak(service, room_name, "user", "What are the top three suspects? @everyone")
    assert set(speakers) == set(PERSONAS), speakers

    answers: dict[str, str] = {}
    for speaker in speakers:
        answers[speaker] = model.answer(
            f"You are @{speaker} ({PERSONAS[speaker]['role']}) in the 'incident-review' team room.",
            "What are the top three suspects? @everyone",
        )
        _speak(service, room_name, speaker, answers[speaker])
    _speak(service, room_name, "user", "thanks")

    room = service.get_room(room_name)
    assert room is not None
    # The room log is a complete, ordered account: human, three answers, human.
    assert [m.sender for m in room.log] == ["user", "architect", "coder", "auditor", "user"]
    # And each answer is visibly that persona's, not a shared template.
    assert len(set(answers.values())) == 3, answers
    for speaker, answer in answers.items():
        assert PERSONAS[speaker]["role"] in answer, (speaker, answer)


# ---------------------------------------------------------------------------
# 2. autonomous group run: fan-out + synthesis, with a stub model
# ---------------------------------------------------------------------------


async def test_group_run_fans_out_to_three_personas_and_synthesises(monkeypatch) -> None:
    """The real run: three personas fanned out, one moderator merging them.

    Records who spoke, the exact system prompt each was given (persona SOUL +
    role + team context), the exact slice prompt each was given (the shared
    objective, deltas by persona), and the moderator's merge input.
    """
    from alpha.groups.runner import get_group_run_service
    from alpha.groups.service import get_group_chat_service

    model = _install_stub_model(monkeypatch)
    service = get_group_chat_service()
    _persona_room(service, name="release-train", mode="moderated")

    run = get_group_run_service().start_run(
        "release-train",
        "Cut the p99 checkout latency regression",
        members=["architect", "coder", "auditor"],
        moderator="architect",
        max_parallel=3,
    )
    terminal = await wait_for_terminal(run.run_id)

    # (1) The run succeeded and every member produced its own deliverable.
    assert terminal.status == "succeeded", terminal.error
    assert set(terminal.member_results) == set(PERSONAS)
    for member in PERSONAS:
        entry = terminal.member_results[member]
        assert entry["status"] == "done", (member, entry)
        assert PERSONAS[member]["role"] in entry["output"], (member, entry)
        assert "Cut the p99 checkout latency regression" in entry["output"], (member, entry)

    # (2) What each member was told: its own SOUL and role, plus the shared
    # team context naming its teammates and the moderator.
    member_calls = model.member_calls
    assert set(member_calls) == set(PERSONAS), model.calls
    for member, call in member_calls.items():
        assert PERSONAS[member]["soul"] in call["system_prompt"], (member, call["system_prompt"])
        assert f"You are @{member} ({PERSONAS[member]['role']})" in call["system_prompt"]
        assert "never ask for clarification" in call["system_prompt"]
        for teammate in PERSONAS:
            assert teammate in call["system_prompt"]
        assert "Moderator: architect" in call["system_prompt"]
        # Every member was handed the same objective...
        assert call["prompt"].startswith("Team objective: Cut the p99 checkout latency regression")
        # ...and a slice that is genuinely its own.
        assert f"Your slice as @{member} ({PERSONAS[member]['role']})" in call["prompt"]

    # (3) Synthesis: the moderator was handed the objective AND every member's
    # output, and its merged answer names all three.
    synthesis_calls = model.synthesis_calls
    assert len(synthesis_calls) == 1, synthesis_calls
    merge_prompt = synthesis_calls[0]["prompt"]
    for member in PERSONAS:
        assert f"--- @{member} ---" in merge_prompt
        assert terminal.member_results[member]["output"] in merge_prompt
    for member in PERSONAS:
        assert f"{member}:" in terminal.synthesis, (member, terminal.synthesis)

    # (4) Every phase landed in the room log, so the Team page is a complete
    # account without polling a second surface.
    room = get_group_chat_service().get_room("release-train")
    assert room is not None
    phases = [m.metadata.get("phase") for m in room.log]
    assert phases == ["started", "member_result", "member_result", "member_result", "synthesis"], phases
    assert {m.sender for m in member_receipts("release-train", "member_result")} == set(PERSONAS)
    assert member_receipts("release-train", "synthesis")[0].sender == "architect"


# ---------------------------------------------------------------------------
# 3. the wedge the sibling/chaos suites do not reach
# ---------------------------------------------------------------------------


async def test_a_wedged_member_times_out_typed_and_does_not_swallow_the_run(monkeypatch) -> None:
    """One member that never reports a terminal status must not eat the run.

    ``run_member`` polls ``get_background_task_result`` until ``is_terminal``.
    Every fault in the sibling and chaos suites makes an execution *terminal*
    (FAILED / TIMED_OUT / CANCELLED, or a raise at submit), so none of them
    touches the case where a submission simply never finishes. Unbounded, that
    member holds the fan-in open forever: ``asyncio.gather`` never returns, so
    the post-join loop never surfaces a sibling's failure, the moderator pass
    never runs, and every member is left with no record at all. One stuck
    component silently swallows the run's whole accounting -- and the run stays
    ``running`` while the UI polls it.

    The wedge is bounded by the component's own execution budget, so the
    outcome is the ordinary typed ``timeout`` the failure path already knows how
    to report, on that member alone.
    """
    import alpha.groups.runner as runner
    from alpha.groups.runner import get_group_run_service
    from alpha.groups.service import get_group_chat_service

    model = _install_stub_model(monkeypatch)
    monkeypatch.setattr(runner, "_COMPONENT_WAIT_CAP_SECONDS", _WEDGE_BUDGET_SECONDS)
    monkeypatch.setattr(runner, "_DEADLINE_GRACE_SECONDS", 0.0)
    service = get_group_chat_service()
    _persona_room(service, name="wedge", mode="moderated")

    wedged = {"told": False}

    def _poller(execution_id: str):
        if ":coder:" in execution_id:
            # Genuinely in flight and genuinely never finishing.
            wedged["told"] = True
            return SimpleNamespace(status=_Status.RUNNING, result=None, error=None)
        record = model.by_execution[execution_id]
        return SimpleNamespace(
            status=_Status.COMPLETED,
            result=model.answer(record["system_prompt"], record["prompt"]),
            error=None,
        )

    import alpha.subagents.executor as executor_mod

    monkeypatch.setattr(executor_mod, "get_background_task_result", _poller)

    run = get_group_run_service().start_run(
        "wedge",
        "Deliver despite a wedged member",
        members=["architect", "coder", "auditor"],
    )
    terminal = await wait_for_terminal(run.run_id)

    # The wedged member really was polled (so the test is not short-circuiting).
    assert wedged["told"] is True
    # ...and it is reported as a typed, terminal, retried failure.
    entry = terminal.member_results["coder"]
    assert entry["status"] in {"failed", "cancelled"}, entry
    assert entry.get("error_type") == "timeout", entry
    assert "terminal status" in entry["error"], entry
    # The healthy siblings kept their work, and the moderator still merged it.
    for member in ("architect", "auditor"):
        assert terminal.member_results[member]["status"] == "done", (member, terminal.member_results[member])
    assert "MERGED DELIVERABLE" in terminal.synthesis, terminal.synthesis
    # And the run does not claim success.
    assert terminal.status == "failed", terminal.status
    assert "member @coder [timeout]" in (terminal.error or ""), terminal.error


async def test_a_wedged_synthesis_pass_times_out_instead_of_hanging_the_run(monkeypatch) -> None:
    """The same wedge on the moderator side must fail the run, not park it.

    The synthesis poll had the identical unbounded wait. A moderator pass that
    never reports a terminal status left the run ``running`` forever *after*
    every member had already delivered, so the work that landed was never
    merged and never reported.
    """
    import alpha.groups.runner as runner
    from alpha.groups.runner import get_group_run_service
    from alpha.groups.service import get_group_chat_service

    model = _install_stub_model(monkeypatch)
    monkeypatch.setattr(runner, "_COMPONENT_WAIT_CAP_SECONDS", _WEDGE_BUDGET_SECONDS)
    monkeypatch.setattr(runner, "_DEADLINE_GRACE_SECONDS", 0.0)
    service = get_group_chat_service()
    _persona_room(service, name="wedge-merge", mode="moderated")

    def _poller(execution_id: str):
        if execution_id.endswith(":synthesis"):
            return SimpleNamespace(status=_Status.RUNNING, result=None, error=None)
        record = model.by_execution[execution_id]
        return SimpleNamespace(
            status=_Status.COMPLETED,
            result=model.answer(record["system_prompt"], record["prompt"]),
            error=None,
        )

    import alpha.subagents.executor as executor_mod

    monkeypatch.setattr(executor_mod, "get_background_task_result", _poller)

    run = get_group_run_service().start_run(
        "wedge-merge",
        "Deliver despite a wedged moderator",
        members=["architect", "coder", "auditor"],
    )
    terminal = await wait_for_terminal(run.run_id)

    assert terminal.status == "failed", terminal.status
    assert "synthesis [timeout]" in (terminal.error or ""), terminal.error
    # The members' work is still preserved and was still posted.
    for member in PERSONAS:
        assert terminal.member_results[member]["status"] == "done", (member, terminal.member_results[member])
    assert {m.sender for m in member_receipts("wedge-merge", "member_result")} == set(PERSONAS)


# ---------------------------------------------------------------------------
# 3. honesty invariants the happy path cannot reach
# ---------------------------------------------------------------------------


async def test_a_synthesis_pass_that_delivers_nothing_is_not_a_successful_run(monkeypatch) -> None:
    """An empty moderator pass must not be reported as a delivered run.

    A member execution that completes with no output is already treated as a
    failure (``@member execution completed: no output``) and retried. The
    moderator pass had no such check: it accepted ``result.result or ""``, so a
    synthesis that "completed" without a deliverable produced ``synthesis=""``
    and a run whose status was ``succeeded`` -- the whole run claiming success
    with nothing in the field. Worse, it posted an *empty* ``phase:
    "synthesis"`` receipt into the room, so the transcript looked complete.

    The invariant is asymmetry-free: no component may report success without a
    deliverable.
    """
    from alpha.groups.runner import get_group_run_service
    from alpha.groups.service import get_group_chat_service

    _install_stub_model(monkeypatch, empty_synthesis=True)
    service = get_group_chat_service()
    _persona_room(service, name="empty-merge", mode="moderated")

    run = get_group_run_service().start_run(
        "empty-merge",
        "Deliver something",
        members=["architect", "coder", "auditor"],
        moderator="architect",
    )
    terminal = await wait_for_terminal(run.run_id)

    # Every member was fine, so the ONLY thing wrong is the moderator pass.
    for member in PERSONAS:
        assert terminal.member_results[member]["status"] == "done", (member, terminal.member_results[member])
    assert "member @" not in (terminal.error or ""), terminal.error

    # The run must not claim success, and must name the synthesis component.
    assert terminal.status == "failed", f"empty synthesis reported as {terminal.status!r}"
    assert "synthesis [" in (terminal.error or ""), terminal.error
    # And the member work that did land is still preserved and posted.
    assert {m.sender for m in member_receipts("empty-merge", "member_result")} == set(PERSONAS)


async def test_a_room_receipt_fault_is_disclosed_and_never_rewrites_a_delivered_member(monkeypatch) -> None:
    """A transcript fault must not become the member's verdict.

    ``run_member`` records the member's deliverable and *then* posts the room
    receipt. That post was unguarded, so a failing transcript raised out of
    ``run_member`` and the post-join loop re-recorded the member as a typed
    failure -- a member that genuinely delivered its work was reported as
    failed, and the healthy siblings' run was failed for a side effect of
    observability.

    The transcript is not part of any member's result, so a fault in it is
    disclosed on that member's own record (never silently) and changes nothing
    about whether the work landed.
    """
    import alpha.groups.service as group_service

    _install_stub_model(monkeypatch)
    service = group_service.get_group_chat_service()
    _persona_room(service, name="receipt-fault", mode="moderated")

    original_post = service.post_message

    def _post(room_name, sender, content, *, intent="discussion", metadata=None):
        if metadata and metadata.get("phase") == "member_result" and sender == "coder":
            raise OSError("room transcript is read-only")
        return original_post(room_name, sender, content, intent=intent, metadata=metadata)

    monkeypatch.setattr(service, "post_message", _post)

    from alpha.groups.runner import get_group_run_service

    run = get_group_run_service().start_run(
        "receipt-fault",
        "Deliver despite a broken transcript",
        members=["architect", "coder", "auditor"],
    )
    terminal = await wait_for_terminal(run.run_id)

    # The member's deliverable is kept, and its verdict is its own execution's.
    entry = terminal.member_results["coder"]
    assert entry["status"] == "done", entry
    assert "Implementation Engineer" in entry["output"], entry
    # The lost receipt is disclosed, not swallowed, and does not masquerade as a
    # component failure.
    assert "room transcript is read-only" in entry["transcript_error"], entry
    assert "error_type" not in entry, entry
    # The healthy siblings are untouched, and their receipts did land.
    for member in ("architect", "auditor"):
        assert terminal.member_results[member]["status"] == "done", (member, terminal.member_results[member])
        assert "transcript_error" not in terminal.member_results[member]
    assert {m.sender for m in member_receipts("receipt-fault", "member_result")} == {"architect", "auditor"}
    # The run is judged on its components, and every component delivered.
    assert terminal.status == "succeeded", terminal.error
    assert "MERGED DELIVERABLE" in terminal.synthesis, terminal.synthesis


async def test_concurrent_group_runs_keep_their_member_records_apart(monkeypatch) -> None:
    """Two runs in flight at once must not share member state.

    ``member_outputs`` is a per-run local, but ``retry_counts`` and
    ``member_results`` live on the shared ``GroupRun`` record, and the runner
    mutates them from several coroutines. Two overlapping runs on the same
    service must each end with exactly their own members' outputs.
    """
    from alpha.groups.runner import get_group_run_service
    from alpha.groups.service import get_group_chat_service

    _install_stub_model(monkeypatch)
    service = get_group_chat_service()
    _persona_room(service, name="crew-a", mode="moderated")
    _persona_room(service, name="crew-b", mode="moderated")

    runner = get_group_run_service()
    first = runner.start_run("crew-a", "Objective A", members=["architect", "coder"], max_parallel=2)
    second = runner.start_run("crew-b", "Objective B", members=["auditor", "coder"], max_parallel=2)

    first_terminal = await wait_for_terminal(first.run_id)
    second_terminal = await wait_for_terminal(second.run_id)

    assert set(first_terminal.member_results) == {"architect", "coder"}, first_terminal.member_results
    assert set(second_terminal.member_results) == {"auditor", "coder"}, second_terminal.member_results
    assert "Objective A" in first_terminal.member_results["architect"]["output"]
    assert "Objective B" in second_terminal.member_results["auditor"]["output"]
    assert first_terminal.status == "succeeded", first_terminal.error
    assert second_terminal.status == "succeeded", second_terminal.error


async def test_a_failure_receipt_fault_is_disclosed_on_the_failed_member(monkeypatch) -> None:
    """A lost *failure* receipt is disclosed, and the failure still fails the run.

    The mirror of the delivered-member case: whatever the transcript does, a
    member that did not deliver must never read as group success, and the
    reason its receipt was lost must be visible on its own record.
    """
    from alpha.groups.runner import get_group_run_service
    from alpha.groups.service import get_group_chat_service

    _install_stub_model(monkeypatch)
    service = get_group_chat_service()
    _persona_room(service, name="late-fault", mode="moderated")

    from test_sibling_survival import failed

    import alpha.subagents.executor as executor_mod

    original_post = service.post_message

    def _post(room_name, sender, content, *, intent="discussion", metadata=None):
        if metadata and metadata.get("phase") == "member_failed" and sender == "auditor":
            raise OSError("room transcript is read-only")
        return original_post(room_name, sender, content, intent=intent, metadata=metadata)

    monkeypatch.setattr(service, "post_message", _post)
    model = _install_stub_model(monkeypatch)

    def _poller(execution_id: str):
        if ":auditor:" in execution_id:
            return failed("auditor model provider refused the request")
        record = model.by_execution[execution_id]
        return SimpleNamespace(
            status=_Status.COMPLETED,
            result=model.answer(record["system_prompt"], record["prompt"]),
            error=None,
        )

    monkeypatch.setattr(executor_mod, "get_background_task_result", _poller)

    run = get_group_run_service().start_run(
        "late-fault",
        "Deliver despite a late fault",
        members=["architect", "coder", "auditor"],
    )
    terminal = await wait_for_terminal(run.run_id)

    # The member that failed is terminal, typed, and named on the run.
    entry = terminal.member_results["auditor"]
    assert entry["status"] == "failed", entry
    assert entry.get("error_type") == "dependency_error", entry
    assert "room transcript is read-only" in entry["transcript_error"], entry
    assert terminal.status == "failed", terminal.status
    assert "member @auditor [" in (terminal.error or ""), terminal.error
    # The healthy siblings are not polluted by its transcript fault.
    for member in ("architect", "coder"):
        assert terminal.member_results[member]["status"] == "done", (member, terminal.member_results[member])
        assert "transcript_error" not in terminal.member_results[member]


@pytest.mark.parametrize("mode", ["mention", "moderated", "quorum", "parallel", "round_robin"])
async def test_every_advertised_orchestration_mode_is_reachable_and_schedules_speakers(monkeypatch, mode: str) -> None:
    """All five modes the rooms API advertises actually schedule speakers.

    ``/api/groups`` validates ``mode`` against
    ``("mention", "moderated", "quorum", "parallel", "round_robin")``; a mode
    that silently returned nothing would leave the room looking alive and dead.
    """
    from alpha.groups.service import get_group_chat_service

    _install_stub_model(monkeypatch)
    service = get_group_chat_service()
    room_name = _persona_room(service, name=f"mode-{mode}", mode=mode)

    speakers = _speak(service, room_name, "user", "status please @everyone")
    assert speakers, f"mode {mode!r} scheduled nobody"
    room = service.get_room(room_name)
    assert room is not None
    # Every scheduled speaker is a real member of the room.
    assert set(speakers) <= set(PERSONAS), (mode, speakers)
    # A member's own reply never re-schedules whoever just spoke in the
    # fan-out modes (the speaker is excluded by design), and in round_robin it
    # always advances to somebody else.
    for speaker in speakers:
        follow_up = _speak(service, room_name, speaker, "done")
        assert speaker not in follow_up, (mode, follow_up)


def _runner_source_text() -> str:
    from pathlib import Path

    import alpha.groups.runner as runner_mod

    return Path(runner_mod.__file__).read_text(encoding="utf-8")
