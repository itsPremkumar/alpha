"""Chrome DevTools Protocol (CDP) Browser Bridge.

Provides a dual-tier browser automation interface:
1. Headless isolated sandbox (for anonymous scraping and untrusted pages).
2. Authenticated user browser attachment via CDP (for signed-in services,
   cloud consoles, internal dashboards, and human-in-the-loop workflows).
"""

from __future__ import annotations

import enum
import json
import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from alpha.browser.cdp_transport import CdpError

logger = logging.getLogger(__name__)


class BrowserMode(enum.StrEnum):
    """Browser execution mode."""

    HEADLESS_SANDBOX = "headless_sandbox"
    USER_CDP = "user_cdp"
    AUTO = "auto"


@dataclass
class BrowserTabInfo:
    """Represents an active target or tab in the CDP browser."""

    target_id: str
    title: str
    url: str
    type: str = "page"
    web_socket_debugger_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "title": self.title,
            "url": self.url,
            "type": self.type,
        }


class CDPSecurityPolicy:
    """Security boundaries governing permitted domains and actions for CDP."""

    def __init__(self, allowed_domains: list[str] | None = None, blocked_domains: list[str] | None = None) -> None:
        self.allowed_domains = [d.lower() for d in (allowed_domains or [])]
        self.blocked_domains = [
            d.lower()
            for d in (
                blocked_domains
                or [
                    "bank",
                    "paypal.com",
                    "accounts.google.com/signin",
                    "login.live.com",
                ]
            )
        ]

    def is_url_permitted(self, url: str) -> bool:
        """Check whether URL complies with safety boundaries."""
        if not url or url.startswith("about:"):
            return True
        try:
            parsed = urlparse(url)
            host = (parsed.netloc or "").lower()
            # Blocked list takes precedence
            for blocked in self.blocked_domains:
                if blocked in host or blocked in url.lower():
                    return False
            # If allowed list specified, host must match
            if self.allowed_domains:
                return any(allowed in host for allowed in self.allowed_domains)
            return True
        except Exception:
            return False


class CDPBrowserBridge:
    """Bridge for querying and commanding an external Chrome DevTools Protocol instance."""

    def __init__(
        self,
        cdp_endpoint: str = "http://127.0.0.1:9222",
        security_policy: CDPSecurityPolicy | None = None,
        transport: Any = None,
    ) -> None:
        self.cdp_endpoint = cdp_endpoint.rstrip("/")
        self.security_policy = security_policy or CDPSecurityPolicy()
        #: An injected CDP transport. Without one, ``evaluate_javascript`` cannot
        #: reach a page and says so.
        self._transport = transport
        self._simulated_tabs: dict[str, BrowserTabInfo] = {}

    def register_mock_tab(self, tab: BrowserTabInfo) -> None:
        """Register a mock tab for headless testing environments."""
        self._simulated_tabs[tab.target_id] = tab

    def list_tabs(self, use_mock_fallback: bool = True) -> list[BrowserTabInfo]:
        """Query /json/list from the CDP endpoint, falling back to simulated tabs if offline."""
        try:
            import urllib.request

            req = urllib.request.Request(
                f"{self.cdp_endpoint}/json/list",
                headers={"User-Agent": "AgentWorkspace-CDPBridge/2.0"},
            )
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
                tabs = []
                for item in raw:
                    if item.get("type") == "page":
                        tabs.append(
                            BrowserTabInfo(
                                target_id=item.get("id", ""),
                                title=item.get("title", ""),
                                url=item.get("url", ""),
                                type=item.get("type", "page"),
                                web_socket_debugger_url=item.get("webSocketDebuggerUrl", ""),
                            )
                        )
                return tabs
        except Exception as exc:
            logger.debug("CDP endpoint '%s' unreachable (%s)", self.cdp_endpoint, exc)
            if use_mock_fallback and self._simulated_tabs:
                return list(self._simulated_tabs.values())
            return []

    def attach_tab(self, target_id: str) -> BrowserTabInfo:
        """Validate target tab authorization against domain security rules."""
        tabs = self.list_tabs()
        matched = next((t for t in tabs if t.target_id == target_id), None)
        if not matched:
            raise KeyError(f"CDP target tab '{target_id}' not found.")

        if not self.security_policy.is_url_permitted(matched.url):
            raise PermissionError(f"Attachment to target '{matched.url}' denied by security policy.")
        return matched

    async def evaluate_javascript(self, target_id: str, expression: str) -> dict[str, Any]:
        """Run a JavaScript snippet in an authorized target page.

        This used to return ``{"status": "success", "result": {"value":
        "Evaluated: <expression>..."}}`` — a fabricated result that echoed the
        expression back. A caller could not tell it apart from a real evaluation,
        and the test suite asserted the echo, so the fake was pinned in place.
        Nothing production called it, which is the only reason it was harmless.

        It now does the work, and raises when it cannot. There is no transport
        that makes a page read happen by accident, so a failure here means the
        caller has learned nothing about the page — never that the page was
        empty.
        """
        tab = self.attach_tab(target_id)
        if self._transport is None:
            raise CdpError(
                "no CDP transport is configured; construct the bridge with a transport, "
                "or drive the page with CdpExecutor instead"
            )
        response = await self._transport.send(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True},
        )
        if response.get("exceptionDetails"):
            detail = response["exceptionDetails"].get("text") or "evaluation failed"
            raise CdpError(str(detail))
        return {
            "target_id": tab.target_id,
            "url": tab.url,
            "expression": expression,
            "result": response.get("result") or {},
        }
