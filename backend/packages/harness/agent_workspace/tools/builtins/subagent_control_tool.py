"""Built-in Sub-Agent Control Tool: Lifecycle, Leases, Checkpointing & Promotion.

Allows AI agents and supervisors to:
- Spawn asynchronous task-scoped subagents without blocking the parent
- Monitor real-time liveness, progress, and lease status
- Record step-level checkpoints and hot-replace stalled workers
- Adopt orphaned child subagents when a parent crashes
- Promote proven recurring subagent roles into permanent Specialist Bots
"""

from __future__ import annotations

import json
from typing import Literal

from langchain.tools import tool

from agent_workspace.subagents.lifecycle import (
    get_subagent_lifecycle_manager,
)
from agent_workspace.subagents.promotion import get_subagent_promotion_manager
from agent_workspace.subagents.resilience import get_subagent_resilience_engine
from agent_workspace.subagents.specialists import (
    ARCHETYPE_REGISTRY,
    generate_dynamic_role,
)


@tool("subagent_control", parse_docstring=True)
def subagent_control(
    action: Literal[
        "spawn",
        "status",
        "result",
        "heartbeat",
        "checkpoint",
        "resume",
        "cancel",
        "replace",
        "adopt",
        "promote",
        "archetypes",
    ] = "status",
    parent_agent_id: str = "lead-agent",
    subagent_id: str = "",
    objective: str = "",
    role: str = "specialist",
    instructions: str = "",
    progress_percent: float = 0.0,
    current_action: str = "",
    last_tool: str = "",
    step_index: int = 1,
    artifacts_json: str = "[]",
    reason: str = "",
    bot_name: str = "",
) -> str:
    """Orchestrates asynchronous subagent lifecycle, heartbeats, resilience checkpoints, and promotion.

    Args:
        action: Operation to perform:
            - 'spawn': Provisions an asynchronous task-scoped subagent and returns its subagent_id immediately.
            - 'status': Returns the lifecycle state, lease validity, active action, and progress.
            - 'result': Retrieves the structured deliverable (findings, artifacts, evidence) of a completed subagent.
            - 'heartbeat': Emits a liveness heartbeat, updates progress, and extends the lease.
            - 'checkpoint': Saves a step checkpoint with intermediate artifacts and decisions.
            - 'resume': Resumes a paused/waiting subagent.
            - 'cancel': Aborts an active subagent and propagates cancellation down to child subagents.
            - 'replace': Hot-replaces a failed/stalled subagent, restoring the latest checkpoint to a new worker.
            - 'adopt': Adopts orphaned subagents whose parent agent crashed.
            - 'promote': Promotes a recurring high-reliability subagent role into a permanent Specialist Bot.
            - 'archetypes': Lists available specialist archetypes (Critic, Judge, Red-Team, Verifier, etc.).
        parent_agent_id: Identifier of the parent agent or supervisor.
        subagent_id: Unique identifier of the subagent (required for status, result, heartbeat, checkpoint, cancel, replace).
        objective: The delegated objective for 'spawn'.
        role: Specialist role or archetype (e.g. 'critic', 'judge', 'verifier', 'coder', 'researcher').
        instructions: Custom system prompt instructions for the subagent.
        progress_percent: Execution progress from 0.0 to 100.0 (for 'heartbeat').
        current_action: Description of the action currently executing (for 'heartbeat').
        last_tool: Name of the last tool called (for 'heartbeat').
        step_index: Integer index of the current execution step (for 'checkpoint').
        artifacts_json: JSON array of artifact paths generated (for 'checkpoint').
        reason: Rationale for replacement or cancellation.
        bot_name: Desired name for permanent bot profile (for 'promote').
    """
    lifecycle = get_subagent_lifecycle_manager()
    resilience = get_subagent_resilience_engine(lifecycle)
    promotion = get_subagent_promotion_manager()

    # Parse artifacts
    artifacts: list[str] = []
    if artifacts_json:
        try:
            parsed = json.loads(artifacts_json)
            if isinstance(parsed, list):
                artifacts = [str(x) for x in parsed]
        except Exception:
            artifacts = []

    # 1. ACTION: ARCHETYPES
    if action == "archetypes":
        lines = ["### 🏛️ Available Specialist Sub-Agent Archetypes"]
        for arch, tpl in ARCHETYPE_REGISTRY.items():
            lines.append(f"- **`{arch.value}`** ({tpl.role_title}): {tpl.description}")
            lines.append(f"  - Recommended Tools: `{', '.join(tpl.recommended_tools) or 'None'}`")
            lines.append(f"  - Model Tier: `{tpl.model_tier}` | Workspace: `{tpl.workspace_mode}`")
        return "\n".join(lines)

    # 2. ACTION: SPAWN
    if action == "spawn":
        if not objective.strip():
            return "Error: 'objective' is required to spawn a subagent."

        # Dynamically generate or load contract
        if role and role != "specialist":
            contract = generate_dynamic_role(f"{role}: {objective}")
        else:
            contract = generate_dynamic_role(objective)

        if instructions.strip():
            contract.instructions = instructions.strip()

        try:
            record = lifecycle.spawn_subagent(parent_agent_id=parent_agent_id, contract=contract)
            return (
                f"### 🚀 Sub-Agent Provisioned Asynchronously\n"
                f"- **Sub-Agent ID**: `{record.subagent_id}`\n"
                f"- **Parent Agent**: `{record.parent_agent_id}`\n"
                f"- **Role**: `{record.contract.role}`\n"
                f"- **Status**: `{record.status.value}`\n"
                f"- **Lease Expiry**: `{record.contract.lease_duration_seconds}s` (Renewed via heartbeats)\n"
                f"- **Workspace Mode**: `{record.contract.workspace_mode}`\n\n"
                f"The parent may continue execution immediately. Poll with `subagent_control(action='status', subagent_id='{record.subagent_id}')`."
            )
        except Exception as exc:
            return f"Failed to spawn subagent: {exc}"

    # Actions requiring subagent_id
    if action in ("status", "result", "heartbeat", "checkpoint", "resume", "cancel", "replace"):
        if not subagent_id.strip():
            return f"Error: 'subagent_id' is required for action '{action}'."

    # 3. ACTION: STATUS
    if action == "status":
        rec = lifecycle.get_subagent(subagent_id)
        if not rec:
            return f"Error: Subagent '{subagent_id}' not found."

        hb_str = "None"
        if rec.last_heartbeat:
            hb_str = f"{rec.last_heartbeat.progress_percent:.1f}% ({rec.last_heartbeat.current_action}) at {rec.last_heartbeat.timestamp}"

        return (
            f"### Sub-Agent Status: `{rec.subagent_id}`\n"
            f"- **Parent**: `{rec.parent_agent_id}` (Depth: {rec.depth})\n"
            f"- **Role**: `{rec.contract.role}`\n"
            f"- **Objective**: {rec.contract.objective}\n"
            f"- **Status**: `{rec.status.value}`\n"
            f"- **Lease Valid**: `{'YES' if rec.lease.is_valid() else 'EXPIRED'}` (Renewals: {rec.lease.renew_count})\n"
            f"- **Last Heartbeat**: {hb_str}\n"
            f"- **Children**: `{len(rec.children_ids)}`\n"
            f"- **Orphaned**: `{'YES' if rec.is_orphaned else 'NO'}`\n"
        )

    # 4. ACTION: RESULT
    if action == "result":
        rec = lifecycle.get_subagent(subagent_id)
        if not rec:
            return f"Error: Subagent '{subagent_id}' not found."
        if not rec.result:
            return f"Subagent '{subagent_id}' is currently in state '{rec.status.value}'. Result not yet finalized."

        return (
            f"### Sub-Agent Deliverable: `{rec.subagent_id}`\n"
            f"- **Status**: `{rec.result.status}`\n"
            f"- **Summary**: {rec.result.summary}\n"
            f"- **Artifacts**: `{', '.join(rec.result.artifacts) or 'None'}`\n"
            f"- **Confidence Score**: `{rec.result.confidence_score}`\n"
            f"- **Findings Count**: `{len(rec.result.findings)}`\n"
            f"- **Errors**: `{', '.join(rec.result.errors) or 'None'}`\n"
        )

    # 5. ACTION: HEARTBEAT
    if action == "heartbeat":
        ok = lifecycle.record_heartbeat(
            subagent_id=subagent_id,
            current_action=current_action or "executing",
            progress_percent=progress_percent,
            last_tool=last_tool or None,
        )
        if not ok:
            return f"Error: Failed to record heartbeat for '{subagent_id}' (not found or terminal)."
        return f"Heartbeat recorded for `{subagent_id}`. Lease extended. Progress: {progress_percent:.1f}%."

    # 6. ACTION: CHECKPOINT
    if action == "checkpoint":
        cp = resilience.save_checkpoint(
            subagent_id=subagent_id,
            step_index=step_index,
            intermediate_artifacts=artifacts,
            next_action=current_action,
        )
        return f"Checkpoint `{cp.checkpoint_id}` saved for `{subagent_id}` at step {step_index}."

    # 7. ACTION: RESUME
    if action == "resume":
        ok = lifecycle.resume_subagent(subagent_id)
        return f"Subagent `{subagent_id}` resumed: {ok}"

    # 8. ACTION: CANCEL
    if action == "cancel":
        ok = lifecycle.cancel_subagent(subagent_id, reason=reason)
        return f"Subagent `{subagent_id}` cancelled (and propagated down descendants): {ok}"

    # 9. ACTION: REPLACE
    if action == "replace":
        try:
            replacement = resilience.hot_replace_subagent(subagent_id, reason=reason)
            return (
                f"### 🔄 Hot-Replacement Completed\n"
                f"- **Failed Worker**: `{subagent_id}`\n"
                f"- **New Worker ID**: `{replacement.subagent_id}`\n"
                f"- **Status**: `{replacement.status.value}`\n"
                f"- **Restored Checkpoint**: Restored latest state and artifacts.\n"
            )
        except Exception as exc:
            return f"Failed to replace subagent: {exc}"

    # 10. ACTION: ADOPT
    if action == "adopt":
        adopted = resilience.adopt_orphaned_subagents(new_parent_id=parent_agent_id)
        return f"Supervisor `{parent_agent_id}` adopted {len(adopted)} orphaned subagents: {adopted}"

    # 11. ACTION: PROMOTE
    if action == "promote":
        target_role = role or "specialist"
        eligible, metrics = promotion.check_promotion_eligibility(target_role)
        if not eligible and not bot_name:
            return (
                f"Role '{target_role}' does not yet meet promotion thresholds.\n"
                f"Executions: {metrics.get('total_executions', 0)}/{metrics.get('required_executions', 5)}, "
                f"Reliability: {metrics.get('reliability', 0.0):.1%}/{metrics.get('required_reliability', 0.8):.1%}.\n"
                f"Specify `bot_name` to force promotion."
            )

        promoted_profile = promotion.promote_to_specialist_bot(role=target_role, bot_name=bot_name or None)
        return (
            f"### 🎖️ Promotion Successful: Sub-Agent -> Permanent Specialist Bot\n"
            f"- **Bot Name**: `{promoted_profile['name']}`\n"
            f"- **Display Name**: {promoted_profile['display_name']}\n"
            f"- **Role**: {promoted_profile['system_role']}\n"
            f"- **Origin**: {promoted_profile['origin']}\n"
            f"- **Permanent Profile**: Installed into bot registry."
        )

    return f"Unknown action '{action}'."
