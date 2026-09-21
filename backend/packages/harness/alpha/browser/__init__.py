"""Headless Browser Suite with CDP & Stealth automation inspired by Hermes Agent."""

from alpha.browser.cdp_bridge import (
    BrowserMode,
    BrowserTabInfo,
    CDPBrowserBridge,
    CDPSecurityPolicy,
)
from alpha.browser.cdp_executor import CdpExecutor
from alpha.browser.cdp_transport import (
    DEFAULT_ENDPOINT,
    CdpError,
    CdpTransport,
    WebSocketCdpTransport,
    discover_ws_url,
)
from alpha.browser.stealth import get_stealth_headers
from alpha.browser.supervisor import BrowserSession, BrowserSupervisor, get_browser_supervisor

__all__ = [
    "DEFAULT_ENDPOINT",
    "BrowserMode",
    "BrowserSession",
    "BrowserSupervisor",
    "BrowserTabInfo",
    "CDPBrowserBridge",
    "CDPSecurityPolicy",
    "CdpError",
    "CdpExecutor",
    "CdpTransport",
    "WebSocketCdpTransport",
    "discover_ws_url",
    "get_browser_supervisor",
    "get_stealth_headers",
]
