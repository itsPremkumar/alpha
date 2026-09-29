"""Honesty 3/6 — the configured price table is actually reachable.

The defect this pins
--------------------
``config.example.yaml`` shipped a ``model_pricing:`` block with eight entries.
``AppConfig`` declared the field, ``ModelPriceEntry`` validated it, and
``packages/harness/alpha/config/AGENTS.md`` documented it as "fallback per-1M
prices for models that omit ``models[].pricing``". **Nothing read it.**
``console._build_pricing_map()`` consulted only ``models[*].pricing``, and no
shipped model declared that, so every console route answered
``total_cost: null`` and ``currency: null`` with no error and no warning.

How it was found
----------------
By reading an operator's screen, not by reading the schema. The Usage view
showed tokens and a dash where spend should be, and the operator's configured
price table was sitting in ``config.yaml`` looking authoritative.

Why it is worse than an absent key
----------------------------------
An absent key says "cost reporting is not configured". A present, documented,
validated key says "cost reporting is configured" — and returns null. The
operator's configuration *looks* in effect when it is not, which is a
configuration lie rather than a missing feature.

What these tests do that the existing ones do not
-------------------------------------------------
``tests/test_console_model_pricing_fallback.py`` proves the fallback table is
honoured when it is handed a **synthetic** ``SimpleNamespace`` config. That
catches a regression in the function and cannot catch the other half of the
defect: a renamed key, a field the schema drops on load, or a template whose
block is renamed or removed. Everything here drives the **shipped
``config.example.yaml``**, loaded through the same resolution chain the Gateway
uses, and asserts the durable numbers the HTTP routes return for a real row.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
import pytest
import yaml
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from alpha.persistence.engine import close_engine, get_session_factory, init_engine
from alpha.persistence.run.model import RunRow
from app.gateway.routers import console

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_EXAMPLE = REPO_ROOT / "config.example.yaml"

#: A model the shipped template prices **only** through ``model_pricing:``. If
#: the template ever stops declaring it, the fixture below fails loudly instead
#: of quietly testing an empty table.
FALLBACK_ONLY_MODEL = "gpt-4o"


@pytest.fixture()
def shipped_config(tmp_path, monkeypatch):
    """Load the real ``config.example.yaml`` the way the Gateway loads it.

    Not a copy, not a ``SimpleNamespace``: the file is copied verbatim to a temp
    ``config.yaml`` and resolved through ``ALPHA_CONFIG_PATH``, which is the
    production resolution chain (``AppConfig.resolve_config_path``). The app
    config cache is reset before and after so this cannot leak into another test.
    """
    from alpha.config import app_config as app_config_module

    project = tmp_path / "project"
    project.mkdir()
    config_path = project / "config.yaml"
    config_path.write_text(CONFIG_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")

    monkeypatch.setenv("ALPHA_PROJECT_ROOT", str(project))
    monkeypatch.setenv("ALPHA_CONFIG_PATH", str(config_path))
    app_config_module.reset_app_config()
    try:
        from alpha.config.app_config import get_app_config

        config = get_app_config()
        # Prove the Gateway really resolved the file we pointed it at, rather
        # than some ambient config left over from another test.
        assert app_config_module.AppConfig.resolve_config_path() == config_path
        yield config
    finally:
        app_config_module.reset_app_config()


def test_the_shipped_template_declares_a_fallback_price_table(shipped_config):
    """The premise. A template that dropped the block cannot be honest about it."""
    table = shipped_config.model_pricing
    assert table, "config.example.yaml no longer declares a fallback price table; the console would answer total_cost: null for every run"
    assert FALLBACK_ONLY_MODEL in table, f"the template no longer prices {FALLBACK_ONLY_MODEL!r} in the fallback table, so the tests below would be testing nothing"


def test_the_shipped_model_is_not_also_priced_inline(shipped_config):
    """``models[].pricing`` is authoritative; this model must rely on the fallback.

    Stated as a premise rather than assumed: if someone later declares
    ``gpt-4o`` in ``models[]`` — with or without an inline price — the
    fallback-only path is no longer what the tests below exercise, and they
    would silently stop covering the defect.
    """
    declared = {model.name: getattr(model, "pricing", None) for model in shipped_config.models}
    assert FALLBACK_ONLY_MODEL not in declared, f"{FALLBACK_ONLY_MODEL} is now declared in models[] (pricing={declared[FALLBACK_ONLY_MODEL]!r}); pick a different model so the fallback path stays covered"
    # And no shipped model carries an inline price at all, so today the whole
    # cost surface depends on the fallback table being read.
    assert all(price is None for price in declared.values()), f"a shipped model now declares an inline price: {declared}"


def test_a_model_priced_only_in_the_fallback_table_produces_a_cost(shipped_config):
    """The regression: the table is the only thing configured, and it must work."""
    pricing = console._build_pricing_map()
    assert FALLBACK_ONLY_MODEL in pricing, "the documented fallback table was ignored by the console"

    entry = pricing[FALLBACK_ONLY_MODEL]
    assert entry.input_per_million > 0 and entry.output_per_million > 0
    assert entry.currency, "a priced model with no currency produces an unusable total"

    cost = console._run_cost(
        pricing,
        model_name=FALLBACK_ONLY_MODEL,
        total_input_tokens=1_000_000,
        total_output_tokens=1_000_000,
        token_usage_by_model=None,
    )
    assert cost is not None, "_run_cost returned null for a model the operator priced"
    assert cost == pytest.approx(12.5), f"expected 2.5 input + 10.0 output, got {cost}"


def test_models_pricing_still_wins_over_the_fallback_table(shipped_config, monkeypatch):
    """The two namespaces are not peers: an inline price is authoritative.

    Asserted against a real loaded ``ModelConfig`` carrying a real inline
    ``pricing`` extra, with a *competing* fallback entry of the same name
    inserted into the real ``model_pricing`` table. If the fallback were
    applied after — or instead of — the per-model price, this is the assertion
    that fails.
    """
    from alpha.config.app_config import ModelPriceEntry
    from alpha.config.model_config import ModelConfig

    target = next(model for model in shipped_config.models if model.name)
    assert isinstance(target, ModelConfig), "the shipped models[] did not load as real ModelConfig objects"

    monkeypatch.setattr(target, "pricing", {"currency": "USD", "input_per_million": 99.0, "output_per_million": 99.0}, raising=False)
    competing = dict(shipped_config.model_pricing)
    competing[target.name] = ModelPriceEntry(input=0.01, output=0.02)
    monkeypatch.setattr(shipped_config, "model_pricing", competing, raising=False)

    # Premise: the inline price really is on the model object the console reads.
    assert getattr(target, "pricing", None), "the inline price was not attached to the model"

    pricing = console._build_pricing_map()
    assert pricing[target.name].input_per_million == 99.0, "the fallback table overrode the per-model price"
    assert pricing[target.name].output_per_million == 99.0
    # The provider-facing id is keyed too and carries the same authoritative entry.
    assert pricing[target.model].input_per_million == 99.0
    # And the competing fallback price is nowhere in the map under that name.
    assert console._lookup_pricing(pricing, target.name).input_per_million != pytest.approx(0.01)


def test_every_declared_fallback_entry_reaches_the_pricing_map(shipped_config):
    """No entry in the shipped table may be silently dropped.

    A key that is declared, validated and then not consulted is the exact
    failure this file exists for, so the check is per-entry rather than
    "at least one works".
    """
    pricing = console._build_pricing_map()
    dropped = []
    for name, entry in shipped_config.model_pricing.items():
        if float(getattr(entry, "input", 0) or 0) <= 0 and float(getattr(entry, "output", 0) or 0) <= 0:
            continue  # a 0/0 entry is a documented placeholder, not a price
        price = console._lookup_pricing(pricing, name)
        if price is None:
            dropped.append(name)
            continue
        assert price.input_per_million == pytest.approx(float(entry.input)), name
        assert price.output_per_million == pytest.approx(float(entry.output)), name
    assert not dropped, f"declared fallback prices that never reach the console: {dropped}"


# ---------------------------------------------------------------------------
# The durable half: the HTTP route must report the number, not null
# ---------------------------------------------------------------------------


def _build_console_app(tmp_path) -> tuple[Any, str]:
    """Stub-authed app with the real console router on a real sqlite engine.

    Mirrors ``test_projects_router._build_projects_app``: a real SQLAlchemy
    session factory over a temp database, so the route's aggregation and its
    ``_build_pricing_map`` call are the production ones.

    A *fixed* user id is returned because the console scopes every query to
    ``get_current_user(request)``; the shared helper's default user is a fresh
    uuid per request, which would make the seeded rows invisible to it.
    """
    from uuid import UUID, uuid4

    from app.gateway.auth.models import User

    owner_id = str(uuid4())

    async def _init() -> None:
        await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'console.db'}", sqlite_dir=str(tmp_path))

    anyio.run(_init)
    app = make_authed_test_app(user_factory=lambda: User(email="honesty-pricing@example.com", password_hash="x", system_role="user", id=UUID(owner_id)))
    app.include_router(console.router)
    return app, owner_id


def _seed_run(*, owner_id: str, run_id: str, model_name: str, input_tokens: int, output_tokens: int, usage_by_model: dict | None) -> None:
    """Insert a completed run row directly through the session factory."""
    session_factory = get_session_factory()

    async def _write() -> None:
        async with session_factory() as session:
            session.add(
                RunRow(
                    run_id=run_id,
                    thread_id="thread-honesty-pricing",
                    assistant_id=None,
                    user_id=owner_id,
                    status="success",
                    operation_kind="run",
                    model_name=model_name,
                    total_input_tokens=input_tokens,
                    total_output_tokens=output_tokens,
                    total_tokens=input_tokens + output_tokens,
                    token_usage_by_model=usage_by_model or {},
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
            await session.commit()

    anyio.run(_write)


@pytest.fixture()
def console_client(tmp_path, shipped_config):
    """A live console client plus the owner id its queries are scoped to."""
    app, owner_id = _build_console_app(tmp_path)
    with TestClient(app) as client:
        yield client, owner_id
    anyio.run(close_engine)


def test_the_usage_route_reports_a_cost_for_a_fallback_only_model(console_client, shipped_config):
    """The claim the operator reads, checked against a durable run row.

    A real ``runs`` row for a model the operator priced only in
    ``model_pricing:``, aggregated by the real route. The response must carry a
    number and a currency; ``null`` is the exact symptom of the defect.
    """
    client, owner_id = console_client
    _seed_run(owner_id=owner_id, run_id="r1", model_name=FALLBACK_ONLY_MODEL, input_tokens=1_000_000, output_tokens=1_000_000, usage_by_model=None)

    usage = client.get("/api/console/usage", params={"days": 7}).json()
    assert usage["total_cost"] is not None, "the console reported total_cost: null for a run the operator priced in model_pricing"
    assert usage["total_cost"] == pytest.approx(12.5), usage
    assert usage["currency"], "a non-null cost with no currency is not a spend figure"

    breakdown = usage["by_model"][FALLBACK_ONLY_MODEL]
    assert breakdown["cost"] == pytest.approx(12.5), breakdown


def test_the_run_history_route_reports_the_same_cost(console_client, shipped_config):
    """A second consumer of the same table, so a partial fix is still caught.

    The bug was "every cost number is null". Pinging one route would have let a
    fix to that route alone pass; this asserts the sibling route, which reaches
    the cost through the same `_run_cost`, also produces a number.
    """
    client, owner_id = console_client
    _seed_run(owner_id=owner_id, run_id="r2", model_name=FALLBACK_ONLY_MODEL, input_tokens=2_000_000, output_tokens=1_000_000, usage_by_model=None)

    body = client.get("/api/console/runs", params={"limit": 10}).json()
    rows = {row["run_id"]: row for row in body["runs"]}
    assert "r2" in rows, f"the seeded run is missing from the console history: {body}"
    assert rows["r2"]["cost"] == pytest.approx(2 * 2.5 + 10.0), rows["r2"]


def test_a_per_model_usage_split_is_priced_through_the_same_fallback_table(console_client, shipped_config):
    """Multi-model runs take the ``token_usage_by_model`` branch, not the legacy one.

    A different code path inside ``_run_cost``; a fallback wired into only one of
    them would leave subagent-using runs reporting null forever.
    """
    client, owner_id = console_client
    _seed_run(
        owner_id=owner_id,
        run_id="r3",
        model_name="some-model-not-in-any-table",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        usage_by_model={
            FALLBACK_ONLY_MODEL: {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "total_tokens": 2_000_000},
        },
    )

    usage = client.get("/api/console/usage", params={"days": 7}).json()
    assert usage["total_cost"] is not None and usage["total_cost"] > 0, usage
    assert usage["by_model"][FALLBACK_ONLY_MODEL]["cost"] == pytest.approx(12.5), usage["by_model"]


def test_a_model_with_no_configured_price_still_reports_null(console_client, shipped_config):
    """The control: the fix did not turn "unpriced" into "free".

    Without this, a fallback wired too eagerly — or a ``0`` default — would make
    every run cost ``0.0`` and pass the tests above.
    """
    client, owner_id = console_client
    _seed_run(owner_id=owner_id, run_id="r4", model_name="a-model-nobody-configured", input_tokens=1_000_000, output_tokens=1_000_000, usage_by_model=None)

    usage = client.get("/api/console/usage", params={"days": 7}).json()
    breakdown = usage["by_model"]["a-model-nobody-configured"]
    assert breakdown["cost"] is None, f"an unpriced model was given a fabricated cost of {breakdown['cost']}"


# ---------------------------------------------------------------------------
# The template itself must keep the block, and it must be a real section
# ---------------------------------------------------------------------------


def test_the_template_block_is_a_mapping_of_positive_prices():
    """Read the YAML directly so a template typo fails here, not at runtime.

    A ``model_pricing:`` that parses to a list, or whose values are strings,
    would load into an ``AppConfig`` that raises — or worse, into one that
    silently prices everything at zero.
    """
    document = yaml.safe_load(CONFIG_EXAMPLE.read_text(encoding="utf-8"))
    table = document.get("model_pricing")
    assert isinstance(table, dict) and table, f"model_pricing must be a non-empty mapping, got {type(table).__name__}"
    for name, entry in table.items():
        assert isinstance(entry, dict), f"{name}: expected a mapping, got {type(entry).__name__}"
        assert {"input", "output"} <= set(entry), f"{name}: missing input/output; ModelPriceEntry would reject it at load"
        assert float(entry["input"]) >= 0 and float(entry["output"]) >= 0, name
