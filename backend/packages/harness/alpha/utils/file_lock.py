"""Cross-process file lock for coordinating shared stores across workers.

The lease store, trigger store, and quarantine store all use ``os.replace``
for atomic writes, which prevents *torn* writes but not *lost updates*: two
processes can read the same state, each apply their own mutation, and the
second ``os.replace`` silently discards the first process's change.

``FileLock`` provides mutual exclusion across processes on the same filesystem.
It uses ``fcntl.flock`` on POSIX and ``msvcrt.locking`` on Windows, both
operating on a dedicated ``.lock`` sidecar file (never the data file itself,
which is replaced atomically and would invalidate any file descriptor).

The lock is **advisory**, not mandatory: it protects only code paths that
acquire it. It is **not** a distributed lock — it works on one filesystem
shared by cooperating processes, not across hosts without a shared filesystem.

A lock acquisition that times out raises ``FileLockTimeout`` with the real
elapsed time — never a silent fallback that pretends the lock was acquired.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from types import TracebackType


class FileLockTimeout(TimeoutError):
    """Raised when a FileLock cannot be acquired within its timeout."""


class FileLock:
    """A cross-process advisory file lock.

    Use as a context manager::

        with FileLock(path, timeout=10.0):
            # read-modify-write the shared store here
            ...

    The lock is re-entrant **within one process** (same ``FileLock`` instance)
    but not across instances — two ``FileLock`` objects on the same path in
    one process will deadlock on the second acquisition.  This is deliberate:
    it makes an accidental nested acquisition a loud failure rather than a
    silent no-op.
    """

    def __init__(self, path: Path | str, *, timeout: float = 5.0, poll_interval: float = 0.05) -> None:
        self._path = Path(path)
        self._timeout = max(0.0, float(timeout))
        self._poll = max(0.001, float(poll_interval))
        self._fd: int | None = None
        self._acquired_at: float | None = None

    @property
    def path(self) -> Path:
        return self._path

    @property
    def is_held(self) -> bool:
        return self._fd is not None

    def acquire(self) -> None:
        """Acquire the lock, blocking up to ``timeout`` seconds.

        Raises ``FileLockTimeout`` on timeout — never returns silently.
        """
        if self._fd is not None:
            raise RuntimeError(f"FileLock on {self._path} is already held by this instance")

        lock_path = self._path.with_name(self._path.name + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
        deadline = time.monotonic() + self._timeout
        try:
            while True:
                if _try_lock(fd):
                    self._fd = fd
                    self._acquired_at = time.monotonic()
                    return
                if time.monotonic() >= deadline:
                    elapsed = self._timeout
                    os.close(fd)
                    raise FileLockTimeout(f"could not acquire lock {lock_path} within {elapsed:.1f}s — another process holds it")
                time.sleep(self._poll)
        except BaseException:
            if self._fd is None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            raise

    def release(self) -> None:
        """Release the lock. Safe to call when not held."""
        fd = self._fd
        if fd is None:
            return
        self._fd = None
        self._acquired_at = None
        try:
            _unlock(fd)
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    def __enter__(self) -> FileLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.release()


def _try_lock(fd: int) -> bool:
    """Attempt a non-blocking exclusive lock. Returns True on success."""
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    else:
        import fcntl

        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False


def _unlock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        import fcntl

        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
