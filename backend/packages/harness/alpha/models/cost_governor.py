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
from alpha.projects.approval_queue import get_approval_queue

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


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


class CostGovernor:
    """Thread-safe cost and token governor with circuit breaker enforcement."""

    def __init__(self, storage_path: Path | None = None):
        self._path = storage_path or _costs_storage_path()
        self._lock = threading.Lock()
        self._records: list[TokenUsageRecord] = []
        self._budgets: dict[str, BudgetConfig] = {}
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
        except Exception:
            logger.warning("Failed to load cost governor records", exc_info=True)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            payload = {
                "version": 1,
                "budgets": {k: b.to_dict() for k, b in self._budgets.items()},
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

    def record_usage(
        self,
        project_id: str,
        bot_name: str,
        model_name: str,
        input_tokens: int,
        output_tokens: int,
    ) -> TokenUsageRecord:
        """Record model execution token counts and compute dollar cost."""
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

        with self._lock:
            self._records.append(record)
            self._save()

        # Check circuit breakers and auto-queue approval if near threshold
        self._check_and_enforce_guardrails(project_id, bot_name)
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
