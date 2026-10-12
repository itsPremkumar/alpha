"""Orphan-module guard: no module may exist without a reference or a manifest note.

Re-runs the AST reference scan (absolute/relative imports + dotted-string
loaders + config ``use:`` paths). A module that nothing references — and that is
not explicitly allowlisted below — fails the build. This is the permanent fix
for integration drift introduced by automated changes.
"""

from __future__ import annotations

import ast
import re
import subprocess
from functools import lru_cache
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
HARNESS = BACKEND / "packages" / "harness"
ALPHA = HARNESS / "alpha"
APP = BACKEND / "app"

SKIP_PARTS = {".venv", "node_modules", "__pycache__", ".alpha", ".git", "htmlcov", ".ruff_cache", "logs"}

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
    "alpha.skills.creation_nudge": "S1 skill-review cadence seam; wired into the turn finalizer by a follow-up wiring unit",
    "alpha.memory.persistence_nudge": "M2 persistence cadence seam; wired into the turn finalizer by a follow-up wiring unit",
    "alpha.evidence.skill_usage_evidence": "E1 usage-evidence builder; consumed by the skill promotion flow in a follow-up wiring unit",
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
    # Memory capture (write side) is the symmetric twin of the already-shipped
    # read side: recall_composition is referenced only from here, so neither
    # half has a production caller yet. Both land together with the
    # per-memory-type registration wave, which is why half a pair is correct.
    "alpha.memory.capture_composition": "write-side twin of alpha.memory.recall_composition; the pair is wired together by the per-memory-type registration wave, so neither half is referenced until then",
    # Ambient writer binding is the injection-free instrumentation seam for
    # the injected BehaviourTraceWriter. Its named consumers (alpha.tools.selection
    # rank_candidates, alpha.runtime.escalation HandoffLedger.append) cannot
    # take a writer parameter without changing every caller, which is why the
    # seam exists; those call sites adopt it when they next change.
    "alpha.observability.ambient": "injection-free instrumentation seam for the injected BehaviourTraceWriter; its consumers adopt it when they next change rather than taking a writer parameter",
    # Stream-JSON output mode. The emitter owns the TERMINAL_FIELDS contract
    # and audit_frame(), which is the drift guard for the mode; nothing
    # selects the mode yet, so it is registered but not reachable from a run.
    "alpha.streamjson.emitter": "stream-JSON output mode emitter owning the TERMINAL_FIELDS/audit_frame drift guard; no run selects the mode yet, so it is registered but not yet reachable",
    # Strict lock-policy decision seam (commit dd211d1). Its consumer is the
    # write-claiming path (groups/write_watch.py / ReadBeforeWriteMiddleware),
    # which must adopt the policy question; wiring it changes write-path
    # behavior under lock_policy="strict", so it ships as a deliberate,
    # separately-tested increment, not inside an unrelated change. Default
    # policy is advisory, which is today's behaviour, so nothing regresses.
    "alpha.groups.enforcement": "strict lock-policy decision seam; the write-claiming path adopts it as a separate increment because wiring changes write-path behaviour under lock_policy=strict, while advisory is today's behaviour",
    # Code-mode gate and availability probe (commit 1dde8eb) for the isolated
    # code bridge, which is NOT built on this host by design (no OS-level
    # permission model; a curated __builtins__ is not a sandbox — see the
    # module docstring). The module IS the tested fail-closed boundary; its
    # production consumer is the bridge executor that a genuine isolated
    # runtime would provide.
    "alpha.tools.discovery.code_mode": "code-mode gate for the not-built isolated bridge; the module is the tested fail-closed boundary and its consumer is the bridge executor a real isolated runtime would provide",
    # Standalone diagnostic scripts (reworked from the hard-coded-path scratch
    # they were committed as in 66e4cbe). Nothing in production imports them by
    # design — a probe that production imports would run at import time — and
    # each is runnable via `python -m <module>` / direct path with paths
    # derived from __file__. They stay exercised by
    # tests/test_diagnostic_scripts.py, which pins that each still imports
    # silently, runs against this checkout, and answers its question.
    "alpha.orchestrator._oracle_probe": "standalone drift probe for orchestrator/replay.py (imported-vs-working-tree source check); run by hand or tests/test_diagnostic_scripts.py, never imported by production code",
    "alpha.workflow._probe_replay_fold": "standalone replay probe: does replay_run fold a connectivity_wait_parked park?; run by hand or tests/test_diagnostic_scripts.py, never imported by production code",
    "alpha.workflow._proof_external_wait": "standalone replay proof: external_wait_parked folds to WAITING_EVENT (exit 0/1); run by hand or tests/test_diagnostic_scripts.py, never imported by production code",
    "app.gateway.routers.route_audit_builder": "standalone AST route inventory (JSON) over app/gateway/routers — never imports the Gateway; run by hand or tests/test_diagnostic_scripts.py, never imported by production code",
}

# Standalone ``python -m <module>`` entry points. Nothing imports these by
# design — the interpreter is the caller. Verified by
# ``test_cli_entry_points_are_main_modules``.
CLI_ENTRY_POINTS: dict[str, str] = {
    "alpha.tui.__main__": "CLI entry point: `python -m alpha.tui` launches the workbench",
    "alpha.runtime.sentinel.__main__": "CLI entry point: `python -m alpha.runtime.sentinel` runs the sentinel CLI",
    "alpha.safety.authority.__main__": "CLI entry point: `python -m alpha.safety.authority` runs the static, read-only authority census (scan/diff/baseline)",
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
_DOTTED_TARGET = re.compile(r"\b(?:alpha|app|alpha)(?:\.[A-Za-z_]\w*)+")
# File-path manifests (langgraph.json) address modules with slashes:
# ``./app/gateway/langgraph_auth.py:auth``.
_PATH_TARGET = re.compile(r"\b(?:alpha|app|alpha)(?:/[A-Za-z_]\w*)+")
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


@lru_cache(maxsize=4)
def _git_ignored(rel_paths: tuple[str, ...]) -> frozenset[str]:
    """Return which repo-relative POSIX paths git would refuse to track.

    This guard's whole claim is about *the repository*. A fresh clone contains
    only what git tracks, so a module that git ignores can never be shipped and
    CI can never see it — yet scanning the raw filesystem made the local verdict
    disagree with CI's. Twenty ad-hoc ``scan_*.py`` scripts hidden by
    ``.gitignore`` (``/backend/app/gateway/routers/scan_*.py`` and friends) tripped
    this test on every local ``make test`` while a fresh clone stayed green:
    hiding a file from git does not hide it from ``Path.rglob``.

    One ``git check-ignore --stdin`` call answers the whole batch. The payload is
    written as **bytes, never text**: on Windows, Python's text-mode stdin wraps
    every ``\n`` in ``\r\n``, so git received ``scan_shadow.py\r``, matched no
    pattern, answered "nothing is ignored", and the guard silently reverted to
    the filesystem-blind behaviour it is meant to remove — measured here as rc=1
    with empty output for a path that plainly matches ``scan_*.py``. The paths are
    matched against patterns without needing to exist, so a clean clone gets the
    same answer as a working tree full of scratch.

    Every failure mode returns "nothing is ignored", which falls back to the
    stricter pre-existing behaviour rather than quietly checking nothing: a
    missing git, a non-repo working tree, a path outside the repository
    (``git`` exits 128) or a timeout all leave the guard doing *more* work, never
    less.
    """
    if not rel_paths:
        return frozenset()
    try:
        proc = subprocess.run(
            ["git", "-c", "core.quotepath=false", "check-ignore", "--stdin"],
            input="\n".join(rel_paths).encode("utf-8"),
            capture_output=True,
            cwd=ROOT,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    # 0 = at least one path ignored, 1 = none ignored, 2+ = fatal/usage error.
    if proc.returncode not in (0, 1):
        return frozenset()
    return frozenset(line.decode("utf-8", "replace") for line in proc.stdout.split(b"\n") if line)


def _shippable(paths: list[Path]) -> list[Path]:
    """Drop the paths git would not track, keeping every path git could.

    Both scan entry points funnel through here so the reference index and the
    orphan sweep always see the same tree. Skipping ignored files in the
    *reference* scan matters just as much as skipping them in the orphan sweep:
    a gitignored scratch file importing ``alpha.foo`` would otherwise mark
    ``alpha.foo`` referenced and mask a real orphan that CI does catch.
    """
    in_repo: dict[str, Path] = {}
    keep: list[Path] = []
    for path in paths:
        try:
            in_repo[path.relative_to(ROOT).as_posix()] = path
        except ValueError:
            # Outside the repository git has no opinion; it ships nothing, so
            # leave it under the guard's scrutiny exactly as before.
            keep.append(path)
    ignored = _git_ignored(tuple(sorted(in_repo)))
    keep.extend(p for key, p in in_repo.items() if key not in ignored)
    return keep


@lru_cache(maxsize=1)
def _referenced_modules() -> set[str]:
    referenced: set[str] = set()
    files: list[Path] = []
    for base in SCAN_CODE_BASES:
        if base.exists():
            files.extend(p for p in base.rglob("*.py") if not _skip(p))
    files.extend(p for p in SCAN_TEXT_FILES if p.exists())
    files = _shippable(files)

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
        candidates = _shippable([p for p in root.rglob("*.py") if not _skip(p) and p.name != "__init__.py"])
        for path in candidates:
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
    assert not orphans, f"modules exist but nothing references them — wire them into the system, delete them, or add an ALLOWED_ORPHANS entry with a reason: {sorted(orphans)}"


def test_allowed_orphans_have_reasons() -> None:
    for name, table in (
        ("ALLOWED_ORPHANS", ALLOWED_ORPHANS),
        ("CLI_ENTRY_POINTS", CLI_ENTRY_POINTS),
        ("TEST_ONLY_MODULES", TEST_ONLY_MODULES),
    ):
        for module, reason in table.items():
            assert module and reason, f"{name} entry needs both module and reason: {module!r}"


def test_waived_modules_still_exist() -> None:
    """Every waiver must name a module that is still there.

    A waiver whose module has been renamed or deleted permits nothing -- the orphan
    scan never reaches a file that is not there -- so the entry stops doing any work
    and rots silently, while the real module it was written about is re-reported as an
    orphan under a new name. This is the same freshness rule the inert-declaration
    contract (``contracts/inert_declarations_allowlist.v1.json``) applies to its own
    entries, and it is why that contract can be trusted: neither list grows in one
    direction only.

    Resolution is per import root, because the two trees are not siblings:
    ``app.gateway.x`` lives under ``backend/app`` while ``alpha.x`` lives under
    ``backend/packages/harness/alpha``. Joining the dotted name onto a single root
    would look under the wrong tree and report a live shim as missing.
    """
    roots = {"app": APP, "alpha": HARNESS / "alpha"}
    for name, table in (
        ("ALLOWED_ORPHANS", ALLOWED_ORPHANS),
        ("CLI_ENTRY_POINTS", CLI_ENTRY_POINTS),
        ("TEST_ONLY_MODULES", TEST_ONLY_MODULES),
    ):
        for module in table:
            head, _, rest = module.partition(".")
            assert head in roots, f"{name} entry {module!r} does not live under a scanned import root ({sorted(roots)}), so the scan can never reach it and the waiver permits nothing"
            path = roots[head] / f"{rest.replace('.', '/')}.py"
            assert path.exists(), f"{name} entry {module!r} points at {path}, which does not exist. The waiver is dead weight; delete it (or fix the path) so the list cannot rot into a blanket waiver."


def test_cli_entry_points_are_main_modules() -> None:
    """A CLI entry point must actually be runnable as ``python -m <pkg>``.

    Guards against the list quietly accumulating ordinary modules that someone
    could not be bothered to wire up.
    """
    for module in CLI_ENTRY_POINTS:
        assert module.split(".")[-1] == "__main__", f"{module} is listed as a CLI entry point but is not a __main__ module"
        path = ALPHA / f"{module[len('alpha.') :].replace('.', '/')}.py"
        assert path.exists(), f"{module} is listed as a CLI entry point but {path} does not exist"


def test_test_only_modules_are_imported_by_tests() -> None:
    """Every TEST_ONLY_MODULES entry must really be imported by the test suite.

    If a module stops being tested (or is renamed), this fails instead of the
    entry silently rotting into a permanent hole in the guard.
    """
    test_referenced = _test_referenced_modules()
    for module in TEST_ONLY_MODULES:
        assert _is_referenced(test_referenced, module), f"{module} is listed as test-only but no test imports it — the entry is stale, so remove it or wire the module up"


def test_test_only_modules_stay_test_only() -> None:
    """Entries must not secretly become production-wired.

    The moment a listed module is imported by real code it is no longer
    test-only, and this list must shrink. Without this check the allowlist
    would drift into a blanket waiver.
    """
    production_referenced = _referenced_modules()
    stale = sorted(m for m in TEST_ONLY_MODULES if _is_referenced(production_referenced, m))
    assert not stale, f"these modules are now referenced by production code, so they are no longer test-only — remove them from TEST_ONLY_MODULES: {stale}"


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
        assert _normalize_target(target) in referenced, f"dotted loader target {target!r} was not indexed — the scan regexes are broken again, so {module} would be reported as an orphan"


def test_gitignored_paths_are_excluded_from_the_guard() -> None:
    """A path git would refuse to track is not part of the repository's modules.

    `.gitignore` carries rules such as `/backend/app/gateway/routers/scan_*.py`,
    and twenty ad-hoc scanner scripts were sitting behind them. They exist only
    in a working tree: a fresh clone cannot contain them, so CI cannot fail on
    them, so this guard reporting them made the local verdict a lie. Matching is
    done against the pattern alone, which is why the probe path below does not
    have to exist — a clean clone gets the same answer.
    """
    gitignored = ROOT / "backend" / "app" / "gateway" / "routers" / "scan_absent_for_test.py"
    tracked = Path(__file__).resolve()

    kept = _shippable([gitignored, tracked])

    assert gitignored not in kept, "a gitignored scratch file was still judged as part of the shipped tree"
    assert tracked in kept, "a tracked module must never be dropped by the gitignore filter"


def test_shippable_keeps_paths_git_has_no_opinion_about() -> None:
    """Outside the repository git exits 128; that must not lose the path.

    `git check-ignore` fatals on a path outside the work tree, so asking it
    about one would return "ignored: nothing" for the whole batch. `_shippable`
    therefore never hands git an out-of-repo path — and this pins that a path it
    leaves alone still comes back out.
    """
    outside = ROOT.parent / "definitely_not_in_this_repo" / "orphan.py"
    assert _shippable([outside]) == [outside]


def test_git_lookup_failure_keeps_every_path(monkeypatch) -> None:
    """No git, no repo, or a timeout must fall back to checking EVERYTHING.

    The filter's only safe failure is to do more work than asked: returning an
    empty "nothing is ignored" would silently restore the exact filesystem-blind
    behaviour this fix exists to remove, and a broken checkout would then report
    a clean bill of health it has not earned.
    """
    probe = ROOT / "backend" / "app" / "gateway" / "routers" / "scan_absent_for_test.py"
    _git_ignored.cache_clear()
    try:

        def _boom(*_args, **_kwargs):
            raise OSError("git is not installed")

        monkeypatch.setattr(subprocess, "run", _boom)

        assert _shippable([probe]) == [probe], "a failed git lookup must not drop paths"
    finally:
        # _git_ignored is lru_cached: leaving the OSError result behind would
        # poison every later caller in this session, including the real guard.
        _git_ignored.cache_clear()
