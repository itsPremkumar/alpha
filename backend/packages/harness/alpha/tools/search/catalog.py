"""Universal Tool Catalog indexer with deferred on-demand schema discovery."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: How many tools go into one System One ranking request.
RANK_POOL = 60


@dataclass
class ToolCatalogEntry:
    name: str
    description: str = ""
    category: str = "general"
    parameters_schema: dict[str, Any] = field(default_factory=dict)
    handler: Callable | None = None


class UniversalToolCatalog:
    """Central registry of agent tools supporting compact search and lazy schema discovery."""

    def __init__(self):
        self._entries: dict[str, ToolCatalogEntry] = {}

    def register_entry(self, entry: ToolCatalogEntry) -> None:
        self._entries[entry.name] = entry

    def register_tool(
        self,
        name: str,
        handler: Callable,
        description: str = "",
        category: str = "general",
        parameters_schema: dict[str, Any] | None = None,
    ) -> None:
        schema = parameters_schema or {}
        if not schema and hasattr(handler, "__doc__") and handler.__doc__:
            # Extract basic docstring if schema wasn't explicitly supplied
            description = description or handler.__doc__.strip().split("\n")[0]

        entry = ToolCatalogEntry(
            name=name,
            description=description,
            category=category,
            parameters_schema=schema,
            handler=handler,
        )
        self.register_entry(entry)

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Compact semantic/keyword search returning only high-level summary cards."""
        q = query.strip().lower()
        if not q:
            # Return top entries up to limit
            return [
                {"name": e.name, "category": e.category, "description": e.description}
                for e in list(self._entries.values())[:limit]
            ]

        scored: list[tuple[int, ToolCatalogEntry]] = []
        pattern = re.compile(re.escape(q), re.IGNORECASE)

        for entry in self._entries.values():
            score = 0
            if pattern.search(entry.name):
                score += 5
            if pattern.search(entry.category):
                score += 3
            if pattern.search(entry.description):
                score += 2

            if score > 0:
                scored.append((score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {"name": e.name, "category": e.category, "description": e.description}
            for _, e in scored[:limit]
        ]

    async def asearch_smart(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Re-rank with System One, merged behind the keyword hits.

        The keyword search stays authoritative for literal matches; System One
        only adds candidates the keyword scorer missed. Falls back silently to
        :meth:`search` whenever System One is off, unreachable, or unsure.
        """
        base = self.search(query, limit=limit)
        ranked = await self._rank(query, limit)
        if not ranked:
            return base
        seen = {r["name"] for r in base}
        extra = [self._card(name) for name in ranked if name not in seen]
        return [*base, *(e for e in extra if e)][:limit]

    def search_smart(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Sync wrapper; uses the plain search when inside a running loop."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.asearch_smart(query, limit))
        logger.debug("search_smart inside a running loop; using keyword search.")
        return self.search(query, limit=limit)

    async def _rank(self, query: str, limit: int) -> list[str]:
        """Ordered tool names from System One, or [] when there is no signal."""
        if not query.strip() or len(self._entries) < 2:
            return []
        try:
            from alpha.tools.selection import Candidate, rank_candidates
        except Exception:
            return []
        entries = list(self._entries.values())[:RANK_POOL]
        candidates = [
            Candidate(
                id=e.name,
                title=e.name,
                summary=f"{e.category}: {e.description}"[:280],
                full=f"{e.category}: {e.description}"[:1200],
            )
            for e in entries
        ]
        try:
            ranking = await rank_candidates(query, candidates, top_n=limit, site="tool_select")
        except Exception:
            logger.debug("System One tool ranking unavailable.", exc_info=True)
            return []
        return ranking.ids if ranking else []

    def _card(self, name: str) -> dict[str, Any] | None:
        entry = self._entries.get(name)
        if entry is None:
            return None
        return {"name": entry.name, "category": entry.category, "description": entry.description}

    def describe(self, tool_name: str) -> dict[str, Any]:
        """Fetch complete parameter schema and usage specification for a single tool on demand."""
        # Exact match or case-insensitive match
        entry = self._entries.get(tool_name)
        if not entry:
            for k, v in self._entries.items():
                if k.lower() == tool_name.lower():
                    entry = v
                    break

        if not entry:
            raise KeyError(f"Tool '{tool_name}' not found in catalog. Available: {list(self._entries.keys())}")

        return {
            "name": entry.name,
            "category": entry.category,
            "description": entry.description,
            "parameters": entry.parameters_schema,
        }

    def call(self, tool_name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Invoke a catalog tool on-demand."""
        args = arguments or {}
        entry = self._entries.get(tool_name)
        if not entry:
            for k, v in self._entries.items():
                if k.lower() == tool_name.lower():
                    entry = v
                    break

        if not entry or not entry.handler:
            raise KeyError(f"Tool '{tool_name}' is not callable or has no registered handler.")

        if hasattr(entry.handler, "invoke") and callable(entry.handler.invoke):
            return entry.handler.invoke(args)
        return entry.handler(**args)


_global_catalog = UniversalToolCatalog()


def get_universal_catalog() -> UniversalToolCatalog:
    """Return the process-wide catalog.

    The catalog is populated lazily: a registry that nothing registers into is
    a dispatch surface that always raises, so the script bridge seeds itself
    here on first access and the already-registered ``catalog_tool_call`` tool
    becomes a live path into it.
    """
    if not getattr(_global_catalog, "_seeded", False):
        try:
            from alpha.tools.script_bridge.service import register_in_catalog

            register_in_catalog(_global_catalog)
        except Exception:  # noqa: BLE001 - never let optional wiring break lookup
            logger.warning("script bridge registration failed; catalog left unseeded", exc_info=True)
        else:
            _global_catalog._seeded = True  # type: ignore[attr-defined]
    return _global_catalog
