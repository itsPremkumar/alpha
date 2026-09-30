"""Tests for the connector marketplace (alpha.connectors)."""

from __future__ import annotations

import sys
from pathlib import Path

_HARNESS_ROOT = Path(__file__).resolve().parents[1] / "packages" / "harness"
if str(_HARNESS_ROOT) not in sys.path:
    sys.path.insert(0, str(_HARNESS_ROOT))

import pytest  # noqa: E402

from alpha.connectors import (  # noqa: E402
    AuthKind,
    ConnectorCatalog,
    ConnectorCategory,
    ConnectorError,
    ConnectorHealth,
    ConnectorStore,
    ConnectorStoreUnreadable,
)


def test_catalog_get_and_ids():
    catalog = ConnectorCatalog()
    assert catalog.get("gmail") is not None
    assert catalog.get("GMAIL").id == "gmail"
    assert catalog.get("does-not-exist") is None
    assert "github" in catalog.ids()


def test_catalog_search_matches_name_vendor_and_actions():
    catalog = ConnectorCatalog()
    assert {c.id for c in catalog.search("google")} == {"gmail", "google_calendar"}
    assert "github" in {c.id for c in catalog.search("open_pr")}
    assert catalog.search("") == catalog.list()


def test_catalog_filter_by_category():
    catalog = ConnectorCatalog()
    storage = catalog.list(category=ConnectorCategory.STORAGE)
    assert {c.id for c in storage} == {"notion", "onedrive"}


def test_install_enable_disable_and_persist(tmp_path):
    path = tmp_path / "connectors.json"
    store = ConnectorStore(path)
    state = store.install("gmail", enable=True, rate_limit_per_min=60)
    assert state.enabled is True
    assert state.rate_limit_per_min == 60

    store.set_enabled("gmail", False)
    assert store.enabled_ids() == []

    reloaded = ConnectorStore(path)
    assert reloaded.get("gmail") is not None
    assert reloaded.get("gmail").enabled is False


def test_install_unknown_connector_is_refused(tmp_path):
    store = ConnectorStore(tmp_path / "connectors.json")
    with pytest.raises(ConnectorError):
        store.install("totally-made-up")


def test_record_health(tmp_path):
    store = ConnectorStore(tmp_path / "connectors.json")
    store.install("slack")
    updated = store.record_health("slack", ConnectorHealth.DEGRADED, error="rate limited", rate_limit_per_min=20)
    assert updated.health == ConnectorHealth.DEGRADED
    assert updated.last_error == "rate limited"
    assert updated.rate_limit_per_min == 20
    assert updated.last_checked_at is not None


def test_store_is_loud_on_corruption(tmp_path):
    path = tmp_path / "connectors.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ConnectorStoreUnreadable):
        ConnectorStore(path)


def test_builtin_connectors_cover_grok_advertised_apps():
    ids = {c.id for c in ConnectorCatalog().list()}
    for expected in ("gmail", "google_calendar", "outlook", "onedrive", "x"):
        assert expected in ids
    assert ConnectorCatalog().get("gmail").auth == AuthKind.OAUTH2
