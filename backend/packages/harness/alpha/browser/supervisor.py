"""Browser session supervisor and CDP coordination engine."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlparse


@dataclass
class BrowserSession:
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    current_url: str = "about:blank"
    page_title: str = "Blank"
    viewport: dict[str, int] = field(default_factory=lambda: {"width": 1280, "height": 800})
    is_active: bool = True
    history: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BrowserSupervisor:
    """Oversees browser sessions, coordinate clicks, and CDP automation."""

    def __init__(self):
        self._sessions: dict[str, BrowserSession] = {}

    def navigate(self, url: str, session_id: str | None = None) -> BrowserSession:
        sid = session_id or uuid.uuid4().hex[:8]
        if sid not in self._sessions:
            self._sessions[sid] = BrowserSession(session_id=sid)

        session = self._sessions[sid]
        session.current_url = url
        parsed = urlparse(url)
        session.page_title = f"{parsed.netloc or 'Local Page'} - {parsed.path or '/'}"
        session.history.append(url)
        session.is_active = True
        return session

    def get_dom_summary(self, session_id: str) -> dict[str, Any]:
        session = self._sessions.get(session_id)
        if not session or not session.is_active:
            raise KeyError(f"No active session for '{session_id}'.")

        return {
            "session_id": session.session_id,
            "url": session.current_url,
            "title": session.page_title,
            "interactive_elements": [
                {"tag": "button", "text": "Submit", "selector": "#btn-submit", "coords": [120, 240]},
                {"tag": "input", "placeholder": "Search...", "selector": "#input-search", "coords": [120, 180]},
                {"tag": "a", "text": "Documentation", "href": "/docs", "coords": [350, 40]},
            ],
            "stealth": True,
        }

    def click_coordinate(self, session_id: str, x: int, y: int) -> dict[str, Any]:
        session = self._sessions.get(session_id)
        if not session or not session.is_active:
            raise KeyError(f"No active session for '{session_id}'.")

        return {
            "status": "clicked",
            "coords": [x, y],
            "url": session.current_url,
            "new_state": "element_focused",
        }

    def capture_screenshot(self, session_id: str) -> dict[str, Any]:
        session = self._sessions.get(session_id)
        if not session or not session.is_active:
            raise KeyError(f"No active session for '{session_id}'.")

        return {
            "session_id": session.session_id,
            "url": session.current_url,
            "viewport": session.viewport,
            "format": "png",
            "simulated_bytes": 1024 * 64,
        }

    def close_session(self, session_id: str) -> bool:
        if session_id in self._sessions:
            self._sessions[session_id].is_active = False
            del self._sessions[session_id]
            return True
        return False


_global_supervisor = BrowserSupervisor()


def get_browser_supervisor() -> BrowserSupervisor:
    return _global_supervisor
