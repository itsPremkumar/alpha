"""Private, append-only work journals for Bots — plus the blocker roll-up.

A blocked Bot writes its blocker down and then goes quiet, so blockers pile up
unseen, one Bot at a time. This module gives every Bot a durable record of what
it actually *did* (outcomes and evidence, never credentials or private
reasoning), and gives the operator one question that answers for all of them:

    "anything waiting on me?"  ->  waiting_on_you across the whole roster

Design contract:

* **Append-only.** Entries are never rewritten in place; corrections are new
  entries that reference the old one.
* **Refuses unsafe content.** Credential-shaped text, private reasoning and
  chain-of-thought are rejected at write time, so the journal is safe to read
  back into a later turn.
* **Local only.** Journals never travel in a shareable export — only in an
  explicit private backup.
* **Self-closing blockers.** An entry that records the same work as completed
  closes the matching open blocker. Nothing to tick off by hand.

Layout: ``<journal_dir>/<bot>/YYYY-MM-DD.md`` with an ``index.json`` holding
the structured blocker state (the Markdown is the human record, the JSON is the
queryable one).
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

__all__ = [
    "JournalEntry",
    "Blocker",
    "BotJournal",
    "get_journal",
    "waiting_on_you",
    "JournalPolicy",
]

# ---------------------------------------------------------------------------
# Policy: what a journal entry may never contain
# ---------------------------------------------------------------------------

_SECRET_SHAPED = re.compile(
    r"(?:"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|(?:sk|pk|rk|ghp|gho|ghs|ghu|xox[baprs])[-_][A-Za-z0-9\-_]{8,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"
    r"|(?:api[_-]?key|apikey|secret|token|passwd|password)\s*[:=]\s*\S+"
    r")",
    re.IGNORECASE,
)

_REASONING_SHAPED = re.compile(
    r"(?:"
    r"^\s*(?:chain of thought|cot|hidden reasoning|internal reasoning)\s*:"
    r"|<thinking>"
    r"|^\s*let me think (?:step by step|this through)"
    r")",
    re.IGNORECASE | re.MULTILINE,
)

#: Field labels an entry may use. Anything else is treated as free prose and
#: still scanned, but the labelled fields are what the roll-up reads.
FIELDS = ("tried", "outcome", "evidence", "blocker", "next_step", "completed")


@dataclass(frozen=True)
class JournalPolicy:
    """What the journal refuses, in one place."""

    max_entry_chars: int = 2000
    allow_secrets: bool = False
    allow_private_reasoning: bool = False

    def violation(self, text: str) -> str | None:
        """Return a refusal reason, or ``None`` when the text is acceptable."""
        if not text or not text.strip():
            return "empty entry"
        if len(text) > self.max_entry_chars:
            return f"entry exceeds {self.max_entry_chars} characters"
        if not self.allow_secrets and _SECRET_SHAPED.search(text):
            return "entry contains credential-shaped content (refused)"
        if not self.allow_private_reasoning and _REASONING_SHAPED.search(text):
            return "entry contains private reasoning / chain-of-thought (refused)"
        return None


DEFAULT_POLICY = JournalPolicy()


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass
class JournalEntry:
    """One dated, factual record of work."""

    bot: str
    day: str
    at: str
    title: str
    tried: str = ""
    outcome: str = ""
    evidence: list[str] = field(default_factory=list)
    blocker: str = ""
    completed: str = ""
    next_step: str = ""
    state: Literal["noted", "blocked", "done"] = "noted"

    @property
    def id(self) -> str:
        return f"{self.day}#{self.at}#{self.title[:40]}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JournalEntry:
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass
class Blocker:
    """An open item that is waiting on the operator."""

    bot: str
    title: str
    opened_at: str
    opened_day: str
    closed_at: str | None = None

    @property
    def is_open(self) -> bool:
        return self.closed_at is None

    def age_days(self, now: datetime | None = None) -> int:
        current = now or datetime.now(UTC)
        try:
            opened = datetime.fromisoformat(self.opened_at)
        except ValueError:
            return 0
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=UTC)
        return max(0, (current - opened).days)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Blocker:
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _split_fields(body: str) -> dict[str, str]:
    """Split an entry body into its labelled fields.

    Accepts ``**blocker**: text`` / ``blocker: text`` / ``- blocker: text``.
    Anything before the first label is the ``tried`` field.
    """
    out: dict[str, str] = {}
    current: str | None = None
    preamble: list[str] = []
    pattern = re.compile(r"^\s*(?:[-*]\s*)?\**(" + "|".join(FIELDS) + r")\**\s*:\s*(.*)$", re.I)
    for line in body.splitlines():
        match = pattern.match(line)
        if match:
            current = match.group(1).lower()
            out.setdefault(current, "")
            if match.group(2).strip():
                out[current] = (out[current] + " " + match.group(2).strip()).strip()
        elif current is None:
            if line.strip():
                preamble.append(line.strip())
        elif line.strip():
            out[current] = (out[current] + " " + line.strip()).strip()
    if preamble:
        out.setdefault("tried", "")
        out["tried"] = (" ".join(preamble) + " " + out.get("tried", "")).strip()
    return out


# ---------------------------------------------------------------------------
# The journal
# ---------------------------------------------------------------------------


class BotJournal:
    """Append-only work journal for one Bot, under a shared root."""

    def __init__(
        self,
        root: str | Path,
        bot: str,
        *,
        policy: JournalPolicy = DEFAULT_POLICY,
    ) -> None:
        self.root = Path(root)
        self.bot = (bot or "").strip().lower()
        if not self.bot:
            raise ValueError("journal requires a bot name")
        # A bot name is operator-supplied and may contain separators or "..".
        safe = re.sub(r"[^a-z0-9._-]+", "-", self.bot)
        safe = re.sub(r"\.{2,}", ".", safe).strip("-.")
        self.bot_dir = self.root / (safe or "bot")
        self.policy = policy
        self._lock = threading.Lock()

    # -- paths ------------------------------------------------------------

    @property
    def index_path(self) -> Path:
        return self.bot_dir / "index.json"

    def _day_path(self, day: str) -> Path:
        # day is produced by _today() and is ISO; validate rather than trust.
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            raise ValueError(f"invalid journal day: {day!r}")
        return self.bot_dir / f"{day}.md"

    # -- enabled ----------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """A journal exists once its directory does."""
        return self.bot_dir.is_dir()

    def enable(self, policy_note: str = "") -> Path:
        """Create the journal and its policy note. Idempotent."""
        with self._lock:
            self.bot_dir.mkdir(parents=True, exist_ok=True)
            guide = self.bot_dir / "POLICY.md"
            if not guide.exists():
                guide.write_text(
                    "# Work journal policy\n\n"
                    "Record **outcomes and evidence** after meaningful work.\n"
                    "Skip routine conversation.\n\n"
                    "Never record:\n"
                    "- credentials or anything credential-shaped\n"
                    "- facts unrelated to this Bot's job\n"
                    "- private reasoning or hidden chain-of-thought\n\n" + (policy_note.strip() + "\n" if policy_note else ""),
                    encoding="utf-8",
                )
            if not self.index_path.exists():
                self._write_index({"entries": [], "blockers": []})
            return self.bot_dir

    def disable(self) -> bool:
        """Remove the journal directory. Returns True when something went."""
        with self._lock:
            if not self.bot_dir.is_dir():
                return False
            import shutil

            shutil.rmtree(self.bot_dir, ignore_errors=True)
            return True

    # -- index IO ---------------------------------------------------------

    def _read_index(self) -> dict[str, Any]:
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"entries": [], "blockers": []}
        if not isinstance(data, dict):
            return {"entries": [], "blockers": []}
        data.setdefault("entries", [])
        data.setdefault("blockers", [])
        return data

    def _write_index(self, data: dict[str, Any]) -> None:
        self.bot_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.index_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.index_path)

    # -- writing ----------------------------------------------------------

    @staticmethod
    def _today() -> str:
        return datetime.now(UTC).date().isoformat()

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def append(
        self,
        title: str,
        *,
        tried: str = "",
        outcome: str = "",
        evidence: Iterable[str] | str = (),
        blocker: str = "",
        completed: str = "",
        next_step: str = "",
    ) -> JournalEntry | str:
        """Append one entry.

        Returns the :class:`JournalEntry` on success, or a **refusal string**
        when the policy rejects it. Refusals are strings, not exceptions, so a
        Bot is told *why* it was refused and the turn continues.
        """
        title = (title or "").strip()
        if not title:
            return "refused: entry requires a title"

        if isinstance(evidence, str):
            evidence_list = [evidence] if evidence.strip() else []
        else:
            evidence_list = [str(e).strip() for e in evidence if str(e).strip()]

        body = "\n".join(x for x in (tried, outcome, blocker, completed, next_step, *evidence_list) if x)
        violation = self.policy.violation(f"{title}\n{body}")
        if violation:
            return f"refused: {violation}"

        # Derive state honestly from what was actually recorded.
        if blocker.strip():
            state: Literal["noted", "blocked", "done"] = "blocked"
        elif completed.strip() or outcome.strip():
            state = "done"
        else:
            state = "noted"

        entry = JournalEntry(
            bot=self.bot,
            day=self._today(),
            at=self._now(),
            title=title,
            tried=tried.strip(),
            outcome=outcome.strip(),
            evidence=evidence_list,
            blocker=blocker.strip(),
            completed=completed.strip(),
            next_step=next_step.strip(),
            state=state,
        )

        with self._lock:
            self.bot_dir.mkdir(parents=True, exist_ok=True)
            self._append_markdown(entry)
            index = self._read_index()
            index["entries"].append(entry.to_dict())
            # Bound the index: the Markdown is the durable record.
            index["entries"] = index["entries"][-500:]
            self._apply_blocker_lifecycle(index, entry)
            self._write_index(index)
        return entry

    def _append_markdown(self, entry: JournalEntry) -> None:
        path = self._day_path(entry.day)
        stamp = entry.at[11:19] or entry.at
        lines = [f"## {stamp} — {entry.title}", ""]
        if entry.tried:
            lines.append(f"- tried: {entry.tried}")
        if entry.outcome:
            lines.append(f"- outcome: {entry.outcome}")
        for item in entry.evidence:
            lines.append(f"- evidence: {item}")
        if entry.blocker:
            lines.append(f"- blocker: {entry.blocker}")
        if entry.completed:
            lines.append(f"- completed: {entry.completed}")
        if entry.next_step:
            lines.append(f"- next: {entry.next_step}")
        lines.append(f"- state: {entry.state}")
        lines.append("")
        existing = ""
        if path.exists():
            existing = path.read_text(encoding="utf-8")
        with path.open("a", encoding="utf-8") as fh:
            if existing and not existing.endswith("\n"):
                fh.write("\n")
            fh.write("\n".join(lines) + "\n")

    def _apply_blocker_lifecycle(self, index: dict[str, Any], entry: JournalEntry) -> None:
        """Open a blocker on block; close it when the same work completes."""
        blockers: list[dict[str, Any]] = index["blockers"]

        if entry.blocker:
            open_titles = {b.get("title", "").strip().lower() for b in blockers if not b.get("closed_at")}
            key = entry.blocker.strip().lower()
            if key not in open_titles:
                blockers.append(
                    Blocker(
                        bot=self.bot,
                        title=entry.blocker.strip(),
                        opened_at=entry.at,
                        opened_day=entry.day,
                    ).to_dict()
                )

        # Completion closes by title match — nothing to tick by hand.
        finished = (entry.completed or "").strip().lower()
        if finished:
            for raw in blockers:
                if raw.get("closed_at"):
                    continue
                if Blocker.from_dict(raw).title.strip().lower() == finished:
                    raw["closed_at"] = entry.at

        # A resolved outcome also closes: "published the post" closes
        # "could not publish the post" when the titles share their core terms.
        if entry.state == "done" and entry.outcome:
            outcome_terms = {t for t in _tokens(entry.outcome)}
            for raw in blockers:
                if raw.get("closed_at"):
                    continue
                blocker_terms = set(_tokens(Blocker.from_dict(raw).title))
                if blocker_terms and blocker_terms <= outcome_terms:
                    raw["closed_at"] = entry.at

        index["blockers"] = blockers

    # -- reading ----------------------------------------------------------

    def read(self, *, day: str | None = None, limit: int = 20) -> list[JournalEntry]:
        """Most recent entries, newest first. ``day`` narrows to one date."""
        with self._lock:
            raw = self._read_index()["entries"]
        entries = [JournalEntry.from_dict(e) for e in raw]
        if day:
            entries = [e for e in entries if e.day == day]
        entries.sort(key=lambda e: e.at, reverse=True)
        return entries[: max(1, limit)]

    def read_day(self, day: str) -> str:
        """The raw Markdown for one day ('' when there is none)."""
        path = self._day_path(day)
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def open_blockers(self) -> list[Blocker]:
        """Unresolved blockers for this Bot, newest first."""
        with self._lock:
            raw = self._read_index()["blockers"]
        out = [Blocker.from_dict(b) for b in raw if Blocker.from_dict(b).is_open]
        out.sort(key=lambda b: b.opened_at, reverse=True)
        return out

    def summary(self) -> dict[str, Any]:
        """Counts for the health check."""
        with self._lock:
            index = self._read_index()
        entries = index["entries"]
        blockers = [b for b in index["blockers"] if not b.get("closed_at")]
        days = sorted({e.get("day", "") for e in entries})
        return {
            "bot": self.bot,
            "enabled": self.enabled,
            "entries": len(entries),
            "blockers_open": len(blockers),
            "first_day": days[0] if days else None,
            "last_day": days[-1] if days else None,
        }


def _tokens(text: str) -> list[str]:
    stop = {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "was",
        "were",
        "not",
        "but",
        "its",
        "it's",
        "has",
        "have",
        "had",
        "can",
        "cannot",
        "will",
        "from",
        "into",
        "when",
        "then",
        "than",
        "them",
        "they",
        "you",
        "your",
        "our",
        "out",
        "get",
        "got",
        "did",
        "does",
        "all",
        "any",
        "one",
        "two",
    }
    return [t for t in re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).split() if len(t) > 2 and t not in stop]


# ---------------------------------------------------------------------------
# Process-wide accessor + cross-Bot roll-up
# ---------------------------------------------------------------------------

_ROOTS: dict[str, BotJournal] = {}
_ROOT_LOCK = threading.Lock()


def get_journal(root: str | Path, bot: str, **kwargs: Any) -> BotJournal:
    """Return (creating if needed) the journal accessor for ``bot``."""
    key = f"{Path(root)}::{bot.lower()}"
    with _ROOT_LOCK:
        existing = _ROOTS.get(key)
        if existing is not None:
            return existing
        journal = BotJournal(root, bot, **kwargs)
        _ROOTS[key] = journal
        return journal


def discover_journals(root: str | Path) -> list[BotJournal]:
    """Every journal that actually exists under ``root``, roster or not.

    The roll-up must be driven by what is on disk rather than by the live
    roster: a Bot that recorded a blocker and was then retired or renamed
    would otherwise have its blocker vanish from ``waiting_on`` — which is
    exactly the pile-up this feature exists to prevent.
    """
    base = Path(root)
    if not base.is_dir():
        return []
    out: list[BotJournal] = []
    try:
        children = sorted(base.iterdir())
    except OSError:
        return []
    for child in children:
        if not child.is_dir() or not (child / "index.json").is_file():
            continue
        try:
            out.append(get_journal(base, child.name))
        except ValueError:
            continue  # a directory name BotJournal refuses to accept
    return out


def waiting_on_you(
    journals: Iterable[BotJournal],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Every unresolved blocker across every Bot, newest first.

    This is the answer to *"anything waiting on me?"* — one line for the whole
    roster instead of opening each Bot in turn.
    """
    current = now or datetime.now(UTC)
    rows: list[dict[str, Any]] = []
    for journal in journals:
        for blocker in journal.open_blockers():
            rows.append(
                {
                    "bot": blocker.bot,
                    "needs": blocker.title,
                    "days_waiting": blocker.age_days(current),
                    "opened_at": blocker.opened_at,
                    "opened_day": blocker.opened_day,
                }
            )
    rows.sort(key=lambda r: (-r["days_waiting"], r["bot"], r["needs"]))
    return rows


def format_waiting(rows: list[dict[str, Any]], *, limit: int = 10) -> str:
    """Render ``waiting_on_you`` as the one-line summary operators ask for."""
    if not rows:
        return "Nothing is waiting on you — no open blockers across the roster."
    shown = rows[:limit]
    head = f"{len(rows)} waiting on you"
    parts = [f"{r['bot']}: {r['needs']}" for r in shown]
    line = f"{head} — " + "; ".join(parts)
    if len(rows) > limit:
        line += f" (+{len(rows) - limit} more)"
    return line
