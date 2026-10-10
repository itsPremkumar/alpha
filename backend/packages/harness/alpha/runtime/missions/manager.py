"""Mission stack + durable per-scope manager.

Why this exists
--------------
This is the durable *file* OpenAI's long-horizon write-ups keep pointing back to:
a spec that freezes the target, a plan of milestones, a runbook, a **live status
log**, and a scratchpad -- all re-readable by the agent at any point. Keeping it
as durable, owner+thread-scoped state is what lets a run survive a context
compaction at hour 6 or a process restart at hour 20 without losing the plot. It
is the antidote to the one failure a compaction cannot paper over: *drift* -- an
agent that is still producing work but is no longer producing the work the
objective asked for.

The manager owns storage and nothing else. It is not a lifecycle owner, it
dispatches nothing, and it never runs a command; :mod:`alpha.runtime.missions.anchor`
projects it into the model's context and the mission tool records into it.

Honesty rules encoded here
--------------------------
* **Server-resolved owner + thread only.** The scope is
  ``(owner, thread_id)`` resolved from the trusted runtime, never from an HTTP
  body or a model-supplied string. A path that escapes the owner's mission
  directory is refused, not sanitized.
* **Fail-closed on a mis-shaped scope; fail-open on a missing file.** An
  absent mission is an empty stack (nothing to anchor). A corrupt mission file
  is loaded as empty with a WARN and a recorded ``load_error`` rather than being
  silently treated as if it held content -- losing a note is survivable,
  inventing one is not.
* **Atomic, fsync-backed writes.** ``save`` writes a temp file, fsyncs, and
  ``os.replace``s it, so a crash mid-write cannot truncate the durable memory
  into a half-object. This is the same discipline as cognitive memory.
* Single-process JSON, restart-recoverable. The locks give no multi-process
  coherence and this never claims cross-process exactly-once.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Final

from alpha.runtime.missions.checkpoint import ResumeCheckpoint
from alpha.runtime.missions.milestones import MilestonePlan
from alpha.runtime.missions.scratchpad import Scratchpad

if TYPE_CHECKING:
    from alpha.config.paths import Paths

logger = logging.getLogger(__name__)

__all__ = ["MissionManager", "MissionStack", "MAX_MISSION_FILE_BYTES"]


#: Absolute ceiling on a single serialized mission file. Structurally the stack
#: is already bounded (plan caps milestones, status and scratchpad are rings),
#: so this only ever trips on a pathological write and refuses rather than
#: filling the disk.
MAX_MISSION_FILE_BYTES: Final[int] = 512 * 1024

_DEFAULT_MAX_STATUS_LINES: Final[int] = 40
_MAX_LINE_CHARS: Final[int] = 2000
_DEFAULT_RING: Final[int] = 12

#: A scope component must be a single safe path segment: no separators, no
#: traversal, no leading dot. Anything else is a mis-shaped scope and is refused.
_SAFE_TOKEN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _clean_line(text: str, *, limit: int = _MAX_LINE_CHARS) -> str:
    return (text or "").strip()[:limit]


@dataclass(frozen=True, slots=True)
class MissionStack:
    """The durable memory of one long-horizon objective for one scope.

    It holds a frozen target (``spec_*`` / ``implement_runbook``), the live
    :class:`MilestonePlan`, an append-only ``status_lines`` log, and a
    :class:`Scratchpad`. Every mutator returns a new stack, so it can be held by
    a checkpoint, an approval, and a recovery scan without racing.
    """

    scope_key: str = ""
    spec_objective: str = ""
    spec_constraints: tuple[str, ...] = ()
    spec_done_when: tuple[str, ...] = ()
    implement_runbook: str = ""
    plan: MilestonePlan | None = None
    status_lines: tuple[str, ...] = ()
    scratchpad: Scratchpad = field(default_factory=Scratchpad)
    # Live operator steer constraints recorded mid-run; surfaced every turn so a
    # course correction survives the next compaction instead of being a one-off.
    steers: tuple[str, ...] = ()
    # Paths already tried and rejected — rendered as an explicit "do not repeat"
    # block so a post-compaction agent does not re-attempt a dead end.
    rejected: tuple[str, ...] = ()
    # Useful ideas parked for later, never worked on now; kept out of the way but
    # not lost, so the mission can pick them up after the current objective.
    deferred: tuple[str, ...] = ()
    # The structured resume checkpoint read after a compaction to continue from
    # the current point rather than restarting earlier work.
    checkpoint: ResumeCheckpoint | None = None
    # Loop-brake state (the fail-closed lesson from the Codex loop ecosystem: a
    # mission must never loop forever on a blocked/no-progress/repeated-failure
    # path; it parks once and returns control to a human).
    blocked: str = ""
    attempts: tuple[str, ...] = ()
    cycle_count: int = 0
    no_progress_cycles: int = 0
    max_status_lines: int = _DEFAULT_MAX_STATUS_LINES
    max_ring: int = _DEFAULT_RING
    load_error: str | None = None
    updated_at: str = ""

    # ---- predicates --------------------------------------------------------------

    def is_active(self) -> bool:
        """Whether there is any durable mission content to anchor.

        An empty stack is not a mission; the middleware injects nothing for it.
        """
        return (
            bool(self.spec_objective.strip())
            or self.plan is not None
            or bool(self.status_lines)
            or self.scratchpad.count > 0
            or bool(self.steers)
            or bool(self.rejected)
            or bool(self.deferred)
            or (self.checkpoint is not None and not self.checkpoint.is_empty())
        )

    # ---- mutators (each returns a new stack) -------------------------------------

    def with_spec(self, *, objective: str, constraints: tuple[str, ...] | list[str] = (), done_when: tuple[str, ...] | list[str] = ()) -> MissionStack:
        return replace(
            self,
            spec_objective=_clean_line(objective),
            spec_constraints=tuple(_clean_line(c) for c in constraints if _clean_line(c)),
            spec_done_when=tuple(_clean_line(c) for c in done_when if _clean_line(c)),
        )

    def with_runbook(self, runbook: str) -> MissionStack:
        return replace(self, implement_runbook=_clean_line(runbook, limit=8000))

    def with_plan(self, plan: MilestonePlan) -> MissionStack:
        return replace(self, plan=plan)

    def note(self, scratch: str) -> MissionStack:
        return replace(self, scratchpad=self.scratchpad.append(scratch))

    def log_status(self, line: str) -> MissionStack:
        cleaned = _clean_line(line)
        if not cleaned:
            return self
        lines = (*self.status_lines, cleaned)
        if len(lines) > self.max_status_lines:
            lines = lines[-self.max_status_lines :]
        return replace(self, status_lines=lines)

    def touch(self, updated_at: str) -> MissionStack:
        return replace(self, updated_at=updated_at)

    def _ring(self, name: str, line: str) -> MissionStack:
        cleaned = _clean_line(line)
        if not cleaned:
            return self
        current = getattr(self, name)
        items = (*current, cleaned)
        if len(items) > self.max_ring:
            items = items[-self.max_ring :]
        return replace(self, **{name: items})

    def steer(self, line: str) -> MissionStack:
        """Record a live operator steer constraint (a record, never a control)."""
        return self._ring("steers", line)

    def reject(self, line: str) -> MissionStack:
        """Record a rejected path so it is never silently re-attempted."""
        return self._ring("rejected", line)

    def defer(self, line: str) -> MissionStack:
        """Park a useful idea for later without it leaking into the current work."""
        return self._ring("deferred", line)

    def with_checkpoint(self, checkpoint: ResumeCheckpoint) -> MissionStack:
        return replace(self, checkpoint=checkpoint)

    def block(self, reason: str) -> MissionStack:
        """Park the mission behind an operator; the loop decides nothing more."""
        return replace(self, blocked=_clean_line(reason, limit=500))

    def unblock(self) -> MissionStack:
        return replace(self, blocked="")

    def tick(self, *, progressed: bool, signature: str = "") -> MissionStack:
        """Record one continuation cycle for the loop brake.

        ``signature`` is a short stable token for the attempt (e.g. the failing
        milestone + a digest of its evidence); repeated identical signatures are
        what the brake reads as "no new information". ``progressed`` resets the
        no-progress counter on any real step forward (a milestone verified, a new
        status line, a new checkpoint), which is the property a mere activity
        pulse does not have.
        """
        attempts = self.attempts
        if signature:
            attempts = (*attempts, _clean_line(signature, limit=200))[-self.max_ring :]
        if progressed:
            return replace(self, attempts=attempts, cycle_count=self.cycle_count + 1, no_progress_cycles=0)
        return replace(self, attempts=attempts, cycle_count=self.cycle_count + 1, no_progress_cycles=self.no_progress_cycles + 1)

    # ---- persistence -------------------------------------------------------------

    def to_dict(self) -> dict[str, object]:
        return {
            "scope_key": self.scope_key,
            "spec_objective": self.spec_objective,
            "spec_constraints": list(self.spec_constraints),
            "spec_done_when": list(self.spec_done_when),
            "implement_runbook": self.implement_runbook,
            "plan": self.plan.to_dict() if self.plan is not None else None,
            "status_lines": list(self.status_lines),
            "scratchpad": self.scratchpad.to_dict(),
            "steers": list(self.steers),
            "rejected": list(self.rejected),
            "deferred": list(self.deferred),
            "checkpoint": self.checkpoint.to_dict() if self.checkpoint is not None else None,
            "blocked": self.blocked,
            "attempts": list(self.attempts),
            "cycle_count": self.cycle_count,
            "no_progress_cycles": self.no_progress_cycles,
            "max_status_lines": self.max_status_lines,
            "max_ring": self.max_ring,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: object) -> MissionStack:
        if not isinstance(data, dict):
            return cls()
        plan_raw = data.get("plan")
        plan = MilestonePlan.from_dict(plan_raw) if isinstance(plan_raw, dict) else None
        constraints = data.get("spec_constraints", [])
        done = data.get("spec_done_when", [])
        status = data.get("status_lines", [])
        steers = data.get("steers", [])
        rejected = data.get("rejected", [])
        deferred = data.get("deferred", [])
        checkpoint_raw = data.get("checkpoint")
        checkpoint = ResumeCheckpoint.from_dict(checkpoint_raw) if isinstance(checkpoint_raw, dict) else None
        return cls(
            scope_key=str(data.get("scope_key", "")),
            spec_objective=str(data.get("spec_objective", "")),
            spec_constraints=tuple(str(c) for c in constraints) if isinstance(constraints, list) else (),
            spec_done_when=tuple(str(c) for c in done) if isinstance(done, list) else (),
            implement_runbook=str(data.get("implement_runbook", "")),
            plan=plan,
            status_lines=tuple(str(c) for c in status) if isinstance(status, list) else (),
            scratchpad=Scratchpad.from_dict(data.get("scratchpad")),
            steers=tuple(str(c) for c in steers) if isinstance(steers, list) else (),
            rejected=tuple(str(c) for c in rejected) if isinstance(rejected, list) else (),
            deferred=tuple(str(c) for c in deferred) if isinstance(deferred, list) else (),
            checkpoint=checkpoint,
            blocked=str(data.get("blocked", "")),
            attempts=tuple(str(c) for c in data.get("attempts", [])) if isinstance(data.get("attempts", []), list) else (),
            cycle_count=int(data.get("cycle_count", 0) or 0),
            no_progress_cycles=int(data.get("no_progress_cycles", 0) or 0),
            max_status_lines=int(data.get("max_status_lines", _DEFAULT_MAX_STATUS_LINES) or _DEFAULT_MAX_STATUS_LINES),
            max_ring=int(data.get("max_ring", _DEFAULT_RING) or _DEFAULT_RING),
            updated_at=str(data.get("updated_at", "")),
        )


class MissionManager:
    """Durable, owner+thread-scoped storage for :class:`MissionStack`.

    Constructed from the trusted :class:`~alpha.config.paths.Paths` (never from
    an operator/HTTP path). The instance is a thin, stateless accessor over the
    filesystem: reads hit disk every time and writes are atomic, so a tool
    writing in step *k* and the middleware reading in step *k+1* agree without a
    shared in-process cache to keep coherent.
    """

    def __init__(
        self,
        paths: Paths,
        *,
        max_status_lines: int = _DEFAULT_MAX_STATUS_LINES,
        max_scratch_entries: int = Scratchpad().max_entries,
    ) -> None:
        self._paths = paths
        self._max_status_lines = max(1, int(max_status_lines))
        self._max_scratch_entries = max(1, int(max_scratch_entries))

    # ---- scope -------------------------------------------------------------------

    @staticmethod
    def _safe_token(value: str) -> str:
        token = (value or "").strip()
        if not token or not _SAFE_TOKEN.match(token) or ".." in token:
            raise ValueError(f"unsafe scope component {value!r}")
        return token

    def _path(self, owner: str, thread_id: str) -> Path:
        owner_safe = self._safe_token(owner)
        thread_safe = self._safe_token(thread_id)
        base = self._paths.user_dir(owner_safe) / "missions"
        return base / f"{thread_safe}.json"

    # ---- reads -------------------------------------------------------------------

    def has_mission(self, owner: str, thread_id: str) -> bool:
        try:
            return self._path(owner, thread_id).exists()
        except ValueError:
            return False

    def load(self, owner: str, thread_id: str) -> MissionStack:
        """Return the stack for the scope, or an empty stack on absence.

        Corrupt JSON loads as an empty stack carrying ``load_error`` and a WARN,
        so the anchor refuses to invent content the file did not cleanly hold.
        """
        try:
            path = self._path(owner, thread_id)
        except ValueError as exc:
            return MissionStack(scope_key=thread_id, load_error=str(exc))
        if not path.exists():
            return MissionStack(scope_key=thread_id, max_status_lines=self._max_status_lines)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            stack = MissionStack.from_dict(data)
        except (OSError, ValueError) as exc:
            logger.warning("mission file corrupt for thread %s: %s", thread_id, exc)
            return MissionStack(scope_key=thread_id, max_status_lines=self._max_status_lines, load_error=str(exc))
        # A persisted mission adopts the *current* ring policy rather than the one
        # it was written with, so a config change re-bounds the pad without a
        # rewrite.
        stack = replace(stack, max_status_lines=self._max_status_lines)
        stack = replace(stack, scratchpad=replace(stack.scratchpad, max_entries=self._max_scratch_entries))
        return stack

    def active(self, owner: str, thread_id: str) -> bool:
        return self.load(owner, thread_id).is_active()

    # ---- writes ------------------------------------------------------------------

    def save(self, owner: str, thread_id: str, stack: MissionStack) -> None:
        """Atomically and durably write *stack* for the scope."""
        path = self._path(owner, thread_id)  # re-validate; raises on a bad scope
        payload = json.dumps(stack.to_dict(), ensure_ascii=False, separators=(",", ":"))
        blob = payload.encode("utf-8")
        if len(blob) > MAX_MISSION_FILE_BYTES:
            raise ValueError(f"mission file would exceed {MAX_MISSION_FILE_BYTES} bytes; record less")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        with open(tmp, "wb") as fh:
            fh.write(blob)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        # fsync the directory so the rename itself is durable, not just the bytes.
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            # A directory fsync is best-effort on filesystems that refuse it; the
            # file fsync above is the load-bearing durability.
            pass

    def scan_active(self, *, max_scopes: int = 200) -> list[tuple[str, str, MissionStack]]:
        """Return active missions across every owner, bounded by *max_scopes*.

        Enumerates ``{users}/*/missions/*.json`` — the same layout
        :meth:`_path` writes — and loads each stack (fail-open: a corrupt or
        unreadable file yields an inactive stack and is skipped). This is the
        bounded, read-only scan a *watchdog* loop uses; it reads durable state and
        never dispatches, so it does not become a second execution authority. The
        owner is the glob path component, so no caller can inject one.
        """
        users_root = self._paths.base_dir / "users"
        if not users_root.is_dir():
            return []
        results: list[tuple[str, str, MissionStack]] = []
        for path in sorted(users_root.glob("*/missions/*.json")):
            if len(results) >= max_scopes:
                break
            mission_dir = path.parent
            owner_dir = mission_dir.parent
            if owner_dir.parent != users_root:
                continue  # defensive: never trust a nested match outside users/*
            owner = owner_dir.name
            thread_id = path.stem
            try:
                self._safe_token(owner)
                self._safe_token(thread_id)
            except ValueError:
                continue
            stack = self.load(owner, thread_id)
            if stack.is_active():
                results.append((owner, thread_id, stack))
        return results
