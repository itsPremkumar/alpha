"""Tests for the HTTP-backed executor — the backend that can act on a real page.

Alpha has no Playwright installed and the built-in supervisor is a stub, so
before this the agent loop had no backend that could act on a real page at all:
``run`` could only drive a fake. This executor closes that gap for everything a
plain HTTP request can do — fetch a page, index it from the HTML, follow a link's
href, fill a field, submit a form.

Two properties matter, and they pull in opposite directions:

1. **It must actually work** — a goal like "follow the link and confirm" has to
   reach ``DONE`` against a fetched page, with real change detection.
2. **It must not become an SSRF engine** — this turns a model-chosen index into a
   network request, so the scheme and the host are a security boundary. The
   refusals are the feature, not an afterthought.
"""

from __future__ import annotations

import json

import httpx
import pytest

from alpha.browser.executor import HtmlExecutor, _is_local_host
from alpha.browser.jev_agent import STATUS_DONE, STATUS_NEEDS_TEXT, STATUS_UNVERIFIED, BrowserAgent
from alpha.config.system_one_config import SystemOneConfig
from alpha.models.system_one import SystemOneClient

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

OPS = ("CLICK", "TYPE_TEXT", "SCROLL_UP", "SCROLL_DOWN", "WAIT", "DONE", "BLOCKED")

HOME = "https://x.test/"


def _site(pages: dict[str, str], *, redirects: dict[str, tuple[int, str]] | None = None) -> httpx.AsyncClient:
    """A mock HTTP client serving `pages` by exact URL.

    The dict is read per request, not captured, so a test can mutate it to make a
    page disappear mid-run.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if redirects and url in redirects:
            status, location = redirects[url]
            return httpx.Response(status, headers={"location": location}, request=request)
        if url in pages:
            return httpx.Response(200, text=pages[url], request=request)
        return httpx.Response(404, text="not found", request=request)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


def _offered(body: dict, qid: str) -> list[str]:
    """The criteria a given question actually offered.

    Needed because ``Answer.validate`` requires the probabilities to cover
    *exactly* the offered set (``set(probs) != allowed_set`` is a rejection). A
    fake that always distributes over all seven operations therefore only works
    against a page that offers all seven — and a page with a single link offers
    six, while a page with no elements at all offers five. Reading the request
    makes the fake correct on any page.
    """
    criteria = ((body.get("questions") or {}).get(qid) or {}).get("criteria")
    if isinstance(criteria, dict) and criteria:
        return list(criteria)
    return list(OPS) if qid == "operation" else []


def _choice(value: str, offered: list[str], confidence: float = 0.95) -> dict:
    """A well-formed answer for `value` over exactly `offered`.

    Falls back to WAIT when a scripted operation was not on offer — which happens
    on a page with nothing to act on. WAIT is chosen because it is non-terminal,
    so the fallback cannot accidentally satisfy a test asserting the run does
    *not* reach DONE.
    """
    if value not in offered:
        value = "WAIT" if "WAIT" in offered else offered[0]
    others = [o for o in offered if o != value]
    share = round(0.02 / len(others), 6) if others else 0.0
    probabilities = {o: share for o in others}
    probabilities[value] = round(1.0 - sum(probabilities.values()), 6)
    return {"type": "choice", "choice": value, "probabilities": probabilities, "confidence": confidence}


def _sequence_client(operations: list[str]) -> SystemOneClient:
    """Answer with a different operation on each successive call.

    Target heads are answered too — with the first offered candidate — so a page
    whose operation head has more than one candidate still resolves.
    """
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        index = min(calls["n"], len(operations) - 1)
        calls["n"] += 1
        try:
            body = json.loads(request.content or b"{}")
        except ValueError:
            body = {}
        answers = {"operation": _choice(operations[index], _offered(body, "operation"))}
        for qid, question in (body.get("questions") or {}).items():
            if qid == "operation" or not isinstance(question, dict):
                continue
            criteria = question.get("criteria")
            if isinstance(criteria, dict) and criteria:
                answers[qid] = _choice(next(iter(criteria)), list(criteria))
        return httpx.Response(200, json={"model": "jev", "answers": answers}, request=request)

    client = SystemOneClient(SystemOneConfig(api_key="k"))
    client._get_client = lambda: httpx.AsyncClient(  # type: ignore[method-assign]
        transport=httpx.MockTransport(handler), timeout=5.0
    )
    return client


class _Server:
    """A mock site that records every request it was asked to serve."""

    def __init__(
        self,
        pages: dict[str, str] | None = None,
        *,
        posts: dict[str, str] | None = None,
        redirects: dict[str, tuple[int, str]] | None = None,
        cookies: str = "",
    ) -> None:
        self.pages = pages or {}
        self.posts = posts or {}
        self.redirects = redirects or {}
        self.cookies = cookies
        self.requests: list[httpx.Request] = []

    def client(self) -> httpx.AsyncClient:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            url = str(request.url)
            if url in self.redirects:
                status, location = self.redirects[url]
                return httpx.Response(status, headers={"location": location}, request=request)
            table = self.posts if request.method == "POST" else self.pages
            if url in table:
                headers = {"set-cookie": self.cookies} if self.cookies and request.method == "POST" else {}
                return httpx.Response(200, text=table[url], headers=headers, request=request)
            return httpx.Response(404, text="not found", request=request)

        return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)

    def posted(self) -> list[httpx.Request]:
        return [request for request in self.requests if request.method == "POST"]

    def sent_cookies(self) -> list[str]:
        return [str(request.headers.get("cookie") or "") for request in self.requests]


def _by_role(state: dict, role: str):
    from alpha.browser.element_table import build_action_space

    return next(e for e in build_action_space(state["elements"]).elements if e.role == role)


# --------------------------------------------------------------------------
# observing a real page
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_observe_fetches_and_indexes_the_page():
    executor = HtmlExecutor(HOME, client=_site({HOME: "<html><head><title>Home</title></head><body><a href='/a'>Alpha</a></body></html>"}))
    state = await executor.observe()

    assert state["url"] == HOME
    assert state["title"] == "Home"
    assert [e["role"] for e in state["elements"]] == ["link"]
    assert executor.fetches == 1


@pytest.mark.asyncio
async def test_observe_captures_the_link_destination():
    """Without `href` on the element there is nothing to follow."""
    executor = HtmlExecutor(HOME, client=_site({HOME: '<a href="/next">Next</a>'}))
    state = await executor.observe()
    assert state["elements"][0]["href"] == "/next"


@pytest.mark.asyncio
async def test_a_failed_fetch_keeps_the_last_known_state():
    """A page that goes away mid-run must not erase what was already observed.

    Reporting an empty page instead would look like the page had been emptied,
    and the guards downstream would read it as a change.
    """
    pages = {HOME: '<a href="/a">A</a>'}
    executor = HtmlExecutor(HOME, client=_site(pages))
    assert (await executor.observe())["elements"], "the first fetch must succeed"

    pages.clear()
    state = await executor.observe()

    assert state["elements"], "the last known page is kept rather than reported as empty"


# --------------------------------------------------------------------------
# acting: navigation works, everything else is refused honestly
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_click_follows_the_link():
    pages = {HOME: '<a href="/next">Next page</a>', "https://x.test/next": "<h1>Arrived</h1>"}
    executor = HtmlExecutor(HOME, client=_site(pages))
    state = await executor.observe()

    element = _element(state)
    result = await executor.act("CLICK", "1", "", element)

    assert result["ok"] is True
    assert result["url"] == "https://x.test/next"
    assert "Arrived" in executor._state["text"]


@pytest.mark.asyncio
async def test_click_without_an_href_is_refused_not_faked():
    """A button with no href is not clickable over HTTP; say so."""
    executor = HtmlExecutor(HOME, client=_site({HOME: '<button>Submit</button>'}))
    state = await executor.observe()

    result = await executor.act("CLICK", "1", "", _element(state))

    assert result["ok"] is False
    assert "no href" in result["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["SCROLL_DOWN", "SCROLL_UP"])
async def test_scrolling_is_refused_honestly(operation):
    """Pretending would be worse than refusing: the run would record progress."""
    executor = HtmlExecutor(HOME, client=_site({HOME: '<input name="q">'}))
    state = await executor.observe()

    result = await executor.act(operation, "1", "text", _element(state))

    assert result["ok"] is False
    assert "real browser" in result["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["TYPE_TEXT", "SELECT"])
async def test_filling_a_field_outside_a_form_is_refused(operation):
    """There would be nothing to submit the value to, so it goes nowhere."""
    executor = HtmlExecutor(HOME, client=_site({HOME: '<input name="q">'}))
    state = await executor.observe()

    result = await executor.act(operation, "1", "text", _element(state))

    assert result["ok"] is False
    assert "not inside a form" in result["detail"]


@pytest.mark.asyncio
async def test_terminal_operations_are_no_ops():
    executor = HtmlExecutor(HOME, client=_site({HOME: "<p>done</p>"}))
    for operation in ("DONE", "WAIT", "BLOCKED"):
        assert (await executor.act(operation, None, "", None))["ok"] is True


def _element(state: dict):
    from alpha.browser.element_table import build_action_space

    return build_action_space(state["elements"]).elements[0]


# --------------------------------------------------------------------------
# the security boundary
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "href",
    ["javascript:alert(1)", "data:text/html,<h1>x</h1>", "file:///etc/passwd", "mailto:a@b.test"],
)
@pytest.mark.asyncio
async def test_non_http_schemes_are_refused(href):
    executor = HtmlExecutor(HOME, client=_site({HOME: f'<a href="{href}">Go</a>'}))
    state = await executor.observe()

    result = await executor.act("CLICK", "1", "", _element(state))

    assert result["ok"] is False
    assert "not an allowed" in result["detail"]


@pytest.mark.parametrize("host", ["127.0.0.1", "169.254.169.254", "localhost", "10.0.0.5", "[::1]"])
@pytest.mark.asyncio
async def test_links_to_local_addresses_are_refused(host):
    """The usual SSRF payloads: loopback, link-local (cloud metadata), private."""
    href = f"http://{host}/latest/meta-data/"
    executor = HtmlExecutor(HOME, client=_site({HOME: f'<a href="{href}">Go</a>'}))
    state = await executor.observe()

    result = await executor.act("CLICK", "1", "", _element(state))

    assert result["ok"] is False


def test_the_local_host_check_covers_the_literal_forms():
    assert _is_local_host("127.0.0.1") is True
    assert _is_local_host("169.254.169.254") is True
    assert _is_local_host("localhost") is True
    assert _is_local_host("10.1.2.3") is True
    assert _is_local_host("x.test") is False
    assert _is_local_host("example.com") is False


@pytest.mark.asyncio
async def test_other_hosts_are_refused_by_default():
    """An unattended agent that follows any host is an SSRF engine."""
    executor = HtmlExecutor(HOME, client=_site({HOME: '<a href="https://elsewhere.test/x">Go</a>'}))
    state = await executor.observe()

    assert (await executor.act("CLICK", "1", "", _element(state)))["ok"] is False


@pytest.mark.asyncio
async def test_other_hosts_can_be_allowed_explicitly():
    pages = {HOME: '<a href="https://elsewhere.test/x">Go</a>', "https://elsewhere.test/x": "<h1>There</h1>"}
    executor = HtmlExecutor(HOME, client=_site(pages), allowed_hosts={"x.test", "elsewhere.test"})
    state = await executor.observe()

    result = await executor.act("CLICK", "1", "", _element(state))

    assert result["ok"] is True
    assert result["url"] == "https://elsewhere.test/x"


@pytest.mark.asyncio
async def test_a_redirect_to_a_disallowed_host_is_not_followed():
    """The allowed host is a hop, not a guarantee: a 302 must be re-checked."""
    redirects = {HOME: (302, "http://169.254.169.254/latest/meta-data/")}
    executor = HtmlExecutor(HOME, client=_site({}, redirects=redirects))

    state = await executor.observe()

    assert state["elements"] == [], "the metadata endpoint must not be fetched"


@pytest.mark.asyncio
async def test_a_redirect_within_the_allowed_host_is_followed():
    pages = {"https://x.test/moved": "<h1>Final</h1>"}
    redirects = {HOME: (302, "/moved")}
    executor = HtmlExecutor(HOME, client=_site(pages, redirects=redirects))

    state = await executor.observe()

    assert state["url"] == "https://x.test/moved"
    assert "Final" in state["text"]


@pytest.mark.asyncio
async def test_a_redirect_loop_gives_up():
    redirects = {HOME: (302, "/a"), "https://x.test/a": (302, "/b"), "https://x.test/b": (302, "/")}
    executor = HtmlExecutor(HOME, client=_site({}, redirects=redirects), max_redirects=2)

    assert (await executor.observe())["elements"] == []


@pytest.mark.asyncio
async def test_the_response_body_is_capped():
    """A huge page is truncated, not allowed to exhaust memory."""
    executor = HtmlExecutor(HOME, client=_site({HOME: "<a href='/x'>Link</a>" + "filler " * 50_000}), max_bytes=2_000)
    state = await executor.observe()

    assert len(state["text"]) <= 4_000, "the text extractor caps it further"
    assert executor.fetches == 1


# --------------------------------------------------------------------------
# forms: filling and submitting
# --------------------------------------------------------------------------

SEARCH = (
    '<form action="/search" method="get">'
    '<input type="hidden" name="csrf" value="tok">'
    '<input type="text" name="q" placeholder="Search">'
    '<button type="submit">Go</button>'
    "</form>"
)


@pytest.mark.asyncio
async def test_a_form_is_extracted_with_its_hidden_fields():
    """A CSRF token is what makes a POST work, and it is not an action."""
    executor = HtmlExecutor(HOME, client=_site({HOME: SEARCH}))
    state = await executor.observe()

    assert state["forms"] == [
        {"index": 1, "action": "/search", "method": "get", "enctype": "", "fields": [{"name": "csrf", "value": "tok"}]}
    ]
    assert [e["role"] for e in state["elements"]] == ["textbox", "button"], "the hidden input is not an action"


@pytest.mark.asyncio
async def test_typing_records_a_value_without_sending_anything():
    """Nothing goes on the wire until submit — which is what a browser does."""
    server = _Server(pages={HOME: SEARCH})
    executor = HtmlExecutor(HOME, client=server.client())
    state = await executor.observe()

    result = await executor.act("TYPE_TEXT", "1", "cats", _by_role(state, "textbox"))

    assert result["ok"] is True
    assert result["changed"] is True, "a recorded value is an effect even though the page did not change"
    assert server.posted() == []
    assert "cats" not in str([r.url for r in server.requests])


@pytest.mark.asyncio
async def test_a_typed_value_survives_the_next_observation():
    """`observe()` rebuilds the state from HTML, and HTML does not hold what was
    typed into it — so the value has to live somewhere else, or it vanishes
    between the fill and the submit."""
    server = _Server(pages={HOME: SEARCH, "https://x.test/search?csrf=tok&q=cats": "<h1>Results</h1>"})
    executor = HtmlExecutor(HOME, client=server.client())

    state = await executor.observe()
    await executor.act("TYPE_TEXT", "1", "cats", _by_role(state, "textbox"))
    await executor.observe()  # the agent does this after every action
    result = await executor.act("CLICK", "2", "", _by_role(state, "button"))

    assert result["url"] == "https://x.test/search?csrf=tok&q=cats"


@pytest.mark.asyncio
async def test_a_select_records_the_option_value_not_its_label():
    """The server wants the value; the model was shown the label."""
    html = (
        '<form action="/s" method="get">'
        '<select name="sort"><option value="relevance">Relevance</option><option value="date">Date</option></select>'
        '<button type="submit">Go</button></form>'
    )
    server = _Server(pages={HOME: html, "https://x.test/s?sort=date": "<h1>ok</h1>"})
    executor = HtmlExecutor(HOME, client=server.client())

    state = await executor.observe()
    select = _by_role(state, "select")
    await executor.act("SELECT", select.options[1]["index"], "Date", select)
    result = await executor.act("CLICK", "2", "", _by_role(state, "button"))

    assert result["url"] == "https://x.test/s?sort=date"


@pytest.mark.asyncio
async def test_a_post_form_sends_hidden_fields_and_typed_values():
    html = (
        '<form action="/login" method="post">'
        '<input type="hidden" name="csrf" value="tok">'
        '<input type="text" name="user">'
        '<button type="submit">Sign in</button>'
        "</form>"
    )
    server = _Server(pages={HOME: html}, posts={"https://x.test/login": "<h1>Welcome</h1>"})
    executor = HtmlExecutor(HOME, client=server.client())

    state = await executor.observe()
    await executor.act("TYPE_TEXT", "1", "ada", _by_role(state, "textbox"))
    result = await executor.act("CLICK", "2", "", _by_role(state, "button"))

    assert result["ok"] is True
    post = server.posted()[-1]
    assert post.content == b"csrf=tok&user=ada"


@pytest.mark.asyncio
async def test_an_empty_action_posts_to_the_current_url():
    """Per the HTML spec, and what every browser does."""
    html = '<form method="post"><input type="text" name="q"><button type="submit">Go</button></form>'
    server = _Server(pages={HOME: html}, posts={HOME: "<h1>Same page</h1>"})
    executor = HtmlExecutor(HOME, client=server.client())

    state = await executor.observe()
    await executor.act("TYPE_TEXT", "1", "x", _by_role(state, "textbox"))
    result = await executor.act("CLICK", "2", "", _by_role(state, "button"))

    assert result["ok"] is True
    assert str(server.posted()[-1].url) == HOME


@pytest.mark.asyncio
async def test_a_session_cookie_survives_to_the_next_request():
    """A login sets a cookie. A client created per request would throw it away,
    which is why the executor keeps one."""
    html = '<form action="/login" method="post"><input type="text" name="user"><button type="submit">Sign in</button></form>'
    server = _Server(pages={HOME: html}, posts={"https://x.test/login": "<h1>Welcome</h1>"}, cookies="sid=abc; Path=/")
    executor = HtmlExecutor(HOME, client=server.client())

    state = await executor.observe()
    await executor.act("TYPE_TEXT", "1", "ada", _by_role(state, "textbox"))
    await executor.act("CLICK", "2", "", _by_role(state, "button"))
    await executor.observe()

    assert any("sid=abc" in cookie for cookie in server.sent_cookies()), "the session must carry forward"


@pytest.mark.asyncio
async def test_a_303_after_a_post_becomes_a_get():
    """Post/redirect/get is the standard pattern; re-posting would submit twice."""
    html = '<form action="/submit" method="post"><input type="text" name="q"><button type="submit">Go</button></form>'
    server = _Server(
        pages={HOME: html, "https://x.test/done": "<h1>Submitted</h1>"},
        posts={"https://x.test/submit": "<p>redirecting</p>"},
        redirects={"https://x.test/submit": (303, "/done")},
    )
    executor = HtmlExecutor(HOME, client=server.client())

    state = await executor.observe()
    await executor.act("TYPE_TEXT", "1", "x", _by_role(state, "textbox"))
    result = await executor.act("CLICK", "2", "", _by_role(state, "button"))

    assert result["url"] == "https://x.test/done"
    assert len(server.posted()) == 1, "the form must not be submitted twice"


@pytest.mark.asyncio
async def test_submitting_clears_the_recorded_values():
    """They have been sent; keeping them would silently re-send them later."""
    server = _Server(pages={HOME: SEARCH, "https://x.test/search?csrf=tok&q=cats": "<h1>R</h1>"})
    executor = HtmlExecutor(HOME, client=server.client())

    state = await executor.observe()
    await executor.act("TYPE_TEXT", "1", "cats", _by_role(state, "textbox"))
    await executor.act("CLICK", "2", "", _by_role(state, "button"))

    assert executor._pending == {}


@pytest.mark.asyncio
async def test_a_multipart_form_is_refused_rather_than_half_sent():
    html = '<form action="/up" method="post" enctype="multipart/form-data"><input type="text" name="q"><button type="submit">Go</button></form>'
    server = _Server(pages={HOME: html})
    executor = HtmlExecutor(HOME, client=server.client())

    state = await executor.observe()
    result = await executor.act("CLICK", "2", "", _by_role(state, "button"))

    assert result["ok"] is False
    assert "multipart" in result["detail"]
    assert server.posted() == []


@pytest.mark.asyncio
async def test_a_field_without_a_name_cannot_be_filled():
    """Without a name there is no key to send it under."""
    html = '<form action="/s" method="get"><input type="text" placeholder="Search"><button type="submit">Go</button></form>'
    executor = HtmlExecutor(HOME, client=_site({HOME: html}))
    state = await executor.observe()

    result = await executor.act("TYPE_TEXT", "1", "cats", _by_role(state, "textbox"))

    assert result["ok"] is False
    assert "no name" in result["detail"]


@pytest.mark.asyncio
async def test_a_button_outside_a_form_is_refused():
    """No href and no form: there is nothing for a click to do."""
    executor = HtmlExecutor(HOME, client=_site({HOME: "<button>Submit</button>"}))
    state = await executor.observe()

    result = await executor.act("CLICK", "1", "", _by_role(state, "button"))

    assert result["ok"] is False
    assert "not in a form" in result["detail"]


# --------------------------------------------------------------------------
# the point of it all: `run` on a real page
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_agent_reaches_done_on_a_fetched_page():
    """End to end: observe -> CLICK -> follow -> DONE, with real change detection."""
    pages = {HOME: '<a href="/done">Finish</a>', "https://x.test/done": "<h1>All finished</h1>"}
    executor = HtmlExecutor(HOME, client=_site(pages))
    agent = BrowserAgent(
        executor,
        client=_sequence_client(["CLICK", "DONE"]),
        scan_injection=False,
        max_steps=3,
    )

    run = await agent.run("follow the link and confirm")

    assert run.status == STATUS_DONE, f"got {run.status}: {run.detail}"
    assert [step.operation for step in run.steps] == ["CLICK"]
    assert run.steps[0].changed is True, "the executor can genuinely observe change"
    assert run.steps[0].evidence[0] == "url", "and it can say what changed"


@pytest.mark.asyncio
async def test_an_unreachable_page_yields_no_elements_and_no_false_done():
    """Nothing was fetched, so nothing may be invented — and DONE would be a claim
    about a page that was never read.

    The freshness guard cannot catch this on its own: two identical *empty* pages
    compare equal, so they are trivially "fresh". A page with nothing on it is
    therefore not something a success claim can rest on.
    """
    executor = HtmlExecutor(HOME, client=_site({}))
    agent = BrowserAgent(executor, client=_sequence_client(["CLICK", "DONE"]), scan_injection=False, max_steps=2)

    run = await agent.run("do something")

    assert executor._state["elements"] == [], "an unfetched page must have no elements"
    assert run.status == STATUS_UNVERIFIED, f"got {run.status}: {run.detail}"
    assert run.fallback is True, "an unsupported claim goes back to the caller"
    assert run.status != STATUS_DONE, "there is no page to have verified"


@pytest.mark.asyncio
async def test_a_real_but_empty_page_also_cannot_support_done():
    """Same guard, reached the ordinary way: the page fetched, and it is blank."""
    executor = HtmlExecutor(HOME, client=_site({HOME: "<html><body></body></html>"}))
    agent = BrowserAgent(executor, client=_sequence_client(["DONE"]), scan_injection=False, max_steps=2)

    run = await agent.run("confirm the order went through")

    assert run.status == STATUS_UNVERIFIED


@pytest.mark.asyncio
async def test_a_page_with_only_text_can_support_done():
    """The guard asks for *something observable*, not for elements specifically."""
    executor = HtmlExecutor(HOME, client=_site({HOME: "<p>Order 1234 confirmed</p>"}))
    agent = BrowserAgent(executor, client=_sequence_client(["DONE"]), scan_injection=False, max_steps=2)

    run = await agent.run("confirm the order went through")

    assert run.status == STATUS_DONE, f"got {run.status}: {run.detail}"


@pytest.mark.asyncio
async def test_a_refused_click_does_not_end_the_run_silently():
    """The refusal is reported as a failed step, and recovery still applies."""
    executor = HtmlExecutor(HOME, client=_site({HOME: "<button>Submit</button>"}))
    agent = BrowserAgent(
        executor,
        client=_sequence_client(["CLICK"]),
        scan_injection=False,
        max_steps=4,
        recoveries_limit=0,
    )

    run = await agent.run("submit the form")

    assert run.steps
    assert run.steps[0].ok is False
    assert "no href" in run.steps[0].detail
    assert run.detail, "the run must explain why it stopped"


SEARCH_FORM = (
    '<form action="/search" method="get">'
    '<input type="text" name="q" placeholder="Search">'
    '<button type="submit">Go</button>'
    "</form>"
)
RESULTS = "https://x.test/search?q=python"


@pytest.mark.asyncio
async def test_the_agent_can_fill_a_form_and_submit_it():
    """End to end: TYPE_TEXT -> the form is submitted -> DONE, all over HTTP.

    Without this, every goal behind a search box or a login was unreachable —
    the loop could follow a link and nothing else.
    """
    site = _Server(pages={HOME: SEARCH_FORM, RESULTS: "<h1>Results for python</h1>"})
    executor = HtmlExecutor(HOME, client=site.client())

    async def provider(goal, element, page_state):  # noqa: ARG001
        return "python" if getattr(element, "name", "") == "q" else None

    agent = BrowserAgent(
        executor,
        client=_sequence_client(["TYPE_TEXT", "CLICK", "DONE"]),
        text_provider=provider,
        scan_injection=False,
        max_steps=4,
    )

    run = await agent.run("search for python")

    assert run.status == STATUS_DONE, f"got {run.status}: {run.detail}"
    assert [step.operation for step in run.steps] == ["TYPE_TEXT", "CLICK"]
    sent = [str(request.url) for request in site.requests]
    assert RESULTS in sent, f"the form was never submitted: {sent}"
    assert run.steps[0].changed is True, "a recorded fill is real progress, not a no-op"


@pytest.mark.asyncio
async def test_a_field_with_no_available_value_stops_the_run_with_needs_text():
    """The honest half: no provider means no value, so the run stops and says so.

    The alternative — typing something plausible into the field — would put a
    wrong value in the right box, which is harder to notice than a stopped run.
    """
    site = _Server(pages={HOME: SEARCH_FORM, RESULTS: "<h1>Results for python</h1>"})
    executor = HtmlExecutor(HOME, client=site.client())
    agent = BrowserAgent(executor, client=_sequence_client(["TYPE_TEXT"]), scan_injection=False, max_steps=3)

    run = await agent.run("search for python")

    assert run.status == STATUS_NEEDS_TEXT, f"got {run.status}: {run.detail}"
    assert run.fallback is True
    assert not site.requests[1:], "nothing may be sent without a value"


@pytest.mark.asyncio
async def test_a_form_value_that_does_not_exist_stops_rather_than_guessing():
    """A provider that cannot answer for *this* field is the same as no provider."""
    site = _Server(pages={HOME: SEARCH_FORM, RESULTS: RESULTS})
    executor = HtmlExecutor(HOME, client=site.client())

    async def provider(goal, element, page_state):  # noqa: ARG001
        return "python" if getattr(element, "name", "") == "some_other_field" else None

    agent = BrowserAgent(
        executor,
        client=_sequence_client(["TYPE_TEXT"]),
        text_provider=provider,
        scan_injection=False,
        max_steps=3,
    )

    run = await agent.run("search for python")

    assert run.status == STATUS_NEEDS_TEXT, f"got {run.status}: {run.detail}"


PICK_FORM = (
    '<form action="/pick" method="get">'
    '<select name="country">'
    '<option value="us">United States</option>'
    '<option value="de">Germany</option>'
    "</select>"
    '<button type="submit">Go</button>'
    "</form>"
)
# `_sequence_client` answers a target head with its first candidate, so the
# scripted SELECT lands on "1:1" — United States.
PICKED = "https://x.test/pick?country=us"


@pytest.mark.asyncio
async def test_selecting_an_option_needs_no_text_value():
    """The target *is* the option; a dropdown must work with no text provider.

    The action space addresses an option as ``<element>:<offset>``, so the
    decision already names which one to choose. Requiring a value for SELECT made
    every dropdown unusable without a provider — and `_record_value` then
    discarded that value anyway, so the requirement bought nothing.
    """
    site = _Server(pages={HOME: PICK_FORM, PICKED: "<h1>Picked United States</h1>"})
    executor = HtmlExecutor(HOME, client=site.client())
    agent = BrowserAgent(
        executor,
        client=_sequence_client(["SELECT", "CLICK", "DONE"]),
        scan_injection=False,
        max_steps=4,
    )

    run = await agent.run("pick a country and submit")

    assert run.status == STATUS_DONE, f"got {run.status}: {run.detail}"
    assert [step.operation for step in run.steps] == ["SELECT", "CLICK"]
    sent = [str(request.url) for request in site.requests]
    assert PICKED in sent, f"the selected option was never submitted: {sent}"
