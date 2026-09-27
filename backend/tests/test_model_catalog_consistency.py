"""Regression coverage for cross-namespace model capability drift.

Alpha resolves models from several namespaces that are merged first-wins by name
in ``GET /api/models``. When the same name appears in two of them with different
capabilities, the API silently keeps the ``models[]`` value while the other
declaration keeps claiming something else. That drift is invisible at runtime
and surfaces as a UI control the backend rejects — the shipped default declared
``union-alpha`` as ``supports_thinking: false`` in ``config.example.yaml`` and
``true`` in two other catalogs.

Hand-maintained capability tables are the industry's most common source of
exactly this bug (see the MiniMax context-window and Cline reasoning-effort
issues), so the check runs on every boot rather than being left to review.
"""

from __future__ import annotations

import pytest

from alpha.config.app_config import AppConfig
from alpha.config.model_config import ModelConfig
from alpha.config.sandbox_config import SandboxConfig
from alpha.models.catalog_consistency import (
    COMPARED_FIELDS,
    CatalogDrift,
    check_model_catalog_consistency,
    enforce_model_catalog_consistency,
)


def _config(*models: ModelConfig) -> AppConfig:
    return AppConfig(sandbox=SandboxConfig(use="alpha.sandbox.local:LocalSandboxProvider"), models=list(models))


def _model(name: str, **overrides) -> ModelConfig:
    return ModelConfig(name=name, use="langchain_openai:ChatOpenAI", model=overrides.pop("model", name), **overrides)


class TestDriftDetection:
    def test_matching_capabilities_report_clean(self) -> None:
        config = _config(_model("shared", supports_thinking=True))
        report = check_model_catalog_consistency(config, include_free_router=False, include_provider_catalog=False)
        assert report.ok
        assert "no capability drift" in report.describe()

    def test_shipped_catalog_has_no_drift(self) -> None:
        """The real example catalog and the real code tables must agree.

        This is the regression that would have caught the original
        ``union-alpha`` disagreement between ``models.example.yaml`` and the
        provider catalog.
        """
        from alpha.config.app_config import AppConfig as _AC

        try:
            config = _AC.from_file()
        except Exception:
            pytest.skip("no readable config.yaml in this environment")
        report = check_model_catalog_consistency(config)
        assert report.ok, f"capability drift between model namespaces:\n{report.describe()}"

    def test_thinking_disagreement_is_reported(self) -> None:
        drift = CatalogDrift(name="shared", field_name="supports_thinking", configured=False, other=True, other_source="test")
        assert drift.describe().startswith("model 'shared'")
        assert "supports_thinking" in drift.describe()
        assert "test" in drift.describe()

    def test_undeclared_capability_is_not_drift(self) -> None:
        """`None` means the namespace did not say, which is legitimate."""
        assert check_model_catalog_consistency(_config(), include_free_router=False, include_provider_catalog=False).ok

    def test_compared_fields_cover_the_capability_surfaces(self) -> None:
        assert "supports_thinking" in COMPARED_FIELDS
        assert "supports_vision" in COMPARED_FIELDS
        # A wrong context window silently corrupts the % context indicator and
        # the thresholds fraction-based summarization resolves from.
        assert "context_window" in COMPARED_FIELDS

    def test_names_only_in_one_namespace_are_not_drift(self) -> None:
        """A catalog-only model has nothing to disagree with."""
        config = _config(_model("configured-only"))
        report = check_model_catalog_consistency(config, include_free_router=False, include_provider_catalog=False)
        assert report.ok
        assert report.checked == 0


class TestEnforcement:
    def test_clean_report_logs_at_info(self, caplog: pytest.LogCaptureFixture) -> None:
        config = _config(_model("m"))
        with caplog.at_level("INFO"):
            report = enforce_model_catalog_consistency(config)
        assert report.ok

    def test_non_strict_downgrades_to_warning(self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
        def _drifted(_config, **_kwargs):
            from alpha.models.catalog_consistency import CatalogReport

            return CatalogReport(
                drifts=[CatalogDrift(name="m", field_name="supports_thinking", configured=False, other=True, other_source="test")],
                checked=1,
            )

        monkeypatch.setattr("alpha.models.catalog_consistency.check_model_catalog_consistency", _drifted)

        with caplog.at_level("WARNING"):
            report = enforce_model_catalog_consistency(_config(_model("m")), strict=False)
        assert not report.ok
        assert any(record.levelname == "WARNING" for record in caplog.records)
        assert not any(record.levelname == "ERROR" for record in caplog.records)

    def test_strict_logs_at_error(self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
        def _drifted(_config, **_kwargs):
            from alpha.models.catalog_consistency import CatalogReport

            return CatalogReport(
                drifts=[CatalogDrift(name="m", field_name="context_window", configured=1000, other=2000, other_source="test")],
                checked=1,
            )

        monkeypatch.setattr("alpha.models.catalog_consistency.check_model_catalog_consistency", _drifted)

        with caplog.at_level("ERROR"):
            report = enforce_model_catalog_consistency(_config(_model("m")), strict=True)
        assert not report.ok
        assert any(record.levelname == "ERROR" for record in caplog.records)
        # The message must say which namespace won, or the fix is guesswork.
        assert "first-wins by name" in caplog.text

    def test_report_lists_every_drift(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from alpha.models.catalog_consistency import CatalogReport

        def _drifted(_config, **_kwargs):
            return CatalogReport(
                drifts=[
                    CatalogDrift(name="a", field_name="supports_thinking", configured=False, other=True, other_source="s1"),
                    CatalogDrift(name="b", field_name="context_window", configured=1, other=2, other_source="s2"),
                ],
                checked=2,
            )

        monkeypatch.setattr("alpha.models.catalog_consistency.check_model_catalog_consistency", _drifted)
        text = enforce_model_catalog_consistency(_config(_model("m"))).describe()
        assert "2 capability drift" in text
        assert "'a'" in text
        assert "'b'" in text
