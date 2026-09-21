"""Built-in Browser Supervisor tool inspired by Hermes Agent.

The indexed-action-space pattern from ``browser-use/jev-ultrafast``: the model
names an element **index** from an observed table, never a selector, coordinate
or script. The executor resolves that index back to an observed node, so model
output can never become code.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from langchain.tools import tool

from alpha.browser.element_table import build_action_space
from alpha.browser.jev_policy import choose_next_action_sync, format_element_table
from alpha.browser.supervisor import get_browser_supervisor

logger = logging.getLogger(__name__)


@tool("browser_navigate_and_inspect", parse_docstring=True)
def browser_navigate_and_inspect(
    action: str = "navigate",
    url: str = "",
    session_id: str = "",
    x: int = 0,
    y: int = 0,
    goal: str = "",
    html: str = "",
    steps: int = 0,
    values: str = "",
) -> str:
    """Automate headless browser navigation, DOM inspection, and System One driven interaction.

    Args:
        action: One of 'navigate', 'dom', 'click', 'screenshot', 'table', 'next', 'run', 'fetch_run', 'cdp_run', 'parse', 'close'.
            navigate opens url; dom inspects elements; click uses x/y; screenshot captures the page;
            table lists the indexed action space; next asks System One for the next step (needs goal);
            run drives the page toward goal; fetch_run fetches url over HTTP and drives it toward goal
            with no browser (follows links, fills fields, submits forms); cdp_run drives a real Chrome
            over the DevTools Protocol, which is the only path that can act on a page needing
            JavaScript (needs Chrome started with --remote-debugging-port); parse indexes elements
            from raw html; close ends the session.
        url: Destination URL when navigating, or the page to start from for 'fetch_run'.
        session_id: Active browser session identifier.
        x: X-coordinate for click actions.
        y: Y-coordinate for click actions.
        goal: Natural-language goal, required by 'next', 'run', and 'fetch_run'.
        html: Raw HTML for the 'parse' action.
        steps: Step budget for 'run' and 'fetch_run' (0 means the default budget).
        values: JSON object of form values for 'fetch_run', keyed by field name (or label),
            e.g. '{"q": "python", "csrf": "tok"}'. A field with no entry here stops the run
            with 'needs_text' rather than being filled with a guess. Text fields only: a
            dropdown's option is chosen from the observed table, so put the desired choice
            in the goal (e.g. "filter by Germany") rather than in values.
    """
    supervisor = get_browser_supervisor()
    act = (action or "").strip().lower()

    try:
        if act == "navigate":
            session = supervisor.navigate(url=url, session_id=session_id or None)
            return json.dumps(session.to_dict(), indent=2)

        if act == "dom":
            return json.dumps(supervisor.get_dom_summary(session_id), indent=2)

        if act == "click":
            return json.dumps(supervisor.click_coordinate(session_id, x, y), indent=2)

        if act == "screenshot":
            return json.dumps(supervisor.capture_screenshot(session_id), indent=2)

        if act == "close":
            ok = supervisor.close_session(session_id)
            return f"Browser session '{session_id}' closed: {ok}"

        if act in {"next", "next_action"}:
            return _suggest_next_action(supervisor, session_id, goal)

        if act == "table":
            return _element_table(supervisor, session_id)

        if act == "run":
            return _run_goal(session_id, goal, steps)

        if act in {"fetch_run", "fetch"}:
            return _fetch_run(url, goal, steps, values)

        if act in {"cdp_run", "cdp"}:
            return _cdp_run(url, goal, steps, values)

        if act == "parse":
            return _parse_html(html, url)

    except Exception as e:
        return f"Browser error: {e}"

    return (
        f"Unknown action '{action}'. Use 'navigate', 'dom', 'click', 'screenshot', "
        "'table', 'next', 'run', 'fetch_run', 'cdp_run', 'parse', or 'close'."
    )


def _page_state(supervisor: Any, session_id: str) -> dict[str, Any]:
    """Page state for a session, adapted to the element-table contract."""
    from alpha.browser.dom_snapshot import page_state_from_dom_summary

    dom = supervisor.get_dom_summary(session_id)
    return page_state_from_dom_summary(dom)


def _suggest_next_action(supervisor: Any, session_id: str, goal: str) -> str:
    """Ask System One which operation and element to use next.

    Returns a JSON string. When System One has no confident answer the payload
    carries ``"decision": null`` and the caller should choose the next step
    itself — null means "no verdict", never "do nothing".
    """
    page_state = _page_state(supervisor, session_id)
    decision = choose_next_action_sync(page_state, goal)

    payload: dict[str, Any] = {
        "session_id": session_id,
        "goal": goal,
        "decision": decision.to_dict() if decision else None,
        # Non-zero means the table the decision was made against is incomplete.
        "omitted": page_state.get("omitted", 0),
    }
    if decision is None:
        payload["note"] = "System One had no confident answer; choose the next step yourself."
        payload["elements"] = page_state["elements"]
        payload["element_table"] = format_element_table(build_action_space(page_state["elements"]))
    else:
        payload["element_table"] = format_element_table(build_action_space(page_state["elements"]))
    return json.dumps(payload, indent=2)


def _element_table(supervisor: Any, session_id: str) -> str:
    """Render the indexed action space the policy sees."""
    page_state = _page_state(supervisor, session_id)
    space = build_action_space(page_state["elements"])
    return json.dumps(
        {
            "session_id": session_id,
            "url": page_state.get("url", ""),
            "truncated": space.truncated,
            # Real targets the element cap dropped. Non-zero means the table is
            # incomplete and scrolling may be needed to reach them.
            "omitted": page_state.get("omitted", 0),
            "operations": sorted(space.operations()),
            "target_heads": {op: sorted(head) for op, head in space.targets.items()},
            "element_table": format_element_table(space),
        },
        indent=2,
    )


def _browser_guard_options() -> dict[str, Any]:
    """The loop guards from config, or nothing if the config cannot be read.

    These are read here rather than hardcoded so that switching the freshness
    guard off is a config change, not a code change. Degrading to ``{}`` means
    the defaults in ``BrowserAgent`` apply — config errors must never break the
    tool.
    """
    try:
        from alpha.config import get_app_config

        cfg = get_app_config().system_one
        return {
            "require_fresh": cfg.browser_require_fresh,
            "stale_retries": cfg.browser_stale_retries,
            "ineffective_limit": cfg.browser_ineffective_limit,
            "max_seconds": cfg.browser_max_seconds,
            "recoveries_limit": cfg.browser_recoveries_limit,
        }
    except Exception:  # pragma: no cover - config errors must never break the tool
        logger.debug("Could not read the browser guard config; using defaults.", exc_info=True)
        return {}


def _run_goal(session_id: str, goal: str, steps: int = 0) -> str:
    """Drive the page toward a goal with the System One agent loop.

    Sync bridge: the tool runs off the event loop. Text values for TYPE_TEXT /
    SELECT steps are not guessed here — the run stops with
    ``status="needs_text"`` and reports the field, so the calling agent supplies
    the value.
    """
    if not goal.strip():
        return json.dumps({"error": "'run' requires a goal."})
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        return json.dumps({"error": "'run' cannot be called from inside a running event loop; use the async API."})

    from alpha.browser.executor import SupervisorExecutor
    from alpha.browser.jev_agent import DEFAULT_MAX_STEPS, BrowserAgent

    executor = SupervisorExecutor(session_id)
    agent = BrowserAgent(executor, max_steps=steps or DEFAULT_MAX_STEPS, **_browser_guard_options())
    try:
        run = asyncio.run(agent.run(goal))
    except Exception as exc:
        return json.dumps({"error": f"run failed: {exc}"})
    return json.dumps(run.to_dict(), indent=2)


def _values_provider(values: dict[str, str]) -> Any:
    """Supply form values by field name, falling back to the visible label.

    Nothing is guessed. A field with no entry returns ``None``, which the agent
    turns into ``needs_text`` — stopping the run with the target named. The
    alternative (typing the only value available into whatever field is at hand)
    would silently put the wrong text in the wrong box.
    """

    async def _provide(goal: str, element: Any, page_state: dict[str, Any]) -> str | None:  # noqa: ARG001
        name = str(getattr(element, "name", "") or "").strip()
        if name and name in values:
            return values[name]
        label = str(getattr(element, "label", "") or "").strip()
        if label and label in values:
            return values[label]
        return None

    return _provide


def _parse_values(values: str) -> dict[str, str] | str:
    """Parse the ``values`` argument. Returns a dict, or an error string."""
    if not values.strip():
        return {}
    try:
        parsed = json.loads(values)
    except json.JSONDecodeError as exc:
        return f"'values' must be a JSON object: {exc}"
    if not isinstance(parsed, dict):
        return "'values' must be a JSON object of field name to value."
    return {str(key): str(value) for key, value in parsed.items()}


def _fetch_run(url: str, goal: str, steps: int = 0, values: str = "") -> str:
    """Fetch `url` over HTTP and drive it toward `goal`, with no browser at all.

    This is what makes the agent loop usable without Playwright installed: the
    page is fetched, indexed from its HTML, and acted on by following links,
    filling fields and submitting forms. What it cannot do is anything a plain
    HTTP request cannot — no JavaScript, no client-side state — and each of
    those is refused with a reason rather than approximated.

    `values` is how a form gets filled: a JSON object keyed by field name. A
    field with no entry stops the run (`needs_text`) instead of being filled
    with a guess, because a wrong value in the right box is harder to notice
    than a run that stops and says which field it wanted.

    Deliberately same-host, and deliberately **not** model-configurable: the
    allowed host set is an SSRF boundary, and a boundary the caller can widen on
    request is not one. A goal that genuinely spans hosts should construct
    `HtmlExecutor` directly, where the widening is a decision a human made.
    """
    if not goal.strip():
        return json.dumps({"error": "'fetch_run' requires a goal."})
    if not url.strip():
        return json.dumps({"error": "'fetch_run' requires a url."})
    parsed_values = _parse_values(values)
    if isinstance(parsed_values, str):
        return json.dumps({"error": parsed_values})
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        return json.dumps({"error": "'fetch_run' cannot be called from inside a running event loop; use the async API."})

    from alpha.browser.executor import HtmlExecutor
    from alpha.browser.jev_agent import DEFAULT_MAX_STEPS, BrowserAgent

    executor = HtmlExecutor(url)
    agent = BrowserAgent(
        executor,
        max_steps=steps or DEFAULT_MAX_STEPS,
        text_provider=_values_provider(parsed_values) if parsed_values else None,
        **_browser_guard_options(),
    )
    try:
        run = asyncio.run(agent.run(goal))
    except Exception as exc:
        return json.dumps({"error": f"fetch_run failed: {exc}"})
    payload = run.to_dict()
    # Where it ended up, and what the run cost. "One step is two requests" is a
    # price a caller should be able to see rather than infer.
    payload["url"] = executor.url
    payload["fetches"] = executor.fetches
    return json.dumps(payload, indent=2)


def _cdp_run(url: str, goal: str, steps: int = 0, values: str = "") -> str:
    """Drive a real Chrome toward `goal` over the DevTools Protocol.

    This is the JavaScript-capable path. `fetch_run` reads HTML over HTTP and
    stops where a plain request stops — no client-side state, no canvas, nothing
    a script rendered. This one attaches to a Chrome the user is already running
    (started with `--remote-debugging-port`), so it sees the page as the browser
    does.

    It is deliberately not a fallback for `fetch_run`: it needs a browser that is
    actually there, and a run that silently degrades from "drove Chrome" to
    "fetched HTML" is worse than one that says which it used. When Chrome is not
    reachable the error says so.

    Same `values` contract as `fetch_run`. Same guard seam, so a run here cannot
    bypass the loop guards. A URL policy is deliberately **not** a tool parameter:
    the boundary a run is allowed to cross is a human's decision, not one the
    model can request. A caller that needs one constructs `CdpExecutor` directly.
    """
    if not goal.strip():
        return json.dumps({"error": "'cdp_run' requires a goal."})
    if not url.strip():
        return json.dumps({"error": "'cdp_run' requires a url."})
    parsed_values = _parse_values(values)
    if isinstance(parsed_values, str):
        return json.dumps({"error": parsed_values})
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        return json.dumps({"error": "'cdp_run' cannot be called from inside a running event loop; use the async API."})

    from alpha.browser.cdp_executor import CdpExecutor
    from alpha.browser.cdp_transport import CdpError
    from alpha.browser.jev_agent import DEFAULT_MAX_STEPS, BrowserAgent

    executor = CdpExecutor(url)
    agent = BrowserAgent(
        executor,
        max_steps=steps or DEFAULT_MAX_STEPS,
        text_provider=_values_provider(parsed_values) if parsed_values else None,
        **_browser_guard_options(),
    )
    try:
        run = asyncio.run(_run_and_close(agent, executor, goal))
    except CdpError as exc:
        return json.dumps(
            {
                "error": f"cdp_run needs a Chrome with remote debugging enabled: {exc}",
                "hint": "Start Chrome with --remote-debugging-port=9222, then retry.",
            }
        )
    except Exception as exc:
        return json.dumps({"error": f"cdp_run failed: {exc}"})
    payload = run.to_dict()
    # What the run cost, and how much of it was browser protocol traffic.
    payload["url"] = executor.url
    payload["reads"] = executor.reads
    payload["commands"] = len(executor.commands)
    return json.dumps(payload, indent=2)


async def _run_and_close(agent: Any, executor: Any, goal: str) -> Any:
    """Run the agent, then close the tab it opened. Always closes it.

    The executor opens its own background tab, so leaving it open would litter
    the user's browser with a tab per run.
    """
    try:
        return await agent.run(goal)
    finally:
        await executor.aclose()


def _parse_html(html: str, url: str = "") -> str:
    """Index interactive elements out of raw HTML (no browser needed)."""
    from alpha.browser.dom_snapshot import page_state_from_html

    if not html.strip():
        return json.dumps({"error": "'parse' requires html."})
    page_state = page_state_from_html(html, url=url)
    space = build_action_space(page_state["elements"])
    return json.dumps(
        {
            "url": page_state.get("url", ""),
            "title": page_state.get("title", ""),
            "truncated": space.truncated,
            "omitted": page_state.get("omitted", 0),
            "operations": sorted(space.operations()),
            "element_table": format_element_table(space),
        },
        indent=2,
    )
