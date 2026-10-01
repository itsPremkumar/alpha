"""Tri-state configuration for the tool discovery layer.

Design provenance
-----------------
The tri-state shape (``unset`` -> structured default, ``true`` -> code mode,
``false`` -> direct schemas, object -> pinned mode + clamped limits) follows the
OpenClaw Tool Search design, **adapted** to Alpha. No third-party code was
copied: the semantics were re-derived from the published behavioral contract
and re-expressed against Alpha's own limits and Alpha's own runtime-home
convention.

Why this module does NOT import ``alpha.config``
-----------------------------------------------
``alpha/config/__init__.py`` eagerly imports ``app_config`` and
``memory_config``. Tool discovery sits under ``alpha/tools/`` and is imported
from agent assembly paths that are themselves reachable from the config layer,
so importing the shared config package from here would close a cycle
(``alpha.tools.discovery.config`` -> ``alpha.config`` -> ``app_config`` ->
``alpha.tools``). The cycle is a known hazard in this repo, so this module
resolves the runtime home itself from ``AGENT_WORKSPACE_HOME``.

The mirror is small (two lines) and is *pinned* by
``backend/tests/test_tool_discovery.py::test_runtime_home_mirror_matches_shared_resolver``,
which imports the real ``alpha.config.runtime_paths.runtime_home`` and asserts
the two agree. The mirror therefore cannot silently drift.

Tri-state resolution
--------------------
=====================  ==========================================================
config value           effective mode
=====================  ==========================================================
absent / ``null``     ``tools`` (structured default)
``true``              ``code``
``false``             ``direct`` (today's behavior: every schema bound directly)
``{...}``             object: ``mode`` when present, else ``code``
=====================  ==========================================================

The "object without a mode still uses ``code``" default is the published
behavioral reference, not an invention here; it is pinned by a test.

Every knob is clamped rather than rejected, so a typo degrades to a documented
bound instead of failing a run:

* ``code_timeout_ms``  -> 1000..60000 (default 10000)
* ``max_search_limit`` -> 1..50 (default 20)
* ``search_default_limit`` -> 1..``max_search_limit`` (default 8)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

#: Filename of the optional runtime-home override document.
CONFIG_FILENAME = "tool_discovery.json"

# ── documented clamps ──────────────────────────────────────────────────────
CODE_TIMEOUT_MS_MIN = 1_000
CODE_TIMEOUT_MS_MAX = 60_000
MAX_SEARCH_LIMIT_MIN = 1
MAX_SEARCH_LIMIT_MAX = 50
SEARCH_DEFAULT_LIMIT_MIN = 1

#: Per-call execution budget for a bridged target tool. Distinct from
#: ``code_timeout_ms``: the code-mode deadline covers the whole bridge
#: invocation, this one bounds one target tool inside structured mode.
CALL_TIMEOUT_MS_MIN = 1_000
CALL_TIMEOUT_MS_MAX = 600_000
DEFAULT_CALL_TIMEOUT_MS = 30_000

#: Response-budget limits for ``tool_search`` batches.
DEFAULT_MAX_BATCH_QUERIES = 16
MAX_BATCH_QUERIES_HARD = 16
DEFAULT_MAX_BATCH_CANDIDATES = 50
MAX_BATCH_CANDIDATES_HARD = 50
DEFAULT_MAX_QUERY_CHARS = 512
MAX_QUERY_CHARS_HARD = 512
DEFAULT_MAX_BATCH_QUERY_BYTES = 512
MAX_BATCH_QUERY_BYTES_HARD = 512
DEFAULT_RESPONSE_CHAR_BUDGET = 4_000

#: Hard bound on the rendered capability directory.
DEFAULT_DIRECTORY_CHAR_BUDGET = 18_000

#: Catalog size at which compaction is assumed to break even. Below this the
#: three control tools plus the directory can cost MORE than direct exposure,
#: because a small catalog's schemas are cheaper than two extra discovery
#: turns. This is configuration, not folklore: the payload regression test
#: measures both sides and asserts the crossover is not assumed.
DEFAULT_BREAK_EVEN_CATALOG_SIZE = 40


class DiscoveryMode(StrEnum):
    """Model-facing surface selected for a run."""

    #: Direct schemas: today's behavior, every cataloged tool stays bound.
    DIRECT = "direct"
    #: ``tool_search`` / ``tool_describe`` / ``tool_call`` + directory.
    TOOLS = "tools"
    #: The same three controls, with a directory-shaped binding.
    DIRECTORY = "directory"
    #: Isolated JS/Python bridge exposing only search/describe/call.
    CODE = "code"


#: Mode used when the config value is absent.
DEFAULT_MODE = DiscoveryMode.TOOLS

VALID_MODES = frozenset(mode.value for mode in DiscoveryMode)


def _clamp(value: Any, low: int, high: int, fallback: int) -> int:
    """Coerce *value* into ``[low, high]``; anything unusable becomes *fallback*."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


def runtime_home() -> Path:
    """Resolve the writable Alpha state directory.

    Mirrors ``alpha.config.runtime_paths.runtime_home`` without importing
    ``alpha.config`` (see the module docstring). A test pins the agreement.
    """
    if env_home := os.getenv("AGENT_WORKSPACE_HOME"):
        return Path(env_home).resolve()
    return Path.cwd().resolve() / ".agent-workspace"


def config_path() -> Path:
    """Absolute path of the optional runtime-home override document."""
    return runtime_home() / CONFIG_FILENAME


@dataclass(frozen=True)
class DiscoveryConfig:
    """Resolved, clamped configuration for one tool-discovery session."""

    mode: DiscoveryMode = DEFAULT_MODE
    code_timeout_ms: int = 10_000
    max_search_limit: int = 20
    search_default_limit: int = 8
    call_timeout_ms: int = DEFAULT_CALL_TIMEOUT_MS
    max_batch_queries: int = DEFAULT_MAX_BATCH_QUERIES
    max_batch_candidates: int = DEFAULT_MAX_BATCH_CANDIDATES
    max_query_chars: int = DEFAULT_MAX_QUERY_CHARS
    max_batch_query_bytes: int = DEFAULT_MAX_BATCH_QUERY_BYTES
    response_char_budget: int = DEFAULT_RESPONSE_CHAR_BUDGET
    directory_char_budget: int = DEFAULT_DIRECTORY_CHAR_BUDGET
    break_even_catalog_size: int = DEFAULT_BREAK_EVEN_CATALOG_SIZE

    @property
    def compaction_enabled(self) -> bool:
        """Whether the run hides catalog schemas behind the control tools."""
        return self.mode is not DiscoveryMode.DIRECT

    @property
    def controls_exposed(self) -> bool:
        """Whether the three control tools belong in the bound tool list."""
        return self.mode is not DiscoveryMode.DIRECT

    def effective_search_limit(self, requested: int | None) -> int:
        """Clamp a caller-supplied per-query limit into the configured bounds."""
        if requested is None:
            return min(self.search_default_limit, self.max_search_limit)
        return max(1, min(int(requested), self.max_search_limit))

    def to_dict(self) -> dict[str, Any]:
        """Serializable projection; used by telemetry and by the wiring patch."""
        payload = {
            "mode": self.mode.value,
            "code_timeout_ms": self.code_timeout_ms,
            "max_search_limit": self.max_search_limit,
            "search_default_limit": self.search_default_limit,
            "call_timeout_ms": self.call_timeout_ms,
            "max_batch_queries": self.max_batch_queries,
            "max_batch_candidates": self.max_batch_candidates,
            "max_query_chars": self.max_query_chars,
            "max_batch_query_bytes": self.max_batch_query_bytes,
            "response_char_budget": self.response_char_budget,
            "directory_char_budget": self.directory_char_budget,
            "break_even_catalog_size": self.break_even_catalog_size,
        }
        return payload


#: Accepted spellings for every knob: the published camelCase contract plus a
#: snake_case alias so an Alpha ``config.yaml`` author can use either.
_KEY_ALIASES: dict[str, tuple[str, ...]] = {
    "mode": ("mode",),
    "code_timeout_ms": ("codeTimeoutMs", "code_timeout_ms"),
    "max_search_limit": ("maxSearchLimit", "max_search_limit"),
    "search_default_limit": ("searchDefaultLimit", "search_default_limit"),
    "call_timeout_ms": ("callTimeoutMs", "call_timeout_ms"),
    "max_batch_queries": ("maxBatchQueries", "max_batch_queries"),
    "max_batch_candidates": ("maxBatchCandidates", "max_batch_candidates"),
    "max_query_chars": ("maxQueryChars", "max_query_chars"),
    "max_batch_query_bytes": ("maxBatchQueryBytes", "max_batch_query_bytes"),
    "response_char_budget": ("responseCharBudget", "response_char_budget"),
    "directory_char_budget": ("directoryCharBudget", "directory_char_budget"),
    "break_even_catalog_size": ("breakEvenCatalogSize", "break_even_catalog_size"),
}

DEFAULT_CONFIG = DiscoveryConfig()

#: Sentinel distinguishing "no override given, read the runtime home" from an
#: explicit ``None`` ("operator said nothing, use the structured default").
_UNSET = object()


def _read_knob(document: dict[str, Any], field: str) -> Any:
    """First present alias for *field*, or ``None``."""
    for alias in _KEY_ALIASES[field]:
        if alias in document:
            return document[alias]
    return None


def resolve_mode(raw: Any) -> DiscoveryMode:
    """Resolve the tri-state config value into a :class:`DiscoveryMode`.

    ``raw`` is the value of the ``toolSearch``-equivalent setting: absent is
    signalled by ``None``. Unknown mode strings degrade to
    :data:`DEFAULT_MODE` and are reported by :func:`load_config` rather than
    raised, because a bad config must not take a run down.
    """
    if raw is None:
        return DEFAULT_MODE
    if raw is True:
        return DiscoveryMode.CODE
    if raw is False:
        return DiscoveryMode.DIRECT
    if isinstance(raw, str):
        text = raw.strip().lower()
        return DiscoveryMode(text) if text in VALID_MODES else DEFAULT_MODE
    if isinstance(raw, dict):
        declared = _read_knob(raw, "mode")
        if declared is None:
            # Object without a mode: the published contract selects `code`.
            return DiscoveryMode.CODE
        text = str(declared).strip().lower()
        # An unknown mode is a typo, not a request for code mode. It degrades to
        # the documented default and is disclosed by ``apply_document`` -- a
        # note that said one thing while the mode did another would be a lie.
        return DiscoveryMode(text) if text in VALID_MODES else DEFAULT_MODE
    return DEFAULT_MODE


def apply_document(raw: Any, base: DiscoveryConfig = DEFAULT_CONFIG) -> tuple[DiscoveryConfig, list[str]]:
    """Apply *raw* to *base*, returning the clamped config plus disclosed notes.

    Notes are collected rather than raised: a misconfigured deployment must
    still start, and every clamp is reported so the operator can see it.
    """
    notes: list[str] = []
    config = replace(base, mode=resolve_mode(raw))
    if not isinstance(raw, dict):
        return config, notes

    declared_mode = _read_knob(raw, "mode")
    if isinstance(declared_mode, str) and declared_mode.strip().lower() not in VALID_MODES:
        notes.append(f"unknown mode {declared_mode!r}; falling back to {DEFAULT_MODE.value}")

    def clamped(field: str, low: int, high: int, current: int, *, maximum: int | None = None) -> int:
        value = _read_knob(raw, field)
        if value is None:
            return current
        ceiling = high if maximum is None else min(high, maximum)
        resolved = _clamp(value, low, ceiling, current)
        if resolved != _clamp(value, low, high, high):
            notes.append(f"{field} clamped to {resolved} (allowed {low}..{ceiling})")
        return resolved

    max_search = clamped("max_search_limit", MAX_SEARCH_LIMIT_MIN, MAX_SEARCH_LIMIT_MAX, config.max_search_limit)
    default_search = clamped("search_default_limit", SEARCH_DEFAULT_LIMIT_MIN, max_search, config.search_default_limit, maximum=max_search)
    config = replace(
        config,
        max_search_limit=max_search,
        search_default_limit=default_search,
        code_timeout_ms=clamped("code_timeout_ms", CODE_TIMEOUT_MS_MIN, CODE_TIMEOUT_MS_MAX, config.code_timeout_ms),
        call_timeout_ms=clamped("call_timeout_ms", CALL_TIMEOUT_MS_MIN, CALL_TIMEOUT_MS_MAX, config.call_timeout_ms),
        max_batch_queries=clamped("max_batch_queries", 1, MAX_BATCH_QUERIES_HARD, config.max_batch_queries),
        max_batch_candidates=clamped("max_batch_candidates", 1, MAX_BATCH_CANDIDATES_HARD, config.max_batch_candidates),
        max_query_chars=clamped("max_query_chars", 1, MAX_QUERY_CHARS_HARD, config.max_query_chars),
        max_batch_query_bytes=clamped("max_batch_query_bytes", 1, MAX_BATCH_QUERY_BYTES_HARD, config.max_batch_query_bytes),
        response_char_budget=clamped("response_char_budget", 200, 200_000, config.response_char_budget),
        directory_char_budget=clamped("directory_char_budget", 200, 200_000, config.directory_char_budget),
        break_even_catalog_size=clamped("break_even_catalog_size", 0, 100_000, config.break_even_catalog_size),
    )
    return config, notes


def load_config(*, override: Any = _UNSET) -> tuple[DiscoveryConfig, list[str]]:
    """Load the effective config and the notes explaining every clamp.

    ``override`` is the tri-state value. Omitting it reads the runtime-home
    override document; passing an explicit value (or ``None`` to mean
    "operator said nothing") bypasses the file.

    ``AGENT_TOOL_DISCOVERY_CONFIG`` is an explicit tri-state escape hatch
    (``tools``/``code``/``directory``/``direct``, or ``1``/``0``) for operators
    who want the surface changed without a runtime-home file. It is read here
    and nowhere else, which is what keeps the env-wiring audit honest.
    """
    env_value = os.getenv("AGENT_TOOL_DISCOVERY_CONFIG")
    if env_value is not None and env_value.strip():
        return apply_document(_parse_env_value(env_value.strip()))
    notes: list[str] = []
    value = override
    if value is _UNSET:
        value, document_note = _read_override_document()
        if document_note:
            notes.append(document_note)
    config, clamp_notes = apply_document(value)
    return config, notes + clamp_notes


def _parse_env_value(text: str) -> Any:
    """Parse ``AGENT_TOOL_DISCOVERY_CONFIG`` into a tri-state value."""
    lowered = text.lower()
    if lowered in VALID_MODES:
        return lowered
    if lowered in ("1", "true", "yes", "on"):
        return True
    if lowered in ("0", "false", "no", "off"):
        return False
    if text.startswith("{"):
        try:
            return json.loads(text)
        except ValueError:
            return None
    return None


def _read_override_document() -> tuple[Any, str]:
    """Read the runtime-home override document, if any. Never raises."""
    path = config_path()
    try:
        raw_text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, ""
    except OSError as exc:
        return None, f"tool discovery config at {path} unreadable; using defaults ({type(exc).__name__}: {exc})"
    try:
        document = json.loads(raw_text)
    except ValueError as exc:
        return None, f"tool discovery config at {path} is not valid JSON; using defaults ({exc})"
    if not isinstance(document, dict):
        return None, f"tool discovery config at {path} is not a JSON object; using defaults"
    if "toolSearch" in document:
        return document["toolSearch"], ""
    return document, ""
