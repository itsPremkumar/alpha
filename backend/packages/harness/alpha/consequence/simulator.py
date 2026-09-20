"""ConsequenceSimulator: Simulates side-effects and evaluates blast radius prior to execution."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from alpha.consequence.affordance import AffordanceModel

logger = logging.getLogger(__name__)


@dataclass
class SimulationReport:
    action_description: str
    safe_to_proceed: bool
    blast_radius: str  # "NONE", "LOCAL_FILE", "PACKAGE_DEPENDENCY", "SYSTEM_WIDE"
    projected_state_changes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    remediation_suggestions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ConsequenceSimulator:
    """Predicts operational side-effects, blast radius, and potential cascading failures."""

    def __init__(self, affordance_model: AffordanceModel | None = None):
        self.affordance_model = affordance_model or AffordanceModel()

    def simulate(
        self,
        action_name: str,
        parameters: dict[str, Any],
        workspace_path: str | None = None,
    ) -> SimulationReport:
        affordances = self.affordance_model.probe(workspace_path)
        warnings: list[str] = []
        projected_changes: list[str] = []
        remediations: list[str] = []
        blast_radius = "LOCAL_FILE"
        safe = True

        action_low = action_name.lower()
        target_file = str(parameters.get("TargetFile") or parameters.get("path") or parameters.get("file_path") or "")
        command_str = str(parameters.get("command") or parameters.get("CommandLine") or "")

        # 1. Non-git workspace warning
        if not affordances.is_git_repo and any(a in action_low for a in ["write", "edit", "replace", "delete"]):
            warnings.append("Workspace is not git-tracked; file mutations cannot be rolled back automatically via git checkout.")
            remediations.append("Initialize git tracking or create manual backup snapshots.")

        # 2. Package manifest modifications
        if any(target_file.endswith(m) for m in ["pyproject.toml", "package.json", "Cargo.toml", "go.mod"]):
            blast_radius = "PACKAGE_DEPENDENCY"
            projected_changes.append(f"Modification to project manifest '{target_file}' will trigger lockfile recalculation.")
            remediations.append("Run dependency installer to regenerate lockfiles after modification.")

        # 3. Database migration modifications
        if "migration" in target_file.lower() or "alembic" in target_file.lower():
            blast_radius = "PACKAGE_DEPENDENCY"
            warnings.append("Altering database migrations may cause schema state divergence across distributed environments.")
            remediations.append("Ensure migration handlers provide both upgrade and downgrade paths.")

        # 4. Long-running server processes in shell commands
        if any(server_cmd in command_str.lower() for server_cmd in ["uvicorn", "flask run", "npm start", "python -m http.server"]):
            warnings.append("Command initiates a foreground listening server that may block standard task execution.")
            remediations.append("Run server in daemon or background mode (IsDaemon=True or nohup).")

        # 5. Destructive shell commands
        if "rm " in command_str or "del " in command_str:
            blast_radius = "SYSTEM_WIDE"
            if not affordances.is_git_repo:
                safe = False
                warnings.append("Deletion in unversioned repository carries risk of permanent data loss.")

        if not projected_changes:
            projected_changes.append(f"Standard execution of '{action_name}'.")

        return SimulationReport(
            action_description=f"{action_name} on {target_file or command_str[:40]}",
            safe_to_proceed=safe,
            blast_radius=blast_radius,
            projected_state_changes=projected_changes,
            warnings=warnings,
            remediation_suggestions=remediations,
        )
