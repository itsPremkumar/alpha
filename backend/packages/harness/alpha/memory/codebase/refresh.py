"""Coordinator for serialized, atomic codebase structure refreshes."""

from __future__ import annotations

import threading
from collections.abc import Iterable
from pathlib import Path

from .config import CodebaseConfig
from .graph import DependencyGraph
from .impact import compute_change_impact
from .indexer import CodebaseIndexer, FileLister, FileReader
from .models import ChangeImpact, CodebaseSnapshot, DependencyEdge, RefreshReport
from .store import CodebaseStore

_refresh_locks_guard = threading.Lock()
_refresh_locks: dict[str, threading.RLock] = {}


def _refresh_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _refresh_locks_guard:
        lock = _refresh_locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _refresh_locks[key] = lock
        return lock


class CodebaseRefresh:
    """Refresh a repository with diff-by-hash semantics and a visibility fence.

    The lock covers listing, parsing, graph reconstruction, and the store write;
    a second concurrent caller therefore observes the first call's complete
    snapshot rather than a partially updated in-memory structure.  The store's
    atomic replace provides the corresponding on-disk visibility boundary.
    """

    def __init__(
        self,
        config: CodebaseConfig | None = None,
        *,
        indexer: CodebaseIndexer | None = None,
        store: CodebaseStore | None = None,
        repo_id: str = "default",
        file_lister: FileLister | None = None,
        file_reader: FileReader | None = None,
    ) -> None:
        self.config = config or (indexer.config if indexer is not None else CodebaseConfig())
        self.indexer = indexer or CodebaseIndexer(self.config, file_lister=file_lister, file_reader=file_reader, repo_id=repo_id)
        self.store = store or CodebaseStore(repo_id=repo_id, config=self.config)
        self.repo_id = str(repo_id or "default")
        self.impact_depth_cap = self.config.max_impact_depth
        self._last_snapshot: CodebaseSnapshot | None = None
        self._lock = _refresh_lock(self.store.path_for(self.repo_id))

    def change_impact(
        self,
        source: CodebaseSnapshot | DependencyGraph | Iterable[DependencyEdge],
        path: str,
        *,
        partial_index: bool = False,
        test_paths: Iterable[str] | None = None,
    ) -> ChangeImpact:
        """Compute impact using the configured bounded traversal depth."""

        return compute_change_impact(source, path, depth_cap=self.impact_depth_cap, partial_index=partial_index, test_paths=test_paths)

    def _indexer_for(
        self,
        lister: FileLister | None,
        reader: FileReader | None,
        repo_id: str,
    ) -> CodebaseIndexer:
        if lister is None and reader is None:
            return self.indexer
        return CodebaseIndexer(
            self.config,
            file_lister=lister or self.indexer.file_lister,
            file_reader=reader or self.indexer.file_reader,
            repo_id=repo_id,
            repo_root=self.indexer.repo_root,
            clock=self.indexer.clock,
        )

    def refresh(
        self,
        repo_id: str | None = None,
        *,
        previous_snapshot: CodebaseSnapshot | None = None,
        previous: CodebaseSnapshot | None = None,
        lister: FileLister | None = None,
        reader: FileReader | None = None,
    ) -> RefreshReport:
        """Refresh once and return a fully disclosed report."""

        if previous is not None and previous_snapshot is not None and previous is not previous_snapshot:
            raise ValueError("provide only one previous snapshot")
        previous_snapshot = previous_snapshot if previous_snapshot is not None else previous
        target_repo = str(repo_id or self.repo_id)
        if not self.config.enabled:
            return RefreshReport(
                repo_id=target_repo,
                status="disabled",
                disclosures=["disabled"],
            )
        with _refresh_lock(self.store.path_for(target_repo)):
            previous = previous_snapshot
            if previous is None:
                previous = self.store.latest(target_repo)
            if previous is None:
                previous = self._last_snapshot
            try:
                snapshot = self._indexer_for(lister, reader, target_repo).index(previous, repo_id=target_repo)
                old_modules = {module.path: module for module in previous.modules} if previous is not None else {}
                new_modules = {module.path: module for module in snapshot.modules}
                added = sorted(set(new_modules) - set(old_modules))
                removed = sorted(set(old_modules) - set(new_modules))
                updated = sorted(path for path in set(old_modules) & set(new_modules) if old_modules[path].content_hash != new_modules[path].content_hash)
                unchanged = sorted(path for path in set(old_modules) & set(new_modules) if path not in updated)
                stored = self.store.save(snapshot, repo_id=target_repo)
                self._last_snapshot = stored
                reasons = dict(snapshot.skipped_reasons)
                for path in snapshot.unparsed_files:
                    reasons.setdefault(path, "unparsed")
                return RefreshReport(
                    repo_id=target_repo,
                    status="succeeded",
                    added=added,
                    updated=updated,
                    removed=removed,
                    skipped=snapshot.skipped_files,
                    unchanged=unchanged,
                    reasons=reasons,
                    read_files=snapshot.read_paths,
                    changed_count=len(added) + len(updated) + len(removed),
                    snapshot_version=stored.version,
                    partial_index=snapshot.partial_index,
                    disclosures=snapshot.disclosures,
                )
            except Exception as exc:  # noqa: BLE001 - report an honest failed refresh
                return RefreshReport(
                    repo_id=target_repo,
                    status="failed",
                    error=f"{type(exc).__name__}: {exc}",
                    disclosures=["refresh_failed: no partial snapshot was published"],
                )

    run = refresh
    refresh_snapshot = refresh


RefreshCoordinator = CodebaseRefresh


def refresh_snapshot(
    source: CodebaseSnapshot | CodebaseIndexer,
    *,
    config: CodebaseConfig | None = None,
    store: CodebaseStore | None = None,
    repo_id: str = "default",
) -> RefreshReport:
    """Convenience function for refreshing an injected indexer or snapshot source.

    A prebuilt snapshot is saved without rereading files; an indexer is wrapped
    in the normal serialized coordinator.  This function exists for small hosts
    that do not need to retain a coordinator object.
    """

    effective_config = config or (source.config if isinstance(source, CodebaseIndexer) else CodebaseConfig())
    if not effective_config.enabled:
        return RefreshReport(repo_id=repo_id, status="disabled", disclosures=["disabled"])
    if isinstance(source, CodebaseSnapshot):
        target_store = store or CodebaseStore(repo_id=repo_id, config=effective_config)
        stored = target_store.save(source, repo_id=repo_id)
        return RefreshReport(repo_id=repo_id, status="succeeded", added=[item.path for item in stored.modules], snapshot_version=stored.version, partial_index=stored.partial_index, disclosures=stored.disclosures)
    coordinator = CodebaseRefresh(effective_config, indexer=source, store=store, repo_id=repo_id)
    return coordinator.refresh(repo_id)


__all__ = ["CodebaseRefresh", "RefreshCoordinator", "RefreshReport", "refresh_snapshot"]
