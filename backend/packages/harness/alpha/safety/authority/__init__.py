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
    # --- runtime policy controls, wired at the authority chokepoints -------
    "BaselinePolicy": "scopes",
    "DenialExplanation": "scopes",
    "EffectivePolicy": "scopes",
    "ScopeDecision": "scopes",
    "ScopeEscalationRefused": "scopes",
    "ScopeError": "scopes",
    "ScopeMember": "scopes",
    "ScopePolicy": "scopes",
    "ScopeResolution": "scopes",
    "ScopeRule": "scopes",
    "SubjectKind": "scopes",
    "assert_not_looser": "scopes",
    "baseline_from_ceiling": "scopes",
    "compose_scopes": "scopes",
    "explain_denial": "scopes",
    "member": "scopes",
    "ActorKind": "receipts",
    "ChainIntegrityError": "receipts",
    "ChainVerification": "receipts",
    "DecisionReceipt": "receipts",
    "ExecutionIdentity": "receipts",
    "IdentityError": "receipts",
    "ReceiptChain": "receipts",
    "ReceiptError": "receipts",
    "ReceiptFlag": "receipts",
    "RejectedAlternative": "receipts",
    "agent": "receipts",
    "automated_system": "receipts",
    "child_agent": "receipts",
    "get_receipt_chain": "receipts",
    "human": "receipts",
    "ISOLATION_CONTROLS": "taint",
    "TRUSTED_CLEARING_CONTROLS": "taint",
    "TaintAuthorityError": "taint",
    "TaintClearRefused": "taint",
    "TaintMark": "taint",
    "TaintTurn": "taint",
    "UnknownUntrustedSource": "taint",
    "UntrustedSource": "taint",
    "absorb_untrusted": "taint",
    "bind_turn": "taint",
    "current_turn": "taint",
    "isolate_child": "taint",
    "new_turn": "taint",
    "ApprovalOutcome": "boundaries",
    "ApprovalResult": "boundaries",
    "ApproverTimeout": "boundaries",
    "ApproverUnavailable": "boundaries",
    "Authentication": "boundaries",
    "BoundaryError": "boundaries",
    "BoundaryKind": "boundaries",
    "CredentialIsolationReport": "boundaries",
    "CredentialOwner": "boundaries",
    "CredentialSpec": "boundaries",
    "CredentialStatus": "boundaries",
    "INBOUND_ENTRY_POINTS": "boundaries",
    "InboundEntryPoint": "boundaries",
    "StartupCheck": "boundaries",
    "StartupRefused": "boundaries",
    "StartupReport": "boundaries",
    "SurfaceKind": "boundaries",
    "UnauthenticatedInboundPath": "boundaries",
    "assert_credential_failures_isolated": "boundaries",
    "assert_inbound_default_deny": "boundaries",
    "assert_startable": "boundaries",
    "boundary_inventory": "boundaries",
    "convenience_boundaries": "boundaries",
    "resolve_authority_approval": "boundaries",
    "resolve_credential_owners": "boundaries",
    "security_boundaries": "boundaries",
    "unauthenticated_inbound_paths": "boundaries",
}

if TYPE_CHECKING:  # pragma: no cover - import-time-free type surface
    from alpha.safety.authority.baseline import BaselineDiff as BaselineDiff
    from alpha.safety.authority.baseline import PostureFlip as PostureFlip
    from alpha.safety.authority.baseline import compare_census as compare_census
    from alpha.safety.authority.baseline import diff_reports as diff_reports
    from alpha.safety.authority.baseline import load_baseline as load_baseline
    from alpha.safety.authority.baseline import save_baseline as save_baseline
    from alpha.safety.authority.baseline import write_baseline as write_baseline
    from alpha.safety.authority.boundaries import INBOUND_ENTRY_POINTS as INBOUND_ENTRY_POINTS
    from alpha.safety.authority.boundaries import ApprovalOutcome as ApprovalOutcome
    from alpha.safety.authority.boundaries import ApprovalResult as ApprovalResult
    from alpha.safety.authority.boundaries import ApproverTimeout as ApproverTimeout
    from alpha.safety.authority.boundaries import ApproverUnavailable as ApproverUnavailable
    from alpha.safety.authority.boundaries import Authentication as Authentication
    from alpha.safety.authority.boundaries import BoundaryError as BoundaryError
    from alpha.safety.authority.boundaries import BoundaryKind as BoundaryKind
    from alpha.safety.authority.boundaries import CredentialIsolationReport as CredentialIsolationReport
    from alpha.safety.authority.boundaries import CredentialOwner as CredentialOwner
    from alpha.safety.authority.boundaries import CredentialSpec as CredentialSpec
    from alpha.safety.authority.boundaries import CredentialStatus as CredentialStatus
    from alpha.safety.authority.boundaries import InboundEntryPoint as InboundEntryPoint
    from alpha.safety.authority.boundaries import StartupCheck as StartupCheck
    from alpha.safety.authority.boundaries import StartupRefused as StartupRefused
    from alpha.safety.authority.boundaries import StartupReport as StartupReport
    from alpha.safety.authority.boundaries import SurfaceKind as SurfaceKind
    from alpha.safety.authority.boundaries import UnauthenticatedInboundPath as UnauthenticatedInboundPath
    from alpha.safety.authority.boundaries import assert_credential_failures_isolated as assert_credential_failures_isolated
    from alpha.safety.authority.boundaries import assert_inbound_default_deny as assert_inbound_default_deny
    from alpha.safety.authority.boundaries import assert_startable as assert_startable
    from alpha.safety.authority.boundaries import boundary_inventory as boundary_inventory
    from alpha.safety.authority.boundaries import convenience_boundaries as convenience_boundaries
    from alpha.safety.authority.boundaries import resolve_authority_approval as resolve_authority_approval
    from alpha.safety.authority.boundaries import resolve_credential_owners as resolve_credential_owners
    from alpha.safety.authority.boundaries import security_boundaries as security_boundaries
    from alpha.safety.authority.boundaries import unauthenticated_inbound_paths as unauthenticated_inbound_paths
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
    from alpha.safety.authority.receipts import KNOWN_POLICY_RULES as KNOWN_POLICY_RULES
    from alpha.safety.authority.receipts import ActorKind as ActorKind
    from alpha.safety.authority.receipts import ChainIntegrityError as ChainIntegrityError
    from alpha.safety.authority.receipts import ChainVerification as ChainVerification
    from alpha.safety.authority.receipts import DecisionReceipt as DecisionReceipt
    from alpha.safety.authority.receipts import ExecutionIdentity as ExecutionIdentity
    from alpha.safety.authority.receipts import IdentityError as IdentityError
    from alpha.safety.authority.receipts import ReceiptChain as ReceiptChain
    from alpha.safety.authority.receipts import ReceiptError as ReceiptError
    from alpha.safety.authority.receipts import ReceiptFlag as ReceiptFlag
    from alpha.safety.authority.receipts import RejectedAlternative as RejectedAlternative
    from alpha.safety.authority.receipts import agent as agent
    from alpha.safety.authority.receipts import automated_system as automated_system
    from alpha.safety.authority.receipts import child_agent as child_agent
    from alpha.safety.authority.receipts import get_receipt_chain as get_receipt_chain
    from alpha.safety.authority.receipts import human as human
    from alpha.safety.authority.report import render as render
    from alpha.safety.authority.report import render_json as render_json
    from alpha.safety.authority.report import render_markdown as render_markdown
    from alpha.safety.authority.report import write_report as write_report
    from alpha.safety.authority.scopes import BaselinePolicy as BaselinePolicy
    from alpha.safety.authority.scopes import DenialExplanation as DenialExplanation
    from alpha.safety.authority.scopes import EffectivePolicy as EffectivePolicy
    from alpha.safety.authority.scopes import ScopeDecision as ScopeDecision
    from alpha.safety.authority.scopes import ScopeError as ScopeError
    from alpha.safety.authority.scopes import ScopeEscalationRefused as ScopeEscalationRefused
    from alpha.safety.authority.scopes import ScopeMember as ScopeMember
    from alpha.safety.authority.scopes import ScopePolicy as ScopePolicy
    from alpha.safety.authority.scopes import ScopeResolution as ScopeResolution
    from alpha.safety.authority.scopes import ScopeRule as ScopeRule
    from alpha.safety.authority.scopes import SubjectKind as SubjectKind
    from alpha.safety.authority.scopes import assert_not_looser as assert_not_looser
    from alpha.safety.authority.scopes import baseline_from_ceiling as baseline_from_ceiling
    from alpha.safety.authority.scopes import compose_scopes as compose_scopes
    from alpha.safety.authority.scopes import explain_denial as explain_denial
    from alpha.safety.authority.scopes import member as member
    from alpha.safety.authority.taint import ISOLATION_CONTROLS as ISOLATION_CONTROLS
    from alpha.safety.authority.taint import TRUSTED_CLEARING_CONTROLS as TRUSTED_CLEARING_CONTROLS
    from alpha.safety.authority.taint import TaintAuthorityError as TaintAuthorityError
    from alpha.safety.authority.taint import TaintClearRefused as TaintClearRefused
    from alpha.safety.authority.taint import TaintMark as TaintMark
    from alpha.safety.authority.taint import TaintTurn as TaintTurn
    from alpha.safety.authority.taint import UnknownUntrustedSource as UnknownUntrustedSource
    from alpha.safety.authority.taint import UntrustedSource as UntrustedSource
    from alpha.safety.authority.taint import absorb_untrusted as absorb_untrusted
    from alpha.safety.authority.taint import bind_turn as bind_turn
    from alpha.safety.authority.taint import current_turn as current_turn
    from alpha.safety.authority.taint import isolate_child as isolate_child
    from alpha.safety.authority.taint import new_turn as new_turn

install_lazy_exports(__name__, _EXPORTS, public=tuple(_EXPORTS))
