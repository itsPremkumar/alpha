"""Tests for experts and expert groups (alpha.experts)."""

from __future__ import annotations

import sys
from pathlib import Path

_HARNESS_ROOT = Path(__file__).resolve().parents[1] / "packages" / "harness"
if str(_HARNESS_ROOT) not in sys.path:
    sys.path.insert(0, str(_HARNESS_ROOT))

import pytest  # noqa: E402

from alpha.experts import (  # noqa: E402
    ExpertCatalog,
    ExpertError,
    ExpertRegistry,
    ExpertStoreUnreadable,
)


def test_catalog_get_and_search():
    cat = ExpertCatalog()
    assert cat.get_expert("researcher") is not None
    assert cat.get_expert("RESEARCHER").category == "research"
    assert cat.get_expert("nope") is None
    assert "researcher" in {e.id for e in cat.search_experts("research")}
    assert "data-analyst" in {e.id for e in cat.list_experts(category="data")}


def test_group_members_resolve():
    cat = ExpertCatalog()
    members = cat.group_members("content-creation")
    assert [m.id for m in members] == ["creative-director", "copywriter", "graphic-designer", "video-generator"]
    assert cat.validate_group("content-creation") == []
    with pytest.raises(KeyError):
        cat.validate_group("does-not-exist")


def test_install_enable_disable_persist(tmp_path):
    path = tmp_path / "experts.json"
    reg = ExpertRegistry(path)
    reg.install_expert("researcher", enable=True)
    reg.install_group("content-creation", enable=True)
    assert set(reg.enabled_ids()) == {"researcher", "content-creation"}

    reg.set_enabled("researcher", False)
    assert "researcher" not in reg.enabled_ids()

    reloaded = ExpertRegistry(path)
    assert reloaded.get("researcher") is not None
    assert reloaded.get("researcher").enabled is False
    assert reloaded.get("content-creation").kind == "group"


def test_unknown_expert_and_group_refused(tmp_path):
    reg = ExpertRegistry(tmp_path / "experts.json")
    with pytest.raises(ExpertError):
        reg.install_expert("ghost")
    with pytest.raises(ExpertError):
        reg.install_group("ghost-team")


def test_store_is_loud_on_corruption(tmp_path):
    path = tmp_path / "experts.json"
    path.write_text("{oops", encoding="utf-8")
    with pytest.raises(ExpertStoreUnreadable):
        ExpertRegistry(path)
