"""Frontier AI-agent capability taxonomy.

A structured, code-grounded checklist of the capabilities a modern production
agent is expected to have, drawn from the 2026 landscape (Grok, OpenAI Operator,
Anthropic Claude, Google Gemini, Cursor, Devin, Manus, and the framework guides).

Each capability carries a ``probe``: a path (relative to the ``alpha`` package
root) whose existence indicates the capability is implemented. The taxonomy is
data, not code — extend it as the field moves.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Capability:
    """One entry in the frontier capability checklist."""

    key: str
    name: str
    category: str
    probe: str
    note: str = ""


TAXONOMY: tuple[Capability, ...] = (
    # -- Core runtime -------------------------------------------------
    Capability("core.runtime", "Agent runtime / control loop", "Core runtime", "runtime"),
    Capability("core.agents", "Agent assembly", "Core runtime", "agents"),
    Capability("core.models", "Model layer / providers", "Core runtime", "models"),
    Capability("core.config", "Config system", "Core runtime", "config"),
    Capability("core.client", "Embedded client", "Core runtime", "client.py"),
    # -- Planning & reasoning ----------------------------------------
    Capability("plan.planning", "Planning", "Planning & reasoning", "planning"),
    Capability("plan.reasoning", "Advanced reasoning (tree search / TTCS)", "Planning & reasoning", "reasoning"),
    Capability("plan.missions", "Goals & missions", "Planning & reasoning", "missions"),
    Capability("plan.decompose", "Task decomposition", "Planning & reasoning", "swarm"),
    Capability("plan.problem_model", "Problem modelling", "Planning & reasoning", "orchestration"),
    # -- Memory & knowledge ------------------------------------------
    Capability("mem.layered", "Layered memory", "Memory & knowledge", "agents/memory"),
    Capability("mem.backends", "Pluggable memory backends", "Memory & knowledge", "agents/memory/backends"),
    Capability("mem.knowledge", "Knowledge base", "Memory & knowledge", "knowledge"),
    Capability("mem.rag", "RAG / retrieval", "Memory & knowledge", "community/ragflow"),
    Capability("mem.learning", "Learning / reflection", "Memory & knowledge", "learning"),
    Capability("mem.context", "Context engineering / compaction", "Memory & knowledge", "context"),
    # -- Multi-agent & orchestration ---------------------------------
    Capability("mas.swarm", "Swarm / parallel agents", "Multi-agent", "swarm"),
    Capability("mas.orchestration", "Orchestration", "Multi-agent", "orchestration"),
    Capability("mas.subagents", "Subagent delegation", "Multi-agent", "subagents"),
    Capability("mas.bots", "Bot workforce", "Multi-agent", "bots"),
    Capability("mas.company", "Org / company structure", "Multi-agent", "company"),
    Capability("mas.groups", "Group chat / teams", "Multi-agent", "groups"),
    Capability("mas.council", "Council", "Multi-agent", "council"),
    Capability("mas.deliberation", "Debate & consensus", "Multi-agent", "deliberation"),
    Capability("mas.synthesis", "Speculative synthesis / best-of-N", "Multi-agent", "synthesis"),
    # -- Tools, MCP & integrations -----------------------------------
    Capability("tool.registry", "Tool registry", "Tools & integrations", "tools"),
    Capability("tool.mcp", "MCP (Model Context Protocol)", "Tools & integrations", "mcp"),
    Capability("tool.integrations", "Managed integrations", "Tools & integrations", "integrations"),
    Capability("tool.extensions", "Extension system", "Tools & integrations", "extensions"),
    Capability("tool.community", "Community tool packs", "Tools & integrations", "community"),
    # -- Computer use & browser --------------------------------------
    Capability("cu.computer_use", "Computer use (OS control)", "Computer use", "computer_use"),
    Capability("cu.browser", "Browser automation (CDP)", "Computer use", "browser"),
    Capability("cu.canvas", "Canvas / visual workspace", "Computer use", "canvas"),
    # -- Skills & extensibility --------------------------------------
    Capability("skill.skills", "Skills", "Skills & extensibility", "skills"),
    # -- Safety, policy & security -----------------------------------
    Capability("safety.guardrails", "Guardrails", "Safety & security", "guardrails"),
    Capability("safety.safety", "Safety layer", "Safety & security", "safety"),
    Capability("safety.policy", "Policy-as-code", "Safety & security", "policy"),
    Capability("safety.authz", "Authorization", "Safety & security", "authz"),
    Capability("safety.security", "Security", "Safety & security", "security"),
    Capability("safety.rules", "Rules engine", "Safety & security", "rules"),
    Capability("safety.governance", "Governance", "Safety & security", "governance"),
    Capability("safety.injection", "Prompt-injection defence", "Safety & security", "agents/middlewares/input_sanitization_middleware.py"),
    Capability("safety.pii", "PII redaction / DLP", "Safety & security", "models/moa/redact.py"),
    # -- Human-in-the-loop -------------------------------------------
    Capability("hitl.approval", "Human approval queue", "Human-in-the-loop", "projects/approval_queue.py"),
    Capability("hitl.action", "Action protocol / primitives", "Human-in-the-loop", "action"),
    Capability("hitl.workflow", "Durable workflow with approvals", "Human-in-the-loop", "workflow"),
    # -- Evaluation & benchmarks -------------------------------------
    Capability("eval.evaluation", "Evaluation harness", "Evaluation", "evaluation"),
    Capability("eval.benchmarks", "Benchmarks", "Evaluation", "benchmarks"),
    Capability("eval.critic", "Critic / verifier", "Evaluation", "critic"),
    Capability("eval.avo", "Autonomous optimisation", "Evaluation", "avo"),
    Capability("eval.evidence", "Evidence collection", "Evaluation", "evidence"),
    # -- Observability & diagnostics ---------------------------------
    Capability("obs.observability", "Observability", "Observability", "observability"),
    Capability("obs.otel", "OpenTelemetry spans", "Observability", "observability/span.py"),
    Capability("obs.diagnostics", "Diagnostics", "Observability", "diagnostics"),
    Capability("obs.ledger", "Audit ledger", "Observability", "ledger"),
    Capability("obs.lineage", "Lineage", "Observability", "lineage"),
    Capability("obs.ops", "Ops / integration health", "Observability", "ops"),
    # -- Self-improvement --------------------------------------------
    Capability("self.rsi", "Recursive self-improvement", "Self-improvement", "rsi"),
    Capability("self.evolution", "Evolution", "Self-improvement", "evolution"),
    Capability("self.repair", "Self-repair", "Self-improvement", "selfrepair"),
    Capability("self.metacognition", "Metacognition", "Self-improvement", "metacognition"),
    Capability("self.recovery", "Recovery", "Self-improvement", "recovery"),
    Capability("self.reproduction", "Reproduction / self-cloning", "Self-improvement", "reproduction"),
    # -- Autonomy & scheduling ---------------------------------------
    Capability("auto.scheduler", "Scheduler / cron", "Autonomy", "scheduler"),
    Capability("auto.perpetual", "Perpetual autonomy", "Autonomy", "perpetual"),
    Capability("auto.supervision", "Supervision loops", "Autonomy", "supervision"),
    Capability("auto.jobs", "Background jobs", "Autonomy", "jobs"),
    Capability("auto.kanban", "Kanban / work queue", "Autonomy", "kanban"),
    # -- Multimodal & voice ------------------------------------------
    Capability("mm.multimodal", "Multimodal (image/audio/video)", "Multimodal & voice", "multimodal"),
    Capability("mm.voice", "Voice / wakeword", "Multimodal & voice", "multimodal/wakeword.py"),
    Capability("mm.media", "Media generation", "Multimodal & voice", "media"),
    # -- Persistence & state -----------------------------------------
    Capability("state.persistence", "Persistence", "Persistence & state", "persistence"),
    Capability("state.state", "State / checkpoints", "Persistence & state", "state"),
    # -- Interfaces & channels ---------------------------------------
    Capability("iface.channels", "IM channels", "Interfaces", "channels"),
    Capability("iface.commands", "Slash commands", "Interfaces", "commands"),
    Capability("iface.streaming", "Streaming / SSE", "Interfaces", "streamjson"),
    # -- Federation & protocols --------------------------------------
    Capability("fed.peer", "Peer network / federation", "Federation", "peer_network"),
    Capability("fed.protocols", "Agent protocols (A2A)", "Federation", "protocols"),
    # -- Sandboxing ---------------------------------------------------
    Capability("sbx.sandbox", "Sandboxed execution", "Sandboxing", "sandbox"),
    # -- Enterprise ---------------------------------------------------
    Capability("ent.enterprise", "Enterprise controls", "Enterprise", "enterprise"),
    # -- Cost ---------------------------------------------------------
    Capability("cost.governor", "Cost / budget governance", "Cost", "models/cost_governor.py"),
    # -- Coding / editing --------------------------------------------
    Capability("code.coding", "Coding engine", "Coding & editing", "coding"),
    Capability("code.editing", "Structured editing", "Coding & editing", "editing"),
    Capability("code.debugging", "Debugging", "Coding & editing", "debugging"),
    # -- Research -----------------------------------------------------
    Capability("res.research", "Deep research", "Research", "research"),
)


CATEGORIES: tuple[str, ...] = tuple(dict.fromkeys(c.category for c in TAXONOMY))

__all__ = ["Capability", "CATEGORIES", "TAXONOMY"]
