"""Regression tests for bounded high-cardinality System One choices."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from alpha.browser.jev_policy import choose_next_action
from alpha.config.system_one_config import PROVIDER_LAYA, SystemOneConfig
from alpha.models.system_one import (
    PartitionedChoiceResult,
    SystemOneClient,
    evaluate_choice_partitioned,
)
from alpha.tools.selection import Candidate, rank_candidates


def _client(handler, **overrides: Any) -> SystemOneClient:
    settings: dict[str, Any] = {
        "provider": PROVIDER_LAYA,
        "model": "english",
        "api_key": None,
        "laya_max_choice_options": 5,
        "min_confidence": 0.5,
        "max_retries": 0,
    }
    settings.update(overrides)
    config = SystemOneConfig(**settings)
    client = SystemOneClient(config)
    transport = httpx.MockTransport(handler)
    client._get_client = lambda: httpx.AsyncClient(transport=transport, timeout=5.0)  # type: ignore[method-assign]
    return client


def _choice_answer(options: list[str], selected: str | None = None) -> dict[str, Any]:
    selected = selected or options[0]
    remainder = (1.0 - 0.8) / max(1, len(options) - 1)
    probabilities = {option: (0.8 if option == selected else remainder) for option in options}
    return {"type": "choice", "choice": selected, "probabilities": probabilities, "confidence": 0.9}


def _choice_handler(calls: list[dict[str, Any]], *, final_pick: str = "first"):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        answers: dict[str, Any] = {}
        for question_id, question in body.get("questions", {}).items():
            options = list(question.get("criteria", {}))
            assert options, "every partition must contain at least one option"
            selected = options[0] if final_pick == "first" else options[-1]
            answers[question_id] = _choice_answer(options, selected)
        return httpx.Response(200, json={"model": "test-laya", "answers": answers}, request=request)

    return handler


@pytest.mark.asyncio
async def test_partitioned_choice_keeps_all_candidates_and_bounds_each_request():
    calls: list[dict[str, Any]] = []
    client = _client(_choice_handler(calls), laya_max_choice_options=5)
    criteria = {f"candidate_{index}": f"Candidate {index}" for index in range(13)}

    result = await evaluate_choice_partitioned(
        {"request": "choose the best candidate"},
        "Choose the best candidate.",
        criteria,
        client=client,
        shortlist_per_partition=2,
        question_id="pick",
    )

    assert isinstance(result, PartitionedChoiceResult)
    assert result.value == "candidate_0"
    assert set(result.ranking) == set(criteria)
    assert result.requests == 5  # 3 partitions + 1 shortlist group + final
    assert result.rounds == 2
    assert all(len(next(iter(body["questions"].values()))["criteria"]) <= 5 for body in calls)


@pytest.mark.asyncio
async def test_partitioned_choice_returns_none_when_a_partition_is_not_confident():
    calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        question_id, question = next(iter(body["questions"].items()))
        options = list(question["criteria"])
        answer = _choice_answer(options)
        answer["confidence"] = 0.1
        return httpx.Response(200, json={"answers": {question_id: answer}}, request=request)

    client = _client(handler, laya_max_choice_options=3)
    result = await evaluate_choice_partitioned(
        "state",
        "Choose.",
        {f"option_{index}": str(index) for index in range(7)},
        client=client,
        shortlist_per_partition=1,
    )

    assert result is None
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_sixty_candidate_selection_is_bounded_and_preserves_every_id():
    calls: list[dict[str, Any]] = []
    client = _client(_choice_handler(calls), laya_max_choice_options=5, max_selection_candidates=60)
    candidates = [Candidate(id=f"candidate-{index}", title=f"Candidate {index}") for index in range(60)]

    result = await rank_candidates("choose the best candidate", candidates, top_n=60, refine=0, client=client)

    assert result is not None
    assert set(result.ids) == {candidate.id for candidate in candidates}
    assert all(len(next(iter(body["questions"].values()))["criteria"]) <= 5 for body in calls)
    assert len(calls) <= 16


@pytest.mark.asyncio
async def test_skill_and_tool_selection_uses_partitioned_laya_choices():
    calls: list[dict[str, Any]] = []
    client = _client(_choice_handler(calls), laya_max_choice_options=4)
    candidates = [Candidate(id=f"candidate-{index}", title=f"Candidate {index}") for index in range(11)]

    result = await rank_candidates(
        "choose the best candidate",
        candidates,
        top_n=5,
        refine=3,
        client=client,
    )

    assert result is not None
    assert result.jev_used is True
    assert set(result.ids) <= {candidate.id for candidate in candidates}
    assert all(len(next(iter(body["questions"].values()))["criteria"]) <= 4 for body in calls)
    assert len(calls) > 2
    assert len(result.ids) == 5
    assert set(result.ids) <= {candidate.id for candidate in candidates}


@pytest.mark.asyncio
async def test_browser_large_target_head_is_partitioned_without_css_or_coordinates():
    calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        questions = body["questions"]
        if "operation" in questions:
            operations = list(questions["operation"]["criteria"])
            selected = "CLICK"
            remainder = (1.0 - 0.8) / max(1, len(operations) - 1)
            probabilities = {option: (0.8 if option == selected else remainder) for option in operations}
            answer = {"type": "choice", "choice": selected, "probabilities": probabilities, "confidence": 0.9}
            return httpx.Response(200, json={"model": "test-laya", "answers": {"operation": answer}}, request=request)

        question_id, question = next(iter(questions.items()))
        options = list(question["criteria"])
        selected = "25" if "25" in options else options[-1]
        answer = _choice_answer(options, selected)
        return httpx.Response(200, json={"model": "test-laya", "answers": {question_id: answer}}, request=request)

    client = _client(handler, laya_max_choice_options=20)
    elements = [{"role": "button", "label": f"Button {index}"} for index in range(25)]

    decision = await choose_next_action(
        {"url": "https://example.test", "title": "Test", "text": "Choose a button", "elements": elements},
        "click the last button",
        client=client,
    )

    assert decision is not None
    assert decision.operation == "CLICK"
    assert decision.target == "25"
    assert decision.element is not None
    assert decision.element.label == "Button 24"
    assert decision.target_requests > 1
    assert decision.target_latency_ms >= 0
    assert all(len(next(iter(body["questions"].values()))["criteria"]) <= 20 for body in calls)
    assert "#" not in json.dumps(decision.to_dict())
    assert "selector" not in json.dumps(calls)
    assert "coords" not in json.dumps(calls)
    assert not decision.element.selector
    assert decision.element.coords is None


@pytest.mark.asyncio
async def test_browser_255_targets_use_bounded_partitions_and_state_projection():
    calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        questions = body["questions"]
        if "operation" in questions:
            options = list(questions["operation"]["criteria"])
            selected = "CLICK"
            remainder = (1.0 - 0.8) / max(1, len(options) - 1)
            answer = {
                "type": "choice",
                "choice": selected,
                "probabilities": {option: (0.8 if option == selected else remainder) for option in options},
                "confidence": 0.9,
            }
            return httpx.Response(200, json={"answers": {"operation": answer}}, request=request)
        question_id, question = next(iter(questions.items()))
        options = list(question["criteria"])
        selected = "255" if "255" in options else options[-1]
        answer = _choice_answer(options, selected)
        return httpx.Response(200, json={"answers": {question_id: answer}}, request=request)

    client = _client(handler, laya_max_choice_options=20, laya_max_partition_requests=16)
    elements = [{"role": "button", "label": f"Button {index}"} for index in range(255)]
    decision = await choose_next_action(
        {"url": "https://example.test", "title": "Test", "text": "Choose a button", "elements": elements},
        "click the last button",
        client=client,
    )

    assert decision is not None
    assert decision.operation == "CLICK"
    assert decision.target == "255"
    assert decision.element is not None and decision.element.label == "Button 254"
    assert decision.target_requests <= 16
    assert len(calls) <= 17  # operation request plus the bounded target tournament
    for body in calls:
        question = next(iter(body["questions"].values()))
        assert len(question["criteria"]) <= 20
        if "operation" not in body["questions"]:
            assert len(body["state"].get("elements", [])) <= 20


@pytest.mark.asyncio
async def test_browser_large_select_options_project_only_partition_options():
    calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        questions = body["questions"]
        if "operation" in questions:
            options = list(questions["operation"]["criteria"])
            selected = "SELECT"
            remainder = (1.0 - 0.8) / max(1, len(options) - 1)
            answer = {
                "type": "choice",
                "choice": selected,
                "probabilities": {option: (0.8 if option == selected else remainder) for option in options},
                "confidence": 0.9,
            }
            return httpx.Response(200, json={"answers": {"operation": answer}}, request=request)
        question_id, question = next(iter(questions.items()))
        options = list(question["criteria"])
        selected = "1:255" if "1:255" in options else options[-1]
        return httpx.Response(
            200,
            json={"answers": {question_id: _choice_answer(options, selected)}},
            request=request,
        )

    client = _client(handler, laya_max_choice_options=20)
    elements = [
        {
            "role": "select",
            "label": "Country",
            "options": [{"label": f"Country {index}"} for index in range(255)],
            "selector": "#country",
            "coords": [4, 5],
        }
    ]
    decision = await choose_next_action(
        {"url": "https://example.test", "title": "Test", "text": "Choose a country", "elements": elements},
        "choose country 255",
        client=client,
    )

    assert decision is not None
    assert decision.operation == "SELECT"
    assert decision.target == "1:255"
    assert decision.element is not None and decision.element.label == "Country"
    for body in calls:
        assert "selector" not in json.dumps(body["state"])
        assert "coords" not in json.dumps(body["state"])
        if "operation" not in body["questions"]:
            state_element = body["state"]["elements"][0]
            assert len(state_element["options"]) <= 20
            assert state_element["options_omitted"] >= 0


@pytest.mark.asyncio
async def test_partition_budget_returns_none_before_any_request():
    calls: list[dict[str, Any]] = []
    client = _client(_choice_handler(calls), laya_max_choice_options=5, laya_max_partition_requests=2)

    result = await evaluate_choice_partitioned(
        "state",
        "Choose.",
        {f"option_{index}": str(index) for index in range(11)},
        client=client,
    )

    assert result is None
    assert calls == []


@pytest.mark.asyncio
async def test_partition_deadline_stops_the_tournament():
    calls: list[dict[str, Any]] = []

    async def slow_handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        await asyncio.sleep(0.05)
        body = json.loads(request.content)
        question_id, question = next(iter(body["questions"].items()))
        options = list(question["criteria"])
        return httpx.Response(200, json={"answers": {question_id: _choice_answer(options)}}, request=request)

    client = _client(slow_handler, laya_max_choice_options=5)
    result = await evaluate_choice_partitioned(
        "state",
        "Choose.",
        {f"option_{index}": str(index) for index in range(11)},
        client=client,
        deadline=0.01,
    )

    assert result is None
    assert len(calls) == 1


def test_choice_limit_is_provider_specific():
    assert SystemOneClient(SystemOneConfig(provider=PROVIDER_LAYA, laya_max_choice_options=7)).choice_option_limit() == 7
    assert SystemOneClient(SystemOneConfig(provider="vercel-gateway", api_key="k")).choice_option_limit() == 255
