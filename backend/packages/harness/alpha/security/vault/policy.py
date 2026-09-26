"""Vault scope: one operation, one target, time-bounded.

A vault entry is not a key you look up.  It is a *permission* to perform one
named operation against one named target before a deadline.  Anything wider -
"any operation", "any target", "no expiry" - is refused at deposit time rather
than at use time, so a too-broad entry never exists to be misused.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

#: Longest lifetime a vault entry may be given.
MAX_TTL_SECONDS = 3600.0
#: Shortest lifetime.  A "permanent" credential is a configuration mistake, not
#: a feature; the operator re-deposits when they want a new window.
MIN_TTL_SECONDS = 30.0

#: Operation names are a closed set, not free text.
ALLOWED_OPERATIONS: frozenset[str] = frozenset(
    {
        "http_request",
        "http_basic_auth",
        "subprocess_env",
        "api_query",
        "sign_request",
        "totp_challenge",
        "session_login",
    }
)


@dataclass(frozen=True)
class VaultScope:
    """The least-privilege grant a vault entry carries."""

    operation: str
    target: str
    ttl_seconds: float = 300.0

    def __post_init__(self) -> None:
        if self.operation not in ALLOWED_OPERATIONS:
            raise ValueError(f"unsupported vault operation {self.operation!r}; allowed: {sorted(ALLOWED_OPERATIONS)}")
        if not self.target or not self.target.strip():
            raise ValueError("a vault scope must name exactly one target")
        if self.target.strip() != self.target:
            raise ValueError("a vault target must not carry leading or trailing whitespace")
        # A wildcard target is "any target", which is the exact breadth a vault
        # entry must not have.  Rejected at construction so a too-broad grant
        # never exists to be misused.
        if any(ch in self.target for ch in "*?[]"):
            raise ValueError(f"a vault target must be one literal target, not a pattern: {self.target!r}")
        if not (MIN_TTL_SECONDS <= float(self.ttl_seconds) <= MAX_TTL_SECONDS):
            raise ValueError(f"a vault entry must be time-bounded between {MIN_TTL_SECONDS}s and {MAX_TTL_SECONDS}s; got {self.ttl_seconds}")

    def expires_at(self, *, now: float | None = None) -> float:
        return (time.time() if now is None else now) + float(self.ttl_seconds)

    def permits(self, operation: str, target: str) -> tuple[bool, str]:
        """Least-privilege check.  Exact match on both axes; no wildcards."""
        if operation != self.operation:
            return False, (f"handle authorises operation {self.operation!r}, not {operation!r}")
        if target != self.target:
            return False, f"handle authorises target {self.target!r}, not {target!r}"
        return True, "permitted"

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "target": self.target,
            "ttl_seconds": self.ttl_seconds,
        }
