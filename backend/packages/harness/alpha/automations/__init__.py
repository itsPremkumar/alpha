"""Scheduled, repeatable tasks (RRULE-style automations).

- :class:`~alpha.automations.automations.AutomationSchedule` — recurrence rule
- :func:`~alpha.automations.automations.next_run` — next-occurrence computation
- :func:`~alpha.automations.automations.to_rrule` — RRULE emission
- :class:`~alpha.automations.automations.AutomationStore` — durable registry
"""

from __future__ import annotations

from alpha.automations.automations import (
    AUTOMATION_STORE_SCHEMA_VERSION,
    Automation,
    AutomationError,
    AutomationSchedule,
    AutomationStore,
    AutomationStoreUnreadable,
    AutomationValidationError,
    Frequency,
    next_run,
    to_rrule,
)

__all__ = [
    "AUTOMATION_STORE_SCHEMA_VERSION",
    "Automation",
    "AutomationError",
    "AutomationSchedule",
    "AutomationStore",
    "AutomationStoreUnreadable",
    "AutomationValidationError",
    "Frequency",
    "next_run",
    "to_rrule",
]
