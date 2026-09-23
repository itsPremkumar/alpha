"""Config for token budget middleware.

Besides the per-run limits, this module owns the optional *scoped* budget
hierarchy (global -> project -> goal -> workflow -> node -> agent):

- The active scope id is carried by a :mod:`contextvars` ContextVar set by
  callers (``budget_scope(...)`` / ``set_budget_scope(...)``). When no scope is
  set (the default), enforcement is exactly the pre-existing per-run behaviour
  so existing callers and tests are unchanged.
- Each declared scope may declare a ``parent``; a parent limit bounds every
  child: the child's effective budget is ``min(child limit, remaining parent)``
  recursively, so no scope can exceed its ancestors.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass

from pydantic import BaseModel, Field, model_validator

# Canonical scope hierarchy, coarsest first. Scope ids conventionally use
# ``"<level>:<name>"`` (e.g. ``workflow:refactor-auth``); the level prefix is
# only a labelling convenience - parent linkage is always explicit.
SCOPE_HIERARCHY: tuple[str, ...] = ("global", "project", "goal", "workflow", "node", "agent")

_current_budget_scope: ContextVar[str | None] = ContextVar("alpha_token_budget_scope", default=None)

# Usage dimension name per configured limit field.
_DIM_USAGE_KEY: dict[str, str] = {
    "max_tokens": "total",
    "max_input_tokens": "input",
    "max_output_tokens": "output",
}


def get_current_budget_scope() -> str | None:
    """Return the active budget scope id, or ``None`` (per-run behaviour)."""
    return _current_budget_scope.get()


def set_budget_scope(scope_id: str | None) -> Token:
    """Set the active budget scope; restore with :func:`reset_budget_scope`."""
    return _current_budget_scope.set(scope_id)


def reset_budget_scope(token: Token) -> None:
    """Restore the scope to the state captured from :func:`set_budget_scope`."""
    _current_budget_scope.reset(token)


@contextmanager
def budget_scope(scope_id: str) -> Iterator[None]:
    """Bind ``scope_id`` as the active budget scope for the duration of the block.

    Nesting is supported (e.g. workflow scope around node scope); the innermost
    binding wins and is restored on exit, including on exceptions.
    """
    token = _current_budget_scope.set(scope_id)
    try:
        yield
    finally:
        _current_budget_scope.reset(token)


class ScopeBudgetConfig(BaseModel):
    """A single scope with its optional limit(s) and explicit parent link."""

    scope_id: str = Field(min_length=1, description="Stable scope identifier, conventionally '<level>:<name>'.")
    parent: str | None = Field(default=None, description="Parent scope id; the parent's remaining limit bounds this scope.")
    level: str | None = Field(default=None, description="Hierarchy level (global/project/goal/workflow/node/agent); defaults to the scope_id prefix when it matches.")
    max_tokens: int | None = Field(default=None, ge=1, description="Optional total-token limit for this scope.")
    max_input_tokens: int | None = Field(default=None, ge=1, description="Optional input-token limit for this scope.")
    max_output_tokens: int | None = Field(default=None, ge=1, description="Optional output-token limit for this scope.")

    @model_validator(mode="after")
    def infer_level(self) -> ScopeBudgetConfig:
        """Default ``level`` from the ``<level>:`` scope-id prefix when valid."""
        if self.level is None and ":" in self.scope_id:
            prefix = self.scope_id.split(":", 1)[0]
            if prefix in SCOPE_HIERARCHY:
                self.level = prefix
        if self.level is not None and self.level not in SCOPE_HIERARCHY:
            raise ValueError(f"scope '{self.scope_id}' has unknown level '{self.level}' (expected one of {', '.join(SCOPE_HIERARCHY)})")
        return self


@dataclass(frozen=True)
class EffectiveScopeLimits:
    """Effective per-dimension limits for one scope, already bounded by ancestors.

    ``None`` for a dimension means "no configured bound anywhere in the chain".
    ``bounders`` lists every declared ancestor limit that clamped a dimension
    (scope id, limit, consumed, remaining) so exhaustion reports can show the
    real numbers instead of only the clamped value.
    """

    scope_id: str
    max_tokens: int | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    bounders: tuple[dict, ...] = ()


class TokenBudgetConfig(BaseModel):
    """Configuration for per-run token budget enforcement."""

    enabled: bool = Field(default=False, description="Whether to enable per-run token budget enforcement.")
    max_tokens: int = Field(default=200000, ge=1000, description="Maximum total tokens (input + output) allowed per run.")
    max_input_tokens: int | None = Field(default=None, ge=1, description="Optional separate limit for input tokens only.")
    max_output_tokens: int | None = Field(default=None, ge=1, description="Optional separate limit for output tokens only.")
    warn_threshold: float = Field(default=0.8, ge=0.0, le=1.0, description="Fraction of max_tokens at which a soft warning is injected. E.g., 0.8 means warn at 80% of max_tokens")
    hard_stop_threshold: float = Field(default=1.0, ge=0.0, le=1.0, description=("Fraction of max_tokens at which tool calls are stripped and the agent is forced to produce a final answer. E.g., 1.0 means stop at 100% of max_tokens."))
    scopes: list[ScopeBudgetConfig] = Field(
        default_factory=list,
        description="Optional scoped budget hierarchy (global/project -> goal -> workflow -> node -> agent). Empty (default) keeps pure per-run behaviour.",
    )

    @model_validator(mode="after")
    def validate_thresholds(self) -> TokenBudgetConfig:
        """Ensure hard stop cannot trigger before the warning."""
        if self.hard_stop_threshold < self.warn_threshold:
            raise ValueError("hard_stop_threshold must be >= warn_threshold")
        return self

    @model_validator(mode="after")
    def validate_scopes(self) -> TokenBudgetConfig:
        """Reject duplicate scope ids, dangling parents and hierarchy cycles."""
        index: dict[str, ScopeBudgetConfig] = {}
        for scope in self.scopes:
            if scope.scope_id in index:
                raise ValueError(f"duplicate budget scope id '{scope.scope_id}'")
            index[scope.scope_id] = scope
        for scope in self.scopes:
            if scope.parent is None:
                continue
            if scope.parent not in index:
                raise ValueError(f"budget scope '{scope.scope_id}' references unknown parent '{scope.parent}'")
            if scope.parent == scope.scope_id:
                raise ValueError(f"budget scope '{scope.scope_id}' cannot be its own parent")
            parent = index[scope.parent]
            if scope.level and parent.level:
                if SCOPE_HIERARCHY.index(parent.level) >= SCOPE_HIERARCHY.index(scope.level):
                    raise ValueError(f"budget scope '{scope.scope_id}' (level {scope.level}) cannot hang under parent '{parent.scope_id}' (level {parent.level})")
        # Cycle detection: walk each parent chain with a visited set.
        for scope in self.scopes:
            seen: set[str] = {scope.scope_id}
            cursor = scope.parent
            while cursor is not None:
                if cursor in seen:
                    raise ValueError(f"budget scope cycle detected at '{cursor}'")
                seen.add(cursor)
                cursor = index[cursor].parent
        return self

    def scope_index(self) -> dict[str, ScopeBudgetConfig]:
        """Declared scopes keyed by scope id."""
        return {scope.scope_id: scope for scope in self.scopes}

    def scope_chain(self, scope_id: str) -> list[str]:
        """``[scope_id, parent, ...]`` for a declared scope; ``[]`` if undeclared."""
        index = self.scope_index()
        if scope_id not in index:
            return []
        chain = [scope_id]
        cursor = index[scope_id].parent
        while cursor is not None and cursor not in chain:
            chain.append(cursor)
            cursor = index[cursor].parent
        return chain

    def effective_scope_limits(self, scope_id: str, usage: Mapping[str, Mapping[str, int]]) -> EffectiveScopeLimits | None:
        """Compute effective limits for ``scope_id`` given per-scope usage.

        ``usage`` maps scope id -> ``{"input": int, "output": int, "total": int}``
        for this scope and its ancestors (missing scopes count as 0).

        Returns ``None`` when ``scope_id`` is not declared (no scoped limit is
        enforceable; per-run limits still apply). Otherwise the result is
        ``min(own limit, remaining parent, remaining grandparent, ...)``
        per dimension - a parent limit bounds every child, and a dimension with
        no declared limit anywhere in the chain stays ``None`` (unbounded).
        """
        index = self.scope_index()
        node = index.get(scope_id)
        if node is None:
            return None

        effective: dict[str, int | None] = {field_name: getattr(node, field_name) for field_name in _DIM_USAGE_KEY}
        bounders: list[dict] = []

        seen: set[str] = {scope_id}
        cursor = node.parent
        while cursor is not None and cursor not in seen:
            seen.add(cursor)
            ancestor = index.get(cursor)
            if ancestor is None:
                break
            for field_name, usage_key in _DIM_USAGE_KEY.items():
                limit = getattr(ancestor, field_name)
                if limit is None:
                    continue
                consumed = int(usage.get(ancestor.scope_id, {}).get(usage_key, 0))
                remaining = max(0, limit - consumed)
                if effective[field_name] is None or remaining < effective[field_name]:
                    effective[field_name] = remaining
                bounders.append(
                    {
                        "scope_id": ancestor.scope_id,
                        "dimension": usage_key,
                        "limit": limit,
                        "consumed": consumed,
                        "remaining": remaining,
                    }
                )
            cursor = ancestor.parent

        return EffectiveScopeLimits(
            scope_id=scope_id,
            max_tokens=effective["max_tokens"],
            max_input_tokens=effective["max_input_tokens"],
            max_output_tokens=effective["max_output_tokens"],
            bounders=tuple(bounders),
        )
