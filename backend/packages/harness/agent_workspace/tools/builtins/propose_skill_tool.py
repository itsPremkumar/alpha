"""Agent skill-proposal tool (Hermes /learn pattern, governed).

Propose a new skill as SKILL.md markdown. The proposal is statically scanned
BEFORE anything is stored: blockers fail closed without persisting. Stored
proposals wait in the proposer's personal queue for admin review in Settings;
nothing proposed is ever discovered, activated, or executed.
"""

from __future__ import annotations

import asyncio
import logging

from langchain.tools import tool

from agent_workspace.runtime.user_context import resolve_runtime_user_id
from agent_workspace.skills.proposals import (
    SkillProposalStore,
    finding_summary,
    proposals_root,
    scan_proposal_markdown,
    validate_proposal_content,
    validate_proposal_name,
)
from agent_workspace.tools.types import Runtime

logger = logging.getLogger(__name__)


@tool("propose_skill", parse_docstring=True)
async def propose_skill_tool(
    runtime: Runtime,
    name: str,
    skill_md: str,
    description: str = "",
) -> str:
    """Propose a new skill for human review and installation.

    Use this when repeated work deserves a reusable skill, or the user asks
    to save a workflow as one. The proposal is security-scanned immediately:
    blocked content is rejected without storing anything. Approved proposals
    are installed into custom skills by an admin; nothing proposed here takes
    effect on its own.

    Args:
        name: Skill name: 1-64 chars, lowercase letters/digits/hyphens (e.g. 'pdf-tables').
        skill_md: Complete SKILL.md markdown (UTF-8, max 64KB).
        description: Short description of what the skill does (max 500 chars).
    """
    try:
        user_id = resolve_runtime_user_id(runtime)
    except Exception as exc:
        return f"Error: cannot resolve calling user: {exc}"
    try:
        validate_proposal_name(name)
        skill_md, description = validate_proposal_content(skill_md, description)
    except ValueError as exc:
        return f"Error: {exc}"
    try:
        findings = await asyncio.to_thread(scan_proposal_markdown, name, skill_md)
    except Exception as exc:
        # Includes scanner blockers: fail closed, persist nothing.
        logger.warning("Skill proposal '%s' blocked at scan: %s", name, exc)
        return f"Error: skill proposal blocked by the security scan: {exc}"
    try:
        store = SkillProposalStore(proposals_root())
        proposal = await asyncio.to_thread(store.create, user_id, name, description, skill_md, findings)
    except ValueError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        logger.warning("Skill proposal storage failed for '%s': %s", name, exc)
        return f"Error: could not store the skill proposal: {exc}"
    summary = finding_summary(findings)
    notes = ", ".join(f"{count} {severity}" for severity, count in summary.items() if count) or "no"
    return f"Skill proposal '{name}' recorded (id {proposal.id}) with {notes} scan findings. An admin reviews proposals in Settings before anything is installed; the proposal takes no effect until approved."
