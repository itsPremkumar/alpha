"""Microtask decomposition: the contract between fingerprinting and routing (plan §3.2).

A :class:`Microtask` is one named operation with ONE deliverable and
*explicit* acceptance criteria; a :class:`Decomposition` is the bounded set
of microtasks one task breaks into, with its dependency graph validated
(acyclic, resolvable, unique ids) and its provenance recorded in
``decomposer``/``notes``.

Four rules decide the shape of this module:

* **No assignment without acceptance criteria.** Every microtask MUST carry
  at least one non-empty acceptance check (plan §25's invariant); an empty
  tuple is refused at construction, so a task nobody can objectively accept
  can never be routed.
* **The fan-out is bounded.** ``MAX_MICROTASKS`` (16) is a *bounded minimum
  useful set*, not unbounded fan-out: the plan says create only as many
  microtasks as add value, and tiny requests stay single-step.
* **The dependency graph is acyclic and resolvable.** Every ``depends_on``
  must name another microtask in the set (a self-dependency is refused),
  and a cycle is refused *by name* so the failure is diagnosable rather
  than a hang later.
* **The model seam is injected, never imported, and failure is disclosed.**
  :func:`decompose_model` receives a ``generate`` callable from the caller
  (never imported here, never called at import time) and parses its JSON
  defensively: bounded output length, strict keys, the SAME structural
  validation the heuristic path gets. A failed or absent generation raises
  :class:`DecompositionError` — it never silently falls back to the
  heuristic, because who decides to fall back is the caller's decision
  (:func:`try_decompose` is the one caller that does, and it stamps the
  fallback into ``notes`` so the disclosure travels with the result).

Both :func:`decompose_heuristic` and the model path are baselines: the
heuristic is crude keyword/capability-slot splitting by design, and the
model path is a prompt seam with no model of its own.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from alpha.capability_mesh.fingerprint import SituationFingerprint

#: The bounded microtask count: a decomposition may not fan out past this.
#: 16 is a deliberately small ceiling — the plan asks for the *minimum useful
#: set*, and an unbounded decomposer turns every request into a fan-out cost
#: problem before anyone has measured whether the split helped.
MAX_MICROTASKS = 16

#: Summaries carry the deliverable, not the prompt: bounded at this many chars.
MAX_SUMMARY_CHARS = 2000

#: Titles are short labels: bounded at this many chars.
MAX_TITLE_CHARS = 200

#: The bounded length of model-decomposition output accepted for parsing.
#: A longer reply is refused before ``json.loads`` ever runs.
MAX_MODEL_OUTPUT_CHARS = 20000


class DecompositionError(ValueError):
    """The model-driven decomposition seam refused its input or its output.

    Raised by :func:`decompose_model` (and therefore by :func:`try_decompose`
    only after it has decided to fall back). It always names the cause, so a
    caller that chooses to fall back can carry the reason with it. It is a
    ``ValueError`` subclass because every refusal in this package is a
    refusal of a bad value.
    """


@dataclass(frozen=True)
class Microtask:
    """One named operation with one deliverable and objective acceptance criteria.

    ``acceptance_checks`` is REQUIRED non-empty: "no assignment without
    explicit acceptance criteria" is a plan invariant (§25), so a microtask
    nobody can objectively accept is refused at the door rather than routed
    to somebody's judgement. ``depends_on`` is validated against the full set
    by :class:`Decomposition` — one microtask cannot know its siblings.
    ``max_attempts`` is a bound, refused below 1 rather than clamped: an
    attempt budget of zero means "may not run", which is a refusal, not a 1.
    """

    microtask_id: str
    title: str
    summary: str
    acceptance_checks: tuple[str, ...]
    required_capabilities: tuple[str, ...] = ()
    required_tools: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    max_attempts: int = 1

    def __post_init__(self) -> None:
        for field in ("microtask_id", "title", "summary"):
            if not getattr(self, field):
                raise ValueError(f"{field} must be non-empty")
        if len(self.title) > MAX_TITLE_CHARS:
            raise ValueError(f"title must be at most {MAX_TITLE_CHARS} chars, got {len(self.title)}")
        if len(self.summary) > MAX_SUMMARY_CHARS:
            raise ValueError(f"summary must be at most {MAX_SUMMARY_CHARS} chars, got {len(self.summary)}")

        checks = tuple(self.acceptance_checks)
        if not checks:
            raise ValueError("acceptance_checks must be non-empty: no assignment without explicit acceptance criteria")
        for check in checks:
            if not check:
                raise ValueError("acceptance_checks entries must be non-empty strings")
        object.__setattr__(self, "acceptance_checks", checks)

        for field in ("required_capabilities", "required_tools", "depends_on"):
            entries = tuple(getattr(self, field))
            for entry in entries:
                if not entry:
                    raise ValueError(f"{field} entries must be non-empty strings")
            object.__setattr__(self, field, entries)

        if isinstance(self.max_attempts, bool) or not isinstance(self.max_attempts, int) or self.max_attempts < 1:
            raise ValueError(f"max_attempts must be an int >= 1, got {self.max_attempts!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "microtask_id": self.microtask_id,
            "title": self.title,
            "summary": self.summary,
            "required_capabilities": list(self.required_capabilities),
            "required_tools": list(self.required_tools),
            "acceptance_checks": list(self.acceptance_checks),
            "depends_on": list(self.depends_on),
            "max_attempts": self.max_attempts,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Microtask:
        # The constructor re-validates, so hand-edited or model-generated
        # JSON cannot bypass the honesty rules by skipping __post_init__.
        return cls(
            microtask_id=str(data["microtask_id"]),
            title=str(data["title"]),
            summary=str(data["summary"]),
            required_capabilities=tuple(str(name) for name in data.get("required_capabilities", ())),
            required_tools=tuple(str(name) for name in data.get("required_tools", ())),
            acceptance_checks=tuple(str(check) for check in data["acceptance_checks"]),
            depends_on=tuple(str(name) for name in data.get("depends_on", ())),
            max_attempts=int(data.get("max_attempts", 1)),
        )


def _assert_acyclic(microtasks: tuple[Microtask, ...]) -> None:
    """Refuse a dependency cycle by name (Kahn peel; the leftovers ARE the cycle).

    A node is peelable once none of its dependencies point at another
    *remaining* node. Self-dependencies were already refused by
    :class:`Microtask`/``Decomposition`` before this runs, so anything the
    peel cannot remove is a genuine cycle — named in the refusal so the
    diagnostic reaches the caller instead of a hang later.
    """
    remaining = {task.microtask_id: set(task.depends_on) for task in microtasks}
    progressed = True
    while progressed:
        progressed = False
        for task_id in sorted(task_id for task_id, deps in remaining.items() if not (deps & (set(remaining) - {task_id}))):
            del remaining[task_id]
            progressed = True
    if remaining:
        raise ValueError(f"dependency cycle among microtasks: {', '.join(sorted(remaining))} (a dependency graph must be acyclic)")


@dataclass(frozen=True)
class Decomposition:
    """The bounded, acyclic microtask set one task decomposes into (plan §3.2).

    ``decomposer`` names how this decomposition was produced
    (``"heuristic_baseline"`` or ``"model"``) and ``notes`` carries honest
    disclosures — a fallback, a limitation, a truncation. Both travel with
    the result so a later reader can always tell a crude split from a
    model-assisted one.
    """

    fingerprint: SituationFingerprint
    microtasks: tuple[Microtask, ...]
    decomposer: str
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.fingerprint, SituationFingerprint):
            raise ValueError(f"fingerprint must be a SituationFingerprint, got {self.fingerprint!r}")
        microtasks = tuple(self.microtasks)
        for task in microtasks:
            if not isinstance(task, Microtask):
                raise ValueError(f"microtasks must contain Microtask rows, got {task!r}")
        object.__setattr__(self, "microtasks", microtasks)
        if not microtasks:
            raise ValueError("microtasks must be non-empty: a decomposition that lists nothing drops the whole task")
        if len(microtasks) > MAX_MICROTASKS:
            raise ValueError(f"microtasks must be at most {MAX_MICROTASKS}, got {len(microtasks)} (bounded fan-out, never unbounded)")
        if not self.decomposer:
            raise ValueError("decomposer must be non-empty: a decomposition must always name how it was produced")
        notes = tuple(self.notes)
        for note in notes:
            if not note:
                raise ValueError("notes entries must be non-empty strings")
        object.__setattr__(self, "notes", notes)

        ids: set[str] = set()
        for task in microtasks:
            if task.microtask_id in ids:
                raise ValueError(f"duplicate microtask_id {task.microtask_id!r}: ids identify one microtask and may not repeat")
            ids.add(task.microtask_id)
        for task in microtasks:
            for dependency in task.depends_on:
                if dependency not in ids:
                    raise ValueError(f"microtask {task.microtask_id!r} depends on unknown microtask {dependency!r}")
                if dependency == task.microtask_id:
                    raise ValueError(f"microtask {task.microtask_id!r} depends on itself: depends_on must name ANOTHER microtask")
        _assert_acyclic(microtasks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint.to_dict(),
            "microtasks": [task.to_dict() for task in self.microtasks],
            "decomposer": self.decomposer,
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Decomposition:
        return cls(
            fingerprint=SituationFingerprint.from_dict(data["fingerprint"]),
            microtasks=tuple(Microtask.from_dict(entry) for entry in data["microtasks"]),
            decomposer=str(data["decomposer"]),
            notes=tuple(str(note) for note in data.get("notes", ())),
        )


#: The honest disclosure carried by every heuristic decomposition: keyword
#: slot-splitting is a baseline, never intelligence.
HEURISTIC_NOTES = ("heuristic baseline decomposition: keyword/capability-slot rules, crude by design, not model intelligence",)

_WHOLE_TASK_TITLE = "complete the whole task"
_WHOLE_TASK_CHECK = "the requested deliverable is produced and matches the request"
_MODEL_INSTRUCTION = (
    "Decompose the task below into 1 to 16 microtasks. Reply with ONLY a JSON object, no prose and no code fences, shaped exactly as:\n"
    '{"microtasks": [{"microtask_id": "mt-1", "title": "short title", "summary": "the deliverable (max 2000 chars)", '
    '"required_capabilities": ["capability"], "required_tools": ["tool"], "acceptance_checks": ["at least one objective check"], '
    '"depends_on": [], "max_attempts": 1}]}\n'
    "Every microtask needs at least one non-empty acceptance check; depends_on must name other microtask_ids and may not form a cycle."
)


def decompose_heuristic(fingerprint: SituationFingerprint, task_text: str) -> Decomposition:
    """Deterministic rule-based baseline: split by declared capability slots (plan §3.2).

    **Crude by design.** If the fingerprint declares ``required_capabilities``
    the task splits into one microtask per capability (``mt-1``..``mt-n``),
    each with an acceptance check referencing that capability; otherwise the
    task stays a single whole-task microtask, because a tiny request must not
    be split for the sake of splitting (plan §3.2). The fingerprint's
    ``required_tools`` travel onto every microtask: a situation fingerprint
    cannot attribute a tool to one slice, so the requirement conservatively
    gates every slice — a hard gate that over-refuses is safe; one that
    under-refuses is not. No dependency edges are invented between slices.
    """
    if not task_text:
        raise ValueError("decompose_heuristic requires non-empty task_text")
    tools = fingerprint.required_tools
    capabilities = fingerprint.required_capabilities
    if capabilities:
        tasks = tuple(
            Microtask(
                microtask_id=f"mt-{index}",
                title=capability,
                summary=f"Deliver the '{capability}' slice of the task.",
                required_capabilities=(capability,),
                required_tools=tools,
                acceptance_checks=(f"the '{capability}' requirement is met with explicit evidence",),
            )
            for index, capability in enumerate(capabilities, start=1)
        )
    else:
        tasks = (
            Microtask(
                microtask_id="mt-1",
                title=_WHOLE_TASK_TITLE,
                summary=task_text,
                required_tools=tools,
                acceptance_checks=(_WHOLE_TASK_CHECK,),
            ),
        )
    return Decomposition(fingerprint=fingerprint, microtasks=tasks, decomposer="heuristic_baseline", notes=HEURISTIC_NOTES)


_MICROTASK_JSON_KEYS = {
    "microtask_id",
    "title",
    "summary",
    "required_capabilities",
    "required_tools",
    "acceptance_checks",
    "depends_on",
    "max_attempts",
}


def decompose_model(fingerprint: SituationFingerprint, task_text: str, *, generate: Callable[[str], str | None]) -> Decomposition:
    """Model-driven decomposition through an INJECTED generation seam (plan §3.2).

    ``generate`` is a caller-supplied callable mapping one strict-JSON
    instruction (carrying ``task_text``) to the model's raw reply. It is
    never imported and never called at import time: this module has no model
    of its own, and wiring one is the caller's decision.

    The reply is parsed **defensively**: bounded at ``MAX_MODEL_OUTPUT_CHARS``
    before parsing, strict JSON (no fence-stripping — a fenced reply is
    non-JSON and refused), exactly the ``{"microtasks": [...]}`` shape, no
    unknown keys anywhere, no empty list, at most ``MAX_MICROTASKS`` rows,
    then the SAME structural validation the heuristic path gets (ids,
    acceptance criteria, dependencies, acyclicity). Every failure raises
    :class:`DecompositionError` naming the cause.

    **A failed or absent generation is never silently swapped for the
    heuristic here.** A ``None``/non-string reply or a generation exception
    raises; whether to fall back is the caller's call, and the one caller
    that does (:func:`try_decompose`) stamps the fallback into the result's
    ``notes`` so the disclosure travels with the decomposition.
    """
    if not task_text:
        raise ValueError("decompose_model requires non-empty task_text")
    try:
        generated = generate(f"{_MODEL_INSTRUCTION}\n\nTask:\n{task_text}")
    except Exception as error:  # noqa: BLE001 - a seam failure is disclosed here, never re-raised raw
        # The generator's own exception is wrapped so the refusal still carries a
        # cause, AND so `try_decompose` can actually fall back: catching only
        # DecompositionError there would let a transport/provider error escape
        # instead of being disclosed as a fallback the caller chose.
        raise DecompositionError(f"the decomposition generator raised {type(error).__name__}: {error}") from error
    if generated is None:
        raise DecompositionError("the decomposition generator returned no output (None): falling back is the caller's decision, never this function's")
    if not isinstance(generated, str):
        raise DecompositionError(f"the decomposition generator returned {type(generated).__name__}, expected a JSON string or None")
    if len(generated) > MAX_MODEL_OUTPUT_CHARS:
        raise DecompositionError(f"model decomposition output is {len(generated)} chars, over the {MAX_MODEL_OUTPUT_CHARS}-char bound: parsing is bounded, never unbounded")
    try:
        document = json.loads(generated)
    except json.JSONDecodeError as error:
        raise DecompositionError(f"model decomposition is not valid JSON: {error}") from error
    if not isinstance(document, dict):
        raise DecompositionError(f"model decomposition must be a JSON object with a 'microtasks' array, got {type(document).__name__}")
    unknown_top = sorted(set(document) - {"microtasks"})
    if unknown_top:
        raise DecompositionError(f"unknown key(s) in model decomposition: {', '.join(unknown_top)}")
    if "microtasks" not in document:
        raise DecompositionError("model decomposition is missing the required 'microtasks' key")
    rows = document["microtasks"]
    if not isinstance(rows, list):
        raise DecompositionError(f"'microtasks' must be an array, got {type(rows).__name__}")
    if not rows:
        raise DecompositionError("model decomposition contains no microtasks: a task may not be dropped")
    if len(rows) > MAX_MICROTASKS:
        raise DecompositionError(f"model decomposition lists {len(rows)} microtasks, over the MAX_MICROTASKS bound of {MAX_MICROTASKS}")
    tasks: list[Microtask] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise DecompositionError(f"microtask {index} must be a JSON object, got {type(row).__name__}")
        unknown = sorted(set(row) - _MICROTASK_JSON_KEYS)
        if unknown:
            raise DecompositionError(f"microtask {index} has unknown key(s): {', '.join(unknown)}")
        try:
            tasks.append(Microtask.from_dict(row))
        except KeyError as error:
            raise DecompositionError(f"microtask {index} is missing the required key {error}") from error
        except (TypeError, ValueError) as error:
            raise DecompositionError(f"microtask {index} rejected: {error}") from error
    try:
        return Decomposition(fingerprint=fingerprint, microtasks=tuple(tasks), decomposer="model", notes=())
    except ValueError as error:
        raise DecompositionError(f"model decomposition failed validation: {error}") from error


def try_decompose(fingerprint: SituationFingerprint, task_text: str, *, generate: Callable[[str], str | None] | None = None) -> Decomposition:
    """Decompose with the model seam when available, falling back with disclosure.

    When ``generate`` is ``None`` (nothing wired) or raises
    :class:`DecompositionError` (model unavailable, reply unparseable,
    validation refused), the heuristic baseline runs and the result carries
    ``decomposer="heuristic_baseline"`` plus the note "model decomposition
    unavailable; heuristic baseline used" — and, on failure, the cause. The
    fallback is *always* disclosed; a reader must never have to guess which
    decomposer produced a decomposition.
    """
    if generate is not None:
        try:
            return decompose_model(fingerprint, task_text, generate=generate)
        except DecompositionError as error:
            fallback_note = ("model decomposition unavailable; heuristic baseline used", f"model decomposition failed: {error}")
            return replace(decompose_heuristic(fingerprint, task_text), notes=(*fallback_note, *HEURISTIC_NOTES))
    return replace(decompose_heuristic(fingerprint, task_text), notes=("model decomposition unavailable; heuristic baseline used", *HEURISTIC_NOTES))


__all__ = [
    "HEURISTIC_NOTES",
    "MAX_MICROTASKS",
    "MAX_MODEL_OUTPUT_CHARS",
    "MAX_SUMMARY_CHARS",
    "MAX_TITLE_CHARS",
    "Decomposition",
    "DecompositionError",
    "Microtask",
    "decompose_heuristic",
    "decompose_model",
    "try_decompose",
]
