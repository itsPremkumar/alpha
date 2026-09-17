from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ReconReport:
    root_dir: str
    build_systems: list[str] = field(default_factory=list)
    test_frameworks: list[str] = field(default_factory=list)
    ci_cd_systems: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    total_files_scanned: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_dir": self.root_dir,
            "build_systems": self.build_systems,
            "test_frameworks": self.test_frameworks,
            "ci_cd_systems": self.ci_cd_systems,
            "languages": self.languages,
            "total_files_scanned": self.total_files_scanned,
        }


class RepoRecon:
    """Automated Repository Reconnaissance Engine."""

    @staticmethod
    def scan_workspace(root_dir: str | Path) -> ReconReport:
        root = Path(root_dir)
        report = ReconReport(root_dir=str(root))

        if not root.exists() or not root.is_dir():
            return report

        # Detect build systems
        if (root / "pyproject.toml").exists() or (root / "setup.py").exists() or (root / "requirements.txt").exists():
            report.build_systems.append("python/pip/pyproject")
        if (root / "package.json").exists():
            report.build_systems.append("node/npm")
        if (root / "Cargo.toml").exists():
            report.build_systems.append("rust/cargo")
        if (root / "Makefile").exists():
            report.build_systems.append("make")

        # Detect test frameworks
        if (root / "pytest.ini").exists() or (root / "tests").exists():
            report.test_frameworks.append("pytest")
        if (root / "jest.config.js").exists() or (root / "jest.config.ts").exists():
            report.test_frameworks.append("jest")

        # Detect CI/CD
        if (root / ".github" / "workflows").exists():
            report.ci_cd_systems.append("github-actions")
        if (root / ".gitlab-ci.yml").exists():
            report.ci_cd_systems.append("gitlab-ci")
        if (root / "Dockerfile").exists() or (root / "docker-compose.yml").exists():
            report.ci_cd_systems.append("docker")

        # Detect languages from file extensions (scan up to depth 3)
        languages = set()
        count = 0
        for p in root.glob("**/*"):
            if p.is_file():
                count += 1
                if p.suffix in (".py", ".pyi"):
                    languages.add("python")
                elif p.suffix in (".js", ".jsx", ".ts", ".tsx"):
                    languages.add("javascript/typescript")
                elif p.suffix == ".rs":
                    languages.add("rust")
                elif p.suffix in (".sh", ".bash", ".ps1"):
                    languages.add("shell")

        report.languages = sorted(list(languages))
        report.total_files_scanned = count
        return report
