"""Progressive tool disclosure for Alpha: Tool Search + Code Mode.

Design provenance
-----------------
The surface, the tri-state configuration, the budget numbers, the trust
boundary between first-party and untrusted tools, and the fail-closed rules are
**adapted from the OpenClaw Tool Search design** (``docs.openclaw.ai/tools/
tool-search``) and the ``openclaw_2_0_advanced_agent_features_implementation_spec``
sections 14 (Dynamic Tool Discovery), 15 (Code Mode), 16 (MCP lifecycle),
39 (capability-based security), 55-59 (runtime owns truth, auditable, never
silently fail, evidence before confidence) and 61 (what NOT to copy blindly).

**No third-party code was copied.** The behavioral contract was re-derived from
the published documentation and re-expressed against Alpha's real architecture:
Alpha's skill ``allowed-tools`` policy, its ``BaseTool`` registry, its
middleware chain, its MCP metadata tag, its shared tokenizer and BM25
constants, and its runtime-home convention. Where Alpha already had a
mechanism, this package calls it instead of building a second one.

Honesty caveat that shapes the defaults
---------------------------------------
Compaction is not always cheaper. A small catalog can net-lose: two extra
discovery turns cost more than the few schemas they save. The feature is
therefore opt-in per deployment (``DiscoveryMode.DIRECT`` restores today's
behavior exactly) and the break-even catalog size is exposed as configuration
(``break_even_catalog_size``), not folklore. The payload regression test
measures both sides for a small and a large synthetic catalog and asserts the
crossover rather than assuming it.

Module map
----------
``config``     tri-state config + clamped limits + runtime-home override
``catalog``    effective, policy-filtered catalog snapshot
``search``     Okapi BM25 ranking, batching, and response budgets
``describe``   full metadata for one entry (trust-aware)
``call``       execution through the existing runtime path, with all guards
``directory``  bounded, cache-stable capability directory for the prompt prefix
``telemetry``  catalog size, source split, per-session counters
``session``    one run's snapshot + index + caller + gate + caches
``tools``      the three structured control tool objects
``code_mode``  the isolated bridge (optional; see its own docstring)
"""

from __future__ import annotations

from typing import Any

#: PEP 562 lazy exports. Importing this package must stay cheap and must not
#: drag in the shared config layer (a known circular-import hazard in this
#: repo), so nothing below is imported until an attribute is actually read.
_LAZY_EXPORTS: dict[str, str] = {
    "CatalogEntry": "alpha.tools.discovery.catalog",
    "CatalogIndex": "alpha.tools.discovery.search",
    "CatalogMode": "alpha.tools.discovery.catalog",
    "CatalogSnapshot": "alpha.tools.discovery.catalog",
    "CallOutcome": "alpha.tools.discovery.call",
    "CallResult": "alpha.tools.discovery.call",
    "CONTROL_TOOL_NAMES": "alpha.tools.discovery.session",
    "ControlTools": "alpha.tools.discovery.tools",
    "DEFAULT_CONFIG": "alpha.tools.discovery.session",
    "DescribeResult": "alpha.tools.discovery.describe",
    "DirectoryCache": "alpha.tools.discovery.directory",
    "DirectoryRender": "alpha.tools.discovery.directory",
    "DiscoveryConfig": "alpha.tools.discovery.config",
    "DiscoveryMode": "alpha.tools.discovery.config",
    "DiscoverySession": "alpha.tools.discovery.session",
    "DiscoveryTelemetry": "alpha.tools.discovery.telemetry",
    "ExecutionGate": "alpha.tools.discovery.call",
    "SearchBudgetError": "alpha.tools.discovery.search",
    "ToolCaller": "alpha.tools.discovery.call",
    "ToolSearcher": "alpha.tools.discovery.search",
    "ToolSource": "alpha.tools.discovery.catalog",
    "build_control_tools": "alpha.tools.discovery.tools",
    "build_session": "alpha.tools.discovery.session",
    "build_snapshot": "alpha.tools.discovery.catalog",
    "is_untrusted": "alpha.tools.discovery.catalog",
    "load_config": "alpha.tools.discovery.config",
    "render_directory": "alpha.tools.discovery.directory",
}

__all__ = sorted(_LAZY_EXPORTS)


def __getattr__(name: str) -> Any:
    """Resolve a public symbol on first access (PEP 562)."""
    module_path = _LAZY_EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = getattr(import_module(module_path), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return list(__all__)
