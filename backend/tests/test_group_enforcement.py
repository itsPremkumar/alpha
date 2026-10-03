"""Strict enforcement: the policy finally means something, and its two limits.

The assertions that matter are the ones about what strict mode must **not** do.
An over-eager enforcer is worse than no enforcer, because an operator who turns
it on and watches real work get refused will turn it off and then trust nothing.
"""

from __future__ import annotations

import pytest

import alpha.groups.activity as activity_mod
import alpha.groups.claims as claims_mod
from alpha.groups.claims import ClaimStore
from alpha.groups.enforcement import (
    CRASHED_LOCK_TTL_SECONDS,
    EnforcementDecision,
    HolderLiveness,
    StrictEnforcer,
    crashed_lock_ttl,
)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPHA_HOME", str(tmp_path / ".alpha"))
    for module, names in (
        (activity_mod, ("_ledger", "_ledger_path")),
        (claims_mod, ("_claims", "_claims_path")),
    ):
        for name in names:
            monkeypatch.setattr(module, name, None, raising=False)
    yield


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point the claim singleton at an isolated store."""
    path = tmp_path / "claims.json"
    monkeypatch.setattr(claims_mod, "get_claim_store", lambda: ClaimStore(path))
    return ClaimStore(path)


# ------------------------------------------------------------ default policy


class TestAdvisoryIsTheDefault:
    def test_a_default_enforcer_allows_everything(self, store):
        store.claim("crew", "coder", "file", "src/api.py")
        decision = StrictEnforcer("crew").check("src/api.py", "frontend")
        assert decision.allowed is True
        assert decision.reason == "advisory_policy"

    def test_the_default_never_reads_the_project_config(self, store):
        """A room nobody configured must not start refusing writes."""
        assert StrictEnforcer("crew").policy_for_project("p1") == "advisory"

    def test_advisory_is_the_default_for_an_explicit_enforcer(self, store):
        store.claim("crew", "coder", "file", "src/api.py")
        assert StrictEnforcer("crew", "advisory").check("src/api.py", "frontend").allowed is True


# ------------------------------------------------------------------ strict


class TestStrictRefuses:
    def test_it_refuses_a_live_claim_and_names_the_holder(self, store):
        store.claim("crew", "coder", "file", "src/api.py")
        decision = StrictEnforcer("crew", "strict").check("src/api.py", "frontend", holder_states={"coder": "working"})
        assert decision.allowed is False
        assert decision.reason == "live_claim"
        assert decision.holder == "coder"
        assert decision.claim_ids

    def test_it_allows_your_own_claim(self, store):
        store.claim("crew", "coder", "file", "src/api.py")
        assert StrictEnforcer("crew", "strict").check("src/api.py", "coder").allowed is True

    def test_it_allows_an_unclaimed_file(self, store):
        assert StrictEnforcer("crew", "strict").check("free.py", "frontend").allowed is True

    def test_it_sweeps_before_deciding(self, store):
        """A decision must never be made against a lapsed lease."""
        store.claim("crew", "coder", "file", "src/api.py", ttl_seconds=-1)
        decision = StrictEnforcer("crew", "strict").check("src/api.py", "frontend")
        assert decision.allowed is True

    def test_a_directory_claim_blocks_a_nested_file(self, store):
        store.claim("crew", "coder", "dir", "src/api")
        decision = StrictEnforcer("crew", "strict").check("src/api/routes.py", "frontend", holder_states={"coder": "working"})
        assert decision.allowed is False


class TestStrictMustNotRefuseOnSoftSignals:
    """Strict mode refuses unless the holder is **confirmed** gone.

    The rule is asymmetric on purpose. Blocking protects real work; allowing
    destroys it. So the question is never "is there evidence the holder is
    alive?" — it is "is there evidence the holder is *dead*?" Only a confirmed
    crash or a measured-dead process answers that, and both mean the work is
    abandoned rather than merely quiet.
    """

    def test_it_never_refuses_a_confirmed_dead_holder(self, store):
        """Refusing here would leave a crashed agent's file locked forever."""
        store.claim("crew", "coder", "file", "src/api.py")
        decision = StrictEnforcer("crew", "strict").check("src/api.py", "frontend", holder_states={"coder": "crashed"})
        assert decision.allowed is True
        assert decision.reason == "holder_gone"
        assert decision.reclaimable is True
        assert decision.holder == "coder"

    def test_it_does_refuse_an_unresponsive_holder(self, store):
        """Silence is not death, but it is also not consent.

        An agent inside a long tool call is unresponsive and very much working,
        so its file must stay held. This is the case a naive "only refuse on
        a proven live holder" rule gets backwards.
        """
        store.claim("crew", "coder", "file", "src/api.py")
        decision = StrictEnforcer("crew", "strict").check("src/api.py", "frontend", holder_states={"coder": "unresponsive"})
        assert decision.allowed is False
        assert decision.holder == "coder"

    def test_it_does_refuse_when_the_holder_state_is_unknown(self, store):
        """No record is not no owner. The holder may be mid-write."""
        store.claim("crew", "coder", "file", "src/api.py")
        decision = StrictEnforcer("crew", "strict").check("src/api.py", "frontend", holder_states={})
        assert decision.allowed is False
        assert decision.holder_state is None


class TestEnforcementNeverBreaksAWrite:
    def test_a_store_outage_defers_to_advisory(self, store, monkeypatch):
        def boom():
            raise RuntimeError("store down")

        monkeypatch.setattr(claims_mod, "get_claim_store", boom)
        decision = StrictEnforcer("crew", "strict").check("src/api.py", "frontend")
        assert decision.allowed is True
        assert decision.reason == "enforcement_unavailable"

    def test_an_unreadable_project_config_defers_to_advisory(self, store, monkeypatch):
        monkeypatch.setenv("ALPHA_HOME", str(mkdtemp_for(monkeypatch)))
        decision = StrictEnforcer("crew").check("src/api.py", "frontend", project_id="missing-project")
        assert decision.allowed is True

    def test_every_decision_carries_a_reason(self, store):
        """A refusal that cannot explain itself is indistinguishable from a bug."""
        store.claim("crew", "coder", "file", "src/api.py")
        for writer in ("coder", "frontend", "unknown"):
            assert StrictEnforcer("crew", "strict").check("src/api.py", writer).reason

    def test_an_empty_subject_is_allowed_rather_than_refused(self, store):
        assert StrictEnforcer("crew", "strict").check("  ", "frontend").reason == "no_subject"


def mkdtemp_for(monkeypatch):
    import tempfile

    return tempfile.mkdtemp()


# ------------------------------------------------------- crashed lock TTL


class TestCrashedLockTtl:
    def test_advisory_shortens_nothing(self):
        assert crashed_lock_ttl("advisory", "crashed") is None

    def test_strict_shortens_only_a_confirmed_crash(self):
        assert crashed_lock_ttl("strict", "crashed") == CRASHED_LOCK_TTL_SECONDS

    @pytest.mark.parametrize("state", ["working", "unresponsive", "idle", "unknown", None])
    def test_a_live_or_unknown_holder_keeps_its_full_lease(self, state):
        assert crashed_lock_ttl("strict", state) is None


# ------------------------------------------------------------- liveness view


class TestHolderLiveness:
    def test_a_confirmed_crash_is_gone(self):
        assert HolderLiveness(activity="crashed").gone is True

    def test_a_measured_dead_process_is_gone(self):
        assert HolderLiveness(alive=False).gone is True

    @pytest.mark.parametrize("activity", ["working", "unresponsive", "blocked", "idle", "offline", "unknown", None])
    def test_everything_else_is_not_gone(self, activity):
        assert HolderLiveness(activity=activity, alive=True).gone is False

    def test_an_alive_process_beats_an_unresponsive_reading(self):
        assert HolderLiveness(activity="unresponsive", alive=True).gone is False


def test_decision_serialises_with_its_reason():
    decision = EnforcementDecision(allowed=False, reason="live_claim", holder="coder", subject="a.py")
    assert decision.to_dict()["reason"] == "live_claim"
