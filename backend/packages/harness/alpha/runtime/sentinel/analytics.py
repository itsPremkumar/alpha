"""Aggregate readings over the Sentinel report journal.

The journal (:mod:`alpha.runtime.sentinel.report_store`) is append-only and
verbatim: every pass lands exactly as the engine produced it. That is the right
shape for a history and the wrong shape for the four questions an operator
actually opens this panel to ask:

* *Is this getting better or worse?* — needs totals across passes, not one row.
* *Which fault keeps coming back?* — needs a per-kind roll-up with first/last
  seen, not a stream of outcome dicts.
* *Is the engine actually repairing anything, or escalating everything?* —
  needs a per-kind verdict from the statuses that kind ever reached.
* *What has a human never looked at?* — needs the ones that escalated most.

This module answers those by **folding the journal**, never by writing a second
record. It is pure: it takes the entries a read already produced and returns a
frozen dataclass. It performs no I/O, so it cannot fail the way a store read
fails, and a caller can test it without touching a filesystem.

Honesty contract — the four rules that make an aggregate trustworthy:

1. **A value nobody measured is ``None``, never ``0``.** ``scanned_total`` is
   ``None`` when no pass reported a ``scanned`` field; a pass that reports it
   alongside one that does not produces a sum over the reporters plus a
   disclosure naming how many did not.
2. **A count of rows and a count of measurements are different numbers.**
   ``outcome_count`` counts outcome dicts that actually exist in the journal;
   ``passes_without_outcomes`` counts passes that carried none. Collapsing them
   turns "the engine recorded nothing per-signal" into "the engine saw no
   signals", which are opposite findings.
3. **A verdict cites evidence.** ``KindReading.verdict`` is derived only from
   statuses this kind actually reached, and a kind that only ever *skipped* is
   ``deferred`` — not a failure, and not a success.
4. **Every reading discloses its own window.** ``disclosures`` names how many
   entries were folded, how many were dropped by a cap, and which fields were
   absent, so a number can never be quoted without the population behind it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Outcome statuses :meth:`SentinelLoop.handle` can produce, worst-last.
#: Anything else is preserved verbatim rather than coerced into this list.
KNOWN_STATUSES: tuple[str, ...] = ("fixed", "reverted", "escalated", "skipped")

#: Verdicts a :class:`KindReading` can carry. ``unmeasured`` is only reachable
#: when a kind appears with no outcome rows at all behind it.
VERDICT_REPAIRED = "repaired"
VERDICT_REVERTED = "reverted"
VERDICT_UNREPAIRED = "unrepaired"
VERDICT_DEFERRED = "deferred"
VERDICT_UNMEASURED = "unmeasured"


def _as_int(value: Any) -> int | None:
    """A finite non-negative int, or ``None``. ``bool`` is not a count."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value >= 0 and value.is_integer() else None
    return None


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        as_float = float(value)
        return as_float if as_float >= 0 else None
    return None


def _as_text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _index_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only well-formed journal entries, in journal order.

    A malformed entry is *skipped and counted*, never silently dropped: the
    honesty contract of the store is that an unreadable line fails the whole
    read, so anything arriving here has already survived that. What can still
    be wrong is the *shape* — an entry that is not an object has no ``report``.
    """
    return [e for e in entries if isinstance(e, dict) and isinstance(e.get("report"), dict)]


@dataclass(frozen=True)
class KindReading:
    """One fault kind rolled up across every folded pass.

    ``passes`` counts the passes this kind appeared in, not the occurrences —
    one pass can record the same kind twice (different fingerprints), and a
    header claiming "seen 3 times" over one pass would be a fabricated count.
    """

    kind: str
    occurrences: int
    passes: int
    fixed: int
    reverted: int
    escalated: int
    skipped: int
    other: int
    #: Outcome rows for this kind that carried no status at all.
    status_missing: int
    distinct_fingerprints: int
    first_seen: str | None
    last_seen: str | None
    verdict: str
    #: Statuses this kind reached that this build does not name, verbatim.
    unknown_statuses: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "occurrences": self.occurrences,
            "passes": self.passes,
            "fixed": self.fixed,
            "reverted": self.reverted,
            "escalated": self.escalated,
            "skipped": self.skipped,
            "other": self.other,
            "status_missing": self.status_missing,
            "distinct_fingerprints": self.distinct_fingerprints,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "verdict": self.verdict,
            "unknown_statuses": list(self.unknown_statuses),
        }


@dataclass(frozen=True)
class RepeatFingerprint:
    """One fingerprint the engine met in more than one pass.

    A fingerprint recurring is not automatically bad — a fault that spans two
    passes of one flapping tool is expected. It *is* the row that decides
    whether the repair is holding, which is why it carries the verdict beside
    the count rather than being implied by it.
    """

    fingerprint: str
    kind: str
    passes: int
    occurrences: int
    first_seen: str | None
    last_seen: str | None
    verdict: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "kind": self.kind,
            "passes": self.passes,
            "occurrences": self.occurrences,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "verdict": self.verdict,
        }


@dataclass(frozen=True)
class SentinelAnalytics:
    """A folded reading over a bounded set of journal entries."""

    #: Entries that were folded. A count of passes, never of signals.
    passes: int
    #: Entries skipped because their shape was unusable.
    malformed: int
    first_recorded_at: str | None
    last_recorded_at: str | None

    passes_with_outcomes: int
    passes_without_outcomes: int
    outcome_count: int
    distinct_fingerprints: int

    scanned_total: int | None
    scanned_reporting_passes: int
    fixed_total: int
    reverted_total: int
    escalated_total: int
    error_total: int

    auto_heal_passes: int | None
    observe_passes: int | None

    trigger_counts: dict[str, int] = field(default_factory=dict)
    status_counts: dict[str, int] = field(default_factory=dict)
    duration_measured_passes: int = 0
    duration_min_s: float | None = None
    duration_mean_s: float | None = None
    duration_max_s: float | None = None

    kinds: tuple[KindReading, ...] = ()
    repeats: tuple[RepeatFingerprint, ...] = ()
    errors: tuple[str, ...] = ()

    #: The cap the caller asked for, or ``None`` when the whole journal folded.
    cap: int | None = None
    #: Entries on disk that the cap excluded. 0 when nothing was excluded.
    dropped_by_cap: int = 0

    disclosures: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "passes": self.passes,
            "malformed": self.malformed,
            "first_recorded_at": self.first_recorded_at,
            "last_recorded_at": self.last_recorded_at,
            "passes_with_outcomes": self.passes_with_outcomes,
            "passes_without_outcomes": self.passes_without_outcomes,
            "outcome_count": self.outcome_count,
            "distinct_fingerprints": self.distinct_fingerprints,
            "scanned_total": self.scanned_total,
            "scanned_reporting_passes": self.scanned_reporting_passes,
            "fixed_total": self.fixed_total,
            "reverted_total": self.reverted_total,
            "escalated_total": self.escalated_total,
            "error_total": self.error_total,
            "auto_heal_passes": self.auto_heal_passes,
            "observe_passes": self.observe_passes,
            "trigger_counts": dict(self.trigger_counts),
            "status_counts": dict(self.status_counts),
            "duration_measured_passes": self.duration_measured_passes,
            "duration_min_s": self.duration_min_s,
            "duration_mean_s": self.duration_mean_s,
            "duration_max_s": self.duration_max_s,
            "kinds": [k.to_dict() for k in self.kinds],
            "repeats": [r.to_dict() for r in self.repeats],
            "errors": list(self.errors),
            "cap": self.cap,
            "dropped_by_cap": self.dropped_by_cap,
            "disclosures": list(self.disclosures),
        }


def _verdict(*, fixed: int, reverted: int, escalated: int, skipped: int, measured: bool) -> str:
    """The single most severe outcome this kind ever reached.

    Ordered worst-last so the verdict is the *best* thing that ever happened,
    which is what an operator reads "repaired?" as. A kind with one fix beside
    nine escalations is still ``repaired`` — with the nine visible in the
    counters beside it, so the word is never doing the numbers' work.
    """
    if not measured:
        return VERDICT_UNMEASURED
    if fixed > 0:
        return VERDICT_REPAIRED
    if reverted > 0:
        return VERDICT_REVERTED
    if escalated > 0:
        return VERDICT_UNREPAIRED
    if skipped > 0:
        return VERDICT_DEFERRED
    return VERDICT_UNMEASURED


def aggregate(
    entries: list[dict[str, Any]],
    *,
    limit: int | None = None,
    total_on_disk: int | None = None,
) -> SentinelAnalytics:
    """Fold journal entries into one reading.

    ``entries`` must already be in journal order (oldest first). ``limit`` is
    the cap the *caller* applied, recorded verbatim so the reading can disclose
    its own window; this function does not re-cap. ``total_on_disk`` is what the
    store reported before the cap, so ``dropped_by_cap`` is a real subtraction
    rather than a guess.
    """
    usable = _index_entries(entries)
    malformed = len(entries) - len(usable)

    passes = len(usable)
    recorded = [_as_text(e.get("recorded_at")) for e in usable]
    known_times = [t for t in recorded if t]

    scanned_total: int | None = None
    scanned_reporting = 0
    fixed_total = reverted_total = escalated_total = error_total = 0
    auto_heal_passes: int | None = None
    observe_passes: int | None = None
    trigger_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    durations: list[float] = []
    errors: list[str] = []

    kind_state: dict[str, dict[str, Any]] = {}
    fp_state: dict[str, dict[str, Any]] = {}
    outcome_count = 0

    for entry in usable:
        report = entry["report"]
        seen_at = _as_text(entry.get("recorded_at"))

        scanned = _as_int(report.get("scanned"))
        if scanned is not None:
            scanned_reporting += 1
            scanned_total = scanned if scanned_total is None else scanned_total + scanned

        for key in ("fixed", "reverted", "escalated"):
            value = _as_int(report.get(key))
            if value is None:
                continue
            if key == "fixed":
                fixed_total += value
            elif key == "reverted":
                reverted_total += value
            else:
                escalated_total += value

        entry_errors = report.get("errors")
        if isinstance(entry_errors, list):
            for item in entry_errors:
                text = _as_text(item)
                if text:
                    errors.append(text)
            error_total += len(entry_errors)

        auto_heal = entry.get("auto_heal")
        if isinstance(auto_heal, bool):
            if auto_heal_passes is None:
                auto_heal_passes = 0
                observe_passes = 0
            if auto_heal:
                auto_heal_passes += 1
            else:
                observe_passes += 1

        trigger = _as_text(entry.get("trigger"))
        if trigger is not None:
            trigger_counts[trigger] = trigger_counts.get(trigger, 0) + 1

        duration = _as_float(report.get("duration_s"))
        if duration is not None:
            durations.append(duration)

        outcomes = report.get("outcomes")
        if not isinstance(outcomes, list):
            continue
        for outcome in outcomes:
            if not isinstance(outcome, dict):
                continue
            outcome_count += 1
            kind = _as_text(outcome.get("kind")) or "unknown"
            status = _as_text(outcome.get("status")) or "unreported"
            fingerprint = _as_text(outcome.get("fingerprint"))

            status_counts[status] = status_counts.get(status, 0) + 1
            status_reported = _as_text(outcome.get("status")) is not None
            unknown_status = status not in KNOWN_STATUSES

            state = kind_state.setdefault(
                kind,
                {
                    "occurrences": 0,
                    "passes": set(),
                    "fixed": 0,
                    "reverted": 0,
                    "escalated": 0,
                    "skipped": 0,
                    "other": 0,
                    "status_missing": 0,
                    "unknown": set(),
                    "fingerprints": set(),
                    "first_seen": None,
                    "last_seen": None,
                },
            )
            state["occurrences"] += 1
            state["passes"].add(entry.get("recorded_at"))
            if seen_at is not None:
                if state["first_seen"] is None:
                    state["first_seen"] = seen_at
                state["last_seen"] = seen_at
            if fingerprint:
                state["fingerprints"].add(fingerprint)
            if status_reported and unknown_status:
                state["unknown"].add(status)
                state["other"] += 1
            elif not status_reported:
                # No status was written at all. That is "the journal did not
                # report" — a different fact from "the journal reported a word
                # this build does not know", and the two must not share a row.
                state["status_missing"] += 1
                state["other"] += 1
            elif status == "fixed":
                state["fixed"] += 1
            elif status == "reverted":
                state["reverted"] += 1
            elif status == "escalated":
                state["escalated"] += 1
            elif status == "skipped":
                state["skipped"] += 1

            if fingerprint:
                fp = fp_state.setdefault(
                    fingerprint,
                    {
                        "kind": kind,
                        "passes": set(),
                        "occurrences": 0,
                        "first_seen": None,
                        "last_seen": None,
                    },
                )
                fp["passes"].add(entry.get("recorded_at"))
                fp["occurrences"] += 1
                if seen_at is not None:
                    if fp["first_seen"] is None:
                        fp["first_seen"] = seen_at
                    fp["last_seen"] = seen_at

    passes_with_outcomes = sum(1 for e in usable if isinstance(e["report"].get("outcomes"), list))
    passes_without_outcomes = passes - passes_with_outcomes

    kinds = tuple(
        sorted(
            (
                KindReading(
                    kind=kind,
                    occurrences=int(state["occurrences"]),
                    passes=len(state["passes"]),
                    fixed=int(state["fixed"]),
                    reverted=int(state["reverted"]),
                    escalated=int(state["escalated"]),
                    skipped=int(state["skipped"]),
                    other=int(state["other"]),
                    status_missing=int(state["status_missing"]),
                    distinct_fingerprints=len(state["fingerprints"]),
                    first_seen=state["first_seen"],
                    last_seen=state["last_seen"],
                    verdict=_verdict(
                        fixed=int(state["fixed"]),
                        reverted=int(state["reverted"]),
                        escalated=int(state["escalated"]),
                        skipped=int(state["skipped"]),
                        measured=True,
                    ),
                    unknown_statuses=tuple(sorted(state["unknown"])),
                )
                for kind, state in kind_state.items()
            ),
            key=lambda k: (-k.escalated, -k.occurrences, k.kind),
        )
    )

    # A fingerprint met in more than one pass is a repeat. The verdict is the
    # rolled-up verdict of its kind: the fingerprint carries no status of its
    # own, so borrowing the kind's would be an inference the log never made.
    repeats = tuple(
        sorted(
            (
                RepeatFingerprint(
                    fingerprint=fp,
                    kind=str(state["kind"]),
                    passes=len(state["passes"]),
                    occurrences=int(state["occurrences"]),
                    first_seen=state["first_seen"],
                    last_seen=state["last_seen"],
                    verdict=_verdict(
                        fixed=int(kind_state[state["kind"]]["fixed"]),
                        reverted=int(kind_state[state["kind"]]["reverted"]),
                        escalated=int(kind_state[state["kind"]]["escalated"]),
                        skipped=int(kind_state[state["kind"]]["skipped"]),
                        measured=True,
                    ),
                )
                for fp, state in fp_state.items()
                if len(state["passes"]) > 1
            ),
            key=lambda r: (-r.passes, -r.occurrences, r.fingerprint),
        )
    )

    duration_mean = (sum(durations) / len(durations)) if durations else None
    dropped = 0
    if total_on_disk is not None:
        dropped = max(0, int(total_on_disk) - passes)

    analytics = SentinelAnalytics(
        passes=passes,
        malformed=malformed,
        first_recorded_at=known_times[0] if known_times else None,
        last_recorded_at=known_times[-1] if known_times else None,
        passes_with_outcomes=passes_with_outcomes,
        passes_without_outcomes=passes_without_outcomes,
        outcome_count=outcome_count,
        distinct_fingerprints=len(fp_state),
        scanned_total=scanned_total,
        scanned_reporting_passes=scanned_reporting,
        fixed_total=fixed_total,
        reverted_total=reverted_total,
        escalated_total=escalated_total,
        error_total=error_total,
        auto_heal_passes=auto_heal_passes,
        observe_passes=observe_passes,
        trigger_counts=dict(sorted(trigger_counts.items())),
        status_counts=dict(sorted(status_counts.items())),
        duration_measured_passes=len(durations),
        duration_min_s=min(durations) if durations else None,
        duration_mean_s=duration_mean,
        duration_max_s=max(durations) if durations else None,
        kinds=kinds,
        repeats=repeats,
        errors=tuple(errors[:50]),
        cap=limit,
        dropped_by_cap=dropped,
        disclosures=(),
    )
    # Built twice on purpose: the disclosure sentences describe the numbers, so
    # they are computed from the finished reading rather than alongside it.
    return SentinelAnalytics(**{**analytics.__dict__, "disclosures": tuple(build_disclosures(analytics))})


def build_disclosures(a: SentinelAnalytics) -> list[str]:
    """Verbatim statements about what this reading did and did not see."""
    lines: list[str] = []
    if a.passes == 0:
        lines.append("history: no passes were folded, so every total below is 0 by construction and no verdict is reachable")
        return lines

    lines.append(f"folded: {a.passes} journal pass(es), oldest first")
    if a.dropped_by_cap:
        lines.append(f"window: {a.dropped_by_cap} older pass(es) were excluded by the requested cap of {a.cap}; these totals describe the window, not the whole journal")
    if a.malformed:
        lines.append(f"shape: {a.malformed} entry(ies) were skipped because they carried no usable 'report' object")
    if a.passes_without_outcomes:
        lines.append(f"detail: {a.passes_without_outcomes} of {a.passes} pass(es) recorded no per-outcome rows; for those, the pass counters are the whole record and no kind or fingerprint was observed")
    if a.scanned_reporting_passes == 0:
        lines.append("scanned: no pass reported a 'scanned' count, so the signal total is null rather than 0")
    elif a.scanned_reporting_passes < a.passes:
        lines.append(f"scanned: summed over the {a.scanned_reporting_passes} pass(es) that reported it; {a.passes - a.scanned_reporting_passes} did not")
    if a.duration_measured_passes == 0:
        lines.append("duration: no pass reported a measured duration, so the duration block is null rather than 0")
    elif a.duration_measured_passes < a.passes:
        lines.append(f"duration: measured over {a.duration_measured_passes} of {a.passes} pass(es)")
    if a.auto_heal_passes is None:
        lines.append("mode: no pass reported its auto_heal flag, so the repair/observe split is unknown")
    else:
        lines.append(f"mode: {a.auto_heal_passes} repair pass(es) and {a.observe_passes} observe pass(es)")
    lines.append("integrity: these totals are folded from the append-only journal; the journal itself is never rewritten ")
    return lines
