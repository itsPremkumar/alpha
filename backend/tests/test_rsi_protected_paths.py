"""Tests for the machine-readable protected-path policy (RSI WP-B3, feature #14).

Pins, per plan section 3 WP-B3:

- ``.env``, ``.jwt_secret``, ``backend/tests/test_x.py``,
  ``contracts/feature_manifest.json`` -> ``deny`` -> ``PermissionError``;
- ``.github/workflows/release.yml`` -> ``review_required`` refuses without
  review, allowed only with an explicit ``reviewed=True`` (human/HITL
  channel — never self-granted inside the candidate loop);
- unknown path -> ``review_required`` (cautious default, never silent-allow);
- branch names ``main``/``master`` -> ``deny``;
- honesty/hygiene: ``classify()`` outputs contain only patterns (never file
  contents) and the module source itself carries no secrets and loads its
  rules from code constants only (never from JSON/file).
"""

import ast
import json
import re
from pathlib import Path

import pytest

from alpha.rsi.protected_paths import (
    PROTECTED_RULES,
    ProtectedRule,
    assert_candidate_path_allowed,
    classify,
    redacted_paths,
)

MODULE_PATH = Path(__file__).resolve().parents[1] / "packages" / "harness" / "alpha" / "rsi" / "protected_paths.py"

# Every input lives under a tmp_path .env file so nothing can leak outside tmp.
SECRET_MARKER = "SECRET_MARKER_c3f91a7d_CONTENT_NEVER_DISCLOSED"


@pytest.fixture(autouse=True)
def isolated_workspace(tmp_path, monkeypatch):
    """Keep runtime_home() inside a temp dir (the env does not isolate it)."""
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))


def _write_secret_file(tmp_path: Path, relative: str) -> Path:
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"API_TOKEN={SECRET_MARKER}\n", encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# Rule table shape (code constants, never file-loaded)
# ---------------------------------------------------------------------------


def test_rules_are_code_constants_and_well_formed():
    assert isinstance(PROTECTED_RULES, tuple)
    assert PROTECTED_RULES, "policy must never be empty"
    assert all(isinstance(rule, ProtectedRule) for rule in PROTECTED_RULES)
    assert all(rule.mode in ("deny", "review_required") for rule in PROTECTED_RULES)
    patterns = [rule.pattern for rule in PROTECTED_RULES]
    assert len(patterns) == len(set(patterns)), "duplicate patterns make matched_pattern ambiguous"
    # The spec section 15 surfaces named in WP-B3 are present in code.
    for required in (
        ".env*",
        "**/.env*",
        "**/.jwt_secret",
        "**/credentials*",
        ".github/workflows/**",
        "**/contracts/feature_manifest.json",
        "backend/tests/**",
        "backend/packages/harness/alpha/benchmarks/**",
        "alpha/reproduction/gates.py",
        "alpha/policy/**",
        "backend/app/gateway/auth/**",
        "references/**",
        "main",
        "master",
    ):
        assert required in patterns, f"missing protected rule: {required}"


def test_module_source_loads_rules_from_code_never_from_a_file():
    source = MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(MODULE_PATH))
    calls = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            calls.add(func.id)
        elif isinstance(func, ast.Attribute):
            calls.add(func.attr)
    # No file/config read can feed the policy (spec section 15: a candidate
    # must never be able to rewrite its own protected-path rules).
    assert calls.isdisjoint({"open", "read_text", "read_bytes", "load", "loads", "safe_load"})
    # PROTECTED_RULES is a literal tuple of ProtectedRule(...) calls in source.
    named_assignments = []
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            named_assignments.append((node.target.id, node))
        elif isinstance(node, ast.Assign):
            named_assignments.extend((t.id, node) for t in node.targets if isinstance(t, ast.Name))
    assign = next(node for name, node in named_assignments if name == "PROTECTED_RULES")
    assert isinstance(assign.value, ast.Tuple)
    # Each element is a literal rule-constructor call (never a variable read
    # or anything derived from external data).
    assert all(
        isinstance(item, ast.Call) and getattr(item.func, "id", None) in {"ProtectedRule", "_rule"}
        for item in assign.value.elts
    )


def test_policy_cannot_be_disabled_by_any_candidate_supplied_input(monkeypatch):
    # No flag/env clears the rules: even a hostile environment keeps them.
    monkeypatch.setenv("RSI_PROTECTED_PATHS_DISABLED", "1")
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(Path.cwd()))
    assert PROTECTED_RULES
    assert classify(".env")[0] == "deny"


# ---------------------------------------------------------------------------
# Mandatory deny pins
# ---------------------------------------------------------------------------


def test_denied_surfaces_classify_deny_and_raise_permission_error(tmp_path):
    cases = [".env", ".jwt_secret", "backend/tests/test_x.py", "contracts/feature_manifest.json"]
    for path in cases:
        mode, pattern = classify(path)
        assert mode == "deny", f"{path} must be denied, got {mode!r}"
        assert pattern, f"deny for {path} must carry a matched pattern"
        with pytest.raises(PermissionError):
            assert_candidate_path_allowed(path)


def test_deny_is_fail_closed_even_with_reviewed_true():
    # Review can never override a denied surface (fail-closed, plan WP-B3).
    for path in (".env", "backend/tests/test_x.py"):
        with pytest.raises(PermissionError):
            assert_candidate_path_allowed(path, reviewed=True)


def test_additional_spec_15_surfaces_are_denied():
    for path in (
        "backend/packages/harness/alpha/benchmarks/release_gate.py",
        "alpha/reproduction/gates.py",
        "alpha/policy/engine.py",
        "backend/app/gateway/auth/dependencies.py",
        "references/ALPHA_RSI_IMPLEMENTATION_PLAN.md",
        "config/credentials_prod.json",
        "backend/.env.production",
    ):
        mode, pattern = classify(path)
        assert mode == "deny", f"{path} must be denied, got {mode!r}"
        assert pattern, f"deny for {path} must carry a matched pattern"
        with pytest.raises(PermissionError):
            assert_candidate_path_allowed(path)


def test_absolute_workspace_paths_resolve_to_repo_relative_tail(tmp_path):
    absolute = tmp_path / "alpha" / "backend" / "tests" / "test_x.py"
    mode, pattern = classify(absolute)
    assert mode == "deny"
    assert pattern == "backend/tests/**"


# ---------------------------------------------------------------------------
# Review-required pin + human-only review channel
# ---------------------------------------------------------------------------


def test_workflows_require_review_refuses_by_default_allows_explicit_human_review():
    path = ".github/workflows/release.yml"
    mode, pattern = classify(path)
    assert mode == "review_required"
    assert pattern == ".github/workflows/**"
    # Default call refuses — a default call can never carry a human review.
    with pytest.raises(PermissionError):
        assert_candidate_path_allowed(path)
    # Explicit reviewed=True (caller's human/HITL channel) allows.
    assert_candidate_path_allowed(path, reviewed=True)


def test_review_is_never_self_granted_inside_the_candidate_loop(tmp_path):
    # A candidate writing a "reviewed" marker into its own workspace cannot
    # flip the verdict: only the caller passing reviewed=True can.
    marker = _write_secret_file(tmp_path, ".github/workflows/approved_by_candidate")
    with pytest.raises(PermissionError):
        assert_candidate_path_allowed(marker)
    with pytest.raises(PermissionError):
        assert_candidate_path_allowed(marker, reviewed=False)


def test_unknown_path_defaults_to_review_required_never_silent_allow():
    for path in ("some/unknown/file.txt", "README.md", "", "   "):
        mode, pattern = classify(path)
        assert (mode, pattern) == ("review_required", ""), f"unknown path {path!r} must review, got {(mode, pattern)!r}"
        with pytest.raises(PermissionError):
            assert_candidate_path_allowed(path)
        # …and is allowed only via the explicit human channel.
        assert_candidate_path_allowed(path, reviewed=True)


# ---------------------------------------------------------------------------
# Branch names
# ---------------------------------------------------------------------------


def test_branch_names_main_and_master_are_denied():
    for branch in ("main", "master", "./main", r"C:\repo\.git\refs\heads\master"):
        mode, pattern = classify(branch)
        assert mode == "deny", f"branch {branch!r} must be denied"
        assert pattern in ("main", "master")
        with pytest.raises(PermissionError):
            assert_candidate_path_allowed(branch)
        with pytest.raises(PermissionError):
            assert_candidate_path_allowed(branch, reviewed=True)


def test_branch_rule_does_not_over_match_ordinary_paths():
    # main/master are exact tail segments (branch names), not substrings.
    assert classify("backend/app/main.py")[0] == "review_required"  # unknown -> cautious review
    assert classify("docs/master.md")[0] == "review_required"


# ---------------------------------------------------------------------------
# Honesty/hygiene pins
# ---------------------------------------------------------------------------


def test_classify_outputs_contain_only_patterns_never_file_contents(tmp_path):
    secret_file = _write_secret_file(tmp_path, ".env")
    mode, pattern = classify(secret_file)
    assert mode == "deny"
    assert pattern == ".env*"
    # Returned strings are code-constant patterns — not derived from the file.
    assert SECRET_MARKER not in mode
    assert SECRET_MARKER not in pattern
    allowed = redacted_paths([secret_file])
    assert allowed == [".env*"]
    assert SECRET_MARKER not in allowed[0]
    # Disclosed as the pattern, never as the body.
    assert "API_TOKEN" not in allowed[0]


def test_redacted_paths_returns_one_pattern_per_path_never_contents(tmp_path):
    secret_env = _write_secret_file(tmp_path, ".env")
    secret_env_nested = _write_secret_file(tmp_path, "nested/dir/.env.local")
    paths: list[str | Path] = [secret_env, secret_env_nested, "backend/tests/test_x.py", "some/unknown/file.txt"]
    disclosed = redacted_paths(paths)
    assert len(disclosed) == len(paths)
    assert disclosed == [".env*", ".env*", "backend/tests/**", ""]
    joined = json.dumps(disclosed)
    assert SECRET_MARKER not in joined
    assert "API_TOKEN" not in joined
    assert all(isinstance(item, str) for item in disclosed)


def test_module_source_contains_no_secrets_static_scan():
    source = MODULE_PATH.read_text(encoding="utf-8")
    forbidden = [
        r"AKIA[0-9A-Z]{16}",
        r"ghp_[A-Za-z0-9]{36}",
        r"gho_[A-Za-z0-9]{36}",
        r"xox[baprs]-[A-Za-z0-9-]{10,}",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        r"(?i)\b(api[_-]?key|passwd|password|secret[_-]?key)\s*[:=]\s*[\"'][^\"'\s]{8,}[\"']",
        r"\b[0-9a-f]{48,}\b",
    ]
    for pattern in forbidden:
        assert re.search(pattern, source) is None, f"possible secret matched {pattern!r} in protected_paths.py"
    # No environment variable in this module can alter or disable the policy.
    tree = ast.parse(source, filename=str(MODULE_PATH))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    env_calls = [
        node
        for node in calls
        if isinstance(node.func, ast.Attribute) and node.func.attr in {"getenv", "environ.get"}
    ]
    assert not env_calls, "protected-path policy must not read environment variables"
