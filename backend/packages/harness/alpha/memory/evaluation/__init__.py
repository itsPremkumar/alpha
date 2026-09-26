"""Alpha's opt-in, deterministic memory evaluation harness.

The package implements the memory-health vocabulary in plan section 26, the
five LongMemEval-inspired dimensions in section 27, and the session/question
case shape in section 28.  It is an explicit benchmark: ``EvaluationConfig``
is disabled by default, and no provider is contacted at import time.

The JSON files under ``cases/`` are original Alpha cases written for this
harness.  No LoCoMo, LongMemEval, or other third-party dataset/question set is
copied or bundled.  The case files are small synthetic fixtures whose expected
answers and evidence are authored in this repository.

Public names are installed with :pep:`562` lazy exports through
:func:`alpha.memory._lazy_exports.install_lazy_exports`, matching the memory
package's import-cycle hygiene contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alpha.memory._lazy_exports import install_lazy_exports

_EXPORTS = {
    "ABILITIES": "models",
    "Ability": "models",
    "BaselineComparison": "models",
    "Case": "models",
    "CaseLoadError": "suite",
    "CaseResult": "models",
    "Event": "models",
    "EvaluationConfig": "config",
    "MemoryCase": "models",
    "MemoryCaseResult": "models",
    "MemoryProvider": "runner",
    "MemoryRecord": "models",
    "MemorySuiteReport": "models",
    "METRIC_NAMES": "models",
    "ProviderKind": "runner",
    "ProviderProtocolError": "runner",
    "ProviderUnavailable": "runner",
    "Question": "models",
    "QuestionOutcome": "models",
    "RealStoreProvider": "runner",
    "RecallRecord": "models",
    "Record": "models",
    "ResultStatus": "models",
    "ScriptedProvider": "runner",
    "Session": "models",
    "SuiteReport": "models",
    "ThresholdCheck": "models",
    "UnavailableError": "runner",
    "UnavailableProvider": "runner",
    "WriteReceipt": "runner",
    "aggregate_metrics": "metrics",
    "answer_matches": "metrics",
    "compare_baseline": "suite",
    "compose_answer": "runner",
    "compute_abstention_rate": "metrics",
    "compute_accuracy": "metrics",
    "compute_contamination_rate": "metrics",
    "compute_evidence_traceability": "metrics",
    "compute_recall_at_k": "metrics",
    "compute_token_efficiency": "metrics",
    "compute_update_correctness": "metrics",
    "compute_write_precision": "metrics",
    "config_from_mapping": "config",
    "contamination_rate": "metrics",
    "default_cases_dir": "suite",
    "evaluate_thresholds": "suite",
    "evidence_traceability": "metrics",
    "is_refusal": "metrics",
    "load_baseline": "suite",
    "load_case_file": "suite",
    "load_cases": "suite",
    "load_config": "config",
    "load_evaluation_config": "config",
    "metric_denominators": "metrics",
    "metric_observed": "metrics",
    "normalize_answer": "metrics",
    "recall_at_k": "metrics",
    "render": "report",
    "render_json": "report",
    "render_text": "report",
    "read_config": "config",
    "ReportFormat": "report",
    "run_case": "runner",
    "run_suite": "suite",
    "scope_matches": "metrics",
    "token_count": "metrics",
    "token_efficiency": "metrics",
    "update_correctness": "metrics",
    "write_baseline": "suite",
    "write_precision": "metrics",
    "write_recall": "metrics",
    "write_report": "report",
}

if TYPE_CHECKING:  # pragma: no cover - import-time-free type surface
    from .config import EvaluationConfig as EvaluationConfig
    from .config import config_from_mapping as config_from_mapping
    from .config import load_config as load_config
    from .config import load_evaluation_config as load_evaluation_config
    from .config import read_config as read_config
    from .metrics import aggregate_metrics as aggregate_metrics
    from .metrics import answer_matches as answer_matches
    from .metrics import compute_abstention_rate as compute_abstention_rate
    from .metrics import compute_accuracy as compute_accuracy
    from .metrics import compute_contamination_rate as compute_contamination_rate
    from .metrics import compute_evidence_traceability as compute_evidence_traceability
    from .metrics import compute_recall_at_k as compute_recall_at_k
    from .metrics import compute_token_efficiency as compute_token_efficiency
    from .metrics import compute_update_correctness as compute_update_correctness
    from .metrics import compute_write_precision as compute_write_precision
    from .metrics import contamination_rate as contamination_rate
    from .metrics import evidence_traceability as evidence_traceability
    from .metrics import is_refusal as is_refusal
    from .metrics import metric_denominators as metric_denominators
    from .metrics import metric_observed as metric_observed
    from .metrics import normalize_answer as normalize_answer
    from .metrics import recall_at_k as recall_at_k
    from .metrics import scope_matches as scope_matches
    from .metrics import token_count as token_count
    from .metrics import token_efficiency as token_efficiency
    from .metrics import update_correctness as update_correctness
    from .metrics import write_precision as write_precision
    from .metrics import write_recall as write_recall
    from .models import ABILITIES as ABILITIES
    from .models import METRIC_NAMES as METRIC_NAMES
    from .models import Ability as Ability
    from .models import BaselineComparison as BaselineComparison
    from .models import Case as Case
    from .models import CaseResult as CaseResult
    from .models import Event as Event
    from .models import MemoryCase as MemoryCase
    from .models import MemoryCaseResult as MemoryCaseResult
    from .models import MemoryRecord as MemoryRecord
    from .models import MemorySuiteReport as MemorySuiteReport
    from .models import Question as Question
    from .models import QuestionOutcome as QuestionOutcome
    from .models import RecallRecord as RecallRecord
    from .models import Record as Record
    from .models import ResultStatus as ResultStatus
    from .models import Session as Session
    from .models import SuiteReport as SuiteReport
    from .models import ThresholdCheck as ThresholdCheck
    from .report import ReportFormat as ReportFormat
    from .report import render as render
    from .report import render_json as render_json
    from .report import render_text as render_text
    from .report import write_report as write_report
    from .runner import MemoryProvider as MemoryProvider
    from .runner import ProviderKind as ProviderKind
    from .runner import ProviderProtocolError as ProviderProtocolError
    from .runner import ProviderUnavailable as ProviderUnavailable
    from .runner import RealStoreProvider as RealStoreProvider
    from .runner import ScriptedProvider as ScriptedProvider
    from .runner import UnavailableError as UnavailableError
    from .runner import UnavailableProvider as UnavailableProvider
    from .runner import WriteReceipt as WriteReceipt
    from .runner import compose_answer as compose_answer
    from .runner import run_case as run_case
    from .suite import CaseLoadError as CaseLoadError
    from .suite import compare_baseline as compare_baseline
    from .suite import default_cases_dir as default_cases_dir
    from .suite import evaluate_thresholds as evaluate_thresholds
    from .suite import load_baseline as load_baseline
    from .suite import load_case_file as load_case_file
    from .suite import load_cases as load_cases
    from .suite import run_suite as run_suite
    from .suite import write_baseline as write_baseline

install_lazy_exports(__name__, _EXPORTS)
