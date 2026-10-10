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


def test_cli_list_reports_zero_builtin_discrepancies(capsys):
    """Every built-in declares a manifest that matches its observed wiring.

    A fresh discrepancy means a mod changed what it subscribes to (or is
    granted) without updating its declared contract — exactly what the
    describe surface exists to catch.
    """
    from alpha.mods.kernel import reset_mod_kernel

    reset_mod_kernel()
    ret = mod_cli_main(["list"])
    assert ret == 0
    out = capsys.readouterr().out
    assert "manifest discrepancy(ies)" not in out


def test_cli_describe_one_mod(capsys):
    import json

    from alpha.mods.kernel import reset_mod_kernel

    reset_mod_kernel()
    ret = mod_cli_main(["describe", "blast_radius_guard"])
    assert ret == 0
    out = capsys.readouterr().out
    entry = json.loads(out.strip())
    assert entry["name"] == "blast_radius_guard"
    assert entry["discrepancies"] == []
    assert entry["first_party"] is True


def test_cli_describe_unknown_mod_fails(capsys):
    from alpha.mods.kernel import reset_mod_kernel

    reset_mod_kernel()
    ret = mod_cli_main(["describe", "no_such_mod"])
    assert ret == 1
    err = capsys.readouterr().err
    assert "no_such_mod" in err


def test_cli_chain_is_sorted_by_priority(capsys):
    from alpha.mods.kernel import reset_mod_kernel

    reset_mod_kernel()
    ret = mod_cli_main(["chain"])
    assert ret == 0
    out = capsys.readouterr().out
    # The security guard and audit ledger are outermost (KERNEL tier, priority 0).
    lines = [ln for ln in out.splitlines() if ln and not ln.startswith("Order") and not ln.startswith("---")]
    assert lines, "chain table disappeared"
    assert "sec_default" in lines[0]
    assert "audit_ledger" in lines[1]


def test_cli_audit_empty_ledger_is_honest(capsys):
    from alpha.mods.kernel import reset_mod_kernel

    reset_mod_kernel()
    ret = mod_cli_main(["audit"])
    assert ret == 0
    out = capsys.readouterr().out
    assert "Audit ledger" in out


def test_cli_audit_missing_mod_fails(capsys):
    """An absent audit mod is an error naming it, never an empty report."""
    from alpha.mods.kernel import reset_mod_kernel, set_mod_kernel

    reset_mod_kernel()
    set_mod_kernel(_KernelWithoutAudit())
    try:
        ret = mod_cli_main(["audit"])
        assert ret == 1
        err = capsys.readouterr().err
        assert "audit_ledger" in err
    finally:
        reset_mod_kernel()


def test_cli_holds_read_only(capsys, tmp_path, monkeypatch):
    from alpha.mods.approvals import HoldStore, set_hold_store
    from alpha.mods.kernel import reset_mod_kernel

    reset_mod_kernel()
    monkeypatch.setattr("alpha.mods.approvals.runtime_home", lambda: tmp_path)
    set_hold_store(HoldStore(root_dir=tmp_path))
    try:
        ret = mod_cli_main(["holds"])
        assert ret == 0
        out = capsys.readouterr().out
        assert "hold store" in out
        assert "No held actions recorded." in out
    finally:
        set_hold_store(None)


class _KernelWithoutAudit:
    """A kernel with no audit mod, to pin the CLI's honest failure."""

    def get_mod(self, name):
        return None

    def list_mods(self):
        return []

    def control_chain(self):
        return []

    def describe_mods(self):
        return []


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
