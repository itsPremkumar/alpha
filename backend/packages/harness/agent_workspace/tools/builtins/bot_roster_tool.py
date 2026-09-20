"""Built-in tool for managing and monitoring autonomous AI Bot profiles and fleet health.

Empowers AI agents to easily create, configure, monitor, and coordinate specialist bots
with zero-configuration auto-provisioning, liveness tracking, and dynamic team generation.
"""

from __future__ import annotations

from typing import Literal

from langchain.tools import tool

from agent_workspace.bots.health import get_health_monitor
from agent_workspace.bots.kill_switch import (
    get_kill_switch_status,
    is_bot_paused,
    pause_bot,
    resume_bot,
    set_global_kill_switch,
)
from agent_workspace.bots.organization import generate_organization_for_goal
from agent_workspace.bots.performance import get_bot_performance
from agent_workspace.bots.registry import get_bot_registry


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
        "pause",
        "resume",
        "kill_switch",
    ],
    name: str = "",
    role: str = "",
    template: str = "",
    department: str = "",
    reports_to: str = "",
    display_name: str = "",
    soul: str = "",
    skills: str = "",
    capabilities: str = "",
    goal: str = "",
    target_bot: str = "",
    task_id: str = "",
    objective: str = "",
    reason: str = "",
) -> str:
    """Create, configure, inspect, and monitor autonomous AI agent profiles and fleet operations.

    Args:
        action: Operation to perform:
            - 'create': Quickly provision a new AI agent profile with role, template, department, or reporting line.
            - 'monitor': Monitor the entire AI agent fleet in real-time (health, liveness, active tasks, stalled workers, kill switch).
            - 'inspect': Deeply inspect an agent's profile, department, reputation, execution stats, and liveness.
            - 'list': List all active agent profiles in the roster.
            - 'generate_team': Dynamically formulate and auto-provision a specialized multi-agent team from a goal description.
            - 'handoff': Coordinate a structured work handoff between two bots with task ID and objective.
            - 'update_soul': Update the SOUL/personality prompt of an existing agent.
            - 'pause': Pause execution for a specific bot.
            - 'resume': Resume a paused bot.
            - 'kill_switch': Toggle fleet-wide emergency stop.
        name: Bot handle name (e.g. 'coder', 'architect', 'secops', 'data-lead').
        role: Specialty role of the bot (e.g. 'Senior Security Engineer').
        template: Pre-configured role template slug (e.g. 'ceo', 'cto', 'architect', 'coder', 'security', 'sre', 'qa', 'researcher', 'frontend', 'marketing', 'support').
        department: Department name ('executive', 'engineering', 'product', 'qa', 'operations', 'security', 'growth', 'support').
        reports_to: Manager bot handle (e.g. 'architect', 'cto', 'ceo').
        display_name: Human-readable display name (e.g. 'Alex the Security Lead').
        soul: Custom SOUL instructions/personality.
        skills: Comma-separated list of skills for the bot.
        capabilities: Comma-separated list of capabilities (e.g. 'python, sql, fastapi').
        goal: Project goal description for 'generate_team'.
        target_bot: Recipient bot handle for 'handoff'.
        task_id: Task identifier for 'handoff'.
        objective: Work objective for 'handoff'.
        reason: Justification reason for 'pause' or 'kill_switch'.
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

        return (
            f"✅ Successfully provisioned AI Agent: @{bot.name} ({bot.display_name})\n"
            f"Role: {bot.role}\n"
            f"Department: {bot.department} | Reports To: @{bot.reports_to or 'None'}\n"
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

        return (
            f"=== Bot Profile: @{bot.name} ({bot.display_name}) {bot.avatar} ===\n"
            f"Role: {bot.role}\n"
            f"Department: {bot.department} | Reports To: @{bot.reports_to or 'None'}\n"
            f"Liveness: {liv['liveness'].upper()} | State: {bot.status} | Paused: {paused} ({pause_reason or 'No'})\n"
            f"Reputation: {bot.reputation_score} ({perf['reputation_tier']}) | Success Rate: {perf['success_rate_percent']}%\n"
            f"Completed Tasks: {perf['completed_runs']} | Failed: {perf['failed_runs']} | Avg Duration: {perf['avg_duration_seconds']}s\n"
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
            lines.append(f"- {b.avatar or '🤖'} **@{b.name}** ({b.display_name}) — `{b.role}` | Dept: `{b.department}` | Rep: `{b.reputation_score}`")
        return "\n".join(lines)

    # 6. TASK HANDOFF
    elif action == "handoff":
        if not name or not target_bot:
            return "Error: 'name' (sender) and 'target_bot' (recipient) are required for 'handoff'."
        from agent_workspace.bots.handoff import execute_handoff

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
        bot = registry.update_bot(name, soul=soul)
        if not bot:
            return f"Error: Bot '@{name}' not found."
        return f"Updated SOUL for @{bot.name}. New capability epoch: {bot.capability_fingerprint()}."

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

    return f"Error: Unknown action '{action}'."
