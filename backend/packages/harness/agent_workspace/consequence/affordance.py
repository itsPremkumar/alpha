"""AffordanceModel: Probes and models environment execution capabilities."""

from __future__ import annotations

import os
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class EnvironmentAffordances:
    """Snapshot of capabilities afforded by the local runtime environment."""
    workspace_dir: str
    is_writable: bool
    is_git_repo: bool
    available_binaries: dict[str, bool] = field(default_factory=dict)
    python_version: str = ""
    open_subprocesses: int = 0
    environment_tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AffordanceModel:
    """Probes the host and workspace environment to determine available affordances."""

    PROBED_BINARIES = ["git", "python", "pytest", "docker", "npm", "cargo", "curl"]

    def probe(self, workspace_path: str | None = None) -> EnvironmentAffordances:
        ws = Path(workspace_path or os.getcwd()).resolve()
        is_writable = os.access(str(ws), os.W_OK)
        is_git = (ws / ".git").exists() or (ws.parent / ".git").exists()

        bins = {b: shutil.which(b) is not None for b in self.PROBED_BINARIES}

        tags = []
        if is_git:
            tags.append("version_controlled")
        if bins.get("pytest"):
            tags.append("python_test_capable")
        if bins.get("docker"):
            tags.append("container_capable")

        return EnvironmentAffordances(
            workspace_dir=str(ws),
            is_writable=is_writable,
            is_git_repo=is_git,
            available_binaries=bins,
            python_version=os.sys.version.split()[0],
            environment_tags=tags,
        )
