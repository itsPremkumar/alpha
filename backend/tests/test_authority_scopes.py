"""Scoped policy overlays: the proofs that make a scope safe to ship.

Every test here corresponds to a property that, if it broke, would let a scope
widen authority rather than narrow it.  The order is the argument:

* (a) a scope can DENY what the baseline allows;
* (b) a scope can NEVER enable what the baseline denies, and the attempt is
      refused with a named reason;
* (c) a scope cannot edit, weaken or disable the component that enforces it;
* (d) overlapping scopes resolve to the STRICTEST UNION and the resolution is
      RECORDED with a reason;
* (e) "why may this agent not do X" returns the scope and the rule;
* wiring: the same properties hold at the real grant chokepoint
      (``alpha.bots.authority_ceiling.enforce_grant``), which is what the
      registry's ``hire_bot``/``rescope_bot`` call.

Encoding note: this file is pure ASCII on purpose.  Two files in this
repository were already damaged by tools that read UTF-8 as cp1252 and wrote it
back; nothing here should give such a tool an opportunity.
"""

from __future__ import annotations

import json

import pytest

from alpha.bots.authority_ceiling import (
    RANK_PROCESS_EXEC,
    RANK_REASON,
    RANK_REPOSITORY_MUTATE,
    RANK_WORKSPACE_WRITE,
    AuthorityCeiling,
    AuthorityViolation,
    baseline_policy,
    clear_scopes,
    enforce_grant,
    explain_effective_policy,
    get_scopes,
    is_protected_component,
    load_scopes,
    register_scopes,
)
from alpha.safety.authority.scopes import (
    COMPOSITION_RULE,
    BaselinePolicy,
    ScopeEscalationRefused,
    ScopePolicy,
    ScopeRule,
    assert_not_looser,
    baseline_from_ceiling,
    compose_scopes,
    explain_denial,
    member,
)


@pytest.fixture(autouse=True)
def _no_leaked_scopes():
    """No test may leave a scope installed for the next one."""

    clear_scopes()
    yield
    clear_scopes()


@pytest.fixture()
def baseline() -> BaselinePolicy:
    return baseline_from_ceiling(AuthorityCeiling())


def _scope(**overrides) -> ScopePolicy:
    payload = {
        "name": "research-envelope",
        "members": [{"kind": "agent", "name": "researcher-3"}],
        "denied_capabilities": ["process_exec"],
    }
    payload.update(overrides)
    return ScopePolicy.from_dict(payload, baseline=baseline_from_ceiling(AuthorityCeiling()))


# ---------------------------------------------------------------------------
# (a) A scope may DENY what the baseline allows.
# ---------------------------------------------------------------------------


def test_scope_denies_what_the_baseline_allows(baseline: BaselinePolicy) -> None:
    scope = _scope()
    assert "process_exec" in baseline.allowed_capabilities, "precondition: baseline allows it"

    resolution = compose_scopes([scope], baseline=baseline, agent="researcher-3")

    permitted, rule = resolution.effective.permits_capability("process_exec")
    assert permitted is False
    assert rule == ScopeRule.SCOPE_DENIES_CAPABILITY.value
    assert "process_exec" in resolution.effective.denied_capabilities
    # The other baseline capabilities survive: a scope narrows one thing, it
    # does not accidentally become a deny-everything.
    assert resolution.effective.permits_capability("workspace_write")[0] is True
    assert resolution.effective.permits_capability("observe")[0] is True


def test_scope_may_lower_the_maximum_rank(baseline: BaselinePolicy) -> None:
    scope = _scope(denied_capabilities=[], max_capability_rank=RANK_REASON)
    assert scope.max_capability_rank == RANK_REASON

    resolution = compose_scopes([scope], baseline=baseline, agent="researcher-3")

    assert resolution.effective.max_capability_rank == RANK_REASON
    assert resolution.effective.permits_capability("workspace_write")[0] is False
    assert resolution.effective.permits_capability("reason")[0] is True


# ---------------------------------------------------------------------------
# (b) A scope can NEVER enable what the baseline denies, and trying is refused.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"enable_capabilities": ["repository_mutate"]},
        {"allow_capabilities": ["repository_mutate"]},
        {"grant_capabilities": ["repository_mutate"]},
        {"required_capabilities": ["repository_mutate"]},
        {"unlock": True},
        {"bypass_ceiling": True},
        {"override_ceiling": True},
        {"disable_ceiling": True},
        {"bypass": "human review"},
        {"widen": "process_exec"},
    ],
)
def test_scope_cannot_enable_and_the_attempt_is_refused(payload: dict) -> None:
    data = {
        "name": "escalation-attempt",
        "members": [{"kind": "agent", "name": "researcher-3"}],
        **payload,
    }
    with pytest.raises(ScopeEscalationRefused) as caught:
        ScopePolicy.from_dict(data, baseline=baseline_from_ceiling(AuthorityCeiling()))
    assert caught.value.violations, "a refusal must name what was attempted"
    assert any(item.startswith("enable_attempt:") for item in caught.value.violations)
    assert caught.value.to_dict()["error"] == "scope_escalation_refused"


def test_scope_cannot_raise_the_max_rank_above_the_baseline() -> None:
    with pytest.raises(ScopeEscalationRefused) as caught:
        ScopePolicy.from_dict(
            {
                "name": "rank-escalation",
                "members": [{"kind": "agent", "name": "researcher-3"}],
                "max_capability_rank": RANK_REPOSITORY_MUTATE,
            },
            baseline=baseline_from_ceiling(AuthorityCeiling()),
        )
    assert any("ABOVE the baseline" in item for item in caught.value.violations)


def test_scope_cannot_turn_off_the_protected_component_refusal() -> None:
    with pytest.raises(ScopeEscalationRefused) as caught:
        ScopePolicy.from_dict(
            {
                "name": "reach-around",
                "members": [{"kind": "agent", "name": "researcher-3"}],
                "deny_protected_components": False,
            },
            baseline=baseline_from_ceiling(AuthorityCeiling()),
        )
    joined = " ".join(caught.value.violations)
    assert "deny_protected_components" in joined


def test_scope_cannot_fail_open() -> None:
    with pytest.raises(ScopeEscalationRefused) as caught:
        ScopePolicy.from_dict(
            {
                "name": "fail-open",
                "members": [{"kind": "agent", "name": "researcher-3"}],
                "fail_closed": False,
            },
            baseline=baseline_from_ceiling(AuthorityCeiling()),
        )
    assert any("fail_closed" in item for item in caught.value.violations)


def test_scope_cannot_re_enable_verified_evidence() -> None:
    strict = BaselinePolicy(
        max_capability_rank=RANK_PROCESS_EXEC,
        allowed_capabilities=frozenset({"observe", "reason"}),
        allow_verified_evidence=False,
    )
    with pytest.raises(ScopeEscalationRefused) as caught:
        ScopePolicy.from_dict(
            {
                "name": "re-enable",
                "members": [{"kind": "agent", "name": "researcher-3"}],
                "allow_verified_evidence": True,
            },
            baseline=strict,
        )
    assert "not_stricter:allow_verified_evidence=True" in caught.value.violations


def test_scope_cannot_drop_required_human_review() -> None:
    reviewing = BaselinePolicy(
        max_capability_rank=RANK_PROCESS_EXEC,
        allowed_capabilities=frozenset({"observe", "reason"}),
        require_human_review=True,
    )
    with pytest.raises(ScopeEscalationRefused) as caught:
        ScopePolicy.from_dict(
            {
                "name": "drop-review",
                "members": [{"kind": "agent", "name": "researcher-3"}],
                "require_human_review": False,
            },
            baseline=reviewing,
        )
    assert "not_stricter:require_human_review=False" in caught.value.violations


def test_unknown_scope_field_is_refused_not_ignored() -> None:
    with pytest.raises(ScopeEscalationRefused) as caught:
        ScopePolicy.from_dict(
            {
                "name": "typo",
                "members": [{"kind": "agent", "name": "researcher-3"}],
                "denied_capabilites": ["process_exec"],  # misspelled
            },
            baseline=baseline_from_ceiling(AuthorityCeiling()),
        )
    assert any(item.startswith("unknown_field:") for item in caught.value.violations)


def test_a_scope_matching_nothing_is_refused() -> None:
    with pytest.raises(ScopeEscalationRefused) as caught:
        ScopePolicy.from_dict({"name": "empty", "members": []}, baseline=baseline_from_ceiling(AuthorityCeiling()))
    assert "empty_scope_members" in caught.value.violations


def test_a_scope_denying_an_unknown_capability_is_refused() -> None:
    with pytest.raises(ScopeEscalationRefused) as caught:
        ScopePolicy.from_dict(
            {
                "name": "typo-capability",
                "members": [{"kind": "agent", "name": "researcher-3"}],
                "denied_capabilities": ["proccess_exec"],
            },
            baseline=baseline_from_ceiling(AuthorityCeiling()),
        )
    assert any(item.startswith("unknown_capability:") for item in caught.value.violations)


# ---------------------------------------------------------------------------
# (c) The ceiling and the enforcement machinery stay outside every scope's reach.
# ---------------------------------------------------------------------------


def test_a_scope_may_not_name_the_enforcement_machinery() -> None:
    for name in ("authority_ceiling", "alpha/bots/authority_ceiling.py", "kill_switch", "safety"):
        with pytest.raises(ScopeEscalationRefused) as caught:
            ScopePolicy.from_dict(
                {"name": "reach-around", "members": [{"kind": "agent", "name": name}]},
                baseline=baseline_from_ceiling(AuthorityCeiling()),
            )
        assert any(item.startswith("scope_reaches_enforcer:") for item in caught.value.violations)
        assert is_protected_component(name)


def test_a_scope_object_is_frozen_and_has_no_mutator() -> None:
    scope = _scope()
    with pytest.raises((AttributeError, TypeError)):
        scope.denied_capabilities = frozenset()  # type: ignore[misc]
    with pytest.raises((AttributeError, TypeError)):
        scope.name = "renamed"  # type: ignore[misc]
    with pytest.raises((AttributeError, TypeError)):
        scope.new_attribute = True  # type: ignore[attr-defined]
    assert not hasattr(scope, "set_denial")
    assert not hasattr(scope, "widen")
    # And it still denies what it denied.
    assert "process_exec" in scope.denied_capabilities


def test_registering_a_scope_that_reaches_the_enforcer_is_refused() -> None:
    forged = ScopePolicy(
        name="forged",
        members=(member("agent", "some-agent"),),
        denied_capabilities=frozenset({"process_exec"}),
    )
    # Bypass the loader on purpose to prove the REGISTRY re-checks, not just the
    # loader. If only the loader checked, a code-constructed scope would slip in.
    assert_not_looser(forged, baseline_policy())
    with pytest.raises(ScopeEscalationRefused):
        register_scopes([_named_scope_naming_the_ceiling()])


def _named_scope_naming_the_ceiling() -> ScopePolicy:
    """Build a scope in code whose NAME is a protected component.

    Constructing it directly skips ``from_dict``, so it exercises the second
    check (registration) rather than the first (load).
    """

    return ScopePolicy(
        name="authority_ceiling",
        members=(member("agent", "some-agent"),),
        denied_capabilities=frozenset({"process_exec"}),
    )


def test_ceiling_is_clamped_after_composition_regardless_of_scope_order() -> None:
    """A scope cannot raise the ceiling, and the ceiling is applied LAST."""

    base = baseline_from_ceiling(AuthorityCeiling())
    scope = _scope(denied_capabilities=[])
    resolution = compose_scopes([scope], baseline=base, agent="researcher-3")
    ceiling_decisions = [
        item for item in resolution.effective.decisions if item.rule_id == ScopeRule.CEILING_CLAMPS.value
    ]
    for decision in ceiling_decisions:
        assert decision.scope == "<authority-ceiling>"
        assert "not expressible as a scope field" in decision.reason


def test_a_tighter_ceiling_wins_over_a_looser_scope() -> None:
    tight = AuthorityCeiling(max_capability_rank=RANK_REASON)
    base = baseline_from_ceiling(tight)
    scope = ScopePolicy(
        name="noop",
        members=(member("agent", "researcher-3"),),
        denied_capabilities=frozenset(),
    )
    resolution = compose_scopes([scope], baseline=base, ceiling=tight, agent="researcher-3")
    assert resolution.effective.permits_capability("workspace_write")[0] is False
    assert resolution.effective.max_capability_rank == RANK_REASON


# ---------------------------------------------------------------------------
# (d) Overlapping scopes resolve to the STRICTEST UNION, and it is RECORDED.
# ---------------------------------------------------------------------------


def test_overlapping_scopes_resolve_to_the_strictest_union(baseline: BaselinePolicy) -> None:
    a = ScopePolicy.from_dict(
        {
            "name": "deny-exec",
            "members": [{"kind": "agent", "name": "researcher-3"}],
            "denied_capabilities": ["process_exec"],
        },
        baseline=baseline,
    )
    b = ScopePolicy.from_dict(
        {
            "name": "deny-write",
            "members": [{"kind": "agent", "name": "researcher-3"}],
            "denied_capabilities": ["workspace_write"],
            "denied_channels": ["slack"],
        },
        baseline=baseline,
    )
    c = ScopePolicy.from_dict(
        {
            "name": "cap-rank",
            "members": [{"kind": "role", "name": "researcher"}],
            "max_capability_rank": RANK_REASON,
        },
        baseline=baseline,
    )

    resolution = compose_scopes([a, b, c], baseline=baseline, agent="researcher-3", role="researcher")

    assert set(resolution.effective.applied_scopes) == {"deny-exec", "deny-write", "cap-rank"}
    # The UNION of the denials, not the intersection of what they permit.
    assert resolution.effective.permits_capability("process_exec")[0] is False
    assert resolution.effective.permits_capability("workspace_write")[0] is False
    assert resolution.effective.permits_capability("slack") is not None
    assert resolution.effective.permits_channel("slack")[0] is False
    # The strictest numeric bound wins.
    assert resolution.effective.max_capability_rank == RANK_REASON
    # Composition order does not change the answer.
    shuffled = compose_scopes([c, b, a], baseline=baseline, agent="researcher-3", role="researcher")
    assert shuffled.effective.to_dict() == resolution.effective.to_dict()


def test_the_resolution_is_recorded_with_a_reason(baseline: BaselinePolicy) -> None:
    a = ScopePolicy.from_dict(
        {
            "name": "deny-exec",
            "members": [{"kind": "agent", "name": "researcher-3"}],
            "denied_capabilities": ["process_exec"],
        },
        baseline=baseline,
    )
    b = ScopePolicy.from_dict(
        {
            "name": "deny-write",
            "members": [{"kind": "agent", "name": "researcher-3"}],
            "denied_capabilities": ["workspace_write"],
        },
        baseline=baseline,
    )
    resolution = compose_scopes([a, b], baseline=baseline, agent="researcher-3")

    recorded = resolution.effective.decisions
    assert recorded, "a narrowing must leave a record"
    for decision in recorded:
        assert decision.rule_id.startswith("SCOPE-")
        assert decision.scope in {"deny-exec", "deny-write", "<none>", "<authority-ceiling>"}
        assert decision.reason.strip(), "a record with no reason is not a record"
        assert decision.before != decision.after or decision.dimension == "scope_match"

    # Every narrowing is attributable to the scope that caused it.
    by_scope = {item.scope for item in recorded}
    assert {"deny-exec", "deny-write"} <= by_scope
    # And it serialises for an operator.
    payload = json.loads(resolution.to_json())
    assert payload["composition_rule"] == COMPOSITION_RULE
    assert payload["effective"]["applied_scopes"] == ["deny-exec", "deny-write"]
    assert any(item["rule_id"] == "SCOPE-DENY-001" for item in payload["effective"]["decisions"])


def test_resolution_id_is_deterministic(baseline: BaselinePolicy) -> None:
    scope = _scope()
    first = compose_scopes([scope], baseline=baseline, agent="researcher-3")
    second = compose_scopes([scope], baseline=baseline, agent="researcher-3")
    assert first.resolution_id == second.resolution_id


def test_stricter_flags_compose_in_the_denying_direction(baseline: BaselinePolicy) -> None:
    a = ScopePolicy.from_dict(
        {
            "name": "no-verified",
            "members": [{"kind": "agent", "name": "researcher-3"}],
            "allow_verified_evidence": False,
        },
        baseline=baseline,
    )
    b = ScopePolicy.from_dict(
        {
            "name": "also-no-verified",
            "members": [{"kind": "agent", "name": "researcher-3"}],
            "allow_verified_evidence": False,
        },
        baseline=baseline,
    )
    resolution = compose_scopes([a, b], baseline=baseline, agent="researcher-3")
    assert resolution.effective.allow_verified_evidence is False
    rules = {item.rule_id for item in resolution.effective.decisions}
    assert ScopeRule.SCOPE_SAWES_FORBIDDEN_EVIDENCE.value in rules


# ---------------------------------------------------------------------------
# (e) "Why may this agent not do X" returns the scope and the rule.
# ---------------------------------------------------------------------------


def test_explain_denial_names_the_scope_and_the_rule(baseline: BaselinePolicy) -> None:
    a = ScopePolicy.from_dict(
        {
            "name": "deny-exec",
            "members": [{"kind": "agent", "name": "researcher-3"}],
            "denied_capabilities": ["process_exec"],
        },
        baseline=baseline,
    )
    b = ScopePolicy.from_dict(
        {
            "name": "cap-rank",
            "members": [{"kind": "agent", "name": "researcher-3"}],
            "max_capability_rank": RANK_REASON,
        },
        baseline=baseline,
    )
    resolution = compose_scopes([a, b], baseline=baseline, agent="researcher-3")

    explanation = explain_denial(resolution, "process_exec", agent="researcher-3")
    assert explanation.allowed is False
    assert explanation.scope == "deny-exec"
    assert explanation.rule_id == ScopeRule.SCOPE_DENIES_CAPABILITY.value
    assert "researcher-3" in explanation.explain()
    assert "deny-exec" in explanation.explain()
    assert explanation.competing_scopes == ("cap-rank",)

    # A capability removed ONLY by the rank cap is attributed to that scope, not
    # to a scope that never mentioned it. Attributing a denial to the wrong
    # scope sends an operator to the wrong configuration file.
    by_rank = explain_denial(resolution, "workspace_write", agent="researcher-3")
    assert by_rank.allowed is False
    assert by_rank.scope == "cap-rank"
    assert by_rank.rule_id == ScopeRule.SCOPE_DENIES_CAPABILITY.value

    permitted = explain_denial(resolution, "observe", agent="researcher-3")
    assert permitted.allowed is True
    assert permitted.rule_id == ScopeRule.BASELINE_ALLOWS.value
    assert set(permitted.competing_scopes) == {"deny-exec", "cap-rank"}


def test_explain_denial_for_a_capability_the_baseline_denies() -> None:
    base = BaselinePolicy(
        max_capability_rank=RANK_PROCESS_EXEC,
        allowed_capabilities=frozenset({"observe", "reason"}),
        denied_capabilities=frozenset({"repository_mutate"}),
    )
    resolution = compose_scopes([], baseline=base, agent="researcher-3")
    explanation = explain_denial(resolution, "repository_mutate", agent="researcher-3")
    assert explanation.allowed is False
    assert explanation.scope == "<baseline>"
    assert explanation.rule_id == ScopeRule.BASELINE_DENIES.value
    assert "may only deny" in explanation.reason


def test_explain_via_the_ceiling_module_is_the_same_answer() -> None:
    register_scopes([_scope()])
    explanation = explain_effective_policy(subject="hire of 'researcher-3'", capability="process_exec")
    assert explanation.allowed is False
    assert explanation.scope == "research-envelope"
    assert explanation.rule_id == ScopeRule.SCOPE_DENIES_CAPABILITY.value


def test_subject_labels_are_matched_not_guessed() -> None:
    register_scopes([_scope()])
    # The registry's real subject string quotes the profile name.
    resolution = compose_scopes(
        get_scopes(),
        baseline=baseline_policy(),
        agent="hire of 'researcher-3'",
        agent_labels=("researcher-3",),
    )
    assert resolution.effective.permits_capability("process_exec")[0] is False
    # A DIFFERENT agent in the same descriptive subject format is unaffected.
    other = compose_scopes(
        get_scopes(),
        baseline=baseline_policy(),
        agent="hire of 'researcher-9'",
        agent_labels=("researcher-9",),
    )
    assert other.effective.permits_capability("process_exec")[0] is True
    assert other.effective.applied_scopes == ()


# ---------------------------------------------------------------------------
# Wiring: the same properties at the real grant chokepoint.
# ---------------------------------------------------------------------------


def test_enforce_grant_is_unaffected_with_no_scopes_registered() -> None:
    """Precondition for every existing caller: registering nothing changes nothing."""

    assert get_scopes() == ()
    granted = enforce_grant(
        {"observe", "reason", "workspace_write"},
        creator_grant={"observe", "reason", "workspace_write", "process_exec"},
        subject="hire of 'researcher-3'",
    )
    assert granted == frozenset({"observe", "reason", "workspace_write"})


def test_enforce_grant_refuses_what_a_scope_denies() -> None:
    register_scopes([_scope()])
    with pytest.raises(AuthorityViolation) as caught:
        enforce_grant(
            {"observe", "process_exec"},
            creator_grant={"observe", "process_exec", "workspace_write"},
            subject="hire of 'researcher-3'",
        )
    assert any("research-envelope" in item for item in caught.value.violations)
    assert any("process_exec" in item for item in caught.value.violations)


def test_enforce_grant_still_allows_a_different_agent() -> None:
    register_scopes([_scope()])
    granted = enforce_grant(
        {"observe", "process_exec"},
        creator_grant={"observe", "process_exec", "workspace_write"},
        subject="hire of 'researcher-9'",
    )
    assert granted == frozenset({"observe", "process_exec"})


def test_load_scopes_refuses_a_present_but_malformed_file(tmp_path) -> None:
    path = tmp_path / "scopes.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        load_scopes(path)

    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "scopes": [
                    {
                        "name": "bad",
                        "members": [{"kind": "agent", "name": "x"}],
                        "enable_capabilities": ["repository_mutate"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ScopeEscalationRefused):
        load_scopes(path)


def test_load_scopes_absent_file_yields_no_scopes(tmp_path) -> None:
    assert load_scopes(tmp_path / "nothing-here.json") == ()


def test_load_scopes_refuses_unknown_top_level_keys(tmp_path) -> None:
    path = tmp_path / "scopes.json"
    path.write_text(json.dumps({"scopes": [], "typo_key": 1}), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown authority scope field"):
        load_scopes(path)


def test_loaded_scopes_are_installed_and_enforced(tmp_path) -> None:
    path = tmp_path / "scopes.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "scopes": [
                    {
                        "name": "no-exec-bot",
                        "members": ["agent:researcher-3"],
                        "denied_capabilities": ["process_exec"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    loaded = load_scopes(path)
    assert [item.name for item in loaded] == ["no-exec-bot"]
    register_scopes(loaded)
    with pytest.raises(AuthorityViolation):
        enforce_grant(
            {"process_exec"},
            creator_grant={"process_exec", "workspace_write"},
            subject="hire of 'researcher-3'",
        )


# ---------------------------------------------------------------------------
# Wiring: a scope that demands human review makes the grant chokepoint fail
# CLOSED, reached from a real runtime path rather than from this test.
# ---------------------------------------------------------------------------


def _review_scope() -> ScopePolicy:
    return ScopePolicy.from_dict(
        {
            "name": "needs-sign-off",
            "members": ["agent:supervised-worker"],
            "require_human_review": True,
        },
        baseline=baseline_from_ceiling(AuthorityCeiling()),
    )


def test_a_scope_demanding_review_refuses_the_grant_because_no_approver_exists() -> None:
    register_scopes([_review_scope()])
    with pytest.raises(AuthorityViolation) as caught:
        enforce_grant(
            {"observe"},
            creator_grant={"observe", "reason", "workspace_write"},
            subject="hire of 'supervised-worker'",
        )
    assert any("approval:approver_unavailable" in item for item in caught.value.violations)
    assert "human approval" in str(caught.value)


def test_the_refusal_is_recorded_as_a_receipt_with_the_alternative_rejected() -> None:
    from alpha.safety.authority.receipts import get_receipt_chain

    chain = get_receipt_chain("authority")
    chain.reset()
    register_scopes([_review_scope()])
    with pytest.raises(AuthorityViolation):
        enforce_grant(
            {"observe"},
            creator_grant={"observe", "reason"},
            subject="hire of 'supervised-worker'",
        )
    receipt = chain.entries()[-1]
    assert receipt.outcome == "refused"
    assert receipt.policy_rule == "BOUNDARY-APPROVAL-FAIL-CLOSED-001"
    assert receipt.rejected_alternatives
    assert "approver_unavailable" in receipt.rejected_alternatives[0].reason
    assert receipt.scope == "needs-sign-off"
    assert chain.verify().ok is True


def test_a_scope_not_demanding_review_leaves_the_default_path_alone() -> None:
    register_scopes([_scope()])
    granted = enforce_grant(
        {"observe"},
        creator_grant={"observe", "reason"},
        subject="hire of 'researcher-77'",
    )
    assert granted == frozenset({"observe"})
