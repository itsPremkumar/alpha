"""Advisory work claims: "I intend to touch X", visible to the rest of the room.

`projects/locks.py` already refuses a second owner of a path, and that refusal
is real. What it cannot do is *warn*. An agent that is about to edit
`src/api/routes.py` gets a 423 after the model has already decided to edit it,
naming a holder but not a reason, and nobody else in the room ever finds out
that two agents had designs for the same file.

So a claim is a different guarantee from a lock, and it lives in a different
store on purpose:

===============  ==========================  ==========================
                 Work claim                  Resource lock
===============  ==========================  ==========================
guarantee        intent, visible to peers    exclusive ownership
on overlap       a soft conflict, nothing    `LockConflictError`, HTTP 423
                 is refused
lifetime         short, renewed by pulse     fixed TTL (1800s default)
holder crashes   ``orphaned`` — available    blocks until the TTL lapses
written by       "I am on it"                "I am about to mutate"
===============  ==========================  ==========================

Collapsing the two into one store with a boolean would force one of those
columns to be a lie.

**The short lifetime is the point.** A claim an agent made and then never
renewed has to evaporate quickly, because a stale claim that sits there for
thirty minutes is worse than no claim at all: peers will route around work
whose owner vanished hours ago. 120 seconds, renewed by the same pulse that
renews the agent's activity record.

## Soft conflicts are computed here, not detected later

`projects/conflicts.py::detect_lock_conflicts()` hunts for two live locks,
same project, same scope, same path, different owners. `LockManager.acquire()`
*raises* on exactly that condition, so by construction the state it looks for
cannot exist through the public API — `backend/tests/test_projects_coordination.py`
says so in a comment and inserts the second lock straight into a private dict.
Only the refusal is live. `detect_soft_conflicts()` below has no such problem:
claims are advisory and non-refusing, so two agents claiming the same path both
succeed, and the overlap is a real, reachable state worth reporting.

## Directory coverage matches the lock layer on purpose

A `dir` claim covers everything beneath it, using the same rule as
`locks.py::_live_holder`, so the two layers cannot disagree about what a
directory covers. Path subjects are also normalised here: neither store
normalises today, which means `"./a.py"` and `"a.py"` are two different files
as far as a lock is concerned. That is a latent bug, and fixing it in the
*claim* layer without touching the lock layer would create a second, different
answer — so `normalise_subject()` is exported for the lock layer to adopt.

## Invariants

- Claims live beside rooms, never in `GroupRoom.members`, which
  `crew._sync_room()` owns and deletes unclaimed entries from.
- A claim held by a **crashed** agent is `orphaned`, not live. It is available
  to take over, and it is never auto-transferred: the crash is evidence, the
  transfer is a decision, and collapsing them would hand a half-finished edit
  to a second agent because of a network blip.
"""

from __future__ import annotations

import json
import logging
import posixpath
import threading
import time
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

ClaimKind = Literal["file", "dir", "symbol", "task", "artifact", "requirement"]
ClaimIntent = Literal["reading", "editing", "reviewing"]
ClaimState = Literal["active", "released", "expired", "orphaned"]

CLAIM_KINDS: tuple[str, ...] = ("file", "dir", "symbol", "task", "artifact", "requirement")
CLAIM_INTENTS: tuple[str, ...] = ("reading", "editing", "reviewing")
CLAIM_STATES: tuple[str, ...] = ("active", "released", "expired", "orphaned")

#: Short on purpose. See the module docstring.
DEFAULT_CLAIM_TTL_SECONDS = 120.0

#: Ceilings so one agent cannot flood the room's claim list.
MAX_CLAIMS_PER_BOT = 25
MAX_CLAIMS_PER_ROOM = 400
#: A write set is one declared intent, so it is bounded well below the per-bot
#: ceiling — otherwise a single call could take every slot and lock the agent
#: out of claiming anything afterwards.
MAX_WRITE_SET_SUBJECTS = 20


def _announce_write_set(room_name: str, holder: str, claims: list[WorkClaim]) -> None:
    """Announce a declared write scope as one message, not N.

    Bounded on the rendered subject list rather than truncated mid-path: a peer
    reading half a path list would draw the wrong conclusion, which is worse than
    reading that the set is larger than the message shows.
    """
    try:
        from alpha.groups.coordination import announce_conflict

        subjects = sorted({c.subject for c in claims})
        shown = subjects[:8]
        more = len(subjects) - len(shown)
        announce_conflict(
            room_name,
            {
                "subject": ", ".join(shown) + (f" (+{more} more)" if more > 0 else ""),
                "kind": "write_set",
                "holders": [holder],
                "claim_ids": [c.claim_id for c in claims],
                "reasons": [c.intent for c in claims],
                "states": {holder: "working"},
                "reclaimable": False,
                "dead_holder": None,
                "detail": f"{holder} declared a write set of {len(subjects)} subject(s): {', '.join(shown)}" + (f" (+{more} more)" if more > 0 else "") + ". Route around this area until it is released.",
            },
        )
    except Exception:
        # A failed announcement must never be the thing that breaks the agent
        # that declared the set; the claims are already recorded.
        logger.warning("Write-set announcement failed for room %s", room_name, exc_info=True)


def _now() -> float:
    return time.time()


def _default_storage_path() -> Path:
    try:
        from alpha.config.runtime_paths import runtime_home

        return runtime_home() / "groups" / "claims.json"
    except Exception:
        return Path.cwd() / ".alpha" / "groups" / "claims.json"


def normalise_subject(subject: str, kind: str = "file") -> str:
    """Collapse a path claim to one canonical spelling.

    `"./a.py"`, `"a.py"` and ``"a.py"`` are the same file, and a store that
    treats them as three is a store that silently lets two agents claim one
    file. Non-path kinds (`symbol`, `task`, `requirement`) are identifiers
    rather than paths and are only stripped of surrounding whitespace, since
    `posixpath.normpath` would mangle a symbol containing a dot.

    Exported so `projects/locks.py` can adopt the same rule; until it does, a
    claim and a lock can still disagree about `"./a.py"`, which is why
    `detect_soft_conflicts` normalises before it compares.
    """
    raw = (subject or "").strip()
    if not raw:
        return ""
    if kind not in ("file", "dir", "artifact"):
        return raw
    # Windows separators collapse too: a peer on another OS naming the same
    # file must not produce a different claim.
    unified = raw.replace("\\", "/")
    collapsed = posixpath.normpath(unified)
    if collapsed == ".":
        return ""
    return collapsed


@dataclass
class WorkClaim:
    claim_id: str
    room_name: str
    holder: str
    kind: ClaimKind
    subject: str
    intent: ClaimIntent = "editing"
    project_id: str | None = None
    run_id: str | None = None
    detail: str = ""
    created_at: float = field(default_factory=_now)
    renewed_at: float = field(default_factory=_now)
    expires_at: float = field(default_factory=lambda: _now() + DEFAULT_CLAIM_TTL_SECONDS)
    state: ClaimState = "active"
    orphaned_at: float | None = None
    #: Why we believe the holder died. Only ever set from a hard crash
    #: verdict — see `groups/activity.py::derive_activity`.
    orphan_evidence: dict[str, Any] | None = None

    @property
    def expired(self) -> bool:
        return _now() > self.expires_at

    @property
    def live(self) -> bool:
        return self.state == "active" and not self.expired

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "live": self.live}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkClaim:
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered)


@dataclass
class SoftConflict:
    """Two live claims from different agents that overlap.

    `reclaimable` is the actionable field: it means one of the two holders is
    confirmed dead, so the subject is actually free and a peer can say so
    rather than routing around work nobody will ever finish.
    """

    subject: str
    kind: str
    holders: list[str]
    claim_ids: list[str]
    reasons: list[str]
    states: dict[str, str]
    reclaimable: bool = False
    dead_holder: str | None = None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Overlap rules ───────────────────────────────────────────────────────────


def _covers(container: str, child: str) -> bool:
    """Whether a directory claim covers a nested subject.

    Mirrors `locks.py::_live_holder`'s dir-covers-children test, so a claim and
    a lock never disagree about whether `src/api/` covers
    `src/api/routes.py`. A path that equals the container is not "covered" in
    the strict sense — it *is* the container — so callers compare exact and
    covering separately.
    """
    if not container or not child:
        return False
    return child.startswith(container.rstrip("/") + "/")


def subjects_overlap(
    a_kind: str,
    a_subject: str,
    b_kind: str,
    b_subject: str,
) -> bool:
    """Whether two claim subjects overlap.

    Exact for every kind. A `dir` claim additionally covers nested subjects,
    in both directions, so a file claim on `src/api/routes.py` overlaps a
    directory claim on `src/api/`.
    """
    if not a_subject or not b_subject:
        return False
    if a_subject == b_subject:
        return True
    if a_kind == "dir" and _covers(a_subject, b_subject):
        return True
    if b_kind == "dir" and _covers(b_subject, a_subject):
        return True
    return False


def detect_soft_conflicts(
    claims: list[WorkClaim],
    *,
    holder_states: dict[str, str] | None = None,
) -> list[SoftConflict]:
    """Overlapping live claims held by different agents.

    `holder_states` maps a bot name to its resolved activity state. When it is
    supplied, a claim whose holder is `crashed` is reported as **reclaimable**
    rather than as a live conflict — which is the whole reason this layer sits
    downstream of the activity ledger.

    Three exclusions, each of which would otherwise train agents to ignore the
    signal:

    - One agent holding two overlapping claims is reasoning about a file it
      already owns. Flagging that is noise.
    - A non-live claim (released, expired, orphaned) is not held by anybody.
    - Two claims of the same subject by the same holder are that agent's own
      bookkeeping, not a collision.
    """
    states = holder_states or {}
    live = [c for c in claims if c.live]
    out: list[SoftConflict] = []
    seen: set[tuple[str, str]] = set()
    for i, first in enumerate(live):
        for second in live[i + 1 :]:
            if first.holder == second.holder:
                continue
            if first.room_name != second.room_name:
                continue
            if not subjects_overlap(first.kind, first.subject, second.kind, second.subject):
                continue
            key = tuple(sorted((first.subject, second.subject)))
            if key in seen:
                continue
            seen.add(key)
            holders = sorted({first.holder, second.holder})
            states_for = {h: states.get(h, "unknown") for h in holders}
            dead = [h for h in holders if states_for[h] == "crashed"]
            reclaimable = bool(dead)
            subject = first.subject if first.subject == second.subject else f"{first.subject} + {second.subject}"
            out.append(
                SoftConflict(
                    subject=subject,
                    kind=first.kind if first.kind == second.kind else "mixed",
                    holders=holders,
                    claim_ids=[first.claim_id, second.claim_id],
                    reasons=[first.detail or first.intent, second.detail or second.intent],
                    states=states_for,
                    reclaimable=reclaimable,
                    dead_holder=dead[0] if reclaimable else None,
                    detail=(f"{first.subject} and {second.subject} overlap; {dead[0]} is crashed so it is available to take" if reclaimable else f"{holders[0]} and {holders[1]} both claim {subject}"),
                )
            )
    return out


# ── Store ───────────────────────────────────────────────────────────────────


class ClaimStore:
    """Thread-safe, file-backed advisory claims.

    Same discipline as `locks.py` and `membership.py`: a `threading.Lock`, an
    atomic `tmp` + `replace()`, and a load failure that logs and starts empty
    rather than raising. A corrupt claim file must not take down the room.
    """

    def __init__(self, storage_path: str | Path | None = None):
        self.storage_path = Path(storage_path).resolve() if storage_path else _default_storage_path()
        self._claims: dict[str, WorkClaim] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            with open(self.storage_path, encoding="utf-8") as f:
                data = json.load(f)
            for item in data.get("claims", []):
                claim = WorkClaim.from_dict(item)
                self._claims[claim.claim_id] = claim
        except Exception:
            logger.warning("Work claim store load failed; starting empty", exc_info=True)
            self._claims = {}

    def _save(self) -> None:
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.storage_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "claims": [c.to_dict() for c in self._claims.values()]}, f, indent=2)
            tmp.replace(self.storage_path)
        except Exception:
            logger.warning("Work claim store save failed", exc_info=True)

    # -- writes ---------------------------------------------------------------

    def claim(
        self,
        room_name: str,
        holder: str,
        kind: str,
        subject: str,
        *,
        intent: str = "editing",
        detail: str = "",
        project_id: str | None = None,
        run_id: str | None = None,
        ttl_seconds: float = DEFAULT_CLAIM_TTL_SECONDS,
    ) -> WorkClaim:
        """Record intent. Never refuses, by design.

        Re-claiming the same subject by the same holder renews the existing
        claim rather than creating a second one, so a long task extends its own
        claim instead of accumulating near-duplicates. A *different* holder
        claiming the same subject also succeeds — that overlap is what
        `detect_soft_conflicts` reports.
        """
        if kind not in CLAIM_KINDS:
            raise ValueError(f"Unknown claim kind '{kind}'")
        if intent not in CLAIM_INTENTS:
            raise ValueError(f"Unknown claim intent '{intent}'")
        clean = normalise_subject(subject, kind)
        if not clean:
            raise ValueError("A claim needs a non-empty subject.")
        owner = holder.lower().strip()
        with self._lock:
            for existing in self._claims.values():
                if existing.live and existing.room_name == room_name and existing.holder == owner and existing.kind == kind and existing.subject == clean:
                    existing.renewed_at = _now()
                    existing.expires_at = _now() + float(ttl_seconds)
                    if detail:
                        existing.detail = detail[:500]
                    if run_id:
                        existing.run_id = run_id
                    self._save()
                    return existing
            held = sum(1 for c in self._claims.values() if c.live and c.room_name == room_name)
            if held >= MAX_CLAIMS_PER_ROOM:
                raise ValueError(f"A room holds at most {MAX_CLAIMS_PER_ROOM} live claims.")
            mine = [c for c in self._claims.values() if c.live and c.room_name == room_name and c.holder == owner]
            if len(mine) >= MAX_CLAIMS_PER_BOT:
                raise ValueError(f"An agent holds at most {MAX_CLAIMS_PER_BOT} live claims; release one first.")
            claim = WorkClaim(
                claim_id=f"cl-{uuid.uuid4().hex[:10]}",
                room_name=room_name,
                holder=owner,
                kind=kind,  # type: ignore[arg-type]
                subject=clean,
                intent=intent,  # type: ignore[arg-type]
                detail=detail[:500],
                project_id=project_id,
                run_id=run_id,
                expires_at=_now() + float(ttl_seconds),
            )
            self._claims[claim.claim_id] = claim
            self._save()
            return claim

    def renew(self, claim_id: str, *, ttl_seconds: float = DEFAULT_CLAIM_TTL_SECONDS) -> WorkClaim | None:
        with self._lock:
            claim = self._claims.get(claim_id)
            if claim is None or not claim.live:
                return None
            claim.renewed_at = _now()
            claim.expires_at = _now() + float(ttl_seconds)
            self._save()
            return claim

    def release(self, claim_id: str, holder: str) -> bool:
        """Release a claim. The holder, `supervisor`, or the operator may."""
        owner = holder.lower().strip()
        with self._lock:
            claim = self._claims.get(claim_id)
            if claim is None:
                return False
            if claim.holder != owner and owner not in ("supervisor", "operator"):
                return False
            claim.state = "released"
            self._save()
            return True

    def release_for_bot(self, room_name: str, holder: str) -> int:
        """Drop every live claim one agent holds in one room.

        Called when a bot leaves the room and when the agent is confirmed
        crashed — except that a crash *orphans* the claim rather than releasing
        it, so a peer can see that the work was started and abandoned.
        """
        owner = holder.lower().strip()
        with self._lock:
            count = 0
            for claim in self._claims.values():
                if claim.live and claim.room_name == room_name and claim.holder == owner:
                    claim.state = "released"
                    count += 1
            if count:
                self._save()
            return count

    def orphan_for_bot(
        self,
        room_name: str,
        holder: str,
        *,
        evidence: dict[str, Any] | None = None,
    ) -> list[WorkClaim]:
        """Mark a confirmed-dead agent's claims available to take over.

        The transfer is deliberately **not** automatic. A crash is evidence; a
        hand-off is a decision. This records what the crash proved and leaves
        the decision to a peer, the moderator, or the operator.
        """
        owner = holder.lower().strip()
        with self._lock:
            moved: list[WorkClaim] = []
            for claim in self._claims.values():
                if claim.live and claim.room_name == room_name and claim.holder == owner:
                    claim.state = "orphaned"
                    claim.orphaned_at = _now()
                    claim.orphan_evidence = evidence or {}
                    moved.append(claim)
            if moved:
                self._save()
            return moved

    def declare_write_set(
        self,
        room_name: str,
        holder: str,
        subjects: Sequence[str],
        *,
        kind: str = "file",
        intent: str = "editing",
        detail: str = "",
        project_id: str | None = None,
        run_id: str | None = None,
        ttl_seconds: float = DEFAULT_CLAIM_TTL_SECONDS,
    ) -> list[WorkClaim]:
        """Claim a *set* of subjects in one call, as a single announced intent.

        A refactor across twelve files would otherwise be twelve claims and up
        to twelve room messages, and a peer polling between them sees a partial
        picture — which is the worst moment to make a coordination decision from.

        This declares the whole write scope up front, so the room learns the
        shape of the work once. That is what turns "is agent X editing file Y"
        into "is agent X working in this area", which is the question a peer
        actually has before deciding whether to start.

        Every subject is claimed on the same terms as `claim()` — advisory,
        non-refusing, individually expiring — so a set declaration is exactly N
        claims plus one announcement, not a weaker guarantee.

        Duplicate subjects collapse, so declaring `src/` and `src/api.py` does
        not produce two claims for one intent.
        """
        owner = holder.lower().strip()
        seen: set[str] = set()
        ordered: list[str] = []
        for raw in subjects:
            clean = normalise_subject(raw, kind)
            if not clean or clean in seen:
                continue
            seen.add(clean)
            ordered.append(clean)
        if not ordered:
            raise ValueError("A write set needs at least one non-empty subject.")
        if len(ordered) > MAX_WRITE_SET_SUBJECTS:
            raise ValueError(f"A write set holds at most {MAX_WRITE_SET_SUBJECTS} subjects; declare the work in stages.")

        claims = [
            self.claim(
                room_name,
                owner,
                kind,
                subject,
                intent=intent,
                detail=detail,
                project_id=project_id,
                run_id=run_id,
                ttl_seconds=ttl_seconds,
            )
            for subject in ordered
        ]
        _announce_write_set(room_name, owner, claims)
        return claims

    def reclaim(self, claim_id: str, new_holder: str) -> WorkClaim | None:
        """Take over an orphaned claim.

        Only an orphaned claim can be reclaimed. Reclaiming a *live* one would
        be a silent steal, which is the failure this layer exists to prevent —
        a live conflict is answered by `request_access` or by the moderator,
        not by whoever called this.
        """
        owner = new_holder.lower().strip()
        with self._lock:
            claim = self._claims.get(claim_id)
            if claim is None or claim.state != "orphaned":
                return None
            claim.holder = owner
            claim.state = "active"
            claim.orphaned_at = None
            claim.orphan_evidence = None
            claim.renewed_at = _now()
            claim.expires_at = _now() + DEFAULT_CLAIM_TTL_SECONDS
            self._save()
            return claim

    def sweep(self, *, now: float | None = None) -> int:
        """Expire lapsed claims. Returns how many were swept."""
        moment = _now() if now is None else now
        with self._lock:
            dead = [cid for cid, c in self._claims.items() if c.state == "active" and moment > c.expires_at]
            for cid in dead:
                self._claims[cid].state = "expired"
            if dead:
                self._save()
            return len(dead)

    # -- reads ----------------------------------------------------------------

    def get(self, claim_id: str) -> WorkClaim | None:
        with self._lock:
            return self._claims.get(claim_id)

    def room_claims(self, room_name: str, *, live_only: bool = False, now: float | None = None) -> list[WorkClaim]:
        """Claims for one room, oldest first.

        Order is creation order so a room reads the same way twice; sorting by
        expiry would make the list jump on every poll.
        """
        moment = _now() if now is None else now
        with self._lock:
            rows = [c for c in self._claims.values() if c.room_name == room_name]
        if live_only:
            rows = [c for c in rows if c.state == "active" and moment <= c.expires_at]
        return sorted(rows, key=lambda c: c.created_at)

    def claims_by_bot(self, room_name: str, *, now: float | None = None) -> dict[str, list[str]]:
        # No outer `with self._lock`: `room_claims` takes the lock itself, and
        # this used to nest a plain `Lock` around it — a self-deadlock that
        # hung every `room_snapshot`, because the activity projection calls this
        # on every read. `GroupChatService._lock` is an `RLock` for the same
        # "composed calls" reason; rather than depend on that, the inner lock is
        # simply the only one held.
        out: dict[str, list[str]] = {}
        for claim in self.room_claims(room_name, live_only=True, now=now):
            out.setdefault(claim.holder, []).append(claim.claim_id)
        return out

    def held_by_bot(self, room_name: str, *, now: float | None = None) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for claim in self.room_claims(room_name, live_only=True, now=now):
            out.setdefault(claim.holder, []).append(claim.subject)
        return out


_claims: ClaimStore | None = None
_claims_path: str | None = None
_claims_lock = threading.Lock()


def get_claim_store(storage_path: str | Path | None = None) -> ClaimStore:
    """Process-wide claim store singleton; rebuilds when `runtime_home()` moves."""
    global _claims, _claims_path
    with _claims_lock:
        if storage_path is not None:
            _claims = ClaimStore(storage_path)
            try:
                _claims_path = str(_claims.storage_path)
            except Exception:
                _claims_path = None
            return _claims
        try:
            live = str(_default_storage_path().resolve())
        except Exception:
            live = None
        if _claims is None or _claims_path != live:
            _claims = ClaimStore()
            try:
                _claims_path = str(_claims.storage_path.resolve())
            except Exception:
                _claims_path = live
        return _claims
