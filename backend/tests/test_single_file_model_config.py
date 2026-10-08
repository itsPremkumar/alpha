"""`config.yaml` is the only file that configures a model. There is no second one.

Found by running the product, not by reading it. `alpha-free` was advertised in
`GET /api/models` as an available free model, yet every run on it failed with::

    no free provider candidates: discovery has not succeeded for any provider yet

and `POST /api/models/free/probe` answered HTTP 200 with::

    {"probes": {}, "synced_models_count": 5, "available_models": ["alpha-free"]}

Zero providers probed, while a model was advertised as usable. The cause: the
keyless gateway list lived **only** in a separate `models.yaml`, read
exclusively through `alpha.config.models_catalog.get_models_catalog()`, which
returns an empty catalog when the file is absent. No first-run step
(`make setup`, `make config`) reliably created one. So a fresh install that
followed the documented path had an empty gateway list and no way to learn why.

Two files declaring models was also the confusion itself: `models[]` existed in
both, merged wholesale by name, so which file was authoritative for a given
capability was not something an operator could determine by reading. The second
file is now **gone** — its three catalog-only sections moved into `config.yaml`
as `model_catalog:`, `free_gateways:` and `model_pricing:`, and the loader,
template, generator script and deprecation path were removed with it.

These tests pin the single-file contract from both directions: `config.yaml`
alone must fully populate the free router, the bring-your-own-provider catalog
and model discovery; and no trace of the removed file may remain to point an
operator at a path that no longer exists.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_EXAMPLE = REPO_ROOT / "config.example.yaml"


def _load_config_example() -> dict:
    return yaml.safe_load(CONFIG_EXAMPLE.read_text(encoding="utf-8")) or {}


class TestConfigExampleCarriesTheWholeCatalog:
    def test_the_three_catalog_sections_live_in_config_example(self) -> None:
        doc = _load_config_example()
        assert doc.get("free_gateways"), "config.example.yaml must declare the keyless free gateways"
        assert doc.get("model_catalog"), "config.example.yaml must declare the bring-your-own-provider offers"
        assert doc.get("model_pricing"), "config.example.yaml must declare the fallback price table"

    def test_every_free_gateway_still_validates(self) -> None:
        from alpha.config.model_catalog_schema import FreeGatewayEntry

        for entry in _load_config_example().get("free_gateways") or []:
            FreeGatewayEntry(**entry)  # raises on a malformed entry

    def test_config_example_still_parses_as_one_document(self) -> None:
        """A block sequence indented level with its key is a YAML error."""
        assert isinstance(_load_config_example(), dict)

    def test_default_alpha_free_model_keeps_provider_failover_enabled(self) -> None:
        models = _load_config_example().get("models") or []
        alpha_free = next(model for model in models if model.get("name") == "alpha-free")
        assert alpha_free["model"] == "auto"

    def test_the_gateway_ids_are_unique(self) -> None:
        """One file means a duplicate id is a config error, not a merge."""
        ids = [g["id"] for g in (_load_config_example().get("free_gateways") or [])]
        assert len(ids) == len(set(ids)), f"duplicate free gateway ids in config.example.yaml: {ids}"


class TestOneFileIsSufficient:
    """With no second model file anywhere, everything resolves from config.yaml."""

    @pytest.fixture
    def config_only(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """A config.yaml carrying the shipped catalog, and no second model file.

        ``ALPHA_PROJECT_ROOT`` is pointed at the temp directory so resolution is
        isolated from whatever this checkout happens to have.
        """
        root = tmp_path / "project"
        root.mkdir()
        (root / "config.yaml").write_text(yaml.safe_dump(_load_config_example(), sort_keys=False), encoding="utf-8")

        from alpha.config import app_config

        monkeypatch.setenv("ALPHA_PROJECT_ROOT", str(root))
        monkeypatch.setenv("ALPHA_CONFIG_PATH", str(root / "config.yaml"))
        monkeypatch.chdir(root)
        app_config.reset_app_config_cache() if hasattr(app_config, "reset_app_config_cache") else None
        yield root / "config.yaml"

    def test_the_free_router_finds_gateways_from_config_alone(self, config_only: Path) -> None:
        from alpha.config.app_config import get_app_config
        from alpha.models.free_router import providers

        cfg = get_app_config()
        assert cfg.free_gateways, "config.yaml alone must supply the free gateways"

        providers.refresh_free_gateways()
        assert providers.PROVIDERS, "the free router found no gateways from config.yaml alone"
        for gateway in cfg.free_gateways:
            assert gateway.id in providers.PROVIDERS, f"{gateway.id} was declared in config.yaml but the router did not see it"
            assert providers.PROVIDERS[gateway.id].base_url == gateway.base_url

    def test_the_byo_provider_catalog_is_populated_from_config_alone(self, config_only: Path) -> None:
        from alpha.models.provider_manager import refresh_provider_specs

        specs = refresh_provider_specs()
        assert specs, "provider_manager found no bring-your-own-provider offers from config.yaml alone"

    def test_model_discovery_sees_providers_from_config_alone(self, config_only: Path) -> None:
        from alpha.models.discovery import known_providers

        assert known_providers(), "discovery found no providers from config.yaml alone"

    def test_a_gateway_needs_no_personal_credential(self, config_only: Path) -> None:
        """A keyless endpoint that quietly required a secret would be a config lie."""
        from alpha.config.app_config import get_app_config

        for gateway in get_app_config().free_gateways:
            for name, value in gateway.auth_header:
                assert value, f"{gateway.id} declares an empty auth header '{name}'"
                assert not value.startswith("sk-"), f"{gateway.id} looks like it carries a real API key in auth_header"
                # A documented anonymous constant is fine; a per-user secret is not.
                assert "$" not in value, f"{gateway.id} auth_header '{name}' interpolates a secret instead of a constant"


class TestTheSecondFileIsGone:
    """Nothing may still point an operator at a file that no longer exists."""

    def test_the_deprecated_template_is_deleted(self) -> None:
        assert not (REPO_ROOT / "models.example.yaml").exists(), "models.example.yaml must be removed, not deprecated"

    def test_the_deprecated_loader_module_is_deleted(self) -> None:
        module = REPO_ROOT / "backend" / "packages" / "harness" / "alpha" / "config" / "models_catalog.py"
        assert not module.exists(), "models_catalog.py must be removed, not deprecated"

    def test_the_deprecated_generator_script_is_deleted(self) -> None:
        script = REPO_ROOT / "backend" / "scripts" / "gen_models_example.py"
        assert not script.exists(), "gen_models_example.py must be removed with the template it generated"

    def test_the_loader_module_cannot_be_imported(self) -> None:
        import importlib

        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("alpha.config.models_catalog")

    def test_no_python_module_still_reads_a_second_model_file(self) -> None:
        """A leftover reference would be a runtime crash, not just a stale comment.

        Parsed with ``ast`` rather than grepped on purpose. A text search also
        matches the docstrings and ``AGENTS.md`` prose that deliberately explain
        why the second file was removed, so it would fail on the historical
        record instead of on live code. This looks only at real code: an import
        of the deleted module, or a name/attribute binding of one of the symbols
        that module exported.
        """
        removed_module = "alpha.config.models_catalog"
        removed_names = {
            "get_models_catalog",
            "load_models_catalog",
            "read_raw_models_catalog",
            "resolve_models_config_path",
            "reset_models_catalog_cache",
            "ModelsCatalog",
            "MODELS_CATALOG_FILENAMES",
            "MODELS_CATALOG_VERSION",
            "MODELS_CONFIG_PATH_ENV",
            "ALPHA_MODELS_CONFIG_PATH",
        }

        offenders: list[str] = []
        for path in sorted((REPO_ROOT / "backend").rglob("*.py")):
            if ".venv" in path.parts or "node_modules" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(removed_module):
                    offenders.append(f"{path}:{node.lineno}: from {node.module} import ...")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if removed_module in alias.name:
                            offenders.append(f"{path}:{node.lineno}: import {alias.name}")
                elif isinstance(node, ast.Name) and node.id in removed_names:
                    offenders.append(f"{path}:{node.lineno}: name {node.id}")
                elif isinstance(node, ast.Attribute) and node.attr in removed_names:
                    offenders.append(f"{path}:{node.lineno}: attribute {node.attr}")

        assert not offenders, "the removed model file is still referenced in code:\n" + "\n".join(offenders)

    def test_make_config_does_not_seed_a_second_model_file(self) -> None:
        """The seeding list must not name the removed file as a destination."""
        source = (REPO_ROOT / "scripts" / "configure.py").read_text(encoding="utf-8")
        assert '"models.yaml"' not in source, "configure.py must not create the removed model file"

    def test_the_two_first_run_paths_agree(self) -> None:
        """The actual defect: two documented setup paths seeded different files.

        `scripts/configure.py` (make config) seeded a second model file while the
        interactive `scripts/setup_wizard.py` never did, so whichever ran, one
        documented first-run order produced a different model configuration than
        the other. They must seed the same set.
        """
        configure = (REPO_ROOT / "scripts" / "configure.py").read_text(encoding="utf-8")
        wizard = (REPO_ROOT / "scripts" / "setup_wizard.py").read_text(encoding="utf-8")
        for name in ("config.yaml", ".env"):
            assert name in configure and name in wizard, f"the two first-run paths disagree about {name}"
