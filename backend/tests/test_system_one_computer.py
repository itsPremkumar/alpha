"""Regression tests for System One/Laya indexed Windows computer control."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from langchain_core.tools import BaseTool

from alpha.computer_use import accessibility, dispatcher
from alpha.computer_use.guard import get_sentinel_guard
from alpha.computer_use.system_one_policy import (
    CLICK,
    HOTKEY,
    TYPE_TEXT,
    build_computer_action_space,
    choose_next_computer_action,
    revalidate_target,
)
from alpha.config.system_one_config import PROVIDER_LAYA, SystemOneConfig
from alpha.models.system_one import SystemOneClient
from alpha.tools.builtins import computer_system_one_tool
from alpha.tools.builtins.computer_system_one_tool import desktop_system_one_action_tool


def _runtime(thread_id: str = "computer-thread") -> SimpleNamespace:
    return SimpleNamespace(context={"thread_id": thread_id}, state={}, config={})


def _choice(options: list[str], selected: str, confidence: float = 0.9) -> dict[str, Any]:
    remainder = (1.0 - confidence) / max(1, len(options) - 1)
    return {
        "type": "choice",
        "choice": selected,
        "probabilities": {option: (confidence if option == selected else remainder) for option in options},
        "confidence": confidence,
    }


def _client(handler, **overrides: Any) -> SystemOneClient:
    settings: dict[str, Any] = {
        "provider": PROVIDER_LAYA,
        "model": "english",
        "api_key": None,
        "shadow_mode": False,
        "enable_computer_action": True,
        "laya_max_choice_options": 20,
        "min_confidence": 0.5,
        "max_retries": 0,
    }
    settings.update(overrides)
    config = SystemOneConfig(**settings)
    client = SystemOneClient(config)
    client._get_client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)  # type: ignore[method-assign]
    return client


def _observation(elements: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "ok": True,
        "status": "ok",
        "available": True,
        "scanned": True,
        "elements": elements,
        "count": len(elements),
        "window_title": "Test",
        "matched_window": "Test",
    }


@pytest.mark.asyncio
async def test_computer_policy_abstains_by_default_until_explicitly_enabled() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500, request=request)

    client = _client(handler)
    client.config.enable_computer_action = False
    decision = await choose_next_computer_action(
        _observation([{"name": "Save", "type": "Button", "bbox": [1, 1, 5, 5]}]),
        "save",
        client=client,
    )
    assert decision is None
    assert called is False


def test_action_space_strips_geometry_from_public_state() -> None:
    space = build_computer_action_space(
        [
            {"name": "Save", "type": "Button", "bbox": [10, 20, 30, 40], "window": "Editor"},
            {"name": "Name", "type": "Edit", "bbox": [50, 60, 150, 80], "window": "Editor"},
            {"name": "bad", "type": "Button", "bbox": [1, 1, 1, 2]},
        ]
    )
    assert [element.index for element in space.elements] == ["1", "2"]
    assert space.targets[CLICK].keys() == {"1", "2"}
    assert space.targets[TYPE_TEXT].keys() == {"2"}
    serialized = json.dumps([element.public_state() for element in space.elements])
    assert "bbox" not in serialized
    assert "center" not in serialized
    assert "selector" not in serialized


def test_action_space_does_not_make_static_text_executable() -> None:
    space = build_computer_action_space(
        [
            {"name": "Status: ready", "type": "Static", "bbox": [1, 1, 20, 20]},
            {"name": "Save", "type": "Button", "bbox": [30, 30, 50, 50]},
            {"name": "Legacy control", "type": "CustomControl", "bbox": [60, 60, 80, 80]},
        ]
    )
    assert [element.name for element in space.elements] == ["Save"]
    assert space.truncated is False


def test_action_space_only_marks_truncation_after_an_executable_tail() -> None:
    static_tail = build_computer_action_space(
        [
            {"name": "Save", "type": "Button", "bbox": [30, 30, 50, 50]},
            *[{"name": f"label {index}", "type": "Static", "bbox": [index, 0, index + 1, 1]} for index in range(5)],
        ],
        max_elements=1,
    )
    assert static_tail.truncated is False

    executable_tail = build_computer_action_space(
        [
            {"name": "Save", "type": "Button", "bbox": [30, 30, 50, 50]},
            {"name": "Cancel", "type": "Button", "bbox": [60, 60, 80, 80]},
        ],
        max_elements=1,
    )
    assert executable_tail.truncated is True


def test_computer_policy_rejects_a_truncated_source_before_http() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500, request=request)

    observation = _observation(
        [
            {"name": "Save", "type": "Button", "bbox": [30, 30, 50, 50]},
            {"name": "Cancel", "type": "Button", "bbox": [60, 60, 80, 80]},
        ]
    )
    observation["max_elements"] = 1
    decision = asyncio.run(choose_next_computer_action(observation, "save", client=_client(handler)))
    assert decision is None
    assert called is False


def test_laya_rejects_an_incomplete_projection_before_http() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500, request=request)

    elements = [{"name": f"Button {index}", "type": "Button", "bbox": [index, 0, index + 5, 5]} for index in range(41)]
    decision = asyncio.run(choose_next_computer_action(_observation(elements), "click something", client=_client(handler)))
    assert decision is None
    assert called is False


@pytest.mark.asyncio
async def test_computer_policy_returns_index_and_never_sends_geometry() -> None:
    calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        answers: dict[str, Any] = {}
        for qid, question in body["questions"].items():
            options = list(question["criteria"])
            selected = "2" if qid == "click_target" else "CLICK"
            answers[qid] = _choice(options, selected)
        return httpx.Response(200, json={"model": "test-laya", "answers": answers}, request=request)

    decision = await choose_next_computer_action(
        _observation(
            [
                {"name": "Cancel", "type": "Button", "bbox": [1, 1, 10, 10], "window": "Test"},
                {"name": "Save", "type": "Button", "bbox": [20, 20, 40, 40], "window": "Test"},
            ]
        ),
        "save the document with SECRET TEXT VALUE using f7 or ctrl+s; my password is hunter2",
        text="SECRET TEXT VALUE",
        key="f7",
        hotkey="ctrl+s",
        client=_client(handler),
    )

    assert decision is not None
    assert decision.operation == CLICK
    assert decision.target == "2"
    assert decision.element is not None
    assert decision.element.center == (30, 30)
    serialized = json.dumps(calls)
    assert "bbox" not in serialized
    assert "center" not in serialized
    assert all("selector" not in element for element in calls[0]["state"]["elements"])
    assert "1, 1" not in serialized
    assert "selector" not in json.dumps(decision.to_dict())
    assert "SECRET TEXT VALUE" not in serialized
    assert "f7" not in serialized
    assert "ctrl+s" not in serialized
    assert decision.element is not None
    assert decision.element.public_state() == {
        "index": "2",
        "name": "Save",
        "type": "button",
        "window": "Test",
        "operations": ["CLICK"],
        "checked": None,
        "disabled": None,
        "expanded": None,
        "secret": False,
        "focused": None,
    }


@pytest.mark.asyncio
async def test_large_computer_target_head_is_partitioned_and_projected() -> None:
    calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        questions = body["questions"]
        if "operation" in questions:
            options = list(questions["operation"]["criteria"])
            return httpx.Response(
                200,
                json={"answers": {"operation": _choice(options, CLICK)}, "model": "test-laya"},
                request=request,
            )
        qid, question = next(iter(questions.items()))
        options = list(question["criteria"])
        selected = "25" if "25" in options else options[-1]
        return httpx.Response(200, json={"answers": {qid: _choice(options, selected)}}, request=request)

    elements = [{"name": f"Button {index}", "type": "Button", "bbox": [index, 0, index + 5, 5]} for index in range(25)]
    decision = await choose_next_computer_action(
        _observation(elements),
        "click the last button",
        client=_client(handler, laya_max_partition_requests=16),
    )

    assert decision is not None
    assert decision.target == "25"
    assert decision.target_requests > 1
    assert all(len(next(iter(body["questions"].values()))["criteria"]) <= 20 for body in calls)
    for body in calls:
        if "operation" not in body["questions"]:
            assert len(body["state"]["elements"]) <= 20
    assert "bbox" not in json.dumps(calls)


def test_revalidation_rejects_multiple_matched_windows() -> None:
    decision = asyncio.run(_async_decision())
    observation = _observation([{"name": "Save", "type": "Button", "bbox": [100, 100, 120, 120], "window": "One"}])
    observation["matched_windows"] = ["One", "Two"]
    assert revalidate_target(observation, decision) is None


def test_execution_summary_redacts_a_bare_coordinate_reason() -> None:
    summary = computer_system_one_tool._execution_summary({"ok": False, "status": "failed", "dispatched": False, "reason": "backend failed at 100, 200"})
    assert "100" not in summary["reason"]
    assert "200" not in summary["reason"]


def test_execution_summary_redacts_caller_supplied_values() -> None:
    summary = computer_system_one_tool._execution_summary(
        {"ok": False, "status": "failed", "dispatched": False, "reason": "could not type alice@example.com or press ctrl+s"},
        sensitive_values=("alice@example.com", "ctrl+s"),
    )
    assert "alice@example.com" not in summary["reason"]
    assert "ctrl+s" not in summary["reason"]


@pytest.mark.asyncio
async def test_computer_policy_rejects_a_malformed_observation() -> None:
    client = _client(lambda request: httpx.Response(500, request=request))
    assert await choose_next_computer_action([], "save", client=client) is None


@pytest.mark.asyncio
async def test_shadow_mode_returns_none_without_exposing_a_target() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        body = json.loads(request.content)
        answers = {}
        for qid, question in body["questions"].items():
            options = list(question["criteria"])
            answers[qid] = _choice(options, options[0])
        return httpx.Response(200, json={"answers": answers}, request=request)

    client = _client(handler, shadow_mode=True)
    decision = await choose_next_computer_action(
        _observation([{"name": "Save", "type": "Button", "bbox": [1, 1, 5, 5]}]),
        "save",
        client=client,
    )
    assert decision is None
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_tool_executes_indexed_click_without_returning_coordinates(monkeypatch: pytest.MonkeyPatch) -> None:
    guard = get_sentinel_guard()
    guard.reset()
    fake = SimpleNamespace(position=(500, 500), calls=[])

    class Backend:
        name = "fake"

        def pointer_position(self):
            return fake.position

        def click(self, x, y, button, clicks):
            fake.calls.append(("click", x, y, button, clicks))

        def type_text(self, text, interval_ms):
            fake.calls.append(("type", text, interval_ms))

        def hotkey(self, keys):
            fake.calls.append(("hotkey", tuple(keys)))

        def press(self, key):
            fake.calls.append(("press", key))

        def scroll(self, clicks, direction):
            fake.calls.append(("scroll", clicks, direction))

    monkeypatch.setattr(dispatcher, "_load_backend", lambda: (Backend(), None))
    monkeypatch.setattr(
        computer_system_one_tool,
        "choose_next_computer_action",
        _async_decision,
    )
    scans = {"count": 0}

    def _scan(title="", limit=200, *, semantic_only=False):
        scans["count"] += 1
        return _observation([{"name": "Save", "type": "Button", "bbox": [100, 100, 120, 120], "window": "Test"}])

    monkeypatch.setattr(accessibility, "inspect_ui_tree", _scan)

    result = await desktop_system_one_action_tool.coroutine(runtime=_runtime(), goal="save")
    assert result["ok"] is True
    assert result["action"] == CLICK
    assert result["decision"]["target"] == "1"
    assert result["dispatched"] is True
    assert "x" not in result
    assert "y" not in result
    assert fake.calls == [("click", 110, 110, "left", 1)]
    assert scans["count"] == 2
    assert guard.active_leases() == ["os-computer:computer-thread"]


@pytest.mark.asyncio
async def test_tool_rejects_an_ambiguous_multi_window_observation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        accessibility,
        "inspect_ui_tree",
        lambda title="", limit=200, *, semantic_only=False: _observation(
            [
                {"name": "Save", "type": "Button", "bbox": [1, 1, 5, 5], "window": "One"},
                {"name": "Save", "type": "Button", "bbox": [10, 10, 20, 20], "window": "Two"},
            ]
        ),
    )
    result = await desktop_system_one_action_tool.coroutine(runtime=_runtime(), goal="save")
    assert result["status"] == "ambiguous"
    assert result["dispatched"] is False


@pytest.mark.asyncio
async def test_tool_rejects_a_target_that_changed_before_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    get_sentinel_guard().reset()
    scans = {"count": 0}

    def _scan(title="", limit=200, *, semantic_only=False):
        scans["count"] += 1
        name = "Save" if scans["count"] == 1 else "Delete"
        return _observation([{"name": name, "type": "Button", "bbox": [100, 100, 120, 120], "window": "Test"}])

    monkeypatch.setattr(accessibility, "inspect_ui_tree", _scan)
    monkeypatch.setattr(computer_system_one_tool, "choose_next_computer_action", _async_decision)

    result = await desktop_system_one_action_tool.coroutine(runtime=_runtime("stale-thread"), goal="save")
    assert result["ok"] is False
    assert result["status"] == "stale_target"
    assert result["dispatched"] is False
    assert get_sentinel_guard().active_leases() == []


async def _async_decision(*args: Any, **kwargs: Any):
    from alpha.computer_use.system_one_policy import ComputerDecision, build_computer_action_space

    space = build_computer_action_space([{"name": "Save", "type": "Button", "bbox": [100, 100, 120, 120], "window": "Test"}])
    element = space.elements[0]
    return ComputerDecision(operation=CLICK, target="1", element=element, confidence=0.9)


async def _async_type_decision(*args: Any, **kwargs: Any):
    from alpha.computer_use.system_one_policy import ComputerDecision, build_computer_action_space

    space = build_computer_action_space([{"name": "Email", "type": "Edit", "bbox": [100, 100, 120, 120], "window": "Test"}])
    return ComputerDecision(operation=TYPE_TEXT, target="1", element=space.elements[0], confidence=0.9)


async def _async_hotkey_decision(*args: Any, **kwargs: Any):
    from alpha.computer_use.system_one_policy import ComputerDecision

    return ComputerDecision(operation=HOTKEY, confidence=0.9)


def test_tool_schema_is_bare_runtime_and_has_no_geometry_arguments() -> None:
    assert isinstance(desktop_system_one_action_tool, BaseTool)
    parameters = list(desktop_system_one_action_tool.coroutine.__annotations__.items())
    assert parameters[0][0] == "runtime"
    schema = desktop_system_one_action_tool.tool_call_schema.model_json_schema()
    properties = schema.get("properties", {})
    assert "runtime" not in properties
    assert "x" not in properties and "y" not in properties
    assert "selector" not in properties
    assert "bbox" not in properties
    assert "key" in properties
    assert "hotkey" in properties
    assert "confirmed" not in properties
    assert "text" in properties
    assert "coordinates" not in properties


def test_semantic_scanner_filters_static_controls_and_redacts_edit_values(monkeypatch: pytest.MonkeyPatch) -> None:
    class Rect:
        left, top, right, bottom = 0, 0, 20, 20

        def width(self) -> int:
            return 20

        def height(self) -> int:
            return 20

    class Info:
        def __init__(self, name: str, automation_id: str = "") -> None:
            self.name = name
            self.automation_id = automation_id
            self.is_password = False

    class Element:
        def __init__(self, control_type: str, label: str, *, automation_id: str = "", focused: bool = False) -> None:
            self.control_type = control_type
            self.label = label
            self.info = Info(label, automation_id)
            self._focused = focused

        def friendly_class_name(self) -> str:
            return self.control_type

        def element_info(self) -> Info:
            return self.info

        def window_text(self) -> str:
            return self.label

        def rectangle(self) -> Rect:
            return Rect()

        def has_keyboard_focus(self) -> bool:
            return self._focused

    class Window:
        def descendants(self):
            return [
                Element("Button", "Save"),
                Element("Static", "private document contents"),
                Element("Edit", "alice@example.com", automation_id="email", focused=True),
                Element("RichEdit", "private draft contents", automation_id="notes", focused=False),
                Element("CustomControl", "unknown"),
            ]

    module = object()
    monkeypatch.setattr(accessibility, "_backend_gate", lambda: (module, None))
    monkeypatch.setattr(accessibility, "_iter_windows", lambda _module, _title: iter([(Window(), "Editor")]))
    observation = accessibility.inspect_ui_tree(max_elements=10, semantic_only=True)

    assert observation["ok"] is True
    assert [(item["type"], item["name"]) for item in observation["elements"]] == [
        ("Button", "Save"),
        ("Edit", "email"),
        ("RichEdit", "notes"),
    ]
    assert observation["elements"][1]["focused"] is True
    serialized = json.dumps(observation)
    assert "alice@example.com" not in serialized
    assert "private document contents" not in serialized
    assert "private draft contents" not in serialized


def test_keyboard_press_cannot_bypass_the_hotkey_blacklist(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class Backend:
        name = "fake"

        def pointer_position(self):
            return (10, 10)

        def press(self, key: str) -> None:
            calls.append(key)

    monkeypatch.setattr(dispatcher, "_load_backend", lambda: (Backend(), None))
    result = dispatcher.keyboard_press("win+l")

    assert result["ok"] is False
    assert result["status"] == "invalid"
    assert calls == []


@pytest.mark.asyncio
async def test_tool_rechecks_focus_before_typing(monkeypatch: pytest.MonkeyPatch) -> None:
    get_sentinel_guard().reset()
    calls: list[tuple] = []

    class Backend:
        name = "fake"

        def pointer_position(self):
            return (500, 500)

        def click(self, x, y, button, clicks):
            calls.append(("click", x, y, button, clicks))

        def type_text(self, text, interval_ms):
            calls.append(("type", text, interval_ms))

    monkeypatch.setattr(dispatcher, "_load_backend", lambda: (Backend(), None))
    monkeypatch.setattr(computer_system_one_tool, "choose_next_computer_action", _async_type_decision)
    scans = {"count": 0}

    def _scan(title="", limit=200, *, semantic_only=False):
        scans["count"] += 1
        return _observation([{"name": "Email", "type": "Edit", "bbox": [100, 100, 120, 120], "window": "Test", "focused": False}])

    monkeypatch.setattr(accessibility, "inspect_ui_tree", _scan)
    result = await desktop_system_one_action_tool.coroutine(runtime=_runtime("type-thread"), goal="fill email", text="alice@example.com")

    assert result["ok"] is False
    assert result["status"] == "focus_unverified"
    assert result["partial_dispatched"] is True
    assert calls == [("click", 110, 110, "left", 1)]


@pytest.mark.asyncio
async def test_tool_rechecks_focus_before_keyboard_action(monkeypatch: pytest.MonkeyPatch) -> None:
    get_sentinel_guard().reset()
    scans = {"count": 0}

    def _scan(title="", limit=200, *, semantic_only=False):
        scans["count"] += 1
        return _observation([{"name": "Save", "type": "Button", "bbox": [100, 100, 120, 120], "window": "Test", "focused": False}])

    monkeypatch.setattr(accessibility, "inspect_ui_tree", _scan)
    monkeypatch.setattr(computer_system_one_tool, "choose_next_computer_action", _async_hotkey_decision)
    result = await desktop_system_one_action_tool.coroutine(runtime=_runtime("hotkey-thread"), goal="save", hotkey="ctrl+s")

    assert result["status"] == "focus_unverified"
    assert result["dispatched"] is False
    assert scans["count"] == 2
