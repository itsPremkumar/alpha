"""Deliberation Verifier: Enforces Truth-Seeking over Vote-Seeking.

Enforces the Truth-Seeking Hierarchy:
Deterministic Test / Tool Execution > External Source Verification > Independent Verifier > LLM Consensus

``code_test_command`` is EXECUTED for real (the old implementation merely
checked the string was non-empty and then stamped ``deterministic_pass`` —
a verification that never verified anything). Outcomes:

- exit 0            -> ``deterministic_pass`` (confidence bump, HIGH)
- non-zero exit      -> ``deterministic_fail`` (confidence penalty, LOW/CONTESTED)
- timeout / OSError  -> ``deterministic_timeout`` / ``deterministic_error`` (penalty)

Without a command the result stays honestly labelled ``consensus_supported``
(LLM-consensus tier) — not ``verified``, which the hierarchy reserves for
checks stronger than model agreement.
"""

from __future__ import annotations

import logging
import subprocess

from alpha.deliberation.models import (
    DeliberationConfidence,
    DeliberationResult,
)

logger = logging.getLogger(__name__)

#: Wall-clock ceiling for a caller-supplied deterministic check.
COMMAND_TIMEOUT_SECONDS = 300


class DeliberationVerifier:
    """Validates claims and calibrates confidence using the Verifier Hierarchy."""

    @classmethod
    def verify_and_calibrate(
        cls,
        result: DeliberationResult,
        code_test_command: str | None = None,
    ) -> DeliberationResult:
        """Applies contradiction detection and deterministic proof checks."""
        # 1. Deterministic code / test verification — actually run it.
        if code_test_command:
            return cls._run_deterministic_check(result, code_test_command)

        # 2. Contradiction Analysis
        has_contradictions = False
        if result.minority_dissent and "caution" in result.minority_dissent.lower():
            has_contradictions = True

        # 3. Confidence Calibration
        if has_contradictions and result.consensus_percentage < 75.0:
            result.confidence_level = DeliberationConfidence.CONTESTED
            result.confidence_score = max(0.50, result.confidence_score - 0.15)
            result.verification_status = "contested_claims_identified"
        else:
            # Honest tier label: model agreement is "consensus", not "verified".
            result.verification_status = "consensus_supported"

        return result

    @classmethod
    def _run_deterministic_check(cls, result: DeliberationResult, command: str) -> DeliberationResult:
        """Run ``command`` through the shell and grade the real outcome."""
        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            result.verification_status = "deterministic_timeout"
            result.confidence_score = max(0.30, result.confidence_score - 0.15)
            result.confidence_level = DeliberationConfidence.LOW_CONFIDENCE
            result.verdict_rationale = (
                (result.verdict_rationale + " ")
                + f"Deterministic check '{command}' exceeded {COMMAND_TIMEOUT_SECONDS}s and was killed."
            ).strip()
            return result
        except OSError as exc:
            result.verification_status = "deterministic_error"
            result.confidence_score = max(0.30, result.confidence_score - 0.15)
            result.confidence_level = DeliberationConfidence.LOW_CONFIDENCE
            result.verdict_rationale = (
                (result.verdict_rationale + " ")
                + f"Deterministic check '{command}' could not run: {exc}."
            ).strip()
            return result

        if proc.returncode == 0:
            # Code verification outranks LLM consensus.
            result.verification_status = "deterministic_pass"
            result.confidence_score = min(1.0, result.confidence_score + 0.08)
            result.confidence_level = DeliberationConfidence.HIGH_CONFIDENCE
            result.verdict_rationale = (
                (result.verdict_rationale + " ")
                + f"Deterministic check passed (exit 0): {command}"
            ).strip()
            return result

        tail = ((proc.stderr or "") + (proc.stdout or "")).strip()[-500:]
        result.verification_status = "deterministic_fail"
        result.confidence_score = max(0.20, result.confidence_score - 0.25)
        result.confidence_level = DeliberationConfidence.CONTESTED
        result.verdict_rationale = (
            (result.verdict_rationale + " ")
            + f"Deterministic check FAILED (exit {proc.returncode}): {command}. Output tail: {tail}"
        ).strip()
        logger.warning("Deterministic verification failed (exit %s): %s", proc.returncode, command)
        return result
