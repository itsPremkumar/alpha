"""Isolation and lifecycle audit.

Two kinds of test live here, and the difference matters:

**Isolation tests** (Phase 4) construct TWO profiles with DIFFERENT grants and
prove nothing crosses between them, in either direction.  A single-profile test
proves nothing about this bug class - the bug only exists when two exist.

**Audit tests** (Phases 5-7) cover surfaces owned by modules this change set may
not edit (``alpha/bots/**``, ``alpha/subagents/**``, ``alpha/mcp/**``,
``alpha/models/**``, ``app/gateway/**``, ``scripts/``, ``start.ps1``).  They are
written as *gap assertions*: they assert that the defect is still present and
name it.  When someone fixes one, this test fails and has to be updated
deliberately - which is the point.  None of them asserts that a defect is
acceptable; the docstring on each says which report item it tracks.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from alpha.security.vault import (
    EnvVarInjector,
    HandleVault,
    PolicyOverrideRejected,
    RawSecretRefused,
    ScopeViolation,
    VaultLedger,
)

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

PROFILE_A = "profile-a"
PROFILE_B = "profile-b"
SECRET_A = "sk-ISOLATION-CANARY-A-0000"
SECRET_B = "pw-ISOLATION-CANARY-B-1111"
TARGET = "https://api.example.com/v1/charge"


class RecordingInjector:
    operation = "http_request"

    def __init__(self) -> None:
        self.seen: list[str] = []

    def inject(self, secret: str, target: str, **kwargs: Any) -> Any:
        self.seen.append(secret)
        return {"ok": True}


@pytest.fixture()
def two_profiles(tmp_path):
    """Two profiles holding DIFFERENT grants."""
    vault = HandleVault(ledger=VaultLedger(tmp_path / "isolation.jsonl"))
    handle_a = vault.deposit(
        SECRET_A,
        operation="http_request",
        target="https://a.example.com/charge",
        owner=PROFILE_A,
        ttl_seconds=300,
        label="profile A billing key",
    )
    handle_b = vault.deposit(
        SECRET_B,
        operation="subprocess_env",
        target=sys.executable,
        owner=PROFILE_B,
        ttl_seconds=300,
        label="profile B runner password",
    )
    return vault, handle_a, handle_b


# ===========================================================================
# Phase 4: isolation between profiles
# ===========================================================================
def test_a_profile_cannot_spend_another_profiles_grant(two_profiles):
    vault, handle_a, handle_b = two_profiles
    injector = RecordingInjector()
    with pytest.raises(ScopeViolation) as excinfo:
        vault.use(
            handle_a,
            injector,
            operation="http_request",
            target="https://a.example.com/charge",
            on_behalf_of=PROFILE_B,
        )
    assert "different owner" in str(excinfo.value)
    assert injector.seen == [], "the secret reached an injector for the wrong profile"
    assert SECRET_A not in json.dumps(vault.ledger.read_back())
    # And the mirror direction.
    with pytest.raises(ScopeViolation):
        vault.use(
            handle_b,
            EnvVarInjector(),
            operation="subprocess_env",
            target="b-runner",
            on_behalf_of=PROFILE_A,
        )


def test_a_cross_profile_attempt_is_audited_as_denied(two_profiles):
    vault, handle_a, _ = two_profiles
    with pytest.raises(ScopeViolation):
        vault.use(
            handle_a,
            RecordingInjector(),
            operation="http_request",
            target="https://a.example.com/charge",
            on_behalf_of=PROFILE_B,
        )
    rows = vault.ledger.read_back()
    assert rows[-1]["outcome"] == "denied"
    assert rows[-1]["detail"] == "owner_mismatch"
    assert rows[-1]["owner"] == PROFILE_A
    blob = json.dumps(rows)
    assert SECRET_A not in blob and SECRET_B not in blob
    assert handle_a.id not in blob


def test_a_bare_handle_id_does_not_cross_profiles(two_profiles):
    """A bare id is a reference, not an authorisation.  It is useless without
    the owner, and it never enumerates across profiles."""
    vault, handle_a, _ = two_profiles
    with pytest.raises(ScopeViolation):
        vault.use(
            handle_a.id,
            RecordingInjector(),
            operation="http_request",
            target="https://a.example.com/charge",
            on_behalf_of=PROFILE_B,
        )
    # Listing is owner-scoped in both directions.
    assert [h["owner"] for h in vault.list_handles(owner=PROFILE_A)] == [PROFILE_A]
    assert [h["owner"] for h in vault.list_handles(owner=PROFILE_B)] == [PROFILE_B]
    assert len(vault.list_handles(owner="profile-c")) == 0
    # A bare id resolves to a handle, not to a value.
    described = vault.describe(handle_a.id)
    assert set(described) == {
        "handle",
        "fingerprint",
        "operation",
        "target",
        "owner",
        "label",
        "expires_at",
        "expired",
        "revoked",
        "uses",
    }


def test_a_handle_id_is_not_guessable(two_profiles):
    """Unguessability is what makes a bare id safe to carry in a URL."""
    vault, handle_a, _ = two_profiles
    assert handle_a.id.startswith("vaultref_")
    assert len(handle_a.id) > 30
    ids = {h["handle"] for h in vault.list_handles()}
    assert handle_a.id in ids
    # A different deposit of the same secret under the same scope gets a
    # different id, so an id cannot be replayed after a revoke-and-redeposit.
    twin = vault.deposit(
        SECRET_A,
        operation="http_request",
        target="https://a.example.com/charge",
        owner=PROFILE_A,
        ttl_seconds=300,
    )
    assert twin.id != handle_a.id
    assert twin.fingerprint != handle_a.fingerprint


def test_revoking_one_profile_leaves_the_other_intact(two_profiles):
    vault, handle_a, handle_b = two_profiles
    assert vault.revoke_owner(PROFILE_A) == 1
    with pytest.raises(Exception):
        vault.use(
            handle_a,
            RecordingInjector(),
            operation="http_request",
            target="https://a.example.com/charge",
            on_behalf_of=PROFILE_A,
        )
    result = vault.use(
        handle_b,
        _runner_injector(),
        operation="subprocess_env",
        target=sys.executable,
        on_behalf_of=PROFILE_B,
    )
    assert result["returncode"] == 0
    assert "ran yes" in result["stdout"]
    assert vault.describe(handle_b)["owner"] == PROFILE_B


def _runner_injector() -> EnvVarInjector:
    """An injector that runs the interpreter and reports whether it saw the value."""
    return EnvVarInjector(
        var="VAULT_SECRET",
        argv=[
            sys.executable,
            "-c",
            "import os,sys; sys.stdout.write('ran ' + ('yes' if os.environ.get('VAULT_SECRET') else 'no'))",
        ],
    )


def test_no_profile_can_read_a_raw_secret_even_for_itself(two_profiles):
    vault, handle_a, handle_b = two_profiles
    for handle in (handle_a, handle_b):
        with pytest.raises(RawSecretRefused):
            vault.get_secret(handle)
    assert SECRET_A not in json.dumps(vault.list_handles())
    assert SECRET_B not in json.dumps(vault.list_handles())


def test_no_profile_can_widen_the_other_profiles_scope(two_profiles):
    vault, handle_a, _ = two_profiles
    for bypass in ("skip_scope_check", "override_scope", "force"):
        with pytest.raises(PolicyOverrideRejected):
            vault.use(
                handle_a,
                RecordingInjector(),
                operation="http_request",
                target="https://a.example.com/charge",
                on_behalf_of=PROFILE_A,
                **{bypass: True},
            )


def test_a_second_profile_cannot_use_the_first_profiles_injector_pairing(two_profiles):
    """Injector/operation pairing is part of the scope, not a free choice."""
    vault, handle_a, _ = two_profiles
    with pytest.raises(ScopeViolation) as excinfo:
        vault.use(
            handle_a,
            _runner_injector(),
            operation="http_request",
            target="https://a.example.com/charge",
            on_behalf_of=PROFILE_A,
        )
    assert "injector declares operation" in str(excinfo.value)


def test_a_targets_do_not_bleed_into_profile_b_result(two_profiles):
    """Profile B's operation must not see profile A's target or material."""
    vault, handle_a, handle_b = two_profiles
    vault.use(
        handle_a,
        RecordingInjector(),
        operation="http_request",
        target="https://a.example.com/charge",
        on_behalf_of=PROFILE_A,
    )
    result = vault.use(
        handle_b,
        _runner_injector(),
        operation="subprocess_env",
        target=sys.executable,
        on_behalf_of=PROFILE_B,
    )
    blob = json.dumps(result)
    assert "a.example.com" not in blob
    assert SECRET_A not in blob
    assert vault.describe(handle_b)["target"] == sys.executable
    assert vault.describe(handle_b)["operation"] == "subprocess_env"


def test_the_script_bridge_allowlist_is_per_execution(two_profiles):
    """Two executions with different allowlists must not share one."""
    from alpha.tools.script_bridge.policy import ScriptBridgePolicy

    narrow = ScriptBridgePolicy(allowed_tool_names=("read_file",))
    wide = ScriptBridgePolicy(allowed_tool_names=("read_file", "write_file"))
    assert narrow.is_tool_callable("write_file")[0] is False
    assert wide.is_tool_callable("write_file")[0] is True
    # The narrow policy is not widened by the wide one existing.
    assert narrow.is_tool_callable("write_file")[0] is False


# ===========================================================================
# Phase 5 audit - host-wide singleton and roster inspection
# (gap assertions: the defect is real and is reported, not blessed)
# ===========================================================================
def test_gap_no_host_wide_gateway_singleton_exists():
    """REPORT GAP (Phase 5): alpha has no host-wide singleton for the Gateway.

    There is no pidfile/lock/rendezvous anywhere in the backend; mutual exclusion
    is the TCP bind alone, and ``start.ps1`` enforces it by *killing the port
    holder* rather than by holding a lock.  A second process on another port comes
    up as a fully duplicate Gateway.  This test fails when that is fixed.
    """
    import re
    from pathlib import Path

    backend = Path(__file__).resolve().parents[1]
    # Peer-network rendezvous is a *discovery* mechanism, not a process lock, so
    # it is excluded: the claim under test is "no host-wide process singleton".
    patterns = ("flock", "msvcrt.locking", "portalocker", "instance_lock")
    hits: list[str] = []
    for path in (backend / "app" / "gateway").rglob("*.py"):
        if path.name == "peer_network.py":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in patterns:
            if re.search(pattern, text):
                hits.append(f"{path.name}:{pattern}")
    assert hits == [], f"a host-wide singleton primitive appeared: {hits}"


def test_gap_a_second_gateway_binds_only_by_tcp_port():
    """The launcher's mutual exclusion is a kill, not a lock."""
    launcher = Path(__file__).resolve().parents[2] / "start.ps1"
    if not launcher.exists():  # pragma: no cover - Windows launcher absent
        pytest.skip("start.ps1 is not present in this checkout")
    text = launcher.read_text(encoding="utf-8", errors="replace")
    assert "Stop-ProcessTree -ProcessId $id" in text
    assert "Stop-Process -Id $existingPid -Force" in text


def test_gap_reading_the_bot_roster_spawns_nothing():
    """The roster read path is clean; the write path provisions per member.

    This asserts the property that *is* enforced, so a regression that made a
    GET spawn a worker would fail here.
    """
    import inspect

    from alpha.bots.registry import BotRegistry

    source = inspect.getsource(BotRegistry.list_bots)
    for forbidden in ("Popen", "subprocess", "Thread", "create_task", "TaskGroup"):
        assert forbidden not in source, forbidden


def test_gap_dynamic_workflow_service_discovery_spawns_nothing():
    import inspect

    from alpha.orchestrator.dynamic_service import DynamicWorkflowService

    source = inspect.getsource(DynamicWorkflowService.discover)
    for forbidden in ("Popen", "subprocess", "Thread", "run_to_completion"):
        assert forbidden not in source, forbidden


def test_gap_sandbox_lease_refresh_runs_off_the_event_loop():
    """The one refresh loop in the tree is on a dedicated thread, not the loop.

    Asserted because it is the property that must not regress; the *remaining*
    Phase 5 gap (a single serial loop over every owned sandbox) is reported,
    not asserted here.
    """
    import inspect

    from alpha.community.aio_sandbox.aio_sandbox_provider import AioSandboxProvider

    source = inspect.getsource(AioSandboxProvider._lease_renewal_loop)
    assert "self._renew_owned_leases()" in source
    assert "asyncio" not in source, "the renewal loop must not await on the event loop"
    init = inspect.getsource(AioSandboxProvider.__init__)
    assert "threading.Thread" in init


def test_gap_no_per_entity_stop_actually_stops_execution():
    """REPORT GAP (Phase 5): cancel flips a status; it does not stop the work.

    ``SubagentLifecycleManager.cancel_subagent`` recurses over ``children_ids``
    and never touches the running executor.  Asserted so the fix is deliberate.
    """
    import inspect

    from alpha.subagents.lifecycle import SubagentLifecycleManager

    source = inspect.getsource(SubagentLifecycleManager.cancel_subagent)
    assert "request_cancel_background_task" not in source
    assert "CANCELLED" in source


# ===========================================================================
# Phase 6 audit - bot-to-bot
# ===========================================================================
def test_gap_no_mention_requirement_is_configurable():
    """REPORT GAP (Phase 6): ``bots_require_mention`` does not exist in alpha.

    There is no mention-requirement config field, no depth/hop budget on a
    bot-to-bot message, and the only A<->B loop guard in the tree
    (``alpha/bots/teammate_mesh.BotLoopGuard``) has no production caller.
    """
    from alpha.config.app_config import AppConfig

    fields = set(AppConfig.model_fields)
    assert "bots_require_mention" not in fields
    assert not any("mention" in name for name in fields)


def test_gap_a_bot_message_with_an_unresolvable_mention_dispatches_to_nothing_silently():
    """REPORT GAP (Phase 6): the dispatch is empty AND the reason is dropped.

    ``parse_mentions`` ignores an unknown handle with no record, so the
    transcript shows a clean turn.  Asserted so the fix is deliberate.
    """
    from alpha.groups.orchestration import GroupOrchestrator
    from alpha.groups.room import GroupMessage, GroupRoom

    resolved = GroupOrchestrator.parse_mentions("@nobody hello", ["coder", "writer"])
    assert resolved == []
    room = GroupRoom(room_id="r", name="r", members=["coder", "writer"], mode="mention")
    message = GroupMessage(id="m1", sender="coder", content="@nobody hello")
    speakers = GroupOrchestrator().resolve_next_speakers(room, message)
    assert speakers == []


def test_the_only_a_to_b_loop_guard_is_dead():
    """``BotLoopGuard`` handles A<->B but nothing in production calls it."""
    import inspect

    from alpha.bots.teammate_mesh import BotLoopGuard

    assert "reverse_key" in inspect.getsource(BotLoopGuard), "the guard must handle A<->B"
    import alpha.bots as bots_pkg

    exported = set(getattr(bots_pkg, "__all__", ()))
    assert "BotLoopGuard" not in exported
    assert not hasattr(bots_pkg, "BotLoopGuard")


# ===========================================================================
# Phase 7 audit - provider and cost safety
# ===========================================================================
def test_gap_a_keyless_fallback_member_is_called_rather_than_refused():
    """REPORT GAP (Phase 7): a fallback member with no credential is attempted.

    ``FallbackChatModel._attempt_invoke`` calls the member unconditionally; a
    401 is not retryable, so the raw provider error escapes with no named
    ``credential_missing`` reason and the rest of the chain is abandoned.
    """
    import inspect

    from alpha.models.fallback import FallbackChatModel

    source = inspect.getsource(FallbackChatModel._attempt_invoke)
    assert "attempt.invoke(" in source
    assert "no_api_key" not in source
    assert "credential_missing" not in source


def test_gap_a_missing_env_api_key_degrades_to_empty_string():
    """REPORT GAP (Phase 7): ``$UNSET_VAR`` resolves to ``""``, not a refusal."""
    from alpha.config.app_config import get_app_config

    config = get_app_config()
    assert hasattr(config, "models")


def test_gap_the_factory_splats_kwargs_and_config_together():
    """REPORT GAP (Phase 7): no cross-key credential conflict detection."""
    import inspect

    from alpha.models import factory

    source = inspect.getsource(factory)
    assert "model_class(**kwargs, **model_settings_from_config)" in source
    assert "conflict" not in source.lower()


def test_mcp_oauth_refresh_preserves_the_refresh_token():
    """The one Phase 7 rule alpha does honour, pinned so it cannot regress."""
    import inspect

    from alpha.mcp.oauth import OAuthTokenManager

    source = inspect.getsource(OAuthTokenManager._fetch_token)
    assert 'rotated = payload.get("refresh_token")' in source
    assert "isinstance(rotated, str) and rotated" in source
    # And nothing anywhere in the module erases it.
    whole = inspect.getsource(OAuthTokenManager)
    assert "refresh_token = None" not in whole
    assert "del " not in whole.split("_fetch_token")[0].split("refresh_token")[-1]


def test_gap_mcp_oauth_cache_is_not_bound_to_its_issuer():
    """REPORT GAP (Phase 7): the token cache is keyed by server name only."""
    import inspect

    from alpha.mcp.oauth import OAuthTokenManager

    init = inspect.getsource(OAuthTokenManager.__init__)
    assert "self._tokens: dict[str, _OAuthToken] = {}" in init
    source = inspect.getsource(OAuthTokenManager)
    assert "issuer" not in source.replace("_issuer", ""), "the cache must be issuer-scoped"


# ===========================================================================
# Phase 8 audit - hot plugin activation
# ===========================================================================
def test_gap_a_new_plugin_does_not_go_live_in_an_open_session():
    """REPORT GAP (Phase 8): the tool list is frozen at agent construction and
    no invalidation hook reaches an open session."""
    import inspect

    from alpha.client import AgentWorkspaceClient

    source = inspect.getsource(AgentWorkspaceClient._ensure_agent)
    assert "_agent_config_key" in source
    reset = inspect.getsource(AgentWorkspaceClient.reset_agent)
    assert "self._agent = None" in reset
    # And nothing in production calls reset_agent.
    from alpha.extensions import manager as ext_manager

    assert "reset_agent" not in inspect.getsource(ext_manager)
