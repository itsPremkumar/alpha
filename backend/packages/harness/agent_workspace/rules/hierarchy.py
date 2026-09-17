"""Hierarchical AGENTS.md Context & Scoped Rules Engine.

Solves the context scaling problem in monorepos:
1. Walks directory paths upwards to discover nested AGENTS.md files:
   project/AGENTS.md -> src/AGENTS.md -> components/AGENTS.md.
2. Cascades rules from project-wide context down to component-specific constraints.
3. Injects scoped contextual blocks into agent prompt without global token bloat.
4. Provides init_deep_scaffold to automatically generate hierarchical AGENTS.md files.
"""

from __future__ import annotations

from pathlib import Path


class HierarchicalRuleEngine:
    """Discovers and resolves hierarchical context from nested AGENTS.md files."""

    def __init__(self, root_dir: Path | None = None):
        self.root_dir = root_dir.resolve() if root_dir else Path.cwd().resolve()

    def discover_context(self, target_path: Path) -> list[dict[str, str]]:
        """Walk up from target_path to root_dir and collect all AGENTS.md files."""
        target = target_path.resolve()
        if target.is_file():
            current = target.parent
        else:
            current = target

        discovered: list[Path] = []
        while True:
            agents_file = current / "AGENTS.md"
            if agents_file.is_file():
                discovered.append(agents_file)

            if current == self.root_dir or current.parent == current:
                break
            try:
                # Ensure we do not walk outside the root_dir
                current.relative_to(self.root_dir)
            except ValueError:
                break
            current = current.parent

        # Order from root-most (least specific) to leaf-most (most specific)
        discovered.reverse()

        results = []
        for file_path in discovered:
            try:
                content = file_path.read_text(encoding="utf-8").strip()
                rel = file_path.relative_to(self.root_dir)
                results.append({
                    "path": str(rel).replace("\\", "/"),
                    "content": content,
                })
            except Exception:
                pass

        return results

    def render_prompt_block(self, target_path: Path) -> str:
        """Render consolidated hierarchical context for prompt injection."""
        contexts = self.discover_context(target_path)
        if not contexts:
            return ""

        blocks = []
        for ctx in contexts:
            blocks.append(f"<!-- Scope: {ctx['path']} -->\n{ctx['content']}")

        joined = "\n\n".join(blocks)
        return f"<hierarchical_project_context target='{target_path}'>\n{joined}\n</hierarchical_project_context>"


def init_deep_scaffold(project_root: Path, subdirs: list[str] | None = None) -> list[Path]:
    """Scaffold hierarchical AGENTS.md files across root and key subdirectories."""
    created: list[Path] = []

    # 1. Root AGENTS.md
    root_agents = project_root / "AGENTS.md"
    if not root_agents.exists():
        root_agents.write_text(
            "# Project Guidelines & Global Architecture\n\n"
            "- Architecture: DeerFlow Autonomous Mega-Agent Harness\n"
            "- Code Quality: 100% test passing required before commits\n",
            encoding="utf-8",
        )
        created.append(root_agents)

    # 2. Subdirectory AGENTS.md files
    default_subdirs = subdirs or ["backend", "frontend"]
    for sub in default_subdirs:
        sub_path = project_root / sub
        if sub_path.is_dir():
            sub_agents = sub_path / "AGENTS.md"
            if not sub_agents.exists():
                sub_agents.write_text(
                    f"# {sub.capitalize()} Scoped Rules\n\n"
                    f"- Scope: Rules strictly applicable to `{sub}/` directory.\n",
                    encoding="utf-8",
                )
                created.append(sub_agents)

    return created
