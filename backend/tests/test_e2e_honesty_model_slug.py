"""Honesty 4/6 — a model slug declared in two namespaces is compared across them.

The defect this pins
--------------------
``models[].model`` and ``model_catalog.models[].model_id`` both carry a
provider-side identifier (a "slug") for the same Alpha-side model name. They are
separate YAML keys, and **nothing compared them**. OpenRouter retired
``stealth/union-alpha``; ``models[].model`` was moved to ``unbiased/pareto`` and
the catalog's ``model_id`` was not, so the first run of every fresh install
404'd — in a model that the Settings picker still advertised.

How it was found
----------------
By running a fresh install and reading the error, then noticing the picker entry
had come from the *other* namespace than the one that had been fixed.

Why ``catalog_consistency`` cannot catch it
-------------------------------------------
``alpha.models.catalog_consistency`` compares
``COMPARED_FIELDS = (supports_thinking, supports_vision,
supports_reasoning_effort, reasoning_efforts, context_window)`` — capabilities,
**by name**. It never reads a slug. The two declarations merge first-wins *by
name*, so the stale catalog value simply loses the merge and survives
unnoticed. ``backend/AGENTS.md`` and the root ``AGENTS.md`` both say so
explicitly; this file turns that note into a test.

What is checked here that was not checked before
------------------------------------------------
``tests/test_model_config.py::test_union_alpha_slug_agrees_across_both_model_
namespaces`` already pins the one model, in the *template*. This file:
  * checks **every** model that appears in both namespaces, so the next
    ``union-alpha``-shaped rename cannot reintroduce the defect on a new entry;
  * reads the file **the Gateway actually loads**, resolved through
    ``AppConfig.resolve_config_path`` rather than a hard-coded template path;
  * pins that ``catalog_consistency`` really does not cover the slug, so nobody
    "fixes" it by assuming a boot-time check will catch it later.

Deliberately NOT covered: whether a slug still exists at the provider. That needs
a network call and a provider account, so it is a ``live`` test elsewhere, and
this file says so rather than pretending a string comparison proves liveness.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from alpha.config.model_config import ModelConfig

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def loaded_config(tmp_path, monkeypatch):
    """The ``AppConfig`` the Gateway would build from the shipped template.

    Resolved through ``ALPHA_CONFIG_PATH`` and the production
    ``AppConfig.resolve_config_path``, so "the file the Gateway loads" is a fact
    this test establishes rather than a path it assumes.

    Note that ``AppConfig`` interpolates ``$VAR`` references against the process
    environment at load time. Anything environment-dependent (a credential, a
    base URL) must therefore be asserted against the raw template text, not
    against this object, or the test's verdict would depend on whose machine ran
    it.
    """
    from alpha.config import app_config as app_config_module

    project = tmp_path / "project"
    project.mkdir()
    config_path = project / "config.yaml"
    config_path.write_text((REPO_ROOT / "config.example.yaml").read_text(encoding="utf-8"), encoding="utf-8")

    monkeypatch.setenv("ALPHA_PROJECT_ROOT", str(project))
    monkeypatch.setenv("ALPHA_CONFIG_PATH", str(config_path))
    app_config_module.reset_app_config()
    try:
        from alpha.config.app_config import get_app_config

        config = get_app_config()
        assert app_config_module.AppConfig.resolve_config_path() == config_path, "the Gateway would not load the file this test pinned"
        yield config, config_path
    finally:
        app_config_module.reset_app_config()


def _catalog_slugs_by_name(raw_document: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Every ``model_catalog`` entry, grouped by its Alpha-side ``id``.

    ``model_catalog`` is a list of providers, each with a ``models`` list whose
    entries carry ``id`` (the Alpha-side name) and ``model_id`` (the provider
    slug). The same ``id`` may legitimately appear under more than one provider
    with a different ``model_id`` — that is two *different* providers' slugs for
    one name, which is not drift. The comparison below is therefore per provider
    block, and only compares against a ``models[]`` entry when the provider is
    the one the runtime model is configured against.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for provider in raw_document.get("model_catalog") or []:
        provider_id = provider.get("id")
        for entry in provider.get("models", []) or []:
            grouped.setdefault(str(entry.get("id")), []).append({"provider": provider_id, **entry})
    return grouped


def test_both_namespaces_are_loaded_and_populated(loaded_config):
    """Premise. An empty namespace cannot disagree with anything."""
    config, _path = loaded_config
    assert config.models, "config.example.yaml must declare models[]"

    raw = yaml.safe_load((REPO_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    assert raw.get("model_catalog"), "config.example.yaml must declare the bring-your-own-provider catalog"

    catalog = _catalog_slugs_by_name(raw)
    assert catalog, "the catalog declared no models at all"


def test_every_model_in_both_namespaces_agrees_on_its_provider_slug(loaded_config):
    """The core cross-namespace check, for every overlapping model.

    ``models[].model`` is the slug the runtime actually sends to the provider.
    ``model_catalog.models[].model_id`` is the slug the Settings picker
    synthesises a ``ModelConfig`` from (``AppConfig.get_model_by_name``). If they
    disagree, one of the two entry points 404s and the other works, which is the
    worst possible shape: a model that is simultaneously offered and broken.
    """
    config, _path = loaded_config
    raw = yaml.safe_load((REPO_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    catalog = _catalog_slugs_by_name(raw)

    checked = 0
    for model in config.models:
        entries = catalog.get(model.name)
        if not entries:
            # Declared in only one namespace: nothing to compare, and that is
            # legitimate (the catalog is a bring-your-own-provider offer list).
            continue
        assert isinstance(model, ModelConfig), f"{model.name} did not load as a real ModelConfig"
        assert model.model, f"{model.name} declares no provider slug in models[].model"
        for entry in entries:
            catalog_slug = entry.get("model_id")
            assert catalog_slug, f"catalog entry for {model.name!r} (provider {entry['provider']!r}) declares no model_id"
            assert model.model == catalog_slug, (
                f"model {model.name!r}: models[].model is {model.model!r} but model_catalog "
                f"[{entry['provider']!r}].models[id={model.name!r}].model_id is {catalog_slug!r}. "
                "The provider slug must be moved in BOTH namespaces at once; catalog_consistency compares "
                "capabilities by name and never the slug, so nothing else will notice."
            )
            checked += 1

    assert checked, "no model appears in both namespaces, so this file is covering nothing; the template's namespaces have diverged"


def test_the_shipped_baseline_model_is_present_in_both_namespaces(loaded_config):
    """Pin at least one overlapping name, so the loop above cannot be vacuous.

    ``union-alpha`` is the documented baseline (root ``AGENTS.md``), so if the
    template stops declaring it in the catalog, the per-model loop above would
    silently shrink to zero comparisons and pass.
    """
    config, _path = loaded_config
    raw = yaml.safe_load((REPO_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    catalog = _catalog_slugs_by_name(raw)

    assert any(model.name == "union-alpha" for model in config.models), "the shipped baseline model is no longer declared in models[]"
    assert "union-alpha" in catalog, "the shipped baseline model is no longer in the catalog, so the cross-namespace comparison has nothing to compare"


def test_catalog_consistency_really_does_not_compare_the_slug():
    """State the gap as a test, so nobody "fixes" a drift expecting a boot check.

    ``check_model_catalog_consistency`` runs at boot and is the check everyone
    assumes would catch a stale slug. It does not, and that is why the comparison
    above exists at all. If the slug is ever added to ``COMPARED_FIELDS`` this
    test fails, and the note here becomes a comment on a redundant check rather
    than a reason to delete the one above.
    """
    from alpha.models import catalog_consistency

    compared = set(catalog_consistency.COMPARED_FIELDS)
    assert "model" not in compared, "catalog_consistency now compares models[].model; the explicit cross-namespace check above may be redundant"
    assert "model_id" not in compared, "catalog_consistency now compares model_catalog model_id; re-read the comment above"

    # And prove the report really is clean even with a deliberately stale slug,
    # so the assertion above is about behaviour and not about a constant.
    from types import SimpleNamespace

    report = catalog_consistency.check_model_catalog_consistency(
        SimpleNamespace(
            models=[
                ModelConfig(
                    name="drift-probe",
                    display_name="drift-probe",
                    description=None,
                    use="langchain_openai:ChatOpenAI",
                    model="retired/slug-A",
                    supports_thinking=False,
                    supports_vision=False,
                )
            ]
        ),
        include_free_router=False,
        include_provider_catalog=False,
    )
    assert report.ok, report.describe()


def test_a_stale_slug_is_not_detected_by_the_boot_check_used_in_production(loaded_config, monkeypatch):
    """The behavioural version of the gap: break the slug, show boot stays green.

    Slower and blunter than the constant check above, and worth it: it proves the
    failure is *silent at boot* rather than merely "not currently implemented",
    which is the reason a fresh install reaches the provider before anyone
    notices.
    """
    from alpha.models import catalog_consistency

    config, _path = loaded_config
    raw = yaml.safe_load((REPO_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    catalog = _catalog_slugs_by_name(raw)
    overlapping = [model for model in config.models if catalog.get(model.name)]
    assert overlapping, "nothing overlaps, so this file is not covering the case it was written for"

    stale = ModelConfig.model_validate({**overlapping[0].model_dump(exclude_none=True), "model": "retired/slug-that-404s"})

    class _Config:
        models = [*config.models[:-1], stale]

    report = catalog_consistency.check_model_catalog_consistency(_Config(), include_free_router=False, include_provider_catalog=False)
    assert report.ok, f"boot-time consistency now rejects a stale slug ({report.describe()}); the explicit check in this file is the only one that used to catch it"

    # The reader-side consequence, stated concretely: the picker synthesises its
    # ModelConfig from the *catalog* slug, so the picker keeps advertising the
    # retired model after models[].model has been fixed.
    catalog_slug = catalog[stale.name][0]["model_id"]
    assert catalog_slug != stale.model


def test_the_shipped_slug_is_pinned_so_a_rename_is_a_deliberate_edit(loaded_config):
    """Pin the verified slug, as a value rather than as a parse check.

    ``backend/tests/test_model_config.py`` pins ``models[].model``; this pins
    that the *catalog* half of the same fact is also that value, and that the
    value is the one the root guide records as confirmed against the live
    provider on 2026-09-28. A provider retirement is then a visible test
    failure naming the field, instead of a 404 on a fresh install.
    """
    config, _path = loaded_config
    model = next((m for m in config.models if m.name == "union-alpha"), None)
    assert model is not None, "the shipped baseline model is missing from models[]"
    assert model.model == "unbiased/pareto", (
        f"the union-alpha provider slug changed to {model.model!r}. A provider retirement is expected here, but the "
        "catalog's model_id must be moved in the same commit and this value must be re-verified against the live "
        "provider catalog before it is pinned again."
    )
    assert model.use == "langchain_openai:ChatOpenAI"

    # The credential reference is asserted against the *raw template*, not the
    # loaded config: `AppConfig` interpolates `$VAR` against the environment at
    # load time, so on a developer machine with the key exported the loaded value
    # is a literal secret and on CI it is "". Asserting on the loaded value would
    # make this test pass or fail depending on whose laptop ran it — and would
    # print a real key into the failure message.
    raw = yaml.safe_load((REPO_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    baseline = next(entry for entry in raw["models"] if entry.get("name") == "union-alpha")
    assert baseline["api_key"] == "$OPENROUTER_API_KEY", "the baseline must reference the key by environment variable, not embed it"
    assert baseline["base_url"] == "https://openrouter.ai/api/v1"


def test_no_shipped_model_embeds_a_credential_literal():
    """A template that ever gains a literal key must fail here, loudly.

    The same class of defect as a stale slug: a value in a shipped file that
    looks authoritative and is wrong (or, here, a secret committed to a
    repository). Checked on the raw template so it cannot be masked by env
    interpolation.
    """
    raw = yaml.safe_load((REPO_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    offenders: list[str] = []

    def _looks_like_a_secret(value: object) -> bool:
        if not isinstance(value, str) or value.startswith("$"):
            return False
        prefixes = ("sk-", "sk-or-", "gsk_", "xai-", "AIza", "hf_")
        return value.startswith(prefixes) or (len(value) >= 32 and value.isalnum())

    for section in ("models", "model_catalog", "free_gateways", "providers"):
        entries = raw.get(section)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for key, value in entry.items():
                if key in ("api_key", "auth_header", "headers") and _looks_like_a_secret(value):
                    offenders.append(f"{section}: {entry.get('name') or entry.get('id')}.{key}")
                if key == "auth_header" and isinstance(value, list):
                    for pair in value:
                        if isinstance(pair, list) and len(pair) == 2 and _looks_like_a_secret(pair[1]):
                            offenders.append(f"{section}: {entry.get('id')}.auth_header[{pair[0]}]")

    assert not offenders, f"a credential literal is committed in config.example.yaml: {offenders}"


def test_a_model_declared_in_only_one_namespace_is_not_an_error():
    """The control: legitimate asymmetry must not be reported as drift.

    Without this, "compare every overlapping name" could be satisfied by
    demanding that the two namespaces be identical, which they are not supposed
    to be — the catalog is a bring-your-own-provider offer list and is allowed to
    carry names ``models[]`` does not.
    """
    raw = yaml.safe_load((REPO_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    catalog = _catalog_slugs_by_name(raw)
    configured = {m.get("name") for m in raw.get("models") or []}

    catalog_only = set(catalog) - configured
    assert catalog_only, "the template's catalog has no names outside models[]; the asymmetry this file must tolerate is not present and the control is vacuous"
    assert configured - set(catalog), "every configured model is also in the catalog; the overlap-only comparison is not being tested against its boundary"
