"""Skills Hub discovery and secure installation engine inspired by Hermes."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from agent_workspace.skills.audit.ast_audit import get_skill_ast_auditor


@dataclass
class SkillPackage:
    name: str
    description: str
    source: str = "github"  # "github", "clawhub", "local"
    url: str = ""
    version: str = "1.0.0"
    author: str = "community"
    code_snippet: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SkillsHub:
    """Discovers and securely installs skill packages with mandatory AST security audits."""

    DEFAULT_CATALOG = [
        SkillPackage(
            name="cartographer",
            description="Deep codebase visualizer and architectural AST analyzer",
            source="github",
            url="https://github.com/community/skill-cartographer",
            code_snippet="def run_map(path='.'):\n    return f'Mapped {path}'",
        ),
        SkillPackage(
            name="git_summarizer",
            description="Analyzes git commit velocity and contributor stats",
            source="clawhub",
            url="https://clawhub.org/skills/git-summary",
            code_snippet="def run_summary():\n    return 'Git velocity 100%'",
        ),
    ]

    def __init__(self, root_dir: Path | str | None = None):
        root = Path(root_dir or Path.cwd())
        self.hub_dir = root / ".deerflow" / "hub"
        self.lock_file = self.hub_dir / "lock.json"
        self.installed_skills_dir = root / ".deerflow" / "skills"
        self._catalog: dict[str, SkillPackage] = {pkg.name: pkg for pkg in self.DEFAULT_CATALOG}
        self._auditor = get_skill_ast_auditor()

    def add_to_catalog(self, package: SkillPackage) -> None:
        self._catalog[package.name] = package

    def search(self, query: str) -> list[SkillPackage]:
        q = query.strip().lower()
        if not q:
            return list(self._catalog.values())
        return [
            pkg for pkg in self._catalog.values()
            if q in pkg.name.lower() or q in pkg.description.lower()
        ]

    def install(self, package_name: str) -> tuple[bool, str]:
        pkg = self._catalog.get(package_name)
        if not pkg:
            return False, f"Package '{package_name}' not found in Skills Hub catalog."

        # 1. Mandatory AST security audit if Python code is present
        if pkg.code_snippet:
            audit = self._auditor.audit_code(pkg.code_snippet)
            if not audit.is_safe:
                return False, f"Security Audit FAILED: Installation blocked due to violations: {'; '.join(audit.violations)}"

        # 2. Write skill files
        target_dir = self.installed_skills_dir / pkg.name
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "SKILL.md").write_text(
            f"---\nname: {pkg.name}\ndescription: {pkg.description}\nversion: {pkg.version}\n---\n\n# {pkg.name}\n\n{pkg.description}\n",
            encoding="utf-8",
        )
        if pkg.code_snippet:
            (target_dir / "main.py").write_text(pkg.code_snippet, encoding="utf-8")

        # 3. Update lockfile
        self.hub_dir.mkdir(parents=True, exist_ok=True)
        lock_data = self._read_lock()
        lock_data[pkg.name] = {
            "version": pkg.version,
            "source": pkg.source,
            "installed_at": time.time(),
            "url": pkg.url,
        }
        self.lock_file.write_text(json.dumps(lock_data, indent=2), encoding="utf-8")

        return True, f"Successfully audited and installed skill '{pkg.name}' (version {pkg.version})."

    def list_installed(self) -> list[dict[str, Any]]:
        lock_data = self._read_lock()
        return [{"name": k, **v} for k, v in lock_data.items()]

    def _read_lock(self) -> dict[str, Any]:
        if self.lock_file.exists():
            try:
                return json.loads(self.lock_file.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}


_global_hub: SkillsHub | None = None


def get_skills_hub(root_dir: Path | str | None = None) -> SkillsHub:
    global _global_hub
    if _global_hub is None or root_dir is not None:
        _global_hub = SkillsHub(root_dir)
    return _global_hub
