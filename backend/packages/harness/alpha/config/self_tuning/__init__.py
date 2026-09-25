"""Alpha's default-off, self-configuration change protocol.

Public names are installed lazily (:pep:`562`, via
:func:`alpha.memory._lazy_exports.install_lazy_exports`) so importing this
package from the shared config plane stays cheap and side-effect free.

Design references (implemented independently, no source copied):

* Google SRE, *Release Engineering*:
  https://sre.google/sre-book/release-engineering/
* Google SRE, *Canarying Releases*:
  https://sre.google/workbook/canarying-releases/
* Martin Fowler, *Feature Toggles (aka Kill Switches)*:
  https://martinfowler.com/articles/feature-toggles.html
* Karl Åström and Richard Murray, *Feedback Systems* (damping, saturation,
  and deadbands in the resource governor).
* AWS Builders' Library, *Making retries safe with idempotent APIs*:
  https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alpha.memory._lazy_exports import install_lazy_exports

_EXPORTS = {
    "ApplyOutcome": "models",
    "ApplyResult": "models",
    "ApplyStage": "apply",
    "AppliedTransaction": "apply",
    "AtomicConfigApplier": "apply",
    "BlastRadius": "models",
    "BlastRadiusClass": "models",
    "BoundsSource": "config",
    "CanaryDecision": "models",
    "CanaryObservation": "models",
    "CanaryProbe": "canary",
    "CanaryResult": "models",
    "CanaryRunner": "canary",
    "CanaryStatus": "models",
    "ChangeKind": "models",
    "ChangeSet": "models",
    "ChangeStatus": "models",
    "Clock": "models",
    "ConfigChange": "models",
    "ConfigChangeValidator": "validate",
    "ConfigFileLoader": "apply",
    "ConfigTarget": "targets",
    "DEFAULT_TARGETS": "targets",
    "FaultInjectionHook": "apply",
    "FullConfigValidator": "validate",
    "GENESIS_HASH": "provenance",
    "HealthCheck": "verify",
    "HealthSnapshot": "models",
    "HealthVerifier": "verify",
    "ManualClock": "models",
    "OperatorAuthorizer": "validate",
    "PROTECTED_PATH_REASONS": "config",
    "PathResolution": "targets",
    "PathResolutionStatus": "targets",
    "ProvenanceAction": "models",
    "ProvenanceChainError": "provenance",
    "ProvenanceEntry": "models",
    "ProvenanceLedger": "provenance",
    "ProtocolRun": "models",
    "Refusal": "models",
    "RefusalCode": "models",
    "ResourceGovernor": "dynamics",
    "ResourceRule": "dynamics",
    "ResourceSignals": "dynamics",
    "RollbackManager": "rollback",
    "ScopeSelector": "canary",
    "ScriptedCanaryProbe": "canary",
    "ScriptedHealthCheck": "verify",
    "SelfConfigurationProtocol": "protocol",
    "SelfTuningConfig": "config",
    "TargetRegistry": "targets",
    "UTCClock": "models",
    "ValidationErrorCode": "models",
    "ValidationIssue": "models",
    "ValidationResult": "models",
    "ValidationWarning": "models",
    "ValidationWarningCode": "models",
    "ValueDomain": "targets",
    "VerificationPolicy": "config",
    "VerificationResult": "models",
    "VerificationStatus": "models",
    "coerce_target_value": "targets",
    "default_off_subsystem_paths": "targets",
    "materialize_validated_changes": "validate",
    "validate_config_file": "apply",
}

if TYPE_CHECKING:  # pragma: no cover - import-time-free type surface
    from .apply import AppliedTransaction as AppliedTransaction
    from .apply import ApplyStage as ApplyStage
    from .apply import AtomicConfigApplier as AtomicConfigApplier
    from .apply import ConfigFileLoader as ConfigFileLoader
    from .apply import FaultInjectionHook as FaultInjectionHook
    from .apply import validate_config_file as validate_config_file
    from .canary import CanaryProbe as CanaryProbe
    from .canary import CanaryRunner as CanaryRunner
    from .canary import ScopeSelector as ScopeSelector
    from .canary import ScriptedCanaryProbe as ScriptedCanaryProbe
    from .config import PROTECTED_PATH_REASONS as PROTECTED_PATH_REASONS
    from .config import BoundsSource as BoundsSource
    from .config import SelfTuningConfig as SelfTuningConfig
    from .config import VerificationPolicy as VerificationPolicy
    from .dynamics import ResourceGovernor as ResourceGovernor
    from .dynamics import ResourceRule as ResourceRule
    from .dynamics import ResourceSignals as ResourceSignals
    from .models import ApplyOutcome as ApplyOutcome
    from .models import ApplyResult as ApplyResult
    from .models import BlastRadius as BlastRadius
    from .models import BlastRadiusClass as BlastRadiusClass
    from .models import CanaryDecision as CanaryDecision
    from .models import CanaryObservation as CanaryObservation
    from .models import CanaryResult as CanaryResult
    from .models import CanaryStatus as CanaryStatus
    from .models import ChangeKind as ChangeKind
    from .models import ChangeSet as ChangeSet
    from .models import ChangeStatus as ChangeStatus
    from .models import Clock as Clock
    from .models import ConfigChange as ConfigChange
    from .models import HealthSnapshot as HealthSnapshot
    from .models import ManualClock as ManualClock
    from .models import ProtocolRun as ProtocolRun
    from .models import ProvenanceAction as ProvenanceAction
    from .models import ProvenanceEntry as ProvenanceEntry
    from .models import Refusal as Refusal
    from .models import RefusalCode as RefusalCode
    from .models import UTCClock as UTCClock
    from .models import ValidationErrorCode as ValidationErrorCode
    from .models import ValidationIssue as ValidationIssue
    from .models import ValidationResult as ValidationResult
    from .models import ValidationWarning as ValidationWarning
    from .models import ValidationWarningCode as ValidationWarningCode
    from .models import VerificationResult as VerificationResult
    from .models import VerificationStatus as VerificationStatus
    from .protocol import SelfConfigurationProtocol as SelfConfigurationProtocol
    from .provenance import GENESIS_HASH as GENESIS_HASH
    from .provenance import ProvenanceChainError as ProvenanceChainError
    from .provenance import ProvenanceLedger as ProvenanceLedger
    from .rollback import RollbackManager as RollbackManager
    from .targets import DEFAULT_TARGETS as DEFAULT_TARGETS
    from .targets import ConfigTarget as ConfigTarget
    from .targets import PathResolution as PathResolution
    from .targets import PathResolutionStatus as PathResolutionStatus
    from .targets import TargetRegistry as TargetRegistry
    from .targets import ValueDomain as ValueDomain
    from .targets import coerce_target_value as coerce_target_value
    from .targets import default_off_subsystem_paths as default_off_subsystem_paths
    from .validate import ConfigChangeValidator as ConfigChangeValidator
    from .validate import FullConfigValidator as FullConfigValidator
    from .validate import OperatorAuthorizer as OperatorAuthorizer
    from .validate import materialize_validated_changes as materialize_validated_changes
    from .verify import HealthCheck as HealthCheck
    from .verify import HealthVerifier as HealthVerifier
    from .verify import ScriptedHealthCheck as ScriptedHealthCheck

install_lazy_exports(__name__, _EXPORTS)
