"""Alpha memory admission policy engine.

This package implements plan Sections 10, 21, 25, and 31 of
``references/ALPHA_ADVANCED_OPEN_SOURCE_AGENTIC_MEMORY_SYSTEM.md``: section 10
(weighted admission and hard rules), section 21 (never persist secrets in
normal semantic memory), section 25 (the policy table and hot reload), and
section 31 (do not preserve everything as equally important memory).

The implementation is original and self-contained.  It reuses Alpha's existing
L1 runtime-home/path conventions but imports no host ``MemoryConfig`` and owns
no process-wide singleton.  Public exports are lazy (:pep:`562`) so the shared
configuration schema can import only :class:`PolicyConfig` without importing
the loader, file I/O, scorer, or rule engine.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alpha.memory._lazy_exports import install_lazy_exports

_EXPORTS = {
    "AdmissionAction": "models",
    "AdmissionAudit": "engine",
    "AdmissionCandidate": "models",
    "AdmissionDecision": "models",
    "AdmissionEngine": "engine",
    "AdmissionRule": "rules",
    "AdmissionTier": "models",
    "DEFAULT_POLICY_PATH": "loader",
    "DEFAULT_RULES": "rules",
    "DEFAULT_SCORE_WEIGHTS": "scoring",
    "DEFAULT_SESSION_TTL_SECONDS": "engine",
    "PolicyConfig": "config",
    "PolicyLoader": "loader",
    "PolicyReloadResult": "loader",
    "PolicySet": "engine",
    "PolicyThresholds": "engine",
    "PolicyValidationError": "loader",
    "ProvenanceResult": "provenance",
    "RULE_ACTIONS": "rules",
    "RULE_IDS": "rules",
    "SCORE_SIGNALS": "scoring",
    "ScoreResult": "scoring",
    "SecretDetection": "rules",
    "append_decision": "provenance",
    "candidate_digest": "provenance",
    "default_policy": "engine",
    "detect_secret_like": "rules",
    "evaluate_rule": "rules",
    "load_policy_document": "loader",
    "parse_policy_document": "loader",
    "policy_root": "provenance",
    "provenance_log_path": "provenance",
    "read_entries": "provenance",
    "resolve_policy_path": "loader",
    "score_candidate": "scoring",
    "validate_score_weights": "scoring",
    "weighted_score": "scoring",
}

if TYPE_CHECKING:  # pragma: no cover - import-time-free type surface
    from .config import PolicyConfig as PolicyConfig
    from .engine import DEFAULT_SESSION_TTL_SECONDS as DEFAULT_SESSION_TTL_SECONDS
    from .engine import AdmissionAudit as AdmissionAudit
    from .engine import AdmissionEngine as AdmissionEngine
    from .engine import PolicySet as PolicySet
    from .engine import PolicyThresholds as PolicyThresholds
    from .engine import default_policy as default_policy
    from .loader import DEFAULT_POLICY_PATH as DEFAULT_POLICY_PATH
    from .loader import PolicyLoader as PolicyLoader
    from .loader import PolicyReloadResult as PolicyReloadResult
    from .loader import PolicyValidationError as PolicyValidationError
    from .loader import load_policy_document as load_policy_document
    from .loader import parse_policy_document as parse_policy_document
    from .loader import resolve_policy_path as resolve_policy_path
    from .models import AdmissionAction as AdmissionAction
    from .models import AdmissionCandidate as AdmissionCandidate
    from .models import AdmissionDecision as AdmissionDecision
    from .models import AdmissionTier as AdmissionTier
    from .provenance import ProvenanceResult as ProvenanceResult
    from .provenance import append_decision as append_decision
    from .provenance import candidate_digest as candidate_digest
    from .provenance import policy_root as policy_root
    from .provenance import provenance_log_path as provenance_log_path
    from .provenance import read_entries as read_entries
    from .rules import DEFAULT_RULES as DEFAULT_RULES
    from .rules import RULE_ACTIONS as RULE_ACTIONS
    from .rules import RULE_IDS as RULE_IDS
    from .rules import AdmissionRule as AdmissionRule
    from .rules import SecretDetection as SecretDetection
    from .rules import detect_secret_like as detect_secret_like
    from .rules import evaluate_rule as evaluate_rule
    from .scoring import DEFAULT_SCORE_WEIGHTS as DEFAULT_SCORE_WEIGHTS
    from .scoring import SCORE_SIGNALS as SCORE_SIGNALS
    from .scoring import ScoreResult as ScoreResult
    from .scoring import score_candidate as score_candidate
    from .scoring import validate_score_weights as validate_score_weights
    from .scoring import weighted_score as weighted_score

install_lazy_exports(__name__, _EXPORTS)
