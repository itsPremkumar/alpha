"""Dynamic Token Burn-Rate & Cost Governor.

Tracks token consumption, dollar burn rates, and financial quotas in real-time
across projects, bot personas, and model tiers. Enforces circuit breakers and
automatically generates Human Approval requests when budget thresholds are reached.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from alpha.config.runtime_paths import runtime_home
from alpha.config.token_budget_config import get_current_budget_scope
from alpha.projects.approval_queue import get_approval_queue

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


class CircuitBreakerTrippedError(RuntimeError):
    """Raised when a scoped charge hits a tripped circuit breaker.

    The breaker hierarchy inherits: a tripped project breaker blocks every
    scope under it, and a tripped scope blocks itself and its declared
    children. The offending usage is still recorded before this is raised
    (the spend is real - the exception blocks further work, never erases
    accounting), so callers must treat it as an explicit stop signal rather
    than a soft warning.
    """

    def __init__(self, scope_id: str, blocked_by: str, detail: str) -> None:
        super().__init__(f"Scope '{scope_id}' blocked by tripped circuit breaker '{blocked_by}': {detail}")
        self.scope_id = scope_id
        self.blocked_by = blocked_by
        self.detail = detail


def _costs_storage_path() -> Path:
    return runtime_home() / "governance" / "costs.json"


MODEL_COST_PER_1K: dict[str, tuple[float, float]] = {
    # model: (input_cost_per_1k, output_cost_per_1k)
    "claude-3-7-sonnet": (0.003, 0.015),
    "claude-3-5-sonnet": (0.003, 0.015),
    "o3-mini": (0.0011, 0.0044),
    "gpt-4o": (0.0025, 0.010),
    "gpt-4o-mini": (0.00015, 0.0006),
    "gemini-2.0-flash": (0.0001, 0.0004),
    "deepseek-coder": (0.00014, 0.00028),
    "default": (0.001, 0.003),
}


@dataclass
class BudgetConfig:
    """Configurable budget guardrails per project."""

    daily_budget_usd: float = 10.0
    max_tokens_per_task: int = 250_000
    hourly_burn_rate_limit_usd: float = 5.0
    alert_threshold_ratio: float = 0.85

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BudgetConfig:
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered)


@dataclass
class TokenUsageRecord:
    """A granular log of token and cost consumption."""

    record_id: str
    project_id: str
    bot_name: str
    model_name: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    timestamp: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TokenUsageRecord:
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered)


@dataclass
class ScopeBudgetState:
    """A scope-aware sub-budget with an inherited circuit breaker.

    ``parent`` links the scope into the hierarchy (e.g. node -> workflow ->
    project); a tripped breaker (``spent_usd >= limit_usd``) blocks this scope
    and every declared child of it. The project-level daily budget acts as the
    implicit root breaker for all scopes.
    """

    scope_id: str
    limit_usd: float
    parent: str | None = None
    spent_usd: float = 0.0
    tripped: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScopeBudgetState:
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered)


class CostGovernor:
    """Thread-safe cost and token governor with circuit breaker enforcement."""

    def __init__(self, storage_path: Path | None = None):
        self._path = storage_path or _costs_storage_path()
        self._lock = threading.Lock()
        self._records: list[TokenUsageRecord] = []
        self._budgets: dict[str, BudgetConfig] = {}
        self._scopes: dict[str, ScopeBudgetState] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
            self._records = [TokenUsageRecord.from_dict(r) for r in data.get("records", [])]
            for proj_id, b_data in data.get("budgets", {}).items():
                self._budgets[proj_id] = BudgetConfig.from_dict(b_data)
            for scope_id, s_data in data.get("scopes", {}).items():
                self._scopes[scope_id] = ScopeBudgetState.from_dict(s_data)
        except Exception:
            logger.warning("Failed to load cost governor records", exc_info=True)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            payload = {
                "version": 1,
                "budgets": {k: b.to_dict() for k, b in self._budgets.items()},
                "scopes": {k: s.to_dict() for k, s in self._scopes.items()},
                "records": [r.to_dict() for r in self._records[-1000:]],
                "updated_at": _now(),
            }
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            tmp.replace(self._path)
        except Exception:
            logger.warning("Failed to save cost governor records", exc_info=True)

    def set_project_budget(self, project_id: str, budget: BudgetConfig) -> None:
        with self._lock:
            self._budgets[project_id] = budget
            self._save()

    def get_project_budget(self, project_id: str) -> BudgetConfig:
        with self._lock:
            return self._budgets.get(project_id, BudgetConfig())

    def set_scope_budget(self, scope_id: str, limit_usd: float, parent: str | None = None) -> None:
        """Declare a scope-aware sub-budget linked into the scope hierarchy.

        ``parent`` is the enclosing scope (e.g. node -> workflow -> project);
        its tripped breaker will block this scope and vice-versa once this
        scope itself trips.
        """
        if limit_usd <= 0:
            raise ValueError("scope limit_usd must be positive")
        with self._lock:
            self._scopes[scope_id] = ScopeBudgetState(scope_id=scope_id, limit_usd=float(limit_usd), parent=parent)
            self._save()

    def get_scope_state(self, scope_id: str) -> ScopeBudgetState | None:
        """Current sub-budget state (spend/tripped) for a scope, if declared."""
        with self._lock:
            return self._scopes.get(scope_id)

    def _scope_chain_locked(self, scope_id: str) -> list[str]:
        """``[scope_id, parent, ...]`` using ``self._scopes``; caller holds the lock."""
        if scope_id not in self._scopes:
            return []
        chain = [scope_id]
        cursor = self._scopes[scope_id].parent
        while cursor is not None and cursor not in chain:
            chain.append(cursor)
            cursor = self._scopes[cursor].parent if cursor in self._scopes else None
        return chain

    def _find_breaker_blocker(self, project_id: str, scope_id: str) -> tuple[str, str] | None:
        """Return ``(blocked_by, detail)`` when a tripped breaker blocks ``scope_id``.

        Evaluated against pre-charge state so the charge that crosses a limit
        is recorded (and trips its breaker); only *subsequent* charges raise.
        The project daily budget is the implicit root breaker.
        """
        budget = self.get_project_budget(project_id)
        spend = self.get_project_spend(project_id, hours=24)
        if spend >= budget.daily_budget_usd:
            return (f"project:{project_id}", f"project spend ${spend:.4f} >= daily budget ${budget.daily_budget_usd:.2f}")
        with self._lock:
            chain = self._scope_chain_locked(scope_id)
            trips = [(sid, self._scopes[sid].spent_usd, self._scopes[sid].limit_usd, self._scopes[sid].tripped) for sid in chain if sid in self._scopes]
        for sid, spent, limit, tripped in trips:
            if tripped or spent >= limit:
                return (sid, f"scope spend ${spent:.4f} >= limit ${limit:.2f}")
        return None

    def _charge_scope(self, scope_id: str, cost_usd: float) -> None:
        """Charge cost to the scope and each declared ancestor; trip on limit."""
        with self._lock:
            chain = self._scope_chain_locked(scope_id)
            changed = False
            for sid in chain:
                state = self._scopes.get(sid)
                if state is None:
                    continue
                state.spent_usd = round(state.spent_usd + cost_usd, 6)
                changed = True
                if not state.tripped and state.spent_usd >= state.limit_usd:
                    state.tripped = True
                    logger.warning(
                        "Circuit breaker tripped for scope %s: $%.4f of $%.2f limit",
                        sid, state.spent_usd, state.limit_usd,
                    )
            if changed:
                self._save()

    def record_usage(
        self,
        project_id: str,
        bot_name: str,
        model_name: str,
        input_tokens: int,
        output_tokens: int,
        *,
        scope_id: str | None = None,
    ) -> TokenUsageRecord:
        """Record model execution token counts and compute dollar cost.

        ``scope_id`` charges a scope-aware sub-budget; it defaults to the
        scope bound via :func:`alpha.config.token_budget_config.budget_scope`
        (``None`` = legacy per-project behaviour, unchanged). When a tripped
        breaker (the project daily budget or any scope in the chain) blocks
        the scope, the usage is still recorded - the spend is real - and
        :class:`CircuitBreakerTrippedError` is raised afterwards so the caller
        stops instead of continuing past the limit.
        """
        clean_model = model_name.lower().strip()
        in_rate, out_rate = MODEL_COST_PER_1K.get(clean_model, MODEL_COST_PER_1K["default"])
        if clean_model.startswith("ollama/") or "local" in clean_model:
            in_rate, out_rate = (0.0, 0.0)

        cost = (input_tokens / 1000.0 * in_rate) + (output_tokens / 1000.0 * out_rate)
        record = TokenUsageRecord(
            record_id=f"USG-{uuid.uuid4().hex[:8].upper()}",
            project_id=project_id,
            bot_name=bot_name,
            model_name=model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=round(cost, 6),
        )

        resolved_scope = scope_id if scope_id is not None else get_current_budget_scope()
        # Evaluate blockers against PRE-charge state: the charge that crosses a
        # limit is recorded and trips the breaker; later charges are blocked.
        blocker = self._find_breaker_blocker(project_id, resolved_scope) if resolved_scope else None

        with self._lock:
            self._records.append(record)
            self._save()

        if resolved_scope:
            self._charge_scope(resolved_scope, record.cost_usd)

        # Check circuit breakers and auto-queue approval if near threshold
        self._check_and_enforce_guardrails(project_id, bot_name)

        if blocker is not None:
            blocked_by, detail = blocker
            raise CircuitBreakerTrippedError(scope_id=resolved_scope or "", blocked_by=blocked_by, detail=detail)
        return record

    def get_project_spend(self, project_id: str, hours: int = 24) -> float:
        """Calculate total spend for a project over the last N hours."""
        cutoff = datetime.now(UTC) - timedelta(hours=hours)
        with self._lock:
            total = 0.0
            for r in self._records:
                if r.project_id == project_id:
                    try:
                        t = datetime.fromisoformat(r.timestamp)
                        if t >= cutoff:
                            total += r.cost_usd
                    except Exception:
                        pass
            return round(total, 4)

    def _check_and_enforce_guardrails(self, project_id: str, bot_name: str) -> None:
        budget = self.get_project_budget(project_id)
        current_daily_spend = self.get_project_spend(project_id, hours=24)
        threshold = budget.daily_budget_usd * budget.alert_threshold_ratio

        if current_daily_spend >= threshold:
            queue = get_approval_queue(project_id)
            # Check if pending approval already exists for budget extension
            has_pending = any(
                r.action_type == "budget_extension" and r.status == "pending"
                for r in queue.list_pending()
            )
            if not has_pending:
                queue.request_approval(
                    bot_name=bot_name,
                    action_type="budget_extension",
                    risk_level="high",
                    details={
                        "current_daily_spend": current_daily_spend,
                        "daily_budget_usd": budget.daily_budget_usd,
                        "requested_extension_usd": 5.0,
                        "reason": f"Project spend (${current_daily_spend:.2f}) exceeded {int(budget.alert_threshold_ratio*100)}% of daily limit.",
                    },
                )
                logger.warning(
                    "Circuit breaker alert: Project %s reached $%.2f of $%.2f budget",
                    project_id, current_daily_spend, budget.daily_budget_usd
                )

    def get_project_summary(self, project_id: str) -> dict[str, Any]:
        """Aggregate total token consumption and costs per bot and model."""
        budget = self.get_project_budget(project_id)
        total_spend = self.get_project_spend(project_id, hours=24)

        with self._lock:
            bot_breakdown: dict[str, dict[str, Any]] = {}
            for r in self._records:
                if r.project_id == project_id:
                    if r.bot_name not in bot_breakdown:
                        bot_breakdown[r.bot_name] = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
                    bot_breakdown[r.bot_name]["input_tokens"] += r.input_tokens
                    bot_breakdown[r.bot_name]["output_tokens"] += r.output_tokens
                    bot_breakdown[r.bot_name]["cost_usd"] = round(
                        bot_breakdown[r.bot_name]["cost_usd"] + r.cost_usd, 4
                    )

        return {
            "project_id": project_id,
            "daily_budget_usd": budget.daily_budget_usd,
            "current_spend_24h": total_spend,
            "budget_utilized_ratio": round(total_spend / max(0.01, budget.daily_budget_usd), 3),
            "bot_breakdown": bot_breakdown,
        }


_global_governor: CostGovernor | None = None
_governor_lock = threading.Lock()


def get_cost_governor() -> CostGovernor:
    global _global_governor
    with _governor_lock:
        if _global_governor is None:
            _global_governor = CostGovernor()
        return _global_governor
