"""Denial Circuit Breaker preventing infinite token burn on repeated denied commands."""

from __future__ import annotations


class DenialCircuitBreaker:
    """Trips after N consecutive DENY verdicts in a session to prevent runaway retries."""

    def __init__(self, threshold: int = 3):
        self.threshold = threshold
        self.consecutive_denials: int = 0
        self.is_tripped: bool = False
        self.trip_reason: str = ""

    def record_verdict(self, verdict: str, reason: str = "") -> None:
        if verdict == "DENY":
            self.consecutive_denials += 1
            if self.consecutive_denials >= self.threshold:
                self.is_tripped = True
                self.trip_reason = (
                    f"Denial circuit breaker tripped: {self.consecutive_denials} consecutive commands "
                    f"were denied by safety guardian. Halting execution to prevent infinite loop."
                )
        elif verdict == "APPROVE":
            self.consecutive_denials = 0
            self.is_tripped = False
            self.trip_reason = ""

    def reset(self) -> None:
        self.consecutive_denials = 0
        self.is_tripped = False
        self.trip_reason = ""


_global_breaker = DenialCircuitBreaker()


def get_denial_breaker() -> DenialCircuitBreaker:
    return _global_breaker
