"""Bounded, deterministic markdown recall blocks for structure memory.

Recall is a projection of indexed records, never a source-code search or a
fabricated completion.  Ranking is intentionally simple and inspectable:

1. exact path match (highest);
2. exact qualified symbol/module name;
3. number of query-token overlaps, normalized by query-token count;
4. lexical path/name tie-breaks.

Every non-empty result is rendered through a hard character budget.  If a
budget forces omission, the returned text contains an explicit ``truncated``
marker.  A blank query returns the empty string, and a query with no indexed
matches also returns the empty string.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from .config import CodebaseConfig
from .models import ChangeImpact, CodebaseSnapshot, ModuleRecord, SymbolRef

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
_DEFAULT_SYMBOLS_BUDGET = 2400
_DEFAULT_MODULE_SUMMARY_BUDGET = 2400
_DEFAULT_IMPACT_BUDGET = 1800
_DEFAULT_STRUCTURE_BUDGET = 3000


def _tokens(value: str) -> list[str]:
    return [token.lower() for token in _TOKEN_RE.findall(value or "")]


def _modules(source: CodebaseSnapshot | ModuleRecord | Iterable[ModuleRecord]) -> list[ModuleRecord]:
    if isinstance(source, CodebaseSnapshot):
        return list(source.modules)
    if isinstance(source, ModuleRecord):
        return [source]
    return [item for item in source if isinstance(item, ModuleRecord)]


def _snapshot(source: CodebaseSnapshot | ModuleRecord | Iterable[ModuleRecord]) -> CodebaseSnapshot | None:
    return source if isinstance(source, CodebaseSnapshot) else None


def _budget(config: CodebaseConfig | None, kind: str, explicit: int | None) -> int | None:
    if config is not None and not config.enabled:
        return None
    if explicit is not None:
        return max(0, int(explicit))
    if config is None:
        return {
            "symbols": _DEFAULT_SYMBOLS_BUDGET,
            "module_summary": _DEFAULT_MODULE_SUMMARY_BUDGET,
            "impact": _DEFAULT_IMPACT_BUDGET,
            "structure": _DEFAULT_STRUCTURE_BUDGET,
        }[kind]
    return {
        "symbols": config.symbols_char_budget,
        "module_summary": config.module_summary_char_budget,
        "impact": config.impact_char_budget,
        "structure": config.structure_char_budget,
    }[kind]


def _render(lines: list[str], budget: int, label: str) -> str:
    if budget <= 0:
        return ""
    full = "\n".join(line for line in lines if line)
    if len(full) <= budget:
        return full
    marker = f"\n[{label} truncated; char budget {budget}]"
    if len(marker) >= budget:
        return marker[:budget]
    available = budget - len(marker)
    rendered: list[str] = []
    used = 0
    for line in lines:
        if not line:
            continue
        addition = len(line) + (1 if rendered else 0)
        if used + addition > available:
            break
        rendered.append(line)
        used += addition
    body = "\n".join(rendered)
    return body + marker


def _match_score(query: str, path: str, *values: str) -> float:
    normalized_query = query.strip().casefold()
    normalized_path = path.casefold()
    if not normalized_query:
        return 0.0
    if normalized_query == normalized_path:
        return 1000.0
    if any(normalized_query == str(value).casefold() for value in values if value):
        return 900.0
    query_tokens = set(_tokens(query))
    if not query_tokens:
        return 0.0
    haystack = " ".join(value for value in (path, *values) if value).casefold()
    value_tokens = set(_tokens(haystack))
    overlap = len(query_tokens & value_tokens)
    score = overlap / len(query_tokens)
    if normalized_query in haystack:
        score += 0.25
    return score


def _module_lines(modules: list[ModuleRecord], query: str, *, top_k: int) -> list[str]:
    ranked: list[tuple[float, str, ModuleRecord]] = [
        (
            _match_score(query, module.path, module.summary, module.language, *module.imports, *(symbol.qualified_name for symbol in module.symbols)),
            module.path,
            module,
        )
        for module in modules
    ]
    ranked.sort(key=lambda item: (-item[0], item[1]))
    selected = [item for item in ranked if item[0] > 0.0][: max(0, top_k)]
    return [f"- `{module.path}` — {module.summary}" if module.summary else f"- `{module.path}`" for _, _, module in selected]


def symbols_block(
    source: CodebaseSnapshot | ModuleRecord | Iterable[ModuleRecord],
    query: str = "",
    *,
    char_budget: int | None = None,
    budget: int | None = None,
    max_chars: int | None = None,
    config: CodebaseConfig | None = None,
    top_k: int | None = None,
) -> str:
    """Return matching indexed symbols as bounded markdown."""

    if not (query or "").strip():
        return ""
    selected_budget = char_budget if char_budget is not None else budget if budget is not None else max_chars
    effective_budget = _budget(config, "symbols", selected_budget)
    if effective_budget is None:
        return ""
    limit = top_k if top_k is not None else (config.max_recall_items if config is not None else 20)
    ranked: list[tuple[float, str, str, str, SymbolRef]] = []
    for module in _modules(source):
        for symbol in module.symbols:
            score = _match_score(query, module.path, symbol.qualified_name, symbol.name, symbol.kind, module.summary)
            ranked.append((score, module.path, symbol.qualified_name, symbol.kind, symbol))
    ranked.sort(key=lambda item: (-item[0], item[1], item[2], item[3]))
    lines = [f"- `{item[1]}:{item[4].line_start}-{item[4].line_end}` **{item[4].kind}** `{item[4].qualified_name}`" for item in ranked if item[0] > 0.0][: max(0, limit)]
    return _render(lines, effective_budget, "symbols")


def module_summary_block(
    source: CodebaseSnapshot | ModuleRecord | Iterable[ModuleRecord],
    query: str = "",
    *,
    char_budget: int | None = None,
    budget: int | None = None,
    max_chars: int | None = None,
    config: CodebaseConfig | None = None,
    top_k: int | None = None,
) -> str:
    """Return matching module responsibility summaries."""

    if not (query or "").strip():
        return ""
    selected_budget = char_budget if char_budget is not None else budget if budget is not None else max_chars
    effective_budget = _budget(config, "module_summary", selected_budget)
    if effective_budget is None:
        return ""
    limit = top_k if top_k is not None else (config.max_recall_items if config is not None else 20)
    return _render(_module_lines(_modules(source), query, top_k=limit), effective_budget, "module summary")


def impact_block(
    impact: ChangeImpact,
    *,
    query: str | None = None,
    char_budget: int | None = None,
    budget: int | None = None,
    max_chars: int | None = None,
    config: CodebaseConfig | None = None,
) -> str:
    """Return a bounded reverse-impact block from an already computed result."""

    if query is not None and not query.strip():
        return ""
    selected_budget = char_budget if char_budget is not None else budget if budget is not None else max_chars
    effective_budget = _budget(config, "impact", selected_budget)
    if effective_budget is None or not isinstance(impact, ChangeImpact):
        return ""
    if not impact.directly_affected and not impact.transitively_affected and not impact.test_files:
        return ""
    lines = [f"### Change impact: `{impact.path}`", f"- confidence: `{impact.confidence}` (partial_index={impact.partial_index})"]
    lines.extend(f"- directly affected: `{path}`" for path in impact.directly_affected)
    lines.extend(f"- transitively affected (depth {depth}): `{path}`" for path, depth in impact.transitively_affected.items())
    if impact.test_files:
        lines.append("- test files: " + ", ".join(f"`{path}`" for path in impact.test_files))
    if impact.disclosures:
        lines.append("- disclosures: " + "; ".join(impact.disclosures))
    return _render(lines, effective_budget, "impact")


def structure_block(
    source: CodebaseSnapshot | Iterable[ModuleRecord],
    query: str = "",
    *,
    char_budget: int | None = None,
    budget: int | None = None,
    max_chars: int | None = None,
    config: CodebaseConfig | None = None,
    top_k: int | None = None,
) -> str:
    """Return matching module summaries and their indexed dependency edges."""

    if not (query or "").strip():
        return ""
    selected_budget = char_budget if char_budget is not None else budget if budget is not None else max_chars
    effective_budget = _budget(config, "structure", selected_budget)
    if effective_budget is None:
        return ""
    limit = top_k if top_k is not None else (config.max_recall_items if config is not None else 20)
    snapshot = _snapshot(source)
    modules = _modules(source)
    ranked_modules = sorted(
        ((_match_score(query, module.path, module.summary, *module.imports, *(symbol.qualified_name for symbol in module.symbols)), module.path, module) for module in modules),
        key=lambda item: (-item[0], item[1]),
    )
    selected = [item for item in ranked_modules if item[0] > 0.0][: max(0, limit)]
    selected_paths = {item[2].path for item in selected}
    lines: list[str] = []
    for _, _, module in selected:
        lines.append(f"- `{module.path}` — {module.summary}" if module.summary else f"- `{module.path}`")
        if module.imports:
            lines.append("  imports: " + ", ".join(f"`{value}`" for value in module.imports))
        if module.dependents:
            lines.append("  dependents: " + ", ".join(f"`{value}`" for value in module.dependents))
    if snapshot is not None:
        edges = [edge for edge in snapshot.edges if edge.from_path in selected_paths or edge.to_path in selected_paths]
        for edge in sorted(edges, key=lambda item: (item.from_path, item.to_path, item.kind, item.evidence)):
            lines.append(f"  edge: `{edge.from_path}` -> `{edge.to_path}` ({edge.kind}; {edge.weight:g})")
    return _render(lines, effective_budget, "structure")


class CodebaseRecaller:
    """Config-gated facade for the four block functions."""

    def __init__(self, config: CodebaseConfig | None = None, snapshot: CodebaseSnapshot | None = None) -> None:
        self.config = config or CodebaseConfig()
        self.snapshot = snapshot

    def _source(self, source: CodebaseSnapshot | ModuleRecord | Iterable[ModuleRecord] | None) -> CodebaseSnapshot | ModuleRecord | Iterable[ModuleRecord]:
        if source is not None:
            return source
        if self.snapshot is None:
            return []
        return self.snapshot

    def symbols(self, query: str, *, source: CodebaseSnapshot | ModuleRecord | Iterable[ModuleRecord] | None = None, char_budget: int | None = None, top_k: int | None = None) -> str:
        return symbols_block(self._source(source), query, char_budget=char_budget, config=self.config, top_k=top_k)

    def module_summary(self, query: str, *, source: CodebaseSnapshot | ModuleRecord | Iterable[ModuleRecord] | None = None, char_budget: int | None = None, top_k: int | None = None) -> str:
        return module_summary_block(self._source(source), query, char_budget=char_budget, config=self.config, top_k=top_k)

    def impact(self, impact: ChangeImpact, *, char_budget: int | None = None) -> str:
        return impact_block(impact, char_budget=char_budget, config=self.config)

    def structure(self, query: str, *, source: CodebaseSnapshot | Iterable[ModuleRecord] | None = None, char_budget: int | None = None, top_k: int | None = None) -> str:
        return structure_block(self._source(source), query, char_budget=char_budget, config=self.config, top_k=top_k)


CodebaseRecall = CodebaseRecaller


def recall_blocks(
    source: CodebaseSnapshot | Iterable[ModuleRecord],
    query: str,
    *,
    config: CodebaseConfig | None = None,
    impact: ChangeImpact | None = None,
) -> dict[str, str]:
    """Return all applicable blocks, with the empty string for no matches."""

    return {
        "symbols": symbols_block(source, query, config=config),
        "module_summary": module_summary_block(source, query, config=config),
        "impact": impact_block(impact, config=config) if impact is not None else "",
        "structure": structure_block(source, query, config=config),
    }


__all__ = [
    "CodebaseRecall",
    "CodebaseRecaller",
    "impact_block",
    "module_summary_block",
    "recall_blocks",
    "structure_block",
    "symbols_block",
]
