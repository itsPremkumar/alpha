"""Tests for the page-freshness guard and change-aware agent loop.

Ported from browser-use/jev-ultrafast (MIT). Two properties matter:

1. A decision made against one page must not be executed against a different
   one. That failure is silent — the click reports success — so it has to be
   caught before input, not diagnosed afterwards.
2. An action that succeeded but changed nothing is not progress. Detecting a run
   of those is what stops the agent from trying three different things that each
   do nothing.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from alpha.browser.executor import ScriptedExecutor
from alpha.browser.freshness import (
    change_evidence,
    describe_change,
    element_guards,
    element_signature,
    is_fresh,
    page_fingerprint,
)
from alpha.browser.jev_agent import (
    STATUS_BLOCKED,
    STATUS_DONE,
    STATUS_MAX_STEPS,
    STATUS_NO_PROGRESS,
    STATUS_REPEATED,
    STATUS_STALE,
    STATUS_TIMEOUT,
    BrowserAgent,
)
from alpha.config.system_one_config import SystemOneConfig
from alpha.models.system_one import SystemOneClient

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

OPS = ("CLICK", "TYPE_TEXT", "SCROLL_UP", "SCROLL_DOWN", "WAIT", "DONE", "BLOCKED")


def page(url: str = "https://example.com", text: str = "Welcome", elements: list | None = None) -> dict:
    return {
        "url": url,
        "title": "Example",
        "text": text,
        "elements": elements
        if elements is not None
        else [
            {"tag": "button", "text": "Submit", "selector": "#s", "coords": [10, 10]},
            {"tag": "input", "placeholder": "Email", "selector": "#e", "value": ""},
        ],
    }


def _choice(operation: str, confidence: float = 0.95) -> dict:
    others = [o for o in OPS if o != operation]
    share = round(0.02 / len(others), 6)
    probabilities = {o: share for o in others}
    probabilities[operation] = round(1.0 - sum(probabilities.values()), 6)
    return {"type": "choice", "choice": operation, "probabilities": probabilities, "confidence": confidence}


def _agent_client(operation: str, target: str | None = "1", *, record: bool = False) -> SystemOneClient:
    answers: dict = {"operation": _choice(operation)}
    if target is not None:
        answers["click_target"] = {"type": "choice", "choice": target, "probabilities": {"1": 0.9, "2": 0.1}, "confidence": 0.95}
        answers["type_text_target"] = {"type": "choice", "choice": target, "probabilities": {"1": 0.9, "2": 0.1}, "confidence": 0.95}
    payload = {"model": "jev", "answers": answers}
    # Recording is off by default so that only the tests that assert on the log
    # touch it; a recorder pointed at the default path would otherwise pick up
    # decisions from every other test in this file.
    client = SystemOneClient(SystemOneConfig(api_key="k", record_decisions=record))
    fake = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload, request=request)),
        timeout=5.0,
    )
    client._get_client = lambda: fake  # type: ignore[method-assign]
    return client


# --------------------------------------------------------------------------
# fingerprints
# --------------------------------------------------------------------------


def test_fingerprint_is_stable_for_the_same_page():
    assert page_fingerprint(page()) == page_fingerprint(page())


def test_fingerprint_ignores_coordinates():
    """Elements move as the page reflows; that must not look like a change."""
    moved = page(elements=[
        {"tag": "button", "text": "Submit", "selector": "#s", "coords": [999, 999]},
        {"tag": "input", "placeholder": "Email", "selector": "#e", "value": ""},
    ])
    assert page_fingerprint(page()) == page_fingerprint(moved)


@pytest.mark.parametrize(
    "changed_page",
    [
        page(url="https://example.com/next"),
        page(text="Something else entirely"),
        page(elements=[{"tag": "button", "text": "Cancel", "selector": "#s"}]),
    ],
    ids=["url", "text", "elements"],
)
def test_fingerprint_changes_when_the_page_changes(changed_page):
    assert page_fingerprint(page()) != page_fingerprint(changed_page)


def test_fingerprint_handles_scroll_and_empty_pages():
    scrolled = {**page(), "scroll": {"y": 400}}
    assert page_fingerprint(page()) != page_fingerprint(scrolled)
    assert isinstance(page_fingerprint({}), str)


# --------------------------------------------------------------------------
# element signatures
# --------------------------------------------------------------------------


def test_signature_is_stable_and_detects_a_filled_field():
    element = {"index": "1", "role": "textbox", "label": "Email", "value": ""}
    filled = {**element, "value": "a@b.com"}
    assert element_signature(element) == element_signature(element)
    assert element_signature(element) != element_signature(filled)


def test_signature_detects_disabled_and_checked():
    element = {"index": "1", "role": "checkbox", "label": "Remember"}
    assert element_signature(element) != element_signature({**element, "disabled": True})
    assert element_signature(element) != element_signature({**element, "checked": "true"})


def test_text_and_label_are_treated_as_the_same_field():
    """Adapters disagree on the key; that must not read as a change."""
    assert element_signature({"index": "1", "role": "button", "text": "Go"}) == element_signature(
        {"index": "1", "role": "button", "label": "Go"}
    )


def test_guards_are_keyed_by_index():
    """Keyed by position, matching how the action space numbers elements."""
    guards = element_guards(page())
    assert set(guards) == {"1", "2"}


def test_guards_fall_back_to_position_when_no_index_is_present():
    """Real page_state dicts have no `index` field; position is the index."""
    assert set(element_guards({"elements": [{"role": "button"}]})) == {"1"}


def test_guards_prefer_an_explicit_index_when_one_is_supplied():
    guards = element_guards({"elements": [{"index": "7", "role": "button", "label": "Go"}]})
    assert set(guards) == {"7"}


def _dropdown(*labels: str) -> dict:
    return {
        "elements": [
            {
                "tag": "select",
                "selector": "#c",
                "options": [{"label": label, "value": label.lower()} for label in labels],
            }
        ]
    }


def test_dropdown_options_are_guarded_too():
    """A SELECT targets "1:2"; without an option guard it is the unguarded case."""
    guards = element_guards(_dropdown("United States", "Germany"))
    assert set(guards) == {"1", "1:1", "1:2"}


def test_a_reordered_dropdown_invalidates_the_option_guard():
    """Same label at a different offset is a different target."""
    before = element_guards(_dropdown("United States", "Germany"))
    after = element_guards(_dropdown("Germany", "United States"))
    assert before["1:1"] != after["1:1"]
    assert is_fresh(_dropdown("United States", "Germany"), _dropdown("Germany", "United States"), "1:1") is False


def test_a_renamed_option_invalidates_that_option():
    before = element_guards(_dropdown("Yes"))
    after = element_guards(_dropdown("No"))
    assert before["1:1"] != after["1:1"]


def test_the_same_label_in_two_dropdowns_is_not_the_same_target():
    """Options are scoped to their parent element, not global."""
    page_with_two = {
        "elements": [
            {"tag": "select", "selector": "#a", "options": [{"label": "Yes", "value": "y"}]},
            {"tag": "select", "selector": "#b", "options": [{"label": "Yes", "value": "y"}]},
        ]
    }
    guards = element_guards(page_with_two)
    assert guards["1:1"] != guards["2:1"]


def test_an_element_without_options_has_no_option_guards():
    assert set(element_guards({"elements": [{"tag": "button", "label": "Go"}]})) == {"1"}


# --------------------------------------------------------------------------
# is_fresh
# --------------------------------------------------------------------------


def test_same_page_is_fresh():
    assert is_fresh(page(), page()) is True


def test_changed_page_is_not_fresh():
    assert is_fresh(page(), page(text="other")) is False


def test_missing_observation_is_not_fresh():
    assert is_fresh(page(), {}) is False
    assert is_fresh({}, page()) is False


def test_target_guard_survives_an_unrelated_page_change():
    """The page may re-render around a target that is still perfectly valid."""
    observed = page()
    current = page(text="Freshly loaded results")
    assert is_fresh(observed, current) is False  # whole page moved
    assert is_fresh(observed, current, "1") is True  # but target 1 is untouched


def test_target_guard_catches_the_target_being_replaced():
    observed = page()
    current = page(elements=[
        {"tag": "button", "text": "Delete account", "selector": "#s"},
        {"tag": "input", "placeholder": "Email", "selector": "#e", "value": ""},
    ])
    assert is_fresh(observed, current, "1") is False


def test_target_guard_fails_open_on_an_unknown_index():
    """A guard that fails closed turns a data problem into a stuck agent."""
    assert is_fresh(page(), page(), "99") is True


def test_describe_change_names_the_actual_change():
    assert "navigated" in describe_change(page(), page(url="https://x.test/"))
    assert "element count" in describe_change(page(), page(elements=[]))
    assert "text changed" in describe_change(page(), page(text="new"))
    assert describe_change(page(), {}) == "no current observation"


def test_describe_change_is_quiet_when_nothing_changed():
    """It is only reached on a page already known to have moved, but the total
    function still has to answer."""
    assert describe_change(page(), page()) == "no change"


def test_describe_change_names_the_target_when_given_one():
    """A replaced target described as a whole-page change sends the reader looking
    in the wrong place."""
    observed = page()
    replaced = page(elements=[
        {"tag": "button", "text": "Delete account", "selector": "#s"},
        {"tag": "input", "placeholder": "Email", "selector": "#e", "value": ""},
    ])
    assert describe_change(observed, replaced, "1") == "the target element changed"
    # Without the index it can only report the coarser fact.
    assert describe_change(observed, replaced) == "page content changed"


# --------------------------------------------------------------------------
# evidence chain: "it changed" is not evidence of what changed
# --------------------------------------------------------------------------


def test_evidence_is_empty_for_an_unchanged_page():
    assert change_evidence(page(), page()) == []


def test_evidence_is_empty_when_there_is_nothing_to_compare():
    """A missing observation is "cannot tell", never "nothing changed"."""
    assert change_evidence(page(), {}) == []
    assert change_evidence({}, page()) == []


def test_evidence_orders_navigation_first():
    """A navigation also alters text; knowing it navigated is the fact that matters."""
    observed = page()
    current = page(url="https://example.com/next", text="Next page")
    assert change_evidence(observed, current) == ["url", "text"]


def test_evidence_orders_element_count_before_text():
    observed = page()
    current = page(text="fewer things", elements=[{"tag": "button", "text": "Submit", "selector": "#s"}])
    assert change_evidence(observed, current) == ["element_count", "text"]


def test_evidence_omits_the_target_when_it_is_untouched():
    """The page may re-render around a target that is still perfectly valid."""
    observed = page()
    current = page(text="results loaded")
    assert change_evidence(observed, current, "1") == ["text"]


def test_evidence_names_the_target_when_only_the_target_moved():
    observed = page()
    replaced = page(elements=[
        {"tag": "button", "text": "Delete account", "selector": "#s"},
        {"tag": "input", "placeholder": "Email", "selector": "#e", "value": ""},
    ])
    assert change_evidence(observed, replaced, "1") == ["target"]


def test_evidence_falls_back_to_content_for_a_change_no_named_check_sees():
    """A filled field changes the fingerprint but not the url, text, or count."""
    observed = page()
    filled = page(elements=[
        {"tag": "button", "text": "Submit", "selector": "#s", "coords": [10, 10]},
        {"tag": "input", "placeholder": "Email", "selector": "#e", "value": "a@b.com"},
    ])
    assert change_evidence(observed, filled) == ["content"]


def test_evidence_sees_a_scroll_as_a_content_change():
    assert change_evidence(page(), {**page(), "scroll": {"y": 400}}) == ["content"]


def test_evidence_content_is_a_catch_all_not_an_addition():
    """It only fires when nothing more specific did, so the list stays a ladder."""
    observed = page()
    current = page(url="https://example.com/next", text="Next", elements=[{"tag": "button", "text": "Go"}])
    evidence = change_evidence(observed, current)
    assert evidence[0] == "url"
    assert "content" not in evidence


# --------------------------------------------------------------------------
# href is part of a link's identity
# --------------------------------------------------------------------------


def _link(href: str) -> dict:
    return page(elements=[{"tag": "a", "text": "Next", "href": href, "selector": "#n"}])


def test_a_retargeted_link_is_a_different_target():
    """The same label pointing somewhere else is a different link.

    This is the re-rendered-list case the whole module exists for: a paginated
    list where "Next" now goes to page 3 while the label is unchanged. Without
    `href` in the signature the target guard sees no difference and the click
    silently goes to the wrong page.
    """
    assert is_fresh(_link("/page/2"), _link("/page/3"), "1") is False


def test_an_unchanged_link_stays_fresh():
    assert is_fresh(_link("/page/2"), _link("/page/2"), "1") is True


def test_a_retargeted_link_changes_the_page_fingerprint():
    """It is in the whole-page fingerprint too — the destination is not cosmetic."""
    assert page_fingerprint(_link("/page/2")) != page_fingerprint(_link("/page/3"))


def test_evidence_reports_a_retargeted_link_as_a_target_change():
    assert change_evidence(_link("/page/2"), _link("/page/3"), "1") == ["target"]


def test_a_link_without_an_href_signs_the_same_as_before():
    """Adding `href` to the signature fields must not move any existing signature."""
    plain = {"role": "link", "label": "Next", "selector": "#n"}
    assert element_signature(plain) == element_signature({**plain, "href": ""})


# --------------------------------------------------------------------------
# agent: change detection
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scripted_executor_reports_unknown_change_by_default():
    """A replay cannot know, and must not claim 'nothing changed'."""
    executor = ScriptedExecutor(pages=[page(), page()])
    agent = BrowserAgent(executor, client=_agent_client("CLICK"), scan_injection=False, max_steps=1)
    run = await agent.run("submit the form")
    assert run.steps
    assert run.steps[0].changed is None
    assert run.steps[0].to_dict()["changed"] is None


@pytest.mark.asyncio
async def test_agent_blocks_after_consecutive_ineffective_actions():
    """Different actions that all do nothing is still a loop."""
    executor = ScriptedExecutor(pages=[page()] * 10, changes=[False], tracks_changes=True)
    agent = BrowserAgent(
        executor,
        client=_agent_client("CLICK"),
        scan_injection=False,
        max_steps=10,
        ineffective_limit=3,
    )
    run = await agent.run("submit the form")
    assert run.status == STATUS_NO_PROGRESS
    assert len(run.steps) == 3
    assert "changed nothing" in run.detail


@pytest.mark.asyncio
async def test_an_effective_action_resets_the_counter():
    """One action that worked buys the run another `ineffective_limit` steps."""
    executor = ScriptedExecutor(pages=[page()] * 10, changes=[False, False, True, False, False], tracks_changes=True)
    agent = BrowserAgent(
        executor,
        client=_agent_client("CLICK"),
        scan_injection=False,
        max_steps=5,
        ineffective_limit=3,
        # The step is identical every time, so the *repeat* detector would fire
        # first and mask the detector under test.
        repeat_limit=99,
    )
    run = await agent.run("submit the form")
    # The True at step 3 resets the run, so 5 steps fit inside the budget.
    assert run.status == STATUS_MAX_STEPS
    assert len(run.steps) == 5


@pytest.mark.asyncio
async def test_wait_does_not_count_as_ineffective():
    """Waiting is supposed to change nothing; it must not trip the detector."""
    executor = ScriptedExecutor(pages=[page()] * 10, changes=[False], tracks_changes=True)
    agent = BrowserAgent(
        executor,
        client=_agent_client("WAIT", target=None),
        scan_injection=False,
        max_steps=4,
        ineffective_limit=3,
        repeat_limit=99,
    )
    run = await agent.run("wait for results")
    assert run.status == STATUS_MAX_STEPS, "WAIT steps should never be called a no-progress loop"


# --------------------------------------------------------------------------
# agent: the step record carries what changed, not just whether
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_step_records_which_checks_saw_the_page_move():
    """`changed=true` alone is not diagnosable; the evidence is."""
    executor = ScriptedExecutor(
        pages=[page(), page(url="https://example.com/results", text="Results")],
        tracks_changes=True,
    )
    agent = BrowserAgent(executor, client=_agent_client("CLICK"), scan_injection=False, max_steps=1)
    run = await agent.run("submit the form")

    step = run.steps[0]
    assert step.changed is True
    assert step.evidence == ["url", "text"]
    assert step.to_dict()["evidence"] == ["url", "text"]


@pytest.mark.asyncio
async def test_a_step_with_no_change_carries_no_evidence():
    executor = ScriptedExecutor(pages=[page()] * 4, tracks_changes=True)
    agent = BrowserAgent(
        executor,
        client=_agent_client("CLICK"),
        scan_injection=False,
        max_steps=1,
        ineffective_limit=99,
    )
    run = await agent.run("submit the form")

    assert run.steps[0].changed is False
    assert run.steps[0].evidence == []


@pytest.mark.asyncio
async def test_the_policy_is_told_what_changed():
    """Evidence the policy cannot see is evidence the policy cannot act on."""
    from alpha.browser.jev_policy import choose_next_action

    seen: dict = {}

    class _Recorder:
        class config:  # noqa: N801 - mirrors the client's shape
            enabled = True
            enable_browser_action = True

        @staticmethod
        def is_available() -> bool:
            return True

        @staticmethod
        def threshold_for(_tier: Any) -> float:
            return 0.5

        @staticmethod
        async def evaluate(state: dict, _questions: dict, **_kwargs: Any):
            seen.update(state)
            return None

    await choose_next_action(
        page(),
        "submit the form",
        [{"operation": "CLICK", "target": "1", "ok": True, "changed": True, "evidence": ["url", "text"]}],
        client=_Recorder(),
    )

    recent = seen["recent_actions"]
    assert recent[0]["evidence"] == ["url", "text"]


@pytest.mark.asyncio
async def test_the_policy_omits_evidence_when_there_is_none():
    from alpha.browser.jev_policy import choose_next_action

    seen: dict = {}

    class _Recorder:
        class config:  # noqa: N801 - mirrors the client's shape
            enabled = True
            enable_browser_action = True

        @staticmethod
        def is_available() -> bool:
            return True

        @staticmethod
        def threshold_for(_tier: Any) -> float:
            return 0.5

        @staticmethod
        async def evaluate(state: dict, _questions: dict, **_kwargs: Any):
            seen.update(state)
            return None

    await choose_next_action(
        page(),
        "submit the form",
        [{"operation": "CLICK", "target": "1", "ok": True, "changed": None}],
        client=_Recorder(),
    )

    assert "evidence" not in seen["recent_actions"][0]


class _FlakyObserver:
    """Observes once, then cannot — the page went away mid-run."""

    tracks_changes = True

    def __init__(self, first: dict) -> None:
        self._first = first
        self._calls = 0

    async def fingerprint(self) -> None:
        return None

    async def observe(self) -> dict:
        self._calls += 1
        if self._calls == 1:
            return self._first
        raise RuntimeError("page closed")

    async def act(self, operation: str, target: str | None, text: str, element: Any) -> dict:
        return {"ok": True, "detail": "clicked"}


@pytest.mark.asyncio
async def test_a_failed_re_observation_is_not_reported_as_no_change():
    """A page we could not look at is not a page that did not change.

    Substituting the previous observation and comparing it to itself would report
    `changed=False`, and a run of those trips the no-progress detector on a page
    that may be moving perfectly well.
    """
    executor = _FlakyObserver(page())
    agent = BrowserAgent(
        executor,
        client=_agent_client("CLICK"),
        scan_injection=False,
        max_steps=3,
        ineffective_limit=2,
    )
    run = await agent.run("submit the form")

    assert run.steps[0].changed is None, "unobservable is not the same as unchanged"
    assert run.steps[0].evidence == []
    assert run.status == STATUS_MAX_STEPS, "an unobservable page must not read as a no-progress loop"


# --------------------------------------------------------------------------
# agent: staleness
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_redecides_when_the_page_keeps_moving():
    """DONE is a claim about a page; if the page moved, the claim is void."""
    pages = [page(text="one"), page(text="two"), page(text="three")]
    executor = ScriptedExecutor(pages=pages)
    agent = BrowserAgent(
        executor,
        client=_agent_client("DONE", target=None),
        scan_injection=False,
        max_steps=6,
        stale_retries=1,
    )
    run = await agent.run("finish")
    assert run.status == STATUS_STALE


@pytest.mark.asyncio
async def test_a_settled_page_still_reaches_done():
    """The guard must not make a normal run impossible."""
    executor = ScriptedExecutor(pages=[page()] * 6)
    agent = BrowserAgent(executor, client=_agent_client("DONE", target=None), scan_injection=False, max_steps=6)
    run = await agent.run("finish")
    assert run.status == STATUS_DONE


@pytest.mark.asyncio
async def test_staleness_check_can_be_switched_off():
    pages = [page(text="one"), page(text="two"), page(text="three")]
    executor = ScriptedExecutor(pages=pages)
    agent = BrowserAgent(
        executor,
        client=_agent_client("DONE", target=None),
        scan_injection=False,
        max_steps=6,
        require_fresh=False,
    )
    run = await agent.run("finish")
    assert run.status == STATUS_DONE


# --------------------------------------------------------------------------
# agent: calibration outcome prefers real change over "did not raise"
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outcome_records_no_change_as_failure(tmp_path):
    """A click can report success and accomplish nothing. That is a miss."""
    from alpha.evaluation.system_one_calibration import configure_recorder, load_records, reset_recorder

    path = tmp_path / "browser.jsonl"
    configure_recorder(path)
    try:
        executor = ScriptedExecutor(pages=[page()] * 6, changes=[False], tracks_changes=True)
        agent = BrowserAgent(
            executor,
            client=_agent_client("CLICK", record=True),
            scan_injection=False,
            max_steps=2,
            ineffective_limit=99,
        )
        await agent.run("submit the form")
    finally:
        reset_recorder()

    records = [r for r in load_records(path) if r.site == "browser"]
    assert records, "the browser decisions should have been recorded"
    assert all(r.outcome is False for r in records), "changed=False must be recorded as an unsuccessful decision"


@pytest.mark.asyncio
async def test_outcome_falls_back_to_ok_when_change_is_unknown(tmp_path):
    from alpha.evaluation.system_one_calibration import configure_recorder, load_records, reset_recorder

    path = tmp_path / "unknown.jsonl"
    configure_recorder(path)
    try:
        executor = ScriptedExecutor(pages=[page()] * 6)  # tracks_changes False -> changed is None
        agent = BrowserAgent(executor, client=_agent_client("CLICK", record=True), scan_injection=False, max_steps=1)
        await agent.run("submit the form")
    finally:
        reset_recorder()

    records = [r for r in load_records(path) if r.site == "browser"]
    assert records
    assert all(r.outcome is True for r in records), "with no change signal, the executor's ok is the best available"


# --------------------------------------------------------------------------
# agent: wall-clock budget
# --------------------------------------------------------------------------


class _Clock:
    """Stand-in for the ``time`` module, so the budget is testable without sleeping.

    Swapped in for the module's own ``time`` reference rather than patching
    ``time.monotonic`` globally — asyncio schedules with the real clock, and
    patching it would break the event loop the test runs on.
    """

    def __init__(self, *values: float) -> None:
        self._values = list(values)
        self._last = values[-1] if values else 0.0

    def monotonic(self) -> float:
        if self._values:
            self._last = self._values.pop(0)
        return self._last


@pytest.mark.asyncio
async def test_run_stops_when_the_wall_clock_budget_is_exhausted(monkeypatch):
    """A step budget alone is not enough: each step can be slow."""
    monkeypatch.setattr("alpha.browser.jev_agent.time", _Clock(0.0, 0.0, 999.0))

    executor = ScriptedExecutor(pages=[page()] * 10, changes=[True], tracks_changes=True)
    agent = BrowserAgent(
        executor,
        client=_agent_client("CLICK"),
        scan_injection=False,
        max_steps=10,
        max_seconds=10.0,
        repeat_limit=99,
    )
    run = await agent.run("submit the form")

    assert run.status == STATUS_TIMEOUT
    assert len(run.steps) == 1, "the step that began inside the budget still completed"
    assert "budget" in run.detail


@pytest.mark.asyncio
async def test_zero_disables_the_wall_clock_budget(monkeypatch):
    """0 must mean "no budget", not "no time" — the two read alike in config."""
    monkeypatch.setattr("alpha.browser.jev_agent.time", _Clock(0.0, 0.0, 9999.0, 9999.0))

    executor = ScriptedExecutor(pages=[page()] * 6)
    agent = BrowserAgent(
        executor,
        client=_agent_client("DONE", target=None),
        scan_injection=False,
        max_steps=6,
        max_seconds=0,
    )
    run = await agent.run("finish")

    assert run.status == STATUS_DONE


@pytest.mark.asyncio
async def test_a_generous_budget_does_not_interfere(monkeypatch):
    monkeypatch.setattr("alpha.browser.jev_agent.time", _Clock(0.0, 0.0, 0.0, 0.0, 0.0))

    executor = ScriptedExecutor(pages=[page()] * 6)
    agent = BrowserAgent(
        executor,
        client=_agent_client("DONE", target=None),
        scan_injection=False,
        max_steps=6,
        max_seconds=600.0,
    )
    run = await agent.run("finish")

    assert run.status == STATUS_DONE


# --------------------------------------------------------------------------
# agent: deterministic recovery ladder
# --------------------------------------------------------------------------


def _sequence_client(operations: list[str], target: str | None = "1") -> SystemOneClient:
    """Answer with a different operation on each successive call.

    Lets a test tell "the action was re-run as-is" from "the action was chosen
    again" — the two stages of the recovery ladder.
    """
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        index = min(calls["n"], len(operations) - 1)
        calls["n"] += 1
        answers: dict = {"operation": _choice(operations[index])}
        if target is not None:
            answers["click_target"] = {"type": "choice", "choice": target, "probabilities": {"1": 0.9, "2": 0.1}, "confidence": 0.95}
            answers["type_text_target"] = {"type": "choice", "choice": target, "probabilities": {"1": 0.9, "2": 0.1}, "confidence": 0.95}
        return httpx.Response(200, json={"model": "jev", "answers": answers}, request=request)

    client = SystemOneClient(SystemOneConfig(api_key="k"))
    client._get_client = lambda: httpx.AsyncClient(  # type: ignore[method-assign]
        transport=httpx.MockTransport(handler), timeout=5.0
    )
    return client


@pytest.mark.asyncio
async def test_a_failed_step_is_retried_once():
    """Most browser failures are transient; one plain retry is the cheapest fix."""
    executor = ScriptedExecutor(pages=[page()] * 8, failures=[True, False], changes=[True], tracks_changes=True)
    agent = BrowserAgent(
        executor,
        client=_agent_client("CLICK"),
        scan_injection=False,
        max_steps=4,
        repeat_limit=99,
    )
    run = await agent.run("submit the form")

    assert run.recoveries == 1
    assert run.status != STATUS_BLOCKED, "a transient failure must not end the run"
    assert [a["operation"] for a in executor.actions[:2]] == ["CLICK", "CLICK"], "stage 1 repeats the same action"
    assert [step.ok for step in run.steps[:2]] == [False, True]
    assert run.to_dict()["recoveries"] == 1


@pytest.mark.asyncio
async def test_stage_one_reuses_the_decision_and_stage_two_re_decides():
    """Stage 1 re-runs the action; stage 2 refreshes and asks again."""
    executor = ScriptedExecutor(pages=[page()] * 8, failures=[True, True, False], changes=[True], tracks_changes=True)
    agent = BrowserAgent(
        executor,
        client=_sequence_client(["CLICK", "SCROLL_DOWN"]),
        scan_injection=False,
        max_steps=3,
        repeat_limit=99,
    )
    run = await agent.run("submit the form")

    operations = [a["operation"] for a in executor.actions]
    assert operations == ["CLICK", "CLICK", "SCROLL_DOWN"], "the retry must not re-decide; the recovery must"
    assert run.recoveries == 2
    assert [step.ok for step in run.steps] == [False, False, True]


@pytest.mark.asyncio
async def test_recovery_gives_up_after_the_limit():
    """Two stages, then hand back to the caller rather than looping forever."""
    executor = ScriptedExecutor(pages=[page()] * 8, failures=[True], changes=[True], tracks_changes=True)
    agent = BrowserAgent(
        executor,
        client=_agent_client("CLICK"),
        scan_injection=False,
        max_steps=8,
        repeat_limit=99,
        recoveries_limit=2,
    )
    run = await agent.run("submit the form")

    assert run.status == STATUS_BLOCKED
    assert run.recoveries == 2
    assert len(run.steps) == 3, "attempt + retry + re-decide, then stop"


@pytest.mark.asyncio
async def test_recovery_can_be_switched_off():
    executor = ScriptedExecutor(pages=[page()] * 4, failures=[True], changes=[True], tracks_changes=True)
    agent = BrowserAgent(
        executor,
        client=_agent_client("CLICK"),
        scan_injection=False,
        max_steps=4,
        recoveries_limit=0,
    )
    run = await agent.run("submit the form")

    assert run.status == STATUS_BLOCKED
    assert run.recoveries == 0
    assert len(run.steps) == 1, "no recovery means the first failure ends the run, as before"


@pytest.mark.asyncio
async def test_a_deliberate_retry_does_not_trip_the_repeat_detector():
    """Recovery is bounded by recoveries_limit, not by repeat_limit.

    With repeat_limit=1 a counted retry would abort the run as `repeated`, so
    reaching MAX_STEPS is the proof that the retry was not counted.
    """
    executor = ScriptedExecutor(pages=[page()] * 6, failures=[True, False], changes=[True], tracks_changes=True)
    agent = BrowserAgent(
        executor,
        client=_agent_client("CLICK"),
        scan_injection=False,
        max_steps=2,
        repeat_limit=1,
    )
    run = await agent.run("submit the form")

    assert run.status == STATUS_MAX_STEPS
    assert run.status != STATUS_REPEATED
    assert run.recoveries == 1
