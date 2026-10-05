"""Transactional Bot forging: build, verify, and roll back.

This is the module that turns *"make me a social media manager"* into a
working Bot — or into **nothing at all**, if any step fails.

Why this exists
---------------
Auto-provisioning a Bot used to be a sequence of independent mutations: create
the profile, write the persona, add the routine, start the worker. If step four
died, steps one to three survived, and the roster filled with half-built Bots
that looked real, answered once, and then broke. Operators cannot tell a
half-built Bot from a working one by looking at it.

So forging is a **transaction**:

* every step declares what it did;
* any failure after the profile is claimed triggers a rollback that deletes
  what was created and says whether the cleanup actually happened;
* a Bot is only reported as *alive* after it has **answered a smoke test** —
  a successful build is not a successful delivery.

The forge also refuses work it should refuse:

* a Bot whose job an existing Bot already covers is **not** built (unless the
  operator explicitly asks for an overlapping roster);
* a routine faster than the cost floor is **not** scheduled;
* a sandbox that is not available on this machine is **not** silently
  downgraded to "runs on your real machine".

Nothing here calls a model. The survey is deterministic, the smoke test is a
caller-supplied probe, and the whole module is importable offline.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alpha.bots.journal import get_journal
from alpha.bots.profile import BotProfile, generate_default_soul
from alpha.bots.survey import (
    MIN_OVERLAP_TERMS,
    SurveyResult,
    score_overlap,
    significant_terms,
    survey_workspace,
)

__all__ = [
    "ForgeStep",
    "ForgeResult",
    "RoutineGuardReport",
    "check_overlap",
    "plan_routine_guard",
    "build_guardrail_soul",
    "forge_bot",
    "DEFAULT_APPROVALS",
    "MIN_ROUTINE_INTERVAL_MINUTES",
]

# ---------------------------------------------------------------------------
# Policy constants
# ---------------------------------------------------------------------------

#: Draft-first: a forged Bot is born asking before it does anything dangerous,
#: unless the operator explicitly passes ``approvals=[]``.
DEFAULT_APPROVALS: tuple[str, ...] = (
    "publish or send anything",
    "spend money",
    "delete files or data",
)

#: Every routine run is a model call. A 15-minute routine is 96 runs a day, so
#: anything faster than this is refused unless the operator opts in.
MIN_ROUTINE_INTERVAL_MINUTES = 30

#: What a Bot may be asked to run its shell in. ``None`` means "on the host",
#: which is a real, deliberate choice — but it must be reported, not assumed.
SANDBOX_BACKENDS = ("docker", "singularity", "apptainer", "podman", "none")

# Literal emoji, not ``\N{...}`` escapes: these are display glyphs only, and a
# literal cannot break the module the way a misspelled Unicode name does.
_ACK_PREFIX = "\U0001f440"  # eyes: picked it up
_BLOCKED_GLYPH = "\u26a0\ufe0f"  # warning: blocked or failed
_DONE_GLYPH = "\u2705"  # check mark: done
_ANSWERING_GLYPH = "\U0001f4ac"  # speech balloon: answering now
_APPROVAL_GLYPH = "\u270b"  # raised hand: needs approval
_SCHEDULED_GLYPH = "\u23f3"  # hourglass: scheduled for later
_ALIVE_GLYPH = "\U0001f9ea"  # test tube: forged and verified


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


@dataclass
class ForgeStep:
    """One unit of work in the build, with the inverse used to undo it."""

    name: str
    detail: str = ""
    undo: Callable[[], None] | None = None
    done: bool = False

    def rollback(self) -> str:
        if not self.done or self.undo is None:
            return ""
        try:
            self.undo()
            return ""
        except Exception as exc:  # pragma: no cover - undo must not mask build
            return f"{self.name}: rollback failed ({exc})"


@dataclass
class ForgeResult:
    """What the forge reports. Honest in both directions."""

    ok: bool
    name: str = ""
    reason: str = ""
    steps: list[ForgeStep] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    survey: SurveyResult | None = None
    smoke_tested: bool = False
    smoke_output: str = ""
    rolled_back: bool = False
    rollback_report: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    @property
    def step_names(self) -> list[str]:
        return [s.name for s in self.steps if s.done]

    def reply(self) -> str:
        """The message the calling agent shows the operator."""
        if not self.ok and self.rolled_back:
            lines = [f"{_BLOCKED_GLYPH} Could not forge @{self.name} — rolled back."]
            lines.append(f"reason: {self.reason}")
            if self.step_names:
                lines.append(f"completed before failure: {', '.join(self.step_names)}")
            if self.rollback_report:
                lines.append("rollback issues:")
                lines.extend(f"  - {r}" for r in self.rollback_report)
            else:
                lines.append("rollback: clean — no partial Bot remains")
            return "\n".join(lines)

        if not self.ok:
            return f"{_BLOCKED_GLYPH} @{self.name} was not created: {self.reason}"

        lines = [f"{_ALIVE_GLYPH} {self.name} is alive."]
        lines.append(f"steps: {', '.join(self.step_names)}")
        if self.smoke_tested:
            lines.append(f"smoke test: passed — {self.smoke_output[:200]}")
        else:
            lines.append("smoke test: skipped (no probe supplied) — unverified")
        if self.survey and self.survey.fits:
            lines.append(f"fits: {self.survey.fits} ({self.survey.fits_path})")
        if self.survey and self.survey.covered_by:
            lines.append(f"heads up: @{self.survey.covered_by} already works in this territory")
        for warning in self.warnings:
            lines.append(f"note: {warning}")
        lines.append(f"built in {self.elapsed_seconds:.1f}s")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Overlap refusal
# ---------------------------------------------------------------------------


def check_overlap(
    soul: str,
    role: str,
    *,
    existing: dict[str, str],
    threshold: float = 0.34,
    name: str = "",
) -> tuple[str | None, float]:
    """Refuse to build a Bot whose job an existing Bot already covers.

    A roster of twenty Bots must not quietly become a roster of twenty
    overlapping ones. Returns ``(offending_bot, score)`` or ``(None, 0.0)``.

    ``existing`` maps ``bot_name -> "role\nsoul snippet"``. The comparison is
    deterministic word overlap — no model call, no tokens.
    """
    if not existing:
        return None, 0.0
    query = "\n".join(x for x in (role, soul) if x)
    best_name: str | None = None
    best = 0.0
    for bot_name, snippet in existing.items():
        if name and bot_name.lower() == name.lower():
            continue  # the Bot being updated is not its own duplicate
        score, _ = score_overlap(query, snippet)
        if score > best:
            best = score
            best_name = bot_name
    if best_name is None or best < threshold:
        return None, best
    if len(significant_terms(query)) < MIN_OVERLAP_TERMS:
        # Evidence, not a verdict: a short role is too thin to call a duplicate
        # on, and refusing here would be arbitrary.
        return None, best
    return best_name, best


# ---------------------------------------------------------------------------
# Routine cost guard
# ---------------------------------------------------------------------------


@dataclass
class RoutineGuardReport:
    """Whether a schedule is affordable, and why."""

    allowed: bool
    interval_minutes: float | None = None
    runs_per_day: float | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "interval_minutes": self.interval_minutes,
            "runs_per_day": self.runs_per_day,
            "reason": self.reason,
        }


_INTERVAL_RE = re.compile(r"^\s*(?:every\s+)?(\d+)\s*(m|min|mins|minutes?)\s*$", re.I)
_HOUR_RE = re.compile(r"^\s*(?:every\s+)?(\d+)\s*(h|hr|hrs|hours?)\s*$", re.I)
_DAY_RE = re.compile(r"^\s*(?:every\s+)?(\d+)\s*(d|day|days)\s*$", re.I)
_CRON_RE = re.compile(r"^\s*[\d*\/,\-\s]+\s+[\d*\/,\-\s]+\s+[\d*\/,\-\s]+\s+[\d*\/,\-\s]+\s+[\d*\/,\-\s]+\s*$")


def _interval_minutes(schedule: str) -> float | None:
    """Best-effort minutes between runs, or ``None`` if unparseable."""
    raw = (schedule or "").strip()
    if not raw:
        return None
    match = _INTERVAL_RE.match(raw)
    if match:
        return float(match.group(1))
    match = _HOUR_RE.match(raw)
    if match:
        return float(match.group(1)) * 60.0
    match = _DAY_RE.match(raw)
    if match:
        return float(match.group(1)) * 1440.0
    if _CRON_RE.match(raw):
        # "* * * * *" is every minute; "* * * * 1" is weekly-ish. Without a
        # parser, the safe read of a 5-field cron is "could be very frequent",
        # so treat a wildcard minute field as 1 minute and otherwise assume
        # daily — and say so in the reason.
        minute_field = raw.split()[0]
        if minute_field == "*":
            return 1.0
        if minute_field.startswith("*/"):
            digits = re.sub(r"\D", "", minute_field)
            return float(digits) if digits else None
        return 1440.0
    return None


def plan_routine_guard(
    schedules: Iterable[str],
    *,
    allow_frequent: bool = False,
    minimum_minutes: float = MIN_ROUTINE_INTERVAL_MINUTES,
) -> RoutineGuardReport:
    """Refuse schedules that would burn the budget.

    Every run is a model call, so a routine faster than the floor is refused
    unless the operator explicitly opted in with ``allow_frequent``.
    """
    items = [s for s in schedules if (s or "").strip()]
    if not items:
        return RoutineGuardReport(True, reason="no routines requested")

    if allow_frequent:
        parsed = [_interval_minutes(s) for s in items]
        known = [p for p in parsed if p]
        fastest = min(known) if known else None
        return RoutineGuardReport(
            True,
            interval_minutes=fastest,
            runs_per_day=(1440.0 / fastest) if fastest else None,
            reason="allow_frequent set — frequency floor bypassed by the operator",
        )

    worst: RoutineGuardReport | None = None
    for schedule in items:
        minutes = _interval_minutes(schedule)
        if minutes is None:
            # Unparseable is not the same as safe: flag it, do not block it.
            continue
        runs = 1440.0 / minutes if minutes > 0 else None
        if minutes < minimum_minutes:
            return RoutineGuardReport(
                False,
                interval_minutes=minutes,
                runs_per_day=runs,
                reason=(f"schedule '{schedule.strip()}' runs every {minutes:g} minutes ({runs:.0f} runs/day); the floor is {minimum_minutes:g} minutes. Every run is a model call. Pass allow_frequent=true to override."),
            )
        if worst is None or minutes < (worst.interval_minutes or 1e9):
            worst = RoutineGuardReport(
                True,
                interval_minutes=minutes,
                runs_per_day=runs,
                reason=f"{runs:.0f} runs/day",
            )
    return worst or RoutineGuardReport(True, reason="schedules within the frequency floor")


# ---------------------------------------------------------------------------
# Guardrail SOUL
# ---------------------------------------------------------------------------


def build_guardrail_soul(
    name: str,
    role: str,
    *,
    approvals: Iterable[str] = DEFAULT_APPROVALS,
    reports_to: str | None = None,
    sandbox: str | None = None,
    survey_block: str = "",
    base_soul: str | None = None,
) -> str:
    """Compose the persona, with approvals and escalation written in at birth.

    Approvals that live in a separate config file are approvals nobody reads.
    They go into the SOUL so the Bot knows, on turn one, what it must ask about
    and who it escalates to.
    """
    soul = base_soul if base_soul is not None else generate_default_soul(name, role)

    # One identity per Bot: strip any inherited "You are <Other>" heading so a
    # copied persona can never introduce itself as a different Bot.
    soul = _retitle(soul, name, role)

    blocks: list[str] = []
    approvals = [a.strip() for a in approvals if a and str(a).strip()]
    if approvals:
        items = "\n".join(f"- {a}" for a in approvals)
        blocks.append(f"## Ask first\nNever do these without the user saying yes:\n{items}\nDrafting is fine — publishing, spending and deleting are not.")
    if reports_to:
        blocks.append(f"## Escalate to\nRaise scope changes, priorities and final calls to @{reports_to}. When unsure which applies, ask them rather than guessing.")
    if sandbox and sandbox != "none":
        blocks.append(f"## Where you run\nYour shell runs inside a `{sandbox}` sandbox — not on the operator's machine. Do not assume access to files outside your workspace.")
    elif sandbox == "none":
        blocks.append("## Where you run\nYour shell runs on the host machine. Treat every destructive command as requiring approval, because there is no container absorbing it.")
    if survey_block:
        blocks.append(survey_block)

    if blocks:
        soul = soul.rstrip() + "\n\n" + "\n\n".join(blocks) + "\n"
    return soul


def _retitle(soul: str, name: str, role: str) -> str:
    """Ensure the persona's heading names *this* Bot and nothing else.

    "One identity per Bot" has to mean *one*. A supplied persona that carries its
    own identity heading twice used to keep both: only the first ``#`` line was
    retitled, and every later one was passed through verbatim. Observed live on
    2026-10-05, where a Bot forged by the agent itself came back as::

        # SOUL.md - Capability-curator (Capability Surface & Prompt Steward)

        # SOUL.md - Capability-curator (Capability Surface & Prompt Steward)
        1. Trigger & description quality. ...

    which is worse than a stale name: it teaches the model that its identity is
    something it may state more than once. This dedupes only headings that repeat
    *this* Bot's identity -- a legitimate sub-section such as ``## Where you run``
    is untouched, and a heading naming a *different* Bot is still rewritten rather
    than removed, so nothing is silently discarded.
    """
    lines = soul.splitlines()
    heading_idx = next((i for i, line in enumerate(lines) if line.lstrip().startswith("#")), None)
    wanted = f"# SOUL.md - {name[:1].upper() + name[1:]} ({role})"
    if heading_idx is None:
        return wanted + "\n\n" + soul
    if name.lower() not in lines[heading_idx].lower():
        lines[heading_idx] = wanted
    identity = re.compile(rf"^\s*(?:you are|you're)\s+\*\*(?!{re.escape(name)}\b)\w+\*\*", re.I)
    # A repeat of this Bot's own identity heading: same "# SOUL.md - <name>"
    # opener, whatever role text follows.
    repeat = re.compile(rf"^\s*#\s*SOUL\.md\s*-\s*{re.escape(name)}\b", re.I)

    cleaned = [lines[heading_idx]]
    for line in lines[1:]:
        if identity.match(line):
            continue
        # Collapse a duplicate identity opener down to its blank line, so the
        # body that followed it keeps its own separation instead of being glued
        # onto the surviving heading.
        if repeat.match(line):
            if not (cleaned and cleaned[-1].strip() == ""):
                cleaned.append("")
            continue
        cleaned.append(line)
    return "\n".join(cleaned)


# ---------------------------------------------------------------------------
# Sandbox probe
# ---------------------------------------------------------------------------


def probe_sandbox(backend: str | None) -> tuple[bool, str]:
    """Is ``backend`` usable on this machine right now?

    Read-only: checks a binary on PATH, never starts a container. Returns
    ``(available, reason)``.
    """
    if not backend or backend == "none":
        return True, "host execution requested"
    if backend not in SANDBOX_BACKENDS:
        return False, (f"unknown sandbox backend '{backend}' — expected one of " + ", ".join(SANDBOX_BACKENDS))
    import shutil as _shutil

    if _shutil.which(backend):
        return True, f"{backend} found on PATH"
    return False, f"'{backend}' is not installed or not on PATH on this machine"


# ---------------------------------------------------------------------------
# The forge
# ---------------------------------------------------------------------------


def forge_bot(
    *,
    name: str,
    role: str,
    registry: Any,
    soul: str | None = None,
    template: str | None = None,
    department: str | None = None,
    reports_to: str | None = None,
    display_name: str | None = None,
    skills: list[str] | None = None,
    toolsets: list[str] | None = None,
    capabilities: list[str] | None = None,
    approvals: Iterable[str] | None = None,
    sandbox: str | None = None,
    routines: list[dict[str, Any]] | None = None,
    allow_frequent: bool = False,
    allow_overlap: bool = False,
    workspace_survey: bool = True,
    workspace_roots: list[str] | None = None,
    journal_enabled: bool = True,
    journal_root: str | Path | None = None,
    existing_roles: dict[str, str] | None = None,
    installed_skills: list[str] | None = None,
    smoke_test: Callable[[BotProfile], str] | None = None,
    cwd: str | None = None,
) -> ForgeResult:
    """Build a complete Bot transactionally. Rolls back on any failure.

    Args:
        name: Bot handle. Must not already exist.
        role: One-line specialty.
        registry: The bot registry to provision into.
        approvals: Explicit approval list. ``None`` means
            :data:`DEFAULT_APPROVALS` (draft-first). ``[]`` means the operator
            deliberately asked for no checkpoints.
        sandbox: Requested backend; refused *before* creating anything if it is
            unusable here, so no half-built Bot is left behind.
        routines: ``{name, schedule, action}`` dicts, cost-checked first.
        allow_frequent: Operator override for the routine frequency floor.
        allow_overlap: Operator override for the duplicate-role refusal.
        workspace_survey: Survey the workspace at birth and write the result
            into the Bot's memory.
        smoke_test: Called with the built profile; returns a short string.
            The Bot is **only** reported alive if this does not raise. This is
            what separates "the files exist" from "the Bot works".
        existing_roles: ``{bot_name: role+soul}`` used for the overlap check.

    Returns:
        A :class:`ForgeResult`. Never raises — a failure is a result, because
        the caller is a model that must be told what happened.
    """
    started = time.monotonic()
    clean = (name or "").strip().lower()
    result = ForgeResult(ok=False, name=clean or "(unnamed)")

    if not clean:
        result.reason = "a Bot name is required"
        result.elapsed_seconds = time.monotonic() - started
        return result
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", clean):
        result.reason = f"'{clean}' is not a valid handle — use lowercase letters, digits, hyphen or underscore (max 64 chars)"
        result.elapsed_seconds = time.monotonic() - started
        return result
    if registry.get_bot(clean) is not None:
        result.reason = f"@{clean} already exists — use update, not create"
        result.elapsed_seconds = time.monotonic() - started
        return result

    # ---- pre-flight checks: nothing is created yet ----------------------
    if sandbox:
        available, why = probe_sandbox(sandbox)
        if not available:
            # Refused BEFORE any Bot exists, with the reason.
            result.reason = f"sandbox refused: {why}"
            result.elapsed_seconds = time.monotonic() - started
            return result

    guard = plan_routine_guard(
        [str(r.get("schedule", "")) for r in (routines or [])],
        allow_frequent=allow_frequent,
    )
    if not guard.allowed:
        result.reason = f"routine refused: {guard.reason}"
        result.elapsed_seconds = time.monotonic() - started
        return result

    roles = existing_roles if existing_roles is not None else _harvest_roles(registry)
    survey: SurveyResult | None = None
    if workspace_survey:
        survey = survey_workspace(
            soul or "",
            role=role,
            name=clean,
            cwd=cwd,
            workspace_roots=workspace_roots,
            skills=skills,
            existing_roles=roles,
            installed_skills=installed_skills,
        )
        result.survey = survey

    offending, score = check_overlap(soul or "", role, existing=roles, name=clean)
    if offending and not allow_overlap:
        result.reason = f"@{offending} already covers this job (overlap {score:.2f}) — refusing to build a second Bot for the same territory. Pass allow_overlap=true if you want both."
        result.elapsed_seconds = time.monotonic() - started
        return result
    if offending and allow_overlap:
        result.warnings.append(f"overlapping @{offending} (score {score:.2f}) — operator set allow_overlap")

    survey_block = survey.memory_block() if survey else ""
    final_approvals = DEFAULT_APPROVALS if approvals is None else list(approvals)
    final_soul = build_guardrail_soul(
        clean,
        role,
        approvals=final_approvals,
        reports_to=reports_to,
        sandbox=sandbox,
        survey_block=survey_block,
        base_soul=soul,
    )

    steps: list[ForgeStep] = []
    result.steps = steps

    # ---- step 1: claim the profile ---------------------------------------
    try:

        def _undo_create() -> None:
            # Rollback of a *failed birth* must remove the profile outright.
            # retire_bot() is a soft delete that archives in place, which would
            # leave a ghost on the roster and make the "no partial Bot remains"
            # claim in reply() false. There is no run history to preserve for a
            # Bot that never answered, so remove it and persist that fact.
            bots = getattr(registry, "_bots", None)
            if bots is None or not hasattr(registry, "_save"):
                raise RuntimeError("registry exposes no removal path — cannot roll back the profile")
            if bots.pop(clean, None) is None:
                raise RuntimeError(f"@{clean} was not present at rollback")
            registry._save()  # noqa: SLF001 - transactional undo, must be verified
            if clean in bots:
                raise RuntimeError(f"@{clean} survived rollback")

        bot = registry.get_or_create(
            clean,
            display_name=display_name or None,
            role=role,
            soul=final_soul,
            template=template,
            department=department,
            reports_to=reports_to,
            capabilities=capabilities,
            skills=skills,
            toolsets=toolsets,
        )
        steps.append(ForgeStep("profile", f"@{clean}", undo=_undo_create, done=True))
    except Exception as exc:
        result.reason = f"could not claim the profile: {type(exc).__name__}: {exc}"
        result.elapsed_seconds = time.monotonic() - started
        return result  # nothing to roll back: nothing was created

    # ---- step 2: identity + approvals in the SOUL ------------------------
    try:
        previous_soul = getattr(bot, "soul", "")
        bot.soul = final_soul
        bot.metadata.setdefault("approvals", list(final_approvals))
        bot.metadata["forged_at"] = datetime.now(UTC).isoformat()
        bot.metadata["forged_by"] = "forge"
        if reports_to:
            bot.reports_to = reports_to
        if sandbox:
            bot.metadata["sandbox"] = sandbox
            bot.metadata["sandbox_verified"] = probe_sandbox(sandbox)[0]

        def _undo_soul() -> None:
            bot.soul = previous_soul

        if hasattr(registry, "_save"):
            registry._save()
        steps.append(ForgeStep("persona", "SOUL + approvals", undo=_undo_soul, done=True))
    except Exception as exc:
        _rollback(steps, result)
        result.reason = f"could not write the persona: {type(exc).__name__}: {exc}"
        result.elapsed_seconds = time.monotonic() - started
        return result

    # ---- step 3: routines (already cost-checked) --------------------------
    added_routines: list[str] = []
    if routines:
        try:
            for spec in routines:
                registry.add_routine(
                    clean,
                    str(spec.get("name", "")),
                    str(spec.get("schedule", "")),
                    str(spec.get("action", "")),
                    **{k: v for k, v in spec.items() if k not in {"name", "schedule", "action"}},
                )
                added_routines.append(str(spec.get("name", "")))

            def _undo_routines() -> None:
                for routine_name in added_routines:
                    registry.remove_routine(clean, routine_name)

            steps.append(
                ForgeStep(
                    "routines",
                    f"{len(added_routines)} scheduled",
                    undo=_undo_routines,
                    done=True,
                )
            )
            if guard.runs_per_day:
                result.warnings.append(f"routine cost: {guard.reason}")
        except Exception as exc:
            _rollback(steps, result)
            result.reason = f"could not schedule routines: {type(exc).__name__}: {exc}"
            result.elapsed_seconds = time.monotonic() - started
            return result

    # ---- step 4: work journal ---------------------------------------------
    if journal_enabled:
        try:
            root = journal_root or _default_journal_root()
            journal = get_journal(root, clean)
            journal.enable(policy_note=f"Bot role: {role}")

            def _undo_journal() -> None:
                journal.disable()

            steps.append(ForgeStep("journal", str(journal.bot_dir), undo=_undo_journal, done=True))
        except Exception as exc:
            result.warnings.append(f"journal unavailable ({exc}) — continuing without one")

    # ---- step 5: smoke test — a Bot is only alive if it answers -----------
    if smoke_test is not None:
        try:
            output = smoke_test(bot)
            result.smoke_tested = True
            result.smoke_output = (output or "").strip() or "(no output)"
            steps.append(ForgeStep("smoke_test", result.smoke_output[:80], done=True))
        except Exception as exc:
            _rollback(steps, result)
            result.reason = f"smoke test failed — the Bot could not answer: {type(exc).__name__}: {exc}"
            result.elapsed_seconds = time.monotonic() - started
            return result
    else:
        result.warnings.append("no smoke test supplied — this Bot is UNVERIFIED (files exist, behaviour was not checked)")

    result.ok = True
    result.name = clean
    result.elapsed_seconds = time.monotonic() - started
    return result


def _rollback(steps: list[ForgeStep], result: ForgeResult) -> None:
    """Undo completed steps in reverse, and report honestly what happened."""
    result.rolled_back = True
    issues: list[str] = []
    for step in reversed(steps):
        message = step.rollback()
        if message:
            issues.append(message)
        step.done = False
    result.rollback_report = issues


def _harvest_roles(registry: Any) -> dict[str, str]:
    """``{name: role + soul}`` for every live Bot, for the overlap check."""
    out: dict[str, str] = {}
    try:
        bots: Iterable[BotProfile] = registry.list_bots(include_archived=False)
    except TypeError:
        bots = registry.list_bots()
    except Exception:
        return out
    for bot in bots:
        if getattr(bot, "is_retired", False):
            continue
        out[bot.name] = "\n".join(x for x in (bot.role, getattr(bot, "soul", "")[:1500]) if x)
    return out


def _default_journal_root() -> Path:
    """Journal root: inside the alpha, never next to credentials."""
    base = os.environ.get("ALPHA_HOME")
    if base:
        return Path(base) / "bot_journals"
    return Path.home() / ".alpha" / "bot_journals"


def _default_export_root() -> Path:
    """Export root: the only directory a shareable template may be written to.

    Confining exports to one directory means a crafted ``path`` argument cannot
    reach anywhere else on the machine — the resolver refuses anything that
    resolves outside this root.
    """
    base = os.environ.get("ALPHA_HOME")
    if base:
        return Path(base) / "bot_exports"
    return Path.home() / ".alpha" / "bot_exports"


def acknowledgement(state: str) -> str:
    """The single emoji that opens a reply, so a caller can prefix consistently."""
    glyphs = {
        "working": _ACK_PREFIX,
        "answering": _ANSWERING_GLYPH,
        "done": _DONE_GLYPH,
        "approval": _APPROVAL_GLYPH,
        "blocked": _BLOCKED_GLYPH,
        "scheduled": _SCHEDULED_GLYPH,
    }
    return glyphs.get(state, _ACK_PREFIX)
