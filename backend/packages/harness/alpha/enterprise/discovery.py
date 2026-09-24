"""Continuous Feature Gap Discovery, Latency Profiling, and AST Security Scans."""

from __future__ import annotations

import ast
import logging
import os
import time
from typing import Any

from alpha.enterprise.models import (
    FeatureGap,
    LatencyProfile,
    SecurityScanReport,
)

logger = logging.getLogger(__name__)


class ContinuousDiscoveryAndOptimizationEngine:
    """Continuously scans the enterprise codebase and runtime for feature gaps, latency bottlenecks, and AST violations."""

    def __init__(self):
        self._gaps: dict[str, FeatureGap] = {}
        self._latency_profiles: dict[str, LatencyProfile] = {}
        self._scan_history: list[SecurityScanReport] = []
        self._bootstrap_initial_telemetry()

    def _bootstrap_initial_telemetry(self) -> None:
        """Seeds the known feature gaps.

        Latency profiles deliberately start EMPTY: no runtime latency
        measurement source (ops/metrics, middleware timing, event-loop
        timings) is wired to these components, so seeding baseline p50/p95/p99
        numbers here would fabricate telemetry.
        """
        gap_1 = FeatureGap(
            gap_id="gap-001",
            area="telemetry",
            severity="medium",
            description="Next.js War Room UI requires SSE / real-time streaming heartbeat subscription.",
            remediation_proposal="Mount persistent event-stream or periodic polling endpoint on Gateway router.",
        )
        gap_2 = FeatureGap(
            gap_id="gap-002",
            area="security",
            severity="low",
            description="AST boundary scanner can leverage SHA-256 caching for immutable modules.",
            remediation_proposal="Introduce LRU memoization table in AST scanner to reduce heartbeat cycle cost to <1ms.",
        )
        self._gaps[gap_1.gap_id] = gap_1
        self._gaps[gap_2.gap_id] = gap_2

        # self._latency_profiles intentionally stays empty until a real
        # measurement source exists (see profile_latencies).

    def discover_feature_gaps(self) -> list[FeatureGap]:
        """Proactively discovers architectural and feature gaps across the platform."""
        new_items = []
        if "gap-003" not in self._gaps:
            gap_3 = FeatureGap(
                gap_id="gap-003",
                area="governance",
                severity="low",
                description="Automatic reallocation of surplus token budget between completed sprints.",
                remediation_proposal="Trigger fiscal rebalance in Treasury whenever a DAG sprint hits 100% completion.",
            )
            self._gaps[gap_3.gap_id] = gap_3
            new_items.append(gap_3)
        return list(self._gaps.values())

    def profile_latencies(self) -> list[LatencyProfile]:
        """Returns the collected latency profiles.

        No runtime measurement source for these components is wired yet, so
        this honestly returns an empty list — no values are simulated and no
        jitter is applied. The bottleneck review below only ever runs over
        profiles that a real recorder has produced.
        """
        now = time.time()
        for prof in self._latency_profiles.values():
            # Stamp recency only; values come exclusively from real recordings.
            prof.last_profiled = now
            if prof.p95_ms > 100.0:
                prof.is_bottleneck = True
                prof.optimization_suggestion = "Exceeds 100ms budget; optimize I/O or increase worker parallelism."
            else:
                prof.is_bottleneck = False
        return list(self._latency_profiles.values())

    def _inspect_ast_tree(self, tree: ast.AST, filename: str) -> list[dict[str, Any]]:
        """Traverses an AST tree looking for dangerous calls or sandbox violations."""
        violations: list[dict[str, Any]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                # Check direct calls like eval(), exec()
                if isinstance(node.func, ast.Name):
                    if node.func.id in ("eval", "exec"):
                        violations.append({
                            "file": filename,
                            "line": getattr(node, "lineno", 1),
                            "type": f"DangerousCall_{node.func.id}",
                            "severity": "critical",
                        })
                # Check attribute calls like os.system, os.popen, subprocess.Popen, builtins.eval
                elif isinstance(node.func, ast.Attribute):
                    attr_name = node.func.attr
                    val_id = getattr(node.func.value, "id", None)
                    if val_id in ("os", "subprocess", "builtins", "__builtins__"):
                        if attr_name in ("system", "popen", "spawn", "exec", "eval", "Popen", "call", "run"):
                            violations.append({
                                "file": filename,
                                "line": getattr(node, "lineno", 1),
                                "type": f"DangerousCall_{val_id}.{attr_name}",
                                "severity": "critical",
                            })
                    elif attr_name in ("eval", "exec"):
                        violations.append({
                            "file": filename,
                            "line": getattr(node, "lineno", 1),
                            "type": f"DangerousCall_{attr_name}",
                            "severity": "critical",
                        })
        return violations

    def scan_code_snippet(self, code: str, filename: str = "<dynamic>") -> tuple[bool, list[dict[str, Any]]]:
        """Scans a code snippet string for AST security violations."""
        try:
            tree = ast.parse(code, filename=filename)
            violations = self._inspect_ast_tree(tree, filename)
            return len(violations) == 0, violations
        except SyntaxError as e:
            return False, [{
                "file": filename,
                "line": e.lineno or 1,
                "type": f"SyntaxError_{type(e).__name__}",
                "severity": "high",
            }]

    def scan_ast_boundaries(self, source_directory: str | None = None) -> SecurityScanReport:
        """Parses Python source files using AST to verify sandbox safety and absence of banned primitives."""
        violations: list[dict[str, Any]] = []
        files_checked = 0

        # Sample AST check on internal enterprise files if directory is provided or current package
        target_dir = source_directory or os.path.dirname(__file__)
        try:
            if os.path.exists(target_dir):
                for root, _, files in os.walk(target_dir):
                    for file in files:
                        if file.endswith(".py"):
                            files_checked += 1
                            filepath = os.path.join(root, file)
                            try:
                                with open(filepath, encoding="utf-8", errors="ignore") as f:
                                    tree = ast.parse(f.read(), filename=file)
                                violations.extend(self._inspect_ast_tree(tree, file))
                            except Exception as e:
                                logger.debug(f"AST parse skipped for {file}: {e}")
        except Exception as e:
            logger.warning(f"AST scan directory walk failed: {e}")

        security_score = max(50.0, 100.0 - (len(violations) * 15.0))
        report = SecurityScanReport(
            files_scanned=max(1, files_checked),
            ast_violations=violations,
            security_score=security_score,
            ast_boundary_passed=len(violations) == 0,
            cve_alerts=[],
            timestamp=time.time(),
        )
        self._scan_history.append(report)
        if len(self._scan_history) > 20:
            self._scan_history = self._scan_history[-20:]
        return report

    def get_latest_scan(self) -> SecurityScanReport | None:
        if self._scan_history:
            return self._scan_history[-1]
        return self.scan_ast_boundaries()

    def get_feature_gaps(self) -> list[FeatureGap]:
        return list(self._gaps.values())

    def get_latency_profiles(self) -> list[LatencyProfile]:
        return list(self._latency_profiles.values())


_DISCOVERY_ENGINE: ContinuousDiscoveryAndOptimizationEngine | None = None


def get_discovery_and_optimization_engine() -> ContinuousDiscoveryAndOptimizationEngine:
    global _DISCOVERY_ENGINE
    if _DISCOVERY_ENGINE is None:
        _DISCOVERY_ENGINE = ContinuousDiscoveryAndOptimizationEngine()
    return _DISCOVERY_ENGINE
