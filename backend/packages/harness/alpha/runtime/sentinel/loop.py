"""The Sentinel loop: observe -> diagnose -> fix -> verify -> commit.

Every stage is bounded and reversible. The one rule that must never break:

    **A red or unknown verification result reverts and escalates. It never
    commits.**

Diagnosis is intentionally *routing only* — it classifies a signal and hands it
to an existing specialist (environment_auto_healer, self_healing_runner, deep
debugger subagent). It does not invent fixes.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alpha.runtime.sentinel.checkpoint import CheckpointManager
from alpha.runtime.sentinel.commit import CommitResult, Committer
from alpha.runtime.sentinel.signals import Signal, SignalTracker, dedupe, sort_by_severity
from alpha.runtime.sentinel.verify import Verifier, VerifyReport

logger = logging.getLogger(__name__)


def record_sentinel_escalation(outcome: LoopOutcome) -> LoopOutcome:
    """Publish one sentinel escalation into the global handoff ledger.

    Sentinel escalations used to be reachable only by reading the sentinel
    report journal, so an operator had to know which subsystem had given up
    before they could find the record. Sentinel has no typed failure code of
    its own — an unknown fault kind, a fix that raised, a verification that
    stayed red — so it publishes ``unknown`` (class ``permanent``: a human owns
    it) with the signal kind, fingerprint and stage in the details.
    """
    from alpha.bots.failure_reasons import UNKNOWN
    from alpha.runtime.escalation import DOMAIN_SENTINEL, escalate_to_human

    try:
        escalate_to_human(
            DOMAIN_SENTINEL,
            outcome.signal.fingerprint,
            f"sentinel:{outcome.signal.source or outcome.signal.kind}",
            reason=UNKNOWN,
            detail=f"sentinel stopped at {outcome.stage}: {outcome.detail}"[:2000],
            details={
                "kind": outcome.signal.kind,
                "stage": outcome.stage,
                "status": outcome.status,
                "fingerprint": outcome.signal.fingerprint,
                "message": outcome.signal.message[:500],
            },
        )
    except Exception:  # noqa: BLE001 - the outcome is already the record of truth
        logger.debug("Failed to record sentinel escalation", exc_info=True)
    return outcome

#: Signal kind -> repair strategy. Anything not listed escalates instead of
#: being guess-fixed.
KNOWN_KINDS: frozenset[str] = frozenset({
    "import_error",
    "syntax_error",
    "test_failure",
    "missing_file",
    "permission_error",
    "connectivity",
    # A UTF-8 .ps1 with non-ASCII content but no BOM. Windows PowerShell 5.1
    # decodes BOM-less scripts as ANSI, corrupting string literals so the file
    # cannot be parsed at all. Registered because the fix is purely mechanical
    # (prepend EF BB BF), is idempotent, and is verifiable by asking PowerShell
    # to parse the file.
    "missing_bom",
})


@dataclass
class LoopOutcome:
    signal: Signal
    stage: str                      # where it stopped
    status: str                     # "fixed" | "reverted" | "escalated" | "skipped"
    detail: str = ""
    commit: CommitResult | None = None
    verify: VerifyReport | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "fingerprint": self.signal.fingerprint,
            "kind": self.signal.kind,
            "stage": self.stage,
            "status": self.status,
            "detail": self.detail,
            "commit": self.commit.to_dict() if self.commit else None,
            "verify": self.verify.to_dict() if self.verify else None,
        }


class SentinelLoop:
    """Runs the repair loop over a batch of signals.

    ``fix_fn`` is injected rather than hard-coded. It is called as
    ``fix_fn(signal, snapshot)`` and returns the paths it modified — but it MUST
    call ``snapshot(paths)`` *before* editing them.

    The snapshot-before-edit ordering is the whole point. An earlier version
    snapshotted after the fix had already run, which captured the *broken*
    content, so a "revert" on verification failure restored the very state that
    failed. Revert has to be able to get back to the pre-fix state, and only the
    fix itself knows which files it is about to touch.
    """

    def __init__(
        self,
        repo_root: str | Path,
        *,
        fix_fn: Callable[[Signal], list[str]],
        verifier: Verifier | None = None,
        committer: Committer | None = None,
        tracker: SignalTracker | None = None,
        checkpoint_manager: CheckpointManager | None = None,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.fix_fn = fix_fn
        self.verifier = verifier or Verifier(self.repo_root)
        self.committer = committer or Committer(self.repo_root)
        self.tracker = tracker or SignalTracker()
        self.checkpoints = checkpoint_manager or CheckpointManager(self.repo_root)

    # -- DIAGNOSE -----------------------------------------------------------
    def diagnose(self, signal: Signal) -> str:
        """Classify, naming the rule. Returns the strategy, or "escalate"."""
        if signal.kind not in KNOWN_KINDS:
            return "escalate"
        return signal.kind

    # -- One full pass over a single signal ---------------------------------
    def handle(self, signal: Signal, *, verification_commands: dict[str, list[str]]) -> LoopOutcome:
        if not self.tracker.should_act(signal):
            return LoopOutcome(signal, "observe", "skipped",
                               "attempt cap or cooldown not satisfied")

        strategy = self.diagnose(signal)
        if strategy == "escalate":
            self.tracker.mark_attempt(signal)
            self.tracker.mark_outcome(signal, "escalated")
            return record_sentinel_escalation(
                LoopOutcome(
                    signal, "diagnose", "escalated",
                    f"unclassified fault kind {signal.kind!r} — not guess-fixed",
                )
            )

        self.tracker.mark_attempt(signal)

        # -- FIX (with a way back) ------------------------------------------
        # The fix snapshots the paths it is about to touch, then edits them.
        captured: dict[str, Any] = {}

        def snapshot(paths: list[str]) -> None:
            captured["checkpoint"] = self.checkpoints.create(list(paths))

        try:
            touched = list(self.fix_fn(signal, snapshot) or [])
        except Exception as exc:  # noqa: BLE001 - a raising fix must not kill the loop
            return record_sentinel_escalation(LoopOutcome(signal, "fix", "escalated", f"fix raised {type(exc).__name__}: {exc}"))

        if not touched:
            self.tracker.mark_outcome(signal, "escalated")
            return record_sentinel_escalation(LoopOutcome(signal, "fix", "escalated", "fix changed nothing"))

        checkpoint = captured.get("checkpoint")
        if checkpoint is None:
            # The fix edited files without snapshotting first. We cannot
            # guarantee a revert, so refuse to proceed rather than continue
            # with a repair we may be unable to undo.
            return record_sentinel_escalation(
                LoopOutcome(
                    signal, "fix", "escalated",
                    "fix did not snapshot before editing; refusing to continue without a way back",
                )
            )

        # -- VERIFY ---------------------------------------------------------
        report = self.verifier.run_all(verification_commands)

        if not report.passed:
            restored = self.checkpoints.restore(checkpoint)
            self.tracker.mark_outcome(signal, "failed")
            return LoopOutcome(
                signal, "verify", "reverted",
                f"verification failed ({report.summary()}); reverted {len(restored)} file(s)",
                verify=report,
            )

        # -- COMMIT ---------------------------------------------------------
        message = (
            f"fix({signal.kind}): automated repair by sentinel\n\n"
            f"Signal: {signal.message}\n"
            f"Fingerprint: {signal.fingerprint}\n"
            f"Source: {signal.source}\n"
            f"Verified: {report.summary()}\n"
        )
        result = self.committer.commit(touched, message)

        if not result.ok:
            self.checkpoints.restore(checkpoint)
            self.tracker.mark_outcome(signal, "failed")
            return LoopOutcome(
                signal, "commit", "reverted",
                f"commit failed ({result.error}); reverted",
                verify=report, commit=result,
            )

        self.tracker.reset(signal.fingerprint)
        return LoopOutcome(signal, "commit", "fixed", report.summary(),
                           verify=report, commit=result)

    # -- Batch --------------------------------------------------------------
    def run(
        self,
        signals: list[Signal],
        *,
        verification_commands: dict[str, list[str]],
        limit: int | None = None,
    ) -> list[LoopOutcome]:
        """Worst-first, deduped, optionally capped."""
        ordered = sort_by_severity(dedupe(signals))
        if limit is not None:
            ordered = ordered[:limit]
        return [self.handle(s, verification_commands=verification_commands) for s in ordered]
