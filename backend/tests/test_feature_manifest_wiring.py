"""Feature-manifest wiring guards (Phase 2 of the unified integration plan).

These tests fail the build when code exists but is not wired at its declared
integration point, or when the manifest goes stale relative to the source.
Regenerate with: cd backend && python scripts/generate_feature_manifest.py
"""

from __future__ import annotations

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
    """Every manifest entry's module must be importable (catches rename drift)."""
    failures: list[str] = []
    for section in ("tools", "routers", "middlewares", "loops"):
        for entry in manifest.get(section, []):
            module = str(entry.get("module", ""))
            if not module:
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
