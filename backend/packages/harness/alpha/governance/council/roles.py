from __future__ import annotations

import logging
from typing import Any

from .models import (
    CONFIDENCE_METHOD_EVIDENCE,
    CONFIDENCE_METHOD_HEURISTIC,
    CONFIDENCE_METHOD_UNVERIFIED,
    NEUTRAL_CONFIDENCE_BASELINE,
    DeliberatorVote,
    VoteVerdict,
)

logger = logging.getLogger("alpha.governance.council.roles")


class IndependentCritic:
    """Deliberator 1: Actively stress-tests assumptions, searches for edge cases, and flags vulnerabilities."""
    name = "IndependentCritic"
    role = "Critic"

    def evaluate(self, artifact_name: str, content: str, metadata: dict[str, Any]) -> DeliberatorVote:
        concerns: list[str] = []
        modifications: list[str] = []

        # Check for unhandled edge cases or missing recovery
        content_lower = content.lower()
        if "error" not in content_lower and "exception" not in content_lower and "fail" not in content_lower:
            concerns.append("Artifact lacks explicit error handling, failure recovery, or fallback strategies.")
            modifications.append("Document error modes and retry/recovery behavior.")

        if "todo" in content_lower or "fixme" in content_lower:
            concerns.append("Unfinished placeholders or TODOs present in artifact.")
            modifications.append("Resolve all placeholder stubs before certification.")

        # Verdict determination
        if len(concerns) >= 2:
            verdict = VoteVerdict.REJECT
            confidence = 0.85
            reasoning = "Critical resilience gaps detected: missing failure modes and incomplete placeholders."
        elif len(concerns) == 1:
            verdict = VoteVerdict.CONDITIONAL
            confidence = 0.75
            reasoning = f"Conditional approval: {concerns[0]}"
        else:
            verdict = VoteVerdict.APPROVE
            confidence = 0.90
            reasoning = "No obvious logical flaws, unhandled edge cases, or assumption traps detected."

        # Disclosed heuristic: the number is an uncalibrated rule score, not a
        # measured probability of correctness.
        note = (
            "Heuristic confidence: uncalibrated rule-based score from a deterministic "
            "keyword scan (error-handling coverage, TODO/FIXME placeholders); not a "
            "measured probability."
        )

        return DeliberatorVote(
            deliberator_name=self.name,
            role=self.role,
            verdict=verdict,
            confidence=confidence,
            reasoning=reasoning,
            concerns=concerns,
            required_modifications=modifications,
            confidence_method=CONFIDENCE_METHOD_HEURISTIC,
            confidence_note=note,
        )


class InvariantVerifier:
    """Deliberator 2: Audits claims against Ground Truth facts, test outputs, and invariant rules."""
    name = "InvariantVerifier"
    role = "InvariantVerifier"

    def evaluate(self, artifact_name: str, content: str, metadata: dict[str, Any]) -> DeliberatorVote:
        concerns: list[str] = []
        modifications: list[str] = []

        # Only trust execution evidence the caller actually supplied. A missing
        # key means "no run happened" — never an assumed pass (the previous
        # .get(..., True) defaults fabricated a passing check and then reported
        # a confident 0.95 on top of it).
        evidence_bits: list[str] = []

        if "test_passed" in metadata:
            test_passed = bool(metadata["test_passed"])
            evidence_bits.append(f"test_passed={test_passed}")
            if not test_passed:
                concerns.append("Ground truth test run reported failure.")
                modifications.append("Fix failing tests and achieve a passing run.")

        if "exit_code" in metadata:
            exit_code = metadata["exit_code"]
            evidence_bits.append(f"exit_code={exit_code}")
            if exit_code != 0:
                concerns.append(f"Ground truth execution check failed (exit code: {exit_code}).")
                modifications.append("Fix failing tests and achieve exit code 0.")

        if "has_tests" in metadata:
            has_tests = bool(metadata["has_tests"])
            evidence_bits.append(f"has_tests={has_tests}")
            if not has_tests and ("def " in content or "function" in content):
                concerns.append("Executable code produced without corresponding test coverage.")
                modifications.append("Attach automated tests proving correctness.")

        if not evidence_bits:
            # Nothing real backs a confidence here: hold the disclosed neutral
            # baseline and say so, instead of fabricating a "verified" 0.95.
            verdict = VoteVerdict.APPROVE
            confidence = NEUTRAL_CONFIDENCE_BASELINE
            method = CONFIDENCE_METHOD_UNVERIFIED
            note = (
                "No test results or exit-code evidence were supplied; confidence is held "
                "at the neutral unverified baseline (0.5) — an honest 'unknown', not a "
                "measured confidence."
            )
            reasoning = (
                "No execution evidence supplied; invariants were not verified. "
                "Approval is procedural, not evidence-backed."
            )
        elif concerns:
            verdict = VoteVerdict.REJECT
            confidence = 0.95
            method = CONFIDENCE_METHOD_EVIDENCE
            note = (
                f"Rule-based score over supplied execution evidence "
                f"({', '.join(evidence_bits)}); failing checks drive this verdict. "
                "Not a calibrated probability."
            )
            reasoning = "Invariant violation: factual verification or test checks failed."
        else:
            verdict = VoteVerdict.APPROVE
            confidence = 0.95
            method = CONFIDENCE_METHOD_EVIDENCE
            note = (
                f"Rule-based score over supplied execution evidence "
                f"({', '.join(evidence_bits)}); not a calibrated probability."
            )
            reasoning = "Supplied execution evidence (tests/exit code) shows no invariant violation."

        return DeliberatorVote(
            deliberator_name=self.name,
            role=self.role,
            verdict=verdict,
            confidence=confidence,
            reasoning=reasoning,
            concerns=concerns,
            required_modifications=modifications,
            confidence_method=method,
            confidence_note=note,
        )


class SecurityReviewer:
    """Deliberator 3: Audits blast radius, permission violations, secret leakage risks, and command dangers."""
    name = "SecurityReviewer"
    role = "SecurityReviewer"

    def evaluate(self, artifact_name: str, content: str, metadata: dict[str, Any]) -> DeliberatorVote:
        concerns: list[str] = []
        modifications: list[str] = []

        dangerous_patterns = [
            ("rm -rf", "Destructive recursive delete command detected."),
            ("push --force", "Destructive force push command detected."),
            ("eval(", "Arbitrary code execution via eval() detected."),
            ("password=", "Potential plain-text credential hardcoded."),
            ("api_key=", "Potential plain-text API key hardcoded."),
            ("secret=", "Potential plain-text secret token hardcoded."),
        ]

        for pat, desc in dangerous_patterns:
            if pat in content.lower():
                concerns.append(desc)
                modifications.append(f"Remove dangerous pattern '{pat}' and use secret vault or safe abstractions.")

        if concerns:
            verdict = VoteVerdict.REJECT
            confidence = 0.98
            reasoning = f"Security hazard detected: {'; '.join(concerns)}"
        else:
            verdict = VoteVerdict.APPROVE
            confidence = 0.90
            reasoning = "No high-risk commands, credential leaks, or blast-radius violations identified."

        # Disclosed heuristic: an approve only means "no listed pattern matched"
        # — it does not prove the artifact is secure.
        note = (
            "Heuristic confidence: uncalibrated rule-based score from a fixed "
            "dangerous-pattern scan (rm -rf, force push, eval, hardcoded secrets); "
            "an approve means 'no listed pattern matched', not that the artifact is "
            "secure. Not a measured probability."
        )

        return DeliberatorVote(
            deliberator_name=self.name,
            role=self.role,
            verdict=verdict,
            confidence=confidence,
            reasoning=reasoning,
            concerns=concerns,
            required_modifications=modifications,
            confidence_method=CONFIDENCE_METHOD_HEURISTIC,
            confidence_note=note,
        )


class QualityReviewer:
    """Deliberator 4: Checks maintainability, clean design, type annotations, and documentation."""
    name = "QualityReviewer"
    role = "QualityReviewer"

    def evaluate(self, artifact_name: str, content: str, metadata: dict[str, Any]) -> DeliberatorVote:
        concerns: list[str] = []
        modifications: list[str] = []

        if len(content.strip()) < 30:
            concerns.append("Artifact is too brief or incomplete to evaluate meaningfully.")
            modifications.append("Provide substantive implementation or documentation details.")

        # Check Python docstrings if python file
        if artifact_name.endswith(".py"):
            if "def " in content and '"""' not in content and "'''" not in content:
                concerns.append("Functions defined without docstrings or type signatures.")
                modifications.append("Add docstrings and type annotations to all public functions.")

        if len(concerns) >= 2:
            verdict = VoteVerdict.REJECT
            confidence = 0.80
            reasoning = "Quality standards not met: lacking documentation and substantive details."
        elif len(concerns) == 1:
            verdict = VoteVerdict.CONDITIONAL
            confidence = 0.85
            reasoning = f"Conditional: {concerns[0]}"
        else:
            verdict = VoteVerdict.APPROVE
            confidence = 0.90
            reasoning = "Code and document structure adhere to clean architecture standards."

        # Disclosed heuristic: structural checks only; no calibration data exists.
        note = (
            "Heuristic confidence: uncalibrated rule-based score from structural checks "
            "(artifact length, docstrings for .py files); not a measured probability."
        )

        return DeliberatorVote(
            deliberator_name=self.name,
            role=self.role,
            verdict=verdict,
            confidence=confidence,
            reasoning=reasoning,
            concerns=concerns,
            required_modifications=modifications,
            confidence_method=CONFIDENCE_METHOD_HEURISTIC,
            confidence_note=note,
        )


class PresidingJudge:
    """Deliberator 5: Evaluates the whole picture, aggregates the 4 specialist votes, and calculates quorum."""
    name = "PresidingJudge"
    role = "PresidingJudge"

    def evaluate_synthesis(
        self,
        artifact_name: str,
        content: str,
        specialist_votes: list[DeliberatorVote],
    ) -> DeliberatorVote:
        reject_count = sum(1 for v in specialist_votes if v.verdict == VoteVerdict.REJECT)
        security_veto = any(v.role == "SecurityReviewer" and v.verdict == VoteVerdict.REJECT for v in specialist_votes)
        invariant_veto = any(v.role == "InvariantVerifier" and v.verdict == VoteVerdict.REJECT for v in specialist_votes)

        concerns: list[str] = []
        modifications: list[str] = []

        if security_veto:
            concerns.append("Security veto upheld: unsafe operation cannot be approved.")
            verdict = VoteVerdict.REJECT
            confidence = 0.99
            reasoning = "Presiding Judge upholds Security Reviewer veto."
        elif invariant_veto:
            concerns.append("Invariant verification veto upheld: test/correctness check failed.")
            verdict = VoteVerdict.REJECT
            confidence = 0.99
            reasoning = "Presiding Judge upholds Invariant Verifier veto."
        elif reject_count >= 2:
            concerns.append(f"Multiple specialist rejections ({reject_count}) preclude certification.")
            verdict = VoteVerdict.REJECT
            confidence = 0.90
            reasoning = "Majority or substantial dissent among specialists."
        elif any(v.verdict == VoteVerdict.CONDITIONAL for v in specialist_votes):
            verdict = VoteVerdict.CONDITIONAL
            confidence = 0.85
            reasoning = "Conditional consensus: minor concerns must be addressed prior to deployment."
        else:
            verdict = VoteVerdict.APPROVE
            confidence = 0.95
            reasoning = "Consensus achieved across all council deliberators."

        # Disclosed heuristic: rule-based synthesis over the specialist votes.
        note = (
            "Heuristic confidence: uncalibrated rule-based synthesis over the 4 "
            "specialist votes (veto and dissent counts); not a measured probability."
        )

        return DeliberatorVote(
            deliberator_name=self.name,
            role=self.role,
            verdict=verdict,
            confidence=confidence,
            reasoning=reasoning,
            concerns=concerns,
            required_modifications=modifications,
            confidence_method=CONFIDENCE_METHOD_HEURISTIC,
            confidence_note=note,
        )
