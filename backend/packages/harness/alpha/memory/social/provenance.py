"""Append-only per-day provenance for social-memory security events.

Interactions, grants, revocations, grant expiries, and identity merges record
who acted, what scope was affected, when it happened, and why. Content bodies
are intentionally excluded so the audit trail does not become a second,
less-protected copy of shared facts.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from .models import normalize_scope
from .paths import provenance_path

SocialEventType = Literal["interaction", "grant", "revocation", "grant_expiry", "merge"]


class SocialProvenanceError(RuntimeError):
    """Raised when a required audit append cannot be made durable."""


class SocialProvenance:
    """Process-local append lock over durable per-owner JSONL files."""

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)
        self._lock = threading.Lock()

    @staticmethod
    def _day(now: float) -> str:
        return datetime.fromtimestamp(now, tz=UTC).strftime("%Y-%m-%d")

    def append(
        self,
        event_type: SocialEventType,
        *,
        owner_scope: str,
        actor: str,
        target: str,
        reason: str,
        details: dict[str, Any] | None = None,
        now: float | None = None,
    ) -> Path:
        """Append and fsync one event, raising if its audit cannot be trusted."""

        at = time.time() if now is None else float(now)
        scope = normalize_scope(owner_scope)
        payload = {
            "event_type": event_type,
            "occurred_at": datetime.fromtimestamp(at, tz=UTC).isoformat(),
            "owner_scope": scope,
            "actor": normalize_scope(actor),
            "target": str(target or "").strip(),
            "reason": str(reason or "").strip(),
            "details": dict(details or {}),
        }
        if not payload["target"] or not payload["reason"]:
            raise ValueError("social provenance requires target and reason")
        try:
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise SocialProvenanceError(f"social provenance details are not JSON serializable: {exc}") from exc

        path = provenance_path(self._root, scope, self._day(at))
        with self._lock:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(encoded + "\n")
                    handle.flush()
            except OSError as exc:
                raise SocialProvenanceError(f"could not append social provenance to {path}: {exc}") from exc
        return path

    def read_entries(self, day: str, *, owner_scope: str) -> list[dict[str, Any]]:
        """Read valid event dictionaries; malformed JSONL lines are disclosed by omission."""

        path = provenance_path(self._root, owner_scope, day)
        if not path.exists():
            return []
        entries: list[dict[str, Any]] = []
        with self._lock:
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError as exc:
                raise SocialProvenanceError(f"could not read social provenance {path}: {exc}") from exc
        for raw in lines:
            if not raw.strip():
                continue
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                entries.append(parsed)
        return entries


__all__ = ["SocialEventType", "SocialProvenance", "SocialProvenanceError"]
