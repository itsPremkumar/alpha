"""Tests for egress routing + browser-profile references (alpha.egress)."""

from __future__ import annotations

import sys
from pathlib import Path

_HARNESS_ROOT = Path(__file__).resolve().parents[1] / "packages" / "harness"
if str(_HARNESS_ROOT) not in sys.path:
    sys.path.insert(0, str(_HARNESS_ROOT))

import pytest  # noqa: E402

from alpha.egress import (  # noqa: E402
    BrowserProfileRef,
    EgressPolicy,
    EgressRoute,
    EgressStore,
    EgressStoreUnreadable,
    EgressValidationError,
    import_profile,
)


def test_policy_default_route():
    policy = EgressPolicy()
    assert policy.resolve("example.com") == EgressRoute.DIRECT


def test_policy_first_match_wins():
    policy = EgressPolicy()
    policy.add_rule("*.example.com", EgressRoute.DATACENTER_PROXY)
    policy.add_rule("secure.example.com", EgressRoute.RESIDENTIAL_PROXY, prepend=True)
    # Prepended rule matches first.
    assert policy.resolve("secure.example.com") == EgressRoute.RESIDENTIAL_PROXY
    assert policy.resolve("api.example.com") == EgressRoute.DATACENTER_PROXY
    assert policy.resolve("other.org") == EgressRoute.DIRECT


def test_set_default_route():
    policy = EgressPolicy()
    policy.default_route = EgressRoute.RESIDENTIAL_PROXY
    assert policy.resolve("anything.net") == EgressRoute.RESIDENTIAL_PROXY


def test_profile_reference_rejects_inline_secrets():
    with pytest.raises(EgressValidationError):
        BrowserProfileRef.from_dict({"profile_id": "p1", "cookies": "session=abc"})
    with pytest.raises(EgressValidationError):
        BrowserProfileRef.from_dict({"profile_id": "p1", "token": "secret"})


def test_import_profile_refuses_credential_blob():
    with pytest.raises(EgressValidationError):
        import_profile("p1", source_path='{"cookies": "abc"}')
    with pytest.raises(EgressValidationError):
        import_profile("p1", source_path="/tmp/sessionid.txt")


def test_import_profile_accepts_plain_path():
    ref = import_profile("work", label="Work Chrome", source_path="C:/Users/me/Chrome/Profile 1", authenticated_domains=["mail.google.com"])
    assert ref.profile_id == "work"
    assert ref.authenticated_domains == ["mail.google.com"]


def test_invalid_domain_rejected():
    with pytest.raises(EgressValidationError):
        BrowserProfileRef(profile_id="p", authenticated_domains=["not a domain"])


def test_store_persists_policy_and_profiles(tmp_path):
    path = tmp_path / "egress.json"
    store = EgressStore(path)
    store.set_default_route(EgressRoute.RESIDENTIAL_PROXY)
    store.add_rule("*.bank.com", EgressRoute.RESIDENTIAL_PROXY, prepend=True)
    store.add_profile(import_profile("work", source_path="/profiles/work"))

    reloaded = EgressStore(path)
    assert reloaded.resolve("secure.bank.com") == EgressRoute.RESIDENTIAL_PROXY
    assert reloaded.get_profile("work") is not None


def test_store_is_loud_on_corruption(tmp_path):
    path = tmp_path / "egress.json"
    path.write_text("null", encoding="utf-8")
    with pytest.raises(EgressStoreUnreadable):
        EgressStore(path)
