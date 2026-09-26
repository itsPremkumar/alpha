"""Optional (opt-in) capabilities: catalogue + lazy loader, and the shared
capability-tag / task-eligibility registry.

See :mod:`alpha.capabilities.catalog` for the declared subsystems and
:mod:`alpha.capabilities.registry` for the loader and status reporting.
:mod:`alpha.capabilities.eligibility` holds the ONE capability-tag vocabulary
and the strict-coverage eligibility rule shared by dispatch, reassignment and
auction bidding.
"""

from __future__ import annotations

from alpha.capabilities.catalog import CAPABILITY_CATALOG, CapabilitySpec, capability_ids
from alpha.capabilities.eligibility import (
    ASSIGNABLE_BOT_STATUSES,
    MAX_REQUIRED_TAGS,
    CapabilityMatch,
    EligibilityResult,
    eligible_candidates,
    match_capabilities,
    normalize_tag,
    normalize_tags,
    profile_capability_tags,
    required_capability_tags,
)
from alpha.capabilities.registry import load, load_enabled_capabilities, resolve, status

__all__ = [
    "ASSIGNABLE_BOT_STATUSES",
    "CAPABILITY_CATALOG",
    "CapabilityMatch",
    "CapabilitySpec",
    "EligibilityResult",
    "MAX_REQUIRED_TAGS",
    "capability_ids",
    "eligible_candidates",
    "load",
    "load_enabled_capabilities",
    "match_capabilities",
    "normalize_tag",
    "normalize_tags",
    "profile_capability_tags",
    "required_capability_tags",
    "resolve",
    "status",
]
