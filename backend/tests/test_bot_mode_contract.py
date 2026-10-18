"""``bot_mode`` — one server-side answer to "what may this bot do right now" (2.1).

Pinned here:

1. ``resolve_bot_mode`` resolves identity only from server-owned carriers (an
   explicit path name, the live run context, the thread's ``assistant_id``),
   and a thread that is not bound to a roster bot has NO bot mode.
2. The contract reports the ceiling-intersected grant: a later ceiling
   tightening demotes and *discloses* (``denied_capabilities``), never silently
   trims; the profile's raw grant stays readable as ``requested_capabilities``.
3. ``GET /api/bots/{name}/mode`` answers the LIVE contract with per-field
   provenance and an enforcement disclosure, and changing the profile changes
   the answer.
4. The lead agent's roster lookups delegate to the same resolver
   (``resolve_roster_bot``), so the run path and the endpoint cannot disagree
   about who is a roster bot — and the one lenient point is pinned to never
   raise.
"""

from __future__ import annotations

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

import alpha.bots.registry as bot_reg
from alpha.bots.authority_ceiling import RANK_REASON, AuthorityCeiling
from alpha.bots.mode import resolve_bot_mode, resolve_roster_bot
from alpha.bots.registry import BotRegistry
from app.gateway.routers import bots as bots_router


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """No test in this file may touch the real ALPHA_HOME roster."""

    monkeypatch.setenv("ALPHA_HOME", str(tmp_path))
    bot_reg._global_registry = None
    bot_reg._global_registry_path = None
    yield
    bot_reg._global_registry = None
    bot_reg._global_registry_path = None


@pytest.fixture()
def registry(tmp_path) -> BotRegistry:
    return BotRegistry(tmp_path / "roster.json")


@pytest.fixture()
def client(monkeypatch, registry) -> TestClient:
    monkeypatch.setattr(bots_router, "_registry", lambda: registry)
    app = make_authed_test_app()
    app.include_router(bots_router.router)
    return TestClient(app)


_SEED_CAPS = ["observe", "reason", "workspace_write", "process_exec"]


def _seed(registry: BotRegistry, name: str = "quill", **overrides):
    payload = dict(
        display_name="Quill",
        role="Write scheduled social posts",
        capabilities=list(_SEED_CAPS),
        skills=["copywriting"],
        toolsets=["social", "browser"],
    )
    payload.update(overrides)
    return registry.get_or_create(name, **payload)


def test_a_non_bot_thread_has_no_bot_mode(registry):
    _seed(registry)
    assert resolve_bot_mode(registry=registry) is None
    assert resolve_bot_mode(thread_metadata={"assistant_id": "lead_agent"}, registry=registry) is None
    assert resolve_bot_mode(thread_metadata={"botName": "ghost"}, registry=registry) is None
    assert resolve_bot_mode(bot_name="ghost", registry=registry) is None


def test_identity_resolves_from_server_owned_carriers(registry):
    _seed(registry)
    _seed(registry, name="atlas", skills=["mapping"])

    by_path = resolve_bot_mode(bot_name="quill", registry=registry)
    by_runtime = resolve_bot_mode(runtime_context={"bot_name": "Quill"}, registry=registry)
    by_thread = resolve_bot_mode(thread_metadata={"botName": "quill"}, registry=registry)
    by_assistant = resolve_bot_mode(thread_metadata={"assistant_id": "quill"}, registry=registry)
    assert {m.bot_name for m in (by_path, by_runtime, by_thread, by_assistant)} == {"quill"}

    # Precedence: an explicit server-validated path name beats the run context,
    # which beats the thread's assistant_id.
    assert resolve_bot_mode(thread_metadata={"assistant_id": "atlas"}, runtime_context={"bot_name": "atlas"}, bot_name="quill", registry=registry).bot_name == "quill"
    assert resolve_bot_mode(thread_metadata={"assistant_id": "atlas"}, runtime_context={"bot_name": "quill"}, registry=registry).bot_name == "quill"


def test_the_contract_reports_effective_scope_and_provenance(registry):
    _seed(registry)
    mode = resolve_bot_mode(bot_name="quill", runtime_context={"thread_id": "t-1", "run_id": "r-1"}, registry=registry)

    assert mode.thread_id == "t-1"
    assert mode.run_id == "r-1"
    assert mode.status == "active"
    assert mode.capabilities == ("observe", "process_exec", "reason", "workspace_write")
    assert mode.denied_capabilities == ()
    assert mode.toolsets == ("social", "browser")
    assert mode.skills == ("copywriting",)
    assert mode.memory_scope == "quill", "a bot with no explicit scope defaults to its own name"
    assert mode.approval_mode == "default"
    assert mode.inbox_policy == "async"
    assert mode.epoch == registry.get_bot("quill").capability_fingerprint()

    # Provenance is a complete lookup table, one label per scoping field.
    assert set(mode.provenance) >= {"capabilities", "toolsets", "skills", "mcp_servers", "model", "memory_scope", "approval_mode", "inbox_policy"}
    assert mode.provenance["capabilities"] == "bot_profile ∩ authority_ceiling"
    assert mode.provenance["model"] == "default — the global default model applies at assembly"
    assert mode.provenance["memory_scope"] == "default — the bot's own name"
    assert mode.provenance["approval_mode"] == "default — no per-bot override yet"
    assert mode.provenance["inbox_policy"] == "default — async delivery"


def test_changing_the_profile_changes_the_answer(registry):
    _seed(registry)
    before = resolve_bot_mode(bot_name="quill", registry=registry)
    registry.update_bot("quill", skills=["copywriting", "seo"], model="gpt-5-mini")
    after = resolve_bot_mode(bot_name="quill", registry=registry)

    assert before.skills != after.skills
    assert after.skills == ("copywriting", "seo")
    assert after.model == "gpt-5-mini"
    assert after.provenance["model"] == "bot.model"
    assert before.epoch != after.epoch, "the capability epoch moves with the hashed surface (skills)"


def test_a_tightened_ceiling_demotes_and_discloses(registry):
    _seed(registry)
    kept = resolve_bot_mode(bot_name="quill", registry=registry)
    assert kept.denied_capabilities == ()

    registry.set_ceiling(AuthorityCeiling(max_capability_rank=RANK_REASON))
    demoted = resolve_bot_mode(bot_name="quill", registry=registry)

    assert demoted.capabilities == ("observe", "reason")
    assert demoted.denied_capabilities == ("process_exec", "workspace_write")
    assert demoted.requested_capabilities == ("observe", "process_exec", "reason", "workspace_write"), "the raw grant stays readable"
    assert set(demoted.capabilities) | set(demoted.denied_capabilities) == set(kept.capabilities), "demotion partitions the grant; nothing is silently dropped"


def test_metadata_overrides_the_policy_defaults(registry):
    bot = _seed(registry)
    bot.metadata = {**(bot.metadata or {}), "approval_mode": "confirm", "inbox_policy": "manual"}
    registry.register(bot)

    mode = resolve_bot_mode(bot_name="quill", registry=registry)
    assert mode.approval_mode == "confirm"
    assert mode.inbox_policy == "manual"
    assert mode.provenance["approval_mode"] == "profile_metadata"
    assert mode.provenance["inbox_policy"] == "profile_metadata"


def test_resolve_roster_bot_degrades_and_never_raises():
    class _Boom:
        def get_bot(self, name):
            raise RuntimeError("roster exploded")

    assert resolve_roster_bot("quill", registry=_Boom()) is None
    assert resolve_roster_bot(None) is None
    assert resolve_roster_bot("") is None


def test_the_mode_route_answers_the_effective_contract(client, registry):
    _seed(registry)
    response = client.get("/api/bots/quill/mode")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["bot_name"] == "quill"
    assert body["capabilities"] == ["observe", "process_exec", "reason", "workspace_write"]
    assert body["denied_capabilities"] == []
    assert body["memory_scope"] == "quill"
    assert body["epoch"] == registry.get_bot("quill").capability_fingerprint()
    assert body["provenance"]["capabilities"] == "bot_profile ∩ authority_ceiling"
    assert set(body["enforcement"]) >= {"capabilities", "toolsets", "skills", "mcp_servers", "model", "memory_scope"}


def test_the_mode_route_404s_for_a_non_bot(client):
    assert client.get("/api/bots/ghost/mode").status_code == 404


def test_the_mode_route_tracks_a_profile_change(client, registry):
    _seed(registry)
    first = client.get("/api/bots/quill/mode").json()
    registry.update_bot("quill", capabilities=["observe"], skills=["copywriting", "seo"], bump_version=False)
    second = client.get("/api/bots/quill/mode").json()

    assert first["capabilities"] != second["capabilities"]
    assert second["capabilities"] == ["observe"]
    assert first["epoch"] != second["epoch"]


def test_the_lead_agent_lookups_delegate_to_the_one_resolver():
    from alpha.agents.lead_agent.agent import _resolve_bot_profile, _roster_bot_name

    reg = bot_reg.get_bot_registry()
    reg.get_or_create("quill", display_name="Quill", role="Write scheduled social posts")

    assert _roster_bot_name("Quill") == "Quill", "case is preserved, the lookup is case-insensitive"
    assert _roster_bot_name("ghost") is None
    profile = _resolve_bot_profile({"bot_name": "quill"})
    assert profile is not None and profile.name == "quill"
    assert _resolve_bot_profile({}) is None
    assert _resolve_bot_profile({"bot_name": "ghost"}) is None
