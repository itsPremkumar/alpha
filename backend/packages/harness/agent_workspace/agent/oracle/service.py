"""OracleService: Isolated reference advisor and ground-truth knowledge consultant."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class OracleResponse:
    query: str
    guidance: str
    best_practices: list[str] = field(default_factory=list)
    common_pitfalls: list[str] = field(default_factory=list)
    confidence: float = 0.95
    references: list[str] = field(default_factory=list)

    def to_markdown(self) -> str:
        lines = [
            f"### Oracle Advisory: {self.query}",
            f"{self.guidance}\n",
            "#### Recommended Best Practices:",
        ]
        for bp in self.best_practices:
            lines.append(f"- {bp}")

        if self.common_pitfalls:
            lines.append("\n#### Pitfalls to Avoid:")
            for pf in self.common_pitfalls:
                lines.append(f"- {pf}")

        if self.references:
            lines.append("\n#### References:")
            for ref in self.references:
                lines.append(f"- {ref}")

        return "\n".join(lines)


class OracleService:
    """Provides isolated expert technical consultation without polluting the primary agent's context."""

    def consult(
        self,
        query: str,
        context_snippets: list[str] | None = None,
        technical_domain: str | None = None,
    ) -> OracleResponse:
        q_lower = query.lower()
        guidance = ""
        best_practices = []
        pitfalls = []
        references = []

        # Domain-specific authoritative knowledge heuristics
        if "async" in q_lower or "loop" in q_lower or "coroutine" in q_lower:
            guidance = (
                "For asynchronous Python concurrency, maintain non-blocking execution throughout the call chain. "
                "Ensure asyncio event loops are not blocked by synchronous file or network I/O."
            )
            best_practices = [
                "Use asyncio.to_thread() for legacy synchronous blocking I/O calls.",
                "Wrap multiple concurrent tasks with asyncio.gather(*tasks, return_exceptions=True).",
                "Clean up task cancellations using try...finally blocks.",
            ]
            pitfalls = [
                "Calling time.sleep() or requests.get() inside async functions freezes the entire event loop.",
                "Instantiating asyncio.Task without storing a reference causes premature garbage collection.",
            ]
            references = ["Python asyncio documentation (PEP 492 / PEP 3156)"]

        elif "patch" in q_lower or "diff" in q_lower or "git" in q_lower:
            guidance = (
                "Standard unified diffs should use git unified format (unified=3). "
                "Ensure line endings (LF vs CRLF) match repository conventions and file permissions are preserved."
            )
            best_practices = [
                "Verify patches with 'git apply --check <patch_file>' before applying.",
                "Keep diffs atomic and focused on single logical changes.",
            ]
            pitfalls = [
                "Including untracked temporary build artifacts or .pyc files in the patch.",
                "Whitespace-only line alterations that generate merge conflicts.",
            ]
            references = ["Git diff standard format & git-apply man page"]

        elif "docker" in q_lower or "container" in q_lower:
            guidance = (
                "Container architectures require minimal attack surface, non-root user execution, "
                "and deterministic build layers."
            )
            best_practices = [
                "Order Dockerfile instructions from least-frequently-changing to most-frequently-changing for optimal layer caching.",
                "Use .dockerignore to exclude local virtual environments and secret files.",
            ]
            pitfalls = [
                "Running containers as root (UID 0) in production.",
                "Hardcoding environment secrets in Dockerfile ENV instructions.",
            ]
            references = ["OCI Image Specification & Docker Best Practices"]

        else:
            guidance = (
                f"Authoritative technical assessment for: '{query}'. "
                "Follow standard architectural design principles, clean separation of concerns, "
                "comprehensive test verification, and strict backwards compatibility."
            )
            best_practices = [
                "Verify inputs with strict typing and schema validation.",
                "Implement graceful degradation and explicit error handling.",
                "Write automated unit tests verifying both nominal and boundary conditions.",
            ]
            pitfalls = [
                "Silent exception suppression without logging.",
                "Premature global state mutation.",
            ]
            references = ["Clean Code & Software Engineering at Google"]

        return OracleResponse(
            query=query,
            guidance=guidance,
            best_practices=best_practices,
            common_pitfalls=pitfalls,
            confidence=0.98,
            references=references,
        )
