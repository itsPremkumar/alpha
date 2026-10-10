"""Typed value types for the Arena engine.

The mutable tournament state itself is a plain dict (see ``bracket.py``
and ``store.py``) so it round-trips to JSON with no translation layer.
Everything that needs validation, parsing or arithmetic lives here as a
frozen dataclass with an explicit ``to_dict``/``from_dict`` pair.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ArenaPhase(StrEnum):
    """Tournament phase marker (what kind of work is next)."""

    DRAFT = "draft"
    SPAWN = "spawn"
    ATTACK = "attack"
    DEFEND = "defend"
    JUDGE = "judge"
    FINAL = "final"
    DONE = "done"


class ArenaStatus(StrEnum):
    """Lifecycle status of a run."""

    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    STOPPED = "stopped"
    FAILED = "failed"
    BUDGET_EXHAUSTED = "budget_exhausted"


# ---------------------------------------------------------------- card parts

# ---------------------------------------------------------------- card parts


class CardPartKind(StrEnum):
    """Which dimension of a strategy card a part occupies."""

    REASONING = "reasoning"
    WORKFLOW = "workflow"
    STRATEGY = "strategy"


@dataclass(frozen=True)
class CardPart:
    """One third of a strategy card: a named way of working."""

    id: str
    name: str
    how: str

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "name": self.name, "how": self.how}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CardPart:
        return cls(id=str(data["id"]), name=str(data["name"]), how=str(data["how"]))


@dataclass(frozen=True)
class StrategyCard:
    """The complete card dealt to one competitor.

    A card never changes for the life of a run: a competitor is its card
    plus its current solution file, and sub-agents remember nothing
    between calls, so the card is what gives a competitor an identity
    across rounds.
    """

    reasoning: CardPart
    workflow: CardPart
    strategy: CardPart

    @property
    def line(self) -> str:
        """One-line card summary, e.g. for a bracket report."""
        return f"{self.reasoning.name} + {self.workflow.name} + {self.strategy.name}"

    def to_dict(self) -> dict[str, dict[str, str]]:
        return {
            "reasoning": self.reasoning.to_dict(),
            "workflow": self.workflow.to_dict(),
            "strategy": self.strategy.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StrategyCard:
        return cls(
            reasoning=CardPart.from_dict(data["reasoning"]),
            workflow=CardPart.from_dict(data["workflow"]),
            strategy=CardPart.from_dict(data["strategy"]),
        )


# ---------------------------------------------------------------- attacks


class AttackSeverity(StrEnum):
    """How badly an attack wounds a solution."""

    FATAL = "FATAL"
    MAJOR = "MAJOR"
    MINOR = "MINOR"


_ATTACK_LINE_RE = re.compile(
    r"^ATTACK\s+(?P<index>\d+)\s+\[(?P<severity>FATAL|MAJOR|MINOR)\]\s*(?P<title>.*)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AttackRecord:
    """One concrete, checkable flaw raised against a solution."""

    index: int
    title: str
    severity: AttackSeverity
    where: str
    problem: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "title": self.title,
            "severity": self.severity.value,
            "where": self.where,
            "problem": self.problem,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AttackRecord:
        return cls(
            index=int(data["index"]),
            title=str(data["title"]),
            severity=AttackSeverity(str(data["severity"]).upper()),
            where=str(data.get("where", "")),
            problem=str(data.get("problem", "")),
        )


def parse_attacks(text: str | None) -> list[AttackRecord]:
    """Parse the attacker's ``ATTACK n [SEV] title`` output format.

    Tolerates prose before and after the attacks, missing ``Where:``/
    ``Problem:`` continuations and empty files. An unparsable or empty
    attack file is *no attacks*, never an error: the defender is then
    told it received none, which is the honest reading of silence.
    """
    if not text:
        return []
    attacks: list[AttackRecord] = []
    current: dict[str, Any] | None = None
    section: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        match = _ATTACK_LINE_RE.match(line)
        if match:
            if current is not None:
                attacks.append(AttackRecord(**current))
            current = {
                "index": int(match.group("index")),
                "title": match.group("title").strip(),
                "severity": AttackSeverity(match.group("severity").upper()),
                "where": "",
                "problem": "",
            }
            section = None
            continue
        if current is None:
            continue
        lowered = line.lower()
        if lowered.startswith("where:"):
            current["where"] = line[len("where:") :].strip()
            section = "where"
        elif lowered.startswith("problem:"):
            current["problem"] = line[len("problem:") :].strip()
            section = "problem"
        elif section == "where" and line:
            current["where"] = f"{current['where']} {line}".strip()
        elif section == "problem" and line:
            current["problem"] = f"{current['problem']} {line}".strip()
    if current is not None:
        attacks.append(AttackRecord(**current))
    return attacks


# ---------------------------------------------------------------- defenses


class DefenseVerdict(StrEnum):
    """How a defender answered one attack."""

    CONCEDE = "CONCEDE"
    REBUT = "REBUT"


@dataclass(frozen=True)
class DefenseRecord:
    """One point-by-point defense entry."""

    attack_index: int
    verdict: DefenseVerdict
    note: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "attack_index": self.attack_index,
            "verdict": self.verdict.value,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DefenseRecord:
        return cls(
            attack_index=int(data["attack_index"]),
            verdict=DefenseVerdict(str(data["verdict"]).upper()),
            note=str(data.get("note", "")),
        )


_DEFENSE_LINE_RE = re.compile(r"^ATTACK\s+(?P<index>\d+)\s*:\s*(?P<verdict>CONCEDE|REBUT)\b", re.IGNORECASE)


def parse_defenses(text: str | None) -> list[DefenseRecord]:
    """Parse the defender's ``ATTACK n: CONCEDE|REBUT. note`` format."""
    if not text:
        return []
    records: list[DefenseRecord] = []
    for raw_line in text.splitlines():
        match = _DEFENSE_LINE_RE.match(raw_line.strip())
        if match:
            note = raw_line.strip()[match.end() :].lstrip(".").strip()
            records.append(
                DefenseRecord(
                    attack_index=int(match.group("index")),
                    verdict=DefenseVerdict(match.group("verdict").upper()),
                    note=note,
                )
            )
    return records


# ---------------------------------------------------------------- rubric scores


@dataclass(frozen=True)
class VerdictScores:
    """One solution's scores on the five rubric criteria (0-10 each)."""

    correctness: float
    completeness: float
    specificity: float
    robustness: float
    clarity: float
    fatal: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "correctness": self.correctness,
            "completeness": self.completeness,
            "specificity": self.specificity,
            "robustness": self.robustness,
            "clarity": self.clarity,
            "fatal": self.fatal,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VerdictScores:
        return cls(
            correctness=float(data.get("correctness", 0.0)),
            completeness=float(data.get("completeness", 0.0)),
            specificity=float(data.get("specificity", 0.0)),
            robustness=float(data.get("robustness", 0.0)),
            clarity=float(data.get("clarity", 0.0)),
            fatal=bool(data.get("fatal", False)),
        )


# ---------------------------------------------------------------- planning


@dataclass(frozen=True)
class ArenaPlanRow:
    """One round of a projected bracket."""

    round: int
    alive: int
    matches: int
    bye: bool
    calls: int
    waves: int


@dataclass(frozen=True)
class ArenaPlan:
    """The cost projection for a run, computed before anything is spent."""

    agents: int
    rounds: int
    calls: int
    waves: int
    wave_size: int
    final_check: bool
    rows: list[ArenaPlanRow]

    @property
    def total_calls(self) -> int:
        """Sub-agent calls including the final check when there is a baseline."""
        return self.calls + (1 if self.final_check else 0)

    @property
    def total_waves(self) -> int:
        return self.waves + (1 if self.final_check else 0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agents": self.agents,
            "rounds": self.rounds,
            "calls": self.calls,
            "waves": self.waves,
            "wave_size": self.wave_size,
            "final_check": self.final_check,
            "total_calls": self.total_calls,
            "total_waves": self.total_waves,
            "rows": [row.__dict__ for row in self.rows],
        }


@dataclass
class ArenaBudget:
    """Measured spend for one run, against declared ceilings.

    Every field is measured, never estimated: ``measured_calls`` counts
    sub-agent calls the runner actually dispatched and
    ``measured_tokens`` sums the token usage the executor reported back.
    A ceiling that is exhausted is reported with the measured figures,
    never a rounded-down remainder.
    """

    max_subagent_calls: int | None = None
    max_tokens: int | None = None
    max_wall_seconds: float | None = None
    measured_calls: int = 0
    measured_tokens: int = 0
    started_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_subagent_calls": self.max_subagent_calls,
            "max_tokens": self.max_tokens,
            "max_wall_seconds": self.max_wall_seconds,
            "measured_calls": self.measured_calls,
            "measured_tokens": self.measured_tokens,
            "started_at": self.started_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArenaBudget:
        return cls(
            max_subagent_calls=data.get("max_subagent_calls"),
            max_tokens=data.get("max_tokens"),
            max_wall_seconds=data.get("max_wall_seconds"),
            measured_calls=int(data.get("measured_calls", 0)),
            measured_tokens=int(data.get("measured_tokens", 0)),
            started_at=data.get("started_at"),
        )

    def charge(self, calls: int = 1, tokens: int = 0) -> None:
        """Record measured spend. Raises nothing; exhaustion is a status,
        decided by ``is_exhausted`` so the caller can report which axis
        fired."""
        self.measured_calls += max(0, int(calls))
        self.measured_tokens += max(0, int(tokens))

    def is_exhausted(self) -> str | None:
        """Return the name of the first exhausted axis, or None."""
        if self.max_subagent_calls is not None and self.measured_calls >= self.max_subagent_calls:
            return "subagent_calls"
        if self.max_tokens is not None and self.measured_tokens >= self.max_tokens:
            return "tokens"
        return None


__all__ = [
    "AttackRecord",
    "AttackSeverity",
    "ArenaBudget",
    "ArenaPlan",
    "ArenaPlanRow",
    "CardPart",
    "CardPartKind",
    "DefenseRecord",
    "DefenseVerdict",
    "StrategyCard",
    "VerdictScores",
    "parse_attacks",
    "parse_defenses",
]
