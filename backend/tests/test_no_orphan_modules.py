"""Orphan-module guard: no module may exist without a reference or a manifest note.

Re-runs the AST reference scan (absolute/relative imports + dotted-string
loaders + config ``use:`` paths). A module that nothing references — and that is
not explicitly allowlisted below — fails the build. This is the permanent fix
for integration drift introduced by automated changes.
"""

from __future__ import annotations

import ast
import re
from functools import lru_cache
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
HARNESS = BACKEND / "packages" / "harness"
ALPHA = HARNESS / "alpha"
APP = BACKEND / "app"

SKIP_PARTS = {".venv", "node_modules", "__pycache__", ".agent-workspace", ".git", "htmlcov", ".ruff_cache", "logs"}

# Modules that are unreachable on purpose. Every entry needs a reason that a
# reviewer can verify; entries without reasons fail this test.
ALLOWED_ORPHANS: dict[str, str] = {
    # Namespace-dir resolution shim (see the file docstring + guard test).
    "app.gateway.services.system_monitor_service": "intentional re-export shim over app.gateway.system_monitor_service",
    # Backwards-compatibility shim: re-exports the enclave implementation
    # (enclave_security_tool), which IS in BUILTIN_TOOLS. Keeps the historical
    # import path alive; see the module docstring.
    "alpha.tools.builtins.astra_security_tool": "intentional re-export shim over alpha.tools.builtins.enclave_security_tool",
    # WP-B2 candidate factory (committed 950716a): deliberately standalone per
    # plan §3 — its consumer (RSI cycle factory/engine wiring) lands with RSI
    # Wave 3, so nothing references it yet by design, not by neglect.
    "alpha.rsi.generator": "RSI WP-B2 factory; wired into the cycle by RSI Wave 3 (plan §3 WP-B2)",
    # dynamic-workflow perceive→assemble bridge: part of the dynamic_* layer
    # HELD uncommitted pending P0 remediation (DY-R1/R2/R3) and the P1
    # orchestrator wiring; unwired by audit verdict, not abandoned.
    "alpha.workflow.dynamic_bridge": "held dynamic_* layer; wiring lands with Dynamic Workflow P1 after P0 remediation",
    # Honest import-ready seams: their consumers are deferred by design (unit
    # rules forbid wiring in the same unit as authoring), not abandoned.
    "alpha.skills.creation_nudge": "Hermes S1 skill-review cadence seam; wired into the turn finalizer by a follow-up wiring unit",
    "alpha.memory.persistence_nudge": "Hermes M2 persistence cadence seam; wired into the turn finalizer by a follow-up wiring unit",
    "alpha.evidence.skill_usage_evidence": "Hermes E1 usage-evidence builder; consumed by the skill promotion flow in a follow-up wiring unit",
    # RSI Wave-3 C2b human review gate: consumed by C2c's promotion.py, the
    # wave's serialization point (C2c is queued after C1/C2a/C2b).
    "alpha.rsi.review": "RSI Wave-3 C2b review gate; wired by C2c promotion.py at the wave serialization point",
    # RSI Wave-3 C2a evidence bundle: provenance store read/written by C2c's
    # promotion.decide() composition at the same serialization point.
    "alpha.rsi.evidence_bundle": "RSI Wave-3 C2a evidence bundle; wired by C2c promotion.py at the wave serialization point",
    # Honesty-audit wave-2 deletions (F8/F9): the ONLY production consumers of
    # these engines were two unreachable fake tools — run_mutation_testing_audit
    # (identity-lambda "test runner") and run_speculative_synthesis_tournament
    # (string-length-as-test-results bake-off) — deleted from
    # code_agentic_core.py because they were unregistered and fabricated
    # results. The engines themselves are honest, test-covered libraries
    # (tests/test_mutation_fuzzer.py, tests/test_speculative_tournament.py);
    # production wiring lands only with a genuine test-runner seam.
    "alpha.testing.mutation_fuzzer": "audit F8: honest mutation engine; its only consumer was the deleted fake run_mutation_testing_audit tool — dormant until a real test-runner seam wires it",
    "alpha.synthesis.speculative_tournament": "audit F9: honest speculative-synthesis engine; its only consumer was the deleted fabricated run_speculative_synthesis_tournament tool — dormant until wired honestly",
}

# Standalone ``python -m <module>`` entry points. Nothing imports these by
# design — the interpreter is the caller. Verified by
# ``test_cli_entry_points_are_main_modules``.
CLI_ENTRY_POINTS: dict[str, str] = {
    "alpha.tui.__main__": "CLI entry point: `python -m alpha.tui` launches the workbench",
    "alpha.runtime.sentinel.__main__": "CLI entry point: `python -m alpha.runtime.sentinel` runs the sentinel CLI",
}

# Escape hatch for modules whose ONLY importer is the test suite. Optional
# subsystems belong in alpha.capabilities.catalog instead — that gives them a
# real production reference and makes them loadable.
TEST_ONLY_MODULES: dict[str, str] = {
    # Intentionally empty. Every optional subsystem that used to live here is
    # now declared in alpha.capabilities.catalog, so production code references
    # it and this waiver is no longer needed.
    #
    # The list is self-correcting: ``test_test_only_modules_stay_test_only``
    # fails if an entry ever gets wired into production (delete the stale entry
    # then), and ``test_test_only_modules_are_imported_by_tests`` fails if an
    # entry rots. Do not add to it without a reason a reviewer can verify.
}

SCAN_CODE_BASES = [BACKEND / "packages", BACKEND / "app"]

# Config/manifest files that load modules dynamically. ``config.example.yaml``
# and ``extensions_config.example.json`` are the shipped templates: a ``use:``
# entry there documents a real, user-selectable loader path, so it counts as a
# reference. ``langgraph.json`` is the LangGraph Server manifest and points at
# modules by *file* path (``./app/gateway/langgraph_auth.py:auth``).
SCAN_TEXT_FILES = [
    ROOT / "config.yaml",
    ROOT / "config.example.yaml",
    ROOT / "extensions_config.json",
    ROOT / "extensions_config.example.json",
    BACKEND / "langgraph.json",
]

# NOTE: every group here must be NON-capturing. ``re.findall`` returns only the
# captured groups, so a capturing prefix group silently truncates every match to
# ``"alpha"``/``"app"`` and the whole scan finds nothing. See
# ``test_dotted_string_references_are_indexed`` for the regression guard.
_DOTTED_TARGET = re.compile(r"\b(?:alpha|app|agent_workspace)(?:\.[A-Za-z_]\w*)+")
# File-path manifests (langgraph.json) address modules with slashes:
# ``./app/gateway/langgraph_auth.py:auth``.
_PATH_TARGET = re.compile(r"\b(?:alpha|app|agent_workspace)(?:/[A-Za-z_]\w*)+")
_QUOTED_TARGET = re.compile(r"[\"']([A-Za-z_][\w]*(?:\.[A-Za-z_]\w*)+)[\"']")


def _skip(path: Path) -> bool:
    return any(part in SKIP_PARTS for part in path.parts)


def _module_name(path: Path, root: Path) -> str | None:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return None
    if rel.suffix != ".py":
        return None
    parts = list(rel.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) or None


@lru_cache(maxsize=1)
def _referenced_modules() -> set[str]:
    referenced: set[str] = set()
    files: list[Path] = []
    for base in SCAN_CODE_BASES:
        if base.exists():
            files.extend(p for p in base.rglob("*.py") if not _skip(p))
    files.extend(p for p in SCAN_TEXT_FILES if p.exists())

    for path in files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        if path.suffix == ".py":
            try:
                tree = ast.parse(text)
            except SyntaxError:
                tree = None
            if tree is not None:
                referenced.update(_walk_tree(tree))
        # Dotted-path strings cover config-driven dynamic loading. A target may
        # carry a ``:Class`` suffix (``use: alpha.x.y:Klass``) or a ``.py``
        # suffix (langgraph.json file paths); normalise both back to a module.
        for pattern in (_QUOTED_TARGET, _DOTTED_TARGET, _PATH_TARGET):
            for match in pattern.findall(text):
                referenced.update(_target_modules(match))
    return referenced


def _normalize_target(raw: str) -> str:
    """Reduce a loader target to the module path it names.

    Handles the three shapes that occur in this repo:

    * dotted + class — ``alpha.models.failover:ModelFailoverChain``
    * dotted only    — ``alpha.models.failover``
    * file path      — ``./app/gateway/langgraph_auth.py:auth`` (langgraph.json)
    """
    target = raw.strip().strip("\"'")
    if ":" in target:
        target = target.split(":", 1)[0]
    if target.startswith("./"):
        target = target[2:]
    target = target.replace("/", ".")
    if target.endswith(".py"):
        target = target[:-3]
    return target


def _target_modules(raw: str) -> set[str]:
    """Normalise one dotted loader target into the module path(s) it names."""
    return {raw, _normalize_target(raw)}


def _walk_tree(tree: ast.AST) -> set[str]:
    """Yield every module path referenced by this file's import statements."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            target = node.module or ""
            if target:
                names.add(target)
                for alias in node.names:
                    if alias.name != "*":
                        names.add(f"{target}.{alias.name}")
            else:
                # ``from . import sibling`` / ``from .. import name``: the name is
                # the module (or package+name) — record it as a short reference.
                for alias in node.names:
                    if alias.name != "*":
                        names.add(alias.name)
    return names


def _is_referenced(referenced: set[str], module: str) -> bool:
    """Decide whether ``module`` appears in ``referenced`` by any evidence channel.

    Deviates deliberately from a strict equality rule: plain ``from X import Y``
    lines only mention full dotted paths, so sibling references like
    ``from .x_tool import foo`` are reconstructed by testing three independent
    evidence channels: the full dotted module path, a parent+basename compound,
    and a basename import inside one of the module's own ancestor packages.
    """
    if module in referenced:
        return True
    parts = module.split(".")
    short = parts[-1]
    # Evidence channel 1: dotted strings and absolute imports can name the
    # full lower package + basename, e.g. "builtins.x_tool".
    for level in range(1, len(parts)):
        if ".".join(parts[level:]) in referenced:
            return True
    # Evidence channel 2: ``from .x import y`` inside an ancestor package is
    # recorded as the bare basename; accept it when any ancestor package is
    # itself a referenced package.
    if short in referenced:
        for level in range(1, len(parts)):
            if ".".join(parts[:level]) in referenced:
                return True
    return False


@lru_cache(maxsize=1)
def _test_referenced_modules() -> set[str]:
    """Modules referenced from the test tree only (used to validate the list)."""
    referenced: set[str] = set()
    base = BACKEND / "tests"
    for path in base.rglob("*.py"):
        if _skip(path):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            tree = None
        if tree is not None:
            referenced.update(_walk_tree(tree))
        for pattern in (_QUOTED_TARGET, _DOTTED_TARGET, _PATH_TARGET):
            for match in pattern.findall(text):
                referenced.update(_target_modules(match))
    return referenced


def test_no_orphan_modules() -> None:
    """Every module must be import-referenced, loader-referenced, or allowlisted.

    Deviates deliberately from a strict equality rule: plain ``from X import Y``
    lines only mention full dotted paths, so sibling references like
    ``from .x_tool import foo`` are reconstructed by testing three independent
    evidence channels: the full dotted module path, a parent+basename compound,
    and a basename import inside one of the module's own ancestor packages.
    """
    referenced = _referenced_modules()

    orphans: list[str] = []
    for root, base in ((ALPHA, HARNESS), (APP, BACKEND)):
        for path in root.rglob("*.py"):
            if _skip(path) or path.name == "__init__.py":
                continue
            module = _module_name(path, base)
            if not module:
                continue
            # Alembic revision scripts are loaded dynamically by the chain.
            if "persistence.migrations" in module:
                continue
            if _is_referenced(referenced, module):
                continue
            if module in ALLOWED_ORPHANS or module in CLI_ENTRY_POINTS or module in TEST_ONLY_MODULES:
                continue
            orphans.append(module)
    assert not orphans, (
        "modules exist but nothing references them — wire them into the system, "
        f"delete them, or add an ALLOWED_ORPHANS entry with a reason: {sorted(orphans)}"
    )


def test_allowed_orphans_have_reasons() -> None:
    for name, table in (
        ("ALLOWED_ORPHANS", ALLOWED_ORPHANS),
        ("CLI_ENTRY_POINTS", CLI_ENTRY_POINTS),
        ("TEST_ONLY_MODULES", TEST_ONLY_MODULES),
    ):
        for module, reason in table.items():
            assert module and reason, f"{name} entry needs both module and reason: {module!r}"


def test_cli_entry_points_are_main_modules() -> None:
    """A CLI entry point must actually be runnable as ``python -m <pkg>``.

    Guards against the list quietly accumulating ordinary modules that someone
    could not be bothered to wire up.
    """
    for module in CLI_ENTRY_POINTS:
        assert module.split(".")[-1] == "__main__", (
            f"{module} is listed as a CLI entry point but is not a __main__ module"
        )
        path = ALPHA / f"{module[len('alpha.') :].replace('.', '/')}.py"
        assert path.exists(), f"{module} is listed as a CLI entry point but {path} does not exist"


def test_test_only_modules_are_imported_by_tests() -> None:
    """Every TEST_ONLY_MODULES entry must really be imported by the test suite.

    If a module stops being tested (or is renamed), this fails instead of the
    entry silently rotting into a permanent hole in the guard.
    """
    test_referenced = _test_referenced_modules()
    for module in TEST_ONLY_MODULES:
        assert _is_referenced(test_referenced, module), (
            f"{module} is listed as test-only but no test imports it — the entry "
            "is stale, so remove it or wire the module up"
        )


def test_test_only_modules_stay_test_only() -> None:
    """Entries must not secretly become production-wired.

    The moment a listed module is imported by real code it is no longer
    test-only, and this list must shrink. Without this check the allowlist
    would drift into a blanket waiver.
    """
    production_referenced = _referenced_modules()
    stale = sorted(m for m in TEST_ONLY_MODULES if _is_referenced(production_referenced, m))
    assert not stale, (
        f"these modules are now referenced by production code, so they are no longer "
        f"test-only — remove them from TEST_ONLY_MODULES: {stale}"
    )


def test_dotted_string_references_are_indexed() -> None:
    """Regression guard for the ``re.findall`` capturing-group bug.

    ``re.findall`` returns only *captured groups*, not the whole match. A
    capturing prefix group therefore reduced every dotted loader target
    (``"app.channels.buzz:BuzzChannel"``) to the bare token ``"app"``, which
    made the orphan scan blind to all config-driven wiring and flagged 48 live
    modules as orphans. These are real targets in the repo; if this test fails,
    the scan regexes silently stopped matching again.
    """
    referenced = _referenced_modules()

    # (module the file defines, the literal target string that names it)
    expected = [
        ("app.channels.buzz", "app.channels.buzz:BuzzChannel"),
        ("app.channels.feishu", "app.channels.feishu:FeishuChannel"),
        ("app.channels.wecom", "app.channels.wecom:WeComChannel"),
        ("app.gateway.langgraph_auth", "./app/gateway/langgraph_auth.py:auth"),
        ("app.gateway.langgraph_studio", "./app/gateway/langgraph_studio.py:langgraph_app"),
    ]
    for module, target in expected:
        assert _normalize_target(target) in referenced, (
            f"dotted loader target {target!r} was not indexed — the scan regexes "
            f"are broken again, so {module} would be reported as an orphan"
        )
