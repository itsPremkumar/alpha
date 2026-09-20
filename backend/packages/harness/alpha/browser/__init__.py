"""Headless Browser Suite with CDP & Stealth automation inspired by Hermes Agent."""

from alpha.browser.cdp_bridge import (
    BrowserMode,
    BrowserTabInfo,
    CDPBrowserBridge,
    CDPSecurityPolicy,
)
from alpha.browser.stealth import get_stealth_headers
from alpha.browser.supervisor import BrowserSession, BrowserSupervisor, get_browser_supervisor

__all__ = [
    "BrowserMode",
    "BrowserSession",
    "BrowserSupervisor",
    "BrowserTabInfo",
    "CDPBrowserBridge",
    "CDPSecurityPolicy",
    "get_browser_supervisor",
    "get_stealth_headers",
]
