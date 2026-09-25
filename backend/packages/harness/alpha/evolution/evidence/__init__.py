"""Alpha's self-evolution evidence gate: a bar, not a hope.

Alpha has plenty of self-evolution machinery - ``alpha/rsi/**``,
``alpha/evolution/**``, ``alpha/evolution/promptbreeder.py``,
``scripts/auto_update.py``, skill evolution. What it did not have was a
component that decides whether a proposed self-change is *actually* an
improvement. Without that bar, "self-evolving" degrades into "edits itself and
hopes" - and, worse, a system that can edit its own tests can report any
improvement it likes.

This package is the bar. It is **default-off** and additive: it changes no
existing behaviour and is not wired into any promotion path yet (see the
integration patch in the module report). The contract it enforces:

* a change becomes the new default only when the evaluator itself was not
  tampered with (:mod:`~alpha.evolution.evidence.integrity`), every required
  precondition gate is green (:mod:`~alpha.evolution.evidence.gates`), and the
  candidate **beats the incumbent beyond a declared noise floor** on the primary
  metric with no regression anywhere else
  (:mod:`~alpha.evolution.evidence.compare`);
* nothing unmeasured can pass: ``unavailable``, ``error``, a timeout, too few
  samples and ``within_noise`` are distinct non-passing states
  (:mod:`~alpha.evolution.evidence.measure`);
* the gate can **refuse to decide**: a change classified
  irreversible/external/financial/data-destroying yields ``needs_human``
  (:mod:`~alpha.evolution.evidence.decision`);
* every proposal, measurement, comparison and verdict is recorded in a
  hash-linked append-only log that can answer "why is this the current
  default?" (:mod:`~alpha.evolution.evidence.provenance`);
* it cannot certify itself: this package's own files are an evaluation surface,
  so editing its decision logic is *detected*
  (:data:`~alpha.evolution.evidence.integrity.SELF_PACKAGE_GLOBS`).

Public names are installed with :pep:`562` lazy exports through
:func:`alpha.memory._lazy_exports.install_lazy_exports`, matching the memory
package's import-cycle hygiene contract, so importing
``alpha.evolution.evidence`` stays cheap and side-effect free.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alpha.memory._lazy_exports import install_lazy_exports

_EXPORTS = {
    "COMPARISON_STATUSES": "models",
    "DEFAULT_HUMAN_CLASSIFICATION_PATTERNS": "config",
    "DEFAULT_INTEGRITY_PATH_GLOBS": "integrity",
    "DEFAULT_REQUIRED_GATES": "config",
    "EVIDENCE_LABELS": "models",
    "GATE_STATUSES": "models",
    "INTEGRITY_STATUSES": "models",
    "SELF_PACKAGE_GLOBS": "integrity",
    "VERDICT_STATUSES": "models",
    "ChangedPath": "integrity",
    "CommandSource": "measure",
    "CommandRun": "measure",
    "Comparison": "models",
    "ComparisonPolicy": "compare",
    "ComparisonReport": "models",
    "EvaluationIntegrityReport": "models",
    "EvidenceEvaluation": "models",
    "EvolutionEvidenceConfig": "config",
    "Gate": "gates",
    "GateContext": "gates",
    "GateResult": "models",
    "IntegrityFinding": "models",
    "IntegrityPolicy": "integrity",
    "Measurement": "models",
    "MeasurementRequest": "measure",
    "MeasurementSource": "measure",
    "ProbeOutcome": "gates",
    "Proposal": "models",
    "ProvenanceEntry": "provenance",
    "ProvenanceLog": "provenance",
    "ReplayReport": "provenance",
    "ScriptedSource": "measure",
    "UnavailableSource": "measure",
    "Verdict": "models",
    "build_repo_precondition_gates": "gates",
    "check_evaluator_integrity": "integrity",
    "classify_human_review": "decision",
    "collect_measurements": "measure",
    "command_gate": "gates",
    "compare_measurements": "compare",
    "comparison_status_for": "compare",
    "decide_evidence_verdict": "decision",
    "default_gate_registry": "gates",
    "default_integrity_path_globs": "integrity",
    "default_integrity_policy": "integrity",
    "default_measurement_requests": "service",
    "evaluate_proposal": "service",
    "new_proposal": "models",
    "replay_chain": "provenance",
    "run_argv": "measure",
    "run_required_gates": "gates",
}

if TYPE_CHECKING:  # pragma: no cover - import-time-free type surface
    from alpha.evolution.evidence.compare import ComparisonPolicy as ComparisonPolicy
    from alpha.evolution.evidence.compare import compare_measurements as compare_measurements
    from alpha.evolution.evidence.compare import comparison_status_for as comparison_status_for
    from alpha.evolution.evidence.config import DEFAULT_HUMAN_CLASSIFICATION_PATTERNS as DEFAULT_HUMAN_CLASSIFICATION_PATTERNS
    from alpha.evolution.evidence.config import DEFAULT_REQUIRED_GATES as DEFAULT_REQUIRED_GATES
    from alpha.evolution.evidence.config import EvolutionEvidenceConfig as EvolutionEvidenceConfig
    from alpha.evolution.evidence.decision import classify_human_review as classify_human_review
    from alpha.evolution.evidence.decision import decide_evidence_verdict as decide_evidence_verdict
    from alpha.evolution.evidence.gates import Gate as Gate
    from alpha.evolution.evidence.gates import GateContext as GateContext
    from alpha.evolution.evidence.gates import ProbeOutcome as ProbeOutcome
    from alpha.evolution.evidence.gates import build_repo_precondition_gates as build_repo_precondition_gates
    from alpha.evolution.evidence.gates import command_gate as command_gate
    from alpha.evolution.evidence.gates import default_gate_registry as default_gate_registry
    from alpha.evolution.evidence.gates import run_required_gates as run_required_gates
    from alpha.evolution.evidence.integrity import DEFAULT_INTEGRITY_PATH_GLOBS as DEFAULT_INTEGRITY_PATH_GLOBS
    from alpha.evolution.evidence.integrity import SELF_PACKAGE_GLOBS as SELF_PACKAGE_GLOBS
    from alpha.evolution.evidence.integrity import ChangedPath as ChangedPath
    from alpha.evolution.evidence.integrity import IntegrityPolicy as IntegrityPolicy
    from alpha.evolution.evidence.integrity import check_evaluator_integrity as check_evaluator_integrity
    from alpha.evolution.evidence.integrity import default_integrity_path_globs as default_integrity_path_globs
    from alpha.evolution.evidence.integrity import default_integrity_policy as default_integrity_policy
    from alpha.evolution.evidence.measure import CommandRun as CommandRun
    from alpha.evolution.evidence.measure import CommandSource as CommandSource
    from alpha.evolution.evidence.measure import MeasurementRequest as MeasurementRequest
    from alpha.evolution.evidence.measure import MeasurementSource as MeasurementSource
    from alpha.evolution.evidence.measure import ScriptedSource as ScriptedSource
    from alpha.evolution.evidence.measure import UnavailableSource as UnavailableSource
    from alpha.evolution.evidence.measure import collect_measurements as collect_measurements
    from alpha.evolution.evidence.measure import run_argv as run_argv
    from alpha.evolution.evidence.models import COMPARISON_STATUSES as COMPARISON_STATUSES
    from alpha.evolution.evidence.models import EVIDENCE_LABELS as EVIDENCE_LABELS
    from alpha.evolution.evidence.models import GATE_STATUSES as GATE_STATUSES
    from alpha.evolution.evidence.models import INTEGRITY_STATUSES as INTEGRITY_STATUSES
    from alpha.evolution.evidence.models import VERDICT_STATUSES as VERDICT_STATUSES
    from alpha.evolution.evidence.models import Comparison as Comparison
    from alpha.evolution.evidence.models import ComparisonReport as ComparisonReport
    from alpha.evolution.evidence.models import EvaluationIntegrityReport as EvaluationIntegrityReport
    from alpha.evolution.evidence.models import EvidenceEvaluation as EvidenceEvaluation
    from alpha.evolution.evidence.models import GateResult as GateResult
    from alpha.evolution.evidence.models import IntegrityFinding as IntegrityFinding
    from alpha.evolution.evidence.models import Measurement as Measurement
    from alpha.evolution.evidence.models import Proposal as Proposal
    from alpha.evolution.evidence.models import Verdict as Verdict
    from alpha.evolution.evidence.models import new_proposal as new_proposal
    from alpha.evolution.evidence.provenance import ProvenanceEntry as ProvenanceEntry
    from alpha.evolution.evidence.provenance import ProvenanceLog as ProvenanceLog
    from alpha.evolution.evidence.provenance import ReplayReport as ReplayReport
    from alpha.evolution.evidence.provenance import replay_chain as replay_chain
    from alpha.evolution.evidence.service import default_measurement_requests as default_measurement_requests
    from alpha.evolution.evidence.service import evaluate_proposal as evaluate_proposal

install_lazy_exports(__name__, _EXPORTS)
