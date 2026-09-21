"""Tests for the CDP backend — a real browser, driven with no browser present.

The point of injecting the transport is that the behaviour worth testing is the
behaviour a live browser will not produce on demand: a target that went away, a
control that is covered, a password field, a document mid-navigation. All of
those are reproduced here against a scripted CDP transport.

What these tests do *not* cover, and cannot: the snapshot JavaScript itself. It
is syntax-checked (see ``test_the_snapshot_javascript_parses``) and reviewed
against the reference implementation, but executing it needs a real DOM. That
gap is real and is stated in the docs rather than papered over.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import httpx
import pytest

from alpha.browser.cdp_executor import CdpExecutor
from alpha.browser.cdp_snapshot import CDP_SNAPSHOT_JS, page_state_from_cdp
from alpha.browser.cdp_transport import CdpError, CdpTransport, WebSocketCdpTransport
from alpha.browser.element_table import build_action_space
from alpha.browser.jev_agent import STATUS_DONE, BrowserAgent
from alpha.config.system_one_config import SystemOneConfig
from alpha.models.system_one import SystemOneClient

# --------------------------------------------------------------------------
# a scripted Chrome
# --------------------------------------------------------------------------


class FakeChrome:
    """A CDP transport backed by a page model, and a record of every command.

    JavaScript is matched by a distinctive marker rather than by identity, so a
    test never has to know the exact script text — only which of the four
    evaluations it is looking at.
    """

    def __init__(
        self,
        *,
        payload: dict | None = None,
        after_click: dict | None = None,
        resolvable: dict[int, dict | None] | None = None,
        select_result: str | None = None,
        registry_size: int = 3,
        fail_evaluate: bool = False,
    ) -> None:
        self.payload = payload
        #: The page as it looks *after* a click. Without this a fake serves the
        #: same page twice, and "nothing changed" is the correct answer — so the
        #: change detector looks broken when it is working.
        self.after_click = after_click
        self.resolvable = resolvable or {}
        self.select_result = select_result
        self.registry_size = registry_size
        self.fail_evaluate = fail_evaluate
        self.calls: list[tuple[str, dict]] = []

    # -- the transport protocol ------------------------------------------

    async def send(self, method, params=None, session_id=None):  # noqa: ARG002
        params = params or {}
        self.calls.append((method, params))
        if method == "Target.createTarget":
            return {"targetId": "T1"}
        if method == "Target.attachToTarget":
            return {"sessionId": "S1"}
        if method == "Input.dispatchMouseEvent" and params.get("type") == "mouseReleased":
            if self.after_click is not None:
                self.payload = self.after_click
        if method == "Runtime.evaluate":
            return self._evaluate(params.get("expression") or "")
        return {}

    async def aclose(self) -> None:
        pass

    # -- helpers ---------------------------------------------------------

    def _evaluate(self, expression: str) -> dict:
        if self.fail_evaluate:
            raise CdpError("Runtime.evaluate failed: target closed")
        if "readyState" in expression:
            return {"result": {"value": "complete"}}
        if "createTreeWalker" in expression:
            if self.payload is None:
                return {"result": {"value": None}}
            return {"result": {"value": self.payload}}
        if "elementFromPoint" in expression:
            node = self._node_argument(expression)
            return {"result": {"value": self.resolvable.get(node)}}
        if "dispatchEvent(new Event('input'" in expression:
            return {"result": {"value": self.select_result}}
        if "nodes.size" in expression:
            return {"result": {"value": self.registry_size}}
        return {"result": {"value": None}}

    @staticmethod
    def _node_argument(expression: str) -> int:
        """The node id substituted into the resolve script's final call."""
        tail = expression.rsplit("})(", 1)[-1]
        digits = "".join(ch for ch in tail if ch.isdigit())
        return int(digits or 0)

    # -- assertions ------------------------------------------------------

    def methods(self) -> list[str]:
        return [method for method, _ in self.calls]

    def mouse_events(self) -> list[dict]:
        return [params for method, params in self.calls if method == "Input.dispatchMouseEvent"]

    def inserted(self) -> list[str]:
        return [str(params.get("text")) for method, params in self.calls if method == "Input.insertText"]


PAGE = {
    "url": "https://x.test/",
    "title": "Home",
    "text": "Welcome",
    "elements": [
        {"node": 1, "role": "link", "label": "Docs", "href": "/docs", "coords": [10, 20]},
        {"node": 2, "role": "textbox", "label": "Search", "name": "q", "coords": [10, 40]},
        {"node": 3, "role": "button", "label": "Go", "coords": [10, 60]},
    ],
    "total": 3,
}

POINT = {"x": 10.0, "y": 20.0, "tag": "A", "type": "", "readOnly": False}

#: The page after the click: it navigated, so the url and the text both differ.
AFTER_CLICK = {
    "url": "https://x.test/docs",
    "title": "Docs",
    "text": "Documentation",
    "elements": [{"node": 4, "role": "link", "label": "Back", "href": "/", "coords": [10, 20]}],
    "total": 1,
}


def _executor(chrome: FakeChrome, url: str = "https://x.test/", **kwargs) -> CdpExecutor:
    return CdpExecutor(url, transport=chrome, **kwargs)


# --------------------------------------------------------------------------
# the session
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_observe_opens_a_session_and_reads_the_page():
    chrome = FakeChrome(payload=PAGE)
    executor = _executor(chrome)

    state = await executor.observe()

    assert state["url"] == "https://x.test/"
    assert state["title"] == "Home"
    assert [e["role"] for e in state["elements"]] == ["link", "textbox", "button"]
    assert chrome.methods()[:4] == [
        "Target.createTarget",
        "Target.attachToTarget",
        "Emulation.setDeviceMetricsOverride",
        "Emulation.setFocusEmulationEnabled",
    ]
    assert "Page.navigate" in chrome.methods()


@pytest.mark.asyncio
async def test_the_session_is_opened_once_and_reused():
    chrome = FakeChrome(payload=PAGE)
    executor = _executor(chrome)

    await executor.observe()
    await executor.observe()

    assert chrome.methods().count("Target.createTarget") == 1, "a second read must reuse the tab"
    assert executor.reads == 2


@pytest.mark.asyncio
async def test_observe_reports_what_the_cap_dropped():
    page = dict(PAGE, total=9)
    state = await _executor(FakeChrome(payload=page)).observe()

    assert state["omitted"] == 6, "a truncated table must be reported, never silent"


@pytest.mark.asyncio
async def test_a_failed_read_keeps_the_last_known_state():
    """Reporting an empty page instead would read downstream as 'the page emptied'."""
    chrome = FakeChrome(payload=PAGE)
    executor = _executor(chrome)
    assert (await executor.observe())["elements"]

    chrome.fail_evaluate = True
    state = await executor.observe()

    assert state["elements"], "the last known page is kept"
    assert state["url"] == "https://x.test/"


@pytest.mark.asyncio
async def test_a_navigating_document_keeps_the_last_known_state():
    """The snapshot returns null while `document.body` is absent."""
    chrome = FakeChrome(payload=PAGE)
    executor = _executor(chrome)
    await executor.observe()

    chrome.payload = None
    assert (await executor.observe())["elements"]


@pytest.mark.asyncio
async def test_an_unreachable_browser_yields_no_elements_and_no_crash():
    class Dead:
        async def send(self, method, params=None, session_id=None):  # noqa: ARG002
            raise CdpError("connection refused")

        async def aclose(self) -> None:
            pass

    state = await _executor(Dead()).observe()  # type: ignore[arg-type]

    assert state["elements"] == []
    assert state["url"] == "https://x.test/"


@pytest.mark.asyncio
async def test_the_registry_size_is_reported():
    chrome = FakeChrome(payload=PAGE, registry_size=7)
    assert await _executor(chrome).registry_size() == 7


# --------------------------------------------------------------------------
# acting by identity
# --------------------------------------------------------------------------


def _element(state: dict, role: str):
    space = build_action_space(state["elements"])
    return next(e for e in space.elements if e.role == role)


@pytest.mark.asyncio
async def test_the_node_id_reaches_the_element_and_the_table():
    """The chain that makes index-only execution real: producer → carrier → consumer."""
    state = await _executor(FakeChrome(payload=PAGE)).observe()

    link = _element(state, "link")
    assert link.node == 1
    assert link.to_dict()["node"] == 1


@pytest.mark.asyncio
async def test_click_resolves_the_node_then_dispatches_real_mouse_events():
    chrome = FakeChrome(payload=PAGE, resolvable={1: POINT})
    executor = _executor(chrome)
    state = await executor.observe()

    result = await executor.act("CLICK", "1", "", _element(state, "link"))

    assert result["ok"] is True
    events = chrome.mouse_events()
    assert [e["type"] for e in events] == ["mousePressed", "mouseReleased"]
    assert (events[0]["x"], events[0]["y"]) == (10.0, 20.0)
    assert "Input.insertText" not in chrome.methods(), "a click must not type"


@pytest.mark.asyncio
async def test_a_click_is_refused_when_the_node_is_gone_or_covered():
    """Geometry is resolved at input time, so a stale decision is caught, not clicked."""
    chrome = FakeChrome(payload=PAGE, resolvable={1: None})
    executor = _executor(chrome)
    state = await executor.observe()

    result = await executor.act("CLICK", "1", "", _element(state, "link"))

    assert result["ok"] is False
    assert "gone, covered, or off-screen" in result["detail"]
    assert chrome.mouse_events() == [], "nothing may be dispatched at an unverified point"


@pytest.mark.asyncio
async def test_an_element_without_a_node_id_is_refused():
    """No node means no identity, and a selector fallback would break the contract."""
    chrome = FakeChrome(payload=PAGE, resolvable={1: POINT})
    executor = _executor(chrome)
    await executor.observe()
    from alpha.browser.element_table import Element

    orphan = Element(index="1", role="link", label="Docs")

    result = await executor.act("CLICK", "1", "", orphan)

    assert result["ok"] is False
    assert "no live node id" in result["detail"]
    assert chrome.mouse_events() == []


@pytest.mark.asyncio
async def test_type_focuses_selects_all_then_inserts():
    chrome = FakeChrome(payload=PAGE, resolvable={2: POINT})
    executor = _executor(chrome)
    state = await executor.observe()

    result = await executor.act("TYPE_TEXT", "2", "python", _element(state, "textbox"))

    assert result["ok"] is True
    assert chrome.inserted() == ["python"]
    key_events = [p for m, p in chrome.calls if m == "Input.dispatchKeyEvent"]
    assert [e["type"] for e in key_events] == ["keyDown", "keyUp"]
    assert key_events[0]["commands"] == ["selectAll"], "an existing value must be replaced, not appended to"
    assert chrome.mouse_events(), "the field must be focused before typing"


@pytest.mark.asyncio
async def test_typing_into_a_read_only_field_is_refused():
    chrome = FakeChrome(payload=PAGE, resolvable={2: dict(POINT, readOnly=True)})
    executor = _executor(chrome)
    state = await executor.observe()

    result = await executor.act("TYPE_TEXT", "2", "python", _element(state, "textbox"))

    assert result["ok"] is False
    assert "read-only" in result["detail"]
    assert chrome.inserted() == []


@pytest.mark.asyncio
async def test_typing_with_no_value_is_refused():
    chrome = FakeChrome(payload=PAGE, resolvable={2: POINT})
    executor = _executor(chrome)
    state = await executor.observe()

    result = await executor.act("TYPE_TEXT", "2", "", _element(state, "textbox"))

    assert result["ok"] is False
    assert chrome.inserted() == []


SELECT_PAGE = {
    "url": "https://x.test/",
    "title": "Home",
    "text": "",
    "elements": [
        {
            "node": 5,
            "role": "select",
            "label": "Country",
            "name": "country",
            "options": [
                {"label": "United States", "value": "us", "selected": False},
                {"label": "Germany", "value": "de", "selected": True},
            ],
        }
    ],
    "total": 1,
}


@pytest.mark.asyncio
async def test_select_sets_the_option_value_and_fires_events():
    """Assigning `.value` alone changes the DOM and not the application state."""
    chrome = FakeChrome(payload=SELECT_PAGE, select_result="de")
    executor = _executor(chrome)
    state = await executor.observe()

    result = await executor.act("SELECT", "1:2", "", _element(state, "select"))

    assert result["ok"] is True
    assert "'de'" in result["detail"]
    select_calls = [p for m, p in chrome.calls if m == "Runtime.evaluate" and "dispatchEvent" in str(p.get("expression"))]
    assert select_calls, "the value must be set through the page, not the DOM alone"


@pytest.mark.asyncio
async def test_select_refuses_an_option_that_was_not_offered():
    chrome = FakeChrome(payload=SELECT_PAGE, select_result="de")
    executor = _executor(chrome)
    state = await executor.observe()

    result = await executor.act("SELECT", "1:99", "", _element(state, "select"))

    assert result["ok"] is False
    assert "unknown option" in result["detail"]


@pytest.mark.asyncio
async def test_select_reports_when_the_page_did_not_apply_it():
    chrome = FakeChrome(payload=SELECT_PAGE, select_result=None)
    executor = _executor(chrome)
    state = await executor.observe()

    result = await executor.act("SELECT", "1:2", "", _element(state, "select"))

    assert result["ok"] is False
    assert "could not be set" in result["detail"]


@pytest.mark.asyncio
async def test_scroll_dispatches_a_wheel_event():
    chrome = FakeChrome(payload=PAGE)
    executor = _executor(chrome)

    down = await executor.act("SCROLL_DOWN", None, "", None)
    up = await executor.act("SCROLL_UP", None, "", None)

    assert down["ok"] and up["ok"]
    wheels = [p for p in chrome.mouse_events() if p.get("type") == "mouseWheel"]
    assert [w["deltaY"] for w in wheels] == [560, -560]


@pytest.mark.asyncio
async def test_terminal_operations_touch_nothing():
    chrome = FakeChrome(payload=PAGE)
    executor = _executor(chrome)
    before = len(chrome.calls)

    for operation in ("DONE", "WAIT", "BLOCKED"):
        assert (await executor.act(operation, None, "", None))["ok"] is True

    assert len(chrome.calls) == before


@pytest.mark.asyncio
async def test_close_closes_only_the_tab_it_opened():
    chrome = FakeChrome(payload=PAGE)
    executor = _executor(chrome)
    await executor.observe()

    await executor.aclose()

    closes = [p for m, p in chrome.calls if m == "Target.closeTarget"]
    assert closes == [{"targetId": "T1"}], "the user's own tabs must not be touched"


@pytest.mark.asyncio
async def test_the_url_policy_is_checked_before_navigating():
    chrome = FakeChrome(payload=PAGE)
    executor = CdpExecutor("https://evil.test/", transport=chrome, url_policy=lambda url: "evil" not in url)

    state = await executor.observe()

    assert "Page.navigate" not in chrome.methods(), "a refused navigation must not be attempted"
    assert state["elements"] == []


# --------------------------------------------------------------------------
# the password field
# --------------------------------------------------------------------------


def test_a_password_field_is_carried_as_secret_with_no_value():
    """The field stays a target so a login can be filled; only the value is withheld."""
    state = page_state_from_cdp(
        {
            "url": "https://x.test/login",
            "elements": [{"node": 1, "role": "textbox", "label": "Password", "value": "", "secret": True}],
        }
    )
    element = build_action_space(state["elements"]).elements[0]

    assert element.secret is True
    assert element.value == ""
    assert element.to_dict()["secret"] is True


def test_the_snapshot_never_reads_a_password_value():
    """A guard on the source, because the leak is in the JavaScript, not the adapter."""
    assert "secret(e) ? '' : String(e.value" in CDP_SNAPSHOT_JS, "the value must be withheld at the source"
    assert "secret(e) ? null : (e.value" in CDP_SNAPSHOT_JS, "and the guard must not be a second channel"


def test_a_secret_field_is_marked_in_the_element_table():
    from alpha.browser.jev_policy import format_element_table

    state = page_state_from_cdp(
        {"url": "https://x.test/", "elements": [{"node": 1, "role": "textbox", "label": "Password", "secret": True}]}
    )
    table = format_element_table(build_action_space(state["elements"]))

    assert "[secret]" in table, "a blank secret field must not read as an empty one"


# --------------------------------------------------------------------------
# the JavaScript itself
# --------------------------------------------------------------------------


def test_the_snapshot_javascript_parses():
    """Syntax is all that can be checked without a DOM — and a syntax error would
    break every run, so it is worth checking."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed; the snapshot JS cannot be syntax-checked here")
    result = subprocess.run([node, "--check", "-"], input=CDP_SNAPSHOT_JS, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"the snapshot JavaScript does not parse:\n{result.stderr}"


def test_the_snapshot_cap_matches_the_python_constant():
    from alpha.browser.dom_snapshot import MAX_ELEMENTS

    assert f"picked.length >= {MAX_ELEMENTS}" in CDP_SNAPSHOT_JS
    assert "__MAX_ELEMENTS__" not in CDP_SNAPSHOT_JS, "the placeholder must be substituted"


# --------------------------------------------------------------------------
# the transport protocol
# --------------------------------------------------------------------------


def test_the_fake_and_the_real_transport_satisfy_the_same_protocol():
    assert isinstance(FakeChrome(), CdpTransport)
    assert isinstance(WebSocketCdpTransport("ws://127.0.0.1:9222/devtools/browser/x"), CdpTransport)


# --------------------------------------------------------------------------
# end to end, through the agent loop
# --------------------------------------------------------------------------


def _sequence_client(operations: list[str]) -> SystemOneClient:
    """Answer System One with a different operation on each successive call."""
    ops = ("CLICK", "TYPE_TEXT", "SCROLL_UP", "SCROLL_DOWN", "WAIT", "DONE", "BLOCKED")
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        index = min(calls["n"], len(operations) - 1)
        calls["n"] += 1
        try:
            body = json.loads(request.content or b"{}")
        except ValueError:
            body = {}
        criteria = ((body.get("questions") or {}).get("operation") or {}).get("criteria") or list(ops)
        wanted = operations[index]
        if wanted not in criteria:
            wanted = "WAIT" if "WAIT" in criteria else next(iter(criteria))
        others = [o for o in criteria if o != wanted]
        share = round(0.02 / len(others), 6) if others else 0.0
        probabilities = {o: share for o in others}
        probabilities[wanted] = round(1.0 - sum(probabilities.values()), 6)
        answers = {
            "operation": {
                "type": "choice",
                "choice": wanted,
                "probabilities": probabilities,
                "confidence": 0.95,
            }
        }
        for qid, question in (body.get("questions") or {}).items():
            if qid == "operation" or not isinstance(question, dict):
                continue
            offered = question.get("criteria")
            if isinstance(offered, dict) and offered:
                pick = next(iter(offered))
                rest = [o for o in offered if o != pick]
                small = round(0.02 / len(rest), 6) if rest else 0.0
                probs = {o: small for o in rest}
                probs[pick] = round(1.0 - sum(probs.values()), 6)
                answers[qid] = {"type": "choice", "choice": pick, "probabilities": probs, "confidence": 0.95}
        return httpx.Response(200, json={"model": "jev", "answers": answers}, request=request)

    client = SystemOneClient(SystemOneConfig(api_key="k"))
    client._get_client = lambda: httpx.AsyncClient(  # type: ignore[method-assign]
        transport=httpx.MockTransport(handler), timeout=5.0
    )
    return client


@pytest.mark.asyncio
async def test_the_agent_reaches_done_against_a_cdp_page():
    """End to end: observe → CLICK by node id → DONE, with real change detection."""
    chrome = FakeChrome(payload=PAGE, after_click=AFTER_CLICK, resolvable={1: POINT})
    executor = _executor(chrome)
    agent = BrowserAgent(
        executor,
        client=_sequence_client(["CLICK", "DONE"]),
        scan_injection=False,
        max_steps=3,
    )

    run = await agent.run("open the docs")

    assert run.status == STATUS_DONE, f"got {run.status}: {run.detail}"
    assert [step.operation for step in run.steps] == ["CLICK"]
    assert run.steps[0].ok is True
    assert run.steps[0].changed is True, "a CDP backend can genuinely observe change"
    assert run.steps[0].evidence, "and it can say what changed"


@pytest.mark.asyncio
async def test_a_refused_click_does_not_end_the_run_silently():
    chrome = FakeChrome(payload=PAGE, resolvable={1: None})
    executor = _executor(chrome)
    agent = BrowserAgent(
        executor,
        client=_sequence_client(["CLICK"]),
        scan_injection=False,
        max_steps=4,
        recoveries_limit=0,
    )

    run = await agent.run("open the docs")

    assert run.steps
    assert run.steps[0].ok is False
    assert "covered" in run.steps[0].detail
    assert run.detail, "the run must explain why it stopped"
