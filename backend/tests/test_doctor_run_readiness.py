"""``scripts/doctor.py`` must not be able to report Ready on a broken checkout.

A checkout shipped four unconditional blockers -- ``capabilities`` leaking into
the provider request payload, a ``thinking_enabled`` default that made a
zero-configuration client unrunnable, async-only middleware breaking the sync
stream, and a cp1252 stdout crashing on non-ASCII output -- and ``make doctor``
printed ``Status: Ready`` for every one of them. These tests pin the two
properties that close that gap:

* the readiness checks are fail-closed, so a broken model cannot read as Ready;
* they actually reach the models a run would use, including **primary** entries.

The second one is a real regression, not a hypothetical: ``_iter_reachable_models``
used to pre-seed each entry's primary into its ``seen`` set. Every chain contains
its own primary, so the primary was then skipped and only the *fallbacks* were
ever checked -- which is the exact opposite of the function's purpose, and it
still produced a fully green Run Readiness section.

No network, no ``config.yaml``, and no provider: everything is driven through
``create_chat_model`` stubs, so these run in the normal unit suite.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCTOR_PATH = REPO_ROOT / "scripts" / "doctor.py"


def _load_doctor():
    if REPO_ROOT / "scripts" not in sys.path:
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("agent_workspace_doctor_p0", DOCTOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def doctor():
    return _load_doctor()


class _Model:
    """Minimal stand-in for a ModelConfig with just what the walker reads."""

    def __init__(self, name: str, fallbacks=None):
        self.name = name
        self.fallbacks = fallbacks or []


class _AppConfig:
    def __init__(self, models):
        self.models = models


# ---------------------------------------------------------------------------
# Reachable-model enumeration
# ---------------------------------------------------------------------------


def test_primary_models_are_probed_not_just_their_fallbacks(doctor, monkeypatch):
    """The regression: a pre-seeded ``seen`` dropped every primary.

    Before the fix this returned only ``[alpha-free]`` for a config whose entry
    was ``union-alpha -> [alpha-free]``, so ``make doctor`` reported Ready while
    never touching the model a run uses first.
    """
    models = [_Model("union-alpha", ["alpha-free"]), _Model("space-bunny", ["alpha-free"]), _Model("alpha-free", [])]
    monkeypatch.setattr(doctor, "_resolve_chain_configs", None, raising=False)

    import alpha.models.factory as factory

    def _chain(name, _app_config):
        entry = next(m for m in models if m.name == name)
        return [entry, *[next(m for m in models if m.name == f) for f in entry.fallbacks]]

    monkeypatch.setattr(factory, "_resolve_chain_configs", _chain)

    reachable = doctor._iter_reachable_models(_AppConfig(models))
    names = [m.name for m in reachable]

    assert "union-alpha" in names, "the primary was not probed; only its fallback was"
    assert "space-bunny" in names
    assert "alpha-free" in names
    assert len(names) == len(set(names)), f"a model was probed twice: {names}"


def test_run_order_is_primary_then_fallbacks(doctor, monkeypatch):
    """Order is the order a run would try, so the first failure is the useful one."""
    models = [_Model("primary", ["secondary"]), _Model("secondary", [])]
    import alpha.models.factory as factory

    def _chain(name, _app_config):
        entry = next(m for m in models if m.name == name)
        return [entry, *[next(m for m in models if m.name == f) for f in entry.fallbacks]]

    monkeypatch.setattr(factory, "_resolve_chain_configs", _chain)
    names = [m.name for m in doctor._iter_reachable_models(_AppConfig(models))]
    assert names == ["primary", "secondary"]


def test_an_unresolvable_chain_still_yields_its_primary(doctor, monkeypatch):
    """A broken chain is itself a finding, so the primary must survive to report it."""
    import alpha.models.factory as factory

    monkeypatch.setattr(factory, "_resolve_chain_configs", lambda name, cfg: (_ for _ in ()).throw(ValueError("no such model")))
    reachable = doctor._iter_reachable_models(_AppConfig([_Model("broken", ["missing"])]))
    assert [m.name for m in reachable] == ["broken"]


# ---------------------------------------------------------------------------
# Fail-closed behaviour
# ---------------------------------------------------------------------------


def test_construction_failure_is_a_fail_not_a_warning(doctor, tmp_path, monkeypatch):
    """The whole point: a model that cannot be built must not read as Ready."""
    import alpha.models.factory as factory

    monkeypatch.setattr(factory, "_resolve_chain_configs", lambda name, cfg: [cfg.models[0]])
    monkeypatch.setattr(
        factory,
        "create_chat_model",
        lambda *a, **k: (_ for _ in ()).throw(TypeError("Completions.create() got an unexpected keyword argument 'capabilities'")),
    )
    results = doctor.check_models_construct(_stub_config(tmp_path))
    assert results, "no result at all, so the check is vacuous"
    assert all(r.status == "fail" for r in results), [(r.label, r.status) for r in results]


def test_a_leaked_metadata_key_fails_without_spending_a_request(doctor, tmp_path, monkeypatch):
    """The belt-and-braces check: a diverted key is caught statically.

    The OpenAI client does not reject an unknown constructor kwarg, it warns and
    moves it into ``model_kwargs``, so construction succeeding proves nothing.
    """
    import alpha.models.factory as factory

    monkeypatch.setattr(factory, "_resolve_chain_configs", lambda name, cfg: [cfg.models[0]])

    class _Leaky:
        model_kwargs = {"capabilities": []}

    monkeypatch.setattr(factory, "create_chat_model", lambda *a, **k: _Leaky())
    results = doctor.check_models_construct(_stub_config(tmp_path))
    assert [r.status for r in results] == ["fail"]
    assert "capabilities" in (results[0].detail or "")


def test_a_clean_model_is_ok(doctor, tmp_path, monkeypatch):
    """Guards the two tests above from passing merely because everything fails."""
    import alpha.models.factory as factory

    monkeypatch.setattr(factory, "_resolve_chain_configs", lambda name, cfg: [cfg.models[0]])

    class _Clean:
        model_kwargs = {}

    monkeypatch.setattr(factory, "create_chat_model", lambda *a, **k: _Clean())
    results = doctor.check_models_construct(_stub_config(tmp_path))
    assert [r.status for r in results] == ["ok"]


def test_an_unreachable_provider_is_a_fail_not_a_warning(doctor, tmp_path, monkeypatch):
    """A working API key against a rejecting endpoint is still unrunnable."""
    import alpha.models.factory as factory

    monkeypatch.setattr(factory, "_resolve_chain_configs", lambda name, cfg: [cfg.models[0]])

    class _Client:
        model_kwargs = {}

        def with_config(self, config):
            return self

        def invoke(self, *a, **k):
            raise RuntimeError("insufficient_quota")

    monkeypatch.setattr(factory, "create_chat_model", lambda *a, **k: _Client())
    results = doctor.check_provider_reachable(_stub_config(tmp_path))
    assert [r.status for r in results] == ["fail"]
    assert "insufficient_quota" in (results[0].detail or "")


def test_no_network_escape_hatch_skips_only_the_request(doctor, tmp_path, monkeypatch):
    """An air-gapped host must get a skip, never a silent Ready.

    Also asserts the skip is *visible*: a budget-exhausted or skipped probe that
    rendered as "ok" is how this whole class of bug hides.
    """
    import alpha.models.factory as factory

    monkeypatch.setattr(factory, "_resolve_chain_configs", lambda name, cfg: [cfg.models[0]])
    monkeypatch.setenv(doctor.SKIP_NETWORK_ENV, "1")

    def _explode(*a, **k):  # pragma: no cover - must not be reached
        raise AssertionError("no request may be sent when the escape hatch is set")

    monkeypatch.setattr(factory, "create_chat_model", _explode)
    results = doctor.check_provider_reachable(_stub_config(tmp_path))
    assert [r.status for r in results] == ["skip"]
    assert doctor.SKIP_NETWORK_ENV in (results[0].detail or "")


def test_probe_budget_exhaustion_reports_skip_not_ok(doctor, tmp_path, monkeypatch):
    """Same reasoning as the escape hatch: unprobed must never read as probed."""
    import alpha.models.factory as factory

    monkeypatch.setattr(factory, "_resolve_chain_configs", lambda name, cfg: [cfg.models[0]])
    monkeypatch.setattr(factory, "create_chat_model", lambda *a, **k: _Client())
    monkeypatch.setattr(doctor, "_PROBE_TOTAL_BUDGET_SECONDS", -1.0)

    class _Client:
        model_kwargs = {}

        def with_config(self, config):
            return self

        def invoke(self, *a, **k):  # pragma: no cover - must not be reached
            raise AssertionError("probe ran despite an exhausted budget")

    monkeypatch.setattr(factory, "create_chat_model", lambda *a, **k: _Client())
    results = doctor.check_provider_reachable(_stub_config(tmp_path))
    assert [r.status for r in results] == ["skip"]
    assert "budget" in (results[0].detail or "")


def _stub_config(tmp_path: Path) -> Path:
    """A minimal config.yaml the readiness checks can load without any real model."""
    path = Path(tmp_path) / "config.yaml"
    path.write_text(
        "models:\n"
        "  - name: probe-model\n"
        "    use: langchain_openai:ChatOpenAI\n"
        "    model: probe-model-1\n"
        "    api_key: test-key-not-a-secret\n"
        "sandbox:\n"
        "  use: alpha.sandbox.local:LocalSandboxProvider\n",
        encoding="utf-8",
    )
    return path
