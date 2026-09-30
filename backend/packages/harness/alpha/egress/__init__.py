"""Egress routing policy + browser-profile references (operate any website).

- :class:`~alpha.egress.policy.EgressPolicy` — domain -> route rules
- :class:`~alpha.egress.policy.BrowserProfileRef` — a reference to an
  authenticated Chrome profile (never a secret store)
- :class:`~alpha.egress.policy.EgressStore` — durable policy + profile registry
"""

from __future__ import annotations

from alpha.egress.policy import (
    BrowserProfileRef,
    EGRESS_STORE_SCHEMA_VERSION,
    EgressError,
    EgressPolicy,
    EgressRoute,
    EgressRule,
    EgressStore,
    EgressStoreUnreadable,
    EgressValidationError,
    import_profile,
)

__all__ = [
    "BrowserProfileRef",
    "EGRESS_STORE_SCHEMA_VERSION",
    "EgressError",
    "EgressPolicy",
    "EgressRoute",
    "EgressRule",
    "EgressStore",
    "EgressStoreUnreadable",
    "EgressValidationError",
    "import_profile",
]
