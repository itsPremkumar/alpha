"""Model-facing System One/Laya desktop action tool.

The tool observes the Windows accessibility tree, asks System One for one
indexed operation, resolves that index locally, and dispatches through the
existing sentinel-guarded OS layer.  It deliberately has no coordinate,
selector, script, or screenshot arguments: System One chooses semantics, while
Alpha owns execution.
"""

import asyncio
import re
from typing import Any

from langchain.tools import tool

from alpha.computer_use import accessibility, dispatcher
from alpha.computer_use.system_one_policy import (
    BLOCKED,
    CLICK,
    DONE,
    HOTKEY,
    PRESS,
    TYPE_TEXT,
    WAIT,
    choose_next_computer_action,
    revalidate_target,
)
from alpha.tools.builtins.os_computer_tool import (
    SCAN_TIMEOUT_S,
    _begin_side_effect,
    _enrich,
    _halt_gated,
    _run_os,
    _thread_id,
)
from alpha.tools.types import Runtime

MAX_TYPE_CHARS = 10000
MAX_KEY_CHARS = 64
MAX_HOTKEY_CHARS = 128
MAX_ELEMENT_LIMIT = accessibility.MAX_ELEMENTS_LIMIT
#: Deadline for the System One decision round trip itself. The client has its own
#: per-request timeout, but a wedged partition tournament must not hold the tool
#: open indefinitely either.
DECISION_TIMEOUT_S = 30.0
_GEOMETRY_REASON_RE = re.compile(
    r"coordinate|bbox|bounds|center|coords?|\b[xy]\s*=|\(\s*-?\d+\s*,\s*-?\d+\s*\)|\[\s*-?\d+\s*,\s*-?\d+(?:\s*,\s*-?\d+){0,2}\s*\]|\b-?\d+\s*,\s*-?\d+\b",
    re.IGNORECASE,
)


def _safe_reason(reason: Any, *, sensitive_values: tuple[str, ...] = ()) -> str | None:
    """Keep useful errors while withholding geometry or caller values."""
    if reason in (None, ""):
        return None
    text = str(reason)
    lowered = text.casefold()
    if any(str(value) and str(value).casefold() in lowered for value in sensitive_values):
        return "desktop action was blocked or failed; execution details were redacted"
    if _GEOMETRY_REASON_RE.search(text):
        return "desktop action was blocked or failed; execution geometry was redacted"
    return text


def _execution_summary(payload: dict[str, Any], *, sensitive_values: tuple[str, ...] = ()) -> dict[str, Any]:
    """Strip dispatcher coordinates, hotkey values, and typed text."""
    summary: dict[str, Any] = {
        "ok": bool(payload.get("ok", False)),
        "status": str(payload.get("status", "failed")),
        "dispatched": bool(payload.get("dispatched", False)),
    }
    if "backend" in payload:
        summary["backend"] = payload["backend"]
    reason = _safe_reason(payload.get("reason"), sensitive_values=sensitive_values)
    if reason:
        summary["reason"] = reason
    return summary


def _window_names(observation: dict[str, Any]) -> set[str]:
    """Return distinct matched window labels without exposing geometry."""
    raw_windows = observation.get("matched_windows")
    if isinstance(raw_windows, list):
        names = {str(window).strip() for window in raw_windows if str(window).strip()}
        if names:
            return names
    elements = observation.get("elements")
    if not isinstance(elements, list):
        return set()
    return {str(item.get("window") or item.get("window_title") or "").strip() for item in elements if isinstance(item, dict) and str(item.get("window") or item.get("window_title") or "").strip()}


def _observation_window_count(observation: dict[str, Any]) -> int | None:
    """Scanner-declared window count, or None when the observation predates it.

    Authoritative when present: two real windows routinely share a title, so the
    de-duplicated label set in :func:`_window_names` can under-report ambiguity
    in both directions.
    """

    count = observation.get("matched_window_count")
    if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
        return count
    names = _window_names(observation)
    return len(names) if names else None


def _context_error(observation: dict[str, Any], *, fresh: bool = False) -> dict[str, Any] | None:
    """Validate a semantic scan before any side effect is considered."""
    if not isinstance(observation, dict):
        return {
            "ok": False,
            "status": "unavailable",
            "reason": "desktop accessibility observation was malformed; no input was dispatched",
            "dispatched": False,
            "fallback": True,
        }
    if observation.get("truncated"):
        return {
            "ok": False,
            "status": "ambiguous",
            "reason": "fresh accessibility element table was truncated; no input was dispatched" if fresh else "accessibility element table was truncated; select a narrower window or lower max_elements",
            "dispatched": False,
            "fallback": True,
        }
    if observation.get("ok") is not True or observation.get("scanned") is not True:
        try:
            count = int(observation.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        return {
            "ok": False,
            "status": str(observation.get("status", "unavailable")),
            "reason": _safe_reason(observation.get("reason")) or "desktop accessibility observation unavailable",
            "dispatched": False,
            "fallback": True,
            "observation": {
                "scanned": bool(observation.get("scanned")),
                "count": count,
            },
        }
    matched_windows = observation.get("matched_windows")
    declared_count = _observation_window_count(observation)
    if declared_count is not None:
        multiple_windows = declared_count > 1
    elif isinstance(matched_windows, list):
        multiple_windows = len([window for window in matched_windows if str(window).strip()]) > 1
    else:
        multiple_windows = len(_window_names(observation)) > 1
    if multiple_windows:
        return {
            "ok": False,
            "status": "ambiguous",
            "reason": "fresh desktop observation matched multiple windows; no input was dispatched" if fresh else "accessibility scan matched multiple windows; select one window_title before acting",
            "dispatched": False,
            "fallback": True,
        }
    return None


def _focused_elements(observation: dict[str, Any]) -> list[dict[str, Any]]:
    elements = observation.get("elements")
    if not isinstance(elements, list):
        return []
    return [element for element in elements if isinstance(element, dict) and element.get("focused") is True]


async def _semantic_observation(title: str, limit: int, thread_id: str = "default_thread") -> dict[str, Any]:
    """One bounded UI-Automation walk, behind the shared OS deadline."""
    return await _run_os(
        accessibility.inspect_ui_tree,
        title,
        limit,
        action="inspect_ui_tree",
        timeout_s=SCAN_TIMEOUT_S,
        thread_id=thread_id,
    )


@tool("desktop_system_one_action", parse_docstring=True)
async def desktop_system_one_action_tool(
    runtime: Runtime,
    goal: str,
    window_title: str = "",
    text: str = "",
    key: str = "",
    hotkey: str = "",
    max_elements: int = 200,
) -> dict[str, Any]:
    """Choose and safely execute one indexed desktop action using System One/Laya.

    The tool observes the operating-system accessibility tree, presents only a
    semantic indexed table to System One, and resolves the selected index back
    to the observed element inside Alpha. It never accepts or returns pixel
    coordinates, CSS-like selectors, scripts, or screenshots.

    System One is optional: when it is disabled, unavailable, in shadow mode,
    or not confident, the result is ``status="no_signal"`` and no input is
    dispatched. The caller should use its existing desktop/LLM fallback.

    Args:
        runtime: Injected harness runtime (automatic; not model-facing).
        goal: Natural-language desktop goal for this step.
        window_title: Optional window-title substring to inspect; empty scans all windows.
        text: Exact text to type when the decision is TYPE_TEXT; max 10000 characters.
        key: Exact single key to press when the decision is PRESS, such as ``enter``.
        hotkey: Exact shortcut to run when the decision is HOTKEY, such as ``ctrl+s``.
        max_elements: Maximum accessibility elements to inspect (1-500).
    """
    blocked = _halt_gated("system_one_desktop_action")
    if blocked is not None:
        return _enrich(blocked, _thread_id(runtime))

    normalized_goal = str(goal or "").strip()
    if not normalized_goal:
        return _enrich(
            {"ok": False, "status": "invalid", "reason": "goal must be a non-empty string", "dispatched": False},
            _thread_id(runtime),
        )
    text_value = str(text or "")
    key_value = str(key or "").strip()
    hotkey_value = str(hotkey or "").strip()
    if len(text_value) > MAX_TYPE_CHARS:
        return _enrich(
            {"ok": False, "status": "invalid", "reason": f"text too long: {len(text_value)} chars (max {MAX_TYPE_CHARS})", "dispatched": False},
            _thread_id(runtime),
        )
    if len(key_value) > MAX_KEY_CHARS or len(hotkey_value) > MAX_HOTKEY_CHARS:
        return _enrich(
            {"ok": False, "status": "invalid", "reason": "key or hotkey is too long", "dispatched": False},
            _thread_id(runtime),
        )
    try:
        element_limit = max(1, min(int(max_elements), MAX_ELEMENT_LIMIT))
    except (TypeError, ValueError):
        return _enrich(
            {"ok": False, "status": "invalid", "reason": "max_elements must be an integer", "dispatched": False},
            _thread_id(runtime),
        )

    observation = await _semantic_observation(str(window_title or "").strip(), element_limit, _thread_id(runtime))
    if observation.get("status") == "timeout":
        return _enrich({**observation, "fallback": True}, _thread_id(runtime))
    context_error = _context_error(observation)
    if context_error is not None:
        return _enrich(context_error, _thread_id(runtime))

    # Keep the requested bound in the observation so the pure policy can apply
    # exactly the same cap as the executor without receiving any geometry.
    policy_observation = dict(observation)
    policy_observation["max_elements"] = element_limit
    try:
        decision = await asyncio.wait_for(
            choose_next_computer_action(
                policy_observation,
                normalized_goal,
                text=text_value,
                key=key_value,
                hotkey=hotkey_value,
            ),
            timeout=DECISION_TIMEOUT_S,
        )
    except TimeoutError:
        return _enrich(
            {
                "ok": False,
                "status": "timeout",
                "reason": f"System One desktop decision exceeded its {DECISION_TIMEOUT_S:g}s deadline; no input was dispatched",
                "dispatched": False,
                "fallback": True,
                "timeout_seconds": DECISION_TIMEOUT_S,
            },
            _thread_id(runtime),
        )
    if decision is None:
        return _enrich(
            {
                "ok": False,
                "status": "no_signal",
                "reason": "System One had no confident desktop action; caller should use its fallback",
                "dispatched": False,
                "fallback": True,
            },
            _thread_id(runtime),
        )

    decision_payload = decision.to_dict()
    if decision.operation in {DONE, BLOCKED}:
        return _enrich(
            {
                "ok": False,
                "status": "unverified" if decision.operation == DONE else "blocked",
                "reason": "DONE is a model proposal and requires independent goal verification" if decision.operation == DONE else "System One reported no supported desktop progress",
                "dispatched": False,
                "fallback": decision.operation == DONE,
                "decision": decision_payload,
            },
            _thread_id(runtime),
        )

    if decision.operation in {CLICK, TYPE_TEXT, PRESS, HOTKEY}:
        fresh_observation = await _semantic_observation(str(window_title or "").strip(), element_limit, _thread_id(runtime))
        if fresh_observation.get("status") == "timeout":
            return _enrich({**fresh_observation, "fallback": True, "decision": decision_payload}, _thread_id(runtime))
        context_error = _context_error(fresh_observation, fresh=True)
        if context_error is not None:
            return _enrich(context_error, _thread_id(runtime))
        if decision.operation in {CLICK, TYPE_TEXT}:
            fresh_decision_element = revalidate_target(
                {**fresh_observation, "max_elements": element_limit},
                decision,
                max_elements=element_limit,
            )
            if fresh_decision_element is None:
                return _enrich(
                    {
                        "ok": False,
                        "status": "stale_target",
                        "reason": "desktop target changed or disappeared before execution; no input was dispatched",
                        "dispatched": False,
                        "fallback": True,
                    },
                    _thread_id(runtime),
                )
            # Use only the fresh geometry locally. The decision/model representation
            # remains index + semantic fields and is never replaced with coordinates.
            decision.element = fresh_decision_element
            decision_payload = decision.to_dict()
        elif len(_focused_elements(fresh_observation)) != 1:
            return _enrich(
                {
                    "ok": False,
                    "status": "focus_unverified",
                    "reason": "keyboard action requires exactly one freshly observed focused control; no input was dispatched",
                    "dispatched": False,
                    "fallback": True,
                },
                _thread_id(runtime),
            )

    operation = decision.operation
    thread_id, side_effect_blocked = _begin_side_effect(runtime, f"computer_{operation.lower()}")
    if side_effect_blocked is not None:
        return _enrich({**side_effect_blocked, "decision": decision_payload}, thread_id)

    # A blacklisted combo is unreachable from this tool: there is no argument
    # here that can supply an operator confirmation token, so the blacklist
    # stands. The System One route is strictly narrower than the low-level one.
    lease = f"os-computer:{thread_id}"

    result: dict[str, Any]
    partial_dispatched = False
    if operation == PRESS:
        if not key_value:
            result = {"ok": False, "status": "invalid", "dispatched": False, "reason": "PRESS requires a non-empty key"}
        else:
            result = await _run_os(dispatcher.keyboard_press, key_value, action="keyboard_press", thread_id=thread_id, lease=lease)
    elif operation == HOTKEY:
        if not hotkey_value:
            result = {"ok": False, "status": "invalid", "dispatched": False, "reason": "HOTKEY requires a non-empty shortcut"}
        else:
            result = await _run_os(dispatcher.keyboard_hotkey, hotkey_value, action="keyboard_hotkey", thread_id=thread_id, lease=lease)
    elif operation == CLICK:
        if decision.element is None:
            result = {"ok": False, "status": "stale_target", "dispatched": False, "reason": "selected desktop element was unavailable at execution"}
        else:
            x, y = decision.element.center
            result = await _run_os(dispatcher.mouse_click, x, y, action="mouse_click", thread_id=thread_id, lease=lease)
    elif operation == TYPE_TEXT:
        if decision.element is None:
            result = {"ok": False, "status": "stale_target", "dispatched": False, "reason": "selected desktop field was unavailable at execution"}
        elif not text_value:
            result = {"ok": False, "status": "invalid", "dispatched": False, "reason": "TYPE_TEXT requires non-empty text"}
        else:
            x, y = decision.element.center
            click_result = await _run_os(dispatcher.mouse_click, x, y, action="mouse_click", thread_id=thread_id, lease=lease)
            if not click_result.get("ok"):
                result = click_result
            else:
                partial_dispatched = True
                post_click_observation = await _semantic_observation(str(window_title or "").strip(), element_limit, thread_id)
                if post_click_observation.get("status") == "timeout":
                    result = {
                        "ok": False,
                        "status": "timeout",
                        "dispatched": True,
                        "reason": "the focus re-check exceeded its deadline; text was not typed",
                    }
                    post_click_error: dict[str, Any] | None = {"ok": False}
                    post_click_target = None
                else:
                    post_click_error = _context_error(post_click_observation, fresh=True)
                post_click_target = None
                if post_click_error is None:
                    post_click_target = revalidate_target(
                        {**post_click_observation, "max_elements": element_limit},
                        decision,
                        max_elements=element_limit,
                    )
                if post_click_observation.get("status") == "timeout":
                    pass  # ``result`` already carries the honest timeout payload
                elif post_click_error is not None or post_click_target is None or post_click_target.focused is not True:
                    result = {
                        "ok": False,
                        "status": "focus_unverified",
                        "dispatched": True,
                        "reason": "the field could not be verified as focused after the guarded click; text was not typed",
                    }
                else:
                    decision.element = post_click_target
                    decision_payload = decision.to_dict()
                    result = await _run_os(dispatcher.keyboard_type, text_value, action="keyboard_type", thread_id=thread_id, lease=lease)
    elif operation == WAIT:
        # Keep the wait bounded; a desktop agent can observe and decide again.
        await asyncio.sleep(0.2)
        result = {"ok": True, "status": "ok", "dispatched": False}
    else:  # Defensive: the policy's operation vocabulary is closed.
        result = {"ok": False, "status": "invalid", "dispatched": False, "reason": f"unsupported desktop operation {operation!r}"}

    response: dict[str, Any] = {
        **_execution_summary(result, sensitive_values=(text_value, key_value, hotkey_value)),
        "action": operation,
        "decision": decision_payload,
    }
    if partial_dispatched and not response.get("ok"):
        response["partial_dispatched"] = True
    return _enrich(response, thread_id)


__all__ = ["desktop_system_one_action_tool"]
