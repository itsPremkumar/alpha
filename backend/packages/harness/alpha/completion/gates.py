"""Typed gate checks: the only place the Completion Engine executes anything.

A gate is a *registered check* plus validated parameters — never free-form
shell text typed by a model. That is the plan's central security decision
("Adapt strongly: use a typed gate registry and allowlisted arguments by
default"), and it is enforced in three layers here:

1. **Registration.** ``CheckRegistry`` holds six implementations
   (``file.exists``, ``artifact.parse``, ``command.run``, ``pytest.run``,
   ``manual.review``, ``api.request``). A ``check_ref`` outside the registry
   is a contract-validation *error*, so an unknown check can never be
   dispatched.
2. **Policy.** Every argv is judged by ``alpha.guardrails.command_policy``
   (the same policy that gates the ``bash`` tool) before execution, plus this
   module's own executable allowlist from ``completion.preapproved_commands``.
   ``deny`` is never approvable; ``ask``/``defer`` needs an approval bound to
   the gate's *definition digest*, so re-parameterising a gate invalidates the
   permission that covered its previous shape.
3. **Containment.** ``argv`` arrays with ``shell=False``, a working directory
   confined to the workspace root, a per-check timeout, a byte ceiling on
   captured output, a deliberately small environment allowlist (never the
   whole environment — secrets do not enter evidence or logs), and
   control-character stripping before anything is displayed.

Output over the ceiling produces ``FAILED`` with an explicit truncation
reason — **never** a truncated match. A check whose output we could not fully
observe has not been observed at all.

Each check reports more than a bool: ``passed``, ``failed``, ``blocked``,
``timed_out``, ``not_run`` or ``invalid_configuration``, and ``not_run`` is
never a pass (``models.CheckOutcome``).
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess  # noqa: S404 - argv-array subprocess execution is this module's job, always shell=False
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from alpha.guardrails import command_policy

from .errors import CheckUnavailable, PathEscape
from .models import CheckOutcome, Expectation, Gate, GateKind
from .policy import CompletionSettings

#: Bumped whenever check behaviour changes; evidence binds it, so old evidence
#: from an older implementation cannot answer for a newer one.
CHECK_VERSION = "1.0.0"

#: Bytes of output retained for display (the tail — the failure the worker
#: needs is what the command printed last).
PREVIEW_BYTES = 4000

#: Upper bound on bytes read from a file for marker/format checks. Beyond this
#: a file gate must declare hashes rather than slurping content.
MAX_INSPECT_BYTES = 2 * 1024 * 1024

#: Environment variables passed to a check. Deliberately small: evidence and
#: logs must never carry a provider key, so the whole environment is not
#: inherited.
_ENV_ALLOWLIST = (
    "PATH",
    "SYSTEMROOT",
    "SystemRoot",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "LANG",
    "LC_ALL",
    "PYTHONIOENCODING",
    "PYTHONUTF8",
    "PYTHONPATH",
    "VIRTUAL_ENV",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
)

_CONTROL_CHARS = dict.fromkeys(range(0x00, 0x20), None)
_CONTROL_CHARS.update(dict.fromkeys(range(0x7F, 0xA0), None))
#: ANSI/VT escape introducer, stripped before any preview reaches a UI or log.
_ANSI_PREFIXES = ("\x1b[", "\x1b(", "\x1b]")


@dataclass(frozen=True)
class CheckContext:
    """Everything one check execution is allowed to see."""

    gate: Gate
    settings: CompletionSettings
    workspace: Path
    #: True when an approval bound to the current definition digest exists.
    approved: bool = False


@dataclass(frozen=True)
class CheckResult:
    """Structured result of one check execution."""

    outcome: CheckOutcome
    reason: str = ""
    exit_code: int | None = None
    output: str = ""
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.outcome is CheckOutcome.PASSED


CheckFn = Callable[[CheckContext], CheckResult]


def strip_control(text: str) -> str:
    """Remove control characters and ANSI sequences from untrusted output."""
    cleaned = text
    for prefix in _ANSI_PREFIXES:
        while prefix in cleaned:
            start = cleaned.index(prefix)
            end = start + 2
            while end < len(cleaned) and not (0x40 <= ord(cleaned[end]) <= 0x7E):
                end += 1
            cleaned = cleaned[:start] + cleaned[end + 1 :] if end < len(cleaned) else cleaned[:start]
    return cleaned.translate(_CONTROL_CHARS)


def tail(text: str, limit: int = PREVIEW_BYTES) -> str:
    """The last *limit* bytes, with an explicit omission marker."""
    if not text:
        return ""
    raw = text.encode("utf-8", errors="replace")
    if len(raw) <= limit:
        return text
    clipped = raw[-limit:].decode("utf-8", errors="replace")
    return f"[... {len(raw) - limit} earlier bytes omitted ...]\n{clipped}"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _basename(command: str) -> str:
    name = Path(str(command)).name.lower()
    for suffix in (".exe", ".bat", ".cmd", ".ps1"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name


def command_permission(argv: list[str], settings: CompletionSettings) -> tuple[str, str]:
    """Return ``(verdict, reason)`` for an argv under the shared command policy.

    ``allow`` means it may run now; ``ask``/``defer`` means it may run only
    with an approval bound to this gate definition (or when the operator
    listed the executable in ``completion.preapproved_commands``); ``deny``
    is final and cannot be approved.
    """
    if not argv:
        return "deny", "no executable was declared"
    rendered = shlex.join([str(part) for part in argv])
    outcome = command_policy.evaluate(rendered, command_policy.READ_ONLY_PROFILE)
    if outcome.allow:
        return "allow", "all command fragments are allowlisted read-only operations"
    if outcome.verdict == "deny":
        return "deny", outcome.reason or "the command policy denies this executable"
    executable = _basename(argv[0])
    if executable in {_basename(name) for name in settings.preapproved_commands}:
        return "allow", f"operator pre-approved executable {executable!r} in completion.preapproved_commands"
    return outcome.verdict, outcome.reason or "the command policy does not auto-approve this command"


def _restricted_env(extra: list[str] | None = None) -> dict[str, str]:
    """Build the check environment from an allowlist; never the whole env."""
    env: dict[str, str] = {}
    allow = list(_ENV_ALLOWLIST) + [name for name in (extra or []) if name]
    for name in allow:
        value = os.environ.get(name)
        if value is not None:
            env[name] = value
    return env


def run_argv(
    argv: list[str],
    *,
    cwd: Path,
    timeout_seconds: float,
    max_output_bytes: int,
    extra_env: list[str] | None = None,
) -> CheckResult:
    """Execute one argv array with containment; the shared command runner.

    Returns ``failed`` for a non-zero exit, ``timed_out`` for a deadline, and
    ``failed`` with an explicit reason when output exceeds the ceiling —
    truncated output is never matched.
    """
    if not argv or not str(argv[0]).strip():
        return CheckResult(CheckOutcome.INVALID_CONFIGURATION, reason="command gate has no executable")
    if not cwd.is_dir():
        return CheckResult(CheckOutcome.INVALID_CONFIGURATION, reason=f"working directory {str(cwd)!r} does not exist")

    started = time.time()
    try:
        completed = subprocess.run(  # noqa: S603 - argv array, shell=False, policy-checked above
            [str(part) for part in argv],
            cwd=str(cwd),
            capture_output=True,
            timeout=timeout_seconds,
            env=_restricted_env(extra_env),
            shell=False,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        partial = _decode_output(exc.stdout, exc.stderr)
        return CheckResult(
            CheckOutcome.TIMED_OUT,
            reason=f"timed out after {timeout_seconds:g}s",
            exit_code=None,
            output=_bounded(partial, max_output_bytes)[0],
            context={"argv": [str(part) for part in argv], "cwd": str(cwd), "duration_seconds": round(time.time() - started, 3)},
        )
    except FileNotFoundError:
        return CheckResult(
            CheckOutcome.INVALID_CONFIGURATION,
            reason=f"executable {argv[0]!r} was not found on this host",
            context={"argv": [str(part) for part in argv], "cwd": str(cwd)},
        )
    except OSError as exc:  # noqa: BLE001 - an unrunnable command is reported, never hidden
        return CheckResult(
            CheckOutcome.INVALID_CONFIGURATION,
            reason=f"{type(exc).__name__}: {exc}",
            context={"argv": [str(part) for part in argv], "cwd": str(cwd)},
        )

    raw = _decode_output(completed.stdout, completed.stderr)
    bounded, truncated = _bounded(raw, max_output_bytes)
    context = {
        "argv": [str(part) for part in argv],
        "cwd": str(cwd),
        "duration_seconds": round(time.time() - started, 3),
        "python": Path(sys.executable).name,
    }
    if truncated:
        return CheckResult(
            CheckOutcome.FAILED,
            reason=f"output exceeded the {max_output_bytes}-byte capture limit; the result was not fully observed, so it cannot be matched",
            exit_code=completed.returncode,
            output=bounded,
            context=context,
        )
    return CheckResult(
        CheckOutcome.PASSED if completed.returncode == 0 else CheckOutcome.FAILED,
        reason="" if completed.returncode == 0 else f"exit code {completed.returncode}",
        exit_code=completed.returncode,
        output=bounded,
        context=context,
    )


def _decode_output(stdout: bytes | str | None, stderr: bytes | str | None) -> str:
    parts: list[str] = []
    for chunk in (stdout, stderr):
        if chunk is None:
            continue
        if isinstance(chunk, bytes):
            parts.append(chunk.decode("utf-8", errors="replace"))
        else:
            parts.append(chunk)
    return "".join(parts)


def _bounded(text: str, limit: int) -> tuple[str, bool]:
    raw = text.encode("utf-8", errors="replace")
    if len(raw) <= limit:
        return text, False
    # Keep the *tail*: a failing command's last words are the diagnostic.
    #
    # The marker is load-bearing, not decoration: ``Evidence.output_truncated``
    # and the "never match a partially observed tail" guard in
    # ``_run_declared_command`` both detect truncation by finding it, so
    # dropping it would report over-limit output as fully observed.
    omitted = len(raw) - limit
    clipped = raw[-limit:].decode("utf-8", errors="replace")
    return f"[... {omitted} earlier bytes omitted ...]\n{clipped}", True


def _resolve_param_path(ctx: CheckContext, relative: str) -> Path:
    """Resolve a declared path inside the workspace or raise PathEscape."""
    return ctx.settings.resolve(relative)


def evaluate_expectation(
    expectation: Expectation,
    *,
    exit_code: int | None,
    output: str,
    size_bytes: int | None = None,
    status_code: int | None = None,
) -> tuple[bool, str]:
    """Apply one expectation to one observation; returns ``(ok, reason)``."""
    if expectation.exit_code is not None and exit_code is not None and exit_code != expectation.exit_code:
        return False, f"expected exit code {expectation.exit_code}, got {exit_code}"
    if expectation.exit_code is not None and exit_code is None and expectation.exit_code != 0:
        return False, f"expected exit code {expectation.exit_code}, but no exit code was observed"
    missing = [marker for marker in expectation.required_markers if marker not in output]
    if missing:
        return False, f"required markers missing from output: {missing[:5]}"
    forbidden = [marker for marker in expectation.forbidden_markers if marker in output]
    if forbidden:
        return False, f"forbidden markers present in output: {forbidden[:5]}"
    if expectation.min_bytes is not None and size_bytes is not None and size_bytes < expectation.min_bytes:
        return False, f"size {size_bytes} is below the required minimum {expectation.min_bytes}"
    if expectation.max_bytes is not None and size_bytes is not None and size_bytes > expectation.max_bytes:
        return False, f"size {size_bytes} exceeds the allowed maximum {expectation.max_bytes}"
    if expectation.status_code is not None and status_code is not None and status_code != expectation.status_code:
        return False, f"expected status {expectation.status_code}, got {status_code}"
    return True, ""


def _approval_gate(ctx: CheckContext, argv: list[str]) -> CheckResult | None:
    """Policy + approval check. ``None`` means the command may run."""
    verdict, reason = command_permission(argv, ctx.settings)
    if verdict == "allow":
        return None
    if verdict == "deny":
        return CheckResult(CheckOutcome.BLOCKED, reason=f"command policy denies this check: {reason}")
    if ctx.approved:
        return None
    return CheckResult(
        CheckOutcome.BLOCKED,
        reason=f"approval required ({verdict}): {reason}. Approve this gate to bind permission to its current definition digest.",
    )


def _check_file(ctx: CheckContext) -> CheckResult:
    """Exists / size / content assertions over one workspace-relative file."""
    relative = str(ctx.gate.parameters.get("path", ""))
    if not relative:
        return CheckResult(CheckOutcome.INVALID_CONFIGURATION, reason="file gate declares no path")
    try:
        path = _resolve_param_path(ctx, relative)
    except PathEscape as exc:
        return CheckResult(CheckOutcome.INVALID_CONFIGURATION, reason=str(exc))
    if not path.exists():
        return CheckResult(CheckOutcome.FAILED, reason=f"{relative!r} does not exist", context={"path": relative})
    if not path.is_file():
        return CheckResult(CheckOutcome.FAILED, reason=f"{relative!r} is not a regular file", context={"path": relative})
    size = path.stat().st_size
    try:
        data = path.read_bytes()[:MAX_INSPECT_BYTES]
    except OSError as exc:
        return CheckResult(CheckOutcome.FAILED, reason=f"could not read {relative!r}: {type(exc).__name__}: {exc}", context={"path": relative})
    text = data.decode("utf-8", errors="replace")
    required = list(ctx.gate.expectation.required_markers) + [str(m) for m in ctx.gate.parameters.get("must_contain", [])]
    combined = Expectation(
        exit_code=None,
        required_markers=required,
        forbidden_markers=list(ctx.gate.expectation.forbidden_markers),
        min_bytes=ctx.gate.expectation.min_bytes if ctx.gate.expectation.min_bytes is not None else ctx.gate.parameters.get("min_bytes"),
        max_bytes=ctx.gate.expectation.max_bytes,
        artifact_exists=None,
    )
    ok, reason = evaluate_expectation(combined, exit_code=None, output=text, size_bytes=size)
    return CheckResult(
        CheckOutcome.PASSED if ok else CheckOutcome.FAILED,
        reason=reason,
        output=strip_control(tail(text)) if reason else "",
        artifacts=[{"path": relative, "sha256": sha256_bytes(data), "bytes": size}],
        context={"path": relative, "bytes": size},
    )


def _check_artifact(ctx: CheckContext) -> CheckResult:
    """The generated artifact opens/parses and conforms to its declared type."""
    relative = str(ctx.gate.parameters.get("path", ""))
    if not relative:
        return CheckResult(CheckOutcome.INVALID_CONFIGURATION, reason="artifact gate declares no path")
    fmt = str(ctx.gate.parameters.get("format", "text")).lower()
    if fmt not in {"text", "json", "markdown", "csv"}:
        return CheckResult(CheckOutcome.INVALID_CONFIGURATION, reason=f"unknown artifact format {fmt!r}")
    try:
        path = _resolve_param_path(ctx, relative)
    except PathEscape as exc:
        return CheckResult(CheckOutcome.INVALID_CONFIGURATION, reason=str(exc))
    if not path.is_file():
        return CheckResult(CheckOutcome.FAILED, reason=f"artifact {relative!r} was not produced", context={"path": relative})
    size = path.stat().st_size
    if size == 0:
        return CheckResult(CheckOutcome.FAILED, reason=f"artifact {relative!r} is empty", context={"path": relative})
    try:
        raw = path.read_bytes()[:MAX_INSPECT_BYTES]
    except OSError as exc:
        return CheckResult(CheckOutcome.FAILED, reason=f"could not read artifact {relative!r}: {type(exc).__name__}: {exc}", context={"path": relative})
    text = raw.decode("utf-8", errors="replace")
    if fmt == "json":
        try:
            json.loads(text)
        except (ValueError, UnicodeDecodeError) as exc:
            return CheckResult(CheckOutcome.FAILED, reason=f"artifact {relative!r} is not valid JSON: {exc}", context={"path": relative})
    ok, reason = evaluate_expectation(ctx.gate.expectation, exit_code=None, output=text, size_bytes=size)
    return CheckResult(
        CheckOutcome.PASSED if ok else CheckOutcome.FAILED,
        reason=reason,
        output=strip_control(tail(text)) if reason else "",
        artifacts=[{"path": relative, "sha256": sha256_bytes(raw), "bytes": size}],
        context={"path": relative, "bytes": size, "format": fmt},
    )


def _command_argv(ctx: CheckContext) -> tuple[list[str], str]:
    """Build the argv for a command/pytest gate from validated parameters."""
    params = ctx.gate.parameters
    if ctx.gate.kind is GateKind.PYTEST or ctx.gate.check_ref == "pytest.run":
        targets = params.get("targets", [])
        if isinstance(targets, str):
            targets = [targets]
        extra = params.get("args", [])
        if isinstance(extra, str):
            extra = [extra]
        allowed_flags = {
            "-q",
            "-v",
            "-vv",
            "-x",
            "-s",
            "--no-header",
            "--disable-warnings",
            "--tb",
            "-k",
            "-m",
            "-p",
            "--timeout",
        }
        for arg in extra:
            text = str(arg)
            flag = text.split("=", 1)[0]
            if not text.startswith("-") or (flag not in allowed_flags and text not in allowed_flags):
                return [], f"pytest argument {text!r} is not in the allowed flag set {sorted(allowed_flags)}"
        argv = [sys.executable, "-m", "pytest"]
        argv.extend(str(t) for t in targets)
        argv.extend(str(a) for a in extra)
        return argv, ""
    argv = params.get("argv")
    if isinstance(argv, str):
        argv = shlex.split(argv)
    if not isinstance(argv, list) or not argv:
        return [], "command gate declares no argv array"
    return [str(part) for part in argv], ""


def _run_declared_command(ctx: CheckContext) -> CheckResult:
    """Shared body for ``command.run`` and ``pytest.run``."""
    argv, error = _command_argv(ctx)
    if error:
        return CheckResult(CheckOutcome.INVALID_CONFIGURATION, reason=error)
    timeout = ctx.settings.gate_timeout_seconds
    declared = ctx.gate.parameters.get("timeout_seconds")
    if declared is not None:
        try:
            declared_f = float(declared)
        except (TypeError, ValueError):
            return CheckResult(CheckOutcome.INVALID_CONFIGURATION, reason=f"timeout_seconds {declared!r} is not a number")
        if declared_f <= 0 or declared_f > timeout:
            return CheckResult(
                CheckOutcome.INVALID_CONFIGURATION,
                reason=f"timeout_seconds {declared_f:g} must be positive and no greater than the configured ceiling {timeout:g}s",
            )
        timeout = declared_f
    blocked = _approval_gate(ctx, argv)
    if blocked is not None:
        return blocked

    cwd_rel = str(ctx.gate.parameters.get("cwd", "") or "")
    try:
        cwd = ctx.settings.resolve(cwd_rel) if cwd_rel else ctx.workspace
    except PathEscape as exc:
        return CheckResult(CheckOutcome.INVALID_CONFIGURATION, reason=str(exc))

    extra_env = [str(name) for name in ctx.gate.parameters.get("env", [])] if isinstance(ctx.gate.parameters.get("env", []), list) else []
    result = run_argv(argv, cwd=cwd, timeout_seconds=timeout, max_output_bytes=ctx.settings.max_output_bytes, extra_env=extra_env)
    if result.outcome in {CheckOutcome.TIMED_OUT, CheckOutcome.INVALID_CONFIGURATION}:
        return result
    if result.outcome is CheckOutcome.BLOCKED:
        return result
    # Over-limit output already returned FAILED with the truncation reason; do
    # not let an expectation match a tail we only partially observed.
    truncated = "earlier bytes omitted" in result.output
    if truncated:
        return result
    ok, reason = evaluate_expectation(ctx.gate.expectation, exit_code=result.exit_code, output=result.output)
    return CheckResult(
        CheckOutcome.PASSED if ok else CheckOutcome.FAILED,
        reason=reason or result.reason,
        exit_code=result.exit_code,
        output=strip_control(tail(result.output)),
        context=result.context,
    )


def _check_command(ctx: CheckContext) -> CheckResult:
    """A declared argv array, policy-checked and confined to the workspace."""
    return _run_declared_command(ctx)


def _check_pytest(ctx: CheckContext) -> CheckResult:
    """``python -m pytest`` over declared targets, through the same runner."""
    return _run_declared_command(ctx)


def _check_manual(ctx: CheckContext) -> CheckResult:
    """Manual review gates are never executed; a human records the verdict."""
    checklist = ctx.gate.parameters.get("checklist", [])
    return CheckResult(
        CheckOutcome.NOT_RUN,
        reason="manual review: a person records the verdict through POST /api/completion/scopes/{id}/gates/{gate_id}/review",
        context={"checklist": [str(item) for item in checklist]} if isinstance(checklist, list) else {},
    )


def _check_api(ctx: CheckContext) -> CheckResult:
    """Bounded HTTP observation; off unless ``completion.allow_network_checks``."""
    if not ctx.settings.allow_network_checks:
        return CheckResult(
            CheckOutcome.BLOCKED,
            reason="network checks are disabled (completion.allow_network_checks is false); an HTTP probe is an external side effect",
        )
    url = str(ctx.gate.parameters.get("url", ""))
    if not url.startswith(("http://", "https://")):
        return CheckResult(CheckOutcome.INVALID_CONFIGURATION, reason=f"url {url!r} must be an absolute http(s) URL")
    blocked = _approval_gate(ctx, [url.split("/")[0] + "//" + url.split("/")[2]])
    if blocked is not None:
        return blocked
    method = str(ctx.gate.parameters.get("method", "GET")).upper()
    if method not in {"GET", "HEAD"}:
        return CheckResult(CheckOutcome.INVALID_CONFIGURATION, reason=f"method {method!r} is not a read-only HTTP verb")
    timeout = min(ctx.settings.gate_timeout_seconds, 30.0)
    started = time.time()
    try:
        request = urllib.request.Request(url, method=method, headers={"User-Agent": f"alpha-completion/{CHECK_VERSION}"})  # noqa: S310 - scheme validated above
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - http(s) only, read-only verb
            status = int(response.status)
            body = response.read(MAX_INSPECT_BYTES).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        body = ""
        if ctx.gate.expectation.status_code is None:
            return CheckResult(
                CheckOutcome.FAILED,
                reason=f"HTTP {exc.code}",
                context={"url": url, "method": method, "duration_seconds": round(time.time() - started, 3)},
            )
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return CheckResult(
            CheckOutcome.FAILED,
            reason=f"request failed: {type(exc).__name__}: {exc}",
            context={"url": url, "method": method, "duration_seconds": round(time.time() - started, 3)},
        )
    ok, reason = evaluate_expectation(ctx.gate.expectation, exit_code=None, output=body, status_code=status)
    return CheckResult(
        CheckOutcome.PASSED if ok else CheckOutcome.FAILED,
        reason=reason,
        output=strip_control(tail(body)) if body else "",
        context={"url": url, "method": method, "status_code": status, "duration_seconds": round(time.time() - started, 3)},
    )


class CheckRegistry:
    """Name -> implementation map for reviewed checks.

    Registration is the allowlist: contract validation asks
    :meth:`names`, so a ``check_ref`` nobody registered is refused *before*
    dispatch rather than at execution time.
    """

    def __init__(self) -> None:
        self._checks: dict[str, CheckFn] = {}

    def register(self, name: str, fn: CheckFn) -> None:
        if not name or name in self._checks:
            raise ValueError(f"check {name!r} is already registered or empty")
        self._checks[name] = fn

    def names(self) -> frozenset[str]:
        return frozenset(self._checks)

    def get(self, name: str) -> CheckFn:
        try:
            return self._checks[name]
        except KeyError:
            raise CheckUnavailable(f"check {name!r} is not registered; registered checks: {sorted(self._checks)}", subject=name) from None

    def run(self, ctx: CheckContext) -> CheckResult:
        """Execute one gate's check. Never raises: a broken check is a result."""
        fn = self.get(ctx.gate.check_ref)
        try:
            return fn(ctx)
        except CheckUnavailable as exc:
            return CheckResult(CheckOutcome.INVALID_CONFIGURATION, reason=str(exc))
        except Exception as exc:  # noqa: BLE001 - an unrunnable gate is red, never green
            return CheckResult(CheckOutcome.INVALID_CONFIGURATION, reason=f"{type(exc).__name__}: {exc}")


def default_registry() -> CheckRegistry:
    """The six checks this build actually implements."""
    registry = CheckRegistry()
    registry.register("file.exists", _check_file)
    registry.register("artifact.parse", _check_artifact)
    registry.register("command.run", _check_command)
    registry.register("pytest.run", _check_pytest)
    registry.register("manual.review", _check_manual)
    registry.register("api.request", _check_api)
    return registry


_DEFAULT_REGISTRY: CheckRegistry | None = None


def default_check_registry() -> CheckRegistry:
    """Process-wide default registry (built once)."""
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = default_registry()
    return _DEFAULT_REGISTRY


def executable_exists(command: str) -> bool:
    """Whether a named executable resolves on this host (used by diagnostics)."""
    return shutil.which(command) is not None


__all__ = [
    "CHECK_VERSION",
    "MAX_INSPECT_BYTES",
    "PREVIEW_BYTES",
    "CheckContext",
    "CheckRegistry",
    "CheckResult",
    "check_context_for",
    "command_permission",
    "default_check_registry",
    "default_registry",
    "evaluate_expectation",
    "executable_exists",
    "run_argv",
    "strip_control",
    "tail",
]


def check_context_for(gate: Gate, settings: CompletionSettings, *, approved: bool = False) -> CheckContext:
    """Convenience constructor so callers never build a context by hand."""
    return CheckContext(gate=gate, settings=settings, workspace=settings.workspace_root, approved=approved)
