"""Turn taint: untrusted content marks the TURN, and taint bounds authority.

The hole this closes
--------------------
OpenClaw states its own limitation plainly: "Turn taint covers network-sourced
tool output; text arriving through non-network tools does not taint the turn."
(https://docs.openclaw.ai/start/why-openclaw, "What we do not claim".)  That
is a real hole, and it is the shape of the three bugs already found and fixed
in this repository:

* a payload 28,400 characters into a fetched page body, past the injection
  screen's window, treated as screened content;
* a page quarantined by the injection screen whose replacement text was then
  stamped ``citation_status="verified"``;
* ``arxiv.org.evil.test`` accepted as ``arxiv.org`` by a suffix match.

Each is the same defect: content arrived untrusted, and the *derived* artefact
(summary, citation, verified finding) did not carry the untrust forward.

Design
------
* **Taint is on the TURN, not the tool call.**  One call site absorbing an
  untrusted source marks the turn; every later step of that turn is inside the
  taint, including steps that never saw the content.
* **Sources are ENUMERATED.**  :class:`UntrustedSource` is a closed set.  An
  unenumerated source is a hole, so a source that is not in the enum cannot be
  absorbed, and a new one has to be added to the enum deliberately.
* **Taint propagates through derived work.**  A summary of tainted content is
  tainted; a tool result computed from tainted input is tainted; a subagent
  spawned on a tainted turn inherits it.
* **Taint bounds authority.**  A tainted turn cannot satisfy an approval gate,
  cannot clear a policy gate without human review, and cannot be recorded as
  VERIFIED evidence.
* **Only a control clears taint, never the model.**  Clearing requires a
  control name from :data:`TRUSTED_CLEARING_CONTROLS`.  A model asking for its
  own untaintedness is refused.

What this is NOT
----------------
Taint is an in-process POLICY control.  It constrains cooperating code in this
process; it does not isolate anything, and code in the same trust envelope can
ignore it.  It is not a security boundary.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

# ---------------------------------------------------------------------------
# The enumerated sources
# ---------------------------------------------------------------------------


class UntrustedSource(StrEnum):
    """Every way untrusted content can enter a turn.

    This list is the module's security argument, so it is a closed enum rather
    than a free-form string.  Adding a source is a deliberate act with a
    reviewable diff; forgetting one is a silent hole.  Each member documents the
    concrete alpha path it covers.
    """

    #: ``alpha.community.ddg_search`` / ``read_url_content`` / httpx fetches.
    WEB_FETCH = "web_fetch"
    #: Browser/DOM content and CDP ``execute`` results.
    BROWSER_DOM = "browser_dom"
    #: ``read_file`` / ``view_file`` / ``list_dir`` -- content on disk is not
    #: content the operator chose to give this agent.
    FILE_READ = "file_read"
    #: Memory recall and every store read (l1/l2/narrative/prospective/...).
    MEMORY_RECALL = "memory_recall"
    #: Skill ``stdout``, skill scripts, skill-bundled assets.
    SKILL_OUTPUT = "skill_output"
    #: A message from another agent, including a delegated child's report.
    AGENT_MESSAGE = "agent_message"
    #: A database row read at runtime (SQL, sqlite, persistence backends).
    DATABASE_ROW = "database_row"
    #: An MCP tool response or an MCP server's tool listing.
    MCP_RESPONSE = "mcp_response"
    #: A subagent's returned result.
    SUBAGENT_RESULT = "subagent_result"
    #: Inbound channel traffic (Telegram/Slack/Discord/Feishu/...), and a
    #: channel's own rendering of a message.
    CHANNEL_INBOUND = "channel_inbound"
    #: A paired peer's envelope, over the peer plane.
    PEER_MESSAGE = "peer_message"
    #: A webhook body (GitHub and any other provider).
    WEBHOOK_BODY = "webhook_body"
    #: Generic non-network tool output not covered above.
    TOOL_OUTPUT = "tool_output"
    #: A process's stdout/stderr, or an environment variable value.
    PROCESS_OUTPUT = "process_output"
    #: A user-attached file, including one that arrived through a channel.
    ATTACHED_FILE = "attached_file"
    #: Content the MODEL itself asserts about its own provenance.  Self-assertion
    #: is not evidence; absorbing it as tainted is the safe default.
    MODEL_SELF_ASSERTION = "model_self_assertion"
    #: Third-party package metadata, lockfiles, and registry responses.
    PACKAGE_METADATA = "package_metadata"
    #: A mirror/rendezvous service response (GitHub rendezvous, A2A federation).
    FEDERATION_RESPONSE = "federation_response"


class TaintError(RuntimeError):
    """Base class for taint failures. Not a ``ValueError``."""


class TaintAuthorityError(TaintError):
    """A tainted turn tried to exercise an authority a clean turn is needed for."""


class TaintClearRefused(TaintError):
    """Something tried to clear taint that is not a trusted control."""


class UnknownUntrustedSource(TaintError):
    """A source outside the closed enumeration. This is a hole, not a warning."""

    def __init__(self, value: object) -> None:
        super().__init__(
            f"{value!r} is not an enumerated untrusted source; an unenumerated source cannot "
            "mark a turn tainted, so this is a gap in the taint model rather than a no-op"
        )
        self.value = value


def coerce_source(value: UntrustedSource | str) -> UntrustedSource:
    """Resolve to an enumerated source, or REFUSE.

    Accepting an arbitrary string here would defeat the entire module: a
    typo'd source name would silently fail to taint a turn.
    """

    if isinstance(value, UntrustedSource):
        return value
    try:
        return UntrustedSource(str(value).strip().lower())
    except ValueError as exc:
        raise UnknownUntrustedSource(value) from exc


#: Only these controls may clear taint.  Every one is a component that
#: adjudicates on evidence rather than on the model's word.  There is
#: deliberately no entry the model can name.
TRUSTED_CLEARING_CONTROLS: Final[frozenset[str]] = frozenset(
    {
        "human_operator",
        "injection_screen_passed",
        "trusted_local_signer",
        "operator_config_signed",
        "static_verifier",
    }
)

#: Sources that are untrusted by construction.  Listed for the report; the
#: enum itself is the enforcement.
UNTRUSTED_BY_DEFAULT: Final[frozenset[UntrustedSource]] = frozenset(UntrustedSource)


# ---------------------------------------------------------------------------
# The turn
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TaintMark:
    """Where untrusted content entered this turn."""

    source: UntrustedSource
    location: str

    def to_dict(self) -> dict[str, str]:
        return {"source": self.source.value, "location": self.location}


@dataclass(slots=True)
class TaintTurn:
    """One turn, and whether anything untrusted entered it.

    Mutable by design: a turn accumulates taint as it runs.  What it
    deliberately does NOT have is a setter for ``tainted`` or for ``marks``.
    Taint is computed from the marks, and the marks are exposed read-only
    through a property, so neither can be assigned off.
    """

    turn_id: str
    _marks: list[TaintMark] = field(default_factory=list, repr=False)
    derived_from: tuple[str, ...] = ()
    origin_turn_id: str = ""
    isolated: bool = False
    isolation_reason: str = ""
    cleared_by: str = ""
    cleared_at_turn: str = ""
    labels: tuple[str, ...] = ()

    @property
    def marks(self) -> tuple[TaintMark, ...]:
        """Read-only view of the marks. Assigning to it raises."""
        return tuple(self._marks)

    # -- absorbing ----------------------------------------------------------

    def absorb(self, source: UntrustedSource | str, *, location: str = "") -> TaintMark:
        """Mark untrusted content as having entered THIS turn.

        Returns the mark. Idempotent per (source, location) so a loop that
        fetches forty results from one domain records one mark, not forty.
        """

        resolved = coerce_source(source)
        mark = TaintMark(source=resolved, location=str(location or ""))
        for existing in self._marks:
            if existing.source is resolved and existing.location == mark.location:
                return existing
        self._marks.append(mark)
        return mark

    def absorb_many(self, sources: Iterable[UntrustedSource | str], *, location: str = "") -> tuple[TaintMark, ...]:
        return tuple(self.absorb(item, location=location) for item in sources)

    # -- derived work -------------------------------------------------------

    @property
    def tainted(self) -> bool:
        """Is this turn tainted? Computed, never assigned."""

        return bool(self._marks) and not self.cleared_by

    def sources(self) -> tuple[str, ...]:
        return tuple(sorted({mark.source.value for mark in self._marks}))

    def is_tainted_by(self, source: UntrustedSource | str) -> bool:
        return any(mark.source is coerce_source(source) for mark in self._marks)

    def derive(self, label: str, *, kind: str = "derived") -> TaintTurn:
        """Produce the turn for work computed FROM this turn.

        A summary, an extraction, a reformatted citation, a SQL query built out
        of recalled text: all of them are new turns that inherit every mark.
        This is the propagation rule that the three historical bugs violated.
        """

        child = TaintTurn(
            turn_id=f"{self.turn_id}.{kind}.{label}",
            _marks=list(self._marks),
            derived_from=(*self.derived_from, self.turn_id),
            origin_turn_id=self.origin_turn_id or self.turn_id,
            isolated=self.isolated,
            isolation_reason=self.isolation_reason,
            # A derived turn inherits the taint but NOT the clearance. A control
            # that cleared turn A did not vet turn B.
            cleared_by="",
            labels=(label,),
        )
        return child

    def summary(self, text: str, *, label: str = "summary") -> TaintTurn:
        """The turn for a summary of this turn's content.

        Taint propagates and nothing more: summarising tainted text yields a
        tainted summary, and summarising clean text yields a clean one.  The
        text is not inspected here -- deciding what counts as untrusted is
        :meth:`absorb`'s job, and a heuristic here would be a second, weaker
        answer to the same question.
        """

        return self.derive(label, kind="summary")

    def tool_result(self, text: str, *, label: str = "result", tool: str = "") -> TaintTurn:
        """The turn for a tool result computed from this turn.

        Whatever the tool did, the result is computed from input that may have
        been untrusted, so the result turn carries the marks.  A tool that
        returns genuinely fresh untrusted content should additionally
        :meth:`absorb` that content's source on the RESULT turn.
        """

        return self.derive(label, kind="tool_result")

    def spawn_child(
        self,
        label: str,
        *,
        isolated: bool = False,
        isolation_reason: str = "",
    ) -> TaintTurn:
        """The turn for a subagent spawned from this one.

        INHERITS taint unless ``isolated=True``.  Isolation is explicit,
        reasoned, and visible in :meth:`marks_payload` -- an unisolated child is
        the default precisely because an unexamined exception is a hole.  Callers
        that want isolation should go through :func:`isolate_child`, which also
        requires a DECLARED control rather than a free-text reason.
        """

        child = TaintTurn(
            turn_id=f"{self.turn_id}.child.{label}",
            _marks=[] if isolated else list(self._marks),
            derived_from=(*self.derived_from, self.turn_id),
            origin_turn_id=self.origin_turn_id or self.turn_id,
            isolated=bool(isolated),
            isolation_reason=str(isolation_reason) if isolated else "",
            labels=(label,),
        )
        return child

    # -- clearing -----------------------------------------------------------

    def clear(self, control: str, *, at_turn: str = "") -> None:
        """Clear taint, but only for a control that adjudicates on evidence.

        ``control`` must be in :data:`TRUSTED_CLEARING_CONTROLS`.  There is no
        branch here that accepts a model's request: the model cannot assert its
        own untaintedness, because the model is one of the reasons the turn is
        tainted.
        """

        name = str(control or "").strip()
        if name not in TRUSTED_CLEARING_CONTROLS:
            raise TaintClearRefused(
                f"{name!r} may not clear taint on turn {self.turn_id!r}; only a control in "
                f"{sorted(TRUSTED_CLEARING_CONTROLS)} can, and model-authored input is never "
                "one of them"
            )
        self.cleared_by = name
        self.cleared_at_turn = str(at_turn or "")

    def request_clear_from_model(self, claim: str) -> None:
        """The model's own request to be considered clean. Always refused.

        Present as a named method so the refusal is a decision with a reason
        rather than an absence.
        """

        raise TaintClearRefused(
            f"the model claimed {claim!r} on turn {self.turn_id!r}; model-authored input is never "
            f"authoritative for its own taint state. Taint cleared by: {self.cleared_by or 'nothing'}"
        )

    # -- authority bounds ---------------------------------------------------

    def assert_clean(self, *, for_what: str) -> None:
        if self.tainted:
            raise TaintAuthorityError(
                f"turn {self.turn_id!r} is tainted by {list(self.sources())} and may not be used "
                f"for {for_what}"
            )

    def may_satisfy_approval(self) -> tuple[bool, str]:
        """A tainted turn cannot satisfy an approval gate."""

        if self.tainted:
            return False, (
                f"approval gate refused: turn {self.turn_id!r} is tainted by {list(self.sources())}; "
                "content the gate would be approving arrived from an untrusted source"
            )
        return True, f"turn {self.turn_id!r} is clean"

    def may_record_verified_evidence(self) -> tuple[bool, str]:
        """A tainted turn cannot produce VERIFIED evidence."""

        if self.tainted:
            return False, (
                f"evidence refused: turn {self.turn_id!r} is tainted by {list(self.sources())}; "
                "work computed from untrusted content cannot be recorded as verified"
            )
        return True, f"turn {self.turn_id!r} is clean"

    def requires_human_review(self) -> bool:
        """A tainted turn cannot pass a policy gate without a human."""

        return self.tainted

    # -- reporting ----------------------------------------------------------

    def marks_payload(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "tainted": self.tainted,
            "sources": list(self.sources()),
            "marks": [mark.to_dict() for mark in self._marks],
            "derived_from": list(self.derived_from),
            "origin_turn_id": self.origin_turn_id,
            "isolated": self.isolated,
            "isolation_reason": self.isolation_reason,
            "cleared_by": self.cleared_by,
            "cleared_at_turn": self.cleared_at_turn,
            "labels": list(self.labels),
        }

    def to_dict(self) -> dict[str, Any]:
        return self.marks_payload()


# ---------------------------------------------------------------------------
# The current turn
# ---------------------------------------------------------------------------

_current_turn: ContextVar[TaintTurn | None] = ContextVar("alpha_turn_taint", default=None)


def new_turn(turn_id: str) -> TaintTurn:
    return TaintTurn(turn_id=str(turn_id))


def current_turn() -> TaintTurn | None:
    """The turn in scope on this thread/task, or ``None`` outside a turn."""

    return _current_turn.get()


@contextmanager
def bind_turn(turn: TaintTurn) -> Iterator[TaintTurn]:
    """Bind *turn* as the current turn for the duration of the block."""

    token = _current_turn.set(turn)
    try:
        yield turn
    finally:
        _current_turn.reset(token)


def absorb_untrusted(
    source: UntrustedSource | str,
    *,
    location: str = "",
    turn: TaintTurn | None = None,
) -> TaintMark | None:
    """Absorb untrusted content into the current turn (or *turn*).

    The runtime chokepoint: any component that brings outside content into the
    loop calls this once, and every later step of the turn is inside the taint.
    Returns ``None`` when no turn is bound, so a caller in a non-turn context
    (a CLI, a health probe) is not made to fabricate one.
    """

    target = turn if turn is not None else current_turn()
    if target is None:
        return None
    return target.absorb(source, location=location)


def assert_current_turn_clean(*, for_what: str) -> None:
    """Refuse when the current turn is tainted. A no-op outside a turn."""

    turn = current_turn()
    if turn is not None:
        turn.assert_clean(for_what=for_what)


def taint_sources_for_report() -> tuple[dict[str, str], ...]:
    """The enumeration, as data, for a report or an operator question."""

    return tuple(
        {"source": item.value, "description": (item.__doc__ or "").strip().splitlines()[0]}
        for item in UntrustedSource
    )


# ---------------------------------------------------------------------------
# Isolating controls
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IsolationControl:
    """A named reason a child turn may legitimately be clean.

    Recorded on the child, so "why is this turn not tainted" is answerable
    rather than a gap in the log.
    """

    name: str
    description: str
    requires_control: str

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "description": self.description,
            "requires_control": self.requires_control,
        }


#: Every declared way to spawn an untainted child.  A caller may only pass one
#: of these names; a free-text reason is not isolation, it is a comment.
ISOLATION_CONTROLS: Final[Mapping[str, IsolationControl]] = {
    "fresh_credentials_no_untrusted_input": IsolationControl(
        name="fresh_credentials_no_untrusted_input",
        description=(
            "The child is spawned with its own credentials and is given no content derived from "
            "the tainted turn, so it has nothing the tainted turn could have influenced."
        ),
        requires_control="operator_config_signed",
    ),
    "operator_declared_clean_room": IsolationControl(
        name="operator_declared_clean_room",
        description="A human operator asserted a clean-room spawn and is accountable for it.",
        requires_control="human_operator",
    ),
    "static_verified_derivation": IsolationControl(
        name="static_verified_derivation",
        description=(
            "The child only runs deterministic code over operator-supplied inputs; no untrusted "
            "content reaches it."
        ),
        requires_control="static_verifier",
    ),
}


def isolate_child(turn: TaintTurn, label: str, control: str) -> TaintTurn:
    """Spawn an explicitly isolated child, naming the control that authorises it."""

    name = str(control or "").strip()
    declared = ISOLATION_CONTROLS.get(name)
    if declared is None:
        raise TaintAuthorityError(
            f"{name!r} is not a declared isolation control; expected one of "
            f"{sorted(ISOLATION_CONTROLS)}. A child of a tainted turn inherits taint unless the "
            "isolation is explicit and named."
        )
    child = turn.spawn_child(label, isolated=True, isolation_reason=declared.description)
    child.cleared_by = declared.requires_control
    child.cleared_at_turn = turn.turn_id
    return child


#: Counters for the report and for a test that shows the control is load-bearing.
_stats_lock = threading.Lock()
_stats: dict[str, int] = {}


def record_stat(name: str, amount: int = 1) -> None:
    with _stats_lock:
        _stats[name] = _stats.get(name, 0) + int(amount)


def taint_stats() -> dict[str, int]:
    with _stats_lock:
        return dict(_stats)


__all__ = [
    "ISOLATION_CONTROLS",
    "TRUSTED_CLEARING_CONTROLS",
    "UNTRUSTED_BY_DEFAULT",
    "IsolationControl",
    "TaintAuthorityError",
    "TaintClearRefused",
    "TaintError",
    "TaintMark",
    "TaintTurn",
    "UnknownUntrustedSource",
    "UntrustedSource",
    "absorb_untrusted",
    "assert_current_turn_clean",
    "bind_turn",
    "coerce_source",
    "current_turn",
    "isolate_child",
    "new_turn",
    "record_stat",
    "taint_sources_for_report",
    "taint_stats",
]
