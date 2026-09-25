"""Prospective (intentional) memory for Alpha.

This package records what the agent owes the future: user commitments,
reminders, and trigger-bound obligations.  It is the canonical prospective
/intentional memory type identified in ``docs/MEMORY_TYPES.md`` row 14, kept
separate from factual/episodic memory because each record has an explicit
lifecycle and provenance trail.

Design provenance: the storage, gate, and disclosure conventions deliberately
follow Alpha's existing L1 memory patterns (per-user layout, atomic writes,
fail-closed corruption handling, and honest operation results).  The records,
trigger grammar, lifecycle policy, and bounded recurrence expansion in this
package are original Python written for Alpha; no third-party code is copied."""

from __future__ import annotations

from typing import TYPE_CHECKING

from alpha.memory._lazy_exports import install_lazy_exports

_EXPORTS = {
    "ConfigLoadResult": "config",
    "ExpiryBatchResult": "store",
    "LEGAL_TRANSITIONS": "models",
    "LifecycleResult": "models",
    "ProspectiveConfig": "config",
    "ProspectiveConfigError": "config",
    "ProspectiveItem": "models",
    "ProspectiveKind": "models",
    "ProspectiveStatus": "models",
    "ProspectiveStore": "store",
    "ProspectiveSummary": "recall",
    "ProvenanceResult": "provenance",
    "RecallResult": "recall",
    "StoreReadResult": "store",
    "TERMINAL_STATUSES": "models",
    "TRANSITIONS": "models",
    "TriggerContext": "models",
    "TriggerEvaluation": "triggers",
    "TriggerKind": "models",
    "TriggerSpec": "models",
    "VALID_TRANSITIONS": "models",
    "append_event": "provenance",
    "append_transition": "provenance",
    "can_transition": "models",
    "cancel": "lifecycle",
    "complete": "lifecycle",
    "create_item": "lifecycle",
    "due_items": "recall",
    "due_items_result": "recall",
    "evaluate_trigger": "triggers",
    "evaluate_trigger_detailed": "triggers",
    "expand_recurring": "lifecycle",
    "expand_recurring_item": "lifecycle",
    "expire_due": "lifecycle",
    "fire": "lifecycle",
    "items_for_trigger": "recall",
    "items_for_trigger_result": "recall",
    "load_prospective_config": "config",
    "load_prospective_config_result": "config",
    "mark_fired": "lifecycle",
    "mark_surfaced": "lifecycle",
    "matches_trigger": "triggers",
    "new_item_id": "models",
    "prospective_enabled": "config",
    "prospective_root": "config",
    "provenance_path": "provenance",
    "read_entries": "provenance",
    "render_active_block": "recall",
    "render_block": "recall",
    "render_block_result": "recall",
    "render_due_block": "recall",
    "render_trigger_block": "recall",
    "rewrite_day": "provenance",
    "summarize": "recall",
    "summary_counts": "recall",
    "transition_allowed": "lifecycle",
}

if TYPE_CHECKING:  # pragma: no cover - import-time-free type surface
    # Explicit ``X as X`` re-exports: the runtime surface is installed
    # lazily above, so this block exists for type checkers and IDEs only.
    from .config import (
        ConfigLoadResult as ConfigLoadResult,
    )
    from .config import (
        ProspectiveConfig as ProspectiveConfig,
    )
    from .config import (
        ProspectiveConfigError as ProspectiveConfigError,
    )
    from .config import (
        load_prospective_config as load_prospective_config,
    )
    from .config import (
        load_prospective_config_result as load_prospective_config_result,
    )
    from .config import (
        prospective_enabled as prospective_enabled,
    )
    from .config import (
        prospective_root as prospective_root,
    )
    from .lifecycle import (
        cancel as cancel,
    )
    from .lifecycle import (
        complete as complete,
    )
    from .lifecycle import (
        create_item as create_item,
    )
    from .lifecycle import (
        expand_recurring as expand_recurring,
    )
    from .lifecycle import (
        expand_recurring_item as expand_recurring_item,
    )
    from .lifecycle import (
        expire_due as expire_due,
    )
    from .lifecycle import (
        fire as fire,
    )
    from .lifecycle import (
        mark_fired as mark_fired,
    )
    from .lifecycle import (
        mark_surfaced as mark_surfaced,
    )
    from .lifecycle import (
        transition_allowed as transition_allowed,
    )
    from .models import (
        LEGAL_TRANSITIONS as LEGAL_TRANSITIONS,
    )
    from .models import (
        TERMINAL_STATUSES as TERMINAL_STATUSES,
    )
    from .models import (
        TRANSITIONS as TRANSITIONS,
    )
    from .models import (
        VALID_TRANSITIONS as VALID_TRANSITIONS,
    )
    from .models import (
        LifecycleResult as LifecycleResult,
    )
    from .models import (
        ProspectiveItem as ProspectiveItem,
    )
    from .models import (
        ProspectiveKind as ProspectiveKind,
    )
    from .models import (
        ProspectiveStatus as ProspectiveStatus,
    )
    from .models import (
        TriggerContext as TriggerContext,
    )
    from .models import (
        TriggerKind as TriggerKind,
    )
    from .models import (
        TriggerSpec as TriggerSpec,
    )
    from .models import (
        can_transition as can_transition,
    )
    from .models import (
        new_item_id as new_item_id,
    )
    from .provenance import (
        ProvenanceResult as ProvenanceResult,
    )
    from .provenance import (
        append_event as append_event,
    )
    from .provenance import (
        append_transition as append_transition,
    )
    from .provenance import (
        provenance_path as provenance_path,
    )
    from .provenance import (
        read_entries as read_entries,
    )
    from .provenance import (
        rewrite_day as rewrite_day,
    )
    from .recall import (
        ProspectiveSummary as ProspectiveSummary,
    )
    from .recall import (
        RecallResult as RecallResult,
    )
    from .recall import (
        due_items as due_items,
    )
    from .recall import (
        due_items_result as due_items_result,
    )
    from .recall import (
        items_for_trigger as items_for_trigger,
    )
    from .recall import (
        items_for_trigger_result as items_for_trigger_result,
    )
    from .recall import (
        render_active_block as render_active_block,
    )
    from .recall import (
        render_block as render_block,
    )
    from .recall import (
        render_block_result as render_block_result,
    )
    from .recall import (
        render_due_block as render_due_block,
    )
    from .recall import (
        render_trigger_block as render_trigger_block,
    )
    from .recall import (
        summarize as summarize,
    )
    from .recall import (
        summary_counts as summary_counts,
    )
    from .store import (
        ExpiryBatchResult as ExpiryBatchResult,
    )
    from .store import (
        ProspectiveStore as ProspectiveStore,
    )
    from .store import (
        StoreReadResult as StoreReadResult,
    )
    from .triggers import (
        TriggerEvaluation as TriggerEvaluation,
    )
    from .triggers import (
        evaluate_trigger as evaluate_trigger,
    )
    from .triggers import (
        evaluate_trigger_detailed as evaluate_trigger_detailed,
    )
    from .triggers import (
        matches_trigger as matches_trigger,
    )

install_lazy_exports(__name__, _EXPORTS)
