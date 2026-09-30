"""Unified agent workspace: one facade over every Alpha capability module.

Alpha ships ~100 capability packages, and this session added several
user-facing ones (modes, experts, automations, connectors, egress, routines,
skills market). This facade composes them into a single operable object so a
host (Gateway, CLI, desktop) can configure and drive the whole workspace through
one entry point instead of wiring each store by hand.

It is a *composition* layer: it owns no behaviour of its own beyond routing to
the modules and enforcing the active :class:`~alpha.modes.WorkMode`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from alpha.automations import Automation, AutomationSchedule, AutomationStore
from alpha.connectors import ConnectorCatalog, ConnectorStore
from alpha.egress import EgressStore
from alpha.experts import ExpertCatalog, ExpertRegistry
from alpha.modes import WorkMode, assert_allowed, capabilities_for, is_allowed, parse_mode
from alpha.routines import Routine, RoutineStore
from alpha.scorecard import ScorecardReport, scan
from alpha.skills_market import SkillMarketCatalog, SkillMarketRegistry

#: The alpha package root, used for the capability scorecard.
_ALPHA_ROOT = Path(__file__).resolve().parents[1]


class AgentWorkspace:
    """A single, configured workspace composing Alpha's capability modules."""

    def __init__(self, root_dir: str | Path, *, mode: WorkMode | str = WorkMode.CRAFT, user_id: str = "default") -> None:
        self.root = Path(root_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.user_id = user_id
        self._mode = parse_mode(mode)

        # Stateless catalogs (declarations).
        self.connectors = ConnectorCatalog()
        self.experts = ExpertCatalog()
        self.skills_market = SkillMarketCatalog()

        # Stateful stores, all under the workspace root.
        self.connector_store = ConnectorStore(self.root / "connectors.json", catalog=self.connectors)
        self.expert_registry = ExpertRegistry(self.root / "experts.json", catalog=self.experts)
        self.skill_registry = SkillMarketRegistry(self.root / "skills.json", catalog=self.skills_market)
        self.automations = AutomationStore(self.root / "automations.json")
        self.egress = EgressStore(self.root / "egress.json")
        self.routines = RoutineStore(self.root / "routines.json")

    # -- mode -------------------------------------------------------------
    @property
    def mode(self) -> WorkMode:
        return self._mode

    def set_mode(self, mode: WorkMode | str) -> WorkMode:
        self._mode = parse_mode(mode)
        return self._mode

    def can(self, action: str) -> bool:
        return is_allowed(self._mode, action)

    def assert_action(self, action: str) -> None:
        """Raise ModeViolation if *action* is not permitted in the active mode."""
        assert_allowed(self._mode, action)

    def mode_description(self) -> str:
        return capabilities_for(self._mode).description

    # -- convenience installers ------------------------------------------
    def install_connector(self, connector_id: str, *, enable: bool = True):
        return self.connector_store.install(connector_id, enable=enable)

    def install_expert(self, expert_id: str, *, enable: bool = True):
        return self.expert_registry.install_expert(expert_id, enable=enable)

    def install_expert_group(self, group_id: str, *, enable: bool = True):
        return self.expert_registry.install_group(group_id, enable=enable)

    def install_skill(self, skill_id: str, *, enable: bool = True):
        return self.skill_registry.install(skill_id, enable=enable)

    def add_automation(self, automation: Automation) -> Automation:
        return self.automations.add(automation)

    def save_routine(self, routine: Routine) -> Routine:
        return self.routines.save(routine)

    # -- introspection ----------------------------------------------------
    def status(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "mode": self._mode.name,
            "connectors_enabled": len(self.connector_store.enabled_ids()),
            "experts_enabled": len(self.expert_registry.enabled_ids()),
            "skills_enabled": len(self.skill_registry.enabled_ids()),
            "automations": len(self.automations.list()),
            "automations_enabled": len(self.automations.enabled()),
            "routines": len(self.routines.list()),
            "egress_profiles": len(self.egress.list_profiles()),
        }

    def snapshot(self) -> dict[str, Any]:
        """Full serialisable state of the workspace."""
        return {
            "status": self.status(),
            "mode_capabilities": capabilities_for(self._mode).__dict__,
            "connectors": [s.to_dict() for s in self.connector_store.list()],
            "experts": [s.to_dict() for s in self.expert_registry.list()],
            "skills": [s.to_dict() for s in self.skill_registry.list()],
            "automations": [a.to_dict() for a in self.automations.list()],
            "routines": [r.to_dict() for r in self.routines.list()],
            "egress": {"policy": self.egress.policy().to_dict(),
                       "profiles": [p.to_dict() for p in self.egress.list_profiles()]},
        }

    def capability_report(self, alpha_root: str | Path | None = None) -> ScorecardReport:
        """Score the installed framework against the frontier taxonomy."""
        return scan(alpha_root or _ALPHA_ROOT)


__all__ = ["AgentWorkspace"]
