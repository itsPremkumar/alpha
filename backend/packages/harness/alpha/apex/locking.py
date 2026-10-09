"""Local filesystem locks used by APEX's durable JSON state stores."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_CROSS_PROCESS_LOCKS: dict[str, threading.Lock] = {}
_CROSS_PROCESS_LOCKS_GUARD = threading.Lock()


@contextmanager
def cross_process_file_lock(target: Path) -> Iterator[None]:
    """Serialize a local JSON/JSONL read-modify-write cycle across workers."""
    target = Path(target).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.parent / f".{target.name}.lock"
    lock_key = str(lock_path)
    with _CROSS_PROCESS_LOCKS_GUARD:
        local_lock = _CROSS_PROCESS_LOCKS.setdefault(lock_key, threading.Lock())
    with local_lock, lock_path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
