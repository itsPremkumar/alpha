"""Review guard middleware.

Enforces comment-density and role-scoped write policies for code edits.
Extends the existing read_before_write middleware with review-time checks.
"""

import logging
import re
from collections.abc import Callable
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langgraph.types import Command

from alpha.config.review_guard_config import ReviewGuardConfig, get_review_guard_config

logger = logging.getLogger(__name__)


class ReviewGuardMiddlewareState(AgentState):
    """Compatible with the ThreadState schema."""

    pass


# Tool names that perform writes
_WRITE_TOOL_NAMES = frozenset(
    {
        "write_file",
        "str_replace",
    }
)

# Comment patterns for different languages
_COMMENT_PATTERNS = {
    ".py": re.compile(r"^\s*#"),
    ".ts": re.compile(r"^\s*//"),
    ".tsx": re.compile(r"^\s*//"),
    ".js": re.compile(r"^\s*//"),
    ".jsx": re.compile(r"^\s*//"),
    ".java": re.compile(r"^\s*//"),
    ".cpp": re.compile(r"^\s*//"),
    ".c": re.compile(r"^\s*//"),
    ".go": re.compile(r"^\s*//"),
    ".rs": re.compile(r"^\s*//"),
    ".md": re.compile(r"^\s*<!--"),  # HTML comments in markdown
}


def _get_extension(path: str) -> str:
    """Extract file extension from path."""
    idx = path.rfind(".")
    return path[idx:] if idx >= 0 else ""


def _calculate_comment_ratio(content: str, extension: str) -> float:
    """Calculate comment-to-code ratio for a file content."""
    if not content:
        return 1.0

    pattern = _COMMENT_PATTERNS.get(extension)
    if not pattern:
        return 1.0  # No enforcement for unknown extensions

    lines = content.splitlines()
    if not lines:
        return 1.0

    comment_lines = sum(1 for line in lines if pattern.match(line))
    non_empty_lines = sum(1 for line in lines if line.strip())

    if non_empty_lines == 0:
        return 1.0

    return comment_lines / non_empty_lines


def _resolve_role(runtime: Runtime, default_role: str) -> str:
    """Resolve the current role from runtime context."""
    if runtime.context and isinstance(runtime.context, dict):
        # Check for explicit role in context
        role = runtime.context.get("review_role")
        if role:
            return str(role)
        # Fall back to principal role if available
        principal = runtime.context.get("principal")
        if principal and hasattr(principal, "role"):
            return str(principal.role)
    return default_role


def _is_write_allowed(role: str, extension: str, policy: dict[str, list[str]]) -> bool:
    """Check if a role is allowed to write files with the given extension."""
    allowed = policy.get(role, policy.get("default", []))
    return extension in allowed


class ReviewGuardMiddleware(AgentMiddleware[ReviewGuardMiddlewareState]):
    """Middleware that enforces review policies on write operations."""

    state_schema = ReviewGuardMiddlewareState

    def __init__(
        self,
        *,
        review_guard_config: ReviewGuardConfig | None = None,
    ) -> None:
        super().__init__()
        self._config = review_guard_config or get_review_guard_config()

    @override
    async def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        """Intercept write tool calls and enforce review policies."""
        tool_name = request.tool_call.get("name", "")
        if tool_name not in _WRITE_TOOL_NAMES:
            return await handler(request)

        # Extract file path from tool arguments
        tool_args = request.tool_call.get("args", {})
        path = tool_args.get("path") or tool_args.get("file_path")
        if not path:
            logger.debug("ReviewGuard: no path in %s args, allowing", tool_name)
            return await handler(request)

        extension = _get_extension(path)
        if not extension:
            logger.debug("ReviewGuard: no extension for %s, allowing", path)
            return await handler(request)

        # Check role-scoped write policy using request.runtime
        role = _resolve_role(request.runtime, self._config.default_role)
        if not _is_write_allowed(role, extension, self._config.role_write_policy):
            logger.warning("ReviewGuard: role %s not allowed to write %s files", role, extension)
            return ToolMessage(
                content=f"Error: Role '{role}' is not permitted to write {extension} files. Allowed: {self._config.role_write_policy.get(role, [])}",
                tool_call_id=str(request.tool_call.get("id") or ""),
                name=tool_name,
                status="error",
            )

        # Execute the tool first to get the new content
        result = await handler(request)

        # Check comment density on successful writes
        if isinstance(result, ToolMessage) and result.status != "error":
            new_content = self._extract_new_content(tool_name, tool_args, result)
            if new_content:
                ratio = _calculate_comment_ratio(new_content, extension)
                if ratio < self._config.min_comment_ratio:
                    logger.warning(
                        "ReviewGuard: comment ratio %.2f below minimum %.2f for %s",
                        ratio,
                        self._config.min_comment_ratio,
                        path,
                    )
                    return ToolMessage(
                        content=(
                            f"Error: Comment density too low ({ratio:.1%}). "
                            f"Minimum required: {self._config.min_comment_ratio:.1%}. "
                            f"Add more comments to {path}."
                        ),
                        tool_call_id=str(request.tool_call.get("id") or ""),
                        name=tool_name,
                        status="error",
                    )

        return result

    def release_policy_parameters(self) -> dict[str, object]:
        """Expose config for assembly identity."""
        return {
            "review_guard_enabled": self._config.enabled,
            "review_guard_min_comment_ratio": self._config.min_comment_ratio,
            "review_guard_enforce_extensions": tuple(sorted(self._config.enforce_on_extensions)),
            "review_guard_role_policy": self._config.role_write_policy,
        }


def build_review_guard_middleware(
    *,
    review_guard_config: ReviewGuardConfig | None = None,
) -> ReviewGuardMiddleware:
    """Factory for the review guard middleware."""
    return ReviewGuardMiddleware(
        review_guard_config=review_guard_config,
    )