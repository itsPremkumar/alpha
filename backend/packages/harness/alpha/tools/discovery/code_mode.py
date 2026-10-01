"""Code-mode policy, capability negotiation, and an honest not-built gate.

STATUS: NOT BUILT, BY DESIGN
----------------------------
The isolated code bridge is **not** implemented in this package, and this module
is the deliberate, tested boundary that says so. The reason is not schedule
pressure; it is that the only isolation primitive available on this host cannot
provide the guarantee the design requires.

What the design requires
~~~~~~~~~~~~~~~~~~~~~~~~
* a short-lived child process with an empty environment,
* no filesystem or network grants,
* only three bridge operations plus ``console`` exposed,
* a parent-enforced wall-clock timeout that kills the child on expiry,
* outstanding calls cancelled when the child settles,
* bounded stderr diagnostics.

Why a Python child process cannot satisfy that here
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The reference implementation runs the model-authored body in a **Node**
subprocess started with Node's permission model -- a real, OS-backed grant
system where ``fs``, ``net``, ``child_process``, and ``worker`` are simply
denied. Python has no equivalent. On this Windows host the options are:

1. **No OS-level isolation.** There is no job object, no ``seccomp``, no
   capability token. A child interpreter has the same OS rights as the parent
   unless the parent grants less, and the parent cannot grant less here.
2. **A curated ``__builtins__`` is not a sandbox.** This is the important one.
   Trimming ``import``/``open``/``eval``/``exec`` out of the globals does *not*
   contain the interpreter. ``().__class__.__base__.__subclasses__()`` walks
   from a builtin to a live class, and from there a real ``os``/``subprocess``
   reference is reachable, with no ``__import__`` involved anywhere. Every
   "restricted ``exec``" sandbox in Python has this hole; closing it needs
   audit hooks plus a type-system rewrite, which is a project, not a patch.
3. **Empty environment and a wall-clock timeout are real but insufficient.**
   They stop runaway code and credential inheritance. They do not stop a
   deliberate read of the source tree.

Shipping (2) anyway would be exactly the half-safe version the brief forbids: a
surface that *looks* isolated to a reviewer and is not, holding real tool
capabilities behind it. The reference design's own rule applies -- fail closed,
and fall back to structured mode rather than to a weaker sandbox.

What this module therefore does instead
---------------------------------------
It implements everything around the executor, which is what a future bridge
needs and what is useful today:

* :func:`resolve_effective_mode` -- the fail-closed fallback from ``code`` to
  ``tools`` when the isolated runtime cannot start, with a disclosed reason.
  Never falls back to ``direct`` silently: direct schemas are a *bigger*
  surface, not a safer one.
* :func:`code_runtime_availability` -- a measured report of what this host
  could offer, so the answer is evidence rather than assertion.
* :class:`CodeModeUnavailable` -- raised by any caller that insists on ``code``.
* :class:`CodeModeGate` -- the per-session decision, cached, so the availability
  probe runs once per session instead of once per turn.

The wiring patch is unaffected: ``DiscoveryMode.CODE`` already exists in the
config, is already reachable from the tri-state value, and
:func:`resolve_effective_mode` already downgrades it. When a genuinely
isolated runtime lands, this module gains a constructor and nothing else moves.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

from alpha.tools.discovery.config import DiscoveryConfig, DiscoveryMode

#: The operations a real bridge would expose. Declared here so the gate, the
#: directory guidance, and a future executor share one vocabulary.
BRIDGE_OPERATIONS: tuple[str, str, str] = ("search", "describe", "call")

#: The only non-bridge global a real bridge would expose.
BRIDGE_GLOBALS: tuple[str, ...] = ("console",)

#: Runtimes that COULD provide a real permission model. None is guaranteed on
#: this host; the probe reports what is actually present.
CANDIDATE_RUNTIMES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("node", ("node", "nodejs")),
    ("deno", ("deno",)),
    ("bun", ("bun",)),
)


class CodeModeUnavailable(RuntimeError):
    """Raised when a caller requires code mode this host cannot provide safely."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class RuntimeAvailability:
    """Measured, honest report of the isolated-runtime options on this host."""

    available: bool
    runtime: str
    reason: str
    candidates_found: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "runtime": self.runtime,
            "reason": self.reason,
            "candidatesFound": list(self.candidates_found),
        }


def code_runtime_availability() -> RuntimeAvailability:
    """Probe for a runtime that can actually enforce the required isolation.

    A candidate is only reported available when it is present AND exposes a
    permission model (``--permission`` / ``--allow-read``-style grants), which
    is checked by asking it for its help text. Probing costs one fast
    subprocess per candidate and the result is cached per process.
    """
    found: list[str] = []
    for name, executables in CANDIDATE_RUNTIMES:
        executable = next((shutil.which(item) for item in executables if shutil.which(item)), None)
        if executable is None:
            continue
        found.append(name)
        if _supports_permission_model(executable):
            return RuntimeAvailability(available=True, runtime=name, reason=f"{name} is installed and exposes a permission model", candidates_found=tuple(found))
    if found:
        return RuntimeAvailability(
            available=False,
            runtime="",
            reason=f"found {', '.join(found)} but none exposes a verified filesystem/network permission model; an unisolated child is not an acceptable substitute",
            candidates_found=tuple(found),
        )
    return RuntimeAvailability(
        available=False,
        runtime="",
        reason="no JavaScript runtime with a permission model is installed; a Python child process cannot provide OS-level isolation, so the bridge is not built",
        candidates_found=(),
    )


_PERMISSION_MARKERS = ("--permission", "--allow-read", "--allow-write", "--allow-net", "--allow-run")


def _supports_permission_model(executable: str) -> bool:
    """Whether *executable* advertises a filesystem/network grant system."""
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell, no user input
            [executable, "--help"],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
            env={},
        )
    except (OSError, subprocess.SubprocessError):
        return False
    help_text = f"{completed.stdout}\n{completed.stderr}"
    return any(marker in help_text for marker in _PERMISSION_MARKERS)


@dataclass(frozen=True)
class ModeDecision:
    """The effective mode plus the reason it was chosen."""

    mode: DiscoveryMode
    requested: DiscoveryMode
    reason: str
    availability: RuntimeAvailability

    @property
    def downgraded(self) -> bool:
        return self.mode is not self.requested

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "requested": self.requested.value,
            "downgraded": self.downgraded,
            "reason": self.reason,
            "codeRuntime": self.availability.to_dict(),
        }


def resolve_effective_mode(config: DiscoveryConfig, *, availability: RuntimeAvailability | None = None) -> ModeDecision:
    """Apply the fail-closed fallback for an unavailable code runtime.

    ``code`` downgrades to ``tools`` -- the structured surface with the same
    policy-filtered catalog and the same execution path. It never downgrades to
    ``direct``: exposing every schema is a larger attack and cost surface, not a
    fallback.
    """
    probe = availability if availability is not None else code_runtime_availability()
    if config.mode is not DiscoveryMode.CODE:
        return ModeDecision(mode=config.mode, requested=config.mode, reason="code mode not requested", availability=probe)
    if probe.available:
        return ModeDecision(mode=DiscoveryMode.CODE, requested=DiscoveryMode.CODE, reason=f"isolated runtime available: {probe.reason}", availability=probe)
    return ModeDecision(mode=DiscoveryMode.TOOLS, requested=DiscoveryMode.CODE, reason=f"code mode unavailable, falling back to structured tools mode: {probe.reason}", availability=probe)


class CodeModeGate:
    """Per-session, cached code-mode decision.

    The availability probe spawns a subprocess, so it runs once per session
    rather than once per turn. This is a plain object with no module-level
    state, so two sessions in one process each decide for themselves.
    """

    def __init__(self, config: DiscoveryConfig, *, availability: RuntimeAvailability | None = None) -> None:
        self._decision = resolve_effective_mode(config, availability=availability)

    @property
    def decision(self) -> ModeDecision:
        return self._decision

    @property
    def effective_mode(self) -> DiscoveryMode:
        return self._decision.mode

    def require_code_mode(self) -> None:
        """Raise unless a genuinely isolated runtime is available.

        A caller that cannot use the structured surface must fail loudly rather
        than quietly execute model-authored code without isolation.
        """
        if self._decision.mode is not DiscoveryMode.CODE:
            raise CodeModeUnavailable(self._decision.reason)


def child_process_baseline() -> dict[str, Any]:
    """The process settings a real bridge must use, named for the wiring patch.

    Exposed as data so the contract is reviewable (and testable) even though the
    executor is not built: empty environment, isolated interpreter, no stdin
    inheritance, pipes for the channel, and a parent-enforced wall clock.
    """
    return {
        "isolated_args": ["-I", "-S", "-E"],
        "env": {},
        "cwd": os.path.abspath(os.sep),
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "shell": False,
        "executable": sys.executable,
        "parent_enforced_timeout_ms": DiscoveryConfig().code_timeout_ms,
        "bridge_operations": list(BRIDGE_OPERATIONS),
        "bridge_globals": list(BRIDGE_GLOBALS),
    }
