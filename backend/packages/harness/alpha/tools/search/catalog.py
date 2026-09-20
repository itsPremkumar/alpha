"""Universal Tool Catalog indexer with deferred on-demand schema discovery."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


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
    return _global_catalog
