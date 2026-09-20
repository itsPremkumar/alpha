"""Fast Recon Worker (Explore & Librarian Profile).

Inspired by Oh My OpenAgent (OmO / Sisyphus):
- Roles: explore (codebase symbol search) & librarian (external docs/OSS patterns)
- Model Assignment: openai/gpt-5.6-luna-fast / deepseek-v4-flash (reasoning: low)
- Strengths: Sub-second search, low token cost, high-throughput symbol discovery
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ReconResult:
    """Lightweight reconnaissance telemetry and discovered references."""
    query: str
    recon_type: str  # "explore" (codebase) or "librarian" (documentation)
    matches: list[dict[str, Any]] = field(default_factory=list)
    latency_ms: float = 0.0
    model_family: str = "openai/gpt-5.6-luna-fast"
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FastReconWorker:
    """High-speed exploration worker feeding symbol context to planners and consultants."""

    def __init__(self, model_id: str = "openai/gpt-5.6-luna-fast"):
        self.model_id: str = model_id

    def search_codebase_symbols(
        self,
        symbol_query: str,
        file_tree: list[str] | None = None,
    ) -> ReconResult:
        """Fast grep and symbol pattern matching across codebase."""
        t_start = time.time()
        matches: list[dict[str, Any]] = []

        q = symbol_query.lower()
        if file_tree:
            for f in file_tree:
                if q in f.lower():
                    matches.append({"file": f, "match_type": "path_or_symbol"})

        elapsed_ms = (time.time() - t_start) * 1000.0

        return ReconResult(
            query=symbol_query,
            recon_type="explore",
            matches=matches,
            latency_ms=round(elapsed_ms, 2),
            model_family=self.model_id,
        )

    def search_documentation(
        self,
        topic: str,
        available_docs: dict[str, str] | None = None,
    ) -> ReconResult:
        """Fast documentation lookup and API pattern retrieval."""
        t_start = time.time()
        matches: list[dict[str, Any]] = []

        q = topic.lower()
        if available_docs:
            for doc_name, content in available_docs.items():
                if q in doc_name.lower() or q in content.lower():
                    matches.append({"doc_name": doc_name, "snippet": content[:200]})

        elapsed_ms = (time.time() - t_start) * 1000.0

        return ReconResult(
            query=topic,
            recon_type="librarian",
            matches=matches,
            latency_ms=round(elapsed_ms, 2),
            model_family=self.model_id,
        )
