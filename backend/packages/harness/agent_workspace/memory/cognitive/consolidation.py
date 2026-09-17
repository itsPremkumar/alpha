"""Memory Consolidation, Sleep/Dream Reflection & Decay Engine.

Implements 3-phase biological sleep consolidation (Light Sleep, REM, Deep Sleep),
Ebbinghaus forgetting curve decay, salience scoring, associative clustering,
and belief conflict reconciliation.
"""

from __future__ import annotations

import math
import re
import time
import uuid
from typing import TYPE_CHECKING, Any

from agent_workspace.memory.cognitive.models import (
    BeliefStatus,
    CognitiveTier,
    ConsolidationReport,
    TraceOutcome,
)

if TYPE_CHECKING:
    from agent_workspace.memory.cognitive.associative_memory import AssociativeNetwork
    from agent_workspace.memory.cognitive.episodic_memory import EpisodicMemoryEngine
    from agent_workspace.memory.cognitive.procedural_memory import ProceduralSkillMemory
    from agent_workspace.memory.cognitive.semantic_graph import SemanticBeliefGraph
    from agent_workspace.memory.cognitive.working_memory import WorkingMemoryEngine


class CognitiveConsolidationEngine:
    """Orchestrates 3-Phase Sleep/Dream consolidation, forgetting curve decay, and belief reconciliation."""

    def __init__(
        self,
        base_stability_seconds: float = 86400.0,  # 1 day base memory stability
        forgetting_threshold: float = 0.20,
    ) -> None:
        self.base_stability_seconds = base_stability_seconds
        self.forgetting_threshold = forgetting_threshold
        self._reports: list[ConsolidationReport] = []

    def run_consolidation_cycle(
        self,
        working_mem: WorkingMemoryEngine,
        episodic_mem: EpisodicMemoryEngine,
        semantic_graph: SemanticBeliefGraph,
        procedural_mem: ProceduralSkillMemory,
        assoc_net: AssociativeNetwork,
    ) -> ConsolidationReport:
        """Run full 3-Phase Sleep/Dream Consolidation Cycle."""
        cycle_id = f"dream_{uuid.uuid4().hex[:8]}"
        now = time.time()
        insights: list[dict[str, Any]] = []

        # =========================================================================
        # Phase 1: Light Sleep — Ingestion, Normalization, Noise Pruning & Dedup
        # =========================================================================
        # 1. Decay working memory and collect salient items
        decayed_wm = working_mem.decay_all(current_time=now)
        salient_wm = working_mem.get_salient_for_promotion(min_salience=0.60)
        recent_traces = episodic_mem.list_traces(limit=100)

        light_sleep_pruned = decayed_wm
        seen_texts: set[str] = set()
        clean_signals: list[dict[str, Any]] = []

        for wm_item in salient_wm:
            norm = re.sub(r"\s+", " ", wm_item.content.lower().strip())
            if norm and norm not in seen_texts:
                seen_texts.add(norm)
                clean_signals.append({
                    "content": wm_item.content,
                    "tag": wm_item.context_tag,
                    "salience": wm_item.salience,
                    "source": "working_memory",
                    "id": wm_item.item_id,
                })

        for trace in recent_traces:
            combined = f"{trace.action} -> {trace.observation}"
            norm = re.sub(r"\s+", " ", combined.lower().strip())
            if norm and norm not in seen_texts:
                seen_texts.add(norm)
                clean_signals.append({
                    "content": combined,
                    "action": trace.action,
                    "observation": trace.observation,
                    "outcome": trace.outcome,
                    "error": trace.error_context,
                    "salience": trace.salience,
                    "source": "episodic_trace",
                    "id": trace.trace_id,
                })

        # =========================================================================
        # Phase 2: REM Sleep — Associative Clustering & Pattern Synthesis
        # =========================================================================
        rem_patterns_discovered = 0
        failure_signals = [s for s in clean_signals if s.get("outcome") == TraceOutcome.FAILURE or s.get("error")]

        # Cluster failures into procedural skills or lessons
        if failure_signals:
            error_groups: dict[str, list[dict[str, Any]]] = {}
            for fs in failure_signals:
                err_key = (fs.get("action") or "general").split()[0].lower()
                error_groups.setdefault(err_key, []).append(fs)

            for key, group in error_groups.items():
                if len(group) >= 1:
                    rem_patterns_discovered += 1
                    pitfalls = [g.get("error") or g["content"] for g in group[:3]]
                    insight = {
                        "type": "failure_pattern",
                        "topic": key,
                        "count": len(group),
                        "summary": f"Detected recurring execution hurdles in operations starting with '{key}'.",
                        "pitfalls": pitfalls,
                    }
                    insights.append(insight)

                    # Synthesize candidate procedural skill avoidance
                    skill_name = f"guard_against_{key}_fault"
                    procedural_mem.register_skill(
                        name=skill_name,
                        description=f"Automated defensive guardrail against repeated errors in {key}",
                        trigger_pattern=rf"\b{re.escape(key)}\b",
                        preconditions=[f"Verify environment state before executing {key}"],
                        steps=[f"Run precondition check for {key}", "Execute guarded command with retry handling"],
                        postconditions=["Validate non-empty exit code and clean output"],
                    )

        # Cross-tier associative link creation
        for i in range(min(len(clean_signals), 10)):
            for j in range(i + 1, min(len(clean_signals), 10)):
                s1, s2 = clean_signals[i], clean_signals[j]
                w = 0.35 + 0.3 * (s1["salience"] + s2["salience"]) / 2
                assoc_net.link_memories(
                    source_tier="episodic_flat" if s1["source"] == "episodic_trace" else "working",
                    source_id=s1["id"],
                    target_tier="episodic_flat" if s2["source"] == "episodic_trace" else "working",
                    target_id=s2["id"],
                    initial_weight=min(1.0, w),
                )

        # =========================================================================
        # Phase 3: Deep Sleep — Structural Belief Crystallization & Decay Gating
        # =========================================================================
        deep_sleep_crystallized = 0

        # Promote high salience items to Semantic Fact Graph
        for signal in clean_signals:
            if signal["salience"] >= 0.70:
                content = signal["content"]
                # Extract simple triple heuristic: subject, predicate, object
                parts = content.split("is", 1) if " is " in content else content.split(":", 1)
                if len(parts) == 2:
                    sub = parts[0].strip()
                    pred = "is" if " is " in content else "configured_as"
                    obj = parts[1].strip()
                else:
                    sub = "system_experience"
                    pred = "demonstrated"
                    obj = content[:120]

                node = semantic_graph.add_belief(
                    subject=sub,
                    predicate=pred,
                    object_val=obj,
                    confidence=0.85,
                    salience=signal["salience"],
                    evidence=[signal["id"]],
                    tags=["crystallized_dream"],
                )
                deep_sleep_crystallized += 1

                # Link new semantic belief to source
                s_tier = (
                    CognitiveTier.EPISODIC_FLAT
                    if signal["source"] == "episodic_trace"
                    else CognitiveTier.WORKING
                )
                assoc_net.link_memories(
                    source_tier=s_tier,
                    source_id=signal["id"],
                    target_tier=CognitiveTier.SEMANTIC_FACT,
                    target_id=node.node_id,
                    initial_weight=0.9,
                )

        # Epistemic Conflict Reconciliation
        conflicts_resolved = semantic_graph.reconcile_conflicts()

        # Ebbinghaus forgetting curve decay over existing active semantic nodes
        decayed_count = 0
        for node in semantic_graph.list_nodes():
            if node.status != BeliefStatus.ACTIVE or "pinned" in node.tags:
                continue

            # Retention probability: R = exp(-dt / (S * (1 + ln(1 + n))))
            dt = max(0.0, now - node.last_accessed_at)
            stability = self.base_stability_seconds * (1.0 + math.log(1.0 + node.access_count))
            retention = math.exp(-dt / max(1.0, stability))

            if retention < self.forgetting_threshold and node.salience < 0.75:
                node.status = BeliefStatus.DEPRECATED
                decayed_count += 1

        summary = (
            f"Consolidation Dream Cycle [{cycle_id}] completed: "
            f"Pruned {light_sleep_pruned} noisy signals (Light Sleep), "
            f"Synthesized {rem_patterns_discovered} behavioral patterns (REM), "
            f"Crystallized {deep_sleep_crystallized} beliefs & resolved {conflicts_resolved} conflicts (Deep Sleep). "
            f"Decayed {decayed_count} dormant items."
        )

        report = ConsolidationReport(
            cycle_id=cycle_id,
            timestamp=now,
            light_sleep_pruned=light_sleep_pruned,
            rem_sleep_patterns_discovered=rem_patterns_discovered,
            deep_sleep_beliefs_crystallized=deep_sleep_crystallized,
            conflicts_reconciled=conflicts_resolved,
            skills_indexed=len(failure_signals),
            decayed_items_count=decayed_count,
            insights=insights,
            summary=summary,
        )
        self._reports.append(report)
        return report

    def get_latest_report(self) -> ConsolidationReport | None:
        return self._reports[-1] if self._reports else None

    def list_reports(self, limit: int = 10) -> list[ConsolidationReport]:
        return list(reversed(self._reports))[:limit]
