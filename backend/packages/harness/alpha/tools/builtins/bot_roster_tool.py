"""Built-in tool for managing and monitoring autonomous AI Bot profiles and fleet health.

Empowers AI agents to easily create, configure, monitor, and coordinate specialist bots
with zero-configuration auto-provisioning, liveness tracking, and dynamic team generation.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from langchain.tools import tool

from alpha.bots.authority_ceiling import AuthorityViolation
from alpha.bots.health import get_health_monitor
from alpha.bots.kill_switch import (
    get_kill_switch_status,
    is_bot_paused,
    pause_bot,
    resume_bot,
    set_global_kill_switch,
)
from alpha.bots.organization import generate_organization_for_goal
from alpha.bots.performance import get_bot_performance
from alpha.bots.registry import SELF_EXTENSION_ACTORS, get_bot_registry
from alpha.skills.authoring import _MARKETING_WORDS

# Length of the fixed words in "Runs the  procedure." — used to size the topic.
_DESC_STEM_CHARS = len("Runs the  procedure.")

# Executables we recognise as a real invocation when the line carries no
# ``$`` prompt. Kept deliberately small: a false negative fails the bar
# honestly ("no verification command"), a false positive lets prose through.
_KNOWN_EXECUTABLES = frozenset(
    {
        "python",
        "python3",
        "pytest",
        "pip",
        "pip3",
        "uv",
        "node",
        "npm",
        "pnpm",
        "yarn",
        "git",
        "make",
        "docker",
        "podman",
        "curl",
        "wget",
        "rg",
        "grep",
        "cat",
        "bash",
        "sh",
        "zsh",
        "powershell",
        "pwsh",
        "cmd",
        "ruff",
        "mypy",
        "go",
        "cargo",
        "rustc",
        "java",
        "mvn",
        "gradle",
        "terraform",
        "kubectl",
        "alpha",
        "uvicorn",
        "poetry",
        "tox",
        "nox",
        "sed",
        "awk",
        "jq",
        "tar",
    }
)

_TOKEN_RE = re.compile(r"^\$?\s*([A-Za-z0-9_./:-]+)\s*(.*)$")
_PREREQ_HINT = re.compile(
    r"(?:\bexport\s+\w+=|\bpip(?:3)?\s+install\b|\bnpm\s+(?:i|install)\b|"
    r"\brequires?\b|\bneeds?\b|\binstall\b|\benvironment\s+variable\b)",
    re.I,
)
_PITFALL_HINT = re.compile(
    r"(?:\bnever\b|\bdon'?t\b|\bdo not\b|\bwarning\b|\bcaution\b|\blimit\b|"
    r"\brates?\s+limit\b|\bfalse positive\b|\bmust not\b|\bcareful\b)",
    re.I,
)


def _looks_like_command(candidate: str) -> str | None:
    """Return the candidate if it is unambiguously a command, else ``None``."""
    line = candidate.strip()
    if not line:
        return None
    prompted = line.startswith("$")
    if prompted:
        line = line.lstrip("$").strip()
    match = _TOKEN_RE.match(line)
    if not match:
        return None
    head, rest = match.group(1), match.group(2)
    executable = head.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if prompted:
        return line
    if executable not in _KNOWN_EXECUTABLES:
        return None
    if re.match(r"^-{1,2}\w", rest) or "/" in rest or "://" in rest:
        return line
    if rest and " " in rest.strip():
        return line  # subcommand form: `git status`, `pytest tests/...`
    return None


def _command_line(text: str) -> str | None:
    """Return the first line that is unambiguously a command, or ``None``.

    Prefers an explicit ``$`` prompt. Without one, the first token must be a
    recognised executable *and* the line must carry a flag, path, URL, or
    subcommand - otherwise ordinary prose such as "call the API" would be
    mistaken for a runnable check and would satisfy the verification bar.
    Procedures routinely embed the check inline, so backtick spans are tested
    as candidates too: ``Run `pytest -q` after every edit`` yields the command.
    """
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        found = _looks_like_command(line)
        if found:
            return found
        for span in re.findall(r"`([^`\n]+)`", line):
            found = _looks_like_command(span)
            if found:
                return found
    return None


def _teach_description(topic: str, bot_name: str) -> str:
    """A capability-first description that fits the 60-char authoring bar.

    Marketing words are stripped rather than submitted and failed: the bar
    exists to keep descriptions honest, not to score own-goals on wording.
    """
    clean = " ".join((topic or "").split())
    for word in _MARKETING_WORDS:
        clean = re.sub(rf"\b{re.escape(word)}\b", "", clean, flags=re.I)
    clean = " ".join(clean.split()) or "operator"
    desc = f"Runs the {clean} procedure."
    if len(desc) > 60:
        desc = f"Runs the {clean[:_DESC_STEM_CHARS].strip()} procedure."
    with_bot = f"{desc[:-1]} for {bot_name}."
    if len(with_bot) <= 60:
        desc = with_bot
    return desc


def _teach_body(topic: str, bot_name: str, content: str) -> str:
    """Structure a raw procedure into the six sections the authoring bar requires.

    Only *derivable* sections are filled. Where the instructor gave nothing —
    notably a Verification command — the section is left honestly empty so
    ``validate_skill_draft`` reports the gap instead of a fabricated section
    passing the bar.
    """
    procedure = (content or "").strip()
    lines = procedure.splitlines()

    command = _command_line(procedure)
    prereqs = [ln.strip() for ln in lines if _PREREQ_HINT.search(ln)][:5]
    pitfalls = [ln.strip() for ln in lines if _PITFALL_HINT.search(ln)][:5]

    def bullets(items: list[str], empty: str) -> str:
        return "\n".join(f"- {item}" for item in items) if items else empty

    how_to_run = command or "See the Procedure below."
    verification = f"```\n{command}\n```" if command else "No verification command was supplied."

    return (
        f"## {topic}\n\n"
        f"Repeats the {topic} procedure for the Bot `{bot_name}`. It covers only the "
        "steps written below — it does not decide scope, and it does not publish, "
        "spend, or delete anything without approval.\n\n"
        "## When to Use\n\n"
        f"- the operator asks {bot_name} to {topic}\n"
        "- the same steps have been written out by hand at least once already\n\n"
        "## Prerequisites\n\n" + bullets(prereqs, "None declared by the instructor.\n") + "\n\n## How to Run\n\n"
        f"```\n{how_to_run}\n```\n\n"
        "## Procedure\n\n"
        f"{procedure}\n\n"
        "## Pitfalls\n\n" + bullets(pitfalls, "None declared by the instructor.\n") + "\n\n## Verification\n\n" + verification + "\n"
    )


# ---------------------------------------------------------------------------
# Self-service authorisation (`update_profile`, `routine`)
# ---------------------------------------------------------------------------
#
# The property both helpers protect is *a Bot may configure itself, but may not
# widen itself.* They exist because these two actions deliberately take the
# operator out of the loop, so the refusal path has to be the default one
# rather than the thing a caller remembers to ask for.

#: Fields a Bot may change about its **own** profile. Presentation only: a
#: name and a face are read by the roster UI and by nothing else, so editing
#: one cannot change what the Bot is allowed to do or what it costs to run.
_SELF_EDITABLE: frozenset[str] = frozenset({"display_name", "avatar"})

#: Fields that are a grant rather than a label. ``role`` is fed to
#: ``ToolPermissionGate.check_permission()``, ``model`` and ``skills`` are part
#: of ``capability_fingerprint()``, ``capabilities`` decides what is offered,
#: and ``department``/``reports_to`` move the Bot around the org chart — a Bot
#: naming any of them would be naming its own authority or its own budget.
_LEADER_ONLY: frozenset[str] = frozenset({"role", "model", "department", "reports_to", "skills", "capabilities"})


def _resolve_actor(actor: str) -> str:
    """Normalise who says they are acting, or refuse when nobody said.

    An empty actor fails closed instead of defaulting to the operator: every
    other default available here would hand an *unidentified* caller more
    privilege than an identified one, which is the exact inversion a
    self-service surface must not ship.
    """
    who = (actor or "").strip().lower()
    if not who:
        raise ValueError(f"actor is required — pass actor=<your own bot handle> to change yourself, or a leader handle ({', '.join(sorted(SELF_EXTENSION_ACTORS))}) to change somebody else.")
    return who


def _authorize_edit(target: str, who: str, fields: set[str]) -> None:
    """Permit the edit, or raise :class:`AuthorityViolation` saying why not.

    ``fields`` is the set of things being touched, so a refusal names the
    actual offenders instead of rejecting a whole batch over one field.
    """
    if who in SELF_EXTENSION_ACTORS:
        return
    if who != target:
        raise AuthorityViolation(
            f"{who!r} may not edit {target!r}: a Bot edits its own profile only (leader actors: {sorted(SELF_EXTENSION_ACTORS)})",
            violations=[f"not_leader:{who}"],
        )
    refused = sorted(fields & _LEADER_ONLY)
    if refused:
        raise AuthorityViolation(
            f"{who!r} may not change {refused} on its own profile: those are grants, not labels. "
            f"Self-service covers presentation only ({', '.join(sorted(_SELF_EDITABLE))}) and the Bot's own routines; "
            f"ask a leader ({', '.join(sorted(SELF_EXTENSION_ACTORS))}) for the rest.",
            violations=[f"self_grant:{field}" for field in refused],
        )


@tool("bot_roster", parse_docstring=True)
def bot_roster_tool(
    action: Literal[
        "list",
        "monitor",
        "inspect",
        "create",
        "generate_team",
        "handoff",
        "update_soul",
        "update_profile",
        "routine",
        "pause",
        "resume",
        "kill_switch",
        "forge",
        "teach",
        "waiting_on",
        "journal",
        "share",
        "import",
        "doctor",
        "sandbox",
    ],
    name: str = "",
    role: str = "",
    template: str = "",
    department: str = "",
    reports_to: str = "",
    display_name: str = "",
    model: str = "",
    avatar: str = "",
    soul: str = "",
    skills: str = "",
    capabilities: str = "",
    actor: str = "",
    routine: str = "",
    goal: str = "",
    target_bot: str = "",
    task_id: str = "",
    objective: str = "",
    reason: str = "",
    approvals: str = "",
    sandbox: str = "",
    path: str = "",
    schedule: str = "",
    content: str = "",
    completed: str = "",
    allow_frequent: bool = False,
    allow_overlap: bool = False,
    journal_enabled: bool = True,
    enabled: bool = True,
) -> str:
    """Create, configure, inspect, and monitor autonomous AI agent profiles and fleet operations.

    Args:
        action: Operation to perform:
            - 'create': Quickly provision a new AI agent profile with role, template, department, or reporting line.
            - 'forge': Build a COMPLETE Bot transactionally — persona, approvals, routines, journal — rolled back on any failure and only reported alive after a smoke test. Refuses duplicate roles and unaffordable routines.
            - 'monitor': Monitor the entire AI agent fleet in real-time (health, liveness, active tasks, stalled workers, kill switch).
            - 'inspect': Deeply inspect an agent's profile, department, reputation, execution stats, and liveness.
            - 'list': List all active agent profiles in the roster.
            - 'generate_team': Dynamically formulate and auto-provision a specialized multi-agent team from a goal description.
            - 'handoff': Coordinate a structured work handoff between two bots with task ID and objective.
            - 'update_soul': Update the SOUL/personality prompt of an existing agent.
            - 'update_profile': Self-service profile edit. A Bot may change its own presentation (display_name, avatar, model); role, department, reports_to, skills and capabilities are grants and stay leader-only. Requires actor.
            - 'routine': List, schedule or remove a Bot's scheduled routines. Reading needs only name; any write requires actor and the same frequency floor forge applies at birth.
            - 'teach': Save a procedure as a skill the named Bot keeps and loads when the job comes up.
            - 'journal': Enable, append to, or read a Bot's private dated work journal.
            - 'waiting_on': Answer 'anything waiting on me?' — every unresolved blocker across every Bot, with its age.
            - 'share': Export a Bot to a secret-scanned .alphabot.json template (design only — never chats, facts or keys).
            - 'import': Build a Bot from a .alphabot.json template, re-scanned on the way in.
            - 'doctor': Check the installation itself: roster state, stalled routines, stopped bots, journals, sandbox coverage.
            - 'sandbox': Report which sandbox backends are usable on this machine and which Bots run on the real host.
            - 'pause': Pause execution for a specific bot.
            - 'resume': Resume a paused bot.
            - 'kill_switch': Toggle fleet-wide emergency stop.
        name: Bot handle name (e.g. 'coder', 'architect', 'secops', 'data-lead').
        role: Specialty role of the bot (e.g. 'Senior Security Engineer').
        template: Pre-configured role template slug (e.g. 'ceo', 'cto', 'architect', 'coder', 'security', 'sre', 'qa', 'researcher', 'frontend', 'marketing', 'support').
        department: Department name ('executive', 'engineering', 'product', 'qa', 'operations', 'security', 'growth', 'support').
        reports_to: Manager bot handle (e.g. 'architect', 'cto', 'ceo').
        display_name: Human-readable display name (e.g. 'Alex the Security Lead').
        model: Model to pin on the profile ('update_profile').
        avatar: Avatar glyph/text shown on the roster row ('update_profile').
        soul: Custom SOUL instructions/personality.
        skills: Comma-separated list of skills for the bot.
        capabilities: Comma-separated list of capabilities (e.g. 'python, sql, fastapi').
        actor: Who is performing an 'update_soul', 'update_profile' or 'routine' write — your own bot handle to change yourself, or a leader handle (alpha/lead/system/server) to change somebody else. Refused when omitted.
        routine: Routine name for 'routine'; omit it to list the Bot's routines, supply it alone to remove that routine, or supply it with schedule and content to add it.
        goal: Project goal description for 'generate_team'.
        target_bot: Recipient bot handle for 'handoff'.
        task_id: Task identifier for 'handoff'.
        objective: Work objective for 'handoff'.
        reason: Justification reason for 'pause', 'kill_switch', or a journal blocker.
        approvals: Comma-separated approval checkpoints written into the SOUL at birth (default: publish/send, spend money, delete data). Pass 'none' to deliberately forge a bot with no checkpoints; an empty value keeps the default.
        sandbox: Sandbox backend for 'forge'/'sandbox' — 'docker', 'singularity', 'apptainer', 'podman' or 'none'. Refused before creating anything if unusable here.
        path: File path for 'share'/'import'.
        schedule: Schedule for 'routine' or routine cost checking on 'forge' (e.g. 'every 30 minutes', '0 7 * * *').
        content: Body text for 'teach' (the procedure), the title of a 'journal' entry, and what a 'routine' runs.
        completed: Work recorded as finished on a 'journal' entry; an entry carrying this closes the matching open blocker automatically.
        allow_frequent: Operator override for the routine frequency floor (default 30 minutes).
        allow_overlap: Operator override for the duplicate-role refusal.
        journal_enabled: Whether 'forge' creates a work journal (default true).
        enabled: Whether a 'routine' is scheduled to run (default true).
    """
    registry = get_bot_registry()
    monitor = get_health_monitor()

    # 1. MONITOR FLEET HEALTH
    if action == "monitor":
        # A retired bot must not be presented as an available teammate.
        all_bots = registry.list_bots(include_archived=False)
        fleet = monitor.get_fleet_health(all_bots)
        summary = fleet["summary"]
        ks = get_kill_switch_status()

        lines = [
            "=== AI Agent Fleet Health & Monitor ===",
            f"Fleet Health Score: {int(fleet['fleet_health_score'] * 100)}% Operational",
            (
                f"Total Agents: {summary['total']} | Healthy: {summary['healthy']} | Sleeping: {summary['sleeping']} | "
                f"Stale: {summary['stale']} | Stalled: {summary['stalled']} | Suspended: {summary['suspended']} | Archived: {summary['archived']}"
            ),
            f"Global Kill Switch: {'ACTIVE (EMERGENCY STOP)' if ks['global_kill_switch_active'] else 'Inactive (Normal Operations)'}",
        ]
        if ks["paused_count"] > 0:
            lines.append(f"Individually Paused Bots: {', '.join(ks['paused_bots'].keys())}")

        if fleet["stalled_workers"]:
            lines.append("\n⚠️ STALLED WORKERS DETECTED:")
            for sw in fleet["stalled_workers"]:
                lines.append(f"  - @{sw['bot_name']}: Stalled on task `{sw['active_task_id']}` (Lease expired)")

        lines.append("\nAgent Roster:")
        for b in fleet["bots"]:
            paused, p_reason = is_bot_paused(b["bot_name"])
            status_badge = f"PAUSED ({p_reason})" if paused else b["liveness"].upper()
            task_str = f" | Working on: {b['active_task_id']}" if b.get("active_task_id") else ""
            lines.append(f"  - @{b['bot_name']} [{status_badge}]{task_str}")

        return "\n".join(lines)

    # 2. CREATE / AUTO-PROVISION AGENT
    elif action == "create":
        if not name:
            return "Error: 'name' is required to create an agent."
        clean_name = name.lower().strip()

        skills_list = [s.strip() for s in skills.split(",") if s.strip()] if skills else None
        caps_list = [c.strip() for c in capabilities.split(",") if c.strip()] if capabilities else None

        bot = registry.get_or_create(
            clean_name,
            display_name=display_name or None,
            role=role or None,
            soul=soul or None,
            template=template or None,
            department=department or None,
            reports_to=reports_to or None,
            capabilities=caps_list,
        )
        if skills_list:
            registry.update_bot(clean_name, skills=skills_list, bump_version=False)

        reports_to_text = f"@{bot.reports_to}" if bot.reports_to else "none"
        return (
            f"✅ Successfully provisioned AI Agent: @{bot.name} ({bot.display_name})\n"
            f"Role: {bot.role}\n"
            f"Department: {bot.department} | Reports To: {reports_to_text}\n"
            f"Status: {bot.status} | Epoch: `{bot.capability_fingerprint()}`\n"
            f"Capabilities: {', '.join(bot.capabilities) if bot.capabilities else 'Generalist'}"
        )

    # 3. DYNAMICALLY GENERATE FULL TEAM FROM GOAL
    elif action == "generate_team":
        target_goal = goal or objective or role
        if not target_goal:
            return "Error: 'goal' is required for 'generate_team'."
        org = generate_organization_for_goal(target_goal, registry=registry, auto_provision=True)

        lines = [
            "=== Dynamic Team Formulated for Goal ===",
            f"Objective: {target_goal}",
            f"Recommended Team Size: {org['recommended_team_size']}",
            f"Auto-Provisioned Bots: {', '.join(org['auto_provisioned'])}",
            "\nTeam Structure:",
        ]
        for r in org["recommended_roles"]:
            lines.append(f"  - {r.get('avatar', '🤖')} @{r['slug']} ({r['display_name']}) — {r['role']} [Dept: {r['department']}, Reports to: @{r.get('reports_to') or 'CEO'}]")
        return "\n".join(lines)

    # 4. INSPECT DETAILED PROFILE & PERFORMANCE
    elif action == "inspect":
        if not name:
            return "Error: 'name' is required for 'inspect'."
        clean_name = name.lower().strip()
        bot = registry.get_bot(clean_name)
        if not bot:
            return f"Error: Bot '@{clean_name}' not found."

        liv = monitor.evaluate_liveness(bot)
        paused, pause_reason = is_bot_paused(clean_name)
        perf = get_bot_performance(clean_name, registry=registry)

        # Honest rendering: with zero recorded runs the performance engine
        # reports None for reputation/success-rate. Python's None must never
        # leak into a value string ("None%") and no number may be invented —
        # render "unverified" instead, and never present the zero-run default
        # duration as a measured average.
        rep_score = perf["reputation_score"]
        rep_text = "unverified" if rep_score is None else str(rep_score)
        success_rate = perf["success_rate_percent"]
        success_text = "unverified" if success_rate is None else f"{success_rate}%"
        avg_text = f"{perf['avg_duration_seconds']}s" if perf["total_runs"] > 0 else "unverified (no recorded runs)"
        reports_to_text = f"@{bot.reports_to}" if bot.reports_to else "none"

        return (
            f"=== Bot Profile: @{bot.name} ({bot.display_name}) {bot.avatar} ===\n"
            f"Role: {bot.role}\n"
            f"Department: {bot.department} | Reports To: {reports_to_text}\n"
            f"Liveness: {liv['liveness'].upper()} | State: {bot.status} | Paused: {paused} ({pause_reason or 'No'})\n"
            f"Reputation: {rep_text} ({perf['reputation_tier']}) | Success Rate: {success_text}\n"
            f"Completed Tasks: {perf['completed_runs']} | Failed: {perf['failed_runs']} | Avg Duration: {avg_text}\n"
            f"Active Task: {liv.get('active_task_id') or 'Idle / None'}\n"
            f"Epoch: `{bot.capability_fingerprint()}`\n"
            f"Capabilities: {', '.join(bot.capabilities) if bot.capabilities else 'None'}\n"
            f"Responsibilities: {', '.join(bot.responsibilities) if bot.responsibilities else 'General domain'}\n\n"
            f"--- SOUL ---\n{bot.soul[:400]}..."
        )

    # 5. LIST ROSTER
    elif action == "list":
        bots = registry.list_bots()
        lines = ["=== Autonomous AI Bot Roster ==="]
        for b in bots:
            # An absent stored score must not render Python's None.
            rep_text = "unverified" if b.reputation_score is None else str(b.reputation_score)
            lines.append(f"- {b.avatar or '🤖'} **@{b.name}** ({b.display_name}) — `{b.role}` | Dept: `{b.department}` | Rep: `{rep_text}`")
        return "\n".join(lines)

    # 6. TASK HANDOFF
    elif action == "handoff":
        if not name or not target_bot:
            return "Error: 'name' (sender) and 'target_bot' (recipient) are required for 'handoff'."
        from alpha.bots.handoff import execute_handoff

        try:
            pkg = execute_handoff(
                task_id=task_id or f"task-handoff-{clean_name}",
                from_bot=name,
                to_bot=target_bot,
                objective=objective or f"Handoff from @{name}",
                handoff_notes=reason or "",
                registry=registry,
            )
            return f"✅ Task handoff completed: @{pkg.from_bot} ➔ @{pkg.to_bot} for task `{pkg.task_id}`: {pkg.objective}."
        except Exception as exc:
            return f"Error executing handoff: {exc}"

    # 7. UPDATE SOUL
    elif action == "update_soul":
        if not name or not soul:
            return "Error: 'name' and 'soul' are required for 'update_soul'."
        target = name.lower().strip()
        if registry.get_bot(target) is None:
            return f"Error: Bot '@{name}' not found."
        try:
            who = _resolve_actor(actor)
            # `soul` is deliberately *not* leader-only: a Bot evolving its own
            # persona is the point of self-modification. The gate exists
            # because writing somebody else's soul is exactly how a refused
            # `role` edit gets re-applied a moment later as prose.
            _authorize_edit(target, who, {"soul"})
        except (ValueError, AuthorityViolation) as exc:
            return f"Error: {exc}"
        bot = registry.update_bot(target, soul=soul)
        if not bot:
            return f"Error: Bot '@{name}' not found."
        return f"Updated SOUL for @{bot.name}. New capability epoch: {bot.capability_fingerprint()}."

    # 7b. SELF-SERVICE PROFILE EDIT
    elif action == "update_profile":
        if not name:
            return "Error: 'name' is required for 'update_profile'."
        target = name.lower().strip()
        if registry.get_bot(target) is None:
            return f"Error: Bot '@{target}' not found."
        try:
            who = _resolve_actor(actor)
        except ValueError as exc:
            return f"Error: {exc}"

        changes: dict[str, Any] = {
            key: value.strip()
            for key, value in {
                "display_name": display_name,
                "model": model,
                "avatar": avatar,
                "role": role,
                "department": department,
                "reports_to": reports_to,
            }.items()
            if (value or "").strip()
        }
        if (skills or "").strip():
            changes["skills"] = [part.strip() for part in skills.split(",") if part.strip()]
        if (capabilities or "").strip():
            changes["capabilities"] = [part.strip() for part in capabilities.split(",") if part.strip()]

        if not changes:
            return f"Error: nothing to change. Settable here — presentation: {', '.join(sorted(_SELF_EDITABLE))}; leader-only: {', '.join(sorted(_LEADER_ONLY))}. Status, reputation and task stats belong to pause/resume and the task ledger."
        try:
            _authorize_edit(target, who, set(changes))
        except AuthorityViolation as exc:
            return f"Error: {exc}"

        bot = registry.update_bot(target, **changes)
        if bot is None:
            return f"Error: Bot '@{target}' not found."
        return f"Updated @{bot.name}: {', '.join(changes)}\nVersion: v{bot.version} | Epoch: `{bot.capability_fingerprint()}`"

    # 7c. SCHEDULED ROUTINES — a Bot's own automation, self-service
    elif action == "routine":
        from alpha.bots.forge import plan_routine_guard

        if not name:
            return "Error: 'name' is required for 'routine'."
        target = name.lower().strip()
        bot = registry.get_bot(target)
        if bot is None:
            return f"Error: Bot '@{target}' not found."

        routine_name = (routine or "").strip()
        if not routine_name:
            if not bot.routines:
                return f"@{target} has no scheduled routines. Add one with action='routine', routine=<name>, schedule='every 60 minutes', content=<what it runs>, actor=<you>."
            lines = [f"@{target} routines ({len(bot.routines)}):"]
            for entry in bot.routines:
                state = "on" if entry.get("enabled", True) else "off"
                lines.append(f" - {entry.get('name')}: {entry.get('schedule')} -> {entry.get('action')} [{state}]")
            return "\n".join(lines)

        # Reading above needs no actor; every write below does.
        try:
            who = _resolve_actor(actor)
            _authorize_edit(target, who, {"routines"})
        except (ValueError, AuthorityViolation) as exc:
            return f"Error: {exc}"

        sched = (schedule or "").strip()
        body = (content or "").strip()
        if not sched and not body:
            if registry.remove_routine(target, routine_name):
                return f"Removed routine '{routine_name}' from @{target}."
            return f"@{target} has no routine named '{routine_name}' — nothing removed."
        if not sched or not body:
            return "Error: a routine write needs both schedule= and content= (pass neither to remove it)."

        verdict = plan_routine_guard([sched], allow_frequent=allow_frequent)
        if not verdict.allowed:
            return f"Error: routine '{routine_name}' refused: {verdict.reason}"

        stored = registry.add_routine(target, routine_name, sched, body, enabled=enabled)
        if stored is None:
            return f"Error: Bot '@{target}' not found."
        return f"Scheduled routine '{routine_name}' on @{target}: {sched} -> {body}\nState: {'on' if enabled else 'off'} | Interval: {verdict.interval_minutes} min | Runs/day: {verdict.runs_per_day}"

    # 8. PAUSE / RESUME
    elif action == "pause":
        if not name:
            return "Error: 'name' is required for 'pause'."
        pause_bot(name, reason=reason or "Agent requested pause")
        return f"⏸️ Bot @{name} has been paused."

    elif action == "resume":
        if not name:
            return "Error: 'name' is required for 'resume'."
        resume_bot(name)
        return f"▶️ Bot @{name} has been resumed."

    # 9. KILL SWITCH
    elif action == "kill_switch":
        # Toggle or activate
        is_active, _ = get_kill_switch_status()["global_kill_switch_active"], ""
        new_active = not is_active if not reason else True
        st = set_global_kill_switch(new_active, reason=reason or "Agent emergency stop")
        return f"🚨 Global Kill Switch is now {'ACTIVATED (All bot operations stopped)' if st['global_kill_switch_active'] else 'DEACTIVATED (Normal operations resumed)'}."

    # 10. FORGE — transactional build with rollback + smoke test
    elif action == "forge":
        if not name:
            return "Error: 'name' is required for 'forge'."
        from alpha.bots.forge import forge_bot

        if approvals.strip():
            token = approvals.strip().lower()
            if token in {"none", "off", "no approvals"}:
                # Explicit opt-out: the operator takes the checkpoint-free path.
                approvals_list = []
            else:
                approvals_list = [a.strip() for a in approvals.split(",") if a.strip()]
        else:
            # Not supplied -> draft-first default. An empty string cannot mean
            # "no approvals" because it is indistinguishable from "not asked";
            # pass approvals="none" to opt out.
            approvals_list = None

        routines: list[dict[str, object]] = []
        if schedule:
            routines.append(
                {
                    "name": f"{name.strip().lower()}-routine",
                    "schedule": schedule,
                    "action": objective or goal or role or "daily check-in",
                }
            )

        result = forge_bot(
            name=name,
            role=role or "Specialist",
            registry=registry,
            soul=soul or None,
            template=template or None,
            department=department or None,
            reports_to=reports_to or None,
            display_name=display_name or None,
            skills=[s.strip() for s in skills.split(",") if s.strip()] or None,
            toolsets=[t.strip() for t in capabilities.split(",") if t.strip()] or None,
            approvals=approvals_list,
            sandbox=sandbox or None,
            routines=routines or None,
            allow_frequent=allow_frequent,
            allow_overlap=allow_overlap,
            journal_enabled=journal_enabled,
        )
        return result.reply()

    # 11. TEACH — record a procedure as a Bot's skill, through the approve gate
    elif action == "teach":
        if not name or not content:
            return "Error: 'teach' requires 'name' and 'content'."
        from alpha.skills.authoring import validate_skill_draft
        from alpha.skills.proposals import (
            SkillProposalStore,
            proposals_root,
            scan_proposal_markdown,
        )
        from alpha.skills.workshop import SkillWorkshopEngine

        clean = name.lower().strip()
        bot = registry.get_bot(clean)
        if not bot:
            return f"Error: Bot '@{clean}' not found."

        topic = " ".join((role or objective or goal or "operator procedure").split())
        slug = SkillWorkshopEngine._sanitize_name(f"{topic} for {clean}")
        description = _teach_description(topic, clean)
        body = _teach_body(topic, clean, content)

        findings = validate_skill_draft(slug, description, body)
        if findings:
            # Report the bar the draft missed rather than installing it anyway.
            lines = [f"NOT taught — @`{slug}` fails the skill authoring bar:"]
            lines.extend(f"  - {f}" for f in findings)
            lines.append("Fix the procedure (a Verification command is required) and retry.")
            return "\n".join(lines)

        skill_md = f"---\nname: {slug}\ndescription: {description}\nversion: 0.1.0\n---\n\n{body}"
        try:
            scan_proposal_markdown(slug, skill_md)
        except Exception as exc:
            return f"NOT taught — static scan blocked `{slug}`: {exc}"

        store = SkillProposalStore(proposals_root())
        try:
            proposal = store.create(clean, slug, description, skill_md)
        except Exception as exc:
            return f"NOT taught — could not queue `{slug}`: {type(exc).__name__}: {exc}"

        bot.metadata.setdefault("proposed_skills", {})[slug] = proposal.id
        registry.update_bot(clean, bump_version=True)

        return (
            f"Taught @{clean}: `{slug}` is queued for review.\n"
            f"proposal: {proposal.id} (status: {proposal.status})\n"
            f"scan: clean | description: {description} ({len(description)} chars)\n"
            "The skill is NOT active yet — the admin approve gate installs it. "
            f"Do not claim it as an installed skill until approval flips it to installed."
        )

    # 12. JOURNAL — enable / append / read a Bot's private work journal
    elif action == "journal":
        if not name:
            return "Error: 'name' is required for 'journal'."
        from alpha.bots.forge import _default_journal_root
        from alpha.bots.journal import get_journal

        clean = name.lower().strip()
        if registry.get_bot(clean) is None:
            # A journal for a Bot that does not exist is a blocker nobody will
            # ever see. Refuse rather than silently creating an orphan.
            return f"Error: Bot '@{clean}' not found - create it before journaling."
        journal = get_journal(_default_journal_root(), clean)
        if not content and not reason:
            if not journal.enabled:
                return f"Journal for @{clean} is not enabled yet. Pass content= to enable it and record the first entry."
            entries = journal.read()
            if not entries:
                return f"@{clean} journal is enabled but has no entries yet."
            lines = [f"=== @{clean} work journal ({len(entries)} most recent) ==="]
            for entry in entries:
                lines.append(f"- [{entry.day}] {entry.title} ({entry.state})")
                if entry.outcome:
                    lines.append(f"    outcome: {entry.outcome}")
                if entry.blocker:
                    lines.append(f"    blocker: {entry.blocker}")
                if entry.completed:
                    lines.append(f"    completed: {entry.completed}")
                for item in entry.evidence:
                    lines.append(f"    evidence: {item}")
            summary = journal.summary()
            if summary["blockers_open"]:
                lines.append(f"open blockers: {summary['blockers_open']}")
            return "\n".join(lines)

        entry = journal.append(
            (content or reason or "work note").strip(),
            tried=objective or "",
            outcome=goal or "",
            blocker=reason.strip(),
            completed=completed.strip(),
            evidence=[e.strip() for e in capabilities.split(",") if e.strip()],
        )
        if isinstance(entry, str):
            # A refusal string, not an entry — tell the Bot why it was refused.
            return f"@{clean}: {entry}"
        summary = journal.summary()
        state = {
            "blocked": "blocker opened — it shows up in waiting_on until closed",
            "done": "outcome recorded",
            "noted": "noted",
        }[entry.state]
        reply = f"Journal entry recorded for @{clean} [{entry.day}] ({state})."
        if summary["blockers_open"]:
            reply += f"\nopen blockers for @{clean}: {summary['blockers_open']}."
        return reply

    # 13. WAITING_ON — the one question that answers for the whole roster
    elif action == "waiting_on":
        from alpha.bots.forge import _default_journal_root
        from alpha.bots.journal import discover_journals, format_waiting, waiting_on_you

        # Disk-driven, not roster-driven: a blocker recorded by a Bot that was
        # later retired or renamed must still surface, or it piles up unseen.
        journals = discover_journals(_default_journal_root())
        rows = waiting_on_you(journals)
        lines = [format_waiting(rows)]
        if rows:
            lines.append("")
            for row in rows:
                lines.append(f"  - @{row['bot']} ({row['days_waiting']}d): {row['needs']}")
            lines.append("\nAn item closes itself when that Bot records the same work as completed - nothing to tick off by hand.")
            off_roster = sorted({r["bot"] for r in rows} - {b.name for b in registry.list_bots(include_archived=False)})
            if off_roster:
                lines.append("off the roster (journal survives the Bot): " + ", ".join(f"@{n}" for n in off_roster))
        return "\n".join(lines)

    # 14. SHARE — secret-scanned, design-only export
    elif action == "share":
        if not name:
            return "Error: 'name' is required for 'share'."
        from alpha.bots.forge import _default_export_root
        from alpha.bots.portable import TemplateError, export_template

        clean = name.lower().strip()
        bot = registry.get_bot(clean)
        if not bot:
            return f"Error: Bot '@{clean}' not found."
        try:
            out = export_template(
                bot.to_dict(),
                path or f"{clean}.alphabot.json",
                export_root=_default_export_root(),
                mode="backup" if reason.strip().lower() == "backup" else "template",
            )
        except TemplateError as exc:
            return f"Error: {exc}"
        excluded = ", ".join(out["excluded"]) or "none"
        return f"Exported @{clean} to {out['path']}\nscan: {out['verdict']} | private: {out['private']} | {out['bytes']} bytes\nexcluded (never shared): {excluded}"

    # 15. IMPORT — re-scanned on the way in
    elif action == "import":
        if not path:
            return "Error: 'path' is required for 'import'."
        from alpha.bots.portable import TemplateError, import_template

        known = [b.name for b in registry.list_bots(include_archived=False)]
        try:
            incoming = import_template(
                path,
                known_names=known,
                proposed_name=name or None,
            )
        except TemplateError as exc:
            return f"Error: {exc}"
        profile = incoming["profile"]
        created = registry.get_or_create(
            profile["name"],
            display_name=profile.get("display_name"),
            role=profile.get("role"),
            soul=profile.get("soul"),
            department=profile.get("department"),
            reports_to=profile.get("reports_to"),
            responsibilities=profile.get("responsibilities"),
            capabilities=profile.get("capabilities"),
            skills=profile.get("skills"),
            toolsets=profile.get("toolsets"),
        )
        return f"Imported @{created.name} from {incoming['source']}\nscan: {incoming['verdict']} | renamed: {incoming['renamed']}\nrole: {created.role} | epoch: `{created.capability_fingerprint()}`"

    # 16. DOCTOR — is this installation actually working?
    elif action == "doctor":
        from alpha.bots.forge import (
            MIN_ROUTINE_INTERVAL_MINUTES,
            _default_journal_root,
            plan_routine_guard,
            probe_sandbox,
        )
        from alpha.bots.journal import get_journal

        problems: list[str] = []
        notes: list[str] = []
        bots = registry.list_bots(include_archived=False)

        if not bots:
            problems.append("roster is empty — no Bots to work with")

        root = _default_journal_root()
        no_journal: list[str] = []
        overdue: list[str] = []
        frequent: list[str] = []
        shell_on_host: list[str] = []

        for bot in bots:
            journal = get_journal(root, bot.name)
            if not journal.enabled:
                no_journal.append(bot.name)
            summary = journal.summary() if journal.enabled else None
            if summary and summary["blockers_open"]:
                overdue.append(f"@{bot.name} ({summary['blockers_open']} open)")

            if bot.routines:
                schedules = [str(r.get("schedule", "")) for r in bot.routines]
                report = plan_routine_guard(schedules)
                if not report.allowed:
                    frequent.append(f"@{bot.name}: {report.reason}")

            declared_sandbox = bot.metadata.get("sandbox")
            shell_capable = {"terminal", "code_execution", "computer_use"} & set(bot.toolsets)
            if shell_capable and declared_sandbox in (None, "", "none"):
                shell_on_host.append(bot.name)
            elif declared_sandbox:
                ok, why = probe_sandbox(str(declared_sandbox))
                if not ok:
                    problems.append(f"@{bot.name}: sandbox unavailable — {why}")

        notes.append(f"bots: {len(bots)} active")
        notes.append(f"routine frequency floor: {MIN_ROUTINE_INTERVAL_MINUTES} minutes")

        if no_journal:
            notes.append(f"no journal yet: {', '.join(no_journal)} — ask to 'enable journaling'")
        if overdue:
            problems.append("bots with open blockers: " + ", ".join(overdue))
        if frequent:
            problems.extend(f"routine cost: {f}" for f in frequent)
        if shell_on_host:
            problems.append("shell-capable Bots running on the REAL HOST (no sandbox): " + ", ".join(shell_on_host))

        lines = ["=== Bot Forge Doctor ==="]
        lines.append(f"status: {'ISSUES FOUND' if problems else 'OK'}")
        for note in notes:
            lines.append(f"  - {note}")
        if problems:
            lines.append("\nProblems:")
            for problem in problems:
                lines.append(f"  ! {problem}")
        else:
            lines.append("\nNo problems found.")
        return "\n".join(lines)

    # 17. SANDBOX — which backends are usable, and who runs on the host
    elif action == "sandbox":
        from alpha.bots.forge import SANDBOX_BACKENDS, probe_sandbox

        lines = ["=== Sandbox backends on this machine ==="]
        usable: list[str] = []
        for backend in SANDBOX_BACKENDS:
            ok, why = probe_sandbox(backend)
            lines.append(f"  {'OK ' if ok else 'NO '} {backend}: {why}")
            if ok and backend != "none":
                usable.append(backend)
        lines.append("usable: " + (", ".join(usable) if usable else "none (host only)"))

        host_bots: list[str] = []
        lines.append("\nPer-Bot sandbox:")
        for bot in registry.list_bots(include_archived=False):
            declared = bot.metadata.get("sandbox")
            shell = {"terminal", "code_execution", "computer_use"} & set(bot.toolsets)
            if declared:
                lines.append(f"  - @{bot.name}: {declared}")
            elif shell:
                host_bots.append(bot.name)
                lines.append(f"  - @{bot.name}: REAL HOST (shell-capable, no sandbox)")
            else:
                lines.append(f"  - @{bot.name}: no shell capability")
        if host_bots:
            lines.append("\nWARNING: shell-capable Bots on the real host can read your files — ask to forge them with sandbox='docker'.")
        return "\n".join(lines)

    return f"Error: Unknown action '{action}'."
