import json

import pytest

from agent_workspace.browser.stealth import get_stealth_headers
from agent_workspace.browser.supervisor import BrowserSupervisor
from agent_workspace.tools.builtins.browser_supervisor_tool import browser_navigate_and_inspect


def test_stealth_headers():
    headers = get_stealth_headers()
    assert "Mozilla/5.0" in headers["User-Agent"]
    assert headers["Sec-Fetch-Dest"] == "document"
    assert "Chromium" in headers["Sec-Ch-Ua"]


def test_browser_supervisor_lifecycle():
    supervisor = BrowserSupervisor()

    # 1. Navigate
    session = supervisor.navigate("https://news.ycombinator.com")
    assert session.current_url == "https://news.ycombinator.com"
    assert "news.ycombinator.com" in session.page_title
    sid = session.session_id

    # 2. Inspect DOM
    dom = supervisor.get_dom_summary(sid)
    assert dom["url"] == "https://news.ycombinator.com"
    assert len(dom["interactive_elements"]) >= 1

    # 3. Click coordinate
    click_res = supervisor.click_coordinate(sid, 120, 240)
    assert click_res["status"] == "clicked"
    assert click_res["coords"] == [120, 240]

    # 4. Screenshot
    shot = supervisor.capture_screenshot(sid)
    assert shot["format"] == "png"
    assert shot["viewport"]["width"] == 1280

    # 5. Close session
    closed = supervisor.close_session(sid)
    assert closed is True
    with pytest.raises(KeyError):
        supervisor.get_dom_summary(sid)


def test_browser_tool():
    # Navigate
    nav_out = browser_navigate_and_inspect.invoke({
        "action": "navigate",
        "url": "https://example.com",
    })
    parsed = json.loads(nav_out)
    sid = parsed["session_id"]
    assert parsed["current_url"] == "https://example.com"

    # DOM
    dom_out = browser_navigate_and_inspect.invoke({
        "action": "dom",
        "session_id": sid,
    })
    dom_parsed = json.loads(dom_out)
    assert len(dom_parsed["interactive_elements"]) >= 1

    # Close
    close_out = browser_navigate_and_inspect.invoke({
        "action": "close",
        "session_id": sid,
    })
    assert "closed: True" in close_out
