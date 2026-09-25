"""One tool-discovery session: the unit of policy, counters, and isolation.

A session owns exactly one catalog snapshot, one telemetry counter set, one
execution gate, and one directory cache. Nothing in this package is a
process-global, so two runs inside one process -- two threads, two users, a
test and a live agent -- cannot see each other's catalog, counters, or
sequential-exclusivity state. That is the property the hermetic tests assert.

The session is the single place the lead's wiring patch touches: build the tool
list, resolve the policy's allowed names, build a session from the result, and
hand the three control tools to the agent.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from langchain.tools import BaseTool

from alpha.tools.discovery.call import (
    AsyncDispatcher,
    ExecutionGate,
    PolicyCheck,
    SyncDispatcher,
    ToolCaller,
)
from alpha.tools.discovery.catalog import (
    CatalogMode,
    CatalogSnapshot,
    ToolSource,
    build_snapshot,
    direct_only_tools,
    is_untrusted,
)
from alpha.tools.discovery.config import DEFAULT_CONFIG, DiscoveryConfig, load_config
from alpha.tools.discovery.describe import DescribeResult, describe
from alpha.tools.discovery.directory import DirectoryCache, DirectoryRender, render_directory
from alpha.tools.discovery.search import CatalogIndex, SearchBudgetError, SearchOutcome, ToolSearcher
from alpha.tools.discovery.telemetry import DiscoveryTelemetry

#: Sentinel telling :func:`_resolve_config` that no config override was supplied,
#: as opposed to an explicit ``None`` tri-state value ("the operator said
#: nothing, use the structured default"). Defined before the session class
#: because a default argument is evaluated when the class body runs.
_UNSET = object()


def _resolve_config(config: DiscoveryConfig | None, config_override: Any) -> tuple[DiscoveryConfig, list[str]]:
    """Resolve the effective config and the notes explaining every clamp.

    An explicit ``config`` wins outright. Otherwise a supplied
    ``config_override`` (the tri-state value, including an explicit ``None``)
    is applied without reading the runtime home; with neither, the runtime home
    is consulted.
    """
    if config is not None:
        return config, []
    if config_override is _UNSET:
        return load_config()
    return load_config(override=config_override)


@dataclass(frozen=True)
class DiscoverySession:
    """Immutable handle onto one run's discovery surface.

    Frozen on purpose: the snapshot, index, caller, and caches are derived once
    and must not be swapped mid-run. A catalog change is a NEW session, which
    is what makes the snapshot id meaningful and the counter scope honest.
    """

    config: DiscoveryConfig
    snapshot: CatalogSnapshot
    index: CatalogIndex
    searcher: ToolSearcher
    caller: ToolCaller
    telemetry: DiscoveryTelemetry
    directory_cache: DirectoryCache
    gate: ExecutionGate
    config_notes: tuple[str, ...] = ()

    # ── construction ───────────────────────────────────────────────────────

    @classmethod
    def create(
        cls,
        tools: Iterable[BaseTool],
        *,
        allowed_names: set[str] | None = None,
        config: DiscoveryConfig | None = None,
        config_override: Any = _UNSET,
        dispatch: SyncDispatcher | None = None,
        adispatch: AsyncDispatcher | None = None,
        policy_check: PolicyCheck | None = None,
        policy_note: str = "",
    ) -> DiscoverySession:
        """Build a session from the already-assembled tool list.

        ``allowed_names`` is the resolved policy set (``None`` = unrestricted).
        ``config_override`` is the tri-state value; omitted means read the
        runtime-home override. No policy filtering happens anywhere else, so
        there is exactly one place a tool can be removed.
        """
        resolved_config, notes = _resolve_config(config, config_override)
        materialized = list(tools)
        snapshot = build_snapshot(materialized, allowed_names=allowed_names, policy_note=policy_note)
        index = CatalogIndex.build(snapshot)
        telemetry = DiscoveryTelemetry.for_catalog(snapshot)
        gate = ExecutionGate()
        caller = ToolCaller(
            snapshot,
            telemetry=telemetry,
            call_timeout_ms=resolved_config.call_timeout_ms,
            dispatch=dispatch,
            adispatch=adispatch,
            policy_check=policy_check,
            gate=gate,
        )
        return cls(
            config=resolved_config,
            snapshot=snapshot,
            index=index,
            searcher=ToolSearcher(index, resolved_config),
            caller=caller,
            telemetry=telemetry,
            directory_cache=DirectoryCache(),
            gate=gate,
            config_notes=tuple(notes),
        )

    # ── operations ─────────────────────────────────────────────────────────

    def search(self, **kwargs: Any) -> SearchOutcome:
        """Run a search under the session's budgets."""
        outcome = self.searcher.run(**kwargs)
        self.telemetry.record_search(
            queries=len(outcome.results),
            candidates=outcome.candidates_returned,
            truncated=outcome.truncated,
        )
        return outcome

    def describe(self, selector: str, *, entry_id: str = "") -> DescribeResult:
        """Describe one entry and count the operation."""
        result = describe(self.snapshot, selector, entry_id=entry_id)
        self.telemetry.record_describe(entry_id=selector or entry_id, ok=result.ok)
        return result

    def call(self, selector: str, arguments: dict[str, Any] | None = None) -> Any:
        """Execute a discovered tool; returns a :class:`CallResult` payload."""
        return self.caller.call(selector, arguments)

    async def acall(self, selector: str, arguments: dict[str, Any] | None = None) -> Any:
        """Async counterpart of :meth:`call`."""
        return await self.caller.acall(selector, arguments)

    def directory(self) -> DirectoryRender:
        """Render (or reuse) the bounded capability directory."""
        return self.directory_cache.render(self.snapshot, char_budget=self.config.directory_char_budget)

    def directory_text(self) -> str:
        """The prompt-safe directory text; ``""`` when compaction is off."""
        if not self.config.compaction_enabled:
            return ""
        return self.directory().text

    def directly_visible(self, tools: Iterable[BaseTool], *, allowed_names: set[str] | None = None) -> list[BaseTool]:
        """The tools that stay model-visible alongside the control surface.

        Direct-only tools stay bound -- they are policy-required surfaces the
        model must always see. The three control tools themselves are added by
        the caller (see :func:`build_control_tools`); this returns only the
        direct-only subset of *tools*.
        """
        return direct_only_tools(tools, allowed_names=allowed_names)

    # ── reporting ──────────────────────────────────────────────────────────

    def surface_report(self) -> dict[str, Any]:
        """Measured accounting of what this session exposes. Used by tests."""
        directory = self.directory() if self.config.compaction_enabled else None
        return {
            "mode": self.config.mode.value,
            "snapshotId": self.snapshot.snapshot_id,
            "catalogSize": self.snapshot.size,
            "sources": self.snapshot.source_counts,
            "controlTools": [name for name in CONTROL_TOOL_NAMES if self.config.controls_exposed],
            "directoryChars": directory.char_count if directory else 0,
            "directoryListed": len(directory.listed_names) if directory else 0,
            "directoryUntrustedCounted": directory.untrusted_count if directory else 0,
            "excluded": list(self.snapshot.excluded),
            "telemetry": self.telemetry.to_dict(),
            "configNotes": list(self.config_notes),
            "breakEvenCatalogSize": self.config.break_even_catalog_size,
        }


#: The three control tool names. Declared once so the tool objects, the
#: directory guidance, and the surface report cannot disagree.
CONTROL_TOOL_NAMES: tuple[str, str, str] = ("tool_search", "tool_describe", "tool_call")


def build_session(tools: Sequence[BaseTool], **kwargs: Any) -> DiscoverySession:
    """Thin named constructor, so the wiring patch reads declaratively."""
    return DiscoverySession.create(tools, **kwargs)


__all__ = [
    "CONTROL_TOOL_NAMES",
    "DEFAULT_CONFIG",
    "CatalogMode",
    "CatalogSnapshot",
    "DiscoveryConfig",
    "DiscoverySession",
    "SearchBudgetError",
    "ToolSource",
    "build_session",
    "is_untrusted",
    "render_directory",
]
