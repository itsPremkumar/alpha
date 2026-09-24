"""Department Token Treasury & Fiscal Governance Engine."""

from __future__ import annotations

import logging
import time
from typing import Any

from alpha.enterprise.models import TreasuryAllocation

logger = logging.getLogger(__name__)


class DepartmentTokenTreasury:
    """Manages departmental token budgets, burn rate monitoring, ROI velocity, and circuit breakers."""

    def __init__(self):
        self._allocations: dict[str, TreasuryAllocation] = {}
        self._bootstrap_treasury()

    def _bootstrap_treasury(self) -> None:
        """Sets initial token capital for enterprise departments."""
        defaults = [
            ("dept-engineering", "Core Software Engineering", 600000, 80000.0),
            ("dept-architecture", "Systems Architecture", 350000, 50000.0),
            ("dept-security", "Security Assurance", 300000, 45000.0),
            ("dept-performance", "Performance & Benchmarks", 250000, 40000.0),
            ("dept-documentation", "Documentation & OpenAPI", 200000, 30000.0),
        ]
        for dept_id, name, budget, threshold in defaults:
            self._allocations[dept_id] = TreasuryAllocation(
                dept_id=dept_id,
                department_name=name,
                allocated_tokens=budget,
                spent_tokens=0,
                balance_tokens=budget,
                burn_rate_tpm=0.0,
                roi_velocity=1.2,
                circuit_breaker_threshold_tpm=threshold,
            )

    def allocate_tokens(self, dept_id: str, additional_tokens: int) -> TreasuryAllocation:
        """Increases or redistributes token capital for a department."""
        if dept_id not in self._allocations:
            self._allocations[dept_id] = TreasuryAllocation(
                dept_id=dept_id,
                department_name=dept_id,
                allocated_tokens=additional_tokens,
                balance_tokens=additional_tokens,
            )
        else:
            alloc = self._allocations[dept_id]
            alloc.allocated_tokens += additional_tokens
            alloc.balance_tokens += additional_tokens
            if alloc.balance_tokens > 0.05 * alloc.allocated_tokens and alloc.burn_rate_tpm < alloc.circuit_breaker_threshold_tpm:
                alloc.circuit_breaker_active = False
            alloc.last_updated = time.time()
        return self._allocations[dept_id]

    def record_token_burn(
        self,
        dept_id: str,
        tokens_burned: int,
        tasks_completed: int = 0,
    ) -> tuple[TreasuryAllocation, bool]:
        """Deducts tokens, calculates burn rate TPM, updates ROI velocity, and triggers circuit breaker if violated.

        Returns:
            (TreasuryAllocation, circuit_breaker_triggered: bool)
        """
        alloc = self._allocations.get(dept_id)
        if not alloc:
            alloc = self.allocate_tokens(dept_id, 200000)

        # Update spent and balance
        alloc.spent_tokens += tokens_burned
        alloc.balance_tokens = max(0, alloc.allocated_tokens - alloc.spent_tokens)

        # Rolling TPM calculation (assuming ~10s per heartbeat cycle, TPM = tokens * 6)
        instant_tpm = float(tokens_burned * 6)
        if instant_tpm > alloc.circuit_breaker_threshold_tpm:
            alloc.burn_rate_tpm = instant_tpm
        else:
            alloc.burn_rate_tpm = round((alloc.burn_rate_tpm * 0.6) + (instant_tpm * 0.4), 1)

        # Track history
        alloc.burn_history.append({
            "timestamp": time.time(),
            "burned": tokens_burned,
            "tpm": alloc.burn_rate_tpm,
            "tasks_completed": tasks_completed,
        })
        if len(alloc.burn_history) > 20:
            alloc.burn_history = alloc.burn_history[-20:]

        # ROI velocity calculation (tasks per 10k tokens consumed).
        # No floor: a low measured ROI is real information about wasted spend
        # and must not be overwritten with a flattering constant.
        if alloc.spent_tokens > 0:
            total_tasks = sum(h.get("tasks_completed", 0) for h in alloc.burn_history)
            alloc.roi_velocity = round((total_tasks / max(1, alloc.spent_tokens)) * 10000.0, 2)

        # Check circuit breaker:
        # Trip if burn rate > threshold OR instant TPM > threshold OR remaining balance <= 5% of allocation
        tripped = False
        if (
            alloc.burn_rate_tpm > alloc.circuit_breaker_threshold_tpm
            or instant_tpm > alloc.circuit_breaker_threshold_tpm
            or alloc.balance_tokens < (0.05 * alloc.allocated_tokens)
        ):
            if not alloc.circuit_breaker_active:
                alloc.circuit_breaker_active = True
                tripped = True
                logger.warning(
                    f"CIRCUIT BREAKER TRIPPED for {dept_id}! "
                    f"Burn rate: {alloc.burn_rate_tpm} TPM (instant: {instant_tpm}, max: {alloc.circuit_breaker_threshold_tpm}), "
                    f"Balance: {alloc.balance_tokens} tokens"
                )

        alloc.last_updated = time.time()
        return alloc, tripped

    def reset_circuit_breaker(
        self,
        dept_id: str,
        new_threshold_tpm: float | None = None,
        top_up_tokens: int | None = None,
    ) -> TreasuryAllocation:
        """Authoritatively resets a tripped circuit breaker, optionally adjusting threshold and replenishing reserve."""
        alloc = self._allocations.get(dept_id)
        if not alloc:
            raise KeyError(f"Department '{dept_id}' not found in treasury.")

        alloc.circuit_breaker_active = False
        if new_threshold_tpm:
            alloc.circuit_breaker_threshold_tpm = new_threshold_tpm
        # Reset burn rate spike
        alloc.burn_rate_tpm = min(alloc.burn_rate_tpm, alloc.circuit_breaker_threshold_tpm * 0.5)

        # If balance is depleted below 5%, replenish reserve so breaker doesn't re-trip immediately
        min_reserve = int(0.15 * alloc.allocated_tokens)
        if top_up_tokens:
            alloc.allocated_tokens += top_up_tokens
            alloc.balance_tokens += top_up_tokens
        elif alloc.balance_tokens < (0.05 * alloc.allocated_tokens):
            replenish = min_reserve - alloc.balance_tokens
            alloc.allocated_tokens += replenish
            alloc.balance_tokens += replenish

        alloc.last_updated = time.time()
        logger.info(f"Circuit breaker manually reset for {dept_id} (balance={alloc.balance_tokens})")
        return alloc

    def can_spend_tokens(self, dept_id: str, requested_tokens: int) -> bool:
        """Checks if a department has remaining budget and is not blocked by circuit breaker."""
        alloc = self._allocations.get(dept_id)
        if not alloc:
            return True
        if alloc.circuit_breaker_active:
            return False
        return alloc.balance_tokens >= requested_tokens

    def get_allocation(self, dept_id: str) -> TreasuryAllocation | None:
        return self._allocations.get(dept_id)

    def list_allocations(self) -> list[TreasuryAllocation]:
        return list(self._allocations.values())

    def get_overall_telemetry(self) -> dict[str, Any]:
        """Constructs fiscal telemetry roll-up for the enterprise dashboard."""
        total_allocated = sum(a.allocated_tokens for a in self._allocations.values())
        total_spent = sum(a.spent_tokens for a in self._allocations.values())
        total_balance = sum(a.balance_tokens for a in self._allocations.values())
        overall_burn_rate = round(sum(a.burn_rate_tpm for a in self._allocations.values()), 1)
        active_breakers = sum(1 for a in self._allocations.values() if a.circuit_breaker_active)
        avg_roi = round(sum(a.roi_velocity for a in self._allocations.values()) / max(1, len(self._allocations)), 2)

        return {
            "total_allocated_tokens": total_allocated,
            "total_spent_tokens": total_spent,
            "total_balance_tokens": total_balance,
            "overall_burn_rate_tpm": overall_burn_rate,
            "active_circuit_breakers_count": active_breakers,
            "average_roi_velocity": avg_roi,
            "departments": [a.model_dump() for a in self._allocations.values()],
        }


_TREASURY: DepartmentTokenTreasury | None = None


def get_department_treasury() -> DepartmentTokenTreasury:
    global _TREASURY
    if _TREASURY is None:
        _TREASURY = DepartmentTokenTreasury()
    return _TREASURY
