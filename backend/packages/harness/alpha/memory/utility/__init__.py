"""Memory utility feedback: an opt-in, proposal-only ranking plane.

The package learns relative utility from observed feedback and exposes
uncertainty-aware scores, retention bands, and similarity merge proposals.  It
is disabled by default and never edits host memory data.  Public names use
:pep:`562` lazy exports so importing a leaf config/model does not eagerly load
the facade, store, or host configuration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alpha.memory._lazy_exports import install_lazy_exports

_EXPORTS = {
    "BudgetState": "models",
    "CalibrationResult": "scoring",
    "EvictionNotice": "models",
    "DedupPolicy": "dedup",
    "DedupSuggestion": "models",
    "EVENT_OVERRIDES": "signals",
    "OVERRIDE_MAP": "signals",
    "FeedbackNormalizer": "signals",
    "FeedbackPolicy": "models",
    "MemoryUtility": "feedback",
    "MemoryUtilityFacade": "feedback",
    "MemoryUtilityStore": "store",
    "ObserveResult": "models",
    "ObservationOutcome": "models",
    "PolicyDecision": "models",
    "RetentionAction": "models",
    "RetentionDecision": "models",
    "RetentionPolicy": "retention",
    "ScoreLabel": "models",
    "ScoreResult": "scoring",
    "SignalOutcome": "models",
    "SignalStatus": "models",
    "UtilityConfig": "config",
    "UtilityEvent": "models",
    "UtilityObservation": "models",
    "UtilityRecord": "models",
    "UtilityRecordStore": "store",
    "UtilityFeedback": "feedback",
    "UtilitySnapshot": "models",
    "UtilityStore": "store",
    "calibrate": "scoring",
    "calibrated_score": "scoring",
    "config_from_mapping": "config",
    "load_config": "config",
    "dedup_suggestions": "dedup",
    "decide_retention": "retention",
    "decide": "retention",
    "merge_observation": "scoring",
    "normalize_feedback": "signals",
    "normalize_observation": "signals",
    "normalize_signal": "signals",
    "normalize": "signals",
    "policy_decision": "retention",
    "propose_dedup": "dedup",
    "read_config": "config",
    "retention_decision": "retention",
    "retention_decisions": "retention",
    "score": "scoring",
    "score_observation": "scoring",
    "score_observations": "scoring",
    "score_record": "scoring",
    "score_records": "scoring",
    "score_utility": "scoring",
    "suggest_dedup": "dedup",
    "utility_root": "paths",
    "document_path": "paths",
    "atomic_write_text": "paths",
}

if TYPE_CHECKING:  # pragma: no cover - import-time-free type surface
    from .config import UtilityConfig as UtilityConfig
    from .config import config_from_mapping as config_from_mapping
    from .config import load_config as load_config
    from .config import read_config as read_config
    from .dedup import DedupPolicy as DedupPolicy
    from .dedup import dedup_suggestions as dedup_suggestions
    from .dedup import propose_dedup as propose_dedup
    from .dedup import suggest_dedup as suggest_dedup
    from .feedback import MemoryUtility as MemoryUtility
    from .feedback import MemoryUtilityFacade as MemoryUtilityFacade
    from .feedback import UtilityFeedback as UtilityFeedback
    from .models import BudgetState as BudgetState
    from .models import DedupSuggestion as DedupSuggestion
    from .models import EvictionNotice as EvictionNotice
    from .models import FeedbackPolicy as FeedbackPolicy
    from .models import ObservationOutcome as ObservationOutcome
    from .models import ObserveResult as ObserveResult
    from .models import PolicyDecision as PolicyDecision
    from .models import RetentionAction as RetentionAction
    from .models import RetentionDecision as RetentionDecision
    from .models import ScoreLabel as ScoreLabel
    from .models import SignalOutcome as SignalOutcome
    from .models import SignalStatus as SignalStatus
    from .models import UtilityEvent as UtilityEvent
    from .models import UtilityObservation as UtilityObservation
    from .models import UtilityRecord as UtilityRecord
    from .models import UtilitySnapshot as UtilitySnapshot
    from .paths import atomic_write_text as atomic_write_text
    from .paths import document_path as document_path
    from .paths import utility_root as utility_root
    from .retention import RetentionPolicy as RetentionPolicy
    from .retention import decide_retention as decide_retention
    from .retention import policy_decision as policy_decision
    from .retention import retention_decision as retention_decision
    from .retention import retention_decisions as retention_decisions
    from .scoring import CalibrationResult as CalibrationResult
    from .scoring import ScoreResult as ScoreResult
    from .scoring import calibrate as calibrate
    from .scoring import calibrated_score as calibrated_score
    from .scoring import merge_observation as merge_observation
    from .scoring import score as score
    from .scoring import score_observation as score_observation
    from .scoring import score_observations as score_observations
    from .scoring import score_record as score_record
    from .scoring import score_records as score_records
    from .scoring import score_utility as score_utility
    from .signals import EVENT_OVERRIDES as EVENT_OVERRIDES
    from .signals import OVERRIDE_MAP as OVERRIDE_MAP
    from .signals import FeedbackNormalizer as FeedbackNormalizer
    from .signals import normalize as normalize
    from .signals import normalize_feedback as normalize_feedback
    from .signals import normalize_observation as normalize_observation
    from .signals import normalize_signal as normalize_signal
    from .store import MemoryUtilityStore as MemoryUtilityStore
    from .store import UtilityRecordStore as UtilityRecordStore
    from .store import UtilityStore as UtilityStore

install_lazy_exports(__name__, _EXPORTS, public=tuple(_EXPORTS))
