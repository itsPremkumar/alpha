"""Tests for the Semantic Git Time-Machine and AST Change-Delta Analyzer."""

from __future__ import annotations

import subprocess

import pytest

from alpha.lineage.semantic_git_delta import (
    SemanticGitDeltaAnalyzer,
    TransformationKind,
    compute_risk_score,
    extract_symbols,
    risk_band,
)
from alpha.tools.builtins.semantic_git_delta_tool import analyze_semantic_git_delta

BEFORE = '''
class PaymentService:
    def charge(self, user, amount):
        raise ValueError("bad amount")
        return amount


def legacy_helper(x):
    return x
'''

AFTER = '''
class PaymentService:
    @audit
    def charge(self, user, amount: float, currency: str = "USD") -> float:
        raise InvalidAmount("bad amount")
        return amount
'''


def _analyzer(tmp_path) -> SemanticGitDeltaAnalyzer:
    """Return an analyzer rooted at ``tmp_path``."""
    return SemanticGitDeltaAnalyzer(tmp_path)


def _kinds(report) -> set[str]:
    """Return the set of transformation kinds in ``report``."""
    return {item.kind.value for item in report.transformations}


# ---------------------------------------------------------------------------
# Symbol extraction
# ---------------------------------------------------------------------------


def test_extract_symbols_captures_classes_and_methods():
    symbols = {item.qualified_name for item in extract_symbols(BEFORE)}
    assert {"PaymentService", "PaymentService.charge", "legacy_helper"} == symbols


def test_extract_symbols_records_parameters_and_bases():
    source = "class Sub(Base):\n    def run(self, a, b=2):\n        return a\n"
    class_symbol = next(item for item in extract_symbols(source) if item.name == "Sub")
    assert class_symbol.bases == ["Base"]
    method = next(item for item in extract_symbols(source) if item.name == "run")
    assert method.parameters == ["self", "a", "b"]
    assert method.defaults["b"] == "2"


def test_extract_symbols_records_raised_and_handled():
    source = (
        "def run():\n"
        "    try:\n"
        "        pass\n"
        "    except KeyError:\n"
        "        raise ValueError('bad')\n"
    )
    symbol = extract_symbols(source)[0]
    assert symbol.raised == ["ValueError"]
    assert symbol.handled == ["KeyError"]


def test_extract_symbols_tolerates_syntax_errors():
    assert extract_symbols("def broken(:\n") == []


def test_extract_symbols_records_var_arguments():
    source = "def run(a, *args, **kwargs):\n    return a\n"
    symbol = extract_symbols(source)[0]
    assert symbol.parameters == ["a", "*args", "**kwargs"]


# ---------------------------------------------------------------------------
# Transformation detection
# ---------------------------------------------------------------------------


def test_rename_is_detected_by_body_shape(tmp_path):
    before = {"svc.py": "def charge(x):\n    return x\n"}
    after = {"svc.py": "def bill(x):\n    return x\n"}
    report = _analyzer(tmp_path).analyze_sources(before, after)
    rename = next(
        item for item in report.transformations if item.kind is TransformationKind.SYMBOL_RENAMED
    )
    assert rename.before == "charge"
    assert rename.after == "bill"
    assert rename.breaking is True


def test_renamed_symbol_is_not_also_reported_as_removed(tmp_path):
    before = {"svc.py": "def charge(x):\n    return x\n"}
    after = {"svc.py": "def bill(x):\n    return x\n"}
    report = _analyzer(tmp_path).analyze_sources(before, after)
    assert TransformationKind.SYMBOL_REMOVED.value not in _kinds(report)


def test_body_rewrite_is_not_treated_as_rename(tmp_path):
    before = {"svc.py": "def charge(x):\n    return x\n"}
    after = {"svc.py": "def charge(x):\n    return x * 3\n"}
    report = _analyzer(tmp_path).analyze_sources(before, after)
    assert TransformationKind.SYMBOL_RENAMED.value not in _kinds(report)


def test_removed_symbol_is_reported(tmp_path):
    before = {"svc.py": "def gone():\n    return 1\n"}
    after = {"svc.py": "def stays():\n    return 2\n"}
    report = _analyzer(tmp_path).analyze_sources(before, after)
    assert TransformationKind.SYMBOL_REMOVED.value in _kinds(report)
    assert TransformationKind.SYMBOL_ADDED.value in _kinds(report)


def test_added_defaulted_parameter_is_not_breaking(tmp_path):
    before = {"svc.py": "def run(a):\n    return a\n"}
    after = {"svc.py": "def run(a, b=1):\n    return a\n"}
    report = _analyzer(tmp_path).analyze_sources(before, after)
    addition = next(
        item for item in report.transformations if item.kind is TransformationKind.PARAMETER_ADDED
    )
    assert addition.breaking is False
    assert "with a default value" in addition.detail


def test_added_required_parameter_is_breaking(tmp_path):
    before = {"svc.py": "def run(a):\n    return a\n"}
    after = {"svc.py": "def run(a, b):\n    return a\n"}
    report = _analyzer(tmp_path).analyze_sources(before, after)
    addition = next(
        item for item in report.transformations if item.kind is TransformationKind.PARAMETER_ADDED
    )
    assert addition.breaking is True
    assert "as required" in addition.detail


def test_removed_parameter_is_breaking(tmp_path):
    before = {"svc.py": "def run(a, b):\n    return a\n"}
    after = {"svc.py": "def run(a):\n    return a\n"}
    report = _analyzer(tmp_path).analyze_sources(before, after)
    assert TransformationKind.PARAMETER_REMOVED.value in _kinds(report)


def test_parameter_rename_is_detected(tmp_path):
    before = {"svc.py": "def run(old_name):\n    return old_name\n"}
    after = {"svc.py": "def run(new_name):\n    return new_name\n"}
    report = _analyzer(tmp_path).analyze_sources(before, after)
    rename = next(
        item
        for item in report.transformations
        if item.kind is TransformationKind.PARAMETER_RENAMED
    )
    assert rename.before == "old_name"
    assert rename.after == "new_name"


def test_annotation_and_return_type_changes(tmp_path):
    before = {"svc.py": "def run(a):\n    return a\n"}
    after = {"svc.py": "def run(a: int) -> str:\n    return a\n"}
    report = _analyzer(tmp_path).analyze_sources(before, after)
    kinds = _kinds(report)
    assert TransformationKind.ANNOTATION_CHANGED.value in kinds
    assert TransformationKind.RETURN_TYPE_CHANGED.value in kinds


def test_decorator_change(tmp_path):
    before = {"svc.py": "def run():\n    return 1\n"}
    after = {"svc.py": "@audit\ndef run():\n    return 1\n"}
    report = _analyzer(tmp_path).analyze_sources(before, after)
    assert TransformationKind.DECORATOR_CHANGED.value in _kinds(report)


def test_exception_contract_change(tmp_path):
    before = {"svc.py": "def run():\n    raise ValueError('x')\n"}
    after = {"svc.py": "def run():\n    raise InvalidAmount('x')\n"}
    report = _analyzer(tmp_path).analyze_sources(before, after)
    assert TransformationKind.EXCEPTION_RAISED_CHANGED.value in _kinds(report)


def test_handled_exception_change_is_not_breaking(tmp_path):
    before = {"svc.py": "def run():\n    try:\n        pass\n    except KeyError:\n        pass\n"}
    after = {"svc.py": "def run():\n    try:\n        pass\n    except IndexError:\n        pass\n"}
    report = _analyzer(tmp_path).analyze_sources(before, after)
    change = next(
        item
        for item in report.transformations
        if item.kind is TransformationKind.EXCEPTION_HANDLED_CHANGED
    )
    assert change.breaking is False


def test_base_class_change(tmp_path):
    before = {"svc.py": "class Node:\n    pass\n"}
    after = {"svc.py": "class Node(Base):\n    pass\n"}
    report = _analyzer(tmp_path).analyze_sources(before, after)
    assert TransformationKind.BASE_CLASS_CHANGED.value in _kinds(report)


def test_visibility_change(tmp_path):
    before = {"svc.py": "def helper():\n    return 1\n"}
    after = {"svc.py": "def _helper():\n    return 1\n"}
    report = _analyzer(tmp_path).analyze_sources(before, after)
    assert TransformationKind.VISIBILITY_CHANGED.value in _kinds(report)


def test_pure_addition_is_not_breaking(tmp_path):
    before = {"svc.py": "def existing():\n    return 1\n"}
    after = {"svc.py": "def existing():\n    return 1\n\n\ndef fresh():\n    return 2\n"}
    report = _analyzer(tmp_path).analyze_sources(before, after)
    breaking = [item for item in report.transformations if item.breaking]
    assert breaking == []
    assert report.risk_score == 0.0
    assert report.risk_band == "none"


# ---------------------------------------------------------------------------
# Risk scoring
# ---------------------------------------------------------------------------


def test_risk_score_is_zero_for_no_transformations():
    assert compute_risk_score([]) == 0.0


def test_risk_score_increases_with_breaking_weight():
    single = compute_risk_score([_fake_transformation(0.5)])
    double = compute_risk_score([_fake_transformation(0.5), _fake_transformation(0.5)])
    assert double > single
    assert 0.0 < single < 1.0


def test_risk_score_is_bounded():
    many = compute_risk_score([_fake_transformation(1.0) for _ in range(50)])
    assert many <= 1.0


def test_non_breaking_transformations_do_not_raise_risk():
    item = _fake_transformation(1.0)
    item.breaking = False
    assert compute_risk_score([item]) == 0.0


def _fake_transformation(weight: float):
    """Build a synthetic transformation for scoring tests."""
    from alpha.lineage.semantic_git_delta import SemanticTransformation

    return SemanticTransformation(
        kind=TransformationKind.SYMBOL_REMOVED,
        file_path="a.py",
        symbol="x",
        detail="",
        weight=weight,
    )


@pytest.mark.parametrize(
    "score,expected",
    [(0.0, "none"), (0.1, "low"), (0.3, "moderate"), (0.6, "elevated"), (0.9, "critical")],
)
def test_risk_bands(score, expected):
    assert risk_band(score) == expected


# ---------------------------------------------------------------------------
# Blast radius and recommendations
# ---------------------------------------------------------------------------


def test_blast_radius_finds_call_sites(tmp_path):
    (tmp_path / "lib.py").write_text("def charge(x):\n    return x\n", encoding="utf-8")
    (tmp_path / "app.py").write_text(
        "from lib import charge\n\n\ndef run():\n    return charge(1)\n", encoding="utf-8"
    )
    analyzer = _analyzer(tmp_path)
    report = analyzer.analyze_sources(
        {"lib.py": "def charge(x):\n    return x\n"},
        {"lib.py": "def other():\n    return 0\n"},
    )
    assert report.blast_radius
    assert any(entry.file_path.endswith("app.py") for entry in report.blast_radius)


def test_callers_of_returns_entries_for_known_symbol(tmp_path):
    (tmp_path / "lib.py").write_text("def charge(x):\n    return x\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("import lib\n\n\ndef run():\n    return lib.charge(1)\n", encoding="utf-8")
    entries = _analyzer(tmp_path)._callers_of("charge")
    assert any(entry.file_path.endswith("app.py") for entry in entries)


def test_recommendations_reference_affected_modules(tmp_path):
    (tmp_path / "lib.py").write_text("def charge(x):\n    return x\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("import lib\n\n\ndef run():\n    return lib.charge(1)\n", encoding="utf-8")
    report = _analyzer(tmp_path).analyze_sources(
        {"lib.py": "def charge(x):\n    return x\n"},
        {"lib.py": "def charge(x, fee):\n    return x + fee\n"},
    )
    assert report.recommendations
    assert any("Supply 'fee'" in item for item in report.recommendations)


def test_recommendations_for_rename(tmp_path):
    report = _analyzer(tmp_path).analyze_sources(
        {"svc.py": "def charge(x):\n    return x\n"},
        {"svc.py": "def bill(x):\n    return x\n"},
    )
    assert any("Rename call sites of 'charge'" in item for item in report.recommendations)


# ---------------------------------------------------------------------------
# Repository mode
# ---------------------------------------------------------------------------


def _git_repo(tmp_path):
    """Initialise a git repository with one committed file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "lib.py").write_text("def charge(x):\n    return x\n", encoding="utf-8")
    for args in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "config", "user.name", "Test"],
        ["git", "add", "lib.py"],
        ["git", "commit", "-q", "-m", "initial"],
    ):
        subprocess.run(args, cwd=repo, check=True)
    return repo


def test_analyze_reads_revision_range(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / "lib.py").write_text(
        "def charge(x, fee):\n    return x + fee\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", "lib.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add fee"], cwd=repo, check=True)

    report = _analyzer(repo).analyze(base_ref="HEAD~1", head_ref="HEAD")
    assert report.error == ""
    assert report.files_changed >= 1
    assert TransformationKind.PARAMETER_ADDED.value in _kinds(report)


def test_analyze_reports_error_for_missing_repository(tmp_path):
    report = _analyzer(tmp_path / "absent").analyze(base_ref="HEAD~1", head_ref="HEAD")
    assert report.error
    assert report.transformations == []


def test_analyze_returns_empty_report_for_identical_refs(tmp_path):
    repo = _git_repo(tmp_path)
    report = _analyzer(repo).analyze(base_ref="HEAD", head_ref="HEAD")
    assert report.transformations == []


def test_report_serialization_is_json_safe(tmp_path):
    report = _analyzer(tmp_path).analyze_sources(
        {"svc.py": "def a():\n    return 1\n"}, {"svc.py": "def b():\n    return 2\n"}
    )
    payload = report.to_dict()
    assert payload["transformation_count"] >= 1
    assert isinstance(payload["recommendations"], list)


# ---------------------------------------------------------------------------
# Tool surface
# ---------------------------------------------------------------------------


def test_tool_sources_action():
    import json

    result = analyze_semantic_git_delta.invoke(
        {
            "action": "sources",
            "before_sources_json": json.dumps({"svc.py": "def charge(x):\n    return x\n"}),
            "after_sources_json": json.dumps({"svc.py": "def bill(x):\n    return x\n"}),
        }
    )
    assert result["success"] is True
    assert result["risk_score"] > 0.0
    assert any(item["kind"] == "symbol_renamed" for item in result["transformations"])


def test_tool_repo_action(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / "lib.py").write_text("def charge(x, fee):\n    return x + fee\n", encoding="utf-8")
    subprocess.run(["git", "add", "lib.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add fee"], cwd=repo, check=True)

    result = analyze_semantic_git_delta.invoke(
        {"action": "repo", "repo_path": str(repo), "base_ref": "HEAD~1", "head_ref": "HEAD"}
    )
    assert result["success"] is True
    assert result["files_changed"] >= 1


def test_tool_rejects_invalid_json():
    result = analyze_semantic_git_delta.invoke(
        {"action": "sources", "before_sources_json": "{oops", "after_sources_json": "{}"}
    )
    assert result["success"] is False


def test_tool_rejects_unknown_action():
    result = analyze_semantic_git_delta.invoke({"action": "explode"})
    assert result["success"] is False
