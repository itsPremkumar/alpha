"""Memory health and observability plane.

The public surface is installed lazily so importing a leaf module (for
example, ``alpha.memory.health.config`` from shared configuration code) does
not eagerly load probes, persistence, or the facade.  The facade itself uses
only injected collaborators and never imports a sibling memory package.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alpha.memory._lazy_exports import install_lazy_exports

_EXPORTS = {
    "ALERT_SEVERITIES": "models",
    "Alert": "models",
    "AlertSeverity": "models",
    "CallableProbe": "probes",
    "Clock": "probes",
    "Comparator": "models",
    "COMPARATORS": "models",
    "ComponentHealth": "models",
    "DuplicateProbeError": "registry",
    "FreshnessProbe": "probes",
    "HEALTH_STATES": "models",
    "HealthConfig": "config",
    "HealthProbeRegistry": "registry",
    "HealthReport": "models",
    "HealthState": "models",
    "LatencyProbe": "probes",
    "MemoryHealth": "health",
    "MetricKind": "metrics",
    "MetricSample": "models",
    "MetricSeries": "models",
    "MetricsRegistry": "metrics",
    "Probe": "probes",
    "QuotaProbe": "probes",
    "RegistryCapacityError": "registry",
    "RegistryError": "registry",
    "SLO": "models",
    "SLOEvaluation": "models",
    "SLOEvaluator": "slo",
    "SLOResult": "models",
    "SLOStatus": "models",
    "SLOTarget": "models",
    "StoreCountProbe": "probes",
    "atomic_write_text": "paths",
    "DEFAULT_SEGMENT": "paths",
    "evaluate_slo": "slo",
    "evaluate_slos": "slo",
    "health_root": "paths",
    "metric_path": "paths",
    "metrics_path": "paths",
    "percentile": "metrics",
    "preserve_corrupt": "paths",
    "read_all_keys": "config",
    "read_json": "paths",
    "read_json_with_status": "paths",
    "read_metrics": "paths",
    "read_report": "paths",
    "report_path": "paths",
    "safe_segment": "paths",
    "write_json": "paths",
    "write_metrics": "paths",
    "write_report": "paths",
}

if TYPE_CHECKING:  # pragma: no cover - import-time-free type surface
    # Explicit ``X as X`` re-exports are required for type checkers and IDEs;
    # runtime names are resolved by the PEP 562 installer above.
    from alpha.memory.health.config import HealthConfig as HealthConfig
    from alpha.memory.health.config import read_all_keys as read_all_keys
    from alpha.memory.health.health import MemoryHealth as MemoryHealth
    from alpha.memory.health.metrics import MetricKind as MetricKind
    from alpha.memory.health.metrics import MetricsRegistry as MetricsRegistry
    from alpha.memory.health.metrics import percentile as percentile
    from alpha.memory.health.models import ALERT_SEVERITIES as ALERT_SEVERITIES
    from alpha.memory.health.models import COMPARATORS as COMPARATORS
    from alpha.memory.health.models import HEALTH_STATES as HEALTH_STATES
    from alpha.memory.health.models import SLO as SLO
    from alpha.memory.health.models import Alert as Alert
    from alpha.memory.health.models import AlertSeverity as AlertSeverity
    from alpha.memory.health.models import Comparator as Comparator
    from alpha.memory.health.models import ComponentHealth as ComponentHealth
    from alpha.memory.health.models import HealthReport as HealthReport
    from alpha.memory.health.models import HealthState as HealthState
    from alpha.memory.health.models import MetricSample as MetricSample
    from alpha.memory.health.models import MetricSeries as MetricSeries
    from alpha.memory.health.models import SLOEvaluation as SLOEvaluation
    from alpha.memory.health.models import SLOResult as SLOResult
    from alpha.memory.health.models import SLOStatus as SLOStatus
    from alpha.memory.health.models import SLOTarget as SLOTarget
    from alpha.memory.health.paths import DEFAULT_SEGMENT as DEFAULT_SEGMENT
    from alpha.memory.health.paths import atomic_write_text as atomic_write_text
    from alpha.memory.health.paths import health_root as health_root
    from alpha.memory.health.paths import metric_path as metric_path
    from alpha.memory.health.paths import metrics_path as metrics_path
    from alpha.memory.health.paths import preserve_corrupt as preserve_corrupt
    from alpha.memory.health.paths import read_json as read_json
    from alpha.memory.health.paths import read_json_with_status as read_json_with_status
    from alpha.memory.health.paths import read_metrics as read_metrics
    from alpha.memory.health.paths import read_report as read_report
    from alpha.memory.health.paths import report_path as report_path
    from alpha.memory.health.paths import safe_segment as safe_segment
    from alpha.memory.health.paths import write_json as write_json
    from alpha.memory.health.paths import write_metrics as write_metrics
    from alpha.memory.health.paths import write_report as write_report
    from alpha.memory.health.probes import CallableProbe as CallableProbe
    from alpha.memory.health.probes import Clock as Clock
    from alpha.memory.health.probes import FreshnessProbe as FreshnessProbe
    from alpha.memory.health.probes import LatencyProbe as LatencyProbe
    from alpha.memory.health.probes import Probe as Probe
    from alpha.memory.health.probes import QuotaProbe as QuotaProbe
    from alpha.memory.health.probes import StoreCountProbe as StoreCountProbe
    from alpha.memory.health.registry import DuplicateProbeError as DuplicateProbeError
    from alpha.memory.health.registry import HealthProbeRegistry as HealthProbeRegistry
    from alpha.memory.health.registry import RegistryCapacityError as RegistryCapacityError
    from alpha.memory.health.registry import RegistryError as RegistryError
    from alpha.memory.health.slo import SLOEvaluator as SLOEvaluator
    from alpha.memory.health.slo import evaluate_slo as evaluate_slo
    from alpha.memory.health.slo import evaluate_slos as evaluate_slos

install_lazy_exports(__name__, _EXPORTS)
