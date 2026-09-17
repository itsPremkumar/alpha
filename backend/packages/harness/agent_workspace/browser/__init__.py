"""Headless Browser Suite with CDP & Stealth automation inspired by Hermes Agent."""

from agent_workspace.browser.stealth import get_stealth_headers
from agent_workspace.browser.supervisor import BrowserSession, BrowserSupervisor, get_browser_supervisor

__all__ = [
    "BrowserSession",
    "BrowserSupervisor",
    "get_browser_supervisor",
    "get_stealth_headers",
]
