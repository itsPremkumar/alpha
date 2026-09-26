"""Child-environment construction for the script bridge.

Design rule, learned the hard way upstream: **never pass variables through by
prefix**.  A prefix passthrough (``HERMES_*`` / ``ALPHA_*``) leaks every future
non-secret config knob into arbitrary sandboxed code the moment somebody adds
one.  This module therefore builds the child environment from an
**exact-name allowlist** plus an **explicit per-skill opt-in**, and then applies
a hard substring deny as a second, independent barrier.

Two independent barriers, because each fails differently:

1. ``SAFE_ENV_ALLOWLIST`` (exact names) - the default set.  Anything not named
   here simply does not exist in the child, whether or not it looks safe.
2. ``SECRET_NAME_SUBSTRINGS`` - names containing ``KEY``, ``TOKEN``,
   ``SECRET``, ``PASSWORD``, ``CREDENTIAL``, ``PASSWD`` or ``AUTH`` are refused
   *even when explicitly opted in*.  An opt-in can therefore never reintroduce a
   credential by accident; that requires a deliberate code change here.

``os.environ`` is never iterated to build the result, so an operator adding
``AGENT_WORKSPACE_SOMETHING`` to the host environment can never widen what a
sandboxed script sees.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping

#: Exact names passed through by default.  Deliberately tiny and boring: enough
#: for CPython to start, resolve files, and print.  No product namespace.
SAFE_ENV_ALLOWLIST: frozenset[str] = frozenset(
    {
        # Process/interpreter essentials
        "PATH",
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONIOENCODING",
        "PYTHONHASHSEED",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONUNBUFFERED",
        "VIRTUAL_ENV",
        # Locale / encoding
        "LANG",
        "LANGUAGE",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
        # Windows
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE",
        # POSIX
        "HOME",
        "SHELL",
        "TMPDIR",
        "USER",
        "LOGNAME",
        "PWD",
        "OLDPWD",
    }
)

#: Substrings that make a name un-injectable.  Checked against the
#: upper-cased name.  This is the barrier an explicit opt-in cannot cross.
SECRET_NAME_SUBSTRINGS: tuple[str, ...] = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "AUTH",
)

#: Variables the bridge itself sets on every child.  Not operator-tunable.
_BRIDGE_SET: tuple[str, ...] = (
    "PYTHONIOENCODING",
    "PYTHONUNBUFFERED",
    "PYTHONDONTWRITEBYTECODE",
    "ALPHA_SCRIPT_BRIDGE",
    "ALPHA_SCRIPT_BRIDGE_SOCKET",
    "ALPHA_SCRIPT_BRIDGE_TOKEN",
    "ALPHA_SCRIPT_BRIDGE_TRANSPORT",
    "ALPHA_SCRIPT_BRIDGE_STUB_SHA",
    "ALPHA_SCRIPT_BRIDGE_MODE",
    "ALPHA_SCRIPT_BRIDGE_KERNEL",
)


class EnvironmentPolicyError(ValueError):
    """A caller asked to inject a variable the policy refuses to inject."""


def is_secret_env_name(name: str) -> bool:
    """Return True when *name* may never be passed to a script child."""
    upper = name.upper()
    return any(token in upper for token in SECRET_NAME_SUBSTRINGS)


def build_child_env(
    source: Mapping[str, str] | None = None,
    *,
    opt_in: Mapping[str, str] | None = None,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build the environment for a script-bridge child process.

    Args:
        source: the mapping to read the safe names from; defaults to
            ``os.environ``.  Only names in :data:`SAFE_ENV_ALLOWLIST` are read.
        opt_in: explicit per-skill opt-in, applied *after* the allowlist.  A
            name here is still rejected when it looks like a credential, so an
            opt-in can never smuggle a secret in.
        extra: bridge-internal variables (socket path, mode, ...).  These are
            applied last and are exempt from the secret check because the bridge
            itself sets them and their values are addresses/tokens it minted.

    Returns:
        A fresh ``dict`` that is the child's complete environment.

    Raises:
        EnvironmentPolicyError: an opt-in name looks like a credential, or an
            opt-in name is not a valid POSIX environment name.
    """
    base_source: Mapping[str, str] = os.environ if source is None else source
    env: dict[str, str] = {}
    for name in SAFE_ENV_ALLOWLIST:
        value = base_source.get(name)
        if value is not None:
            env[name] = value

    for name, value in (opt_in or {}).items():
        _validate_env_name(name)
        if is_secret_env_name(name):
            raise EnvironmentPolicyError(f"refusing to inject '{name}': the name contains a credential token ({', '.join(SECRET_NAME_SUBSTRINGS)}). An explicit opt-in cannot override the secret barrier; use a vault handle instead.")
        env[name] = str(value)

    for name in _BRIDGE_SET:
        if name in (extra or {}):
            env[name] = str((extra or {})[name])

    if os.name == "nt":  # pragma: no cover - Windows shape
        # Windows has no PWD and several stdlib paths consult these.
        env.setdefault("SYSTEMROOT", base_source.get("SYSTEMROOT", ""))
        env.setdefault("TEMP", base_source.get("TEMP", base_source.get("TMP", "")))
        env.setdefault("TMP", base_source.get("TMP", base_source.get("TEMP", "")))
    else:
        env.setdefault("TMPDIR", base_source.get("TMPDIR", "/tmp"))

    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONHASHSEED"] = env.get("PYTHONHASHSEED", "0")
    return env


def _validate_env_name(name: str) -> None:
    if not name or "=" in name or "\0" in name:
        raise EnvironmentPolicyError(f"invalid environment variable name: {name!r}")
    if not (name[0].isalpha() or name[0] == "_"):
        raise EnvironmentPolicyError(f"invalid environment variable name: {name!r}")
    if not all(ch.isalnum() or ch == "_" for ch in name):
        raise EnvironmentPolicyError(f"invalid environment variable name: {name!r}")


def child_python_executable() -> str:
    """The interpreter used for script-bridge children.

    Defaults to the running interpreter so the child sees the same site-packages
    as the Gateway.  ``strict`` mode additionally pins ``-I`` semantics via the
    command-line flags the runner supplies, which is what makes a strict-mode
    execution reproducible.
    """
    return sys.executable or "python"
