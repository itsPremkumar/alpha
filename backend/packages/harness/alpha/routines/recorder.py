"""Recorder and replay for demonstration-captured routines.

Two responsibilities:

- :class:`RoutineRecorder` turns an observed sequence of tool calls into a
  :class:`~alpha.routines.models.Routine`, auto-detecting string values that
  look like user-specific inputs (emails, ids, absolute paths, URLs) as
  candidate parameters.
- :func:`replay` performs a pure, validated substitution of parameter values
  into a routine's steps and returns the concrete tool calls to execute. It
  never executes anything itself.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from alpha.routines.models import (
    Routine,
    RoutineStep,
    RoutineValidationError,
)

#: A string arg matching one of these is a candidate for parameterisation.
_CANDIDATE_PATTERNS = (
    re.compile(r"^[\w.+-]+@[\w-]+\.[\w.-]+$"),          # email
    re.compile(r"^https?://", re.IGNORECASE),           # url
    re.compile(r"^(?:[A-Za-z]:[\\/]|/)"),               # absolute path
    re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE),  # uuid
)

_TEMPLATE_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def looks_like_input(value: Any) -> bool:
    """True when a captured string value is a plausible user-specific input."""
    if not isinstance(value, str):
        return False
    return any(pattern.search(value) for pattern in _CANDIDATE_PATTERNS)


class RoutineRecorder:
    """Accumulates demonstrated steps and finalises them into a routine."""

    def __init__(self, name: str, description: str = "", *, source: str = "demonstration") -> None:
        self._name = name
        self._description = description
        self._source = source
        self._steps: list[RoutineStep] = []
        self._explicit_params: dict[str, Any] = {}
        self._auto_params: dict[str, Any] = {}

    @property
    def steps(self) -> list[RoutineStep]:
        return list(self._steps)

    def record_step(self, tool: str, args: Mapping[str, Any] | None = None, *, parameterize: Iterable[str] | None = None, note: str = "") -> RoutineStep:
        """Record one observed tool call.

        ``parameterize`` names args to templatize explicitly. When omitted, any
        string arg whose value looks like a user-specific input is templatized
        automatically and registered as a required parameter.
        """
        args = dict(args or {})
        explicit = list(parameterize or [])
        if parameterize is None:
            auto = [k for k, v in args.items() if looks_like_input(v)]
            explicit = auto
            for key in auto:
                self._auto_params.setdefault(key, None)
        step = RoutineStep(tool=tool, args=args, parameterize=explicit, note=note)
        self._steps.append(step)
        return step

    def set_parameter(self, name: str, default: Any = None) -> None:
        """Declare a routine-level parameter and its default (``None`` = required)."""
        self._explicit_params[name] = default

    def finalize(self, *, parameters: Mapping[str, Any] | None = None, description: str | None = None) -> Routine:
        merged: dict[str, Any] = dict(self._auto_params)
        merged.update(self._explicit_params)
        if parameters:
            merged.update(dict(parameters))
        return Routine(
            name=self._name,
            description=description if description is not None else self._description,
            steps=list(self._steps),
            parameters=merged,
            source=self._source,
        )


def _substitute(value: Any, values: Mapping[str, Any], *, routine_name: str) -> Any:
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in values:
                raise RoutineValidationError(f"routine {routine_name!r} references undefined parameter {key!r}")
            return str(values[key])

        return _TEMPLATE_RE.sub(replace, value)
    if isinstance(value, list):
        return [_substitute(v, values, routine_name=routine_name) for v in value]
    if isinstance(value, dict):
        return {k: _substitute(v, values, routine_name=routine_name) for k, v in value.items()}
    return value


def replay(routine: Routine, values: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Substitute parameter values into a routine and return concrete tool calls.

    Raises :class:`RoutineValidationError` when a required parameter is missing.
    Never executes anything — the caller decides whether to run the steps.
    """
    provided = dict(values or {})
    missing = [p for p in routine.required_parameters if p not in provided]
    if missing:
        raise RoutineValidationError(f"routine {routine.name!r} is missing required parameter(s): {missing}")

    effective = {name: (provided.get(name, default)) for name, default in routine.parameters.items()}
    effective.update(provided)

    calls: list[dict[str, Any]] = []
    for step in routine.steps:
        args = dict(step.args)
        for key in step.parameterize:
            if key not in effective:
                raise RoutineValidationError(f"routine {routine.name!r} step {step.tool!r} needs parameter {key!r}")
            args[key] = effective[key]
        args = _substitute(args, effective, routine_name=routine.name)
        calls.append({"tool": step.tool, "args": args})
    return calls


def capture_trace(name: str, trace: Iterable[Mapping[str, Any]], *, description: str = "") -> Routine:
    """Build a routine directly from an observed ``[{tool, args}, ...]`` trace."""
    recorder = RoutineRecorder(name, description)
    for entry in trace:
        recorder.record_step(str(entry.get("tool", "")), entry.get("args") or {})
    return recorder.finalize()


__all__ = ["RoutineRecorder", "capture_trace", "looks_like_input", "replay"]
