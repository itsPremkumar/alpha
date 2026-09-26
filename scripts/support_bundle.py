#!/usr/bin/env python3
"""Create a redacted Alpha diagnostic bundle for community troubleshooting.

One command, the whole picture
------------------------------
The bundle is the answer to "what happened on this machine", and the evidence a
maintainer needs to act on it is only useful together. It now collects, in one
zip:

* the **logs** -- redacted tails of every launcher log, plus a level histogram so
  a triage can see at a glance that the gateway was emitting WARNING+ while the
  reporter saw nothing;
* the **run trace** -- spans and events, rendered through
  :mod:`scripts.export_run_trace` so the timeline uses that module's total order
  and truncation disclosure rather than a second renderer;
* the **config** and **extensions config** summaries, the **git** state, the
  **environment/toolchain versions**, and the optional **doctor** output.

Before this, the logs lived in one script and the trace in another, nothing
called the trace renderer, and neither was reachable from ``make support-bundle``
-- so a reporter could not produce a complete picture with one command.

Redaction
---------
This file historically carried its **own** regex redactor, which made it a third
independent implementation alongside the trace-side
:class:`alpha.observability.redaction.Redactor` and the ``Redactor`` the log
chokepoint now uses. The local patterns are kept -- they are what lets this
script run in an environment where the backend venv is broken, which is exactly
when a support bundle is most valuable -- but they are no longer the *only*
thing between a secret and the zip.

The composition point is :func:`redact_evidence_text`: every piece of **newly
collected evidence** (log tails, the rendered run trace) goes through the local
patterns and then the shared ``Redactor``, so a credential family the local
patterns do not know about is still scrubbed.
:func:`redact_text` itself is left byte-for-byte unchanged, because its output is
pinned by ``tests/test_support_bundle.py`` and because changing it would silently
alter every config/git/doctor redaction in every existing workflow for no gain in
the cases those paths actually see. The chaining is best-effort by design; it can
only ever redact more, never less.

Bound and disclosed
-------------------
Log tails are line- and byte-bounded and *say so* when anything was dropped,
because a bundle that silently contains the first 500 lines of a 200,000-line
log is worse than no bundle: it looks complete. The trace renderer discloses its
own truncation. Neither boundary is quiet.
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception:  # pragma: no cover - exercised only in broken environments
    yaml = None


#: Directory holding this script; the trace renderer is a sibling and is
#: imported, not reimplemented.
_SCRIPTS_DIR = Path(__file__).resolve().parent


def _load_sibling(name: str) -> Any:
    """Import a sibling script by file path, or return ``None``.

    Path-based rather than a plain ``import`` because ``scripts/`` is not a
    package: adding one would change how every other script in the directory is
    resolved. A missing or broken sibling is an absence the bundle discloses, not
    a crash -- the logs half must still work when the trace half cannot.
    """
    import importlib.util

    path = _SCRIPTS_DIR / f"{name}.py"
    if not path.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location(f"_agent_workspace_{name}", path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception:
        return None
    return module


_export_run_trace = _load_sibling("export_run_trace")


def _harness_redactor() -> Any:
    """Return a shared ``Redactor``, or ``None`` when the harness is unavailable.

    Built lazily and cached: importing the harness on first use keeps a
    venv-broken environment working (the local patterns still run) while a
    healthy one gets the engine's full pattern registry.
    """
    global _HARNESS_REDACTOR
    if _HARNESS_REDACTOR is not _UNSET:
        return _HARNESS_REDACTOR
    redactor = None
    try:
        from alpha.observability.redaction import Redactor

        # Sized for whole log lines rather than one span attribute, matching the
        # log chokepoint: the *patterns* are identical, only the disclosure
        # bound differs, so a log tail is not truncated mid-credential.
        redactor = Redactor(
            "standard", max_value_chars=65536, max_items=4096, max_depth=16
        )
    except Exception:
        redactor = None
    _HARNESS_REDACTOR = redactor
    return redactor


class _Unset:
    __slots__ = ()


_UNSET = _Unset()
_HARNESS_REDACTOR: Any = _UNSET


SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|access[_-]?key|private[_-]?key|(?<![a-zA-Z])key(?![a-zA-Z])"
    r"|token|secret|password|passwd|pwd|(?<![a-zA-Z])pass(?!port)"
    r"|authorization|cookie|credential|(?<![a-zA-Z])dsn(?![a-zA-Z]))",
    re.IGNORECASE,
)
# Bare-word coverage above mirrors env_policy.py's *KEY*/*SECRET*/*TOKEN*/*PASS*/
# *CREDENTIAL*/*DSN* sandbox-env denylist (backend/packages/harness/alpha/sandbox/env_policy.py):
# a fixed keyword allowlist misses a secret stored under an unanticipated key name
# inside an open-ended config dict (e.g. guardrails.provider.config, which is an
# arbitrary dict of provider-specific kwargs). The api_key/access_key/private_key/
# password/passwd/pwd forms predate this and stay for their glued-compound coverage
# (e.g. "apikey" with no separator); the new bare key/pass/dsn alternatives are
# boundary-guarded so they match only their own delimited token and not an
# unrelated word that merely starts with the same letters (keywords, keyboard).
# `pass` only excludes a trailing "port" (passport) rather than any trailing
# letter: excluding any trailing letter also missed genuine secret-bearing
# names like passphrase/passcode, which env_policy.py's *PASS* substring match
# does catch -- `compass`/`bypass` stay excluded via the leading-letter
# lookbehind regardless of the lookahead.
#
# Case-insensitive exact key names that carry a bare credential with no
# distinguishing keyword substring, mirroring env_policy.py's no-flag credential
# sources (GH_PAT/GITHUB_PAT/REDIS_AUTH/REDISCLI_AUTH/PGSERVICEFILE). Matched as a
# full key name, not a substring: a bare "pat"/"auth" wildcard would false-positive
# on unrelated fields (author, authenticated, compatible, pattern, ...). Connection
# strings (DATABASE_URL, REDIS_URL, ...) are deliberately not in this set -- their
# embedded credentials are already stripped in place by URL_USERINFO_RE, which
# preserves the host/port/db-name diagnostic value instead of blanking the field.
NO_FLAG_CREDENTIAL_KEY_NAMES = frozenset(
    {"gh_pat", "github_pat", "redis_auth", "rediscli_auth", "pgservicefile"}
)
ENV_KEY_RE = re.compile(r"(?i)^env$")
VAR_REFERENCE_RE = re.compile(r"^\$\{?[A-Za-z_][A-Za-z0-9_]*\}?$")
ENV_SECRET_RE = re.compile(
    r"(?im)^([A-Z0-9_]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|PASSWD|AUTHORIZATION|COOKIE|CREDENTIAL)[A-Z0-9_]*\s*=\s*)(.+)$"
)
YAML_SECRET_RE = re.compile(
    r"(?im)^(\s*[\w.-]*(?:api[_-]?key|token|secret|password|passwd|authorization|cookie|credential|private[_-]?key)[\w.-]*\s*:\s*)(.+)$"
)
BEARER_RE = re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+")
OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
URL_USERINFO_RE = re.compile(r"([a-zA-Z][\w+.-]*://)([^/?#\s@]+)@")
URL_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&][\w.-]*(?:api[_-]?key|token|secret|password|passwd|authorization|access[_-]?token|credential)[\w.-]*=)([^&\s#]+)"
)
CLI_INLINE_SECRET_RE = re.compile(
    r"(?i)(--?[\w.-]*(?:api[_-]?key|token|secret|password|passwd|authorization|cookie|credential)[\w.-]*=)(\S+)"
)
SECRET_FLAG_RE = re.compile(
    r"(?i)^--?[\w.-]*(?:api[_-]?key|token|secret|password|passwd|authorization|cookie|credential)[\w.-]*$"
)
HEADER_KEY_RE = re.compile(r"(?i)header")
POSIX_HOME_RE = re.compile(r"(?<![\w.-])(/Users|/home)/([^/\s:]+)")
WINDOWS_HOME_RE = re.compile(r"(?i)([A-Z]:\\Users\\)([^\\\s:]+)")
# Must stay byte-identical to alpha.utils.thread_id.THREAD_ID_PATTERN
# (canonical thread ID contract); pinned by a parity test in backend/tests.
# Kept as a local copy because this script must run even in environments
# where the backend venv is broken.
SAFE_THREAD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
DOCTOR_STATUS_RE = re.compile(
    r"Status:\s*(\d+)\s+error\(s\),\s*(\d+)\s+warning\(s\)", re.IGNORECASE
)
ATTENTION_SIGNAL_NAMES = {
    "doctor_failed",
    "config_missing",
    "config_error",
    "models_missing",
    "extensions_config_error",
    "node_missing",
    "node_version_too_old",
    "nginx_missing",
    "dirty_worktree",
}


def _redact_yaml_secret_match(match: re.Match[str]) -> str:
    prefix = match.group(1)
    value = match.group(2)
    if "authorization" in prefix.lower() and value.lstrip().lower().startswith(
        "bearer "
    ):
        return prefix + BEARER_RE.sub(r"\1<redacted>", value)
    return prefix + "<redacted>"


def redact_text(text: str) -> str:
    """Redact common secret patterns from free-form text.

    This is the historical, self-contained scrubber: local regexes only, no
    import of the backend package. It is what keeps the bundle working in an
    environment where the backend venv is broken, which is exactly when a support
    bundle is most valuable.

    It is *not* the strongest scrubber in the repository. The shared
    :class:`alpha.observability.redaction.Redactor` knows more credential families
    than these patterns do, and it is what the trace plane and the log chokepoint
    use. Rather than fold it in here -- which would change every output this
    function has ever produced, including ones pinned by
    `tests/test_support_bundle.py` -- the two are composed at
    :func:`redact_evidence_text`, which the newly collected evidence (log tails
    and the rendered run trace) goes through. Config, git, and doctor output keep
    the exact historical behaviour.
    """
    text = POSIX_HOME_RE.sub(r"\1/<user>", text)
    text = WINDOWS_HOME_RE.sub(r"\1<user>", text)
    text = URL_USERINFO_RE.sub(r"\1<redacted>@", text)
    text = URL_QUERY_SECRET_RE.sub(r"\1<redacted>", text)
    text = CLI_INLINE_SECRET_RE.sub(r"\1<redacted>", text)
    text = ENV_SECRET_RE.sub(r"\1<redacted>", text)
    text = YAML_SECRET_RE.sub(_redact_yaml_secret_match, text)
    text = BEARER_RE.sub(r"\1<redacted>", text)
    return OPENAI_KEY_RE.sub("sk-<redacted>", text)


def redact_evidence_text(text: str) -> str:
    """Redact one piece of collected evidence: local patterns, then the shared engine.

    The ordering matters and is not interchangeable. The local patterns run
    first because they handle things a credential scrubber has no business
    touching -- home-directory paths and structured ``KEY=value`` context -- and
    because they keep working without the backend venv. The shared
    :class:`~alpha.observability.redaction.Redactor` then runs over the result, so
    a credential family these patterns do not know about is still scrubbed and the
    bundle is never a weaker guarantee than the trace plane it explains.

    Chaining can only *add* redactions. The engine's patterns never match its own
    ``[REDACTED:...]`` placeholders, so a second pass over already-scrubbed text
    is a no-op, and when the harness is unavailable the local result is returned
    unchanged rather than raising.
    """
    local = redact_text(text)
    redactor = _harness_redactor()
    if redactor is None:
        return local
    try:
        return str(redactor.redact_value(local).value)
    except Exception:
        return local


def _redact_secret_flag_list(items: list[Any]) -> list[Any]:
    """Mask the value that follows a secret-like CLI flag (e.g. ['--api-key', 'X'])."""
    redacted: list[Any] = []
    mask_next = False
    for item in items:
        if mask_next:
            redacted.append(
                "<redacted>" if isinstance(item, str) else redact_data(item)
            )
            mask_next = False
            continue
        if isinstance(item, str) and SECRET_FLAG_RE.fullmatch(item):
            redacted.append(item)
            mask_next = True
            continue
        redacted.append(redact_data(item))
    return redacted


def _redact_env_value(value: Any) -> Any:
    """Mask env values by default; keep only ``$VAR`` / ``${VAR}`` references visible."""
    if isinstance(value, str) and VAR_REFERENCE_RE.fullmatch(value.strip()):
        return value
    if isinstance(value, (dict, list, tuple)):
        return redact_data(value)
    return "<redacted>"


def redact_data(value: Any) -> Any:
    """Recursively redact secret-like mapping keys while preserving structure."""
    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            if (
                SECRET_KEY_RE.search(key_str)
                or key_str.lower() in NO_FLAG_CREDENTIAL_KEY_NAMES
            ):
                redacted[key] = "<redacted>"
            elif ENV_KEY_RE.fullmatch(key_str) and isinstance(item, dict):
                redacted[key] = {k: _redact_env_value(v) for k, v in item.items()}
            elif HEADER_KEY_RE.search(key_str) and isinstance(item, dict):
                redacted[key] = {k: "<redacted>" for k in item}
            else:
                redacted[key] = redact_data(item)
        return redacted
    if isinstance(value, list):
        return _redact_secret_flag_list(value)
    if isinstance(value, tuple):
        return _redact_secret_flag_list(list(value))
    if isinstance(value, str):
        return redact_text(value)
    return value


def _read_yaml(path: Path) -> Any:
    if not path.exists():
        return {"present": False}
    if yaml is None:
        return {"present": True, "error": "PyYAML is not available"}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        return {"present": True, "error": f"{type(exc).__name__}: {exc}"}


def _read_json(path: Path) -> Any:
    if not path.exists():
        return {"present": False}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"present": True, "error": f"{type(exc).__name__}: {exc}"}


def _run_command(args: list[str], cwd: Path, timeout_s: int = 10) -> dict[str, Any]:
    try:
        result = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": redact_text((result.stdout or "").strip()),
            "stderr": redact_text((result.stderr or "").strip()),
        }
    except FileNotFoundError:
        return {"ok": False, "error": f"{args[0]} not found"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"{args[0]} timed out after {timeout_s}s"}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _version_command(name: str, args: list[str], cwd: Path) -> dict[str, Any]:
    result = _run_command(args, cwd=cwd, timeout_s=5)
    return {"name": name, **result}


def collect_environment(project_root: Path) -> dict[str, Any]:
    """Collect non-secret environment and toolchain metadata."""
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "commands": [
            _version_command("node", ["node", "--version"], project_root),
            _version_command(
                "pnpm",
                [
                    sys.executable,
                    str(project_root / "scripts" / "pnpm.py"),
                    "--version",
                ],
                project_root / "frontend",
            ),
            _version_command("uv", ["uv", "--version"], project_root),
            _version_command("nginx", ["nginx", "-v"], project_root),
            _version_command("docker", ["docker", "--version"], project_root),
        ],
    }


def collect_config_summary(config_path: Path) -> Any:
    return redact_data(_read_yaml(config_path))


def collect_extensions_summary(extensions_config_path: Path) -> Any:
    return redact_data(_read_json(extensions_config_path))


def collect_git_summary(project_root: Path) -> dict[str, Any]:
    """Collect best-effort git metadata without requiring a git checkout."""
    commands = {
        "branch": ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        "head": ["git", "rev-parse", "HEAD"],
        "upstream": [
            "git",
            "rev-parse",
            "--abbrev-ref",
            "--symbolic-full-name",
            "@{u}",
        ],
        "status_short": ["git", "status", "--short", "--branch"],
        "diff_stat": ["git", "diff", "--stat"],
    }
    return {
        name: _run_command(command, cwd=project_root)
        for name, command in commands.items()
    }


def _validate_thread_id(thread_id: str) -> None:
    if (
        not thread_id
        or thread_id in {".", ".."}
        or ".." in thread_id
        or not SAFE_THREAD_ID_RE.fullmatch(thread_id)
    ):
        raise ValueError(f"Invalid thread_id: {thread_id!r}")


def _candidate_thread_data_dirs(project_root: Path, thread_id: str) -> list[Path]:
    _validate_thread_id(thread_id)
    candidates = [
        project_root / ".agent-workspace" / "threads" / thread_id / "user-data",
        project_root
        / "backend"
        / ".agent-workspace"
        / "threads"
        / thread_id
        / "user-data",
    ]
    for base in (
        project_root / ".agent-workspace" / "users",
        project_root / "backend" / ".agent-workspace" / "users",
    ):
        if base.exists():
            candidates.extend(
                user_dir / "threads" / thread_id / "user-data"
                for user_dir in base.iterdir()
                if user_dir.is_dir()
            )
    return candidates


def _display_path(path: Path, project_root: Path) -> str:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except (OSError, ValueError):
        return redact_text(path.as_posix())


def _file_manifest(root: Path, *, max_files: int = 500) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    entries: list[dict[str, Any]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if len(entries) >= max_files:
            entries.append(
                {"path": "<truncated>", "reason": f"file limit {max_files} reached"}
            )
            break
        try:
            stat = path.stat()
        except OSError as exc:
            entries.append(
                {
                    "path": redact_text(path.relative_to(root).as_posix()),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        entries.append(
            {
                "path": redact_text(path.relative_to(root).as_posix()),
                "size_bytes": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
            }
        )
    return entries


def collect_thread_summary(project_root: Path, thread_id: str) -> dict[str, Any]:
    """Collect a thread file manifest without reading user file contents."""
    for data_dir in _candidate_thread_data_dirs(project_root, thread_id):
        if data_dir.exists():
            return {
                "thread_id": thread_id,
                "found": True,
                "layout": _display_path(data_dir, project_root),
                "workspace": _file_manifest(data_dir / "workspace"),
                "uploads": _file_manifest(data_dir / "uploads"),
                "outputs": _file_manifest(data_dir / "outputs"),
            }
    return {
        "thread_id": thread_id,
        "found": False,
        "checked_layouts": [
            _display_path(path, project_root)
            for path in _candidate_thread_data_dirs(project_root, thread_id)
        ],
    }


def collect_doctor_output(project_root: Path) -> dict[str, Any]:
    backend_dir = project_root / "backend"
    cwd = backend_dir if backend_dir.exists() else project_root
    return _run_command(
        [sys.executable, str(project_root / "scripts" / "doctor.py")],
        cwd=cwd,
        timeout_s=60,
    )


# --------------------------------------------------------------------------- #
# Logs
# --------------------------------------------------------------------------- #

#: A log tail has to be big enough to contain the failure and small enough to
#: attach to an issue. 2,000 lines / 512 KiB per file is the default; both are
#: disclosed in ``logs.json`` whenever they bite.
DEFAULT_LOG_TAIL_LINES = 2000
DEFAULT_LOG_TAIL_BYTES = 512 * 1024
#: The launcher log names this repo writes, plus the interactive debug session's
#: log. Matching is by suffix *or* by a rotated-generation suffix: `scripts/
#: rotate_logs.py` names a generation ``gateway.log.1``, which does not end in
#: ``.log``. Excluding generations here would drop the one file most likely to
#: hold the crash that preceded a restart, so they are collected and the collector
#: is what decides not to re-rotate them.
LOG_FILE_SUFFIXES = (".log", ".txt")
LOG_ROTATION_SUFFIX_RE = re.compile(r"^\.\d+$")
#: A level is a bounded, well-known set; anything unrecognised lands in
#: ``UNKNOWN`` rather than being dropped, so a new format cannot hide records
#: from the histogram.
_KNOWN_LOG_LEVELS = (
    "CRITICAL",
    "ERROR",
    "WARNING",
    "INFO",
    "DEBUG",
    "NOTSET",
    "UNKNOWN",
)


def _log_level_of(line: str) -> str:
    """Return the level token in a log *line*, or ``"UNKNOWN"``.

    Line-oriented on purpose. A whole-file regex would attribute a record to the
    level of whatever happened to appear later in the file, which is worse than
    admitting the line is not a level-bearing record.
    """
    match = re.search(
        r"\b(CRITICAL|FATAL|ERROR|WARNING|WARN|INFO|DEBUG|TRACE|NOTSET)\b", line
    )
    if not match:
        return "UNKNOWN"
    token = match.group(1)
    if token == "FATAL":
        return "CRITICAL"
    if token == "WARN":
        return "WARNING"
    if token == "TRACE":
        return "DEBUG"
    return token


def _level_histogram(lines: list[str]) -> dict[str, int]:
    histogram = {level: 0 for level in _KNOWN_LOG_LEVELS}
    for line in lines:
        histogram[_log_level_of(line)] += 1
    return histogram


def _read_log_tail(
    path: Path, *, max_lines: int, max_bytes: int
) -> tuple[list[str], dict[str, Any]]:
    """Return the last *max_lines* lines of *path*, bounded by *max_bytes*.

    Reads the tail by seeking rather than loading the file: a launcher log can be
    gigabytes and a support bundle must not OOM trying to describe it. Decoding
    is ``errors="replace"`` because a log truncated mid-multibyte-character by a
    kill must still produce a usable tail rather than an exception.
    """
    stat = path.stat()
    size = stat.st_size
    with path.open("rb") as handle:
        window = min(size, max_bytes)
        handle.seek(size - window)
        blob = handle.read(window)
    text = blob.decode("utf-8", errors="replace")
    lines = text.splitlines()
    partial_first_line = window < size
    if partial_first_line and lines:
        # The first line in the window is a fragment of a line that started
        # before it. Dropping it keeps every retained line a whole record.
        lines = lines[1:]
    total_lines = len(lines)
    dropped_for_bytes = partial_first_line
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
        dropped_for_bytes = True
    detail = {
        "size_bytes": size,
        "read_bytes": window,
        "lines_read": total_lines,
        "lines_included": len(lines),
        "dropped": bool(dropped_for_bytes and total_lines > len(lines)),
        "truncated": len(lines) < total_lines or dropped_for_bytes,
        "max_lines": max_lines,
        "max_bytes": max_bytes,
    }
    return lines, detail


def is_collected_log(path: Path) -> bool:
    """Whether *path* is a launcher log (or a rotated generation of one) to bundle.

    A generation keeps the base name and adds a numeric suffix, so the check is on
    the part before the first ``.log``/``.txt`` rather than the whole name. A
    rotated generation is included deliberately: the file a rotation preserved is
    usually the one holding the failure that triggered the restart, and a bundle
    that only collected the *current* log would miss exactly the interesting part.
    """
    if not path.is_file():
        return False
    name = path.name.lower()
    for suffix in LOG_FILE_SUFFIXES:
        if name.endswith(suffix):
            return True
        # A rotated generation is `<base><suffix>.<generation>`, so the numeric
        # tail has to sit *after* the base suffix, not replace it.
        marker = name.find(suffix)
        if marker > 0 and LOG_ROTATION_SUFFIX_RE.match(name[marker + len(suffix) :]):
            return True
    return False


def collect_logs(
    project_root: Path,
    *,
    max_lines: int = DEFAULT_LOG_TAIL_LINES,
    max_bytes: int = DEFAULT_LOG_TAIL_BYTES,
) -> list[dict[str, Any]]:
    """Collect redacted tails of every launcher log under *project_root*.

    Returns one entry per log file with its metadata, a level histogram, and the
    redacted tail. The *content* is kept out of this structure and returned as
    ``text`` for the caller to zip separately, so the manifest can describe a
    log without carrying it.
    """
    directories = [project_root / "logs", project_root / "backend" / "logs"]
    collected: list[dict[str, Any]] = []
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if not is_collected_log(path):
                continue
            try:
                lines, detail = _read_log_tail(
                    path, max_lines=max_lines, max_bytes=max_bytes
                )
            except OSError as exc:
                collected.append(
                    {
                        "name": _display_path(path, project_root),
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue
            redacted = [redact_evidence_text(line) for line in lines]
            entry: dict[str, Any] = {
                "name": _display_path(path, project_root),
                "mtime": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
                "levels": _level_histogram(redacted),
                "text": "\n".join(redacted) + ("\n" if redacted else ""),
            }
            entry.update(detail)
            collected.append(entry)
    return collected


def log_error_signals(logs: list[dict[str, Any]]) -> dict[str, Any]:
    """Fold the per-file level histograms into the triage signals.

    ``has_errors``/``has_warnings`` are what turn "the UI showed nothing" into
    "the gateway was already logging ERROR", which is the single most useful
    fact in a bundle where the reporter cannot reproduce the problem.
    """
    errors = 0
    warnings = 0
    for entry in logs:
        levels = entry.get("levels")
        if not isinstance(levels, dict):
            continue
        errors += int(levels.get("CRITICAL", 0)) + int(levels.get("ERROR", 0))
        warnings += int(levels.get("WARNING", 0))
    return {
        "logs_included": bool(logs),
        "log_files": len(logs),
        "log_error_lines": errors,
        "log_warning_lines": warnings,
        "log_tail_truncated": any(bool(entry.get("truncated")) for entry in logs),
    }


# --------------------------------------------------------------------------- #
# Run trace
# --------------------------------------------------------------------------- #


def _trace_candidate_paths(project_root: Path, config_summary: Any) -> list[Path]:
    """Return the trace JSONL files to consider, most-configured first.

    ``observability.file_sink_path`` is an operator config with no default, so
    the configured path is tried first; the bounded glob over the runtime homes
    is the fallback for a deployment that configured it somewhere this script
    cannot resolve. The glob is depth- and count-bounded: a bundle must not walk
    a user's whole data directory.
    """
    candidates: list[Path] = []
    configured = None
    if isinstance(config_summary, dict):
        observability = config_summary.get("observability")
        if isinstance(observability, dict):
            candidate = observability.get("file_sink_path")
            if (
                isinstance(candidate, str)
                and candidate.strip()
                and not candidate.strip().startswith("$")
            ):
                configured = Path(candidate.strip()).expanduser()
    roots = [
        project_root / ".agent-workspace",
        project_root / "backend" / ".agent-workspace",
    ]
    if configured is not None:
        for base in (
            *roots,
            Path(configured).parent if configured.is_absolute() else project_root,
        ):
            resolved = configured if configured.is_absolute() else base / configured
            if resolved not in candidates:
                candidates.append(resolved)
    for base in roots:
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.jsonl")):
            if len(candidates) >= 25:
                break
            if path not in candidates:
                candidates.append(path)
    return candidates


def collect_trace(
    project_root: Path,
    config_summary: Any,
    *,
    run_id: str | None = None,
    max_events: int = 500,
) -> dict[str, Any]:
    """Render the newest readable trace through :mod:`scripts.export_run_trace`.

    Returns a summary plus the rendered ``timeline`` text. When no trace exists
    the reason is recorded rather than raised: "observability is not configured
    on this deployment" is a triage fact, not a bundle failure.
    """
    summary: dict[str, Any] = {
        "available": False,
        "run_id_filter": run_id,
        "max_events": max_events,
    }
    if _export_run_trace is None or not hasattr(_export_run_trace, "render_trace"):
        summary["reason"] = "export_run_trace.py is unavailable next to this script"
        return summary
    attempted: list[str] = []
    # Pick the candidate with the most *matching* records rather than the first
    # readable one. A reporter who passes ``--run-id`` is asking for that run;
    # returning whichever file happened to sort first would hand them an empty
    # timeline and call it "no trace". A candidate with zero matches is only used
    # when no candidate has any match at all, so "the run id is wrong" stays
    # distinguishable from "there is no trace".
    best: tuple[int, Path] | None = None
    for candidate in _trace_candidate_paths(project_root, config_summary):
        if not candidate.is_file():
            attempted.append(f"{_display_path(candidate, project_root)}: absent")
            continue
        try:
            records, _unparsable = _export_run_trace.read_records(candidate)
        except Exception as exc:
            attempted.append(
                f"{_display_path(candidate, project_root)}: {type(exc).__name__}: {exc}"
            )
            continue
        matched = len(_export_run_trace.filter_records(records, run_id=run_id))
        if best is None or matched > best[0]:
            best = (matched, candidate)
        if matched:
            break
    if best is None:
        summary["reason"] = "no readable trace file found"
        summary["candidates_checked"] = attempted[:25]
        return summary
    source = best[1]
    try:
        timeline = _export_run_trace.render_trace(
            source, max_events=max_events, run_id=run_id
        )
    except Exception as exc:
        summary["reason"] = (
            f"could not render {source.name}: {type(exc).__name__}: {exc}"
        )
        summary["candidates_checked"] = attempted[:25]
        return summary
    summary.update(
        {
            "available": True,
            "source": _display_path(source, project_root),
            "size_bytes": source.stat().st_size,
            "mtime": datetime.fromtimestamp(source.stat().st_mtime, UTC).isoformat(),
            "matching_records": best[0],
            "timeline": redact_evidence_text(timeline),
        }
    )
    if attempted:
        summary["candidates_checked"] = attempted[:25]
    return summary


def _command_output(command: dict[str, Any] | None) -> str | None:
    if not command:
        return None
    for key in ("stdout", "stderr", "error"):
        value = command.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _environment_versions(environment: dict[str, Any]) -> dict[str, str | None]:
    platform_info = environment.get("platform", {})
    python_version = (
        platform_info.get("python") if isinstance(platform_info, dict) else None
    )
    versions: dict[str, str | None] = {
        "python": python_version if isinstance(python_version, str) else None
    }
    for command in environment.get("commands", []):
        if isinstance(command, dict) and isinstance(command.get("name"), str):
            versions[command["name"]] = _command_output(command)
    return versions


def _parse_major_version(version_text: str | None) -> int | None:
    if not version_text:
        return None
    match = re.search(r"v?(\d+)(?:\.\d+)?", version_text)
    return int(match.group(1)) if match else None


def _git_stdout(git_summary: dict[str, Any], key: str) -> str | None:
    value = git_summary.get(key)
    return _command_output(value) if isinstance(value, dict) else None


def _doctor_counts(doctor: dict[str, Any] | None) -> tuple[int | None, int | None]:
    if not doctor:
        return (None, None)
    output = "\n".join(
        value
        for value in (
            _command_output(doctor),
            doctor.get("stdout"),
            doctor.get("stderr"),
        )
        if isinstance(value, str)
    )
    match = DOCTOR_STATUS_RE.search(output)
    if not match:
        return (None, None)
    return (int(match.group(1)), int(match.group(2)))


def _enabled_mapping_keys(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    keys: list[str] = []
    for key, item in value.items():
        if isinstance(item, dict) and item.get("enabled") is False:
            continue
        keys.append(str(key))
    return sorted(keys)


def _config_summary(config_summary: Any) -> dict[str, Any]:
    if not isinstance(config_summary, dict):
        return {"present": True, "shape": type(config_summary).__name__}
    present = config_summary.get("present", True)
    if present is False:
        return {"present": False, "models": 0, "tools": [], "channels": []}
    models = config_summary.get("models")
    tools = config_summary.get("tools")
    channels = config_summary.get("channels")
    return {
        "present": True,
        "config_version": config_summary.get("config_version"),
        "error": config_summary.get("error"),
        "models": len(models) if isinstance(models, list) else 0,
        "tools": sorted(
            str(tool.get("name"))
            for tool in tools
            if isinstance(tool, dict) and tool.get("name")
        )
        if isinstance(tools, list)
        else [],
        "channels": _enabled_mapping_keys(channels),
    }


def _extensions_summary(extensions_summary: Any) -> dict[str, Any]:
    if not isinstance(extensions_summary, dict):
        return {"present": True, "shape": type(extensions_summary).__name__}
    present = extensions_summary.get("present", True)
    if present is False:
        return {"present": False, "mcp_servers": [], "skills": []}
    return {
        "present": True,
        "error": extensions_summary.get("error"),
        "mcp_servers": _enabled_mapping_keys(extensions_summary.get("mcpServers")),
        "skills": _enabled_mapping_keys(extensions_summary.get("skills")),
    }


def _dirty_worktree(status_short: str | None) -> bool:
    if not status_short:
        return False
    return any(line and not line.startswith("##") for line in status_short.splitlines())


def _status_from_signals(signals: dict[str, bool]) -> str:
    if (
        signals["config_missing"]
        or signals["config_error"]
        or signals["models_missing"]
        or signals["extensions_config_error"]
    ):
        return "needs_user_setup"
    if (
        signals["node_missing"]
        or signals["node_version_too_old"]
        or signals["nginx_missing"]
    ):
        return "environment_mismatch"
    if not signals["doctor_included"]:
        return "insufficient_evidence"
    if signals["doctor_failed"]:
        return "likely_runtime_issue"
    return "ok"


def _active_signal_names(signals: dict[str, bool]) -> list[str]:
    return [
        name
        for name, enabled in signals.items()
        if enabled and name in ATTENTION_SIGNAL_NAMES
    ]


def _maintainer_next_steps(status: str, signals: dict[str, bool]) -> list[str]:
    steps: list[str] = []
    if status == "needs_user_setup":
        steps.append(
            "Ask the reporter to complete local setup with `make setup`, then rerun `make doctor` and `make support-bundle`."
        )
    if signals["node_missing"] or signals["node_version_too_old"]:
        steps.append(
            "Ask the reporter to install Node.js 22+ before treating this as an application bug."
        )
    if signals["config_missing"] or signals["models_missing"]:
        steps.append(
            "Do not triage model/runtime behavior until `config.yaml` exists and at least one model is configured."
        )
    if signals["config_error"]:
        steps.append(
            "Ask the reporter to fix `config.yaml` syntax or regenerate it with `make setup`."
        )
    if signals["extensions_config_error"]:
        steps.append(
            "Ask the reporter to fix `extensions_config.json` syntax before triaging MCP/skill behavior."
        )
    if signals["doctor_failed"] and status == "likely_runtime_issue":
        steps.append(
            "Use `doctor.json` plus the reproduction steps in the issue body to identify the failing subsystem."
        )
    if signals["thread_summary_included"]:
        steps.append(
            "Use `thread-summary.json` to inspect workspace/upload/output file shape; raw file contents are intentionally absent."
        )
    if signals["logs_included"]:
        steps.append(
            "Use `logs.json` for the per-file level histogram and `logs/` for the redacted tails before asking the reporter for more detail; a non-zero `log_error_lines` is the most direct evidence of what the reporter could not see."
        )
    if signals["trace_included"]:
        steps.append(
            "Use `trace-timeline.txt` for the span/event shape of the newest run, and `--run-id` on this script to scope it to one run."
        )
    elif signals.get("trace_configured_but_missing"):
        steps.append(
            "No readable run trace was found. `observability.sinks: [file]` with `observability.file_sink_path` is the opt-in that produces one; without it spans and events are not persisted anywhere."
        )
    if not steps:
        steps.append(
            "Use the issue reproduction steps and evidence JSON files to continue triage."
        )
    return steps


def _reporter_next_steps(status: str, signals: dict[str, bool]) -> list[str]:
    steps: list[str] = []
    if status == "needs_user_setup":
        steps.append(
            "Run `make setup`, then rerun `make doctor` and `make support-bundle` before filing the issue if the problem changes."
        )
    if signals["node_missing"] or signals["node_version_too_old"]:
        steps.append("Install Node.js 22+ and rerun `make doctor`.")
    if signals["config_missing"] or signals["models_missing"]:
        steps.append(
            "Create or repair `config.yaml` with `make setup`; model/runtime issues cannot be triaged until at least one model is configured."
        )
    if signals["config_error"]:
        steps.append("Fix `config.yaml` syntax or regenerate it with `make setup`.")
    if signals["doctor_failed"] and status == "likely_runtime_issue":
        steps.append(
            "Paste the generated issue summary into the GitHub issue. Attach the zip if a maintainer asks for the evidence bundle."
        )
    if signals["log_error_lines"]:
        steps.append(
            "The bundle already contains WARNING/ERROR log lines, so paste those instead of asking the reporter to reproduce anything again."
        )
    if not steps:
        steps.append(
            "Paste the generated issue summary into the GitHub issue if the issue still reproduces. Attach the zip if a maintainer asks for the evidence bundle."
        )
    return steps


def _evidence_files(
    *,
    include_doctor: bool,
    include_thread_summary: bool,
    include_logs: bool,
    include_trace: bool,
) -> list[dict[str, str]]:
    files = [
        ("README.md", "Human-readable entrypoint for the support bundle."),
        (
            "issue-summary.md",
            "Markdown summary intended to be pasted into a GitHub issue.",
        ),
        (
            "ai-issue-draft.md",
            "GitHub issue draft for AI-assisted filing with required placeholders for unknown user facts.",
        ),
        (
            "triage.json",
            "Stable machine-readable summary for AI or script-assisted triage.",
        ),
        ("manifest.json", "Bundle schema, generation time, and privacy declaration."),
        ("environment.json", "OS, Python, and toolchain version probes."),
        ("config-summary.json", "Redacted config.yaml structure."),
        ("extensions-summary.json", "Redacted extensions_config.json structure."),
        ("git.json", "Branch, commit, upstream, status, and diff-stat metadata."),
    ]
    if include_logs:
        files.append(
            (
                "logs.json",
                "Launcher log inventory: sizes, level histograms, and per-file truncation disclosure.",
            )
        )
        files.append(("logs/", "Redacted log tails, one file per collected log."))
    if include_trace:
        files.append(
            (
                "trace.json",
                "Run-trace inventory: source file, size, and the reason when no trace was found.",
            )
        )
        files.append(
            (
                "trace-timeline.txt",
                "Rendered span/event timeline for the newest readable run trace.",
            )
        )
    if include_thread_summary:
        files.append(
            (
                "thread-summary.json",
                "Optional thread workspace/upload/output file manifests only.",
            )
        )
    if include_doctor:
        files.append(("doctor.json", "Redacted make doctor output."))
    return [{"path": path, "description": description} for path, description in files]


def build_triage_report(
    *,
    manifest: dict[str, Any],
    environment: dict[str, Any],
    config_summary: Any,
    extensions_summary: Any,
    git_summary: dict[str, Any],
    doctor: dict[str, Any] | None,
    thread_summary: dict[str, Any] | None,
    logs: list[dict[str, Any]] | None = None,
    trace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the stable machine-readable summary that maintainers and AI read first."""
    versions = _environment_versions(environment)
    config = _config_summary(config_summary)
    extensions = _extensions_summary(extensions_summary)
    node_major = _parse_major_version(versions.get("node"))
    status_short = _git_stdout(git_summary, "status_short")
    doctor_errors, doctor_warnings = _doctor_counts(doctor)
    log_signals = log_error_signals(logs or [])
    trace = trace or {}
    signals = {
        "doctor_included": doctor is not None,
        "doctor_failed": bool(doctor and not doctor.get("ok")),
        "config_missing": config.get("present") is False,
        "config_error": bool(config.get("error")),
        "models_missing": bool(
            config.get("present") is True and config.get("models") == 0
        ),
        "extensions_config_missing": extensions.get("present") is False,
        "extensions_config_error": bool(extensions.get("error")),
        "node_missing": versions.get("node") is not None
        and "not found" in versions["node"].lower(),
        "node_version_too_old": node_major is not None and node_major < 22,
        "nginx_missing": versions.get("nginx") is not None
        and "not found" in versions["nginx"].lower(),
        "dirty_worktree": _dirty_worktree(status_short),
        "thread_summary_included": thread_summary is not None,
        "thread_summary_found": bool(thread_summary and thread_summary.get("found")),
        "trace_included": bool(trace.get("available")),
        "trace_configured_but_missing": bool(trace)
        and not trace.get("available")
        and "no readable trace file" in str(trace.get("reason") or ""),
    }
    signals.update(log_signals)
    status = _status_from_signals(signals)
    return {
        "schema_version": 1,
        "generated_at": manifest["generated_at"],
        "status": status,
        "active_signals": _active_signal_names(signals),
        "signals": signals,
        "versions": versions,
        "platform": environment.get("platform", {}),
        "config": config,
        "extensions": extensions,
        "git": {
            "branch": _git_stdout(git_summary, "branch"),
            "head": _git_stdout(git_summary, "head"),
            "upstream": _git_stdout(git_summary, "upstream"),
            "dirty_worktree": signals["dirty_worktree"],
        },
        "doctor": {
            "included": doctor is not None,
            "ok": bool(doctor and doctor.get("ok")),
            "returncode": doctor.get("returncode") if doctor else None,
            "errors": doctor_errors,
            "warnings": doctor_warnings,
        },
        "thread": {
            "included": thread_summary is not None,
            "found": bool(thread_summary and thread_summary.get("found")),
        },
        "logs": {
            "included": log_signals["logs_included"],
            "files": log_signals["log_files"],
            "error_lines": log_signals["log_error_lines"],
            "warning_lines": log_signals["log_warning_lines"],
            "tail_truncated": log_signals["log_tail_truncated"],
        },
        "trace": {
            "included": bool(trace.get("available")),
            "source": trace.get("source"),
            "run_id_filter": trace.get("run_id_filter"),
            "reason": trace.get("reason"),
        },
        "reporter_next_steps": _reporter_next_steps(status, signals),
        "maintainer_next_steps": _maintainer_next_steps(status, signals),
        "evidence_files": _evidence_files(
            include_doctor=doctor is not None,
            include_thread_summary=thread_summary is not None,
            include_logs=log_signals["logs_included"],
            include_trace=bool(trace),
        ),
        "privacy": manifest["privacy"],
    }


def _markdown_list(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "- None"


def render_issue_summary(triage: dict[str, Any]) -> str:
    """Render Markdown that users can paste into the GitHub issue body."""
    git = triage["git"]
    doctor = triage["doctor"]
    versions = triage["versions"]
    logs = triage["logs"]
    trace = triage["trace"]
    lines = [
        "## Alpha support bundle summary",
        "",
        f"- Triage status: {triage['status']}",
        f"- Active signals: {', '.join(triage['active_signals']) or 'none'}",
        f"- Doctor: included={doctor['included']}, ok={doctor['ok']}, errors={doctor['errors']}, warnings={doctor['warnings']}",
        f"- Logs: included={logs['included']}, files={logs['files']}, error_lines={logs['error_lines']}, warning_lines={logs['warning_lines']}, tail_truncated={logs['tail_truncated']}",
        f"- Run trace: included={trace['included']}, source={trace['source'] or 'none'}"
        + (f", reason={trace['reason']}" if trace.get("reason") else ""),
        f"- Git: branch={git['branch'] or 'unknown'}, head={git['head'] or 'unknown'}, dirty_worktree={git['dirty_worktree']}",
        f"- Versions: python={versions.get('python') or 'unknown'}, node={versions.get('node') or 'unknown'}, pnpm={versions.get('pnpm') or 'unknown'}, uv={versions.get('uv') or 'unknown'}, nginx={versions.get('nginx') or 'unknown'}",
        "",
        "### Reporter next steps",
        _markdown_list(triage["reporter_next_steps"]),
        "",
        "### Upload guidance",
        "Paste this summary into the GitHub issue. Attach the zip if a maintainer asks for the evidence bundle, or if the summary alone is not enough to diagnose the issue.",
        "",
        "### Maintainer next steps",
        _markdown_list(triage["maintainer_next_steps"]),
        "",
        "### Evidence files in the attached zip",
        _markdown_list(
            [
                f"`{item['path']}` - {item['description']}"
                for item in triage["evidence_files"]
            ]
        ),
        "",
        "Privacy: this bundle excludes `.env`, raw conversation messages, and user file contents. Logs and the run trace are secret-scrubbed but not otherwise summarised, so read them before uploading.",
        "",
    ]
    return "\n".join(lines)


def _os_label(platform_info: dict[str, Any]) -> str:
    system = platform_info.get("system")
    if system == "Darwin":
        return "macOS"
    if system == "Linux":
        return "Linux"
    if system == "Windows":
        return "Windows"
    return "Other"


def _platform_details(platform_info: dict[str, Any]) -> str:
    details = [
        platform_info.get("machine"),
        platform_info.get("system"),
        platform_info.get("release"),
    ]
    return ", ".join(str(item) for item in details if item) or "_No response_"


def _draft_affected_areas(triage: dict[str, Any]) -> list[str]:
    signals = triage["signals"]
    areas: list[str] = []
    if (
        signals["config_missing"]
        or signals["config_error"]
        or signals["models_missing"]
        or signals["node_missing"]
        or signals["node_version_too_old"]
        or signals["nginx_missing"]
    ):
        areas.append("Config / setup (make, config.yaml, env)")
    if signals["extensions_config_error"]:
        areas.extend(["MCP", "Skills"])
    if not areas:
        areas.append("Not sure")
    return areas


def _doctor_excerpt(
    doctor: dict[str, Any] | None, *, max_lines: int = 80, max_chars: int = 12000
) -> str:
    output = _command_output(doctor) if doctor else None
    if not output:
        return "<REQUIRED: paste key log lines. Do not invent if unknown.>"
    output = redact_text(output)
    lines = output.splitlines()
    truncated = False
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        truncated = True
    excerpt = "\n".join(lines)
    if len(excerpt) > max_chars:
        excerpt = excerpt[:max_chars].rstrip()
        truncated = True
    if truncated:
        excerpt += "\n<support bundle doctor output truncated>"
    return excerpt


def render_ai_issue_draft(
    triage: dict[str, Any], issue_summary: str, doctor: dict[str, Any] | None
) -> str:
    """Render a GitHub issue body scaffold for AI-assisted reporters."""
    versions = triage["versions"]
    git = triage["git"]
    platform_info = triage["platform"]
    lines = [
        "# AI issue draft",
        "",
        "Use this when a coding agent or AI assistant files a Alpha bug report.",
        "Do not file this issue until every REQUIRED placeholder is replaced.",
        "Do not invent if unknown; ask the reporter for missing reproduction facts instead.",
        "",
        "## Issue title",
        "",
        "[bug] <REQUIRED: one-line problem summary>",
        "",
        "### Before you start",
        "",
        "- [ ] I searched [existing issues](https://github.com/bytedance/agent-workspace/issues?q=is%3Aissue) and this is not a duplicate.",
        "- [ ] I can reproduce this on the latest `main`.",
        "",
        "### Problem summary",
        "",
        "<!-- REQUIRED: One sentence describing the bug. Do not invent if unknown. -->",
        "<REQUIRED: one sentence problem summary>",
        "",
        "### Affected area(s)",
        "",
        "\n".join(_draft_affected_areas(triage)),
        "<!-- AI hint: derived from support bundle signals; adjust only if the reporter's reproduction proves a better area. -->",
        "",
        "### What happened?",
        "",
        "<!-- REQUIRED: Actual behavior and key error lines. Do not invent if unknown. -->",
        "<REQUIRED: describe what happened>",
        "",
        "### Expected behavior",
        "",
        "<!-- REQUIRED: What should have happened instead. Do not invent if unknown. -->",
        "<REQUIRED: describe expected behavior>",
        "",
        "### Steps to reproduce",
        "",
        "<!-- REQUIRED: Exact commands and sequence. Do not invent if unknown. -->",
        "1. <REQUIRED: first command or action>",
        "2. <REQUIRED: next command or action>",
        "",
        "### Relevant logs",
        "",
        "<!-- Include additional gateway/frontend/sandbox logs if the reporter has them. Keep secrets redacted. -->",
        "```shell",
        _doctor_excerpt(doctor),
        "```",
        "",
        "### How are you running Alpha?",
        "",
        "<REQUIRED: choose Local, Docker, CI, or Other>",
        "",
        "### Operating system",
        "",
        _os_label(platform_info),
        "",
        "### Platform details",
        "",
        _platform_details(platform_info),
        "",
        "### Python version",
        "",
        versions.get("python") or "_No response_",
        "",
        "### Node.js version",
        "",
        versions.get("node") or "_No response_",
        "",
        "### pnpm version",
        "",
        versions.get("pnpm") or "_No response_",
        "",
        "### uv version",
        "",
        versions.get("uv") or "_No response_",
        "",
        "### Git state",
        "",
        f"branch: {git['branch'] or 'unknown'}",
        f"commit: {git['head'] or 'unknown'}",
        f"upstream: {git['upstream'] or 'unknown'}",
        f"dirty_worktree: {git['dirty_worktree']}",
        "",
        "### Support bundle summary",
        "",
        issue_summary.rstrip(),
        "",
        "### Additional context",
        "",
        "Attach the zip only if a maintainer asks for the evidence bundle, or if the summary alone is not enough.",
        "",
    ]
    return "\n".join(lines)


def render_bundle_readme(triage: dict[str, Any]) -> str:
    """Render the support bundle README."""
    lines = [
        "# Alpha Support Bundle",
        "",
        "## Start here",
        "",
        "Paste `issue-summary.md` into the GitHub issue body.",
        "If an AI assistant is filing the issue, start from `ai-issue-draft.md` and replace every REQUIRED placeholder first.",
        "Maintainers or AI triage tools should read `triage.json` first, then inspect the evidence JSON files only as needed.",
        "",
        "## Triage Summary",
        "",
        f"- Status: {triage['status']}",
        f"- Active signals: {', '.join(triage['active_signals']) or 'none'}",
        "",
        "## Reporter next steps",
        "",
        _markdown_list(triage["reporter_next_steps"]),
        "",
        "## Upload guidance",
        "",
        "Paste `issue-summary.md` into the GitHub issue. Attach the zip if a maintainer asks for the evidence bundle, or if the summary alone is not enough to diagnose the issue.",
        "",
        "## Maintainer next steps",
        "",
        _markdown_list(triage["maintainer_next_steps"]),
        "",
        "## Files",
        "",
        _markdown_list(
            [
                f"`{item['path']}` - {item['description']}"
                for item in triage["evidence_files"]
            ]
        ),
        "",
        "## Privacy",
        "",
        "- `.env` is not included.",
        "- Raw conversation messages are not included.",
        "- Thread workspace/upload/output file contents are not included; optional thread data is a file manifest only.",
        "- Launcher log tails and the run trace are included, secret-scrubbed. They are real operational data: read them before uploading.",
        "- Log tails are line- and byte-bounded; `logs.json` and every bundled timeline disclose when something was dropped.",
        "",
    ]
    return "\n".join(lines)


def _default_out_path(project_root: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return (
        project_root
        / ".agent-workspace"
        / "support-bundles"
        / f"agent-workspace-support-bundle-{timestamp}.zip"
    )


def _write_json(zf: zipfile.ZipFile, name: str, data: Any) -> None:
    zf.writestr(f"{name}.json", json.dumps(data, indent=2, sort_keys=True) + "\n")


def _write_text(zf: zipfile.ZipFile, name: str, text: str) -> None:
    zf.writestr(name, text)


def _issue_summary_sidecar_path(out_path: Path) -> Path:
    return out_path.with_name(f"{out_path.stem}-issue-summary.md")


def _issue_draft_sidecar_path(out_path: Path) -> Path:
    return out_path.with_name(f"{out_path.stem}-issue-draft.md")


def _zip_name(name: str) -> str:
    """Return a zip member name for a collected log.

    ``collect_logs`` already produces repo-relative posix paths
    (``logs/gateway.log``, ``backend/logs/gateway.log.1``), so the member keeps
    that shape: a zip whose entries mirror the checkout paths is navigable, and
    the entry in ``logs.json`` names the same thing. Only a leading ``/``, a
    Windows separator, and any ``..`` need neutralising, because this value
    reaches a zip member name -- and a name that is not relative is redacted
    rather than trusted.
    """
    cleaned = name.replace("\\", "/").lstrip("/")
    parts = [part for part in cleaned.split("/") if part not in ("", ".", "..")]
    if not parts:
        return "logs/unnamed.log"
    return "/".join(parts)


def create_support_bundle(
    *,
    project_root: Path,
    out_path: Path | None = None,
    config_path: Path | None = None,
    extensions_config_path: Path | None = None,
    thread_id: str | None = None,
    include_doctor: bool = False,
    include_logs: bool = True,
    include_trace: bool = True,
    log_tail_lines: int = DEFAULT_LOG_TAIL_LINES,
    run_id: str | None = None,
    max_trace_events: int = 500,
) -> Path:
    """Create a redacted diagnostic bundle and return the zip path.

    Logs and the run trace are on by default: the point of this command is that
    one invocation produces the whole picture, and a maintainer who has to ask
    for the logs separately has already lost the reporter. ``include_logs=False``
    / ``include_trace=False`` exist for a reporter who does not want operational
    data in a zip at all.
    """
    project_root = project_root.resolve()
    config_path = (config_path or project_root / "config.yaml").resolve()
    extensions_config_path = (
        extensions_config_path or project_root / "extensions_config.json"
    ).resolve()
    out_path = (out_path or _default_out_path(project_root)).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if thread_id:
        _validate_thread_id(thread_id)

    manifest = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "project": project_root.name,
        "includes": {
            "doctor": include_doctor,
            "thread_summary": thread_id is not None,
            "logs": include_logs,
            "trace": include_trace,
        },
        "privacy": {
            "redacted_secret_fields": True,
            "raw_thread_messages": False,
            "raw_user_files": False,
            "raw_env_file": False,
            "log_tails": include_logs,
            "run_trace": include_trace,
        },
    }

    environment = collect_environment(project_root)
    config_summary = collect_config_summary(config_path)
    extensions_summary = collect_extensions_summary(extensions_config_path)
    git_summary = collect_git_summary(project_root)
    thread_summary = (
        collect_thread_summary(project_root, thread_id) if thread_id else None
    )
    doctor = collect_doctor_output(project_root) if include_doctor else None
    logs = collect_logs(project_root, max_lines=log_tail_lines) if include_logs else []
    trace = (
        collect_trace(
            project_root, config_summary, run_id=run_id, max_events=max_trace_events
        )
        if include_trace
        else {}
    )
    triage = build_triage_report(
        manifest=manifest,
        environment=environment,
        config_summary=config_summary,
        extensions_summary=extensions_summary,
        git_summary=git_summary,
        doctor=doctor,
        thread_summary=thread_summary,
        logs=logs,
        trace=trace,
    )

    issue_summary = render_issue_summary(triage)
    issue_draft = render_ai_issue_draft(triage, issue_summary, doctor)
    with zipfile.ZipFile(out_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        _write_text(zf, "README.md", render_bundle_readme(triage))
        _write_text(zf, "issue-summary.md", issue_summary)
        _write_text(zf, "ai-issue-draft.md", issue_draft)
        _write_json(zf, "triage", triage)
        _write_json(zf, "manifest", manifest)
        _write_json(zf, "environment", environment)
        _write_json(zf, "config-summary", config_summary)
        _write_json(zf, "extensions-summary", extensions_summary)
        _write_json(zf, "git", git_summary)
        if include_logs:
            log_entries = []
            for entry in logs:
                content = entry.pop("text", "")
                log_entries.append(entry)
                if content:
                    _write_text(
                        zf, _zip_name(str(entry.get("name", "unnamed.log"))), content
                    )
            _write_json(zf, "logs", log_entries)
        if include_trace:
            trace_summary = {
                key: value for key, value in trace.items() if key != "timeline"
            }
            _write_json(zf, "trace", trace_summary)
            if trace.get("timeline"):
                _write_text(zf, "trace-timeline.txt", str(trace["timeline"]))
        if thread_summary is not None:
            _write_json(zf, "thread-summary", thread_summary)
        if doctor is not None:
            _write_json(zf, "doctor", doctor)

    _issue_summary_sidecar_path(out_path).write_text(issue_summary, encoding="utf-8")
    _issue_draft_sidecar_path(out_path).write_text(issue_draft, encoding="utf-8")
    return out_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    repo_root = Path(__file__).resolve().parents[1]
    parser.add_argument(
        "--project-root", type=Path, default=repo_root, help="Alpha project root"
    )
    parser.add_argument("--config", type=Path, default=None, help="Path to config.yaml")
    parser.add_argument(
        "--extensions-config",
        type=Path,
        default=None,
        help="Path to extensions_config.json",
    )
    parser.add_argument(
        "--thread-id",
        default=None,
        help="Optional thread id to include file manifests for",
    )
    parser.add_argument("--out", type=Path, default=None, help="Output zip path")
    parser.add_argument(
        "--include-doctor",
        action="store_true",
        help="Include redacted make doctor output",
    )
    parser.add_argument(
        "--no-logs",
        action="store_true",
        help="Omit launcher log tails (included by default)",
    )
    parser.add_argument(
        "--no-trace",
        action="store_true",
        help="Omit the rendered run trace (included by default)",
    )
    parser.add_argument(
        "--log-tail-lines",
        type=int,
        default=DEFAULT_LOG_TAIL_LINES,
        help=f"Maximum log lines kept per file (default: {DEFAULT_LOG_TAIL_LINES})",
    )
    parser.add_argument(
        "--log-tail-bytes",
        type=int,
        default=DEFAULT_LOG_TAIL_BYTES,
        help=f"Maximum log bytes read per file (default: {DEFAULT_LOG_TAIL_BYTES})",
    )
    parser.add_argument(
        "--run-id", default=None, help="Render only trace records for this run id"
    )
    parser.add_argument(
        "--max-trace-events",
        type=int,
        default=500,
        help="Maximum trace records rendered (default: 500)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.log_tail_lines < 1:
        print("error: --log-tail-lines must be >= 1", file=sys.stderr)
        return 2
    if args.log_tail_bytes < 1:
        print("error: --log-tail-bytes must be >= 1", file=sys.stderr)
        return 2
    if args.max_trace_events < 0:
        print("error: --max-trace-events must be >= 0", file=sys.stderr)
        return 2
    try:
        bundle_path = create_support_bundle(
            project_root=args.project_root,
            out_path=args.out,
            config_path=args.config,
            extensions_config_path=args.extensions_config,
            thread_id=args.thread_id,
            include_doctor=args.include_doctor,
            include_logs=not args.no_logs,
            include_trace=not args.no_trace,
            log_tail_lines=args.log_tail_lines,
            run_id=args.run_id,
            max_trace_events=args.max_trace_events,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    with zipfile.ZipFile(bundle_path) as zf:
        triage = json.loads(zf.read("triage.json").decode("utf-8"))
    print(f"Support bundle: {bundle_path}")
    print(f"Issue summary: {_issue_summary_sidecar_path(bundle_path)}")
    print(f"Issue draft: {_issue_draft_sidecar_path(bundle_path)}")
    logs = triage.get("logs", {})
    trace = triage.get("trace", {})
    if logs.get("included"):
        print(
            f"Logs: {logs.get('files')} file(s), {logs.get('error_lines')} error / {logs.get('warning_lines')} warning line(s) in the collected tail"
            + (" (tail truncated)" if logs.get("tail_truncated") else "")
        )
    elif not args.no_logs:
        print(
            "Logs: none found. A started stack writes them under logs/ or backend/logs/; nothing has been collected there."
        )
    if trace.get("included"):
        print(f"Run trace: {trace.get('source')}")
    elif trace.get("reason"):
        print(f"Run trace: not available ({trace.get('reason')})")
    print("Suggested next steps:")
    for step in triage["reporter_next_steps"]:
        print(f"- {step}")
    print("If you still file an issue, paste the issue summary.")
    print(
        "If an AI assistant files the issue, start from the issue draft and replace every REQUIRED placeholder."
    )
    print(
        "Attach the zip if a maintainer asks for the evidence bundle, or if the summary alone is not enough."
    )
    print("Maintainers or AI triage tools should read triage.json first.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
