"""Process liveness as corroborating evidence.

`RunManager` knows when a run was orphaned, drained, or lost its lease. It cannot
know about the case that matters most to a *peer*: the agent's process is gone
and nothing ever wrote a terminal status, because the write itself is what died.

COORD-Harness derives status from "the claim lease and whether the process holding
it is still alive", and its stated reason is worth repeating here — *"status is
never stored, so nothing can sit at running because a process crashed under
it."* This module supplies the second half of that sentence for Alpha.

## Why liveness orphans a claim but does not declare a crash

Two consumers, two different costs of being wrong:

- **The status display** says "this agent crashed". A false positive here is a
  fabricated death in the operator's UI, and it is the exact error the activity
  ledger exists to remove. So `crashed` still requires a *hard run fact* — a
  named terminal stop reason — and never moves on liveness alone.
- **Claim orphaning** turns held work into *available* work. A false positive
  costs a peer one wasted read of the file before it claims it. The consequence
  is reversible and cheap, so liveness is allowed to trigger it.

That asymmetry is the whole design. Liveness is evidence, not a verdict.

## Pid reuse is the reason this is not a hard fact

Pids are recycled. A dead pid 40 seconds after an agent wrote a claim may be a
recycled pid belonging to something unrelated. Two guards:

1. The claim must also be **stale** — the agent stopped reporting — so a live
  process with a live claim is never orphaned on a recycled pid alone.
2. A pid the ledger has never recorded is `None`, not `False`. "I did not measure"
   is not "measured dead".
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)

#: An agent whose last heartbeat is older than this AND whose pid is gone is
#: treated as gone for orphaning purposes. Long enough that a recycled pid would
#: have to land on the same number twice.
LIVENESS_STALE_SECONDS = 60.0


def pid_alive(pid: int | None) -> bool | None:
    """Is this process alive? `None` when there is nothing to measure.

    Three-valued on purpose. `None` means "no pid was recorded", which is the
    answer for every claim made before this existed; folding that into `False`
    would orphan the entire historical claim set the first time a sweep ran.
    """
    if pid is None or isinstance(pid, bool):
        # `bool` is an `int` subclass, so `int(True)` is 1 — and pid 1 really
        # does exist on Windows. Rejected explicitly so a stray boolean cannot
        # masquerade as "the init system is gone".
        return None
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None

    if os.name == "nt":
        # Windows has no signal-0 probe. A process handle that cannot be opened
        # is gone; ERROR_ACCESS_DENIED means it exists under a different
        # account, so a hard failure here must not read as death.
        return _windows_pid_alive(value)

    try:
        os.kill(value, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Alive, owned by someone else.
        return True
    except OSError:
        return None
    return True


#: `kernel32` is resolved once. Loading a DLL per liveness check turned a
#: cheap tri-state read into the most expensive thing in the read path — this
#: function is called once per agent per activity derivation, and a room
#: snapshot derives every member.
_KERNEL32: Any = None
_KERNEL32_LOCK = threading.Lock()


def _kernel32() -> Any:
    global _KERNEL32
    if _KERNEL32 is not None:
        return _KERNEL32
    with _KERNEL32_LOCK:
        if _KERNEL32 is None:
            try:
                import ctypes
                from ctypes import wintypes

                lib = ctypes.WinDLL("kernel32", use_last_error=True)
                lib.OpenProcess.restype = wintypes.HANDLE
                lib.GetExitCodeProcess.restype = wintypes.BOOL
                lib.CloseHandle.restype = wintypes.BOOL
                _KERNEL32 = (lib, wintypes, ctypes)
            except Exception:
                _KERNEL32 = False
        return _KERNEL32 or None


def _windows_pid_alive(pid: int) -> bool | None:
    resolved = _kernel32()
    if resolved is None:  # pragma: no cover - ctypes is always present on Windows
        return None
    kernel32, wintypes, ctypes = resolved

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259

    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        # 5 == ERROR_ACCESS_DENIED: the process exists, we may simply not look.
        # 87 == ERROR_INVALID_PARAMETER: no such process.
        if error == 5:
            return True
        if error == 87:
            return False
        return None

    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return None
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def holder_is_gone(*, pid: int | None, last_seen_at: float | None, now: float | None = None) -> bool:
    """Should this holder's work be treated as abandoned?

    Requires **both** signals, deliberately:

    - the process is measured gone (`False`, not `None`), and
    - the agent has stopped reporting for at least `LIVENESS_STALE_SECONDS`.

    Either alone is too weak. A stale heartbeat can be a long tool call; a dead
    pid can be a recycled one.
    """
    import time

    moment = time.time() if now is None else now
    alive = pid_alive(pid)
    if alive is not False:
        return False
    if last_seen_at is None:
        return False
    return (moment - last_seen_at) >= LIVENESS_STALE_SECONDS


def process_evidence(pid: int | None, *, now: float | None = None) -> dict[str, Any]:
    """A machine-readable liveness reading, for the evidence block.

    `alive` is tri-state and `reason` names which question was asked, so a
    display can distinguish "measured dead" from "nothing to measure".
    """
    import time

    moment = time.time() if now is None else now
    alive = pid_alive(pid)
    if alive is None:
        reason = "no_pid_recorded"
    elif alive:
        reason = "process_alive"
    else:
        reason = "process_gone"
    return {"alive": alive, "reason": reason, "pid": pid, "observed_at": moment}
