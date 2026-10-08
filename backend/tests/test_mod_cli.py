"""Unit tests for the alpha mod CLI tools and offline test harness."""

from pathlib import Path

from alpha.mods.cli import main as mod_cli_main
from alpha.tui.cli import main as tui_main


def test_cli_list(capsys):
    from alpha.mods.kernel import reset_mod_kernel

    reset_mod_kernel()
    ret = mod_cli_main(["list"])
    assert ret == 0
    out = capsys.readouterr().out
    assert "fleet_estop" in out
    assert "blast_radius_guard" in out
    assert "verification_evidence_gate" in out


def test_cli_validate_clean_file(tmp_path):
    mod_code = """
class MyCustomMod:
    name = "custom_mod"
    version = "1.0.0"
    priority = 2000
    required_capabilities = {"tools:read", "evidence:record"}
    subscribed_events = {"tool.requested"}

    async def handle(self, ctx, event, next_fn):
        _ = ctx.tools.list_tools()
        ctx.evidence.record({"status": "ok"})
        return await next_fn(event)
"""
    mod_file = tmp_path / "valid_mod.py"
    mod_file.write_text(mod_code, encoding="utf-8")

    ret = mod_cli_main(["validate", str(mod_file)])
    assert ret == 0


def test_cli_validate_undeclared_capability_fails(tmp_path, capsys):
    mod_code = """
class BadMod:
    name = "bad_mod"
    version = "1.0.0"
    priority = 2000
    required_capabilities = {"tools:read"}  # Missing estop:control!
    subscribed_events = {"tool.requested"}

    async def handle(self, ctx, event, next_fn):
        ctx.estop.engage("trip")
        return await next_fn(event)
"""
    mod_file = tmp_path / "bad_mod.py"
    mod_file.write_text(mod_code, encoding="utf-8")

    ret = mod_cli_main(["validate", str(mod_file)])
    assert ret == 1
    err = capsys.readouterr().err
    assert "estop:control" in err


def test_cli_test_synthetic_runner_on_builtins(capsys):
    from alpha.mods import enforcers

    enforcers_dir = Path(enforcers.__file__).parent

    ret = mod_cli_main(["test", str(enforcers_dir)])
    assert ret == 0
    out = capsys.readouterr().out
    assert "ALL TESTS PASSED" in out


def test_tui_cli_mod_delegation(capsys):
    ret = tui_main(["mod", "list"])
    assert ret == 0
    out = capsys.readouterr().out
    assert "fleet_estop" in out
