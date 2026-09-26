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
    assert MANIFEST_PATH.exists(), (
        "contracts/feature_manifest.json is missing — run: cd backend && python scripts/generate_feature_manifest.py"
    )
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _ids(manifest: dict, section: str) -> set[str]:
    return {entry["id"] for entry in manifest.get(section, [])}


def test_manifest_versions_and_sections(manifest: dict) -> None:
    assert manifest["version"]
    for section in ("tools", "routers", "middlewares", "loops"):
        assert section in manifest and isinstance(manifest[section], list)
    assert manifest["excluded_local_only"], "local-only exclusions must be recorded"


def test_every_builtin_tool_is_in_the_manifest(manifest: dict) -> None:
    source = (BACKEND / "packages" / "harness" / "alpha" / "tools" / "tools.py").read_text(encoding="utf-8")
    # Source-level check (no heavy import): every tool symbol registered in code
    # must appear as a manifest entry id.
    import ast

    tree = ast.parse(source)
    registered: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "BUILTIN_TOOLS" for t in node.targets
        ):
            if isinstance(node.value, ast.List):
                registered = {elt.id for elt in node.value.elts if isinstance(elt, ast.Name)}
            break
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
    assert not unwired, (
        "middleware modules exist but are not referenced by any agent chain "
        f"(wire them or mark intentionally_unwired with a reason): {unwired}"
    )


def test_supervisor_loops_match_the_manifest(manifest: dict) -> None:
    from app.gateway.autonomy.supervisor import AutonomySupervisor

    supervisor = AutonomySupervisor()
    supervisor.register_default_loops()
    code_ids = set(supervisor.status()["loops"])
    manifest_ids = _ids(manifest, "loops")
    assert code_ids == manifest_ids, (
        f"AutonomySupervisor loops {sorted(code_ids)} != manifest loops {sorted(manifest_ids)} — "
        "regenerate the manifest or update the supervisor registry"
    )


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
            module_path = module.split(":")[0]
            try:
                importlib.import_module(module_path)
            except Exception as exc:  # noqa: BLE001 - report every drift at once
                failures.append(f"{section}:{entry['id']} -> {module_path} ({type(exc).__name__}: {exc})")
    assert not failures, "manifest module drift detected:\n" + "\n".join(failures)


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
        assert package_dir.is_dir(), (
            f"dormant_packages lists {package!r} but that package no longer exists — remove the manifest entry"
        )

        importers = []
        for path in sources:
            if package_dir in path.parents:
                continue  # the package importing itself is not a consumer
            if any(name == package or name.startswith(f"{package}.") for name in _imported_module_paths(path)):
                importers.append(str(path.relative_to(BACKEND)))

        assert not importers, (
            f"{package!r} is waived as dormant in contracts/feature_manifest.json, but it is now imported by "
            f"{sorted(importers)} — the waiver is stale, so remove the dormant_packages entry (and wire it properly)"
        )


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
    assert "alpha.critic" in _imported_module_paths(middleware), (
        "FinishFirstVerifierMiddleware no longer imports alpha.critic — the critic pipeline is unwired again"
    )

    lead_agent = BACKEND / "packages" / "harness" / "alpha" / "agents" / "lead_agent" / "agent.py"
    assert "alpha.critic" in _imported_module_paths(lead_agent), (
        "the lead agent no longer builds a critic pipeline — a terminal message would be accepted unchecked"
    )


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
    registered = _imported_symbols(_TOOLS_PY)
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
        assert symbol in _imported_symbols(path), (
            f"{symbol!r} is waived as wired by {rel_path}, but that file no longer imports it — "
            "the waiver is stale, so register the tool or correct the path"
        )


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
    referenced = _referenced_names(_TOOLS_PY)
    exported = _relative_exported_symbols(_BUILTINS_INIT)
    allowlisted = set(UNREGISTERED_BUILTINS)
    dead = sorted((exported & imported) - referenced - allowlisted)
    assert not dead, (
        "builtin tools are imported by alpha.tools.tools but never referenced in it, "
        "so they are in no registry and no agent can call them — add them to "
        f"BUILTIN_TOOLS / SUBAGENT_TOOLS or waive them in UNREGISTERED_BUILTINS: {dead}"
    )
