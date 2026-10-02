"""Self-knowledge: Alpha answering questions about itself from real runtime state.

The rule
--------
**Never ask a model.** Every field in every payload produced here is read from a
live subsystem. Nothing is inferred from a prompt, guessed, or filled in from a
default that would read as a fact. Where a subsystem cannot answer a question,
the payload says so explicitly (``"unavailable"``, ``None``, or a ``reason``),
because a fabricated capability claim is the single most damaging thing an agent
can get wrong about itself — it routes work to something that does not exist.

What is projected, and from where
---------------------------------
Every entry reads the subsystem that already owns the fact:

===================================== ==================================================
Question                               Read from
===================================== ==================================================
Which tools exist?                     ``alpha.tools`` registry
Which skills exist?                    ``alpha.skills``
Which MCP servers exist?               ``alpha.mcp.cache``
Which models are available?            ``alpha.models`` / ``AppConfig.models``
Which agents / experts?                ``alpha.experts`` catalog + fabric
Which experts are weak?                fabric metrics (a *measured* low score)
What am I learning?                    journal + plasticity state
What changed recently?                 journal tail
What is the current task?              ``AppConfig``/run manager where available
What is pending?                       journal + fabric state counts
===================================== ==================================================

Every projection is wrapped so a raising subsystem becomes
``{"available": False, "reason": "<real error>"}`` instead of a 500. That is not
leniency: one unavailable optional subsystem must not make the whole self-knowledge
endpoint useless, and the alternative (propagating) would mean a single
misconfigured MCP server hides Alpha's entire capability inventory.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["SelfKnowledgeService", "get_self_knowledge"]


def _safe(label: str, fn: Callable[[], Any]) -> dict[str, Any]:
    """Run ``fn`` and wrap it, converting any failure into a disclosed absence."""
    try:
        return {"available": True, "reason": "", "data": fn()}
    except Exception as exc:  # noqa: BLE001 - disclosure is the whole point
        logger.info("self-knowledge projection %r is unavailable: %s", label, exc)
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}", "data": None}


class SelfKnowledgeService:
    """Read-only projection of real runtime state.

    Holds no state of its own. Every method performs live reads, so a caller
    cannot hold a stale snapshot and call it "current".
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()

    # -- configuration ------------------------------------------------------

    def config(self) -> dict[str, Any]:
        """The live ``intelligence:`` section, as resolved."""

        def read() -> Any:
            from alpha.intelligence.config import intelligence_config

            section = intelligence_config()
            return section.model_dump(mode="json")

        return _safe("intelligence_config", read)

    def mode(self) -> dict[str, Any]:
        """The effective learning mode and what it currently permits."""

        def read() -> Any:
            from alpha.intelligence.config import intelligence_config
            from alpha.intelligence.models import LearningMode

            section = intelligence_config()
            return {
                "enabled": section.enabled,
                "mode": section.mode.value,
                "permits": {mode.value: section.allows(mode) for mode in LearningMode},
            }

        return _safe("intelligence_mode", read)

    # -- capabilities -------------------------------------------------------

    def tools(self) -> dict[str, Any]:
        """Model-visible tool names, from the real tool assembly.

        Read through :func:`alpha.tools.get_available_tools`, the same function
        that assembles a run's toolset — not a re-derivation of the registry. The
        arguments mirror the ``standard`` agent preset so the reported set is the
        one an ordinary run actually sees; the preset's own narrowing flags
        (``include_mcp``) are left at their defaults, and MCP is reported
        separately by :meth:`mcp_servers` rather than folded in here.
        """

        def read() -> Any:
            from alpha.tools import get_available_tools

            specs = get_available_tools(groups=None, include_mcp=False, model_name=None, subagent_enabled=False)
            names = sorted({str(getattr(spec, "name", spec)) for spec in specs})
            return {"count": len(names), "names": names, "source": "alpha.tools.get_available_tools"}

        return _safe("tools", read)

    def skills(self) -> dict[str, Any]:
        """Enabled skills, from the skills storage layer.

        Reads :meth:`alpha.skills.storage.LocalSkillStorage.load_skills` with
        ``enabled_only=True`` — the same call
        ``alpha.skills.prompt`` makes when building the prompt block, so this
        reports what an ordinary run actually sees rather than every skill that
        happens to be installed. ``SkillCatalog`` is *not* used directly: it is a
        search index that must be constructed from a skill list and has no
        enumeration role of its own.
        """

        def read() -> Any:
            from alpha.skills import get_or_new_skill_storage

            storage = get_or_new_skill_storage()
            names = sorted(str(getattr(skill, "name", skill)) for skill in storage.load_skills(enabled_only=True))
            return {
                "count": len(names),
                "names": names,
                "source": "alpha.skills.get_or_new_skill_storage().load_skills(enabled_only=True)",
            }

        return _safe("skills", read)

    def mcp_servers(self) -> dict[str, Any]:
        """Configured MCP servers, names only. Never credentials or tool schemas."""

        def read() -> Any:
            from alpha.mcp.cache import get_cached_mcp_tools

            tools = get_cached_mcp_tools()
            servers = sorted({str(getattr(spec, "server", None) or "unknown") for spec in tools or []})
            return {
                "count": len(servers),
                "names": servers,
                "tool_count": len(tools or []),
                "note": "server names only; no credentials or schemas are exposed",
            }

        return _safe("mcp_servers", read)

    def models(self) -> dict[str, Any]:
        """Configured models, read from ``AppConfig``. Never from a live provider."""

        def read() -> Any:
            from alpha.config.app_config import get_app_config

            app_config = get_app_config()
            names = [model.name for model in app_config.models]
            return {
                "count": len(names),
                "names": sorted(names),
                "default_model": app_config.default_model_name,
                "source": "config.yaml (declared, not probed)",
            }

        return _safe("models", read)

    # -- experts ------------------------------------------------------------

    def experts(self) -> dict[str, Any]:
        """Every expert: declared catalog entries and learned fabric records."""

        def read() -> Any:
            from alpha.experts.catalog import ExpertCatalog
            from alpha.intelligence.expert_fabric import get_expert_fabric

            catalog = ExpertCatalog()
            declared = [{"expert_id": expert.id, "name": expert.name, "category": expert.category, "source": "catalog", "status": "DECLARED", "generation": 0} for expert in catalog.list_experts()]
            fabric = get_expert_fabric()
            learned = [
                {
                    "expert_id": record.identity.expert_id,
                    "name": record.identity.capability,
                    "category": record.identity.kind,
                    "source": "learned",
                    "status": record.identity.status.value,
                    "generation": record.identity.generation,
                    "protected": record.identity.protected,
                    "success_rate": record.metrics.success_rate,
                    "usage": record.metrics.usage,
                }
                for record in fabric.list(include_terminal=True)
            ]
            return {
                "declared_count": len(declared),
                "learned_count": len(learned),
                "status_counts": fabric.status_counts(),
                "experts": declared + learned,
            }

        return _safe("experts", read)

    def weak_experts(self, *, max_success_rate: float = 0.34, min_usage: int = 1) -> dict[str, Any]:
        """Learned experts whose **measured** success rate is below the bar.

        An expert with no observations is reported separately as
        ``unmeasured``, never folded into ``weak``: "we have never seen this
        work" is not the same claim as "this keeps failing", and merging them
        would prune novelty.
        """

        def read() -> Any:
            from alpha.intelligence.expert_fabric import get_expert_fabric

            fabric = get_expert_fabric()
            weak: list[dict[str, Any]] = []
            unmeasured: list[dict[str, Any]] = []
            for record in fabric.list(include_terminal=False):
                rate = record.metrics.success_rate
                if rate is None:
                    unmeasured.append({"expert_id": record.identity.expert_id, "status": record.identity.status.value})
                    continue
                if record.metrics.usage >= min_usage and rate < max_success_rate:
                    weak.append(
                        {
                            "expert_id": record.identity.expert_id,
                            "status": record.identity.status.value,
                            "success_rate": rate,
                            "usage": record.metrics.usage,
                            "protected": record.identity.protected,
                        }
                    )
            return {
                "weak": sorted(weak, key=lambda item: item["success_rate"]),
                "unmeasured": sorted(unmeasured, key=lambda item: item["expert_id"]),
                "threshold": max_success_rate,
                "note": "unmeasured experts are listed separately from weak ones on purpose",
            }

        return _safe("weak_experts", read)

    # -- learning -----------------------------------------------------------

    def replay(self) -> dict[str, Any]:
        """Reservoir occupancy, strata, and the age of the oldest retained item."""

        def read() -> Any:
            from alpha.intelligence.config import intelligence_config
            from alpha.intelligence.replay import ReplayReservoir

            section = intelligence_config().replay
            reservoir = ReplayReservoir(
                capacity=section.capacity,
                stratum_weights=section.strata,
                half_life_seconds=section.recency_half_life_seconds,
            )
            return reservoir.stats()

        return _safe("replay", read)

    def regression(self) -> dict[str, Any]:
        """Standing-suite coverage. Read-only; runs no cases."""

        def read() -> Any:
            from alpha.intelligence.regression import coverage_report

            return coverage_report()

        return _safe("regression", read)

    def plasticity(self) -> dict[str, Any]:
        """Tier multipliers and the controller's current settings."""

        def read() -> Any:
            from alpha.intelligence.plasticity import PlasticityController

            return PlasticityController.from_config().status()

        return _safe("plasticity", read)

    def journal(self, *, limit: int = 20) -> dict[str, Any]:
        """The learning journal tail plus its chain integrity."""

        def read() -> Any:
            from alpha.intelligence.journal import LearningJournal

            journal = LearningJournal()
            entries, corrupt = journal.tail(limit=limit)
            return {
                "entries": [entry.event.to_dict() for entry in entries],
                "corrupt_lines": corrupt,
                "integrity": journal.verify_chain().to_dict(),
            }

        return _safe("journal", read)

    def learning_now(self) -> dict[str, Any]:
        """What the system is learning right now: recent learning events."""

        def read() -> Any:
            from alpha.intelligence.journal import LearningJournal

            entries, _corrupt = LearningJournal().recent(kinds=("expert_grown", "expert_promoted", "expert_rejected", "expert_pruned", "plasticity_shift", "rollback"), limit=10)
            return {
                "in_flight": [],
                "recent_events": [entry.event.to_dict() for entry in entries],
                "note": "no background learning worker is registered by this package; learning is invoked explicitly",
            }

        return _safe("learning_now", read)

    def snapshots(self, *, limit: int = 10) -> dict[str, Any]:
        """Stored intelligence snapshots, newest first."""

        def read() -> Any:
            from alpha.intelligence.snapshots import SnapshotManager

            manager = SnapshotManager()
            return {
                "count": len(manager.list()),
                "retain": manager.retain,
                "latest": [info.to_dict() for info in manager.list()[:limit]],
            }

        return _safe("snapshots", read)

    # -- paging -------------------------------------------------------------

    def paging(self) -> dict[str, Any]:
        """Paging configuration. Residency is reported only when a manager exists."""

        def read() -> Any:
            from alpha.intelligence.config import intelligence_config

            section = intelligence_config().paging
            return {
                "enabled": section.enabled,
                "policy": section.policy,
                "ram_cache_size": section.ram_cache_size,
                "max_resident": section.max_resident,
                "resident_now": None,
                "note": "no PagingManager is active; residency is measured per request by the router",
            }

        return _safe("paging", read)

    # -- aggregate ----------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """The full self-knowledge snapshot: every projection, with disclosure.

        The top-level ``mode`` and ``config`` blocks come first because a caller
        that wants "is learning on?" should not have to walk the whole payload.
        """
        with self._lock:
            return {
                "mode": self.mode(),
                "config": self.config(),
                "capabilities": {
                    "tools": self.tools(),
                    "skills": self.skills(),
                    "mcp_servers": self.mcp_servers(),
                    "models": self.models(),
                },
                "experts": self.experts(),
                "weak_experts": self.weak_experts(),
                "learning": {
                    "now": self.learning_now(),
                    "replay": self.replay(),
                    "plasticity": self.plasticity(),
                    "regression": self.regression(),
                },
                "journal": self.journal(limit=20),
                "snapshots": self.snapshots(),
                "paging": self.paging(),
            }


_SERVICE_LOCK = threading.Lock()
_SERVICE: SelfKnowledgeService | None = None


def get_self_knowledge() -> SelfKnowledgeService:
    """Process-wide service. Stateless, so a singleton is safe to share."""
    global _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is None:
            _SERVICE = SelfKnowledgeService()
        return _SERVICE
