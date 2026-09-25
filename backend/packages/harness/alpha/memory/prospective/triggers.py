"""Pure, fail-closed evaluation of prospective-memory triggers."""

from __future__ import annotations

import ast
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .models import TriggerContext, TriggerKind, TriggerSpec

_COMPARISON_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*(==|!=|>=|<=|>|<)\s*(.+?)\s*$")


@dataclass(slots=True)
class TriggerEvaluation:
    """Detailed trigger result for status surfaces and tests."""

    matched: bool
    kind: str = ""
    reason: str = ""

    @property
    def status(self) -> str:
        """Return a compact matched/not-matched status."""

        return "matched" if self.matched else "not_matched"


def _spec(trigger: TriggerSpec | Mapping[str, Any]) -> TriggerSpec | None:
    try:
        if isinstance(trigger, TriggerSpec):
            return trigger
        return TriggerSpec.model_validate(dict(trigger))
    except (TypeError, ValueError):
        return None


def _context(context: TriggerContext | Mapping[str, Any] | None) -> TriggerContext | None:
    if context is None:
        return TriggerContext()
    try:
        if isinstance(context, TriggerContext):
            return context
        return TriggerContext.model_validate(dict(context))
    except (TypeError, ValueError):
        return None


def _first_value(params: Mapping[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in params:
            return params[name]
    return None


def _matches(expected: Any, actual: Any) -> bool:
    """Match a scalar exactly or a mapping as a subset of actual facts."""

    if isinstance(expected, Mapping):
        if not isinstance(actual, Mapping):
            return False
        return all(key in actual and _matches(value, actual[key]) for key, value in expected.items())
    if isinstance(expected, (list, tuple)):
        if not isinstance(actual, (list, tuple)):
            return False
        return len(expected) == len(actual) and all(_matches(left, right) for left, right in zip(expected, actual))
    return expected == actual


def _scope_matches(params: Mapping[str, Any], context: TriggerContext) -> bool:
    scope = params.get("scope")
    scope_map = scope if isinstance(scope, Mapping) else params
    expected_user = _first_value(scope_map, ("user_id", "user"))
    expected_agent = _first_value(scope_map, ("agent_name", "agent"))
    if expected_user is not None and str(expected_user) != str(context.user_id or ""):
        return False
    if expected_agent is not None and str(expected_agent) != str(context.agent_name or ""):
        return False
    return True


def _fact_map(context: TriggerContext) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    for values in (
        context.values,
        context.attributes,
        context.state,
        context.metadata,
        context.tool_args,
        context.arguments,
        context.event_data,
        context.payload,
    ):
        facts.update(values)
    if isinstance(context.condition, Mapping):
        facts.update(context.condition)
    return facts


def _lookup_fact(context: TriggerContext, key: str) -> Any:
    facts = _fact_map(context)
    if key in facts:
        return facts[key]
    current: Any = facts
    for part in key.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _literal(value: str) -> Any:
    try:
        return ast.literal_eval(value)
    except (SyntaxError, ValueError):
        return value.strip().strip("'\"")


def _compare(actual: Any, operator: str, expected: Any) -> bool:
    if operator == "==":
        return _matches(expected, actual)
    if operator == "!=":
        return not _matches(expected, actual)
    if operator == "in":
        try:
            return actual in expected
        except TypeError:
            return False
    if operator == "contains":
        try:
            return expected in actual
        except TypeError:
            return False
    try:
        if operator == ">=":
            return actual >= expected
        if operator == "<=":
            return actual <= expected
        if operator == ">":
            return actual > expected
        if operator == "<":
            return actual < expected
    except (TypeError, ValueError):
        return False
    return False


def _condition_matches(params: Mapping[str, Any], context: TriggerContext) -> tuple[bool, str]:
    if not _scope_matches(params, context):
        return False, "scope_mismatch"

    condition = params.get("condition")
    if isinstance(condition, str):
        actual_condition = context.condition
        if actual_condition is None:
            return False, "condition_missing"
        return _matches(condition, actual_condition), "condition_value"
    if isinstance(condition, Mapping):
        return _matches(condition, _fact_map(context)), "condition_mapping"

    all_conditions = params.get("all")
    if isinstance(all_conditions, list):
        for child in all_conditions:
            if not isinstance(child, Mapping):
                return False, "invalid_all_condition"
            matched, _ = _condition_matches(child, context)
            if not matched:
                return False, "all_condition_failed"
        return True, "all_conditions"

    any_conditions = params.get("any")
    if isinstance(any_conditions, list):
        for child in any_conditions:
            if not isinstance(child, Mapping):
                continue
            matched, _ = _condition_matches(child, context)
            if matched:
                return True, "any_condition"
        return False, "any_condition_failed"

    key = _first_value(params, ("key", "field", "path"))
    if key is None:
        return False, "condition_format_missing"
    key_text = str(key)
    actual = _lookup_fact(context, key_text)
    if "exists" in params:
        exists = bool(actual) if params["exists"] else actual is not None
        return exists, "condition_exists"

    operator = str(_first_value(params, ("op", "operator")) or "==")
    expected = _first_value(params, ("value", "equals", "expected"))
    if expected is None and "expression" in params:
        expression = str(params["expression"])
        match = _COMPARISON_RE.match(expression)
        if match is None:
            return False, "invalid_expression"
        key_text, operator, raw_expected = match.groups()
        actual = _lookup_fact(context, key_text)
        expected = _literal(raw_expected)
    if expected is None:
        return False, "condition_value_missing"
    return _compare(actual, operator, expected), "condition_comparison"


def _tool_matches(params: Mapping[str, Any], context: TriggerContext) -> tuple[bool, str]:
    if not _scope_matches(params, context):
        return False, "scope_mismatch"
    expected_name = _first_value(params, ("name", "tool", "tool_name"))
    actual_name = context.tool_name or context.tool
    if actual_name is None:
        return False, "tool_missing"
    if expected_name is not None and str(expected_name) != str(actual_name):
        return False, "tool_name_mismatch"
    expected_args = _first_value(params, ("args", "arguments", "tool_args"))
    actual_args = context.tool_args or context.arguments
    if expected_args is not None and not _matches(expected_args, actual_args):
        return False, "tool_args_mismatch"
    expected_result = _first_value(params, ("result", "tool_result"))
    if expected_result is not None and not _matches(expected_result, context.tool_result):
        return False, "tool_result_mismatch"
    return True, "tool"


def _event_matches(params: Mapping[str, Any], context: TriggerContext) -> tuple[bool, str]:
    if not _scope_matches(params, context):
        return False, "scope_mismatch"
    expected_name = _first_value(params, ("name", "event", "event_name"))
    actual_name = context.event_name or context.event
    if actual_name is None:
        return False, "event_missing"
    if expected_name is not None and str(expected_name) != str(actual_name):
        return False, "event_name_mismatch"
    expected_data = _first_value(params, ("data", "payload", "event_data"))
    if expected_data is not None:
        actual_data = context.event_data or context.payload or context.metadata.get("event_data", context.values)
        if not _matches(expected_data, actual_data):
            return False, "event_data_mismatch"
    return True, "event"


def evaluate_trigger_detailed(
    trigger: TriggerSpec | Mapping[str, Any] | None,
    context: TriggerContext | Mapping[str, Any] | None = None,
) -> TriggerEvaluation:
    """Evaluate a trigger and disclose why it did or did not match."""

    if trigger is None:
        return TriggerEvaluation(matched=False, reason="trigger_missing")
    spec = _spec(trigger)
    if spec is None:
        return TriggerEvaluation(matched=False, reason="trigger_invalid")
    ctx = _context(context)
    if ctx is None:
        return TriggerEvaluation(matched=False, kind=spec.kind, reason="context_invalid")
    kind = str(spec.kind or "").strip().lower()
    if kind == TriggerKind.TOOL.value:
        matched, reason = _tool_matches(spec.params, ctx)
    elif kind == TriggerKind.EVENT.value:
        matched, reason = _event_matches(spec.params, ctx)
    elif kind == TriggerKind.CONDITION.value:
        matched, reason = _condition_matches(spec.params, ctx)
    else:
        return TriggerEvaluation(matched=False, kind=kind or "unknown", reason="unknown_trigger_kind")
    return TriggerEvaluation(matched=matched, kind=kind, reason=reason)


def evaluate_trigger(
    trigger: TriggerSpec | Mapping[str, Any] | None,
    context: TriggerContext | Mapping[str, Any] | None = None,
) -> bool:
    """Return whether ``trigger`` matches ``context``; unknown means false."""

    return evaluate_trigger_detailed(trigger, context).matched


# Short alias for callers that naturally use the verb ``matches``.
matches_trigger = evaluate_trigger


__all__ = [
    "TriggerEvaluation",
    "evaluate_trigger",
    "evaluate_trigger_detailed",
    "matches_trigger",
]
