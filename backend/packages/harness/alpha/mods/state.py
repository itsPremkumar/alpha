"""Shared, kernel-owned mod state — Alpha's ``$.state``.

A Claude Code mod keeps state in its hook's module scope and in ``$.state``,
which survives a hot reload: one hook records a count and a *different* hook
reads it. Alpha's equivalent is a store the **kernel** owns and hands to every
handler of a given mod.

Why the kernel must own it: :class:`~alpha.mods.context.CapabilityContext` is
rebuilt for every dispatched event, so a dict held by a per-context capability
object loses everything it recorded the moment the event ends. Two mods that
each handle a different phase of one run therefore could not pass a value
between them, and a counter reset to zero on the next event. This module is the
store that makes cross-handler, cross-event state real.

Three properties are load-bearing:

- **Namespaced per mod.** ``clear()`` on one mod's namespace must not erase
  another mod's state, so every key is stored under ``<mod_name>:<key>``.
- **Bounded.** An unbounded store is a memory leak in a long-lived Gateway. Key
  count and per-value size are both capped, and an over-bound write is *refused*
  with the bound named rather than silently truncated — a truncated value is a
  corrupted value nobody can detect.
- **A read of an absent key returns the default, never an error.** Mods poll
  state that a later handler may not have written yet; that is the normal shape
  of a pipeline, not a fault.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

#: Default maximum number of keys held by a single mod.
DEFAULT_MAX_KEYS = 512

#: Default maximum serialized size, in bytes, of a single value.
DEFAULT_MAX_VALUE_BYTES = 64 * 1024


class StateQuotaError(RuntimeError):
    """A mod tried to store more state than its bounds allow."""


def _bounded_size(value: Any) -> int:
    """Best-effort measurement of a value's serialized footprint.

    Measurement is deliberately approximate and capped: hashing or fully
    serializing an arbitrary object to police a 64 KiB bound would cost more
    than the value is worth. JSON is tried first because mod state is near
    always JSON-shaped; anything else falls back to ``repr``.
    """
    try:
        return len(json.dumps(value, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        return min(len(repr(value)), DEFAULT_MAX_VALUE_BYTES)


class ModStateStore:
    """Process-wide, mod-namespaced key/value state shared across handlers.

    The store is thread-safe. It is **process-local**: it survives a hot
    re-registration of a mod inside one process, and it does not survive a
    Gateway restart or reach a second worker. Callers that need durability must
    write to a durable store themselves; claiming otherwise here would resume
    the exact drift this repo keeps rejecting.
    """

    def __init__(
        self,
        *,
        max_keys: int = DEFAULT_MAX_KEYS,
        max_value_bytes: int = DEFAULT_MAX_VALUE_BYTES,
    ) -> None:
        self._max_keys = max(1, int(max_keys))
        self._max_value_bytes = max(1, int(max_value_bytes))
        self._values: dict[str, Any] = {}
        self._declared_reads: dict[str, set[str]] = {}
        self._declared_writes: dict[str, set[str]] = {}
        self._observed_reads: dict[str, set[str]] = {}
        self._observed_writes: dict[str, set[str]] = {}
        self._lock = threading.RLock()

    # -- namespacing -------------------------------------------------------

    @staticmethod
    def _key(mod_name: str, key: str) -> str:
        return f"{mod_name}\x00{key}"

    def _prefix(self, mod_name: str) -> str:
        return f"{mod_name}\x00"

    # -- value access ------------------------------------------------------

    def get(self, mod_name: str, key: str, default: Any = None) -> Any:
        with self._lock:
            self._observed_reads.setdefault(mod_name, set()).add(key)
            return self._values.get(self._key(mod_name, key), default)

    def set(self, mod_name: str, key: str, value: Any) -> None:
        size = _bounded_size(value)
        if size > self._max_value_bytes:
            raise StateQuotaError(f"Mod '{mod_name}' state key '{key}' is {size} bytes, over the {self._max_value_bytes}-byte bound; refusing to store a truncated value.")
        full = self._key(mod_name, key)
        with self._lock:
            if full not in self._values and self.count(mod_name) >= self._max_keys:
                raise StateQuotaError(f"Mod '{mod_name}' already holds {self._max_keys} state keys; refusing an unbounded store.")
            self._values[full] = value
            self._observed_writes.setdefault(mod_name, set()).add(key)

    def delete(self, mod_name: str, key: str) -> bool:
        with self._lock:
            return self._values.pop(self._key(mod_name, key), None) is not None

    def keys(self, mod_name: str) -> list[str]:
        prefix = self._prefix(mod_name)
        with self._lock:
            return sorted(k[len(prefix) :] for k in self._values if k.startswith(prefix))

    def count(self, mod_name: str) -> int:
        return len(self.keys(mod_name))

    def clear(self, mod_name: str) -> int:
        prefix = self._prefix(mod_name)
        with self._lock:
            doomed = [k for k in self._values if k.startswith(prefix)]
            for k in doomed:
                self._values.pop(k, None)
            return len(doomed)

    # -- whole-namespace operations ---------------------------------------

    def snapshot(self, mod_name: str | None = None) -> dict[str, Any]:
        """Return a copy of one mod's state, or every mod's state.

        The snapshot is a projection for operators and tests. It is never fed
        back as authoritative: a snapshot taken while a handler is mid-write
        is a point-in-time copy, not a transactional one.
        """
        with self._lock:
            if mod_name is not None:
                return {k: self.get(mod_name, k) for k in self.keys(mod_name)}
            out: dict[str, Any] = {}
            for full_key, value in self._values.items():
                mod, _, key = full_key.partition("\x00")
                out.setdefault(mod, {})[key] = value
            return out

    def restore(self, mod_name: str, data: dict[str, Any]) -> int:
        """Replace one mod's namespace with ``data``; returns keys written."""
        if not isinstance(data, dict):
            raise TypeError("restore() requires a mapping")
        with self._lock:
            self.clear(mod_name)
            written = 0
            for key, value in data.items():
                self.set(mod_name, str(key), value)
                written += 1
            return written

    def mod_names(self) -> list[str]:
        with self._lock:
            return sorted({full_key.partition("\x00")[0] for full_key in self._values})

    def total_keys(self) -> int:
        with self._lock:
            return len(self._values)

    # -- declared / observed key tracking ---------------------------------

    def declare(self, mod_name: str, *, reads: set[str] | None = None, writes: set[str] | None = None) -> None:
        """Record the state keys a mod claims to read and/or write."""
        with self._lock:
            if reads is not None:
                self._declared_reads[mod_name] = set(reads)
            if writes is not None:
                self._declared_writes[mod_name] = set(writes)

    def declaration(self, mod_name: str) -> dict[str, list[str]]:
        with self._lock:
            return {
                "state_reads": sorted(self._declared_reads.get(mod_name, set())),
                "state_writes": sorted(self._declared_writes.get(mod_name, set())),
            }

    def observed(self, mod_name: str) -> dict[str, list[str]]:
        """Keys this mod actually read or wrote in this process.

        This is the honest counterpart to :meth:`declaration`: a declared key
        nobody touched is a claim, and an observed key nobody declared is a
        surprise. Both directions matter to a reviewer, so both are kept.
        """
        with self._lock:
            return {
                "state_reads": sorted(self._observed_reads.get(mod_name, set())),
                "state_writes": sorted(self._observed_writes.get(mod_name, set())),
            }
