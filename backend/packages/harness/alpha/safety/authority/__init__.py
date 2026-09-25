"""Authority Model Auditor: a static, read-only map of unattended authority.

The package inventories capability and enforcement surfaces so an operator can
answer "what could this agent do without asking anyone?" It does not grant
authority, edit policy, or treat an unknown as safe.  The runtime surface is
installed lazily (:pep:`562`) to keep importing one census helper cheap and to
avoid import cycles with the harness configuration layer.

Security context: the design follows least privilege and zero-trust review
practice (see the OWASP Agentic AI Threats and Mitigations guide and NIST SP
800-207 references in :mod:`alpha.safety.authority.models`).  A static census
is evidence for a human decision, never a substitute for runtime policy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alpha.memory._lazy_exports import install_lazy_exports

_EXPORTS = {
    "AuditGap": "models",
    "AuditReport": "models",
    "AuthorityRecord": "models",
    "Capability": "models",
    "CoverageSummary": "models",
    "Externality": "models",
    "Gate": "models",
    "GateKind": "models",
    "GatePosture": "models",
    "Reversibility": "models",
    "UnknownFinding": "models",
    "Verdict": "models",
    "AuthorityAuditConfig": "config",
    "classify_action": "classify",
    "classification_rule_documentation": "classify",
    "CLASSIFICATION_RULES": "classify",
    "RULES": "classify",
    "compute_coverage": "coverage",
    "coverage_for_report": "coverage",
    "census": "census",
    "iter_source_files": "census",
    "scan": "census",
    "scan_tree": "census",
    "render": "report",
    "render_json": "report",
    "render_markdown": "report",
    "write_report": "report",
    "BaselineDiff": "baseline",
    "PostureFlip": "baseline",
    "compare_census": "baseline",
    "diff_reports": "baseline",
    "load_baseline": "baseline",
    "save_baseline": "baseline",
    "write_baseline": "baseline",
    "build_parser": "cli",
    "main": "cli",
}

if TYPE_CHECKING:  # pragma: no cover - import-time-free type surface
    from alpha.safety.authority.baseline import BaselineDiff as BaselineDiff
    from alpha.safety.authority.baseline import PostureFlip as PostureFlip
    from alpha.safety.authority.baseline import compare_census as compare_census
    from alpha.safety.authority.baseline import diff_reports as diff_reports
    from alpha.safety.authority.baseline import load_baseline as load_baseline
    from alpha.safety.authority.baseline import save_baseline as save_baseline
    from alpha.safety.authority.baseline import write_baseline as write_baseline
    from alpha.safety.authority.census import census as census
    from alpha.safety.authority.census import iter_source_files as iter_source_files
    from alpha.safety.authority.census import scan as scan
    from alpha.safety.authority.census import scan_tree as scan_tree
    from alpha.safety.authority.classify import CLASSIFICATION_RULES as CLASSIFICATION_RULES
    from alpha.safety.authority.classify import RULES as RULES
    from alpha.safety.authority.classify import classification_rule_documentation as classification_rule_documentation
    from alpha.safety.authority.classify import classify_action as classify_action
    from alpha.safety.authority.cli import build_parser as build_parser
    from alpha.safety.authority.cli import main as main
    from alpha.safety.authority.config import AuthorityAuditConfig as AuthorityAuditConfig
    from alpha.safety.authority.coverage import compute_coverage as compute_coverage
    from alpha.safety.authority.coverage import coverage_for_report as coverage_for_report
    from alpha.safety.authority.models import AuditGap as AuditGap
    from alpha.safety.authority.models import AuditReport as AuditReport
    from alpha.safety.authority.models import AuthorityRecord as AuthorityRecord
    from alpha.safety.authority.models import Capability as Capability
    from alpha.safety.authority.models import CoverageSummary as CoverageSummary
    from alpha.safety.authority.models import Externality as Externality
    from alpha.safety.authority.models import Gate as Gate
    from alpha.safety.authority.models import GateKind as GateKind
    from alpha.safety.authority.models import GatePosture as GatePosture
    from alpha.safety.authority.models import Reversibility as Reversibility
    from alpha.safety.authority.models import UnknownFinding as UnknownFinding
    from alpha.safety.authority.models import Verdict as Verdict
    from alpha.safety.authority.report import render as render
    from alpha.safety.authority.report import render_json as render_json
    from alpha.safety.authority.report import render_markdown as render_markdown
    from alpha.safety.authority.report import write_report as write_report

install_lazy_exports(__name__, _EXPORTS, public=tuple(_EXPORTS))
