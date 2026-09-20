"""MissionCompiler: Translates natural language objectives into verified, structured missions."""

from __future__ import annotations

import logging
import re
from typing import Any

from alpha.mission.models import Mission, ProofObligation, RiskTier

logger = logging.getLogger(__name__)


class MissionCompiler:
    """Compiles ambiguous natural-language requests into structured, verifiable Mission contracts."""

    AMBIGUITY_PATTERNS = [
        r"^(?:please\s+)?(make|do|fix|improve|update|change|add|remove|create|build|implement)\s+(?:a|an|the)?\s*",
        r"(quickly|fast|asap|soon|later|eventually)",
        r"(something|anything|stuff|things)",
        r"(better|good|nice|great|awesome)",
    ]

    CONSTRAINT_KEYWORDS = {
        "hard": ["must", "required", "mandatory", "shall", "always", "strictly"],
        "soft": ["should", "prefer", "ideally", "would be nice", "optional"],
        "forbidden": ["never", "don't", "dont", "avoid", "must not", "prohibited", "do not"],
        "legal": ["license", "copyright", "legal", "compliance", "gdpr", "hipaa"],
        "ethical": ["ethical", "fair", "privacy", "transparent", "consent", "unbiased"],
        "physical": ["hardware", "device", "disk space", "memory limit", "gpu"],
    }

    def compile(self, raw_request: str, context: dict[str, Any] | None = None) -> Mission:
        """Execute full compilation pipeline on raw request."""
        clean_req = raw_request.strip()

        # 1. Interpret core intent
        intent = self._interpret_intent(clean_req)

        # 2. Detect latent needs (what user will need next)
        latent_needs = self._detect_latent_needs(clean_req)

        # 3. Extract constraints
        constraints = self._extract_constraints(clean_req)

        # 4. Assess risk tier
        risk_tier = self._assess_risk_tier(clean_req, constraints)

        # 5. Define acceptance criteria
        acceptance = self._define_acceptance_criteria(clean_req, risk_tier)

        # 6. Generate proof obligations
        obligations = self._generate_proof_obligations(clean_req, risk_tier)

        return Mission(
            raw_request=clean_req,
            interpreted_intent=intent,
            latent_needs=latent_needs,
            desired_outcome=f"Production-grade completion of '{intent}' with zero regressions.",
            risk_tier=risk_tier,
            constraints=constraints,
            acceptance_criteria=acceptance,
            proof_obligations=obligations,
            budget={"max_steps": 30, "timeout_seconds": 1800},
        )

    def _interpret_intent(self, text: str) -> str:
        cleaned = text.lower()
        for pat in self.AMBIGUITY_PATTERNS:
            cleaned = re.sub(pat, "", cleaned, flags=re.IGNORECASE)
        return cleaned.strip().capitalize() or text.strip()

    def _detect_latent_needs(self, text: str) -> str:
        t_low = text.lower()
        needs = []
        if "bug" in t_low or "fix" in t_low:
            needs.append("Regression test verifying the fix.")
        if "refactor" in t_low:
            needs.append("Backwards compatibility preservation for public API callers.")
        if "api" in t_low or "endpoint" in t_low:
            needs.append("Schema documentation and request validation.")
        if "perf" in t_low or "optimize" in t_low:
            needs.append("Empirical before/after benchmark measurements.")

        return " ".join(needs) if needs else "Standard verification suite and clean code documentation."

    def _extract_constraints(self, text: str) -> dict[str, list[str]]:
        constraints: dict[str, list[str]] = {
            "hard": [], "soft": [], "forbidden": [],
            "legal": [], "ethical": [], "physical": [],
        }
        sentences = re.split(r"[.;\n]", text.lower())

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue

            for ctype, kws in self.CONSTRAINT_KEYWORDS.items():
                if any(kw in sentence for kw in kws):
                    constraints[ctype].append(sentence)

        return constraints

    def _assess_risk_tier(self, text: str, constraints: dict[str, list[str]]) -> RiskTier:
        t_low = text.lower()

        # R5: Destructive irreversible operations
        if any(w in t_low for w in ["drop database", "drop table", "wipe", "rm -rf", "delete all", "purge", "force push"]):
            return RiskTier.R5

        # R6: Strategic security / identity / licensing
        if any(w in t_low for w in ["license rewrite", "security bypass", "core identity", "governance"]):
            return RiskTier.R6

        # R4: Significant side-effects
        if any(w in t_low for w in ["migration", "schema alter", "major refactor", "upgrade framework", "deploy"]):
            return RiskTier.R4

        # R0: Pure reasoning
        if any(w in t_low for w in ["why", "how come", "think through", "calculate", "compare"]):
            return RiskTier.R0

        # R1: Read-only exploration
        if any(w in t_low for w in ["explain", "read", "view", "search", "where is", "list", "show"]):
            return RiskTier.R1

        # R3: External low-impact
        if any(w in t_low for w in ["pip install", "npm install", "download docs", "fetch"]):
            return RiskTier.R3

        # Default R2: Reversible local work with git tracking
        return RiskTier.R2

    def _define_acceptance_criteria(self, text: str, risk_tier: RiskTier) -> list[str]:
        criteria = [
            f"Deliver functional implementation satisfying: '{text[:80]}'",
            "Ensure existing automated test suite passes without regressions.",
        ]
        if risk_tier in {RiskTier.R4, RiskTier.R5}:
            criteria.append("Validate rollback mechanism and data integrity checkpoints.")
        return criteria

    def _generate_proof_obligations(self, text: str, risk_tier: RiskTier) -> list[ProofObligation]:
        obligations = [
            ProofObligation(
                description="Workspace modifications verified via non-empty git diff.",
                verification_type="diff_non_empty",
            ),
            ProofObligation(
                description="Automated tests executed and passed cleanly.",
                verification_type="test_pass",
            ),
        ]
        if risk_tier in {RiskTier.R5, RiskTier.R6}:
            obligations.append(
                ProofObligation(
                    description="Explicit human gate authorization required for high-risk operation.",
                    verification_type="manual_approval",
                )
            )
        return obligations
