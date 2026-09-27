"""The feature manifest must ship with every image the Gateway runs in.

``GET /api/ops/integration-health`` reads
``Path(__file__).resolve().parents[4] / "contracts" / "feature_manifest.json"``.
Inside a container the module lives at ``<workdir>/backend/app/gateway/routers/``,
so that lookup resolves to ``<workdir>/contracts/feature_manifest.json`` -- but
``backend/Dockerfile`` copied only ``backend`` and neither compose file mounts
``contracts/``, so every containerised deployment got ``manifest_found: false``
with ``coverage: {}`` and ``unwired: []``: an integration-health report that says
nothing while presenting itself as complete.

These tests pin the deployment contract instead of the symptom: each image stage
the compose files build must land the manifest at exactly the path the endpoint
looks up, and the endpoint's own constant must resolve to a real file in the
checkout (the positive control that makes "shipped to the right path" mean
anything).
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any

from app.gateway.routers.ops_integration import _MANIFEST_PATH, _load_manifest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "backend" / "Dockerfile"

# The endpoint's own lookup, expressed relative to the checkout root. In a
# container that identical relative path is resolved against the image WORKDIR,
# so this is a single source of truth shared by both sides of the contract.
MANIFEST_REL = PurePosixPath(*_MANIFEST_PATH.relative_to(REPO_ROOT).parts)


def _stages() -> list[dict[str, Any]]:
    """Split ``backend/Dockerfile`` into FROM-delimited stages."""
    stages: list[dict[str, Any]] = []
    for raw in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        if re.match(r"^\s*FROM\s+", raw):
            stages.append({"header": raw.strip(), "body": []})
        elif stages:
            stages[-1]["body"].append(raw)

    parsed: list[dict[str, Any]] = []
    for stage in stages:
        workdir: str | None = None
        copies: list[str] = []
        for raw in stage["body"]:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("WORKDIR "):
                workdir = line.split(None, 1)[1]
            elif line.startswith("COPY "):
                copies.append(line[len("COPY ") :])
        parsed.append({"header": stage["header"], "workdir": workdir, "copies": copies})
    return parsed


def _stage_with_alias(alias: str) -> dict[str, Any]:
    for stage in _stages():
        parts = stage["header"].split()
        if len(parts) >= 4 and parts[-2].upper() == "AS" and parts[-1] == alias:
            return stage
    raise AssertionError(f"backend/Dockerfile no longer declares a `{alias}` stage; the compose files build it by that name")


def _runtime_stage() -> dict[str, Any]:
    """The stage a `target:`-less build produces, i.e. docker-compose.yaml's gateway."""
    stages = _stages()
    assert stages, "backend/Dockerfile declares no stages"
    return stages[-1]


def _copy_tokens(args: str) -> list[str]:
    """Split COPY arguments, dropping flags such as ``--from=builder`` / ``--chown 0:0``."""
    parts = args.split()
    kept: list[str] = []
    index = 0
    while index < len(parts):
        token = parts[index]
        if token.startswith("--"):
            index += 1 if "=" in token else 2
            continue
        kept.append(token)
        index += 1
    return kept


def _contract_landings(stage: dict[str, Any]) -> list[PurePosixPath]:
    """Absolute directories/paths this stage's ``COPY`` rules put ``contracts`` at."""
    workdir = PurePosixPath(stage["workdir"] or "/")
    landings: list[PurePosixPath] = []
    for args in stage["copies"]:
        tokens = _copy_tokens(args)
        if len(tokens) < 2:
            continue
        sources, dest = tokens[:-1], tokens[-1]
        if not any(PurePosixPath(source.rstrip("/")).name == "contracts" for source in sources):
            continue
        landings.append(PurePosixPath(dest) if dest.startswith("/") else workdir / dest)
    return landings


def _assert_stage_ships_manifest(stage: dict[str, Any], role: str) -> None:
    workdir = stage["workdir"]
    assert workdir, f"{role} stage declares no WORKDIR, so the endpoint's path arithmetic is undefined"

    # The endpoint sits at <workdir>/backend/app/gateway/routers/..., so its
    # parents[4] must be <workdir> -- that is what makes the lookup agree.
    endpoint = PurePosixPath(workdir) / "backend" / "app" / "gateway" / "routers" / "ops_integration.py"
    assert endpoint.parents[4] == PurePosixPath(workdir), f"{role} stage no longer places the backend tree directly under WORKDIR {workdir}; the endpoint's parents[4] root would move and contracts would need to move with it"

    lookup = endpoint.parents[4] / MANIFEST_REL
    landings = _contract_landings(stage)
    assert landings, f"{role} stage never COPYs contracts/, so {lookup} is absent at runtime and `_load_manifest()` silently returns {{}} -- integration-health then reports manifest_found=false with empty coverage and an empty unwired list"
    assert any(landing == lookup or lookup.is_relative_to(landing) for landing in landings), f"{role} stage lands contracts at {[str(p) for p in landings]}, but the endpoint looks up {lookup}"


def test_endpoint_constant_resolves_to_a_real_manifest_in_the_checkout():
    """Positive control: absent that, "shipped to the right path" proves nothing."""
    manifest = _load_manifest()
    assert manifest, f"_load_manifest() returned nothing for {_MANIFEST_PATH}; the endpoint would report manifest_found=false even where the file is supposed to be"
    assert manifest.get("version"), f"{_MANIFEST_PATH} parsed but carries no version"
    for section in ("tools", "routers", "middlewares", "loops"):
        assert isinstance(manifest.get(section), list), f"{_MANIFEST_PATH} is missing its {section!r} section"


def test_builder_stage_ships_the_manifest():
    """docker-compose-dev.yaml builds `target: dev`, which is `FROM builder`."""
    _assert_stage_ships_manifest(_stage_with_alias("builder"), "builder (dev image)")


def test_runtime_stage_ships_the_manifest():
    """docker-compose.yaml declares no `target:`, so it builds the last FROM."""
    _assert_stage_ships_manifest(_runtime_stage(), "runtime (production image)")
