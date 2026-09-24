"""Resolve a real POSIX shell for tests that execute the repository's ``.sh`` files.

Several suites exercise the actual shell entrypoints (``docker/dev-entrypoint.sh``,
the UV_EXTRAS resolution path, ``pnpm`` wrappers). They used to spawn a bare
``["sh", ...]``, which raises ``FileNotFoundError`` on Windows hosts where Git for
Windows is installed but its ``usr/bin`` directory is **not on PATH** — the shell
exists, the test simply could not find it, and the whole module failed.

This helper resolves a shell honestly:

1. ``sh`` / ``bash`` on PATH (the normal POSIX path),
2. the standard Git-for-Windows install locations,
3. otherwise the caller is **skipped with a clear reason** — never a silent
   success and never a fake pass.

Prefer a real shell over skipping: on this host a shell IS available, so the
suites keep their full coverage instead of losing ~27 assertions on Windows.
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_WINDOWS_CANDIDATES = (
    r"C:\Program Files\Git\usr\bin\sh.exe",
    r"C:\Program Files\Git\bin\bash.exe",
    r"C:\Program Files (x86)\Git\usr\bin\sh.exe",
    r"C:\Program Files (x86)\Git\bin\bash.exe",
    r"C:\msys64\usr\bin\sh.exe",
    r"C:\cygwin64\bin\bash.exe",
)


def _works(candidate: str) -> bool:
    """Probe a candidate: it must really run a trivial command.

    Existence is not enough on Windows: ``C:\\Windows\\system32\\bash.EXE`` is
    the WSL launcher, which not only fails without a WSL distribution — it can
    HANG waiting for one, so it is excluded by path before probing (and every
    other candidate gets a bounded probe).
    """
    normalized = os.path.normcase(os.path.abspath(candidate))
    windows_dir = os.path.normcase(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32"))
    if normalized == os.path.normcase(os.path.join(windows_dir, "bash.exe")):
        return False
    try:
        completed = subprocess.run(
            [candidate, "-c", "exit 0"],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


@functools.lru_cache(maxsize=1)
def posix_shell() -> str | None:
    """Return a POSIX shell that really works on this host, else ``None``."""
    candidates: list[str] = []
    for name in ("sh", "bash"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        candidates.append(str(Path(local_appdata) / "Programs" / "Git" / "usr" / "bin" / "sh.exe"))
    candidates.extend(_WINDOWS_CANDIDATES)
    for candidate in candidates:
        if _works(candidate):
            return candidate
    return None


def posix_shell_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment where the resolved shell's own tools (``python3``, ``git``) resolve.

    Git for Windows ships a POSIX ``usr/bin`` that is normally absent from the
    Windows PATH. Shell scripts executed by the tests legitimately call those
    names, so when we resolve a Git shell we also expose its bin directories to
    the child process instead of failing with "command not found".
    """
    env = dict(base if base is not None else os.environ)
    shell = posix_shell()
    if shell:
        shell_dir = str(Path(shell).parent)
        extra = [shell_dir]
        # Git's usr/bin and bin are siblings; add both when we are using one.
        parent = Path(shell_dir).parent
        for sibling in ("usr/bin", "bin"):
            candidate = parent / sibling
            if candidate.is_dir() and str(candidate) not in extra:
                extra.append(str(candidate))
        current = env.get("PATH", "")
        env["PATH"] = os.pathsep.join([*extra, current]) if current else os.pathsep.join(extra)
    # The .sh entrypoints resolve optional dependencies through python/python3,
    # and Git Bash ships neither. Expose the interpreter that is running this
    # test so a "host has python but no python3" scenario is reproducible
    # instead of failing on a missing interpreter.
    interpreter_dir = str(Path(sys.executable).parent)
    if interpreter_dir:
        current = env.get("PATH", "")
        env["PATH"] = os.pathsep.join([interpreter_dir, current]) if current else interpreter_dir
    return env


def sh_argv(*args: str) -> list[str]:
    """Command prefix for running ``args`` under a real POSIX shell.

    Skips the calling test (with a precise reason) when the host genuinely has
    no working POSIX shell, instead of failing with an opaque WinError 2 or a
    WSL-launcher error.
    """
    shell = posix_shell()
    if shell is None:
        pytest.skip(
            "no working POSIX shell available on this host - install Git for Windows "
            "(usr/bin/sh.exe) or put sh on PATH to run the .sh entrypoint tests"
        )
    return [shell, *args]
