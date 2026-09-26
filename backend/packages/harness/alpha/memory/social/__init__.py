"""Social/shared memory for Alpha (canonical taxonomy row 18).

This package answers who Alpha is dealing with, how each relationship is
changing over time, and which jointly learned facts a particular audience may
read. It implements the ``Social / shared`` type identified in
``docs/MEMORY_TYPES.md`` row 18. The feature is default-off, owner-scoped, and
default-deny across users: team membership and audience labels never replace
an explicit, active, audited ``AudienceGrant``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from alpha.memory._lazy_exports import install_lazy_exports

_EXPORTS = {
    "AccessDecision": "models",
    "AudienceGrant": "models",
    "Counterpart": "models",
    "CounterpartKind": "models",
    "CounterpartWriteResult": "system",
    "ExpiryResult": "system",
    "FactSensitivity": "models",
    "FactWriteResult": "system",
    "GrantExpiryAuditError": "sharing",
    "GrantResult": "sharing",
    "INTERACTION_STATUSES": "models",
    "InteractionStatus": "models",
    "InteractionSummary": "models",
    "InteractionWriteResult": "system",
    "MergeWriteResult": "system",
    "RankedCounterpart": "relationships",
    "RecallBlock": "recall",
    "Relationship": "models",
    "RelationshipManager": "relationships",
    "RelationshipUpdate": "relationships",
    "SharedFact": "models",
    "SharingManager": "sharing",
    "SocialConfig": "config",
    "SocialEventType": "provenance",
    "SocialMemorySystem": "system",
    "SocialProvenance": "provenance",
    "SocialProvenanceError": "provenance",
    "SocialSnapshot": "store",
    "SocialStats": "recall",
    "SocialStore": "store",
    "SocialStoreCapacityError": "store",
    "SocialStoreCorruptError": "store",
    "SummaryModel": "summary",
    "VisibleFacts": "models",
    "decayed_trust": "relationships",
    "relationship_block": "recall",
    "shared_context": "recall",
    "stats": "recall",
    "summarize_interaction": "summary",
}

if TYPE_CHECKING:  # pragma: no cover - import-time-free type surface
    # Explicit ``X as X`` re-exports: the runtime surface is installed
    # lazily above, so this block exists for type checkers and IDEs only.
    from .config import SocialConfig as SocialConfig
    from .models import (
        INTERACTION_STATUSES as INTERACTION_STATUSES,
    )
    from .models import (
        AccessDecision as AccessDecision,
    )
    from .models import (
        AudienceGrant as AudienceGrant,
    )
    from .models import (
        Counterpart as Counterpart,
    )
    from .models import (
        CounterpartKind as CounterpartKind,
    )
    from .models import (
        FactSensitivity as FactSensitivity,
    )
    from .models import (
        InteractionStatus as InteractionStatus,
    )
    from .models import (
        InteractionSummary as InteractionSummary,
    )
    from .models import (
        Relationship as Relationship,
    )
    from .models import (
        SharedFact as SharedFact,
    )
    from .models import (
        VisibleFacts as VisibleFacts,
    )
    from .provenance import (
        SocialEventType as SocialEventType,
    )
    from .provenance import (
        SocialProvenance as SocialProvenance,
    )
    from .provenance import (
        SocialProvenanceError as SocialProvenanceError,
    )
    from .recall import (
        RecallBlock as RecallBlock,
    )
    from .recall import (
        SocialStats as SocialStats,
    )
    from .recall import (
        relationship_block as relationship_block,
    )
    from .recall import (
        shared_context as shared_context,
    )
    from .recall import (
        stats as stats,
    )
    from .relationships import (
        RankedCounterpart as RankedCounterpart,
    )
    from .relationships import (
        RelationshipManager as RelationshipManager,
    )
    from .relationships import (
        RelationshipUpdate as RelationshipUpdate,
    )
    from .relationships import (
        decayed_trust as decayed_trust,
    )
    from .sharing import (
        GrantExpiryAuditError as GrantExpiryAuditError,
    )
    from .sharing import (
        GrantResult as GrantResult,
    )
    from .sharing import (
        SharingManager as SharingManager,
    )
    from .store import (
        SocialSnapshot as SocialSnapshot,
    )
    from .store import (
        SocialStore as SocialStore,
    )
    from .store import (
        SocialStoreCapacityError as SocialStoreCapacityError,
    )
    from .store import (
        SocialStoreCorruptError as SocialStoreCorruptError,
    )
    from .summary import SummaryModel as SummaryModel
    from .summary import summarize_interaction as summarize_interaction
    from .system import (
        CounterpartWriteResult as CounterpartWriteResult,
    )
    from .system import (
        ExpiryResult as ExpiryResult,
    )
    from .system import (
        FactWriteResult as FactWriteResult,
    )
    from .system import (
        InteractionWriteResult as InteractionWriteResult,
    )
    from .system import (
        MergeWriteResult as MergeWriteResult,
    )
    from .system import (
        SocialMemorySystem as SocialMemorySystem,
    )

install_lazy_exports(__name__, _EXPORTS)
