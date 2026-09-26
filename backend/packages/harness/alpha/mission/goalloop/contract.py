"""Completion contracts: five optional fields layered on a standing goal.

A bare goal is judgeable but vague, and a vague goal produces vague judging. The
contract names *what done means, how to prove it, what must not break, what is
in scope, and when to stop* - the five things that turn "keep going" into a
loop that terminates for the right reason.

Two ways in:

* **Inline.** ``field: value`` lines mixed into the goal text. Only the known
  prefixes below are recognised, so ``Fix bug: the parser drops commas`` keeps
  its colon and stays the headline. That non-mangling rule is the whole reason
  this is a whitelist and not "split on the first colon".
* **Drafted.** :func:`draft_contract` asks an auxiliary model to expand a plain
  objective. Drafting is best-effort: if the model is unavailable the caller
  still gets a usable free-form goal, because *setting a goal must never depend
  on a second model being up*.

The contract is advisory input to the judge and the continuation prompt. It is
NOT a trust boundary: it never grants privilege, never approves its own
completion, and a contract that says ``verification: whatever looks right`` does
not make a red gate green. The gate in :mod:`gates` is the deterministic
control; this is the prose the judge reads.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

#: The five canonical contract fields, in the order they are rendered.
CONTRACT_FIELDS: Final[tuple[str, ...]] = (
    "outcome",
    "verification",
    "constraints",
    "boundaries",
    "stop_when",
)

#: Prefix -> canonical field. Aliases exist because operators type the words
#: they think of, and a rejected alias silently drops a real constraint.
_PREFIX_ALIASES: Final[dict[str, str]] = {
    "outcome": "outcome",
    "goal": "outcome",
    "deliverable": "outcome",
    "verification": "verification",
    "verify": "verification",
    "verified by": "verification",
    "proof": "verification",
    "constraints": "constraints",
    "preserve": "constraints",
    "boundaries": "boundaries",
    "boundary": "boundaries",
    "scope": "boundaries",
    "stop when": "stop_when",
    "stop_when": "stop_when",
    "stopwhen": "stop_when",
}

#: A line is a field line only if this many characters follow the colon.
#: ``verify:`` with nothing after it is a typo, not a constraint.
_MIN_FIELD_VALUE_CHARS: Final[int] = 1

_LINE_SPLIT: Final[re.Pattern[str]] = re.compile(r"\r\n|\r|\n")


@dataclass(frozen=True)
class CompletionContract:
    """Five optional fields. Every one of them may legitimately be empty."""

    outcome: str = ""
    verification: str = ""
    constraints: str = ""
    boundaries: str = ""
    stop_when: str = ""

    @property
    def is_empty(self) -> bool:
        return not any(getattr(self, name) for name in CONTRACT_FIELDS)

    def get(self, name: str) -> str:
        if name not in CONTRACT_FIELDS:
            raise KeyError(f"unknown completion-contract field '{name}'")
        return getattr(self, name)

    def to_dict(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in CONTRACT_FIELDS}

    def render(self) -> str:
        """Render the populated fields as ``field: value`` lines."""
        rows = [f"{name}: {getattr(self, name)}" for name in CONTRACT_FIELDS if getattr(self, name)]
        return "\n".join(rows)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CompletionContract:
        return cls(**{name: str(data.get(name, "") or "") for name in CONTRACT_FIELDS})


@dataclass(frozen=True)
class ParsedGoal:
    """A goal text split into its headline and its contract."""

    objective: str
    contract: CompletionContract = field(default_factory=CompletionContract)

    @property
    def has_contract(self) -> bool:
        return not self.contract.is_empty


def _field_for_line(line: str) -> tuple[str, str] | None:
    """Return ``(canonical_field, value)`` if *line* is a field line.

    A field line is ``<known prefix><colon><at least one non-space char>``.
    The prefix is matched case-insensitively after stripping, and the *longest*
    matching alias wins so ``stop when:`` is not captured by a hypothetical
    shorter alias.
    """
    if ":" not in line:
        return None
    prefix, _, value = line.partition(":")
    key = " ".join(prefix.strip().lower().split())
    if not key:
        return None
    if len(value.strip()) < _MIN_FIELD_VALUE_CHARS:
        return None
    canonical = _PREFIX_ALIASES.get(key)
    if canonical is None:
        return None
    return canonical, value.strip()


def parse_goal_text(text: str) -> ParsedGoal:
    """Split goal text into a headline and a contract.

    Lines whose prefix is a known field name are consumed as contract fields;
    every other line - including a line with an incidental colon - stays in the
    objective, in order. The objective is the non-field lines joined by
    newlines, which is what the judge and the continuation prompt both quote.
    """
    objective_lines: list[str] = []
    fields: dict[str, list[str]] = {}

    for raw_line in _LINE_SPLIT.split(text or ""):
        line = raw_line.strip()
        if not line:
            continue
        parsed = _field_for_line(line)
        if parsed is None:
            objective_lines.append(line)
            continue
        name, value = parsed
        fields.setdefault(name, []).append(value)

    contract = CompletionContract(
        **{name: "\n".join(fields.get(name, [])) for name in CONTRACT_FIELDS},
    )
    return ParsedGoal(objective="\n".join(objective_lines).strip(), contract=contract)


#: Signature of the drafting call. It receives the plain objective and returns
#: either a contract-shaped mapping, a free-form string, or ``None``.
DraftFn = Callable[[str], Awaitable["CompletionContract | Mapping[str, Any] | str | None"]]


def _coerce_draft(raw: CompletionContract | Mapping[str, Any] | str | None) -> CompletionContract | None:
    if raw is None:
        return None
    if isinstance(raw, CompletionContract):
        return raw
    if isinstance(raw, Mapping):
        contract = CompletionContract.from_dict(raw)
        return None if contract.is_empty else contract
    if isinstance(raw, str):
        # A model asked for JSON usually answers with JSON; fall back to the
        # inline `field: value` grammar only when it did not.
        stripped = raw.strip()
        if stripped.startswith("{"):
            import json

            try:
                decoded = json.loads(stripped)
            except ValueError:
                decoded = None
            if isinstance(decoded, Mapping):
                contract = CompletionContract.from_dict(decoded)
                return None if contract.is_empty else contract
        parsed = parse_goal_text(raw)
        return None if parsed.contract.is_empty else parsed.contract
    return None


async def draft_contract(objective: str, draft: DraftFn | None) -> tuple[CompletionContract | None, str | None]:
    """Draft a contract from a plain objective. Never raises.

    Returns ``(contract, error)``. ``contract`` is ``None`` when no draft could
    be produced - either because no drafting function was supplied, or because
    the draft failed, or because the draft was unusable. The caller then keeps
    the plain free-form goal. Drafting failing must not stop an operator from
    setting a goal.
    """
    if draft is None:
        return None, "no contract drafter configured"
    try:
        raw = await draft(objective)
    except Exception as exc:  # noqa: BLE001 - drafting is best-effort by design
        return None, f"{type(exc).__name__}: {exc}"
    contract = _coerce_draft(raw)
    if contract is None:
        return None, "drafter returned no usable contract fields"
    return contract, None


def build_draft_prompt(objective: str) -> str:
    """The prompt used to expand a one-liner into a contract."""
    return (
        "Expand this objective into a completion contract. Reply with one JSON "
        "object and nothing else, using exactly these keys: "
        '"outcome", "verification", "constraints", "boundaries", "stop_when". '
        "Use an empty string for any field you cannot infer honestly. "
        "verification must name a specific command, test, or artifact that "
        "proves the outcome - never 'looks right' or 'seems to work'.\n\n"
        f"Objective: {objective}"
    )


__all__ = [
    "CONTRACT_FIELDS",
    "CompletionContract",
    "DraftFn",
    "ParsedGoal",
    "build_draft_prompt",
    "draft_contract",
    "parse_goal_text",
]
