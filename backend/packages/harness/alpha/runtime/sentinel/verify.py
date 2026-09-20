"""VERIFY stage — the gate that must never let red through.

This is the most important stage in the loop. Every other stage can be
approximate; this one cannot. If verification is green, the Sentinel commits. If
it is anything else — failed, timed out, or *unknown* — the repair is reverted
and escalated.

Critical rule: **unknown is failure.** A command that could not be run (missing
binary, wrong cwd, subprocess error) is NOT a pass. Treating "couldn't check" as
"probably fine" is how an autonomous loop silently destroys a repo.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CheckResult:
    name: str
    passed: bool
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    duration_s: float = 0.0

    @property
    def ok(self) -> bool:
        """True only when the check ran AND passed."""
        return self.passed and self.error is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "ok": self.ok,
            "returncode": self.returncode,
            "error": self.error,
            "duration_s": round(self.duration_s, 3),
            # Truncate: verification output can be enormous, and the point of
            # keeping it is the tail where failures are reported.
            "stdout_tail": self.stdout[-2000:],
            "stderr_tail": self.stderr[-2000:],
        }


@dataclass
class VerifyReport:
    results: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """Green only when there was at least one check and every one ran and passed."""
        return bool(self.results) and all(r.ok for r in self.results)

    @property
    def failed(self) -> list[CheckResult]:
        return [r for r in self.results if not r.ok]

    def summary(self) -> str:
        if not self.results:
            return "no checks ran — treated as failure"
        bad = self.failed
        if not bad:
            return f"all {len(self.results)} checks passed"
        return "; ".join(f"{r.name}: {r.error or f'exit {r.returncode}'}" for r in bad)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "summary": self.summary(),
            "checks": [r.to_dict() for r in self.results],
        }


class Verifier:
    """Runs verification commands and reports green/red."""

    def __init__(self, cwd: str | Path, *, default_timeout: float = 900.0) -> None:
        self.cwd = Path(cwd)
        self.default_timeout = default_timeout

    def run(
        self,
        name: str,
        command: list[str],
        *,
        timeout: float | None = None,
    ) -> CheckResult:
        import time

        started = time.time()
        try:
            proc = subprocess.run(
                command,
                cwd=str(self.cwd),
                capture_output=True,
                text=True,
                timeout=timeout or self.default_timeout,
            )
            return CheckResult(
                name=name,
                passed=proc.returncode == 0,
                returncode=proc.returncode,
                stdout=proc.stdout or "",
                stderr=proc.stderr or "",
                duration_s=time.time() - started,
            )
        except subprocess.TimeoutExpired as exc:
            return CheckResult(
                name=name, passed=False, error=f"timed out after {timeout or self.default_timeout}s",
                stdout=(exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else str(exc.stdout or ""),
                duration_s=time.time() - started,
            )
        except FileNotFoundError as exc:
            # Unknown is failure — never "probably fine".
            return CheckResult(name=name, passed=False, error=f"command not found: {exc}")
        except Exception as exc:  # noqa: BLE001 - must not escape the gate
            return CheckResult(name=name, passed=False, error=f"{type(exc).__name__}: {exc}")

    def run_all(self, checks: dict[str, list[str]], *, timeout: float | None = None) -> VerifyReport:
        """Run every named check. An empty dict yields a failing report."""
        report = VerifyReport()
        for name, command in checks.items():
            report.results.append(self.run(name, command, timeout=timeout))
        return report
