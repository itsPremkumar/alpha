"""Feature-manifest wiring guards (Phase 2 of the unified integration plan).

These tests fail the build when code exists but is not wired at its declared
integration point, or when the manifest goes stale relative to the source.
Regenerate with: cd backend && python scripts/generate_feature_manifest.py
"""

from __future__ import annotations

import ast
import importlib
import json
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
MANIFEST_PATH = ROOT / "contracts" / "feature_manifest.json"


@pytest.fixture(scope="module")
def manifest() -> dict:
    assert MANIFEST_PATH.exists(), "contracts/feature_manifest.json is missing — run: cd backend && python scripts/generate_feature_manifest.py"
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _ids(manifest: dict, section: str) -> set[str]:
    return {entry["id"] for entry in manifest.get(section, [])}


def test_manifest_versions_and_sections(manifest: dict) -> None:
    assert manifest["version"]
    for section in ("tools", "routers", "middlewares", "loops"):
        assert section in manifest and isinstance(manifest[section], list)
    assert manifest["excluded_local_only"], "local-only exclusions must be recorded"


def _registered_tool_symbols(tree: ast.AST) -> set[str]:
    """Tool symbols named by the BUILTIN_TOOLS registry literal in *tree*.

    The registry is resolved lazily (PEP 562), so the literal lives inside
    ``_build_registries`` and is passed straight to ``_deduplicate_tools``; a
    module-level ``BUILTIN_TOOLS = [...]`` assignment is still accepted because it
    describes the same registry. This stays a source-level check on purpose: it
    must not import tools.py, and it must not share code with the generator whose
    output it is checking.
    """
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_build_registries":
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name) and inner.func.id == "_deduplicate_tools" and inner.args and isinstance(inner.args[0], ast.List):
                    return {elt.id for elt in inner.args[0].elts if isinstance(elt, ast.Name)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "BUILTIN_TOOLS" for t in node.targets):
            if isinstance(node.value, ast.List):
                return {elt.id for elt in node.value.elts if isinstance(elt, ast.Name)}
    return set()


def test_every_builtin_tool_is_in_the_manifest(manifest: dict) -> None:
    source = (BACKEND / "packages" / "harness" / "alpha" / "tools" / "tools.py").read_text(encoding="utf-8")
    # Source-level check (no heavy import): every tool symbol registered in code
    # must appear as a manifest entry id.
    registered = _registered_tool_symbols(ast.parse(source))
    assert registered, "BUILTIN_TOOLS registry disappeared from tools.py"
    missing = registered - _ids(manifest, "tools")
    assert not missing, f"tools missing from the manifest (regenerate it): {sorted(missing)}"


def test_every_mounted_router_is_in_the_manifest_and_wired(manifest: dict) -> None:
    import re

    app_py = BACKEND / "app" / "gateway" / "app.py"
    text = app_py.read_text(encoding="utf-8")
    mounted = set(re.findall(r"app\.include_router\(\s*(\w+)\.router", text))
    assert mounted, "router mounting block disappeared from app.py"
    manifest_ids = _ids(manifest, "routers")
    missing = {f"app.gateway.routers.{name}" for name in mounted} - manifest_ids
    assert not missing, f"mounted routers missing from the manifest: {sorted(missing)}"
    wired_ids = {entry["id"] for entry in manifest["routers"] if entry.get("wired")}
    not_wired = {f"app.gateway.routers.{name}" for name in mounted} - wired_ids
    assert not not_wired, f"mounted routers not marked wired: {sorted(not_wired)}"


def test_every_middleware_module_is_in_the_manifest(manifest: dict) -> None:
    mw_dir = BACKEND / "packages" / "harness" / "alpha" / "agents" / "middlewares"
    on_disk = {f"alpha.agents.middlewares.{p.stem}" for p in mw_dir.glob("*_middleware.py")}
    manifest_ids = _ids(manifest, "middlewares")
    missing = on_disk - manifest_ids
    assert not missing, f"middleware modules missing from the manifest: {sorted(missing)}"
    extra = manifest_ids - on_disk
    assert not extra, f"manifest lists middleware modules that no longer exist: {sorted(extra)}"


def test_no_middleware_is_left_unwired(manifest: dict) -> None:
    unwired = [entry["id"] for entry in manifest["middlewares"] if not entry.get("wired", False)]
    assert not unwired, f"middleware modules exist but are not referenced by any agent chain (wire them or mark intentionally_unwired with a reason): {unwired}"


def test_supervisor_loops_match_the_manifest(manifest: dict) -> None:
    from app.gateway.autonomy.supervisor import AutonomySupervisor

    supervisor = AutonomySupervisor()
    supervisor.register_default_loops()
    code_ids = set(supervisor.status()["loops"])
    manifest_ids = _ids(manifest, "loops")
    assert code_ids == manifest_ids, f"AutonomySupervisor loops {sorted(code_ids)} != manifest loops {sorted(manifest_ids)} — regenerate the manifest or update the supervisor registry"


def test_declared_modules_exist(manifest: dict) -> None:
    """Every manifest entry's module must be declared and importable.

    A missing module used to be skipped silently (``if not module: continue``),
    which hid a generator bug that emitted ``module: ""`` for all 117 tools:
    the drift check therefore verified nothing. Every entry now carries a real
    module, so an empty one is itself drift.
    """
    failures: list[str] = []
    for section in ("tools", "routers", "middlewares", "loops"):
        for entry in manifest.get(section, []):
            module = str(entry.get("module", ""))
            if not module:
                failures.append(f"{section}:{entry['id']} -> (no module declared)")
                continue
            # Loop modules are declared as adapter call paths: module:function
            module_path, _, symbol = module.partition(":")
            try:
                imported = importlib.import_module(module_path)
            except Exception as exc:  # noqa: BLE001 - report every drift at once
                failures.append(f"{section}:{entry['id']} -> {module_path} ({type(exc).__name__}: {exc})")
                continue
            # The symbol half was previously discarded with the split, so a
            # manifest naming a nonexistent tick adapter imported the module and
            # called it a success. `test_declared_loop_adapters_are_resolvable`
            # covers the registry specifically; this keeps every entry honest
            # under the one check that already claimed to verify all of them.
            if symbol and not hasattr(imported, symbol):
                failures.append(f"{section}:{entry['id']} -> {module} (module imports, but defines no {symbol!r})")
    assert not failures, "manifest module drift detected:\n" + "\n".join(failures)


def test_declared_loop_adapters_are_resolvable(manifest: dict) -> None:
    """Every supervisor loop must name an adapter the adapter module defines.

    ``generate_feature_manifest.py`` reads the adapter straight out of the
    ``defaults`` tuple in ``register_default_loops`` and cross-checks it against
    the functions ``app.gateway.autonomy.loops`` actually declares, so a loop the
    supervisor would raise ``AttributeError`` on cannot be published as wired.
    This test is the runtime half of the same claim: it imports the real modules
    and asserts the supervisor's registered tick *is* the manifest's adapter, so
    the two descriptions cannot drift apart in either direction.
    """
    import app.gateway.autonomy.loops as loop_adapters
    from app.gateway.autonomy.supervisor import AutonomySupervisor

    supervisor = AutonomySupervisor()
    supervisor.register_default_loops()
    registered = supervisor._specs  # noqa: SLF001 - the registry is the subject under test

    entries = {entry["id"]: entry for entry in manifest["loops"]}
    assert set(entries) == set(registered), f"manifest loops {sorted(entries)} != supervisor loops {sorted(registered)} — regenerate the manifest"

    failures: list[str] = []
    for loop_id, entry in sorted(entries.items()):
        module_path, _, symbol = str(entry["module"]).partition(":")
        assert module_path == "app.gateway.autonomy.loops", f"{loop_id} names adapter module {module_path!r}; every adapter lives in app.gateway.autonomy.loops"
        assert symbol, f"{loop_id} declares no adapter symbol"
        if not entry.get("wired"):
            failures.append(f"{loop_id}: manifest marks it unwired ({entry.get('reason', 'no reason given')})")
        if not callable(getattr(loop_adapters, symbol, None)):
            failures.append(f"{loop_id}: app.gateway.autonomy.loops defines no callable {symbol!r}")
        spec_tick = getattr(registered[loop_id], "tick")
        if getattr(spec_tick, "__name__", None) != symbol:
            failures.append(f"{loop_id}: supervisor registers {getattr(spec_tick, '__name__', spec_tick)!r} but the manifest names {symbol!r}")
    assert not failures, "supervisor loop adapters are not wired:\n" + "\n".join(failures)


def test_the_generator_detects_a_loop_whose_adapter_does_not_exist(tmp_path: Path) -> None:
    """The generator's negative path, proved rather than assumed.

    Every other check in this file reads the committed manifest, so a generator
    that stamped ``wired: true`` unconditionally would pass all of them. This
    runs ``collect_loops`` against a *copy* of the adapter module with one tick
    renamed: the entry must come back unwired, carrying the name it looked for
    and why it is missing. Without that, "the manifest measures the wiring"
    would itself be an untested claim.
    """
    import importlib.util
    import shutil

    spec = importlib.util.spec_from_file_location("feature_manifest_generator", BACKEND / "scripts" / "generate_feature_manifest.py")
    assert spec is not None and spec.loader is not None
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)

    autonomy = BACKEND / "app" / "gateway" / "autonomy"
    probe = tmp_path / "app" / "gateway" / "autonomy"
    probe.mkdir(parents=True)
    loops = (autonomy / "loops.py").read_text(encoding="utf-8")
    renamed = loops.replace("def apex_tick()", "def apex_tick_RENAMED()", 1)
    assert renamed != loops, "the fixture must actually rename a real tick, or this test proves nothing"
    (probe / "loops.py").write_text(renamed, encoding="utf-8")
    shutil.copyfile(autonomy / "supervisor.py", probe / "supervisor.py")

    original_app = generator.APP
    # `APP` must point at the `app/` package root, because `collect_loops`
    # appends `gateway/autonomy/...` to it.
    generator.APP = tmp_path / "app"
    try:
        rows = generator.collect_loops()
    finally:
        generator.APP = original_app

    unwired = [row for row in rows if not row["wired"]]
    assert len(unwired) == 1, f"renaming one tick must mark exactly one loop unwired, got {[row['id'] for row in unwired]}"
    assert unwired[0]["id"] == "apex"
    assert unwired[0]["symbol"] == "apex_tick", "the entry must name the adapter it looked for, not the id-derived guess"
    assert unwired[0]["reason"], "an unwired entry without a reason is indistinguishable from a generator bug"
    assert sum(1 for row in rows if row["wired"]) == len(rows) - 1, "the other loops must still resolve — a whole-file parse failure would pass this test too"


def test_manifest_loop_wiring_is_verified_not_asserted(manifest: dict) -> None:
    """The generator must not publish ``wired: true`` it never checked.

    ``collect_loops`` used to build each entry from ``f"{loop_id}_tick"`` - a
    name derived from the loop id, never looked up - and stamp ``wired: True``
    unconditionally. Renaming an adapter or emptying ``loops.py`` therefore left
    a manifest that still claimed every loop was wired, and the integration
    health endpoint reported 100% loop coverage over loops that could not run.
    The entry must now carry the adapter the supervisor really calls, and a
    missing one has to surface as ``wired: false`` rather than as silence.
    """
    for entry in manifest["loops"]:
        assert entry.get("module"), f"{entry['id']} declares no adapter module"
        assert entry.get("symbol"), f"{entry['id']} declares no adapter symbol"
        if not entry.get("wired"):
            assert entry.get("reason"), f"{entry['id']} is unwired with no reason — an unexplained unwired entry is indistinguishable from a generator bug"


def test_local_only_exclusions_are_recorded(manifest: dict) -> None:
    excluded_ids = {entry["id"] for entry in manifest["excluded_local_only"]}
    for required in ("auth", "channels", "remote_mcp_acp", "cloud_sandboxes", "remote_tracing", "github_webhooks"):
        assert required in excluded_ids, f"missing local-only exclusion: {required}"


def _production_sources() -> list[Path]:
    """Every first-party .py file outside the test tree."""
    roots = [
        BACKEND / "packages" / "harness" / "alpha",
        BACKEND / "app",
    ]
    return [path for root in roots for path in root.rglob("*.py")]


def _imported_module_paths(path: Path) -> set[str]:
    """Dotted module paths a file imports (``from a.b import c`` -> ``a.b``).

    Relative imports are resolved against the containing package so an intra-package
    import is not mistaken for an external consumer.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            level = node.level or 0
            if level:
                # ``from . import x`` inside ``a/b/c.py`` resolves against ``a.b``.
                parts = list(path.with_suffix("").parts)
                if parts[-1] == "__init__":
                    parts = parts[:-1]
                else:
                    parts = parts[:-1]
                package = parts[: len(parts) - (level - 1)]
                base = ".".join([*package, base]) if base else ".".join(package)
            if base:
                names.add(base)
            names.update(f"{base}.{alias.name}" for alias in node.names if alias.name != "*")
    return names


def test_dormant_packages_are_still_dormant(manifest: dict) -> None:
    """A waived dormant package must still exist and still have no consumer.

    ``alpha.ledger`` and ``alpha.evidence`` are complete subsystems that nothing
    constructs: a dedicated test suite makes them look alive in CI while no
    production path reaches them. That is a deliberate deferral, recorded here so
    it is discoverable -- and this guard is what keeps the record honest. It fails
    if the package disappears (the waiver now dangles) or if some production
    module starts importing it (the waiver is stale, so drop the entry).
    """
    entries = manifest.get("dormant_packages")
    assert isinstance(entries, list), "dormant_packages section disappeared from the manifest"

    sources = _production_sources()
    for entry in entries:
        package = entry["id"]
        package_dir = BACKEND / "packages" / "harness" / Path(*package.split("."))
        assert package_dir.is_dir(), f"dormant_packages lists {package!r} but that package no longer exists — remove the manifest entry"

        importers = []
        for path in sources:
            if package_dir in path.parents:
                continue  # the package importing itself is not a consumer
            if any(name == package or name.startswith(f"{package}.") for name in _imported_module_paths(path)):
                importers.append(str(path.relative_to(BACKEND)))

        assert not importers, f"{package!r} is waived as dormant in contracts/feature_manifest.json, but it is now imported by {sorted(importers)} — the waiver is stale, so remove the dormant_packages entry (and wire it properly)"


def test_wired_package_is_not_listed_as_dormant(manifest: dict) -> None:
    """``alpha.critic`` was dormant and is now wired into the completion path.

    It is the counterpart to the guard above: the manifest must not claim a
    package is dormant once a production module constructs it. ``FinishFirstVerifierMiddleware``
    builds the pipeline, so ``alpha.critic`` must never appear in the waiver list.
    """
    dormant_ids = {entry["id"] for entry in manifest.get("dormant_packages", [])}
    assert "alpha.critic" not in dormant_ids

    middleware = BACKEND / "packages" / "harness" / "alpha" / "agents" / "middlewares" / "finish_first_verifier_middleware.py"
    assert middleware.exists(), "the completion critic middleware disappeared"
    # Check the import, not a substring: the module docstring names ``alpha.critic``
    # too, so a naive text match would survive the import being deleted.
    assert "alpha.critic" in _imported_module_paths(middleware), "FinishFirstVerifierMiddleware no longer imports alpha.critic — the critic pipeline is unwired again"

    lead_agent = BACKEND / "packages" / "harness" / "alpha" / "agents" / "lead_agent" / "agent.py"
    assert "alpha.critic" in _imported_module_paths(lead_agent), "the lead agent no longer builds a critic pipeline — a terminal message would be accepted unchecked"


# Builtin tool symbols exported from ``alpha.tools.builtins`` that are
# deliberately absent from ``alpha.tools.tools``. Each entry maps the symbol to
# the production file that really wires it, so the waiver self-corrects: if that
# file stops importing it, the test fails instead of the hole rotting open.
UNREGISTERED_BUILTINS: dict[str, tuple[str, str]] = {
    "setup_agent": (
        "packages/harness/alpha/agents/lead_agent/agent.py",
        "agent bootstrap handshake; added to the lead's tool list at build time",
    ),
    "update_agent": (
        "packages/harness/alpha/agents/lead_agent/agent.py",
        "custom-agent self-update; withheld for webhook-channel runs, so it is added conditionally",
    ),
}

_BUILTINS_INIT = BACKEND / "packages" / "harness" / "alpha" / "tools" / "builtins" / "__init__.py"
_TOOLS_PY = BACKEND / "packages" / "harness" / "alpha" / "tools" / "tools.py"


def _relative_exported_symbols(path: Path) -> set[str]:
    """Symbols a package __init__ re-exports via relative imports.

    Relative imports carry the dot in ``node.level``, not in ``node.module`` —
    ``from .a2a_tool import a2a_tool`` is module="a2a_tool", level=1. Matching
    on ``module.startswith(".")`` silently matches nothing.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    symbols: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.level or 0) > 0:
            for alias in node.names:
                if alias.name != "*":
                    symbols.add(alias.name)
        # Lazy package facades keep the complete public map in a literal so
        # wiring checks stay source-only and do not import every builtin.
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "_EXPORT_MODULES" for target in node.targets) and isinstance(node.value, ast.Dict):
            symbols.update(key.value for key in node.value.keys if isinstance(key, ast.Constant) and isinstance(key.value, str))
    return symbols


def _imported_symbols(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    symbols: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name != "*":
                    symbols.add(alias.asname or alias.name.split(".")[-1])
    return symbols


def _facade_resolved_symbols(path: Path) -> set[str]:
    """Names pulled through ``builtins.resolve_builtin_tool("<name>")``.

    ``alpha.tools.builtins`` is a PEP-562 lazy facade whose ``__getattr__``
    resolves a public name on first access, but a direct submodule import binds
    the *module* on the package attribute and makes that lookup hand back a
    module instead of the tool. ``_build_registries`` therefore resolves every
    name-collision-prone export through ``resolve_builtin_tool("name")`` rather
    than a bare ``from alpha.tools.builtins import name``.

    Those imports are real wiring — they perform the same import the registry
    literal names — but they are ``Call`` nodes, not ``ImportFrom``, so an
    import-symbol scan missed all 26 of them and reported every one as an
    unregistered builtin. This helper reads the same evidence the import scan
    reads, for that second spelling.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != "resolve_builtin_tool":
            continue
        for argument in node.args:
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                names.add(argument.value)
    return names


def test_every_exported_builtin_tool_is_registered() -> None:
    """A builtin exported from the package must reach a tool registry.

    Exporting a tool is not the same as wiring it. ``deep_research`` was
    exported and listed in ``__all__`` but never imported by ``tools.py``, so
    the ``deep-research`` subagent category advertised a tool that
    ``_filter_tools`` silently dropped — the subagent was instructed to call
    something it could not reach. Registration is the only thing that matters.
    """
    exported = _relative_exported_symbols(_BUILTINS_INIT)
    assert exported, "builtins/__init__.py no longer re-exports anything"
    registered = _imported_symbols(_TOOLS_PY) | _facade_resolved_symbols(_TOOLS_PY)
    allowlisted = set(UNREGISTERED_BUILTINS)
    missing = sorted(exported - registered - allowlisted)
    assert not missing, (
        "builtin tools are exported from alpha.tools.builtins but never imported by "
        "alpha.tools.tools, so no agent can call them — add them to BUILTIN_TOOLS / "
        f"SUBAGENT_TOOLS, or add a UNREGISTERED_BUILTINS entry naming the real caller: {missing}"
    )


def test_unregistered_builtins_are_imported_by_their_declared_caller() -> None:
    """Each UNREGISTERED_BUILTINS waiver must still point at a real importer."""
    for symbol, (rel_path, _reason) in UNREGISTERED_BUILTINS.items():
        path = BACKEND / rel_path
        assert path.exists(), f"UNREGISTERED_BUILTINS entry for {symbol!r} points at a missing file: {rel_path}"
        assert symbol in _imported_symbols(path), f"{symbol!r} is waived as wired by {rel_path}, but that file no longer imports it — the waiver is stale, so register the tool or correct the path"


def _referenced_names(path: Path) -> set[str]:
    """Identifiers actually referenced in *path*, excluding import statements.

    ``ast.walk`` yields ``alias`` nodes for imports, never ``ast.Name``, so the
    set of ``Name`` ids is exactly "identifiers used in the body" — which makes
    this the AST equivalent of ruff's F401.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}


def test_imported_builtin_tools_are_actually_used_in_the_registry_module() -> None:
    """Importing a tool into ``tools.py`` is not the same as wiring it.

    ``test_every_exported_builtin_tool_is_registered`` compares against the
    symbols ``tools.py`` *imports*, which a tool satisfies merely by appearing
    in the import block. ``execute_slash_command_tool`` and
    ``identify_autonomous_command_tool`` did exactly that: imported, exported,
    fully implemented — and named in neither ``BUILTIN_TOOLS`` nor
    ``SUBAGENT_TOOLS``, so no agent could reach either. The only outward
    symptom was a ruff F401.

    This guard requires the symbol to be *referenced* somewhere in the module
    body, so an import-only entry fails.
    """
    imported = _imported_symbols(_TOOLS_PY)
    referenced = _referenced_names(_TOOLS_PY) | _facade_resolved_symbols(_TOOLS_PY)
    exported = _relative_exported_symbols(_BUILTINS_INIT)
    allowlisted = set(UNREGISTERED_BUILTINS)
    dead = sorted((exported & imported) - referenced - allowlisted)
    assert not dead, (
        f"builtin tools are imported by alpha.tools.tools but never referenced in it, so they are in no registry and no agent can call them — add them to BUILTIN_TOOLS / SUBAGENT_TOOLS or waive them in UNREGISTERED_BUILTINS: {dead}"
    )
