"""Cross-process file locks with disclosed degradation.

Lock protocol
-------------
A store names a stable sibling lock file for every document, for example
``events.json.lock``.  The lock file is never the document itself: replacing a
document changes its inode, so a lock on the document inode would not exclude
another process.  Writers request an **exclusive** lock and hold it across the
whole read/modify/write/persist sequence.  Readers request a **shared** lock
where the platform supports it (``flock(LOCK_SH)`` on POSIX).  Windows'
``msvcrt.locking`` primitive exposes only an exclusive byte-range lock, so a
reader is upgraded to an exclusive lock and says so in the result; a caller
that explicitly requires shared semantics receives ``unavailable`` instead of
silently weaker protection.  The Windows descriptor is opened with
``CreateFileW`` sharing flags so a competing waiter can open the same lock file
and observe contention rather than receiving a misleading sharing violation;
this mirrors the byte-range locking model documented by Microsoft's CRT
``locking``/``LockFile`` APIs, not a claim of Windows shared-lock support.

The API is deliberately fail-closed.  ``FileLock.acquire()`` returns a
:class:`LockResult` whose ``status`` is one of ``acquired``, ``timeout``,
``unavailable``, or ``failed``.  A caller must check ``acquired`` (or
``protected``) before treating a read or write as coordinated.  There is no
best-effort fallback that reports success when the OS refused a lock.

Deadlock avoidance is a protocol rule, not a clever scheduler: when one
operation needs several scopes, acquire their lock files in the deterministic
order returned by :func:`ordered_lock_paths` (a lexical sort of their absolute
paths).  :func:`ordered_locks` implements that rule and releases everything it
acquired if any member cannot be acquired.  Never hold a document lock while
acquiring an unrelated lock in the reverse order.

Locks are released in ``finally`` blocks, including when a store callback
raises.  The lock file itself is intentionally left on disk; unlinking a lock
file races with a waiter that has already opened the old inode.  Stale lock
metadata is advisory and is used only to explain a timeout.  A lock is
considered stale when its owner process is demonstrably gone (and the age
threshold has passed), or when an ownerless file is older than the threshold.
The live OS lock is still the source of truth: the implementation never
assumes it may forcibly break a live owner's lock.
"""

from __future__ import annotations

import errno
import json
import os
import socket
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

try:  # pragma: no cover - the branch is selected by the host OS
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

try:  # pragma: no cover - the branch is selected by the host OS
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None  # type: ignore[assignment]

__all__ = [
    "FileLock",
    "LockBackendInfo",
    "LockMode",
    "LockResult",
    "LockStatus",
    "StaleLockInfo",
    "file_lock_path",
    "inspect_lock",
    "lock_backend",
    "lock_order_key",
    "ordered_lock_paths",
    "ordered_locks",
    "pid_alive",
]


class LockMode(StrEnum):
    """Requested lock semantics."""

    EXCLUSIVE = "exclusive"
    SHARED = "shared"
    #: Resolve to shared when supported and exclusive (with disclosure) on
    #: Windows.  This is the recommended read mode for cross-platform stores.
    READ = "read"


class LockStatus(StrEnum):
    """Stable machine-readable lock outcomes."""

    ACQUIRED = "acquired"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    RELEASED = "released"
    ALREADY_RELEASED = "already_released"


@dataclass(frozen=True, slots=True)
class LockBackendInfo:
    """Capabilities of the host's file-locking implementation."""

    name: str
    available: bool
    shared_supported: bool
    reason: str = ""


@dataclass(frozen=True, slots=True)
class LockResult:
    """Disclosed result of acquire/release; never implies unverified safety."""

    status: str
    path: Path
    requested_mode: str
    effective_mode: str
    backend: str
    attempts: int = 0
    waited_seconds: float = 0.0
    reason: str = ""
    owner: dict[str, Any] | None = None
    stale_detected: bool = False

    @property
    def acquired(self) -> bool:
        return self.status == LockStatus.ACQUIRED.value

    @property
    def protected(self) -> bool:
        """True only when the OS confirmed the requested coordination."""

        return self.acquired

    @property
    def unavailable(self) -> bool:
        return self.status == LockStatus.UNAVAILABLE.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "path": str(self.path),
            "requested_mode": self.requested_mode,
            "effective_mode": self.effective_mode,
            "backend": self.backend,
            "attempts": self.attempts,
            "waited_seconds": self.waited_seconds,
            "reason": self.reason,
            "owner": dict(self.owner) if self.owner is not None else None,
            "stale_detected": self.stale_detected,
            "acquired": self.acquired,
        }


@dataclass(frozen=True, slots=True)
class StaleLockInfo:
    """Advisory explanation of why a lock file looks abandoned."""

    stale: bool
    age_seconds: float
    owner: dict[str, Any] | None
    reason: str


def file_lock_path(document_path: Path | str) -> Path:
    """Return the stable sibling lock path for a document path."""

    path = Path(document_path)
    return path.with_name(f"{path.name}.lock")


def lock_backend() -> LockBackendInfo:
    """Describe the real file-lock backend available on this process."""

    if os.name == "nt" and msvcrt is not None:
        return LockBackendInfo(
            name="msvcrt",
            available=True,
            shared_supported=False,
            reason="msvcrt byte-range locking has no portable shared mode",
        )
    if os.name == "posix" and fcntl is not None:
        return LockBackendInfo(name="fcntl", available=True, shared_supported=True)
    return LockBackendInfo(
        name="none",
        available=False,
        shared_supported=False,
        reason="neither fcntl nor msvcrt is available on this platform",
    )


def _pid_alive_posix(pid: int) -> bool | None:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:  # pragma: no cover - unusual platform errno
        if exc.errno == errno.ESRCH:
            return False
        return None
    return True


def _pid_alive_windows(pid: int) -> bool | None:  # pragma: no cover - exercised on Windows
    if pid <= 0:
        return False
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        process_query_limited_information = 0x1000
        handle = kernel32.OpenProcess(process_query_limited_information, False, int(pid))
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def pid_alive(pid: int) -> bool | None:
    """Return whether ``pid`` is alive, or ``None`` when the host cannot tell."""

    if os.name == "nt":
        return _pid_alive_windows(pid)
    return _pid_alive_posix(pid)


def _read_owner(path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return dict(value) if isinstance(value, dict) else None


def _age(path: Path, now: float) -> float:
    try:
        return max(0.0, now - path.stat().st_mtime)
    except OSError:
        return 0.0


def inspect_lock(
    path: Path | str,
    *,
    stale_after_seconds: float = 300.0,
    now: float | None = None,
) -> StaleLockInfo:
    """Inspect lock metadata without acquiring or changing the lock.

    The check is intentionally conservative for a foreign host: a PID from
    another machine cannot be probed, so it is reported as not stale rather
    than guessed away.  A same-host dead owner or an ownerless old file is
    stale after the configured threshold.
    """

    lock_path = Path(path)
    moment = time.time() if now is None else float(now)
    owner = _read_owner(lock_path)
    age = _age(lock_path, moment)
    if not lock_path.exists():
        return StaleLockInfo(False, age, owner, "lock_file_absent")
    if stale_after_seconds <= 0 or age < stale_after_seconds:
        return StaleLockInfo(False, age, owner, "below_stale_threshold")
    if owner is None:
        return StaleLockInfo(True, age, owner, "ownerless_lock_file")
    owner_host = str(owner.get("host", ""))
    current_host = socket.gethostname()
    if owner_host and owner_host != current_host:
        return StaleLockInfo(False, age, owner, "foreign_host_owner_not_probeable")
    raw_pid = owner.get("pid")
    try:
        pid = int(raw_pid)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return StaleLockInfo(True, age, owner, "owner_pid_missing_or_invalid")
    alive = pid_alive(pid)
    if alive is False:
        return StaleLockInfo(True, age, owner, "owner_process_is_gone")
    if alive is None:
        return StaleLockInfo(False, age, owner, "owner_liveness_unknown")
    return StaleLockInfo(False, age, owner, "owner_process_alive")


def _canonical_path(path: Path | str) -> str:
    return str(Path(path).expanduser().resolve(strict=False))


def lock_order_key(path: Path | str) -> str:
    """Return the documented total order key for a lock path."""

    return _canonical_path(path)


def ordered_lock_paths(paths: Iterable[Path | str]) -> tuple[Path, ...]:
    """Return lock paths in the deadlock-avoiding lexical order."""

    return tuple(sorted((Path(path) for path in paths), key=lock_order_key))


def _try_lock(descriptor: int, mode: str) -> bool:
    if os.name == "nt" and msvcrt is not None:
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            # LK_NBLCK is the only portable Windows mode exposed by msvcrt.
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            contention = {errno.EACCES, errno.EAGAIN, errno.EDEADLK}
            deadlk = getattr(errno, "WSAEDEADLK", None)
            if deadlk is not None:
                contention.add(deadlk)
            if exc.errno in contention:
                return False
            raise
        return True
    if os.name == "posix" and fcntl is not None:
        operation = fcntl.LOCK_SH if mode == LockMode.SHARED.value else fcntl.LOCK_EX
        try:
            fcntl.flock(descriptor, operation | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            if isinstance(exc, BlockingIOError) or getattr(exc, "errno", None) in {errno.EACCES, errno.EAGAIN}:
                return False
            raise
        return True
    raise OSError(errno.ENOSYS, "file locking is unavailable on this platform")


def _unlock(descriptor: int, mode: str) -> None:
    if os.name == "nt" and msvcrt is not None:
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        return
    if os.name == "posix" and fcntl is not None:
        operation = fcntl.LOCK_UN
        fcntl.flock(descriptor, operation)
        return
    raise OSError(errno.ENOSYS, "file locking is unavailable on this platform")


def _open_lock_descriptor(path: Path) -> int:
    """Open a lock file with sharing that permits competing waiters on Windows.

    The CRT's ``os.open`` mode can deny a second opener while the first process
    holds a byte-range lock.  A native ``CreateFileW`` handle explicitly shares
    read/write/delete access, so a waiter can open the same file and observe
    contention through ``msvcrt.locking`` instead of receiving a misleading
    open failure.  POSIX keeps the ordinary ``os.open`` path.
    """

    if os.name != "nt":
        return os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:  # pragma: no cover - Windows-only branch
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        create_file.restype = wintypes.HANDLE
        handle = create_file(
            str(path),
            0x80000000 | 0x40000000,  # GENERIC_READ | GENERIC_WRITE
            0x00000001 | 0x00000002 | 0x00000004,  # FILE_SHARE_READ|WRITE|DELETE
            None,
            4,  # OPEN_ALWAYS
            0x00000080,  # FILE_ATTRIBUTE_NORMAL
            None,
        )
        invalid = wintypes.HANDLE(-1).value
        if handle == invalid or handle is None:
            raise OSError(ctypes.get_last_error(), "CreateFileW failed for lock file")
        try:
            return msvcrt.open_osfhandle(int(handle), os.O_RDWR | os.O_BINARY)  # type: ignore[union-attr]
        except OSError:
            kernel32.CloseHandle(handle)
            raise
    except ImportError:  # pragma: no cover - unusual Windows runtime
        return os.open(path, os.O_RDWR | os.O_CREAT, 0o600)


def _write_owner(descriptor: int, owner: dict[str, Any]) -> None:
    body = json.dumps(owner, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    os.lseek(descriptor, 0, os.SEEK_SET)
    os.ftruncate(descriptor, 0)
    # Windows requires a byte to exist before a byte-range lock is released;
    # the JSON body also guarantees that the file is non-empty.
    view = memoryview(body)
    while view:
        written = os.write(descriptor, view)
        view = view[written:]
    os.fsync(descriptor)


class FileLock:
    """A bounded, always-released, cross-process lock for one sibling path.

    ``clock`` and ``sleep`` are injectable so timeout and stale-lock tests are
    deterministic.  They default to monotonic time and a short poll interval;
    no operation waits indefinitely.
    """

    def __init__(
        self,
        path: Path | str,
        *,
        mode: LockMode | str = LockMode.EXCLUSIVE,
        timeout: float = 2.0,
        stale_after_seconds: float = 300.0,
        poll_interval_seconds: float = 0.01,
        enabled: bool = True,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        owner: dict[str, Any] | None = None,
    ) -> None:
        requested = LockMode(mode)
        if requested not in set(LockMode):
            raise ValueError(f"unsupported lock mode: {mode!r}")
        if timeout < 0:
            raise ValueError("timeout must be non-negative")
        if stale_after_seconds < 0:
            raise ValueError("stale_after_seconds must be non-negative")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        self.path = Path(path)
        self.mode = requested
        self.timeout = float(timeout)
        self.stale_after_seconds = float(stale_after_seconds)
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.enabled = bool(enabled)
        self._clock = clock
        self._sleep = sleep
        self._owner_override = dict(owner) if owner is not None else None
        self._descriptor: int | None = None
        self._acquired = False
        self._result: LockResult | None = None
        self._effective_mode = requested.value

    @property
    def acquired(self) -> bool:
        return self._acquired

    @property
    def protected(self) -> bool:
        return self._acquired

    @property
    def locked(self) -> bool:
        """Readable alias for :attr:`acquired`."""

        return self._acquired

    @property
    def result(self) -> LockResult | None:
        return self._result

    def _owner(self) -> dict[str, Any]:
        if self._owner_override is not None:
            return dict(self._owner_override)
        return {
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "started_at": time.time(),
            "mode": self._effective_mode,
        }

    def _unavailable(self, reason: str, *, backend: str, effective: str | None = None) -> LockResult:
        result = LockResult(
            status=LockStatus.UNAVAILABLE.value,
            path=self.path,
            requested_mode=self.mode.value,
            effective_mode=effective or self.mode.value,
            backend=backend,
            reason=reason,
        )
        self._result = result
        return result

    def acquire(self) -> LockResult:
        """Acquire the lock, returning a disclosed result instead of guessing."""

        if self._acquired:
            return self._result or LockResult(
                status=LockStatus.ACQUIRED.value,
                path=self.path,
                requested_mode=self.mode.value,
                effective_mode=self._effective_mode,
                backend=lock_backend().name,
            )
        if not self.enabled:
            return self._unavailable("locking_disabled", backend="disabled")
        backend = lock_backend()
        if not backend.available:
            return self._unavailable(backend.reason, backend=backend.name)
        if self.mode is LockMode.SHARED and not backend.shared_supported:
            return self._unavailable(
                "shared_locks_unsupported",
                backend=backend.name,
                effective=LockMode.EXCLUSIVE.value,
            )
        if self.mode is LockMode.READ and not backend.shared_supported:
            self._effective_mode = LockMode.EXCLUSIVE.value
            disclosure = "read_upgraded_to_exclusive_shared_unsupported"
        else:
            self._effective_mode = self.mode.value
            disclosure = ""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = _open_lock_descriptor(self.path)
            if os.fstat(descriptor).st_size == 0:
                # msvcrt.locking requires a byte to exist in the range.  The
                # byte is metadata-free padding; owner JSON replaces it once
                # the lock is held.
                os.write(descriptor, b"\0")
        except OSError as exc:
            try:
                os.close(descriptor)
            except (NameError, OSError):
                pass
            result = LockResult(
                status=LockStatus.UNAVAILABLE.value,
                path=self.path,
                requested_mode=self.mode.value,
                effective_mode=self._effective_mode,
                backend=backend.name,
                reason=f"lock_open_failed: {exc}",
            )
            self._result = result
            return result

        started = self._clock()
        attempts = 0
        stale_detected = False
        owner: dict[str, Any] | None = None
        if self.path.exists():
            initial_info = inspect_lock(self.path, stale_after_seconds=self.stale_after_seconds)
            stale_detected = initial_info.stale
            owner = initial_info.owner
        try:
            while True:
                attempts += 1
                try:
                    locked = _try_lock(descriptor, self._effective_mode)
                except OSError as exc:
                    result = LockResult(
                        status=LockStatus.FAILED.value,
                        path=self.path,
                        requested_mode=self.mode.value,
                        effective_mode=self._effective_mode,
                        backend=backend.name,
                        attempts=attempts,
                        waited_seconds=max(0.0, self._clock() - started),
                        reason=f"lock_operation_failed: {exc}",
                    )
                    self._result = result
                    return result
                if locked:
                    self._descriptor = descriptor
                    self._acquired = True
                    owner = self._owner()
                    if self.mode is LockMode.READ:
                        # A *read-only* acquisition must not write, whatever the
                        # platform made us take.  On Windows a READ lock is
                        # upgraded to EXCLUSIVE because the CRT has no shared
                        # byte-range lock, but it is still a read: it has no
                        # owner to record, and truncating the owner record would
                        # destroy a live writer's metadata - the very thing that
                        # makes a stale-lock reclaim safe.  The lock itself is
                        # unchanged: it is still taken, and contention is still
                        # observed.  The upgrade stays visible in ``reason``.
                        if stale_detected:
                            disclosure = f"{disclosure}; stale_owner_reclaimed".strip("; ")
                    else:
                        try:
                            _write_owner(descriptor, owner)
                        except OSError as exc:
                            # The OS lock is held, but metadata is not a
                            # correctness prerequisite.  Disclose the degradation
                            # while keeping the acquired lock safe.
                            disclosure = f"{disclosure}; owner_metadata_write_failed: {exc}".strip("; ")
                    if stale_detected:
                        disclosure = f"{disclosure}; stale_owner_reclaimed".strip("; ")
                    result = LockResult(
                        status=LockStatus.ACQUIRED.value,
                        path=self.path,
                        requested_mode=self.mode.value,
                        effective_mode=self._effective_mode,
                        backend=backend.name,
                        attempts=attempts,
                        waited_seconds=max(0.0, self._clock() - started),
                        reason=disclosure,
                        owner=owner,
                        stale_detected=stale_detected,
                    )
                    self._result = result
                    return result

                info = inspect_lock(self.path, stale_after_seconds=self.stale_after_seconds)
                if info.stale:
                    stale_detected = True
                    owner = info.owner
                    # The kernel releases a dead owner's flock/byte-range
                    # lock.  Retrying is therefore both safe and sufficient;
                    # never forcibly unlock a live owner's descriptor.
                elapsed = max(0.0, self._clock() - started)
                if elapsed >= self.timeout:
                    result = LockResult(
                        status=LockStatus.TIMEOUT.value,
                        path=self.path,
                        requested_mode=self.mode.value,
                        effective_mode=self._effective_mode,
                        backend=backend.name,
                        attempts=attempts,
                        waited_seconds=elapsed,
                        reason="stale_owner_detected" if stale_detected else "lock_timeout",
                        owner=owner,
                        stale_detected=stale_detected,
                    )
                    self._result = result
                    return result
                remaining = self.timeout - elapsed
                self._sleep(min(self.poll_interval_seconds, remaining))
        finally:
            if not self._acquired:
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    def release(self) -> LockResult:
        """Release the lock exactly once, including on exceptional paths."""

        if not self._acquired or self._descriptor is None:
            result = LockResult(
                status=LockStatus.ALREADY_RELEASED.value,
                path=self.path,
                requested_mode=self.mode.value,
                effective_mode=self._effective_mode,
                backend=lock_backend().name,
            )
            self._result = result
            return result
        descriptor = self._descriptor
        error = ""
        try:
            _unlock(descriptor, self._effective_mode)
        except OSError as exc:
            error = f"unlock_failed: {exc}"
        finally:
            try:
                os.close(descriptor)
            except OSError as exc:
                error = error or f"close_failed: {exc}"
            self._descriptor = None
            self._acquired = False
        result = LockResult(
            status=LockStatus.RELEASED.value,
            path=self.path,
            requested_mode=self.mode.value,
            effective_mode=self._effective_mode,
            backend=lock_backend().name,
            reason=error,
        )
        self._result = result
        return result

    def __enter__(self) -> FileLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        self.release()
        return False

    def __del__(self) -> None:  # pragma: no cover - interpreter shutdown safety
        try:
            self.release()
        except Exception:
            pass


@contextmanager
def ordered_locks(
    paths: Sequence[Path | str],
    *,
    mode: LockMode | str = LockMode.EXCLUSIVE,
    timeout: float = 2.0,
    stale_after_seconds: float = 300.0,
    poll_interval_seconds: float = 0.01,
    enabled: bool = True,
) -> Iterator[tuple[FileLock, ...]]:
    """Acquire several locks in the documented order and always release them.

    The context manager yields only after every member is acquired.  If any
    member times out or is unavailable, previously acquired members are
    released and the failure is raised as :class:`TimeoutError` or
    :class:`RuntimeError`; callers that need a result object can use
    :class:`FileLock` directly.
    """

    locks = tuple(
        FileLock(
            path,
            mode=mode,
            timeout=timeout,
            stale_after_seconds=stale_after_seconds,
            poll_interval_seconds=poll_interval_seconds,
            enabled=enabled,
        )
        for path in ordered_lock_paths(paths)
    )
    acquired: list[FileLock] = []
    try:
        for lock in locks:
            result = lock.acquire()
            if not result.acquired:
                raise TimeoutError(f"could not acquire ordered lock {lock.path}: {result.status}/{result.reason}")
            acquired.append(lock)
        yield acquired
    finally:
        for lock in reversed(acquired):
            lock.release()
