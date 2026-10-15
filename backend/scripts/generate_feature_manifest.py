"""Generate contracts/feature_manifest.json — the single wiring source of truth.

Static, import-free analysis (AST + regex) of the repo so the manifest can be
regenerated cheaply in CI or by a contributor. Emits one entry per capability
with ``wired`` state and its wiring point, plus the explicit local-only
exclusion list.

Usage: cd backend && python scripts/generate_feature_manifest.py
"""

from __future__ import annotations

import ast
import json
import re
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
ALPHA = BACKEND / "packages" / "harness" / "alpha"
APP = BACKEND / "app"
OUT = ROOT / "contracts" / "feature_manifest.json"

EXCLUDED_LOCAL_ONLY = [
    {"id": "auth", "reason": "Operator constraint: no sign-in/sign-up/OIDC in the integrated local system."},
    {"id": "channels", "reason": "Operator constraint: no IM channel bridges."},
    {"id": "remote_mcp_acp", "reason": "Operator constraint: no remote MCP/ACP servers."},
    {"id": "cloud_sandboxes", "reason": "Operator constraint: no E2B/OpenSandbox/Tenki/Boxlite."},
    {"id": "remote_tracing", "reason": "Operator constraint: no LangSmith/remote exporters."},
    {"id": "github_webhooks", "reason": "Operator constraint: no webhook ingress."},
]

INTENTIONALLY_UNWIRED = [
    {
        "id": "app.gateway.services.system_monitor_service",
        "reason": "Intentional re-export shim over app.gateway.system_monitor_service; documents namespace-dir resolution (guarded by test_gateway_services_resolution).",
    }
]

# Complete subsystems with a dedicated test suite and no production consumer.
# These are not defects and not dead code to delete: they are finished features
# whose integration point is a product decision that has been deferred. The
# entry exists so the next reader does not rediscover them, and
# ``test_dormant_packages_are_still_dormant`` fails the moment one becomes
# reachable (so the list cannot rot) or disappears (so the entry cannot dangle).
DORMANT_PACKAGES: list[dict[str, str]] = []


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def wired_symbols(path: Path) -> set[str]:
    """All module-level import targets present in a file (absolute + relative)."""
    try:
        tree = ast.parse(read(path))
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            names.add(base)
            for alias in node.names:
                if alias.name != "*":
                    names.add(f"{base}.{alias.name}" if base else alias.name)
    return names


def registry_literal(tree: ast.AST) -> ast.List | None:
    """Locate the ``BUILTIN_TOOLS`` registry literal in a parsed tools.py.

    The registry is resolved lazily (PEP 562), so the literal now lives inside
    ``_build_registries`` — passed straight to ``_deduplicate_tools`` — instead of
    being a module-level assignment evaluated at import time. Both shapes are
    accepted, because a module-level ``BUILTIN_TOOLS = [...]`` is still a complete
    description of the registry (the hermetic drift-gate fixture is exactly that).

    Returns ``None`` when neither shape is found, so the caller can fail loudly:
    an empty ``tools`` section would satisfy every downstream drift check while
    describing nothing.
    """
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_build_registries":
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name) and inner.func.id == "_deduplicate_tools" and inner.args and isinstance(inner.args[0], ast.List):
                    return inner.args[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "BUILTIN_TOOLS" for target in node.targets):
            if isinstance(node.value, ast.List):
                return node.value
    return None


def collect_tools() -> list[dict[str, object]]:
    """Parse BUILTIN_TOOLS in alpha/tools/tools.py + the builtins import map."""
    tools_py = ALPHA / "tools" / "tools.py"
    init_py = ALPHA / "tools" / "builtins" / "__init__.py"

    symbol_map: dict[str, str] = {}
    for node in ast.walk(ast.parse(read(init_py))):
        # Relative imports carry the dot in ``node.level``, NOT in
        # ``node.module``: ``from .a2a_tool import a2a_tool`` parses as
        # module="a2a_tool", level=1. Testing ``module.startswith(".")`` never
        # matched, so every tool entry was emitted with an empty module and the
        # manifest could not detect module drift.
        if isinstance(node, ast.ImportFrom) and (node.level or 0) > 0 and node.module:
            module = f"alpha.tools.builtins.{node.module}"
            for alias in node.names:
                if alias.name != "*":
                    symbol_map.setdefault(alias.name, module)
        # The lazy facade declares no module-level imports at all: it maps every
        # public symbol to the submodule that provides it via `_EXPORT_MODULES`
        # and resolves it on first access in `__getattr__`. Without this branch
        # the symbol map stays empty, so regenerating the manifest emits
        # `module: ""` for all 139 tools and `test_declared_modules_exist`
        # verifies nothing — the exact drift this map exists to catch.
        elif isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "_EXPORT_MODULES" for target in node.targets) and isinstance(node.value, ast.Dict):
            for key, value in zip(node.value.keys, node.value.values):
                if key is not None and isinstance(key, ast.Constant) and isinstance(key.value, str) and isinstance(value, ast.Constant) and isinstance(value.value, str):
                    symbol_map.setdefault(key.value, f"alpha.tools.builtins.{value.value}")

    entries: list[dict[str, object]] = []
    literal = registry_literal(ast.parse(read(tools_py)))
    if literal is None:
        raise SystemExit(f"no BUILTIN_TOOLS registry literal found in {tools_py} — the registry is neither a module-level assignment nor _deduplicate_tools([...]) inside _build_registries()")
    for element in literal.elts:
        if isinstance(element, ast.Name):
            symbol = element.id
            entries.append(
                {
                    "id": symbol,
                    "module": symbol_map.get(symbol, ""),
                    "symbol": symbol,
                    "wiring_point": "alpha.tools.tools:BUILTIN_TOOLS",
                    "wired": True,
                    "state": "wired",
                }
            )
    return entries


def collect_routers() -> list[dict[str, object]]:
    """Every router imported in app.gateway.app + whether include_router mounts it."""
    app_py = APP / "gateway" / "app.py"
    text = read(app_py)
    tree = ast.parse(text)

    router_modules: list[str] = []
    seen: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "app.gateway.routers":
            # Accumulate across EVERY matching import, not just the first.
            #
            # This used to `break` after the first `from
            # app.gateway.routers import (...)` block, so any router imported in
            # a second statement was invisible to the manifest: the drift gate
            # passed while the count was quietly short by those routers, and
            # test_feature_manifest_wiring then failed naming the router it
            # could not find. Keeping one shape — every import, deduplicated,
            # sorted — means the manifest describes app.py rather than the
            # first line of it.
            for alias in node.names:
                if alias.name == "*":
                    continue
                if alias.name not in seen:
                    seen.add(alias.name)
                    router_modules.append(alias.name)

    include_calls = set(re.findall(r"app\.include_router\(\s*(\w+)\.router", text))
    # A router is only "wired" when the name inside include_router matches the
    # name it was imported under. An alias on one side and not the other is a
    # permanent mismatch the generator cannot reconcile, which is why app.py
    # imports and mounts every router under one name.
    entries: list[dict[str, object]] = []
    for name in sorted(router_modules):
        mounted = name in include_calls
        entries.append(
            {
                "id": f"app.gateway.routers.{name}",
                "module": f"app.gateway.routers.{name}",
                "symbol": "router",
                "wiring_point": "app.gateway.app:include_router",
                "wired": mounted,
                "state": "wired" if mounted else "intentionally_unwired",
            }
        )
    return entries


def collect_middlewares() -> list[dict[str, object]]:
    """All *_middleware.py files + whether an agent chain file imports them."""
    mw_dir = ALPHA / "agents" / "middlewares"
    wiring_files = [
        ALPHA / "agents" / "lead_agent" / "agent.py",
        ALPHA / "agents" / "middlewares" / "tool_error_handling_middleware.py",
        ALPHA / "agents" / "factory.py",
    ]
    wired_names: set[str] = set()
    for path in wiring_files:
        if path.exists():
            wired_names |= wired_symbols(path)

    entries: list[dict[str, object]] = []
    for path in sorted(mw_dir.glob("*_middleware.py")):
        module = f"alpha.agents.middlewares.{path.stem}"
        wired = module in wired_names
        entries.append(
            {
                "id": module,
                "module": module,
                "wiring_point": "alpha.agents.lead_agent.agent:middlewares | alpha.agents.middlewares.tool_error_handling_middleware:build_lead_runtime_middlewares | alpha.agents.factory",
                "wired": wired,
                "state": "wired" if wired else "intentionally_unwired",
            }
        )
    return entries


def _top_level_defs(tree: ast.AST) -> set[str]:
    """Names defined at module scope (functions, classes, assignments)."""
    names: set[str] = set()
    for node in getattr(tree, "body", []):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(target.id for target in node.targets if isinstance(target, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def collect_loops() -> list[dict[str, object]]:
    """Loop ids owned by AutonomySupervisor.register_default_loops().

    The tick is read from the registry tuple itself (``loop_adapters.<name>``)
    instead of being reconstructed from the loop id, and that name is then
    checked against the functions ``app/gateway/autonomy/loops.py`` actually
    defines.

    The previous shape fabricated both halves: it wrote
    ``module: "app.gateway.autonomy.loops:<id>_tick"`` (a name it never looked
    up) and asserted ``wired: True`` without reading either file. So renaming an
    adapter, or emptying ``loops.py``, still produced a manifest claiming every
    loop was wired - and the only test that checked it,
    ``test_declared_modules_exist``, split on ``:`` and imported just the module
    half, so the adapter symbol was never resolved at all. A loop the supervisor
    would raise ``AttributeError`` on was reported as wired.
    """
    supervisor_py = APP / "gateway" / "autonomy" / "supervisor.py"
    loops_py = APP / "gateway" / "autonomy" / "loops.py"
    tree = ast.parse(read(supervisor_py))
    registry = next(
        (node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "register_default_loops"),
        None,
    )
    adapters: dict[str, str] = {}
    if registry is not None:
        for node in ast.walk(registry):
            if not isinstance(node, ast.Assign) or not any(isinstance(target, ast.Name) and target.id == "defaults" for target in node.targets):
                continue
            if not isinstance(node.value, (ast.Tuple, ast.List)):
                continue
            for entry in node.value.elts:
                if not (isinstance(entry, ast.Tuple) and entry.elts and isinstance(entry.elts[0], ast.Constant) and isinstance(entry.elts[0].value, str)):
                    continue
                loop_id = str(entry.elts[0].value)
                tick = entry.elts[2] if len(entry.elts) > 2 else None
                if not isinstance(tick, ast.Attribute):
                    raise SystemExit(f"autonomy loop {loop_id!r} does not name its tick as an adapter attribute (loop_adapters.<name>); the generator cannot verify it, so fix the registry rather than the manifest")
                adapters[loop_id] = tick.attr
            break
    if registry is None:
        raise SystemExit(f"AutonomySupervisor.register_default_loops() not found in {supervisor_py} — the loop inventory would be empty while looking complete")

    defined = _top_level_defs(ast.parse(read(loops_py))) if loops_py.exists() else set()
    entries: list[dict[str, object]] = []
    for loop_id in sorted(adapters):
        adapter = adapters[loop_id]
        wired = adapter in defined
        entries.append(
            {
                "id": loop_id,
                "module": f"app.gateway.autonomy.loops:{adapter}",
                "symbol": adapter,
                "wiring_point": "app.gateway.autonomy.supervisor:register_default_loops",
                "wired": wired,
                "state": "wired" if wired else "intentionally_unwired",
                **({} if wired else {"reason": f"{adapter} is registered as {loop_id}'s tick but {loops_py.name} defines no such callable"}),
            }
        )
    return entries


def collect_engines() -> list[dict[str, object]]:
    """Count the engine modules under ``alpha/`` as importable subpackages.

    This exists because the engine count was previously hand-typed in four
    documents and had drifted into three different values (89, 97, 99). A
    capability count nobody generates is a capability count nobody maintains,
    which is the same rule the tools/routers/middlewares/loops counts already
    follow.

    An *engine module* is a direct child directory of ``alpha/`` that either
    declares ``__init__.py`` or contains a submodule of its own. Directories
    holding only data (or nothing) are excluded, as is ``__pycache__``. The
    definition is deliberately mechanical so the number is reproducible: it
    counts directories, not intent.

    A subdirectory counts only if it actually holds Python at any depth. Measured
    on 2026-10-06: ``backend/packages/harness/alpha/backend/packages/harness/alpha``
    existed as three nested *empty* directories with zero tracked files, and the
    old test (``p.is_dir()``) counted ``alpha.backend`` as an engine. The committed
    manifest therefore said 118 while a fresh run said 119 - a capability count
    that changes depending on when it is read, which defeats the point of
    generating it. Empty-directory chains are not importable packages, so they
    are excluded, and this directory may be left on disk: the count no longer
    depends on it.
    """
    engines: list[dict[str, object]] = []

    def _holds_python(directory: Path) -> bool:
        return any(p.suffix == ".py" for p in directory.rglob("*.py"))

    for child in sorted(ALPHA.iterdir()):
        if not child.is_dir() or child.name == "__pycache__":
            continue
        has_init = (child / "__init__.py").is_file()
        submodules = sorted(p.name for p in child.iterdir() if p.is_dir() and p.name != "__pycache__" and _holds_python(p))
        py_files = sorted(p.name for p in child.glob("*.py"))
        if not has_init and not submodules and not py_files:
            # Data-only or empty directory: not an importable engine.
            continue
        engines.append(
            {
                "id": f"alpha.{child.name}",
                "kind": "package" if has_init else "namespace",
                "submodules": submodules,
            }
        )
    return engines


def main() -> int:
    manifest = {
        "version": "1.0",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "generator": "backend/scripts/generate_feature_manifest.py",
        "tools": collect_tools(),
        "routers": collect_routers(),
        "middlewares": collect_middlewares(),
        "loops": collect_loops(),
        "engines": collect_engines(),
        "intentionally_unwired": INTENTIONALLY_UNWIRED,
        "dormant_packages": DORMANT_PACKAGES,
        "excluded_local_only": EXCLUDED_LOCAL_ONLY,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    # json.dumps() emits LF-only separators; write_text() on Windows would
    # translate the "\n" to CRLF, making a freshly regenerated artifact
    # byte-different from the committed one (CRLF vs LF). Normalize to LF so a
    # fresh build matches the committed artifact byte-for-byte.
    # json.dumps() emits LF-only separators; write_text() on Windows would
    # translate the "\n" to CRLF, making a freshly regenerated artifact
    # byte-different from the committed one (CRLF vs LF). Normalize to LF so a
    # fresh build matches the committed artifact byte-for-byte.
    OUT.write_text(
        json.dumps(manifest, indent=1).replace("\r\n", "\n").replace("\r", "\n") + "\n",
        encoding="utf-8",
    )

    counts = {key: len(manifest[key]) for key in ("tools", "routers", "middlewares", "loops", "engines")}
    print(f"manifest written: {OUT}")
    print(f"counts: {counts}")
    for key in ("tools", "routers", "middlewares", "loops"):
        ids = [entry["id"] for entry in manifest[key] if not entry.get("wired", False)]
        if ids:
            print(f"unwired[{key}]: {ids}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
