"""Deep Handoff Contract and clean-context protocol.

Defines the strict serializable data contract for delegating work to Deep Agents
and returning compact synthesis back to the parent orchestrator.

Design goals:
- Zero human-in-the-loop blocking: all fields are machine verifiable.
- Clean context isolation: raw exploration traces never cross this boundary.
  Only the compact contract below is returned to the parent.
- Mathematical context compression: tens of thousands of intermediate tokens
  collapse into under 1,000 tokens of actionable synthesis.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

MAX_SUMMARY_WORDS = 300
PARENT_SYNTHESIS_CHAR_BUDGET = 4000
ESTIMATED_CHARS_PER_TOKEN = 4


class DeepExecutionStatus(StrEnum):
    """Terminal execution status of a Deep Agent run."""

    SUCCESS = "SUCCESS"
    PARTIAL_PROGRESS = "PARTIAL_PROGRESS"
    UNRECOVERABLE_ERROR = "UNRECOVERABLE_ERROR"


def count_words(text: str) -> int:
    """Count whitespace separated words in a text.

    Args:
        text: Input text.

    Returns:
        Number of words.
    """
    if not text or not text.strip():
        return 0
    return len(text.strip().split())


def truncate_to_words(text: str, max_words: int) -> str:
    """Truncate text to a maximum number of words.

    Args:
        text: Input text.
        max_words: Maximum words to keep.

    Returns:
        Truncated text.
    """
    words = text.strip().split()
    if len(words) <= max_words:
        return text.strip()
    return " ".join(words[:max_words])


def estimate_tokens(text: str) -> int:
    """Estimate token count using a fixed character ratio.

    Args:
        text: Input text.

    Returns:
        Estimated token count.
    """
    if not text:
        return 0
    return max(1, len(text) // ESTIMATED_CHARS_PER_TOKEN)


@dataclass
class DeepTaskSpec:
    """Specification for a delegated Deep Agent task.

    Attributes:
        goal: Goal description for the deep agent.
        target_files: Targeted file paths relevant to the task.
        token_budget: Token budget ceiling for the isolated run.
        allowed_toolset: Allowed tool names inside the isolated run.
        max_iterations: Maximum autonomous iterations before halting.
        agent_type: Requested deep specialist type identifier.
        time_budget_seconds: Wall clock budget for the isolated run.
        context_hints: Optional bounded hints (never raw dumps).
    """

    goal: str
    target_files: list[str] = field(default_factory=list)
    token_budget: int = 50000
    allowed_toolset: list[str] = field(default_factory=list)
    max_iterations: int = 15
    agent_type: str = "general"
    time_budget_seconds: int = 900
    context_hints: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.goal or not self.goal.strip():
            raise ValueError("goal must be a non-empty string")
        if self.token_budget <= 0:
            raise ValueError("token_budget must be positive")
        if self.max_iterations <= 0:
            raise ValueError("max_iterations must be positive")
        if self.time_budget_seconds <= 0:
            raise ValueError("time_budget_seconds must be positive")
        self.target_files = [str(p) for p in (self.target_files or [])]
        self.allowed_toolset = [str(t) for t in (self.allowed_toolset or [])]
        self.context_hints = [str(h) for h in (self.context_hints or [])]
        if len(self.goal) > 8000:
            raise ValueError("goal exceeds 8000 characters; keep the spec compact")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON serializable dictionary.

        Returns:
            Dictionary representation of the task spec.
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> DeepTaskSpec:
        """Build a task spec from a dictionary.

        Args:
            payload: Dictionary payload.

        Returns:
            Reconstructed DeepTaskSpec instance.
        """
        return cls(
            goal=str(payload.get("goal", "")),
            target_files=list(payload.get("target_files", []) or []),
            token_budget=int(payload.get("token_budget", 50000)),
            allowed_toolset=list(payload.get("allowed_toolset", []) or []),
            max_iterations=int(payload.get("max_iterations", 15)),
            agent_type=str(payload.get("agent_type", "general")),
            time_budget_seconds=int(payload.get("time_budget_seconds", 900)),
            context_hints=list(payload.get("context_hints", []) or []),
        )


@dataclass
class DeepHandoffContract:
    """Compact synthesis contract returned from a Deep Agent to its parent.

    Attributes:
        status: Terminal execution status.
        executive_summary: Executive summary capped at 300 words.
        unified_diff: Minimal unified diff or patch text.
        test_oracles: Verified test oracles (commands and outcomes).
        security_stamps: Security audit stamps.
        invariant_assertions: Key invariant assertions verified by the agent.
        tokens_consumed: Tokens consumed inside the isolated run.
        tokens_returned: Estimated tokens of the parent visible synthesis.
        session_id: Isolated session identifier.
        agent_type: Specialist type that produced the contract.
        artifacts: Bounded list of artifact paths produced.
        error_detail: Optional error detail for unrecoverable failures.
    """

    status: DeepExecutionStatus
    executive_summary: str
    unified_diff: str = ""
    test_oracles: list[dict[str, Any]] = field(default_factory=list)
    security_stamps: list[str] = field(default_factory=list)
    invariant_assertions: list[str] = field(default_factory=list)
    tokens_consumed: int = 0
    tokens_returned: int = 0
    session_id: str = ""
    agent_type: str = ""
    artifacts: list[str] = field(default_factory=list)
    error_detail: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            self.status = DeepExecutionStatus(self.status)
        word_count = count_words(self.executive_summary or "")
        if word_count > MAX_SUMMARY_WORDS:
            self.executive_summary = truncate_to_words(self.executive_summary, MAX_SUMMARY_WORDS)
        if self.tokens_consumed < 0:
            raise ValueError("tokens_consumed must be non-negative")
        if self.tokens_returned <= 0:
            self.tokens_returned = max(1, estimate_tokens(self.to_parent_text()))

    def compression_ratio(self) -> float:
        """Compute context compression ratio.

        Returns:
            Ratio of consumed tokens to returned tokens.
        """
        if self.tokens_returned <= 0:
            return float(self.tokens_consumed)
        return float(self.tokens_consumed) / float(self.tokens_returned)

    def to_parent_text(self) -> str:
        """Render the parent visible synthesis text within budget.

        Returns:
            Bounded synthesis text safe for the parent context.
        """
        lines = [
            f"status={self.status.value}",
            f"agent={self.agent_type or 'deep'}",
            f"summary={self.executive_summary.strip()}",
        ]
        if self.invariant_assertions:
            lines.append("invariants=" + "; ".join(self.invariant_assertions[:10]))
        if self.test_oracles:
            oracle_bits = []
            for oracle in self.test_oracles[:5]:
                if isinstance(oracle, dict):
                    oracle_bits.append(str(oracle.get("name") or oracle.get("command") or "oracle"))
                else:
                    oracle_bits.append(str(oracle))
            lines.append("oracles=" + "; ".join(oracle_bits))
        if self.security_stamps:
            lines.append("security=" + "; ".join(self.security_stamps[:5]))
        if self.unified_diff:
            diff = self.unified_diff
            if len(diff) > 1500:
                diff = diff[:1500] + "\n... [diff truncated for parent context]"
            lines.append("diff:\n" + diff)
        if self.error_detail and self.status == DeepExecutionStatus.UNRECOVERABLE_ERROR:
            detail = self.error_detail[:500]
            lines.append(f"error={detail}")
        text = "\n".join(lines)
        if len(text) > PARENT_SYNTHESIS_CHAR_BUDGET:
            text = text[:PARENT_SYNTHESIS_CHAR_BUDGET] + "\n... [synthesis truncated]"
        return text

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON serializable dictionary.

        Returns:
            Dictionary representation of the contract.
        """
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["summary_word_count"] = count_words(self.executive_summary)
        payload["compression_ratio"] = self.compression_ratio()
        payload["parent_text"] = self.to_parent_text()
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> DeepHandoffContract:
        """Build a contract from a dictionary.

        Args:
            payload: Dictionary payload.

        Returns:
            Reconstructed DeepHandoffContract instance.
        """
        status_raw = payload.get("status", DeepExecutionStatus.PARTIAL_PROGRESS.value)
        status = DeepExecutionStatus(status_raw) if isinstance(status_raw, str) else status_raw
        return cls(
            status=status,
            executive_summary=str(payload.get("executive_summary", "")),
            unified_diff=str(payload.get("unified_diff", "")),
            test_oracles=list(payload.get("test_oracles", []) or []),
            security_stamps=[str(s) for s in (payload.get("security_stamps", []) or [])],
            invariant_assertions=[str(s) for s in (payload.get("invariant_assertions", []) or [])],
            tokens_consumed=int(payload.get("tokens_consumed", 0)),
            tokens_returned=int(payload.get("tokens_returned", 0)),
            session_id=str(payload.get("session_id", "")),
            agent_type=str(payload.get("agent_type", "")),
            artifacts=[str(a) for a in (payload.get("artifacts", []) or [])],
            error_detail=str(payload.get("error_detail", "")),
        )

    def is_success(self) -> bool:
        """Return True when the run succeeded.

        Returns:
            True for SUCCESS status.
        """
        return self.status == DeepExecutionStatus.SUCCESS


def build_partial_contract(
    session_id: str,
    agent_type: str,
    summary: str,
    *,
    tokens_consumed: int = 0,
    artifacts: list[str] | None = None,
) -> DeepHandoffContract:
    """Build a partial progress contract for halted or budgeted runs.

    Args:
        session_id: Isolated session identifier.
        agent_type: Specialist type identifier.
        summary: Partial summary text.
        tokens_consumed: Tokens consumed before halting.
        artifacts: Optional artifact paths recovered before halting.

    Returns:
        Partial progress handoff contract.
    """
    return DeepHandoffContract(
        status=DeepExecutionStatus.PARTIAL_PROGRESS,
        executive_summary=truncate_to_words(summary, MAX_SUMMARY_WORDS),
        tokens_consumed=tokens_consumed,
        session_id=session_id,
        agent_type=agent_type,
        artifacts=list(artifacts or []),
    )


def build_error_contract(
    session_id: str,
    agent_type: str,
    error_detail: str,
) -> DeepHandoffContract:
    """Build an unrecoverable error contract.

    Args:
        session_id: Isolated session identifier.
        agent_type: Specialist type identifier.
        error_detail: Compact error description.

    Returns:
        Error handoff contract.
    """
    return DeepHandoffContract(
        status=DeepExecutionStatus.UNRECOVERABLE_ERROR,
        executive_summary="Deep agent run failed without recoverable progress.",
        error_detail=error_detail[:2000],
        session_id=session_id,
        agent_type=agent_type,
    )
