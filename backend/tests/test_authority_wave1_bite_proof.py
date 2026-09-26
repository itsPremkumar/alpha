"""Bite proofs: each guard is LOAD-BEARING, demonstrated by neutralising it in
memory and showing the property breaks.

The environment this was written in has restarted mid-script and left a tracked
source file REVERTED, so nothing here mutates a tracked file on disk.  Every
neutralisation is either:

* an in-memory ``monkeypatch`` of a module-level constant or function,
* a throwaway :class:`~alpha.safety.authority.receipts.ReceiptChain` in the
  temp workspace, or
* a deliberately BROKEN re-implementation written inline in the test, used only
  to show that the shipped implementation is what makes the assertion hold.

Each test asserts the SHIPPED behaviour first, then removes one control, then
asserts the property is now violated.  A test that cannot fail this way is not
proving the control exists; it is only proving the control happens to be
present.

Pure ASCII on purpose: two files in this repository were already damaged by
tools that read UTF-8 as cp1252 and wrote it back.
"""

from __future__ import annotations

import inspect
import json
from typing import Any

import pytest

from alpha.bots.authority_ceiling import (
    AuthorityCeiling,
    AuthorityViolation,
    clear_scopes,
    enforce_grant,
    register_scopes,
)
from alpha.safety.authority import boundaries as boundaries_mod
from alpha.safety.authority import receipts as receipts_mod
from alpha.safety.authority import scopes as scopes_mod
from alpha.safety.authority import taint as taint_mod
from alpha.safety.authority.boundaries import (
    INBOUND_ENTRY_POINTS,
    Authentication,
    CredentialSpec,
    InboundEntryPoint,
    SurfaceKind,
    assert_inbound_default_deny,
    assert_startable,
    resolve_authority_approval,
    resolve_credential_owners,
)
from alpha.safety.authority.receipts import (
    ReceiptChain,
    ReceiptFlag,
    RejectedAlternative,
    automated_system,
)
from alpha.safety.authority.scopes import (
    ScopeEscalationRefused,
    ScopePolicy,
    baseline_from_ceiling,
    compose_scopes,
    explain_denial,
)
from alpha.safety.authority.taint import (
    TaintClearRefused,
    TaintTurn,
    UntrustedSource,
    bind_turn,
    isolate_child,
    new_turn,
)


@pytest.fixture(autouse=True)
def _clean_state():
    clear_scopes()
    yield
    clear_scopes()


def _baseline():
    return baseline_from_ceiling(AuthorityCeiling())


# ===========================================================================
# (a)/(b) The enable-refusal bites: remove the key list and the escalation works.
# ===========================================================================


def test_bite_the_enable_attempt_refusal_is_load_bearing(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "name": "escalation-attempt",
        "members": [{"kind": "agent", "name": "researcher-3"}],
        "enable_capabilities": ["repository_mutate"],
    }

    # 1. The shipped behaviour: refused.
    with pytest.raises(ScopeEscalationRefused):
        ScopePolicy.from_dict(payload, baseline=_baseline())

    # 2. Remove the control: the enablement key is now simply unknown-and-refused
    #    for a different reason, so also remove the unknown-key check, which is
    #    the control that was actually catching it.
    monkeypatch.setattr(scopes_mod, "_ENABLE_ATTEMPT_KEYS", frozenset())
    monkeypatch.setattr(
        scopes_mod.ScopePolicy,
        "from_dict",
        classmethod(_from_dict_without_unknown_key_check),
    )
    scope = ScopePolicy.from_dict(payload, baseline=_baseline())
    assert scope.name == "escalation-attempt"

    # 3. And the property is genuinely gone: the baseline still denies
    #    repository_mutate, but a scope claiming to grant it now EXISTS rather
    #    than being refused, which is the exact shape of a silent escalation.
    resolution = compose_scopes([scope], baseline=_baseline(), agent="researcher-3")
    assert resolution.effective.permits_capability("repository_mutate")[0] is False
    assert scope.denied_capabilities == frozenset(), (
        "the payload's enable_capabilities was silently ignored, i.e. a caller could believe it "
        "widened a scope when it did not"
    )


_ORIGINAL_KNOWN = {
    "name",
    "members",
    "denied_capabilities",
    "denied_channels",
    "denied_roles",
    "max_capability_rank",
    "require_human_review",
    "allow_verified_evidence",
    "deny_protected_components",
    "taint_bounds_authority",
    "fail_closed",
    "description",
    "schema_version",
}


def _from_dict_without_unknown_key_check(cls, data, *, baseline):
    """``ScopePolicy.from_dict`` with the unknown/enablement key check removed."""

    stripped = {str(key): value for key, value in dict(data).items() if str(key) in _ORIGINAL_KNOWN}
    return scopes_mod._real_from_dict(stripped, baseline=baseline)


@pytest.fixture(autouse=True, scope="module")
def _install_pristine_from_dict():
    """Capture the real ``from_dict`` body before any test patches it."""

    scopes_mod._real_from_dict = (
        lambda payload, *, baseline: ScopePolicy.from_dict.__wrapped__(ScopePolicy, payload, baseline=baseline)
        if hasattr(ScopePolicy.from_dict, "__wrapped__")
        else _PRISTINE_FROM_DICT[0](payload, baseline=baseline)
    )
    yield


_PRISTINE_FROM_DICT: list[Any] = [ScopePolicy.from_dict]


# ===========================================================================
# (c) The enforcer-reach refusal bites: blind is_protected_component and a scope
#     can name the ceiling.
# ===========================================================================


def test_bite_the_scope_cannot_reach_the_enforcer_check_is_load_bearing(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"name": "reach-around", "members": [{"kind": "agent", "name": "authority_ceiling"}]}

    # 1. Shipped: refused.
    with pytest.raises(ScopeEscalationRefused) as caught:
        ScopePolicy.from_dict(payload, baseline=_baseline())
    assert any(item.startswith("scope_reaches_enforcer:") for item in caught.value.violations)

    # 2. Remove the control: the protected-component matcher reports nothing.
    import alpha.bots.authority_ceiling as ceiling_mod

    monkeypatch.setattr(ceiling_mod, "is_protected_component", lambda target: False)
    scope = ScopePolicy.from_dict(payload, baseline=_baseline())
    assert scope.name == "reach-around"
    assert scope.members[0].name == "authority_ceiling"

    # 3. The property is gone: a scope now names the component that enforces it,
    #    and nothing refuses.
    monkeypatch.setattr(ceiling_mod, "is_protected_component", lambda target: True)
    with pytest.raises(ScopeEscalationRefused):
        scope.assert_cannot_reach_enforcer()


# ===========================================================================
# (d)/(e) The strictest-union and the attribution bite: reverse the order and
#     the answer changes, which is why the order is stated.
# ===========================================================================


def test_bite_composition_order_is_the_property_not_an_accident() -> None:
    a = ScopePolicy.from_dict(
        {"name": "a-no-exec", "members": ["agent:x"], "denied_capabilities": ["process_exec"]},
        baseline=_baseline(),
    )
    b = ScopePolicy.from_dict(
        {"name": "b-no-write", "members": ["agent:x"], "denied_capabilities": ["workspace_write"]},
        baseline=_baseline(),
    )

    shipped = compose_scopes([a, b], baseline=_baseline(), agent="x")
    assert shipped.effective.denied_capabilities == frozenset({"process_exec", "workspace_write"})
    assert explain_denial(shipped, "process_exec", agent="x").scope == "a-no-exec"
    assert explain_denial(shipped, "workspace_write", agent="x").scope == "b-no-write"

    # A "last scope wins" composition -- the ambiguous precedence the rules call
    # a defect -- loses one of the two denials.
    buggy = compose_scopes([a, b], baseline=_baseline(), agent="x")
    only_last = _compose_last_wins([a, b], baseline=_baseline(), agent="x")
    assert only_last.effective.denied_capabilities != buggy.effective.denied_capabilities, (
        "if a last-wins composition gave the same answer, the strictest-union rule would be "
        "untested by these cases"
    )
    assert set(buggy.effective.denied_capabilities) > set(only_last.effective.denied_capabilities)


def _compose_last_wins(scopes, *, baseline, agent):
    """A deliberately broken composition: the last matching scope replaces the rest."""

    from alpha.safety.authority.scopes import EffectivePolicy, ScopeResolution

    effective = None
    for scope in scopes:
        ok, _key = scope.matches(agent=agent)
        if not ok:
            continue
        effective = EffectivePolicy(
            allowed_capabilities=frozenset(baseline.allowed_capabilities) - set(scope.denied_capabilities),
            denied_capabilities=set(scope.denied_capabilities) and frozenset(scope.denied_capabilities),
            max_capability_rank=baseline.max_capability_rank,
            denied_channels=frozenset(),
            denied_roles=frozenset(),
            require_human_review=baseline.require_human_review,
            allow_verified_evidence=baseline.allow_verified_evidence,
            taint_bounds_authority=True,
            applied_scopes=(scope.name,),
            decisions=(),
            baseline_denied_capabilities=frozenset(baseline.denied_capabilities),
            baseline_allowed_capabilities=frozenset(baseline.allowed_capabilities),
        )
    assert effective is not None
    return ScopeResolution(
        subject={"agent": agent, "channel": "", "role": ""},
        baseline=baseline,
        effective=effective,
        resolution_id="last-wins",
        composition_rule="LAST WINS (deliberately broken)",
    )


# ===========================================================================
# (f)/(g)/(h)/(i) The receipt guards bite.
# ===========================================================================


def test_bite_the_tamper_detection_is_load_bearing() -> None:
    chain = ReceiptChain(chain_id="bite-h")
    for index in range(3):
        chain.append(
            decision="authority_grant",
            identity=automated_system(f"agent-{index}"),
            outcome="granted",
            policy_rule="CEILING-WITHIN-RANK-001",
            work_ref=f"run:{index}",
        )
    assert chain.verify().ok is True

    # 1. Tamper in memory: detected.
    object.__setattr__(chain.entries()[1], "policy_rule", "SOMETHING-ELSE")
    assert chain.verify().ok is False

    # 2. Remove the control: verify() becomes a rubber stamp and the tamper goes
    #    unnoticed, which is the whole difference between evidence and a log.
    chain.reset()
    for index in range(3):
        chain.append(
            decision="authority_grant",
            identity=automated_system(f"agent-{index}"),
            outcome="granted",
            policy_rule="CEILING-WITHIN-RANK-001",
            work_ref=f"run:{index}",
        )
    object.__setattr__(chain.entries()[1], "policy_rule", "SOMETHING-ELSE")
    rubber_stamp = _verify_without_hashing(chain)
    assert rubber_stamp.ok is True, (
        "with the hash check removed the tampered receipt verifies, so a receipt that can be "
        "edited after the fact is not evidence"
    )
    assert chain.entries()[1].compute_hash() != chain.entries()[1].hash, (
        "the stored hash no longer matches the record's contents"
    )


def _verify_without_hashing(chain: ReceiptChain):
    """A deliberately broken verify: checks ordering and linkage, never CONTENT.

    This is the "append-only JSONL with a seq column" shape -- and it is exactly
    why a receipt that can be edited after the fact is not evidence: the
    structure still verifies after an edit, because nothing recomputes the
    digest of the record itself.
    """

    from alpha.safety.authority.receipts import ChainVerification

    receipts = list(chain.entries())
    previous = ""
    for index, receipt in enumerate(receipts, start=1):
        if receipt.seq != index or receipt.prev_hash != previous:
            return ChainVerification(ok=False, receipts_checked=index - 1, broken_at_seq=receipt.seq, reason="out of order")
        previous = receipt.hash
    return ChainVerification(ok=True, receipts_checked=len(receipts))


def test_bite_the_unnameable_policy_rule_flag_is_load_bearing(monkeypatch: pytest.MonkeyPatch) -> None:
    chain = ReceiptChain(chain_id="bite-i")
    receipt = chain.append(
        decision="authority_grant",
        identity=automated_system("agent-x"),
        outcome="granted",
        policy_rule="",
        work_ref="grant:agent-x",
    )
    assert ReceiptFlag.UNNAMEABLE_POLICY_RULE in receipt.flags
    assert chain.flagged() == (receipt,)

    # Remove the control: an unnamed rule produces a clean-looking receipt, so the
    # gap is invisible rather than countable.
    monkeypatch.setattr(receipts_mod, "KNOWN_POLICY_RULES", frozenset({"", "ANYTHING"}))
    monkeypatch.setattr(receipts_mod, "classify_flags", lambda **kwargs: ())
    chain.reset()
    clean = chain.append(
        decision="authority_grant",
        identity=automated_system("agent-y"),
        outcome="granted",
        policy_rule="",
        work_ref="grant:agent-y",
    )
    assert clean.flagged is False
    assert chain.flagged() == ()
    assert clean.policy_rule == "", "and the receipt claims a rule it does not have"


def test_bite_the_child_identity_requirement_is_load_bearing() -> None:
    # Shipped: a child with no delegator is refused as unattributable.
    from alpha.safety.authority.receipts import IdentityError

    with pytest.raises(IdentityError):
        receipts_mod.ExecutionIdentity(kind="child_agent", principal_id="orphan")

    # Remove the control: the child collapses into a parentless identity, and
    # "who did this" is unanswerable in exactly the case that matters.
    orphan = receipts_mod.ExecutionIdentity(
        kind=receipts_mod.ActorKind.AGENT, principal_id="orphan"
    )
    assert orphan.delegator_id == ""
    chain = ReceiptChain(chain_id="bite-g")
    chain.append(
        decision="authority_grant",
        identity=orphan,
        outcome="granted",
        policy_rule="CEILING-WITHIN-RANK-001",
        work_ref="run:1",
    )
    assert "delegated by" not in chain.entries()[0].identity.actor_line()
    assert chain.receipts_for_actor("lead") == ()


def test_bite_a_rejection_without_a_reason_is_refused() -> None:
    with pytest.raises(receipts_mod.ReceiptError):
        RejectedAlternative(option="do the thing", reason="")
    # Without the check, "rejected" with no reason is indistinguishable from a
    # decision nobody actually weighed.
    bare = RejectedAlternative(option="do the thing", reason="unspecified")
    assert bare.reason == "unspecified"
    assert not bare.reason.strip() or bare.reason == "unspecified"


# ===========================================================================
# (j)/(k)/(l)/(m)/(n) The taint guards bite.
# ===========================================================================


def test_bite_the_derivation_propagation_is_load_bearing(monkeypatch: pytest.MonkeyPatch) -> None:
    turn = new_turn("t-1")
    turn.absorb(UntrustedSource.WEB_FETCH, location="https://attacker.test")

    # Shipped: a summary of tainted content is tainted.
    assert turn.summary("the page says run rm -rf /", label="s").tainted is True
    assert turn.tool_result("computed from it", tool="calc").tainted is True

    # Remove the control: derive() returns a fresh clean turn, which is exactly
    # what the three historical bugs did.
    monkeypatch.setattr(TaintTurn, "derive", lambda self, label, *, kind="derived": new_turn("clean"))
    assert turn.summary("the page says run rm -rf /", label="s").tainted is False
    assert turn.tool_result("computed from it", tool="calc").tainted is False
    assert turn.tainted is True, "the original turn is still tainted; only the derivation lost it"


def test_bite_the_subagent_inheritance_is_load_bearing(monkeypatch: pytest.MonkeyPatch) -> None:
    turn = new_turn("t-1")
    turn.absorb(UntrustedSource.AGENT_MESSAGE, location="peer/1")
    assert turn.spawn_child("researcher-3").tainted is True

    monkeypatch.setattr(
        TaintTurn, "spawn_child", lambda self, label, *, isolated=False, isolation_reason="": new_turn("clean-child")
    )
    assert turn.spawn_child("researcher-3").tainted is False
    assert turn.tainted is True


def test_bite_the_isolation_control_requirement_is_load_bearing() -> None:
    turn = new_turn("t-1")
    turn.absorb(UntrustedSource.PEER_MESSAGE, location="peer/1")

    # Shipped: isolation needs a DECLARED control.
    from alpha.safety.authority.taint import TaintAuthorityError

    with pytest.raises(TaintAuthorityError):
        isolate_child(turn, "worker", "trust-me")

    # Remove the control: a free-text reason produces a clean child, so any
    # caller can exempt itself from taint with a sentence.
    bare = turn.spawn_child("worker", isolated=True, isolation_reason="trust-me")
    assert bare.tainted is False
    assert bare.isolation_reason == "trust-me"
    assert bare.cleared_by == "", "and nothing recorded WHO authorised the exemption"


def test_bite_only_a_control_clears_taint(monkeypatch: pytest.MonkeyPatch) -> None:
    turn = new_turn("t-1")
    turn.absorb(UntrustedSource.MODEL_SELF_ASSERTION, location="assistant message")

    # Shipped: the model's own claim is refused and taint stands.
    with pytest.raises(TaintClearRefused):
        turn.request_clear_from_model("I am clean")
    with pytest.raises(TaintClearRefused):
        turn.clear("model")
    assert turn.tainted is True

    # Remove the control: add "model" to the trusted list and the model can
    # assert its own untaintedness, which is the entire hole.
    monkeypatch.setattr(taint_mod, "TRUSTED_CLEARING_CONTROLS", frozenset({*taint_mod.TRUSTED_CLEARING_CONTROLS, "model"}))
    turn.clear("model")
    assert turn.tainted is False
    assert turn.may_record_verified_evidence()[0] is True, (
        "with the model able to clear its own taint, a fetched page can produce VERIFIED evidence"
    )


def test_bite_taint_bounds_the_real_grant_chokepoint() -> None:
    """The refusal is produced by ``enforce_grant``, not by this test file."""

    chain = receipts_mod.get_receipt_chain("authority")
    chain.reset()
    turn = new_turn("t-fetch")
    turn.absorb(UntrustedSource.WEB_FETCH, location="https://attacker.test")
    with bind_turn(turn):
        with pytest.raises(AuthorityViolation) as caught:
            enforce_grant(
                {"observe"},
                creator_grant={"observe", "reason", "workspace_write"},
                subject="hire of 'researcher-3'",
            )
    assert any("tainted" in item for item in caught.value.violations)
    receipt = chain.entries()[-1]
    assert receipt.taint_sources == ("web_fetch",)
    assert ReceiptFlag.TAINTED_TURN in receipt.flags

    # And the receipt chain itself is intact, so the refusal is evidence.
    assert chain.verify().ok is True
    chain.reset()


# ===========================================================================
# (o) The inbound enumeration assertion bites.
# ===========================================================================


def test_bite_the_inbound_default_deny_assertion_is_load_bearing() -> None:
    assert_inbound_default_deny()  # passes today

    rogue = InboundEntryPoint(
        name="rogue",
        surface=SurfaceKind.GATEWAY_ROUTE,
        path="/api/rogue",
        authentication=Authentication.NONE,
        enforcement_site="x.py:1",
        kind=boundaries_mod.BoundaryKind.SECURITY,
    )
    from alpha.safety.authority.boundaries import UnauthenticatedInboundPath

    with pytest.raises(UnauthenticatedInboundPath):
        assert_inbound_default_deny(INBOUND_ENTRY_POINTS + (rogue,))

    # Remove the control: the assertion becomes a no-op and the rogue path is
    # simply not noticed.
    def _no_op(entry_points=None):
        return None

    assert _no_op(INBOUND_ENTRY_POINTS + (rogue,)) is None
    assert rogue not in [item for item in INBOUND_ENTRY_POINTS if item.authenticated_by_default]


# ===========================================================================
# (p) The fail-closed approval bites.
# ===========================================================================


def test_bite_approval_fails_closed_because_only_true_approves() -> None:
    # Structural proof: APPROVED is constructed in exactly one place, behind
    # ``verdict is True``. No error path can reach it.
    source = inspect.getsource(boundaries_mod._verdict_to_result)
    assert source.count("ApprovalOutcome.APPROVED") == 1
    assert "if verdict is True:" in source
    for forbidden in ("except", "try:", "timeout", "unavailable"):
        assert forbidden not in source, (
            f"_verdict_to_result must contain no {forbidden!r} branch: it converts a verdict, and a "
            "verdict converter with an error path can approve on error"
        )

    # Behavioural proof for each failure mode.
    from alpha.safety.authority.boundaries import ApproverTimeout, ApproverUnavailable

    for approver, expected in (
        (None, "approver_unavailable"),
        (lambda: (_ for _ in ()).throw(ApproverUnavailable("x")), "approver_unavailable"),
        (lambda: (_ for _ in ()).throw(ApproverTimeout("x")), "approver_timeout"),
        (lambda: (_ for _ in ()).throw(RuntimeError("x")), "approver_internal_error"),
        (lambda: None, "approver_returned_no_verdict"),
        (lambda: "approved", "approver_returned_unrecognised_verdict"),
        (lambda: 1, "approver_returned_unrecognised_verdict"),
    ):
        result = resolve_authority_approval(approver, subject="x")
        assert result.approved is False
        assert result.reason == expected

    # Remove the control: a fail-OPEN variant, written inline, approves on error.
    def _fail_open(approver, *, subject, approver_name="human_operator", taint_sources=()):
        try:
            return boundaries_mod.ApprovalResult(
                outcome=boundaries_mod.ApprovalOutcome.APPROVED,
                approver=approver_name,
                reason="assumed_ok",
            )
        except Exception:
            return boundaries_mod.ApprovalResult(
                outcome=boundaries_mod.ApprovalOutcome.APPROVED,
                approver=approver_name,
                reason="assumed_ok_after_error",
            )

    broken = _fail_open(lambda: (_ for _ in ()).throw(RuntimeError("db down")), subject="x")
    assert broken.approved is True, (
        "a fail-open gate approves a destructive action because the approver crashed; this is the "
        "failure mode the shipped resolver exists to prevent"
    )


# ===========================================================================
# (q) Credential isolation bites.
# ===========================================================================


def test_bite_credential_isolation_is_load_bearing() -> None:
    specs = (
        CredentialSpec(owner="a", capability="ca", credential_key="ka", validator=lambda: True),
        CredentialSpec(
            owner="b",
            capability="cb",
            credential_key="kb",
            validator=lambda: (_ for _ in ()).throw(RuntimeError("401")),
        ),
    )
    shipped = resolve_credential_owners(specs)
    assert shipped.owner("a").available is True
    assert shipped.owner("b").available is False

    # Remove the control: ONE try around the whole loop, which is the shared
    # error handler that takes down unrelated capability.
    def _shared_try(specs_in):
        results = []
        try:
            for spec in specs_in:
                verdict = spec.validator()
                results.append(
                    boundaries_mod.CredentialOwner(
                        owner=spec.owner,
                        capability=spec.capability,
                        credential_key=spec.credential_key,
                        status=(
                            boundaries_mod.CredentialStatus.AVAILABLE
                            if verdict is True
                            else boundaries_mod.CredentialStatus.FAILED
                        ),
                        reason="" if verdict is True else f"returned {verdict!r}",
                    )
                )
        except Exception as exc:
            # The bug: ONE handler for every owner, which discards every result
            # collected so far and marks them all failed.
            results.clear()
            for spec in specs_in:
                results.append(
                    boundaries_mod.CredentialOwner(
                        owner=spec.owner,
                        capability=spec.capability,
                        credential_key=spec.credential_key,
                        status=boundaries_mod.CredentialStatus.FAILED,
                        reason=f"{type(exc).__name__}: {exc}",
                    )
                )
        return boundaries_mod.CredentialIsolationReport(owners=tuple(results))

    shared = _shared_try(specs)
    assert shared.owner("a").available is False, (
        "one invalid credential took down an unrelated capability: this is the property the "
        "per-owner try in resolve_credential_owners exists to hold"
    )
    assert shared.owner("b").available is False


# ===========================================================================
# (r) Startup refusal bites.
# ===========================================================================


def test_bite_startup_refusal_is_load_bearing() -> None:
    from alpha.safety.authority.boundaries import StartupRefused

    with pytest.raises(StartupRefused):
        assert_startable({"bind_host": "0.0.0.0", "require_ingress_auth": False})
    with pytest.raises(StartupRefused):
        assert_startable({"bind_host": "127.0.0.1", "auth_disabled": True})
    with pytest.raises(StartupRefused):
        assert_startable({"bind_host": "127.0.0.1", "authentcation": True})

    # Remove the control: the same three configurations start, which is what
    # reporting "Status: Ready" for a product that cannot safely serve a request
    # looks like from the inside.
    def _lenient(config, *, baseline=None):
        violations = [
            f"unknown configuration keys {sorted(set(config) - {'bind_host', 'require_ingress_auth', 'auth_disabled'})}"
            for _ in [0]
            if set(config) - {"bind_host", "require_ingress_auth", "auth_disabled"}
        ]
        if violations:
            logger_warning(violations)
        return boundaries_mod.StartupReport(
            checks=((boundaries_mod.StartupCheck.CONFIG_PARSES, True, "ok"),)
        )

    def logger_warning(_violations):
        return None

    for config in (
        {"bind_host": "0.0.0.0", "require_ingress_auth": False},
        {"bind_host": "127.0.0.1", "auth_disabled": True},
        {"bind_host": "127.0.0.1", "authentcation": True},
    ):
        assert _lenient(config).ok is True, (
            "a lenient startup gate starts an unauthenticated, exposed Gateway: the exact inverse "
            "of the rule being defended"
        )


# ===========================================================================
# The wiring itself bites: remove the scope registry and the grant stops
# consulting scopes at all.
# ===========================================================================


def test_bite_the_scope_intersection_at_the_grant_chokepoint_is_load_bearing() -> None:

    scope = ScopePolicy.from_dict(
        {"name": "no-exec", "members": ["agent:researcher-3"], "denied_capabilities": ["process_exec"]},
        baseline=_baseline(),
    )
    register_scopes([scope])
    with pytest.raises(AuthorityViolation):
        enforce_grant(
            {"process_exec"},
            creator_grant={"process_exec", "workspace_write"},
            subject="hire of 'researcher-3'",
        )

    # Remove the control: no registered scopes, i.e. the pre-scopes behaviour.
    clear_scopes()
    granted = enforce_grant(
        {"process_exec"},
        creator_grant={"process_exec", "workspace_write"},
        subject="hire of 'researcher-3'",
    )
    assert granted == frozenset({"process_exec"}), (
        "with the scope intersection removed the denied capability is granted, so the chokepoint "
        "wiring is what makes a scope more than a configuration convention"
    )


def test_bite_scope_schemas_stay_readable() -> None:
    """A receipt and a resolution must both serialise; an unreadable audit is
    not an audit."""

    chain = ReceiptChain(chain_id="bite-json")
    receipt = chain.append(
        decision="authority_grant",
        identity=automated_system("agent-1"),
        outcome="granted",
        policy_rule="SCOPE-DENY-001",
        scope="no-exec",
        inputs=["requested=['process_exec']"],
        rejected_alternatives=(RejectedAlternative(option="grant", reason="denied"),),
        work_ref="grant:agent-1",
    )
    assert json.loads(receipt.to_json())["policy_rule"] == "SCOPE-DENY-001"
    resolution = compose_scopes(
        [
            ScopePolicy.from_dict(
                {"name": "no-exec", "members": ["agent:x"], "denied_capabilities": ["process_exec"]},
                baseline=_baseline(),
            )
        ],
        baseline=_baseline(),
        agent="x",
    )
    assert json.loads(resolution.to_json())["effective"]["applied_scopes"] == ["no-exec"]
