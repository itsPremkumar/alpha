"""Snapshot and rollback manager for Continual Harness state."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from alpha.harness.continual.state import HarnessState


def _now() -> str:
    return datetime.now(UTC).isoformat()


class HarnessSnapshotManager:
    """Manages versioned snapshots and safe rollback for HarnessState."""

    def __init__(self, state: HarnessState):
        self.state = state

    @property
    def snapshot_dir(self) -> Path | None:
        if self.state.file_path is None:
            return None
        return self.state.file_path.parent / "snapshots"

    def create_snapshot(self, description: str = "") -> str | None:
        """Create an immutable snapshot of current harness state."""
        if self.state.file_path is None or not self.state.file_path.exists():
            # Save first if in-memory had updates but file_path is set
            if self.state.file_path is not None:
                self.state.save()
            else:
                return None

        s_dir = self.snapshot_dir
        if s_dir is None:
            return None
        s_dir.mkdir(parents=True, exist_ok=True)

        snapshot_id = f"snap_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}"
        target_path = s_dir / f"{snapshot_id}.json"

        shutil.copy2(self.state.file_path, target_path)

        manifest_file = s_dir / "manifest.json"
        manifest: list[dict[str, Any]] = []
        if manifest_file.exists():
            try:
                with open(manifest_file, encoding="utf-8") as f:
                    manifest = json.load(f)
            except Exception:
                manifest = []

        manifest.append({
            "snapshot_id": snapshot_id,
            "created_at": _now(),
            "description": description,
            "file": target_path.name,
            "scope": self.state.scope,
            "entry_count": sum(len(v) for v in self.state.entries.values()),
        })

        with open(manifest_file, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        return snapshot_id

    def list_snapshots(self) -> list[dict[str, Any]]:
        s_dir = self.snapshot_dir
        if s_dir is None or not s_dir.exists():
            return []
        manifest_file = s_dir / "manifest.json"
        if not manifest_file.exists():
            return []
        try:
            with open(manifest_file, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def restore_snapshot(self, snapshot_id: str) -> bool:
        """Rollback current harness state to the given snapshot."""
        s_dir = self.snapshot_dir
        if s_dir is None or self.state.file_path is None:
            return False

        snapshot_file = s_dir / f"{snapshot_id}.json"
        if not snapshot_file.exists():
            return False

        # Create a backup snapshot of current state before rollback
        self.create_snapshot(description=f"Auto-backup before rollback to {snapshot_id}")

        shutil.copy2(snapshot_file, self.state.file_path)
        self.state.load()
        return True
