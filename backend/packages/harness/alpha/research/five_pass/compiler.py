from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class SearchPassType(StrEnum):
    DISCOVERY = "discovery"
    SPECIFIC_EVIDENCE = "specific_evidence"
    ADVERSARIAL_CONTRADICTION = "adversarial_contradiction"
    FACT_VERIFICATION = "fact_verification"
    STRATEGIC_SYNTHESIS = "strategic_synthesis"


@dataclass
class CompiledSearchLane:
    """A specialized search lane within the 5-Pass Search Superintelligence."""

    pass_type: SearchPassType
    query: str
    purpose: str
    filters: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pass_type": self.pass_type.value,
            "query": self.query,
            "purpose": self.purpose,
            "filters": self.filters,
        }


@dataclass
class FivePassSearchPlan:
    """Compiled 5-pass search plan ready for parallel execution."""

    original_question: str
    lanes: list[CompiledSearchLane] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_question": self.original_question,
            "lanes_count": len(self.lanes),
            "lanes": [lane.to_dict() for lane in self.lanes],
            "created_at": self.created_at,
        }


class FivePassSearchCompiler:
    """
    Compiles a single natural language question into 5 parallel search facets,
    explicitly constructing an adversarial contradiction search to falsify assumptions.
    """

    @staticmethod
    def compile(
        question: str,
        domain_context: str = "software engineering",
        year_hint: int | None = None,
    ) -> FivePassSearchPlan:
        q_clean = question.strip()
        effective_year = year_hint or time.localtime().tm_year

        # 1. Discovery Lane
        lane_disc = CompiledSearchLane(
            pass_type=SearchPassType.DISCOVERY,
            query=f"{q_clean} overview architecture {effective_year}",
            purpose="Broad landscape survey and structural concepts",
        )

        # 2. Specific Evidence Lane
        lane_evid = CompiledSearchLane(
            pass_type=SearchPassType.SPECIFIC_EVIDENCE,
            query=f"{q_clean} official documentation API specification guide",
            purpose="Concrete implementation details, function signatures, and standards",
        )

        # 3. Adversarial Contradiction Lane (The Flagship Falsification Query)
        lane_contra = CompiledSearchLane(
            pass_type=SearchPassType.ADVERSARIAL_CONTRADICTION,
            query=f"{q_clean} (issues OR bugs OR limitations OR deprecation OR failure OR memory leak)",
            purpose="Actively search for disconfirming evidence, bottlenecks, and edge-case failures",
        )

        # 4. Fact Verification Lane
        lane_fact = CompiledSearchLane(
            pass_type=SearchPassType.FACT_VERIFICATION,
            query=f"{q_clean} benchmark results empirical evaluation verified",
            purpose="Grounded empirical measurements and verified evidence",
        )

        # 5. Strategic Synthesis Lane
        lane_strat = CompiledSearchLane(
            pass_type=SearchPassType.STRATEGIC_SYNTHESIS,
            query=f"{q_clean} vs alternatives comparison trade-offs production best practices",
            purpose="Comparative analysis and architectural trade-off evaluation",
        )

        return FivePassSearchPlan(
            original_question=q_clean,
            lanes=[lane_disc, lane_evid, lane_contra, lane_fact, lane_strat],
        )
