"""The static deployment preflight must not claim runtime readiness."""

from __future__ import annotations

import prod_check


def test_successful_static_preflight_does_not_claim_production_readiness(monkeypatch, capsys):
    monkeypatch.setattr(prod_check, "check_versions", lambda failures, warnings: None)
    monkeypatch.setattr(prod_check, "check_config_files", lambda failures, warnings: None)
    monkeypatch.setattr(prod_check, "check_update_policy", lambda failures, warnings: None)
    monkeypatch.setattr(prod_check, "check_env", lambda failures, warnings: None)
    monkeypatch.setattr("sys.argv", ["prod_check.py"])

    assert prod_check.main() == 0

    output = capsys.readouterr().out
    assert "deployment preflight: passed" in output
    assert "Runtime, model-provider, and UI readiness were not checked" in output
    assert "production ready" not in output.lower()
