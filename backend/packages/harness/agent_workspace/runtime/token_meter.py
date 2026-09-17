"""Process-local cumulative token ledger (DeepSeek-Harness-style token meter).

``ctx.tokenMeter`` in DeepSeek Harness is the central usage vocabulary that
budgets and benchmarks build on. DeerFlow already enforces per-run limits
(:class:`TokenBudgetMiddleware`) and attributes usage per message
(:class:`TokenUsageMiddleware`), but nothing accumulates spend across the
turns of a thread or the runs of a user inside the process. This module is
that ledger: small, dependency-free, thread-safe, and safe to import from
anywhere in the harness.

Scope notes (read before extending):
- Process-local by design. Multi-worker deployments must keep using the
  durable reporting layer (``runs.token_usage_by_model`` + the console
  ``/usage`` route); this meter is for in-process budgets, live guards, and
  tests — never for billing.
- Bounded memory: aggregates are keyed by ``(user_id, thread_id, model)``
  and capped at ``max_scopes`` entries (oldest-inserted evicted). No
  per-event rows are ever retained.
- Recording is total: non-integer or negative counts coerce to ``0`` rather
  than raising out of a middleware hot path.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class TokenSnapshot:
    """Aggregate usage over the selected scopes."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    runs: int = 0
    scopes: int = 0


@dataclass
class _ScopeUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    runs: int = 0


def _coerce_count(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int) and value > 0:
        return value
    return 0


def _coerce_key(value: object) -> str:
    if isinstance(value, str) and value:
        return value
    return "unknown"


class TokenMeter:
    """Thread-safe cumulative token ledger keyed by ``(user_id, thread_id, model)``."""

    def __init__(self, *, max_scopes: int = 10_000) -> None:
        if max_scopes <= 0:
            raise ValueError(f"max_scopes must be positive, got {max_scopes!r}")
        self._max_scopes = max_scopes
        self._lock = threading.Lock()
        self._scopes: dict[tuple[str, str, str], _ScopeUsage] = {}

    @property
    def scope_count(self) -> int:
        """Number of tracked scopes (bounded by ``max_scopes``)."""
        with self._lock:
            return len(self._scopes)

    def record(
        self,
        *,
        user_id: object = None,
        thread_id: object = None,
        model: object = None,
        input_tokens: object = 0,
        output_tokens: object = 0,
    ) -> None:
        """Add one model-response usage reading to the ledger. Never raises
        on malformed input — counts coerce to ``0``, keys to ``"unknown"``."""
        key = (_coerce_key(user_id), _coerce_key(thread_id), _coerce_key(model))
        incoming_in = _coerce_count(input_tokens)
        incoming_out = _coerce_count(output_tokens)
        with self._lock:
            scope = self._scopes.get(key)
            if scope is None:
                while len(self._scopes) >= self._max_scopes:
                    self._scopes.pop(next(iter(self._scopes)))
                scope = self._scopes[key] = _ScopeUsage()
            scope.input_tokens += incoming_in
            scope.output_tokens += incoming_out
            scope.runs += 1

    def snapshot(
        self,
        *,
        user_id: str | None = None,
        thread_id: str | None = None,
        model: str | None = None,
    ) -> TokenSnapshot:
        """Aggregate over scopes matching all given filters (``None`` = wildcard)."""
        total_in = total_out = runs = scopes = 0
        with self._lock:
            items = list(self._scopes.items())
        for (scope_user, scope_thread, scope_model), usage in items:
            if user_id is not None and scope_user != user_id:
                continue
            if thread_id is not None and scope_thread != thread_id:
                continue
            if model is not None and scope_model != model:
                continue
            total_in += usage.input_tokens
            total_out += usage.output_tokens
            runs += usage.runs
            scopes += 1
        return TokenSnapshot(
            input_tokens=total_in,
            output_tokens=total_out,
            total_tokens=total_in + total_out,
            runs=runs,
            scopes=scopes,
        )

    def check_budget(
        self,
        max_total_tokens: int,
        *,
        user_id: str | None = None,
        thread_id: str | None = None,
        model: str | None = None,
    ) -> dict[str, int | bool]:
        """Compare selected-scope usage against a token budget.

        Returns ``{"max_total_tokens", "used", "remaining", "exceeded"}``.
        A non-positive budget is fail-closed (always exceeded) so a
        misconfigured ``0`` cannot read as unlimited.
        """
        used = self.snapshot(user_id=user_id, thread_id=thread_id, model=model).total_tokens
        exceeded = max_total_tokens <= 0 or used >= max_total_tokens
        return {
            "max_total_tokens": max_total_tokens,
            "used": used,
            "remaining": max(0, max_total_tokens - used),
            "exceeded": exceeded,
        }

    def reset(
        self,
        *,
        user_id: str | None = None,
        thread_id: str | None = None,
        model: str | None = None,
    ) -> int:
        """Clear scopes matching all given filters. Returns the cleared count."""
        with self._lock:
            doomed = [key for key in self._scopes if (user_id is None or key[0] == user_id) and (thread_id is None or key[1] == thread_id) and (model is None or key[2] == model)]
            for key in doomed:
                del self._scopes[key]
            return len(doomed)


_DEFAULT_METER = TokenMeter()


def default_meter() -> TokenMeter:
    """Process-global meter fed by :class:`TokenUsageMiddleware`."""
    return _DEFAULT_METER


def record_token_usage(
    *,
    user_id: object = None,
    thread_id: object = None,
    model: object = None,
    input_tokens: object = 0,
    output_tokens: object = 0,
) -> None:
    """Record one reading on the process-global meter."""
    _DEFAULT_METER.record(
        user_id=user_id,
        thread_id=thread_id,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def meter_snapshot(
    *,
    user_id: str | None = None,
    thread_id: str | None = None,
    model: str | None = None,
) -> TokenSnapshot:
    """Snapshot the process-global meter over the selected scopes."""
    return _DEFAULT_METER.snapshot(user_id=user_id, thread_id=thread_id, model=model)


def check_token_budget(
    max_total_tokens: int,
    *,
    user_id: str | None = None,
    thread_id: str | None = None,
    model: str | None = None,
) -> dict[str, int | bool]:
    """Budget check against the process-global meter."""
    return _DEFAULT_METER.check_budget(max_total_tokens, user_id=user_id, thread_id=thread_id, model=model)


def reset_token_meter(
    *,
    user_id: str | None = None,
    thread_id: str | None = None,
    model: str | None = None,
) -> int:
    """Clear process-global meter scopes. Returns the cleared count."""
    return _DEFAULT_METER.reset(user_id=user_id, thread_id=thread_id, model=model)


__all__ = [
    "TokenMeter",
    "TokenSnapshot",
    "check_token_budget",
    "default_meter",
    "meter_snapshot",
    "record_token_usage",
    "reset_token_meter",
]
