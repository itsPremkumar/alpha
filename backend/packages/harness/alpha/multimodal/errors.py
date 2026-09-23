"""Honest exhaustion error for the multimodal capability chain."""

from __future__ import annotations

from typing import Any


class MultimodalUnavailableError(ConnectionError):
    """Every tier of the capability chain was exhausted — nothing served it.

    Carries the full ``attempts`` list (``{tier, engine, error, detail,
    retryable}`` rows) so HTTP callers get a 503 with a real account of what
    was tried. ``ConnectionError`` by design: ``alpha.models.fallback`` treats
    it as retryable, matching the free-router precedent
    (``FreeLLMUnavailableError``).

    Never constructed without attempts from a real run — the chain does not
    fabricate success, and this error never fabricates a cause.
    """

    def __init__(
        self,
        capability: str,
        attempts: list[dict[str, Any]],
        message: str | None = None,
    ) -> None:
        self.capability = str(capability)
        self.attempts = [dict(row) for row in attempts]
        if message is None:
            if self.attempts:
                rendered = ", ".join(
                    f"{row.get('tier', '?')}/{row.get('engine', '?')}={row.get('error', '')}"
                    for row in self.attempts
                )
            else:
                rendered = "(no attempt rows recorded)"
            message = f"No engine could serve capability '{self.capability}'; attempts: {rendered}"
        super().__init__(message)
