"""Tests for the Skill Marketplace (alpha.skills_market)."""

from __future__ import annotations

import sys
from pathlib import Path

_HARNESS_ROOT = Path(__file__).resolve().parents[1] / "packages" / "harness"
if str(_HARNESS_ROOT) not in sys.path:
    sys.path.insert(0, str(_HARNESS_ROOT))

import pytest  # noqa: E402

from alpha.skills_market import (  # noqa: E402
    SkillMarketCatalog,
    SkillMarketError,
    SkillMarketRegistry,
    SkillStoreUnreadable,
)


def test_catalog_get_list_search():
    cat = SkillMarketCatalog()
    assert cat.get("excel-processing") is not None
    assert cat.get("EXCEL-PROCESSING").category == "content"
    assert cat.get("nope") is None
    assert "web-search" in {s.id for s in cat.search("search")}
    assert "ppt-generator" in {s.id for s in cat.list(category="content")}
    assert "enterprise" in {s.source for s in cat.list()}
    assert "research" in cat.categories()


def test_recommended_subset():
    cat = SkillMarketCatalog()
    rec = cat.recommended()
    assert rec and all(s.recommended for s in rec)


def test_install_enable_persist(tmp_path):
    path = tmp_path / "skills.json"
    reg = SkillMarketRegistry(path)
    reg.install("excel-processing", enable=True)
    reg.install("web-search", enable=True)
    assert set(reg.enabled_ids()) == {"excel-processing", "web-search"}

    reg.set_enabled("web-search", False)
    reloaded = SkillMarketRegistry(path)
    assert reloaded.get("web-search").enabled is False
    assert reloaded.uninstall("web-search") is True
    assert reloaded.get("web-search") is None


def test_unknown_skill_refused(tmp_path):
    reg = SkillMarketRegistry(tmp_path / "skills.json")
    with pytest.raises(SkillMarketError):
        reg.install("made-up")


def test_store_is_loud_on_corruption(tmp_path):
    path = tmp_path / "skills.json"
    path.write_text("null", encoding="utf-8")
    with pytest.raises(SkillStoreUnreadable):
        SkillMarketRegistry(path)
