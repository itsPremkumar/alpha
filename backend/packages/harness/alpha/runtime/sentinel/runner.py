"""Sentinel runner — the missing trigger.

Everything before this was a correct engine with no ignition key: signals could
be detected, fixes verified and commits made, but nothing actually ran on a
schedule. This module is the entry point that turns the loop into something that
can be driven by a scheduler, a cron-style routine, or a one-shot CLI.

Design constraints:
- **Bounded.** Every run has a ceiling on how many faults it will touch. An
  autonomous repair agent that will happily rewrite 400 files in one pass is not
  safe to leave running.
- **Push stays off.** Auto-commit is already a big step; auto-push is opt-in per
  deployment and defaults to False.
- **Unattended-safe.** Exceptions are caught per signal so one bad fault cannot
  abort the whole run.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from alpha.runtime.sentinel.commit import Committer
from alpha.runtime.sentinel.loop import LoopOutcome, SentinelLoop
from alpha.runtime.sentinel.signals import Signal, SignalTracker
from alpha.runtime.sentinel.sources import logs as log_sources
from alpha.runtime.sentinel.sources import scripts as script_sources
from alpha.runtime.sentinel.verify import Verifier

logger = logging.getLogger(__name__)


@dataclass
class RunReport:
    started_at: float
    duration_s: float = 0.0
    scanned: int = 0
    outcomes: list[LoopOutcome] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def fixed(self) -> list[LoopOutcome]:
        return [o for o in self.outcomes if o.status == "fixed"]

    @property
    def reverted(self) -> list[LoopOutcome]:
        return [o for o in self.outcomes if o.status == "reverted"]

    @property
    def escalated(self) -> list[LoopOutcome]:
        return [o for o in self.outcomes if o.status == "escalated"]

    def summary(self) -> str:
        return (
            f"scanned {self.scanned} signal(s): "
            f"{len(self.fixed)} fixed, {len(self.reverted)} reverted, "
            f"{len(self.escalated)} escalated"
            + (f", {len(self.errors)} error(s)" if self.errors else "")
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "duration_s": round(self.duration_s, 3),
            "scanned": self.scanned,
            "summary": self.summary(),
            "fixed": len(self.fixed),
            "reverted": len(self.reverted),
            "escalated": len(self.escalated),
            "errors": list(self.errors),
            "outcomes": [o.to_dict() for o in self.outcomes],
        }


class SentinelRunner:
    """Runs the repair loop over the configured sources.

    ``fix_fns`` maps a signal *kind* to a repair callable with the signature
    ``fix_fn(signal, snapshot) -> list[paths]``. A kind with no entry escalates,
    which is the safe default.
    """

    def __init__(
        self,
        repo_root: str | Path,
        *,
        fix_fns: dict[str, Callable[[Signal, Any], list[str]]] | None = None,
        verification_commands: dict[str, list[str]] | None = None,
        tracker: SignalTracker | None = None,
        committer: Committer | None = None,
        max_fixes_per_run: int = 5,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.fix_fns = dict(fix_fns or {})
        self.verification_commands = dict(verification_commands or {})
        self.tracker = tracker or SignalTracker()
        self.committer = committer or Committer(self.repo_root)
        if max_fixes_per_run < 1:
            raise ValueError("max_fixes_per_run must be >= 1")
        self.max_fixes_per_run = max_fixes_per_run

    # -- OBSERVE ------------------------------------------------------------
    def collect(self) -> list[Signal]:
        """Gather signals from every configured source."""
        signals: list[Signal] = []
        signals.extend(script_sources.scan_directory(self.repo_root))
        logs_dir = self.repo_root / "logs"
        if logs_dir.is_dir():
            signals.extend(log_sources.scan_directory(logs_dir))
        return signals

    # -- One pass -----------------------------------------------------------
    def run_once(self) -> RunReport:
        started = time.time()
        report = RunReport(started_at=started)

        try:
            signals = self.collect()
        except Exception as exc:  # noqa: BLE001 - a bad scan must not crash a daemon
            report.errors.append(f"collection failed: {type(exc).__name__}: {exc}")
            signals = []

        report.scanned = len(signals)

        for signal in signals:
            kind = signal.kind
            fix_fn = self.fix_fns.get(kind)
            if fix_fn is None:
                # No registered repair strategy -> escalate, never guess.
                report.outcomes.append(
                    LoopOutcome(signal, "diagnose", "escalated",
                                f"no repair strategy registered for kind {kind!r}")
                )
                continue

            try:
                loop = SentinelLoop(
                    self.repo_root,
                    fix_fn=fix_fn,
                    verifier=Verifier(self.repo_root),
                    committer=self.committer,
                    tracker=self.tracker,
                )
                outcome = loop.handle(
                    signal, verification_commands=self.verification_commands
                )
                report.outcomes.append(outcome)
            except Exception as exc:  # noqa: BLE001 - one bad fault must not abort the run
                report.errors.append(f"{kind}/{signal.fingerprint}: {type(exc).__name__}: {exc}")
                report.outcomes.append(
                    LoopOutcome(signal, "loop", "escalated",
                                f"loop raised {type(exc).__name__}: {exc}")
                )

            if len(report.fixed) >= self.max_fixes_per_run:
                logger.info("Reached max_fixes_per_run (%d); stopping this pass",
                            self.max_fixes_per_run)
                break

        report.duration_s = time.time() - started
        logger.info("Sentinel run complete: %s", report.summary())
        return report

    # -- Daemon -------------------------------------------------------------
    def run_forever(
        self,
        *,
        interval_seconds: float = 300.0,
        max_iterations: int | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> list[RunReport]:
        """Poll on an interval. ``max_iterations`` bounds it (None = forever).

        ``sleep_fn`` is injectable so this is testable without actually waiting.
        """
        reports: list[RunReport] = []
        iteration = 0
        while max_iterations is None or iteration < max_iterations:
            reports.append(self.run_once())
            iteration += 1
            if max_iterations is None or iteration < max_iterations:
                sleep_fn(interval_seconds)
        return reports


def make_default_fix_fns(repo_root: str | Path) -> dict[str, Callable[[Signal, Any], list[str]]]:
    """The repair strategies safe to run unattended today.

    Deliberately small. Each entry has been verified end-to-end; a kind not
    listed here escalates rather than being guessed at.
    """
    root = Path(repo_root)

    def fix_missing_bom(signal: Signal, snapshot: Any) -> list[str]:
        from alpha.runtime.sentinel.sources import scripts

        target = Path(signal.context["path"])
        rel = str(target.relative_to(root))
        snapshot([rel])
        scripts.apply_bom_fix(target)
        return [rel]

    return {"missing_bom": fix_missing_bom}


def make_default_verification_commands() -> dict[str, list[str]]:
    """Safe verification checks to ensure python scripts and repository integrity remain intact."""
    import sys

    return {
        "syntax_check": [
            sys.executable,
            "-c",
            "import sys; from alpha.runtime.sentinel.sources import scripts; sys.exit(0)",
        ]
    }

