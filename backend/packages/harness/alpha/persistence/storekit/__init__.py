"""StoreKit: crash-safe files, cross-process locks, and versioned documents.

The package is additive and default-off.  Public names resolve lazily
(:pep:`562`) through
:func:`alpha.memory._lazy_exports.install_lazy_exports`, so importing
``alpha.persistence.storekit`` does not eagerly import every store, model, or
pydantic config.  The explicit ``TYPE_CHECKING`` block keeps the same surface
visible to type checkers and IDEs without changing runtime import behavior.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alpha.memory._lazy_exports import install_lazy_exports

_EXPORTS = {
    "ATOMIC_WRITE_STEPS": "atomic",
    "AtomicWriteResult": "atomic",
    "Document": "documents",
    "DocumentCorruptError": "documents",
    "DocumentEnvelope": "documents",
    "DocumentLoadResult": "documents",
    "DocumentValidation": "documents",
    "FileLock": "locking",
    "FsyncPolicy": "atomic",
    "GlobalBudgetReport": "retention",
    "GlobalRetentionBudget": "retention",
    "InjectedCrash": "atomic",
    "LockBackendInfo": "locking",
    "LockMode": "locking",
    "LockResult": "locking",
    "LockStatus": "locking",
    "Migration": "migrations",
    "MigrationError": "migrations",
    "MigrationPlan": "migrations",
    "MigrationRegistry": "migrations",
    "MigrationRequirement": "registry",
    "MigrationResult": "migrations",
    "DEFAULT_STORE_REGISTRATIONS": "registry",
    "CoercionResult": "degradation",
    "DAMAGE_SCOPE": "degradation",
    "DamageClass": "degradation",
    "DamageReport": "degradation",
    "Degradation": "degradation",
    "FAIL_CLOSED_CLASSES": "degradation",
    "RepairOutcome": "degradation",
    "SELF_HEALING": "degradation",
    "SafeRepair": "degradation",
    "UNREADABLE_PLACEHOLDER": "degradation",
    "UnsafeRepair": "degradation",
    "classify_damage": "degradation",
    "coerce_records": "degradation",
    "degrade": "degradation",
    "index_health_probe": "degradation",
    "RegistryFootprint": "registry",
    "RegistryReport": "registry",
    "RetentionAction": "retention",
    "RetentionBudget": "retention",
    "RetentionCandidate": "retention",
    "RetentionDecision": "retention",
    "RetentionItem": "retention",
    "RetentionPolicy": "retention",
    "RetentionReport": "retention",
    "ScopedStore": "store",
    "StaleLockInfo": "locking",
    "StoreIndex": "store",
    "StoreKitConfig": "config",
    "StoreReadResult": "store",
    "StoreRegistration": "registry",
    "StoreRegistry": "registry",
    "StoreResult": "store",
    "StoreUnavailableError": "store",
    "StoreWriteResult": "store",
    "atomic_write_bytes": "atomic",
    "atomic_write_json": "atomic",
    "atomic_write_text": "atomic",
    "build_default_registry": "registry",
    "canonical_json": "documents",
    "checksum_for": "documents",
    "decide_retention": "retention",
    "decode_document": "documents",
    "directory_fsync_supported": "atomic",
    "file_lock_path": "locking",
    "fsync_directory": "atomic",
    "inspect_lock": "locking",
    "load_document": "documents",
    "load_store_kit_config": "config",
    "lock_backend": "locking",
    "lock_order_key": "locking",
    "make_document": "documents",
    "migrate_document": "migrations",
    "normalize_fsync_policy": "atomic",
    "ordered_lock_paths": "locking",
    "ordered_locks": "locking",
    "pid_alive": "locking",
    "plan_global_retention": "retention",
    "plan_migrations": "migrations",
    "plan_retention": "retention",
    "quarantine_document": "documents",
    "register_store": "registry",
    "save_document": "documents",
    "store_kit_enabled": "config",
    "validate_document": "documents",
    "try_plan_migrations": "migrations",
    "write_document": "documents",
}

if TYPE_CHECKING:  # pragma: no cover - import-time-free type surface
    from .atomic import (
        ATOMIC_WRITE_STEPS as ATOMIC_WRITE_STEPS,
    )
    from .atomic import (
        AtomicWriteResult as AtomicWriteResult,
    )
    from .atomic import (
        FsyncPolicy as FsyncPolicy,
    )
    from .atomic import (
        InjectedCrash as InjectedCrash,
    )
    from .atomic import (
        atomic_write_bytes as atomic_write_bytes,
    )
    from .atomic import (
        atomic_write_json as atomic_write_json,
    )
    from .atomic import (
        atomic_write_text as atomic_write_text,
    )
    from .atomic import (
        directory_fsync_supported as directory_fsync_supported,
    )
    from .atomic import (
        fsync_directory as fsync_directory,
    )
    from .atomic import (
        normalize_fsync_policy as normalize_fsync_policy,
    )
    from .config import (
        StoreKitConfig as StoreKitConfig,
    )
    from .config import (
        load_store_kit_config as load_store_kit_config,
    )
    from .config import (
        store_kit_enabled as store_kit_enabled,
    )
    from .documents import (
        Document as Document,
    )
    from .documents import (
        DocumentCorruptError as DocumentCorruptError,
    )
    from .documents import (
        DocumentEnvelope as DocumentEnvelope,
    )
    from .documents import (
        DocumentLoadResult as DocumentLoadResult,
    )
    from .documents import (
        DocumentValidation as DocumentValidation,
    )
    from .documents import (
        canonical_json as canonical_json,
    )
    from .documents import (
        checksum_for as checksum_for,
    )
    from .documents import (
        decode_document as decode_document,
    )
    from .documents import (
        load_document as load_document,
    )
    from .documents import (
        make_document as make_document,
    )
    from .documents import (
        quarantine_document as quarantine_document,
    )
    from .documents import (
        save_document as save_document,
    )
    from .documents import (
        validate_document as validate_document,
    )
    from .documents import (
        write_document as write_document,
    )
    from .locking import (
        FileLock as FileLock,
    )
    from .locking import (
        LockBackendInfo as LockBackendInfo,
    )
    from .locking import (
        LockMode as LockMode,
    )
    from .locking import (
        LockResult as LockResult,
    )
    from .locking import (
        LockStatus as LockStatus,
    )
    from .locking import (
        StaleLockInfo as StaleLockInfo,
    )
    from .locking import (
        file_lock_path as file_lock_path,
    )
    from .locking import (
        inspect_lock as inspect_lock,
    )
    from .locking import (
        lock_backend as lock_backend,
    )
    from .locking import (
        lock_order_key as lock_order_key,
    )
    from .locking import (
        ordered_lock_paths as ordered_lock_paths,
    )
    from .locking import (
        ordered_locks as ordered_locks,
    )
    from .locking import (
        pid_alive as pid_alive,
    )
    from .migrations import (
        Migration as Migration,
    )
    from .migrations import (
        MigrationError as MigrationError,
    )
    from .migrations import (
        MigrationPlan as MigrationPlan,
    )
    from .migrations import (
        MigrationRegistry as MigrationRegistry,
    )
    from .migrations import (
        MigrationResult as MigrationResult,
    )
    from .migrations import (
        migrate_document as migrate_document,
    )
    from .migrations import (
        plan_migrations as plan_migrations,
    )
    from .migrations import (
        try_plan_migrations as try_plan_migrations,
    )
    from .registry import (
        DEFAULT_STORE_REGISTRATIONS as DEFAULT_STORE_REGISTRATIONS,
    )
    from .registry import (
        MigrationRequirement as MigrationRequirement,
    )
    from .registry import (
        RegistryFootprint as RegistryFootprint,
    )
    from .registry import (
        RegistryReport as RegistryReport,
    )
    from .registry import (
        StoreRegistration as StoreRegistration,
    )
    from .registry import (
        StoreRegistry as StoreRegistry,
    )
    from .registry import (
        build_default_registry as build_default_registry,
    )
    from .registry import (
        register_store as register_store,
    )
    from .retention import (
        GlobalBudgetReport as GlobalBudgetReport,
    )
    from .retention import (
        GlobalRetentionBudget as GlobalRetentionBudget,
    )
    from .retention import (
        RetentionAction as RetentionAction,
    )
    from .retention import (
        RetentionBudget as RetentionBudget,
    )
    from .retention import (
        RetentionCandidate as RetentionCandidate,
    )
    from .retention import (
        RetentionDecision as RetentionDecision,
    )
    from .retention import (
        RetentionItem as RetentionItem,
    )
    from .retention import (
        RetentionPolicy as RetentionPolicy,
    )
    from .retention import (
        RetentionReport as RetentionReport,
    )
    from .retention import (
        decide_retention as decide_retention,
    )
    from .retention import (
        plan_global_retention as plan_global_retention,
    )
    from .retention import (
        plan_retention as plan_retention,
    )
    from .store import (
        ScopedStore as ScopedStore,
    )
    from .store import (
        StoreIndex as StoreIndex,
    )
    from .store import (
        StoreReadResult as StoreReadResult,
    )
    from .store import (
        StoreResult as StoreResult,
    )
    from .store import (
        StoreUnavailableError as StoreUnavailableError,
    )
    from .store import (
        StoreWriteResult as StoreWriteResult,
    )

install_lazy_exports(__name__, _EXPORTS)
