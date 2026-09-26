"""The vault ledger: which handle, which operation, which target, when.

Append-only JSONL with fsync, mirroring the shape of
``alpha.ledger.ActionLedger`` (owner / what / when, arguments as a digest, never
in the clear).  A vault *use* is recorded whether it succeeded, was denied, or
raised - a denied attempt is exactly the row an operator wants.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class VaultLedgerEntry:
    """One row.  Contains a handle fingerprint, never a handle id and never a secret."""

    handle_fingerprint: str
    operation: str
    target: str
    owner: str
    outcome: str  # "succeeded" | "denied" | "error"
    at: float = field(default_factory=time.time)
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class VaultLedger:
    """Append-only, fsync'd record of every handle use."""

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self._lock = threading.Lock()
        self._memory: list[VaultLedgerEntry] = []
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        *,
        handle_fingerprint: str,
        operation: str,
        target: str,
        owner: str,
        outcome: str,
        detail: str = "",
    ) -> VaultLedgerEntry:
        entry = VaultLedgerEntry(
            handle_fingerprint=handle_fingerprint,
            operation=operation,
            target=target,
            owner=owner,
            outcome=outcome,
            detail=detail,
        )
        with self._lock:
            self._memory.append(entry)
            if self.path is not None:
                line = json.dumps(entry.to_dict(), ensure_ascii=False, sort_keys=True)
                with open(self.path, "a", encoding="utf-8", newline="\n") as handle:
                    handle.write(line + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
        return entry

    def entries(self) -> list[VaultLedgerEntry]:
        with self._lock:
            return list(self._memory)

    def read_back(self) -> list[dict[str, Any]]:
        """Read the persisted ledger.  Used by tests and by the operator view."""
        if self.path is None or not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows
