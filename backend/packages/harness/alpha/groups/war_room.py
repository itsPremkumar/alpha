"""The war room: a staged, clock-bounded group deliberation inside a chat channel.

This is a *scheduler over the existing engines*, not a second deliberation
system. Reasoning comes from :mod:`alpha.deliberation` (via a participant
callable), vote recording and tallying come from :class:`alpha.groups.quorum.QUorumEngine`,
participants are real subagents through the same
:class:`alpha.subagents.executor.SubagentExecutor` seam ``groups/runner.py``
uses, and every transition lands in the one governance ledger.

The five invariants this module exists to hold, each with a test:

1. **A wedged participant cannot hold the room open.** Every stage is bounded
   by its own ``timeout_seconds`` plus grace on an injected monotonic clock. A
   participant that never returns is cancelled at the hard bound. If a stage
   cannot reach its quorum inside its bound the run ends in the typed status
   ``timeout``. It never sits in ``running``. (A prior audit of this codebase
   found exactly this unbounded-wait bug.)

2. **One member's failure never degrades a sibling.** Each participant is run
   as an independently bounded task and gathered with
   ``return_exceptions=True``. A failure is recorded in that member's own
   receipt; it cannot cancel, delay or truncate another member's contribution.

3. **Synthesis fails loudly on empty output.** An empty synthesis is a FAILED
   run with ``synthesis=None`` and a named ``synthesis_error``. It is never
   published as ``synthesis=""`` with ``status="succeeded"`` and an empty
   receipt, which is the exact shape that once made an incomplete transcript
   look complete.

4. **A transcript fault cannot rewrite a delivered verdict.** Verdicts are
   finalised in memory first; a transcript append failure is recorded in
   ``transcript_errors`` and never mutates a member's status. Conversely a
   failure receipt that cannot be persisted is escalated to a ``receipt_loss``
   on the run, never swallowed.

5. **Quorum is explicit, configurable and reported.** ``all`` / ``any`` /
   ``majority`` / ``supermajority``. The run records which policy it used, the
   threshold that policy implies, and the vote tally behind the outcome.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from alpha.channels import ledger as channel_ledger
from alpha.channels.timing import Clock, Deadline, RoomIntake, StageBudget, monotonic_clock, run_bounded
from alpha.channels.transcript import TranscriptStore, utc_now_iso
from alpha.groups.quorum import QuorumEngine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# vocabulary
# ---------------------------------------------------------------------------
class QuorumPolicy(StrEnum):
    """How many contributions must land for a stage to count as decided."""

    ALL = "all"
    ANY = "any"
    MAJORITY = "majority"
    SUPERMAJORITY = "supermajority"


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    #: Some members failed or timed out but the quorum held and synthesis landed.
    PARTIAL = "partial"
    FAILED = "failed"
    #: A stage could not reach quorum inside its bound. Terminal, not hanging.
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    TIMEOUT = "timeout"
    FAILED = "failed"
    SKIPPED = "skipped"


class MemberStatus(StrEnum):
    CONTRIBUTED = "contributed"
    #: Returned successfully but produced nothing. Never counted as agreement.
    EMPTY = "empty"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"

    @property
    def is_delivery(self) -> bool:
        return self is MemberStatus.CONTRIBUTED


#: The default stage sequence. Deliberately short: a war room that cannot reach
#: a decision in three stages should escalate, not deliberate longer.
DEFAULT_STAGE_NAMES: tuple[str, ...] = ("positions", "cross_exam", "synthesis")


class QuorumError(RuntimeError):
    """The quorum could not be evaluated. The run fails closed."""


class LedgerUnavailable(RuntimeError):
    """The governance ledger refused the run. The run does not start."""


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class StageSpec:
    """One bounded stage."""

    name: str
    prompt: str
    budget: StageBudget
    #: False for a stage that runs but collects nothing (a pure moderator pass).
    collects: bool = True
    #: Skip this stage when the previous stage did not reach quorum.
    requires_previous_quorum: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "prompt": self.prompt,
            "budget": self.budget.to_dict(),
            "collects": self.collects,
            "requires_previous_quorum": self.requires_previous_quorum,
        }


@dataclass(frozen=True, slots=True)
class WarRoomConfig:
    """Everything a room needs to open, validated at construction."""

    topic: str
    participants: tuple[str, ...]
    stages: tuple[StageSpec, ...]
    quorum_policy: QuorumPolicy = QuorumPolicy.MAJORITY
    moderator: str | None = None
    #: Overrides the count the policy implies. Clamped to at least 1.
    quorum_min_votes: int | None = None
    max_parallel_participants: int = 4
    #: Refuse to open a room with fewer live participants than this.
    min_participants: int = 2

    def __post_init__(self) -> None:
        topic = (self.topic or "").strip()
        if not topic:
            raise ValueError("a war room needs a topic")
        if len(topic) > 20000:
            raise ValueError("war room topic exceeds 20000 characters")
        participants = tuple(dict.fromkeys(p.strip() for p in self.participants if p and p.strip()))
        if len(participants) < self.min_participants:
            raise ValueError(
                f"war room needs at least {self.min_participants} participants, got {len(participants)}"
            )
        if not self.stages:
            raise ValueError("a war room needs at least one stage")
        object.__setattr__(self, "topic", topic)
        object.__setattr__(self, "participants", participants)
        if self.max_parallel_participants < 1:
            raise ValueError("max_parallel_participants must be >= 1")

    def required_votes(self, eligible: int | None = None) -> int:
        """How many delivered contributions the policy demands.

        This is the single place the policy is turned into a number, so the
        reported threshold and the enforced threshold cannot drift apart.
        """
        pool = int(eligible if eligible is not None else len(self.participants))
        pool = max(1, pool)
        if self.quorum_min_votes is not None:
            return max(1, min(int(self.quorum_min_votes), pool))
        policy = QuorumPolicy(self.quorum_policy)
        if policy is QuorumPolicy.ALL:
            return pool
        if policy is QuorumPolicy.ANY:
            return 1
        if policy is QuorumPolicy.MAJORITY:
            return pool // 2 + 1
        # supermajority: strictly more than two thirds.
        return (2 * pool) // 3 + 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "participants": list(self.participants),
            "stages": [s.to_dict() for s in self.stages],
            "quorum_policy": str(self.quorum_policy),
            "moderator": self.moderator,
            "quorum_min_votes": self.quorum_min_votes,
            "required_votes": self.required_votes(),
            "max_parallel_participants": self.max_parallel_participants,
        }


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class MemberReceipt:
    """One participant's outcome for one stage. Final once written."""

    stage: str
    participant: str
    status: str
    output: str = ""
    error: str = ""
    error_type: str = ""
    started_at: float = 0.0
    duration_ms: int = 0
    seq: int = 0
    #: Set when this receipt could not be persisted. Never silently dropped.
    persistence_error: str = ""

    @property
    def delivered(self) -> bool:
        return MemberStatus(self.status) is MemberStatus.CONTRIBUTED

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class QuorumDecision:
    """Which policy decided this stage, and on what evidence."""

    stage: str
    policy: str
    required_votes: int
    eligible_voters: int
    agree: int
    disagree: int
    amend: int
    total_votes: int
    passed: bool
    proposal_id: str = ""
    engine_status: str = ""
    #: False when QuorumEngine's own tally verdict disagrees with the policy
    #: evaluation. Recorded rather than smoothed over.
    engine_agrees_with_policy: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def human_line(self) -> str:
        verdict = "PASSED" if self.passed else "NOT MET"
        return (
            f"stage={self.stage} policy={self.policy} required={self.required_votes} "
            f"agree={self.agree} disagree={self.disagree} amend={self.amend} "
            f"of eligible={self.eligible_voters} -> {verdict}"
        )


@dataclass(slots=True)
class StageResult:
    name: str
    status: str
    budget: dict[str, Any] = field(default_factory=dict)
    receipts: list[MemberReceipt] = field(default_factory=list)
    quorum: QuorumDecision | None = None
    synthesis: str = ""
    error: str = ""
    started_at: str = ""
    finished_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "budget": dict(self.budget),
            "receipts": [r.to_dict() for r in self.receipts],
            "quorum": self.quorum.to_dict() if self.quorum else None,
            "synthesis": self.synthesis,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


@dataclass(slots=True)
class WarRoomRun:
    """The whole run, durable and replayable."""

    run_id: str
    room: str
    config: WarRoomConfig
    status: str = RunStatus.PENDING
    stages: list[StageResult] = field(default_factory=list)
    synthesis: str | None = None
    error: str = ""
    #: Named reason the run is not succeeded. Never an empty string with a
    #: success status.
    failure_reason: str = ""
    started_at: str = field(default_factory=utc_now_iso)
    finished_at: str = ""
    killed_by: str = ""
    #: Transcript append failures. A fault here NEVER rewrites a receipt.
    transcript_errors: list[str] = field(default_factory=list)
    #: Receipts that could not be persisted. Non-empty means the record lies.
    receipt_loss: list[str] = field(default_factory=list)
    final_quorum: QuorumDecision | None = None
    schema_version: int = 1

    @property
    def terminal(self) -> bool:
        return self.status in {
            RunStatus.SUCCEEDED,
            RunStatus.PARTIAL,
            RunStatus.FAILED,
            RunStatus.TIMEOUT,
            RunStatus.CANCELLED,
        }

    def stage(self, name: str) -> StageResult | None:
        for result in self.stages:
            if result.name == name:
                return result
        return None

    def all_receipts(self) -> list[MemberReceipt]:
        return [r for stage in self.stages for r in stage.receipts]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "room": self.room,
            "status": str(self.status),
            "config": self.config.to_dict(),
            "stages": [s.to_dict() for s in self.stages],
            "synthesis": self.synthesis,
            "error": self.error,
            "failure_reason": self.failure_reason,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "killed_by": self.killed_by,
            "transcript_errors": list(self.transcript_errors),
            "receipt_loss": list(self.receipt_loss),
            "final_quorum": self.final_quorum.to_dict() if self.final_quorum else None,
        }


# ---------------------------------------------------------------------------
# the participant seam
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class ContributionContext:
    """Everything a participant is told, and everything it must return."""

    run_id: str
    room: str
    topic: str
    stage: StageSpec
    participant: str
    previous_outputs: Mapping[str, str] = field(default_factory=dict)
    moderator: str | None = None
    intake: RoomIntake | None = None
    transcript: TranscriptStore | None = None

    def prompt(self) -> str:
        prior = ""
        if self.previous_outputs:
            rendered = "\n".join(
                f"--- @{name} ---\n{text}" for name, text in self.previous_outputs.items() if text
            )
            prior = f"\n\nEarlier in this room:\n{rendered}"
        return (
            f"War room topic: {self.topic}\n"
            f"Stage: {self.stage.name}\n"
            f"Your instruction: {self.stage.prompt}\n"
            f"{prior}\n\n"
            f"Answer as @{self.participant}. Your answer is your only contribution to this "
            "stage; it is recorded verbatim and attributed to you."
        )


#: A participant. Real runtime supplies :class:`SubagentContributor`; tests
#: supply a plain coroutine function. Both are the same seam, which is why the
#: timeout and isolation tests exercise the real scheduler.
Participant = Callable[[ContributionContext], Awaitable[str]]


class SubagentParticipant:
    """The real runtime participant: a subagent through the group runner's seam.

    Uses the same three calls ``groups/runner.py`` uses
    (:class:`SubagentExecutor`, ``get_background_task_result``,
    ``request_cancel_background_task``) so a war-room participant is a normal
    delegated subagent and not a bespoke execution path.
    """

    def __init__(
        self,
        *,
        agent_type: str = "general-purpose",
        max_turns: int = 20,
        timeout_seconds: float = 900.0,
        disallowed_tools: Sequence[str] | None = None,
    ) -> None:
        self.agent_type = agent_type
        self.max_turns = max_turns
        self.timeout_seconds = timeout_seconds
        self.disallowed_tools = tuple(disallowed_tools or ("task", "ralph_loop"))
        self._cancel: Callable[[str], None] | None = None

    def bind_canceller(self, cancel: Callable[[str], None]) -> None:
        self._cancel = cancel

    async def __call__(self, ctx: ContributionContext) -> str:
        from alpha.subagents.config import SubagentConfig
        from alpha.subagents.executor import SubagentExecutor

        config = SubagentConfig(
            name=f"warroom-{ctx.participant}-{ctx.stage.name}",
            max_turns=self.max_turns,
            timeout_seconds=self.timeout_seconds,
            disallowed_tools=list(self.disallowed_tools),
        )
        executor = SubagentExecutor(config=config, agent_type=self.agent_type)
        execution_id = executor.execute_async(
            ctx.prompt(), task_id=f"{ctx.run_id}:{ctx.stage.name}:{ctx.participant}"
        )
        if self._cancel is not None:
            self._cancel(execution_id)
        from alpha.subagents.executor import get_background_task_result

        while True:
            result = get_background_task_result(execution_id)
            if result is not None:
                break
            await asyncio.sleep(2.0)
        status = str(getattr(result, "status", ""))
        text = str(getattr(result, "result", "") or "")
        if status and status.upper() not in {"COMPLETED", "SUCCESS", "SUCCEEDED"}:
            raise RuntimeError(f"subagent for @{ctx.participant} ended with status {status!r}")
        return text


# ---------------------------------------------------------------------------
# the scheduler
# ---------------------------------------------------------------------------
class WarRoom:
    """A single scheduled, staged, bounded deliberation."""

    def __init__(
        self,
        config: WarRoomConfig,
        *,
        participants: Mapping[str, Participant],
        moderator: Participant | None = None,
        room: str = "war-room",
        root: str | Path,
        clock: Clock = monotonic_clock,
        ledger_store: Any | None = None,
        engine: QuorumEngine | None = None,
        debounce_seconds: float = 0.5,
    ) -> None:
        missing = [p for p in config.participants if p not in participants]
        if missing:
            raise ValueError(f"no participant implementation for: {', '.join(missing)}")
        self.config = config
        self.participants = dict(participants)
        self.moderator = moderator
        self.room = room
        self.clock = clock
        self.ledger_store = ledger_store
        self.engine = engine or QuorumEngine()
        self.debounce_seconds = debounce_seconds

        self.run_id = f"wrun_{uuid4().hex[:12]}"
        self.dir = Path(root) / room / self.run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.transcript = TranscriptStore(self.dir / "transcript.jsonl")
        self.run = WarRoomRun(run_id=self.run_id, room=room, config=config)
        self._cancelled = asyncio.Event()
        self._kill_watcher: asyncio.Task[None] | None = None
        self._saved = False

    # -- kill switch ------------------------------------------------------
    def _kill_switch_engaged(self) -> tuple[bool, str]:
        """Read the existing fleet kill switch. Absent module is NOT 'off'."""
        try:
            from alpha.bots.kill_switch import is_kill_switch_active
        except Exception as exc:  # noqa: BLE001
            # Fail closed: an unreadable kill switch is not a healthy one.
            return True, f"kill switch unavailable ({type(exc).__name__}: {exc}); treating as engaged"
        try:
            return is_kill_switch_active()
        except Exception as exc:  # noqa: BLE001
            return True, f"kill switch raised {type(exc).__name__}: {exc}; treating as engaged"

    async def _watch_kill_switch(self) -> None:
        """Poll the kill switch while the run is live.

        Polling is deliberate: the switch is process-local module state with no
        notification channel, so a watch is the only way a long stage notices it.
        """
        while not self._cancelled.is_set():
            await asyncio.sleep(0.05)
            engaged, reason = self._kill_switch_engaged()
            if engaged:
                self.run.killed_by = reason
                self._cancelled.set()
                return

    def cancel(self, reason: str = "cancelled by operator") -> None:
        self._cancelled.set()
        self.run.killed_by = self.run.killed_by or reason

    # -- persistence ------------------------------------------------------
    def _persist(self) -> None:
        tmp = self.dir / "run.json.tmp"
        tmp.write_text(
            json.dumps(self.run.to_dict(), indent=2, ensure_ascii=True), encoding="utf-8", newline="\n"
        )
        os.replace(tmp, self.dir / "run.json")
        self._saved = True

    def _ledger(self, event: str, **kwargs: Any) -> None:
        channel_ledger.append(
            event,
            actor=str(kwargs.pop("actor", "war_room")),
            target=str(kwargs.pop("target", self.run_id)),
            reason=str(kwargs.pop("reason", "")),
            details=kwargs,
            store=self.ledger_store,
        )

    def _transcript(self, author: str, body: str, kind: str, **meta: Any) -> int:
        """Append to the transcript. A fault is recorded, never propagated as a verdict change."""
        try:
            message = self.transcript.append(author, body, kind=kind, metadata=dict(meta))
        except Exception as exc:  # noqa: BLE001
            note = f"transcript append failed for {kind}: {type(exc).__name__}: {exc}"
            self.run.transcript_errors.append(note)
            logger.error("%s", note)
            return 0
        return message.seq

    def _record_receipt(self, receipt: MemberReceipt) -> None:
        """Persist a receipt. A failure to persist is escalated, never swallowed."""
        try:
            message = self.transcript.append(
                receipt.participant,
                receipt.output or receipt.error or f"({receipt.status})",
                kind=f"receipt:{receipt.status}",
                metadata={"stage": receipt.stage, "duration_ms": receipt.duration_ms},
            )
        except Exception as exc:  # noqa: BLE001
            receipt.persistence_error = f"{type(exc).__name__}: {exc}"
            self.run.receipt_loss.append(
                f"stage={receipt.stage} participant={receipt.participant} status={receipt.status}: "
                f"{receipt.persistence_error}"
            )
            logger.error("receipt loss: %s", receipt.persistence_error)
            return
        receipt.seq = message.seq

    # -- quorum -----------------------------------------------------------
    def _tally(self, stage_name: str, receipts: Sequence[MemberReceipt]) -> QuorumDecision:
        """Record votes in the QuorumEngine and evaluate the configured policy."""
        eligible = len(self.config.participants)
        required = self.config.required_votes(eligible)
        # Set the engine threshold to exactly what the policy demands, so the
        # engine's own approve test is the policy test.
        proposal = self.engine.create_proposal(
            room_id=self.run_id,
            proposer=self.config.moderator or "war_room",
            question=f"stage {stage_name}: did the room reach a position?",
            threshold=required / eligible,
        )
        for receipt in receipts:
            if receipt.status == MemberStatus.CONTRIBUTED:
                choice = "agree"
            elif receipt.status == MemberStatus.EMPTY:
                choice = "amend"
            else:
                choice = "disagree"
            self.engine.cast_vote(proposal.proposal_id, receipt.participant, choice)

        tally = self.engine.tally(proposal.proposal_id, total_eligible_voters=eligible)
        if tally.get("status") == "error":
            raise QuorumError(f"quorum tally failed for stage {stage_name!r}: {tally.get('error')}")
        agree = int(tally.get("agree", 0))
        passed = agree >= required
        engine_passed = str(tally.get("status")) == "approved"
        return QuorumDecision(
            stage=stage_name,
            policy=str(self.config.quorum_policy),
            required_votes=required,
            eligible_voters=eligible,
            agree=agree,
            disagree=int(tally.get("disagree", 0)),
            amend=int(tally.get("amend", 0)),
            total_votes=int(tally.get("total_votes", 0)),
            passed=passed,
            proposal_id=proposal.proposal_id,
            engine_status=str(tally.get("status")),
            engine_agrees_with_policy=(engine_passed == passed),
        )

    # -- intake -----------------------------------------------------------
    def _intake(self, stage_name: str) -> RoomIntake:
        """Fresh per-stage intake. Constructing it here is what makes
        :mod:`alpha.channels.debounce` reachable from a real runtime path."""
        return RoomIntake(
            f"{self.run_id}:{stage_name}", debounce_seconds=self.debounce_seconds
        )

    # -- one stage --------------------------------------------------------
    async def _run_stage(self, stage: StageSpec, prior: Mapping[str, str]) -> tuple[StageResult, dict[str, str]]:
        budget = stage.budget
        deadline = Deadline(budget, clock=self.clock)
        result = StageResult(
            name=stage.name,
            status=StageStatus.RUNNING,
            budget=budget.to_dict(),
            started_at=utc_now_iso(),
        )
        self._ledger(
            channel_ledger.EV_STAGE,
            actor="war_room",
            reason=f"stage {stage.name} started",
            stage=stage.name,
            timeout_seconds=budget.timeout_seconds,
            grace_seconds=budget.grace_seconds,
            run_status=str(self.run.status),
        )
        self._transcript(
            "war_room",
            f"stage {stage.name} opened with a {budget.total_seconds:.3f}s bound",
            "stage_open",
            stage=stage.name,
        )

        intake = self._intake(stage.name) if stage.collects else None
        semaphore = asyncio.Semaphore(max(1, self.config.max_parallel_participants))

        async def _contribute(name: str) -> MemberReceipt:
            """One participant, independently bounded. NEVER raises.

            The bound is a slice of the STAGE budget, not the whole of it, so one
            slow agent cannot consume the stage and starve its siblings. Queue
            time for the concurrency semaphore is inside the slice: a participant
            that never gets a slot is TIMEOUT, which is the honest answer, not a
            participant that silently waited forever.

            The outer ``except BaseException`` is the isolation guarantee in code
            form: whatever a participant does, this function returns a receipt.
            """
            receipt = MemberReceipt(stage=stage.name, participant=name, status=MemberStatus.FAILED)
            started = self.clock()
            receipt.started_at = started
            slot_timed_out = False
            try:
                share = max(
                    0.05,
                    deadline.remaining() / max(1, self.config.max_parallel_participants),
                )
                try:
                    await asyncio.wait_for(semaphore.acquire(), timeout=share)
                except TimeoutError:
                    # No slot in time. Recorded distinctly from a participant
                    # that got a slot and then wedged: both are timeouts, but
                    # only one of them is a stuck agent.
                    slot_timed_out = True
                    raise
                try:
                    ctx = ContributionContext(
                        run_id=self.run_id,
                        room=self.room,
                        topic=self.config.topic,
                        stage=stage,
                        participant=name,
                        previous_outputs=prior,
                        moderator=self.config.moderator,
                        intake=intake,
                        transcript=self.transcript,
                    )
                    participant_deadline = Deadline(
                        StageBudget(
                            name=f"{stage.name}:{name}",
                            timeout_seconds=share,
                            grace_seconds=stage.budget.grace_seconds,
                        ),
                        clock=self.clock,
                    )
                    status, value, error = await run_bounded(
                        lambda: self.participants[name](ctx), participant_deadline
                    )
                finally:
                    semaphore.release()

                receipt.duration_ms = int((self.clock() - started) * 1000)
                if status == "timeout":
                    receipt.status = MemberStatus.TIMEOUT
                    receipt.error = error
                    receipt.error_type = "timeout"
                elif status == "cancelled":
                    receipt.status = MemberStatus.CANCELLED
                    receipt.error = error
                    receipt.error_type = "cancelled"
                elif status == "failed":
                    receipt.status = MemberStatus.FAILED
                    receipt.error = error
                    receipt.error_type = error.split(":", 1)[0] if error else "error"
                else:
                    text = "" if value is None else str(value)
                    if not text.strip():
                        # A successful call that delivered nothing is EMPTY, not
                        # CONTRIBUTED. This is what stops an empty receipt from
                        # counting as agreement.
                        receipt.status = MemberStatus.EMPTY
                        receipt.error = "returned no contribution"
                        receipt.error_type = "empty"
                    else:
                        receipt.status = MemberStatus.CONTRIBUTED
                        receipt.output = text
            except TimeoutError:
                # A wedge INSIDE run_bounded is already converted to a
                # ("timeout", ...) tuple, so a TimeoutError reaching here can
                # only be the slot acquisition. The branch is kept explicit
                # rather than merged so the two cases cannot be confused.
                receipt.status = MemberStatus.TIMEOUT
                receipt.error = (
                    "no execution slot within the stage bound"
                    if slot_timed_out
                    else "timed out before the participant could be scheduled"
                )
                receipt.error_type = "slot_timeout" if slot_timed_out else "timeout"
                receipt.duration_ms = int((self.clock() - started) * 1000)
            except asyncio.CancelledError:
                receipt.status = MemberStatus.CANCELLED
                receipt.error = "room cancelled"
                receipt.error_type = "cancelled"
                receipt.duration_ms = int((self.clock() - started) * 1000)
            except BaseException as exc:  # noqa: BLE001 - isolation is the point
                receipt.status = MemberStatus.FAILED
                receipt.error = f"{type(exc).__name__}: {exc}"
                receipt.error_type = type(exc).__name__
                receipt.duration_ms = int((self.clock() - started) * 1000)
            return receipt

        # The isolation boundary. return_exceptions=True plus a _contribute that
        # never raises: a sibling cannot cancel, delay or truncate another.
        names = list(self.config.participants)
        gathered = await asyncio.gather(*(_contribute(n) for n in names), return_exceptions=True)
        receipts: list[MemberReceipt] = []
        for name, outcome in zip(names, gathered, strict=True):
            if isinstance(outcome, BaseException):
                # Unreachable via _contribute, but a BaseException that escapes a
                # participant must still land in that participant's own receipt
                # rather than taking the stage with it.
                receipts.append(
                    MemberReceipt(
                        stage=stage.name,
                        participant=name,
                        status=MemberStatus.FAILED,
                        error=f"{type(outcome).__name__}: {outcome}",
                        error_type=type(outcome).__name__,
                    )
                )
            else:
                receipts.append(outcome)

        result.receipts = receipts
        for receipt in receipts:
            self._record_receipt(receipt)
        result.quorum = self._tally(stage.name, receipts)
        self._ledger(
            channel_ledger.EV_STAGE,
            actor="war_room",
            reason=result.quorum.human_line(),
            stage=stage.name,
            status=str(result.status),
            quorum=result.quorum.to_dict(),
        )

        delivered = {r.participant: r.output for r in receipts if r.delivered}
        result.synthesis = "\n\n".join(
            f"--- @{r.participant} ---\n{r.output}" for r in receipts if r.delivered
        )
        if not result.quorum.passed:
            # A member that was itself reaped for exceeding ITS slice means the
            # stage ran out of clock, which is a TIMEOUT. A member that merely
            # failed or said nothing while others delivered is a policy failure
            # (FAILED), which is a different claim about the world and is
            # reported as such. Conflating the two would make a genuinely
            # disagreeing room indistinguishable from a wedged one.
            stage_expired = deadline.expired or any(
                r.status is MemberStatus.TIMEOUT for r in receipts
            )
            result.status = StageStatus.TIMEOUT if stage_expired else StageStatus.FAILED
            result.error = (
                f"quorum not met: {result.quorum.agree}/{result.quorum.required_votes} "
                f"delivered under policy {result.quorum.policy}"
                + (
                    "; at least one member exceeded its slice of the stage bound"
                    if stage_expired and not deadline.expired
                    else ""
                )
            )
        else:
            result.status = StageStatus.COMPLETED
        result.finished_at = utc_now_iso()
        self._transcript(
            "war_room",
            f"stage {stage.name} closed: {result.quorum.human_line()}",
            "stage_close",
            stage=stage.name,
            status=str(result.status),
        )
        return result, delivered

    # -- the run ----------------------------------------------------------
    async def execute(self) -> WarRoomRun:
        """Execute every stage on the clock and return a terminal run.

        Named ``execute`` rather than ``run`` because ``self.run`` is the run
        RECORD; a method and an attribute cannot share that name.

        Always returns a terminal run. There is no path that returns while
        ``status == running``.
        """
        engaged, reason = self._kill_switch_engaged()
        if engaged:
            self.run.status = RunStatus.CANCELLED
            self.run.failure_reason = f"kill switch engaged before start: {reason}"
            self.run.killed_by = reason
            self.run.error = self.run.failure_reason
            self.run.finished_at = utc_now_iso()
            self._persist()
            return self.run

        self.run.status = RunStatus.RUNNING
        self._persist()
        prior: dict[str, str] = {}
        try:
            self._transcript(
                "war_room",
                f"room opened on topic: {self.config.topic}",
                "room_open",
                policy=str(self.config.quorum_policy),
                required_votes=self.config.required_votes(),
            )
            # The OPENING transition is a precondition, not a progress report.
            # A ledger that cannot record it means the whole run would be
            # unrecorded, so the run is refused HERE, before any participant is
            # dispatched. The in-flight transitions below obey the same
            # fail-closed rule through the LedgerUnavailable handler.
            self._ledger(
                channel_ledger.EV_STAGE,
                actor="war_room",
                reason="war room opened",
                stage="__open__",
                topic=self.config.topic[:2000],
                policy=str(self.config.quorum_policy),
                required_votes=self.config.required_votes(),
            )
            self._kill_watcher = asyncio.ensure_future(self._watch_kill_switch())

            for stage in self.config.stages:
                if self._cancelled.is_set():
                    return self._finish_cancelled()
                if stage.collects:
                    result, delivered = await self._run_stage(stage, prior)
                    self.run.stages.append(result)
                    prior = delivered
                    if result.status is not StageStatus.COMPLETED and stage.requires_previous_quorum:
                        # A stage that could not reach quorum ends the run in a
                        # TYPED terminal state. It does not wait for a wedged
                        # participant and it does not report success.
                        #
                        # The status mirrors the CAUSE: a stage that ran out of
                        # clock is a TIMEOUT, a stage that simply did not get
                        # enough real contributions is a FAILED run. Bailing out
                        # to the synthesis stage after a quorum failure would be
                        # wrong precisely when the room has not agreed, because
                        # it would report a deliverable that no agreed-upon
                        # quorum stands behind.
                        if result.status is StageStatus.TIMEOUT:
                            self.run.status = RunStatus.TIMEOUT
                            detail = (
                                f"exceeded its {stage.budget.total_seconds:.3f}s bound "
                                f"without reaching quorum"
                            )
                        else:
                            self.run.status = RunStatus.FAILED
                            detail = "did not reach quorum"
                        self.run.failure_reason = f"stage {stage.name!r} {detail}: {result.error}"
                        self.run.error = self.run.failure_reason
                        self.run.final_quorum = result.quorum
                        return self._finish()
                else:
                    # The synthesis stage. Fails LOUDLY on empty output.
                    synth = await self._run_synthesis_stage(stage, prior)
                    self.run.stages.append(synth)
                    if synth.status is not StageStatus.COMPLETED:
                        if synth.status is StageStatus.TIMEOUT:
                            self.run.status = RunStatus.TIMEOUT
                        else:
                            self.run.status = RunStatus.FAILED
                        self.run.failure_reason = synth.error
                        self.run.error = synth.error
                        self.run.synthesis = None
                        return self._finish()
                    # INVARIANT 3, enforced here and nowhere else.
                    if not (synth.synthesis or "").strip():
                        synth.status = StageStatus.FAILED
                        synth.error = (
                            "synthesis produced no deliverable; refusing to publish an empty "
                            "synthesis as a successful run"
                        )
                        self.run.status = RunStatus.FAILED
                        self.run.failure_reason = synth.error
                        self.run.error = synth.error
                        self.run.synthesis = None
                        return self._finish()
                    self.run.synthesis = synth.synthesis
                    self._transcript("war_room", synth.synthesis, "synthesis", stage=synth.name)
                if self._cancelled.is_set():
                    return self._finish_cancelled()

            # The reported quorum is the last stage that actually VOTED. The
            # synthesis stage collects nothing, so it has no tally of its own;
            # reporting "no quorum policy used" on a run that clearly used one
            # would be a lie about the room's own evidence.
            for stage_result in reversed(self.run.stages):
                if stage_result.quorum is not None:
                    self.run.final_quorum = stage_result.quorum
                    break
            return self._finish()
        except QuorumError as exc:
            self.run.status = RunStatus.FAILED
            self.run.failure_reason = f"quorum evaluation failed: {exc}"
            self.run.error = self.run.failure_reason
            return self._finish()
        except channel_ledger.LedgerUnavailable as exc:
            # Fail closed: an unrecorded transition is not a transition.
            self.run.status = RunStatus.FAILED
            self.run.failure_reason = f"ledger refused the run: {exc}"
            self.run.error = self.run.failure_reason
            return self._finish()
        finally:
            if self._kill_watcher is not None:
                self._kill_watcher.cancel()

    async def _run_synthesis_stage(self, stage: StageSpec, prior: Mapping[str, str]) -> StageResult:
        """The synthesis stage: one bounded moderator pass over the contributions.

        With no moderator configured the room's own contributions are the
        deliverable. Either way the emptiness check happens in :meth:`run`, so
        there is exactly one place that decides "is this synthesis real".
        """
        result = StageResult(
            name=stage.name,
            status=StageStatus.RUNNING,
            budget=stage.budget.to_dict(),
            started_at=utc_now_iso(),
        )
        self._ledger(
            channel_ledger.EV_STAGE,
            actor="war_room",
            reason=f"stage {stage.name} started",
            stage=stage.name,
            timeout_seconds=stage.budget.timeout_seconds,
        )
        deadline = Deadline(stage.budget, clock=self.clock)

        async def _synth() -> str:
            if self.moderator is None:
                return "\n\n".join(f"--- @{n} ---\n{t}" for n, t in prior.items() if t.strip())
            ctx = ContributionContext(
                run_id=self.run_id,
                room=self.room,
                topic=self.config.topic,
                stage=stage,
                participant=self.config.moderator or "moderator",
                previous_outputs=prior,
                moderator=self.config.moderator,
            )
            return await self.moderator(ctx)

        status, value, error = await run_bounded(_synth, deadline)
        if status == "completed":
            result.status = StageStatus.COMPLETED
            result.synthesis = "" if value is None else str(value)
        elif status == "timeout":
            result.status = StageStatus.TIMEOUT
            result.error = f"synthesis exceeded its {stage.budget.total_seconds:.3f}s bound: {error}"
        elif status == "cancelled":
            result.status = StageStatus.FAILED
            result.error = f"synthesis cancelled: {error}"
        else:
            result.status = StageStatus.FAILED
            result.error = f"synthesis failed: {error}"
        result.finished_at = utc_now_iso()
        self._ledger(
            channel_ledger.EV_STAGE,
            actor="war_room",
            reason=f"synthesis stage closed: {result.status}",
            stage=stage.name,
            status=str(result.status),
        )
        return result

    def _finish_cancelled(self) -> WarRoomRun:
        self.run.status = RunStatus.CANCELLED
        self.run.failure_reason = f"run cancelled: {self.run.killed_by or 'operator'}"
        self.run.error = self.run.failure_reason
        return self._finish()

    def _finish(self) -> WarRoomRun:
        """Settle the run into a terminal, honest state and persist it."""
        if self.run.status is RunStatus.RUNNING:
            degraded = [
                r
                for r in self.run.all_receipts()
                if r.status in {MemberStatus.FAILED, MemberStatus.TIMEOUT, MemberStatus.EMPTY}
            ]
            if self.run.synthesis:
                self.run.status = RunStatus.PARTIAL if degraded else RunStatus.SUCCEEDED
                if degraded:
                    self.run.failure_reason = (
                        f"{len(degraded)} participant(s) did not deliver; synthesis completed "
                        f"on the remaining contributions"
                    )
            else:
                self.run.status = RunStatus.FAILED
                self.run.failure_reason = self.run.failure_reason or "run produced no synthesis"

        if self.run.status is not RunStatus.SUCCEEDED and self.run.synthesis is None and not self.run.error:
            self.run.error = self.run.failure_reason

        if self.run.receipt_loss:
            # A run whose record is incomplete cannot claim success.
            if self.run.status is RunStatus.SUCCEEDED:
                self.run.status = RunStatus.PARTIAL
            note = f"{len(self.run.receipt_loss)} receipt(s) could not be persisted"
            self.run.failure_reason = (
                f"{self.run.failure_reason}; {note}" if self.run.failure_reason else note
            )

        if not self.run.finished_at:
            self.run.finished_at = utc_now_iso()
        self._transcript(
            "war_room",
            f"run {self.run.run_id} ended: status={self.run.status} reason={self.run.failure_reason or 'n/a'}",
            "room_close",
            status=str(self.run.status),
        )
        self._persist()
        return self.run

    # -- replay -----------------------------------------------------------
    def replay(self) -> str:
        """Human-readable replay of the durable transcript."""
        return self.transcript.render()

    def verify_transcript(self) -> None:
        """Raise if the durable transcript is not gap-free."""
        self.transcript.verify_ordered_and_gap_free()

    def load_persisted(self) -> WarRoomRun:
        """Read the run back from disk, for a restart or an audit."""
        raw = json.loads((self.dir / "run.json").read_text(encoding="utf-8"))
        self.run = WarRoomRun(
            run_id=raw["run_id"],
            room=raw["room"],
            config=self.config,
            status=raw["status"],
            synthesis=raw.get("synthesis"),
            error=raw.get("error", ""),
            failure_reason=raw.get("failure_reason", ""),
            started_at=raw.get("started_at", ""),
            finished_at=raw.get("finished_at", ""),
            transcript_errors=list(raw.get("transcript_errors") or []),
            receipt_loss=list(raw.get("receipt_loss") or []),
        )
        return self.run


def build_default_config(
    topic: str,
    participants: Sequence[str],
    *,
    stage_timeout_seconds: float = 20.0,
    stage_grace_seconds: float = 5.0,
    quorum_policy: str = "majority",
    moderator: str | None = None,
    stage_names: Sequence[str] = DEFAULT_STAGE_NAMES,
) -> WarRoomConfig:
    """Build a config with the default three-stage shape.

    The final stage does not collect: it is the synthesis pass. Naming the
    synthesis stage explicitly (rather than inferring it) is what lets a caller
    say "no synthesis" by asking for the first two stages only.
    """
    stages: list[StageSpec] = []
    for index, name in enumerate(stage_names):
        is_last = index == len(stage_names) - 1
        stages.append(
            StageSpec(
                name=name,
                prompt=(
                    "Synthesise the room's positions on the topic into one decision."
                    if is_last
                    else f"Give your position on the topic for the {name.replace('_', ' ')} stage."
                ),
                budget=StageBudget(
                    name=name,
                    timeout_seconds=stage_timeout_seconds,
                    grace_seconds=stage_grace_seconds,
                ),
                collects=not is_last,
            )
        )
    return WarRoomConfig(
        topic=topic,
        participants=tuple(participants),
        stages=tuple(stages),
        quorum_policy=QuorumPolicy(quorum_policy),
        moderator=moderator,
    )
