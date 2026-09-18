"""Unit tests for the Chrome DevTools Protocol (CDP) Browser Bridge."""

from __future__ import annotations

import pytest

from agent_workspace.browser.cdp_bridge import (
    BrowserTabInfo,
    CDPBrowserBridge,
    CDPSecurityPolicy,
)


def test_cdp_security_policy_domain_filtering():
    policy = CDPSecurityPolicy(
        allowed_domains=["github.com", "console.aws.amazon.com"],
        blocked_domains=["paypal.com", "bank"],
    )

    assert policy.is_url_permitted("https://github.com/org/repo") is True
    assert policy.is_url_permitted("https://console.aws.amazon.com/lambda") is True
    # Unlisted domain when allowed_domains is configured
    assert policy.is_url_permitted("https://random-site.org") is False
    # Blocked domain
    assert policy.is_url_permitted("https://mybank.com/account") is False


def test_cdp_browser_bridge_mock_tabs():
    bridge = CDPBrowserBridge(cdp_endpoint="http://127.0.0.1:9222")

    mock_tab = BrowserTabInfo(
        target_id="tab-1",
        title="GitHub Repository",
        url="https://github.com/project/alpha",
    )
    bridge.register_mock_tab(mock_tab)

    tabs = bridge.list_tabs(use_mock_fallback=True)
    assert len(tabs) == 1
    assert tabs[0].target_id == "tab-1"
    assert tabs[0].title == "GitHub Repository"

    # Attach to allowed tab
    attached = bridge.attach_tab("tab-1")
    assert attached.target_id == "tab-1"

    # Evaluate JS
    eval_res = bridge.evaluate_javascript("tab-1", "document.title")
    assert eval_res["status"] == "success"
    assert "document.title" in eval_res["expression"]


def test_cdp_browser_bridge_security_violation():
    policy = CDPSecurityPolicy(blocked_domains=["untrusted.org"])
    bridge = CDPBrowserBridge(security_policy=policy)

    bad_tab = BrowserTabInfo(
        target_id="tab-bad",
        title="Untrusted Page",
        url="https://untrusted.org/malware",
    )
    bridge.register_mock_tab(bad_tab)

    with pytest.raises(PermissionError):
        bridge.attach_tab("tab-bad")


def test_cdp_browser_bridge_missing_tab_raises():
    bridge = CDPBrowserBridge()
    with pytest.raises(KeyError, match="not found"):
        bridge.attach_tab("non-existent-tab")


def test_browser_tab_info_to_dict():
    tab = BrowserTabInfo(target_id="t-1", title="Title", url="https://example.com")
    d = tab.to_dict()
    assert d["target_id"] == "t-1"
    assert d["title"] == "Title"
    assert d["url"] == "https://example.com"
