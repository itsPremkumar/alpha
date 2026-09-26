"""Alpha Memory Fabric: canonical envelopes, namespaces, and lifecycle.

This package implements sections 8, 9, 18, 19, 20, 21, 25, and 31 of
``references/ALPHA_ADVANCED_OPEN_SOURCE_AGENTIC_MEMORY_SYSTEM.md`` and the
reconsolidation rules sketched in section 3.4.  It is cross-cutting storage
infrastructure for the types catalogued in ``docs/MEMORY_TYPES.md`` (working,
episodic, semantic, procedural, spatio-temporal, and future types), not a new
memory taxonomy row.

Runtime exports use :pep:`562` lazy loading through
:func:`alpha.memory._lazy_exports.install_lazy_exports`.  The shared config
schema can therefore import ``FabricConfig`` without an import cycle through
this facade's models, store, lifecycle, or runtime-home seams.
"""

from __future__ import annotations

import sys
from importlib import import_module
from types import ModuleType
from typing import TYPE_CHECKING

from alpha.memory._lazy_exports import install_lazy_exports

_EXPORTS = {
    "FabricConfig": "config",
    "FabricStore": "store",
    "LEGAL_TRANSITIONS": "lifecycle",
    "Lifecycle": "models",
    "LifecycleStatus": "models",
    "MemoryEnvelope": "models",
    "MemoryEnvelopeStore": "store",
    "MemoryFabricStore": "store",
    "MemoryScope": "models",
    "PROMOTION_ORDER": "lifecycle",
    "ProvenanceResult": "provenance",
    "Quality": "models",
    "Representations": "models",
    "Security": "models",
    "SecurityClassification": "models",
    "SourceRef": "models",
    "StoreReadResult": "store",
    "StoreResult": "store",
    "Timestamps": "models",
    "TemporalResult": "temporal",
    "Transition": "lifecycle",
    "TransitionResult": "lifecycle",
    "VALID_TRANSITIONS": "lifecycle",
    "ForgetReport": "forget",
    "ForgetService": "forget",
    "append_event": "provenance",
    "archive": "forget",
    "as_of": "temporal",
    "assert_temporal_integrity": "temporal",
    "contradict": "temporal",
    "demote": "lifecycle",
    "due_transitions": "lifecycle",
    "fabric_enabled": "config",
    "fabric_root": "config",
    "forget": "forget",
    "forget_scope": "forget",
    "generate_memory_id": "models",
    "latest": "temporal",
    "load_fabric_config": "config",
    "promote": "lifecycle",
    "provenance_path": "provenance",
    "read_entries": "provenance",
    "restore": "forget",
    "restore_envelope": "lifecycle",
    "scope_document_path": "store",
    "supersede": "temporal",
    "transition": "lifecycle",
    "transition_allowed": "lifecycle",
}

if TYPE_CHECKING:  # pragma: no cover - type-checker-only public surface
    from .config import FabricConfig as FabricConfig
    from .config import fabric_enabled as fabric_enabled
    from .config import fabric_root as fabric_root
    from .config import load_fabric_config as load_fabric_config
    from .forget import ForgetReport as ForgetReport
    from .forget import ForgetService as ForgetService
    from .forget import archive as archive
    from .forget import forget as forget
    from .forget import forget_scope as forget_scope
    from .forget import restore as restore
    from .lifecycle import LEGAL_TRANSITIONS as LEGAL_TRANSITIONS
    from .lifecycle import PROMOTION_ORDER as PROMOTION_ORDER
    from .lifecycle import VALID_TRANSITIONS as VALID_TRANSITIONS
    from .lifecycle import Transition as Transition
    from .lifecycle import TransitionResult as TransitionResult
    from .lifecycle import demote as demote
    from .lifecycle import due_transitions as due_transitions
    from .lifecycle import promote as promote
    from .lifecycle import restore_envelope as restore_envelope
    from .lifecycle import transition as transition
    from .lifecycle import transition_allowed as transition_allowed
    from .models import Lifecycle as Lifecycle
    from .models import LifecycleStatus as LifecycleStatus
    from .models import MemoryEnvelope as MemoryEnvelope
    from .models import MemoryScope as MemoryScope
    from .models import Quality as Quality
    from .models import Representations as Representations
    from .models import Security as Security
    from .models import SecurityClassification as SecurityClassification
    from .models import SourceRef as SourceRef
    from .models import Timestamps as Timestamps
    from .models import generate_memory_id as generate_memory_id
    from .provenance import ProvenanceResult as ProvenanceResult
    from .provenance import append_event as append_event
    from .provenance import provenance_path as provenance_path
    from .provenance import read_entries as read_entries
    from .store import FabricStore as FabricStore
    from .store import MemoryEnvelopeStore as MemoryEnvelopeStore
    from .store import MemoryFabricStore as MemoryFabricStore
    from .store import StoreReadResult as StoreReadResult
    from .store import StoreResult as StoreResult
    from .store import scope_document_path as scope_document_path
    from .temporal import TemporalResult as TemporalResult
    from .temporal import as_of as as_of
    from .temporal import assert_temporal_integrity as assert_temporal_integrity
    from .temporal import contradict as contradict
    from .temporal import latest as latest
    from .temporal import supersede as supersede


install_lazy_exports(__name__, _EXPORTS)


class _FabricModule(ModuleType):
    """Keep the required ``forget.py`` submodule from shadowing its function."""

    def __getattribute__(self, name: str) -> object:
        value = super().__getattribute__(name)
        if name == "forget" and isinstance(value, ModuleType):
            return import_module(f"{__package__}.forget").forget
        return value


sys.modules[__name__].__class__ = _FabricModule
