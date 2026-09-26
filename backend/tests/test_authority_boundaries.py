"""Boundaries: default-deny inbound, fail-closed approval, owner-isolated
credentials, and a startup that refuses bad configuration.

Proofs:
  (o) EVERY inbound entry point is authenticated by default, and the
      enumeration test FAILS on an added unauthenticated one;
  (p) an unavailable approver, an approver timeout and an internal error each
      produce REFUSED, never ALLOWED;
  (q) one invalid credential degrades ONLY its own owner;
  (r) invalid configuration REFUSES TO START rather than starting degraded.

Plus the security-vs-convenience classification, which is the deliverable
rather than a detail: a convenience boundary presented as a security boundary
is worse than none, because it retires the question.

Pure ASCII on purpose: two files in this repository were already damaged by
tools that read UTF-8 as cp1252 and wrote it back.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from alpha.safety.authority.boundaries import (
    INBOUND_ENTRY_POINTS,
    ApprovalOutcome,
    ApproverTimeout,
    ApproverUnavailable,
    Authentication,
    BoundaryError,
    BoundaryKind,
    CredentialSpec,
    InboundEntryPoint,
    StartupRefused,
    SurfaceKind,
    UnauthenticatedInboundPath,
    assert_credential_failures_isolated,
    assert_inbound_default_deny,
    assert_startable,
    boundary_inventory,
    convenience_boundaries,
    declared_public_exemptions,
    resolve_authority_approval,
    resolve_credential_owners,
    security_boundaries,
    unauthenticated_inbound_paths,
    undeclared_unauthenticated_inbound_paths,
)
from alpha.safety.authority.models import GatePosture

# ===========================================================================
# (o) Every inbound entry point is authenticated by default.
# ===========================================================================


def test_every_inbound_entry_point_is_classified_with_an_enforcement_site() -> None:
    assert INBOUND_ENTRY_POINTS, "an empty enumeration proves nothing"
    for entry in INBOUND_ENTRY_POINTS:
        assert entry.name.strip(), "an entry point needs a name"
        assert entry.surface in SurfaceKind
        assert entry.kind in BoundaryKind, f"{entry.name} has no security/convenience classification"
        assert entry.enforcement_site.strip(), (
            f"{entry.name} names no enforcement site, so a reviewer cannot go and read the check"
        )
        assert ":" in entry.enforcement_site, f"{entry.name} enforcement site is not file:line"
        assert entry.default_posture in GatePosture


def test_the_enumeration_assertion_passes_today() -> None:
    # Every unauthenticated entry point either does not exist, or has a reviewed
    # exemption on record. The three that do are the login/register endpoints,
    # the public capability card, and the operator-selected auth-disabled mode.
    assert undeclared_unauthenticated_inbound_paths() == ()
    assert_inbound_default_deny()


def test_the_three_unauthenticated_paths_are_exempted_with_a_reason() -> None:
    exempt = {item.name for item in INBOUND_ENTRY_POINTS if item.is_exempted}
    assert exempt == {
        "gateway-http-auth-disabled",
        "auth-login-register-status",
        "peer-discovery-card",
    }
    for entry in INBOUND_ENTRY_POINTS:
        if entry.is_exempted:
            assert len(entry.exemption) > 40, f"{entry.name} exemption is too thin to be a review"


def test_the_enumeration_assertion_fails_on_an_added_unauthenticated_entry_point() -> None:
    """The property that makes the enumeration worth having."""

    rogue = InboundEntryPoint(
        name="rogue-webhook",
        surface=SurfaceKind.WEBHOOK,
        path="/api/webhooks/internal-trigger",
        authentication=Authentication.NONE,
        enforcement_site="backend/app/gateway/routers/rogue.py:10",
        kind=BoundaryKind.SECURITY,
        note="added by a well-meaning change with no credential check",
    )
    augmented = INBOUND_ENTRY_POINTS + (rogue,)

    with pytest.raises(UnauthenticatedInboundPath) as caught:
        assert_inbound_default_deny(augmented)

    assert caught.value.offenders == (rogue,)
    assert "rogue-webhook" in str(caught.value)
    payload = caught.value.to_dict()
    assert payload["error"] == "unauthenticated_inbound_path"
    assert payload["offenders"][0]["path"] == "/api/webhooks/internal-trigger"
    # The unauthenticated list names it too, so an operator can find it without
    # running the assertion.
    assert rogue in unauthenticated_inbound_paths(augmented)
    assert rogue in undeclared_unauthenticated_inbound_paths(augmented)


def test_an_unauthenticated_path_defaults_to_default_allow_and_says_so() -> None:
    rogue = InboundEntryPoint(
        name="rogue",
        surface=SurfaceKind.GATEWAY_ROUTE,
        path="/api/rogue",
        authentication=Authentication.NONE,
        enforcement_site="x.py:1",
        kind=BoundaryKind.SECURITY,
    )
    assert rogue.authenticated_by_default is False
    assert rogue.default_posture is GatePosture.DEFAULT_ALLOW
    # And a real one is default-deny.
    assert INBOUND_ENTRY_POINTS[0].default_posture is GatePosture.DEFAULT_DENY


def test_a_websocket_entry_point_is_present_and_authenticated() -> None:
    """A websocket bypasses BaseHTTPMiddleware, so it must check its own token."""

    websockets = [item for item in INBOUND_ENTRY_POINTS if item.surface is SurfaceKind.WEBSOCKET]
    assert websockets, "the peer websocket is an inbound entry point and must be enumerated"
    for entry in websockets:
        assert entry.authenticated_by_default is True
        assert "BaseHTTPMiddleware" in entry.note or "AuthMiddleware" in entry.note


def test_every_public_middleware_exemption_has_a_classified_entry() -> None:
    """A new exemption cannot be added to the middleware without an entry here."""

    from alpha.safety.authority.boundaries import unclassified_public_exemptions

    exemptions = declared_public_exemptions()
    if not exemptions:
        pytest.skip("auth_middleware.py is not resolvable from this working directory")
    unclassified = unclassified_public_exemptions()
    assert unclassified == (), (
        "these middleware auth exemptions have no classified entry in INBOUND_ENTRY_POINTS: "
        f"{unclassified}"
    )


def test_the_known_unauthenticated_paths_are_declared_not_hidden() -> None:
    """The auth-disabled override is a real unauthenticated path; name it."""

    unauthenticated = unauthenticated_inbound_paths()
    names = {item.name for item in unauthenticated}
    assert "gateway-http-auth-disabled" in names
    assert "auth-login-register-status" in names, (
        "the login/register exemptions are unauthenticated by necessity and must be listed, "
        "with the credential check that makes them safe stated in the note"
    )
    assert "peer-discovery-card" in names
    for item in unauthenticated:
        assert item.note.strip(), f"{item.name} is unauthenticated and explains nothing"
        assert item.is_exempted, f"{item.name} is unauthenticated with no reviewed exemption"
    # And a convenience boundary presented as a security one would be worse than
    # none, so the public card is explicitly NOT a security boundary.
    card = next(item for item in unauthenticated if item.name == "peer-discovery-card")
    assert card.is_security_boundary is False
    assert "no authority" in card.note


# ---------------------------------------------------------------------------
# SECURITY vs CONVENIENCE: the deliverable, not a detail.
# ---------------------------------------------------------------------------


def test_the_security_vs_convenience_split_is_stated_and_partitioned() -> None:
    security = security_boundaries()
    convenience = convenience_boundaries()
    assert security, "a product with no security boundary at all would be a finding"
    assert convenience, "a product with no convenience boundary would be a finding too"

    security_names = {item.name for item in security}
    convenience_names = {item.name for item in convenience}
    assert not (security_names & convenience_names), "an entry point is exactly one kind"
    assert security_names | convenience_names == {item.name for item in INBOUND_ENTRY_POINTS}


def test_a_security_boundary_is_one_where_the_far_side_must_prove_a_credential() -> None:
    for entry in security_boundaries():
        assert entry.authenticated_by_default or entry.name == "auth-login-register-status", (
            f"{entry.name} is classified SECURITY but requires no credential from the far side"
        )
        # An unauthenticated SECURITY entry point is only defensible when the
        # credential check happens inside the handler, and that has to be said.
        if not entry.authenticated_by_default:
            assert entry.is_exempted
            assert "handler" in entry.exemption or "handler" in entry.note


def test_the_inventory_states_the_envelope_honestly() -> None:
    inventory = boundary_inventory()
    statement = inventory["statement"]
    assert "one OS user" in statement
    assert "one trust envelope" in statement
    assert "CONVENIENCE" in statement
    # Every control this wave added is a convenience boundary over cooperating
    # code, and the inventory says so rather than implying otherwise.
    assert inventory["security_boundaries"]
    assert inventory["convenience_boundaries"]
    for entry in inventory["convenience_boundaries"]:
        assert entry["kind"] == "convenience"


def test_the_a2a_finding_is_enumerated_as_authenticated_but_not_authorized() -> None:
    a2a = next(item for item in INBOUND_ENTRY_POINTS if item.name == "a2a-cards-and-delegate")
    assert a2a.authenticated_by_default is True
    assert a2a.is_security_boundary is False, "authenticated is not the same as authorized"
    assert a2a.kind is BoundaryKind.CONVENIENCE
    assert "require_permission" in a2a.note
    assert "sender_agent_id" in a2a.note


# ===========================================================================
# (p) Approval paths FAIL CLOSED.
# ===========================================================================


def test_an_unavailable_approver_refuses() -> None:
    result = resolve_authority_approval(None, subject="delete production database")
    assert result.approved is False
    assert result.outcome is ApprovalOutcome.REFUSED
    assert result.reason == "approver_unavailable"
    assert "an unconfigured gate refuses" in result.detail


def test_an_approver_reporting_unavailable_refuses() -> None:
    def approver():
        raise ApproverUnavailable("the operator's phone is off")

    result = resolve_authority_approval(approver, subject="rm -rf /")
    assert result.approved is False
    assert result.reason == "approver_unavailable"
    assert "phone is off" in result.detail


def test_an_approver_timeout_refuses() -> None:
    def approver():
        raise ApproverTimeout("no answer after 30s")

    result = resolve_authority_approval(approver, subject="git push --force")
    assert result.approved is False
    assert result.reason == "approver_timeout"
    assert "no answer after 30s" in result.detail


def test_an_approver_internal_error_refuses() -> None:
    def approver():
        raise RuntimeError("database connection reset")

    result = resolve_authority_approval(approver, subject="drop table users")
    assert result.approved is False
    assert result.reason == "approver_internal_error"
    assert "RuntimeError" in result.detail


@pytest.mark.parametrize(
    "value",
    [None, False, 0, "", [], {}, "yes", "approved", 1, object()],
    ids=["none", "false", "zero", "empty-str", "empty-list", "empty-dict", "yes", "approved", "one", "object"],
)
def test_only_the_explicit_boolean_true_is_an_approval(value: object) -> None:
    result = resolve_authority_approval(lambda: value, subject="anything")
    assert result.approved is False, f"{value!r} must not read as an approval"
    assert result.outcome is ApprovalOutcome.REFUSED
    assert result.reason in {
        "approver_declined",
        "approver_returned_no_verdict",
        "approver_returned_unrecognised_verdict",
    }


def test_an_explicit_true_is_the_only_approval() -> None:
    result = resolve_authority_approval(lambda: True, subject="deploy production")
    assert result.approved is True
    assert result.outcome is ApprovalOutcome.APPROVED
    assert result.reason == "explicit_approval"


def test_a_tainted_turn_cannot_satisfy_an_approval_even_with_an_approver() -> None:
    result = resolve_authority_approval(
        lambda: True,
        subject="deploy production",
        taint_sources=["web_fetch"],
    )
    assert result.approved is False
    assert result.reason == "tainted_turn_cannot_satisfy_approval"
    assert result.taint_sources == ("web_fetch",)


@pytest.mark.asyncio
async def test_the_async_resolver_also_fails_closed() -> None:
    from alpha.safety.authority.boundaries import resolve_authority_approval_async

    async def approved():
        return True

    async def broken():
        raise RuntimeError("boom")

    async def silent():
        return None

    def sync_true():
        return True

    good = await resolve_authority_approval_async(approved, subject="x")
    assert good.approved is True
    bad = await resolve_authority_approval_async(broken, subject="x")
    assert bad.approved is False
    assert bad.reason == "approver_internal_error"
    noverdict = await resolve_authority_approval_async(silent, subject="x")
    assert noverdict.approved is False
    assert noverdict.reason == "approver_returned_no_verdict"
    missing = await resolve_authority_approval_async(None, subject="x")
    assert missing.approved is False
    assert missing.reason == "approver_unavailable"
    # A plain callable works in the async form too; the two forms cannot drift.
    also_good = await resolve_authority_approval_async(sync_true, subject="x")
    assert also_good.approved is True


def test_a_coroutine_handed_to_the_sync_resolver_is_refused_not_awaited() -> None:
    async def approver():
        return True

    result = resolve_authority_approval(approver, subject="x")
    assert result.approved is False
    assert result.reason == "approver_returned_unrecognised_verdict"
    assert "resolve_authority_approval_async" in result.detail


# ===========================================================================
# (q) A credential failure degrades ONLY its own owner.
# ===========================================================================


def _two_credentials_one_broken():
    return (
        CredentialSpec(
            owner="research-bot",
            capability="web_research",
            credential_key="SEARCH_API_KEY",
            validator=lambda: True,
            secret_ref="vault://search",
        ),
        CredentialSpec(
            owner="deploy-bot",
            capability="production_deploy",
            credential_key="DEPLOY_TOKEN",
            validator=lambda: (_ for _ in ()).throw(RuntimeError("401 unauthorized")),
            secret_ref="vault://deploy",
        ),
    )


def test_one_invalid_credential_degrades_only_its_own_owner() -> None:
    report = resolve_credential_owners(_two_credentials_one_broken())

    research = report.owner("research-bot")
    deploy = report.owner("deploy-bot")
    assert research is not None and deploy is not None
    assert research.status.value == "available"
    assert research.available is True
    assert deploy.status.value == "failed"
    assert deploy.available is False
    assert "401 unauthorized" in deploy.reason
    assert report.available == (research,)
    assert report.degraded == (deploy,)
    assert_credential_failures_isolated(report)


def test_a_credential_that_returns_no_verdict_degrades_rather_than_allowing() -> None:
    report = resolve_credential_owners(
        (
            CredentialSpec(owner="a", capability="cap_a", credential_key="K1", validator=lambda: True),
            CredentialSpec(owner="b", capability="cap_b", credential_key="K2", validator=lambda: None),
        )
    )
    assert report.owner("a").status.value == "available"
    assert report.owner("b").status.value == "degraded"
    assert "not a decision" in report.owner("b").reason


def test_a_credential_that_raises_does_not_stop_the_others_being_resolved() -> None:
    called: list[str] = []

    def ok(key: str):
        def validator():
            called.append(key)
            return True

        return validator

    def boom(key: str):
        def validator():
            called.append(key)
            raise RuntimeError("nope")

        return validator

    report = resolve_credential_owners(
        (
            CredentialSpec(owner="a", capability="ca", credential_key="ka", validator=ok("ka")),
            CredentialSpec(owner="b", capability="cb", credential_key="kb", validator=boom("kb")),
            CredentialSpec(owner="c", capability="cc", credential_key="kc", validator=ok("kc")),
        )
    )
    assert sorted(called) == ["ka", "kb", "kc"], "each validator runs independently"
    assert {item.owner for item in report.available} == {"a", "c"}
    assert [item.owner for item in report.degraded] == ["b"]


def test_a_secret_value_is_never_recorded_only_its_reference() -> None:
    report = resolve_credential_owners(
        (CredentialSpec(owner="a", capability="ca", credential_key="ka", validator=lambda: True, secret_ref="vault://x"),)
    )
    payload = report.to_dict()
    text = repr(payload)
    assert "vault://x" in text
    for forbidden in ("sk-", "AKIA", "password", "BEGIN PRIVATE"):
        assert forbidden not in text


def test_a_degradation_with_no_reason_is_a_defect() -> None:
    report = resolve_credential_owners(
        (CredentialSpec(owner="a", capability="ca", credential_key="ka", validator=lambda: False),)
    )
    # A refusal from check_permission returns False, which the resolver turns into
    # an explicit reason. Assert the reason is present rather than absent.
    assert report.owner("a").reason
    assert_credential_failures_isolated(report)


# ===========================================================================
# (r) Invalid configuration REFUSES TO START.
# ===========================================================================


def test_a_valid_configuration_starts() -> None:
    report = assert_startable({"bind_host": "127.0.0.1", "require_ingress_auth": True})
    assert report.ok is True
    assert report.failures == ()
    names = {name.value for name, _passed, _detail in report.checks}
    assert "ingress_auth_configured" in names
    assert "config_known_keys" in names


def test_unknown_configuration_keys_refuse_to_start() -> None:
    with pytest.raises(StartupRefused) as caught:
        assert_startable({"bind_host": "127.0.0.1", "authentcation": True})
    assert any("unknown configuration keys" in item for item in caught.value.violations)
    assert "authentcation" in str(caught.value)


def test_authentication_disabled_refuses_to_start() -> None:
    with pytest.raises(StartupRefused) as caught:
        assert_startable({"bind_host": "127.0.0.1", "auth_disabled": True})
    assert any("auth_disabled=true" in item for item in caught.value.violations)


def test_authentication_disabled_in_production_refuses_to_start() -> None:
    with pytest.raises(StartupRefused):
        assert_startable({"bind_host": "127.0.0.1", "auth_disabled": True, "environment": "production"})


def test_an_exposed_bind_host_with_no_ingress_auth_refuses_to_start() -> None:
    with pytest.raises(StartupRefused) as caught:
        assert_startable({"bind_host": "0.0.0.0", "require_ingress_auth": False})
    assert any("unauthenticated network ingress" in item for item in caught.value.violations)


def test_a_missing_internal_token_with_required_ingress_auth_refuses_to_start() -> None:
    with pytest.raises(StartupRefused) as caught:
        assert_startable(
            {"bind_host": "127.0.0.1", "require_ingress_auth": True, "internal_auth_token_configured": False}
        )
    assert any("no internal auth token is configured" in item for item in caught.value.violations)


def test_a_scope_that_is_not_stricter_refuses_to_start() -> None:
    from alpha.bots.authority_ceiling import AuthorityCeiling
    from alpha.safety.authority.scopes import baseline_from_ceiling

    baseline = baseline_from_ceiling(AuthorityCeiling())
    with pytest.raises(StartupRefused) as caught:
        assert_startable(
            {
                "bind_host": "127.0.0.1",
                "scopes": [
                    {
                        "name": "escalation",
                        "members": [{"kind": "agent", "name": "researcher-3"}],
                        "enable_capabilities": ["repository_mutate"],
                    }
                ],
            },
            baseline=baseline,
        )
    assert any(item.startswith("scopes[0] refused") for item in caught.value.violations)


def test_a_valid_scope_configuration_starts() -> None:
    from alpha.bots.authority_ceiling import AuthorityCeiling
    from alpha.safety.authority.scopes import baseline_from_ceiling

    report = assert_startable(
        {
            "bind_host": "127.0.0.1",
            "scopes": [
                {
                    "name": "no-exec",
                    "members": ["agent:researcher-3"],
                    "denied_capabilities": ["process_exec"],
                }
            ],
        },
        baseline=baseline_from_ceiling(AuthorityCeiling()),
    )
    assert report.ok is True
    checks = {name.value: passed for name, passed, _detail in report.checks}
    assert checks["scopes_not_looser"] is True


def test_every_violation_is_reported_not_just_the_first() -> None:
    with pytest.raises(StartupRefused) as caught:
        assert_startable(
            {"bind_host": "0.0.0.0", "require_ingress_auth": False, "auth_disabled": True, "typo": 1}
        )
    assert len(caught.value.violations) >= 3, caught.value.violations


def test_an_empty_configuration_is_a_valid_minimum() -> None:
    # The default bind is loopback with ingress auth required, so {} starts.
    report = assert_startable({})
    assert report.ok is True


def test_the_startup_report_serialises() -> None:
    report = assert_startable({"bind_host": "127.0.0.1"})
    payload = report.to_dict()
    assert payload["ok"] is True
    assert payload["checks"]
    for check in payload["checks"]:
        assert set(check) == {"check", "passed", "detail"}


def test_a_presented_but_malformed_ceiling_file_still_raises_rather_than_defaulting() -> None:
    """The rule the ``Status: Ready`` incident got backwards.

    ``load_ceiling`` returns the safe default for an ABSENT file and raises for a
    PRESENT but malformed one.  A product that cannot start a run must not be
    reported as ready, and a policy file with a typo must not be silently
    replaced by the default.
    """

    import json

    from alpha.bots.authority_ceiling import AuthorityCeiling as Ceiling
    from alpha.bots.authority_ceiling import load_ceiling, load_scopes

    # ABSENT file -> the safe built-in default, so a fresh install starts.
    with tempfile.TemporaryDirectory() as tmp:
        assert isinstance(load_ceiling(Path(tmp) / "absent.json"), Ceiling)

    # PRESENT but wrong -> raises. A typo must not become "the default".
    with pytest.raises(ValueError):
        Ceiling.from_dict({"max_capability_rank": "very high"}, source="test")
    with pytest.raises(ValueError):
        Ceiling.from_dict({"max_capabilty_rank": 50}, source="test")
    with pytest.raises(ValueError):
        Ceiling.from_dict({"max_capability_rank": 999}, source="test")

    # And a scope file with a typo raises rather than loading nothing.
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "scopes.json"
        path.write_text(json.dumps({"scopes": [{"name": "x", "member": []}]}), encoding="utf-8")
        with pytest.raises(Exception) as caught:
            load_scopes(path)
        assert caught.value.violations, "the refusal must name what was wrong"


# ===========================================================================
# Honest labelling: no policy control is described as a boundary.
# ===========================================================================


def test_no_policy_control_is_labelled_a_security_boundary() -> None:
    """The failure mode this whole task exists to remove."""

    for name in ("gateway-http-auth-disabled", "a2a-cards-and-delegate", "scheduler-and-mcp-task-workers"):
        entry = next(item for item in INBOUND_ENTRY_POINTS if item.name == name)
        assert entry.is_security_boundary is False, (
            f"{name} is a policy control inside one trust envelope and must not be labelled "
            "a security boundary"
        )


def test_the_boundary_errors_are_not_value_errors() -> None:
    """A security refusal must not be swallowable as ordinary bad input."""

    for exc in (UnauthenticatedInboundPath, StartupRefused, BoundaryError):
        assert issubclass(exc, RuntimeError)
        assert not issubclass(exc, ValueError)
    assert not issubclass(ApproverTimeout, ValueError)
    assert not issubclass(ApproverUnavailable, ValueError)
    # Same rule for the sibling modules in this package.
    from alpha.safety.authority.receipts import ChainIntegrityError, IdentityError, ReceiptError
    from alpha.safety.authority.scopes import ScopeError
    from alpha.safety.authority.taint import TaintClearRefused, TaintError

    for exc in (ChainIntegrityError, IdentityError, ReceiptError, ScopeError, TaintClearRefused, TaintError):
        assert issubclass(exc, RuntimeError)
        assert not issubclass(exc, ValueError)
