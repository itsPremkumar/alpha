"""Built-in Browser Supervisor tool inspired by Hermes Agent."""

from __future__ import annotations

import json

from langchain.tools import tool

from alpha.browser.supervisor import get_browser_supervisor


@tool("browser_navigate_and_inspect", parse_docstring=True)
def browser_navigate_and_inspect(
    action: str = "navigate",
    url: str = "",
    session_id: str = "",
    x: int = 0,
    y: int = 0,
) -> str:
    """Automate headless browser navigation, DOM inspection, and CDP coordinate interactions.

    Args:
        action: Operational action: 'navigate' (open URL), 'dom' (inspect elements), 'click' (click coordinate), 'screenshot' (capture page), or 'close' (terminate session).
        url: Destination URL when navigating.
        session_id: Active browser session identifier.
        x: X-coordinate for click actions.
        y: Y-coordinate for click actions.
    """
    supervisor = get_browser_supervisor()
    act = action.strip().lower()

    try:
        if act == "navigate":
            session = supervisor.navigate(url=url, session_id=session_id or None)
            return json.dumps(session.to_dict(), indent=2)

        elif act == "dom":
            return json.dumps(supervisor.get_dom_summary(session_id), indent=2)

        elif act == "click":
            return json.dumps(supervisor.click_coordinate(session_id, x, y), indent=2)

        elif act == "screenshot":
            return json.dumps(supervisor.capture_screenshot(session_id), indent=2)

        elif act == "close":
            ok = supervisor.close_session(session_id)
            return f"Browser session '{session_id}' closed: {ok}"

    except Exception as e:
        return f"Browser error: {e}"

    return f"Unknown action '{action}'. Use 'navigate', 'dom', 'click', 'screenshot', or 'close'."
