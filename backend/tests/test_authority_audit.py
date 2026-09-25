"""Hermetic regression tests for the static authority auditor.

Every scan in this file uses a synthetic tree under ``tmp_path``.  The real
Alpha checkout is scanned only by the explicit CLI deliverable, never by a
unit test; this keeps the auditor's own tests hermetic and prevents a passing
test from depending on unrelated application modules.
"""

from __future__ import annotations

import json
from pathlib import Path

from alpha.safety.authority import (
    AuditReport,
    AuthorityAuditConfig,
    Reversibility,
    Verdict,
    compute_coverage,
    diff_reports,
    load_baseline,
    render_json,
    render_markdown,
    scan,
    write_baseline,
)
from alpha.safety.authority.cli import EXIT_CLEAN, EXIT_FINDINGS, EXIT_USAGE, main


def _config(root: Path, **overrides: object) -> AuthorityAuditConfig:
    values: dict[str, object] = {"scanned_roots": ["."], "excluded_paths": []}
    values.update(overrides)
    return AuthorityAuditConfig(**values)


def _write(root: Path, name: str, source: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def _records(report: AuditReport, needle: str) -> list:
    return [record for record in report.records if needle in record.capability.concrete_action]


def test_synthetic_tree_records_gates_and_rule_ids(tmp_path: Path) -> None:
    source = """
from fastapi import APIRouter
router = APIRouter()

@router.post("/admin/delete")
@require_permission("admin", "delete")
async def delete_admin():
    os.remove("managed.txt")

def guarded_delete():
    requires_approval = True
    os.remove("approved.txt")

def unguarded_delete():
    os.remove("not-approved.txt")

def run_command():
    subprocess.run(["echo", "hello"])

def reach_network():
    requests.post("https://example.invalid/collect")
"""
    _write(tmp_path, "surface.py", source)
    report = scan(tmp_path, config=_config(tmp_path), revision="fixture-1")

    route = _records(report, "route POST /admin/delete")
    assert len(route) == 1
    assert route[0].verdict is Verdict.SAFE_UNATTENDED
    assert route[0].capability.source == "surface.py:7"
    assert route[0].capability.classification_rule_id == "REV-002"
    assert route[0].gate is not None
    assert route[0].gate.default_posture.value == "default_deny"

    approved = _records(report, "os.remove approved.txt")
    assert len(approved) == 1
    assert approved[0].capability.reversibility_class is Reversibility.DESTRUCTIVE_LOCAL
    assert approved[0].capability.classification_rule_id == "REV-003"
    assert approved[0].gate is not None
    assert approved[0].verdict is Verdict.REQUIRES_OPERATOR_TOKEN

    ungated = _records(report, "not-approved.txt")
    assert len(ungated) == 1
    assert ungated[0].gate is None
    assert ungated[0].capability.currently_gated is False
    assert ungated[0].verdict is Verdict.REQUIRES_OPERATOR_TOKEN

    assert len(_records(report, "subprocess.run")) == 1
    assert len(_records(report, "requests.post")) == 1
    assert _records(report, "requests.post")[0].capability.externality.value == "external"
    assert report.coverage.denominator >= 3
    assert report.coverage.numerator < report.coverage.denominator


def test_unparseable_file_is_unknown_and_scan_completes(tmp_path: Path) -> None:
    _write(tmp_path, "broken.py", "def broken(:\n")
    _write(tmp_path, "read_only.py", "def status():\n    return 1\n")

    report = scan(tmp_path, config=_config(tmp_path), revision="fixture-2")

    parse_unknowns = [item for item in report.unknowns if item.kind == "parse_error"]
    assert len(parse_unknowns) == 1
    assert parse_unknowns[0].path == "broken.py"
    assert report.verdict_counts[Verdict.UNKNOWN.value] >= 1
    assert any(record.verdict is Verdict.UNKNOWN for record in report.records)
    assert not any(record.verdict is Verdict.SAFE_UNATTENDED and record.capability.source_file == "broken.py" for record in report.records)


def test_empty_allowlist_fail_open_is_ineffective(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "guard.py",
        """
ALLOWED_TOOLS = []

def is_allowed(name):
    if name not in ALLOWED_TOOLS:
        return True
    return False
""",
    )
    report = scan(tmp_path, config=_config(tmp_path), revision="fixture-3")

    ineffective = [gate for gate in report.gates if not gate.can_fail]
    assert any(gate.enforcement_point == "is_allowed" for gate in ineffective)
    assert any("fail-open" in (gate.ineffective_reason or "") for gate in ineffective)
    assert any(gate.id in report.ineffective_gates for gate in ineffective)


def test_constant_guard_is_reported_as_gate_that_cannot_fail(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "constant.py",
        """
def always_allow(value):
    return True
""",
    )
    report = scan(tmp_path, config=_config(tmp_path), revision="fixture-4")

    guard = next(gate for gate in report.gates if gate.enforcement_point == "always_allow")
    assert guard.can_fail is False
    assert guard.id in report.ineffective_gates
    assert "unconditional True" in (guard.ineffective_reason or "")


def test_unknown_is_never_safe_and_strict_cli_fails(tmp_path: Path, capsys) -> None:
    _write(tmp_path, "dynamic.py", "def register_runtime():\n    register(dynamic_name)\n")
    report = scan(tmp_path, config=_config(tmp_path), revision="fixture-5")
    assert any(record.verdict is Verdict.UNKNOWN for record in report.records)
    assert not any(record.verdict is Verdict.SAFE_UNATTENDED for record in report.records if record.capability.classification_rule_id.startswith("UNK-"))

    exit_code = main(["scan", "--root", str(tmp_path), "--json", "--strict", "--revision", "fixture-5"])
    assert exit_code == EXIT_FINDINGS
    assert '"unknown"' in capsys.readouterr().out


def test_rendering_is_deterministic_and_has_no_wall_clock(tmp_path: Path) -> None:
    _write(tmp_path, "surface.py", "def remove_it():\n    os.remove('x')\n")
    settings = _config(tmp_path)
    first = scan(tmp_path, config=settings, revision="fixed-revision")
    second = scan(tmp_path, config=settings, revision="fixed-revision")

    first_json = render_json(first)
    second_json = render_json(second)
    first_markdown = render_markdown(first)
    second_markdown = render_markdown(second)
    assert first_json == second_json
    assert first_markdown == second_markdown
    assert "fixed-revision" in first_json
    assert "generated_at" not in first_json.casefold()
    assert "timestamp" not in first_json.casefold()
    assert "timestamp" not in first_markdown.casefold()
    assert json.loads(first_json)["revision"] == "fixed-revision"


def test_report_does_not_copy_secret_literals(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "secret.py",
        """
PASSWORD = "super-secret-value"
def login():
    requests.post("https://example.invalid", json={"password": PASSWORD})
""",
    )
    report = scan(tmp_path, config=_config(tmp_path), revision="fixture-redaction")
    rendered = render_json(report) + render_markdown(report)
    assert "super-secret-value" not in rendered
    assert any("secret access" in record.capability.concrete_action for record in report.records)


def test_baseline_diff_reports_new_destructive_removed_gate_posture_flip_and_unknown(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "surface.py",
        """
def remove_it():
    os.remove('x')

def is_allowed(name):
    if name not in {'old_tool'}:
        return False
    return True

def old_guard(name):
    return name in {'old_tool'}
""",
    )
    old = scan(tmp_path, config=_config(tmp_path), revision="old")
    baseline_path = tmp_path / "baseline.json"
    write_baseline(baseline_path, old, revision="old")

    _write(
        tmp_path,
        "surface.py",
        """
def remove_it():
    os.remove('x')

def is_allowed(name):
    return True

def new_danger():
    subprocess.run(['rm', '-rf', 'x'])

def never():
    return True

def new_guard(name):
    return name in {'new_tool'}

def dynamic_surface():
    register(dynamic_name)
""",
    )
    new = scan(tmp_path, config=_config(tmp_path), revision="new")
    diff = diff_reports(old, new)
    assert diff.new_destructive_capabilities
    assert diff.new_gates
    assert diff.removed_gates
    assert diff.posture_flips
    assert diff.posture_flips[0].severity == "critical"
    assert diff.new_unknowns
    assert diff.baseline_revision == "old"
    assert diff.current_revision == "new"

    loaded = load_baseline(baseline_path)
    assert loaded.revision == "old"


def test_census_does_not_import_application_module(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "explodes.py",
        """
raise RuntimeError('this module must never be imported by the auditor')

def destructive():
    os.remove('x')
""",
    )
    report = scan(tmp_path, config=_config(tmp_path), revision="fixture-6")
    assert report.scanned_files == ("explodes.py",)
    assert any(record.capability.concrete_action == "os.remove x" for record in report.records)


def test_config_readers_cover_every_declared_key(tmp_path: Path) -> None:
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps({"keyword_rules": {"teleport": {"rule_id": "CUSTOM-1", "reversibility": "irreversible", "externality": "external", "reason": "reviewed custom rule"}}}), encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    output = tmp_path / "out.json"
    settings = AuthorityAuditConfig(
        enabled=True,
        scanned_roots=["src"],
        excluded_paths=["src/skip"],
        rule_set_path="rules.json",
        baseline_path="baseline.json",
        output_path="out.json",
        strictness="strict",
    )
    assert settings.should_run() is True
    assert settings.strict is True
    assert settings.resolve_scanned_roots(tmp_path) == [(tmp_path / "src").resolve()] or settings.resolve_scanned_roots(tmp_path) == [tmp_path.resolve()]
    assert settings.is_excluded(tmp_path / "src" / "skip" / "x.py", tmp_path) is True
    assert settings.read_rule_set(tmp_path)["teleport"]["rule_id"] == "CUSTOM-1"
    assert settings.resolve_baseline_path(tmp_path) == baseline.resolve()
    assert settings.resolve_output_path(tmp_path) == output.resolve()
    assert settings.unknown_is_failure() is True


def test_clean_read_only_scan_exits_zero(tmp_path: Path, capsys) -> None:
    _write(tmp_path, "read_only.py", "def status():\n    return 1\n")
    assert main(["scan", "--root", str(tmp_path), "--json", "--revision", "clean"]) == EXIT_CLEAN
    assert '"safe_unattended"' in capsys.readouterr().out


def test_cli_writes_only_declared_output_and_uses_documented_exit_codes(tmp_path: Path, capsys) -> None:
    _write(tmp_path, "surface.py", "def remove_it():\n    os.remove('x')\n")
    output = tmp_path / "report.json"
    code = main(["scan", "--root", str(tmp_path), "--json", "--output", str(output), "--revision", "cli"])
    assert code == EXIT_FINDINGS
    assert output.exists()
    assert json.loads(output.read_text(encoding="utf-8"))["revision"] == "cli"
    assert not (tmp_path / "docs").exists()
    assert "authority audit:" in capsys.readouterr().out

    bad_root = tmp_path / "missing"
    assert main(["scan", "--root", str(bad_root), "--json"]) == EXIT_USAGE


def test_coverage_reports_gates_that_protect_nothing(tmp_path: Path) -> None:
    _write(tmp_path, "surface.py", "def status():\n    return 1\n")
    _write(tmp_path, "flags.py", "enabled = False\n")
    report = scan(tmp_path, config=_config(tmp_path), revision="fixture-7")
    assert any(gate.id in report.coverage.gates_protecting_nothing for gate in report.gates)
    assert compute_coverage(report.records, report.gates).denominator == report.coverage.denominator
