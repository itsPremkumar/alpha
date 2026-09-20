"""DreamStore managing MEMORY.md durable rules and DREAMS.md sleep journals."""

from __future__ import annotations

from pathlib import Path


class DreamStore:
    """Persistent filesystem store for consolidated memory rules and dream cycles."""

    def __init__(self, root_dir: Path | str | None = None):
        if root_dir is None:
            # Default to workspace root or current directory
            root_dir = Path.cwd()
        self.root_dir = Path(root_dir)
        self.memory_path = self.root_dir / "MEMORY.md"
        self.dreams_path = self.root_dir / "DREAMS.md"
        self._ensure_files()

    def _ensure_files(self) -> None:
        if not self.memory_path.exists():
            self.memory_path.parent.mkdir(parents=True, exist_ok=True)
            self.memory_path.write_text(
                "# Long-Term Durable Memory\n\nCurated principles, habits, and knowledge promoted from Dreaming cycles.\n\n",
                encoding="utf-8",
            )
        if not self.dreams_path.exists():
            self.dreams_path.parent.mkdir(parents=True, exist_ok=True)
            self.dreams_path.write_text(
                "# Dreaming Consolidation Journal\n\nNightly and background sleep synthesis records.\n\n",
                encoding="utf-8",
            )

    def read_memory(self) -> str:
        if self.memory_path.exists():
            return self.memory_path.read_text(encoding="utf-8", errors="replace")
        return ""

    def read_dreams(self) -> str:
        if self.dreams_path.exists():
            return self.dreams_path.read_text(encoding="utf-8", errors="replace")
        return ""

    def append_memory(self, rule: str) -> None:
        """Append a validated principle to MEMORY.md if not already present."""
        current = self.read_memory()
        norm_rule = rule.strip()
        if norm_rule not in current:
            with open(self.memory_path, "a", encoding="utf-8") as f:
                f.write(f"- {norm_rule}\n")

    def append_dream(self, entry: str) -> None:
        """Append a dream cycle narrative to DREAMS.md."""
        with open(self.dreams_path, "a", encoding="utf-8") as f:
            f.write(f"\n{entry.strip()}\n")


_global_dream_store: DreamStore | None = None


def get_dream_store(root_dir: Path | str | None = None) -> DreamStore:
    global _global_dream_store
    if _global_dream_store is None or root_dir is not None:
        _global_dream_store = DreamStore(root_dir)
    return _global_dream_store
