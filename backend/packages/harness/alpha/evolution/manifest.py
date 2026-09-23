"""Project manifest loader: the single source of truth for repository identity.

``config/project-manifest.json`` at the repository root names the canonical
GitHub repository, its default (host/source-of-truth) branch, and the release
channel — architecture spec section 3. Nothing else may hardcode repository
URLs.

Resolution order (first hit wins):

1. ``ALPHA_PROJECT_MANIFEST`` — explicit manifest path. This env knob is read
   ONLY here (documented per scripts/audit_env_wiring.py conventions, where it
   classifies as READ-ONLY: nothing in the repo sets it, users/tests may
   supply it). When set it is authoritative: a missing or invalid file raises
   so a broken override is never silently ignored. The getenv call below uses
   the literal name so audit_env_wiring.py's literal-based READ scan sees it.
2. ``project_root()/config/project-manifest.json``.
3. The first ``config/project-manifest.json`` found walking up from this file
   (covers source checkouts invoked from a subdirectory such as ``backend/``).

A missing or invalid manifest raises ``RuntimeError`` naming what was
searched or which field failed — identity, prompt, and update-check callers
fail loudly instead of falling back to fabricated defaults.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from alpha.config.runtime_paths import project_root

MANIFEST_RELPATH = ("config", "project-manifest.json")

_REQUIRED_REPOSITORY_FIELDS = ("provider", "owner", "name", "url", "defaultBranch")


def find_project_manifest_path() -> Path:
    """Return the manifest path via the documented resolution order.

    Raises ``RuntimeError`` when no manifest can be found — there is no
    default repository to fall back to.
    """
    override = os.getenv("ALPHA_PROJECT_MANIFEST")
    if override:
        path = Path(override).expanduser()
        if not path.is_file():
            raise RuntimeError(f"ALPHA_PROJECT_MANIFEST is set to '{path}', but no project manifest exists there.")
        return path
    project_candidate = project_root().joinpath(*MANIFEST_RELPATH)
    if project_candidate.is_file():
        return project_candidate
    searched = [project_candidate]
    for parent in Path(__file__).resolve().parents:
        candidate = parent.joinpath(*MANIFEST_RELPATH)
        if candidate.is_file():
            return candidate
        searched.append(candidate)
    unique = list(dict.fromkeys(str(item) for item in searched))
    raise RuntimeError(f"Project manifest '{'/'.join(MANIFEST_RELPATH)}' not found. Searched: {', '.join(unique)}.")


def load_project_manifest_at(path: Path) -> dict[str, Any]:
    """Parse and validate the manifest at an explicit path.

    Enforces the honesty rules of the manifest contract: it must be a JSON
    object with the identity fields present, and it must NOT carry a
    ``version`` field (versions live in the four sources gated by
    ``scripts/verify_versions.sh``; a fifth source would drift).
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise RuntimeError(f"Project manifest {path} is not valid JSON: {exc}") from exc
    except OSError as exc:
        raise RuntimeError(f"Project manifest {path} could not be read: {exc}") from exc
    if not isinstance(raw, dict):
        raise RuntimeError(f"Project manifest {path} must contain a JSON object, found {type(raw).__name__}.")
    if "version" in raw:
        raise RuntimeError(
            f"Project manifest {path} must not carry a 'version' field: versions belong to the four sources "
            "gated by scripts/verify_versions.sh (deploy/helm Chart.yaml, both pyproject.toml files, "
            "frontend/package.json), and a fifth source would drift."
        )

    def _invalid(container: Any, field: str) -> bool:
        value = container.get(field)
        return not isinstance(value, str) or not value.strip()

    problems = [field for field in ("projectId", "name") if _invalid(raw, field)]
    repository = raw.get("repository")
    if not isinstance(repository, dict):
        problems.append("repository (object)")
    else:
        problems.extend(f"repository.{field}" for field in _REQUIRED_REPOSITORY_FIELDS if _invalid(repository, field))
    release = raw.get("release")
    if not isinstance(release, dict) or _invalid(release, "channel"):
        problems.append("release.channel")
    if problems:
        raise RuntimeError(f"Project manifest {path} has missing or invalid fields: {', '.join(problems)}.")
    return raw


def load_project_manifest() -> dict[str, Any]:
    """Load the canonical project manifest via :func:`find_project_manifest_path`."""
    return load_project_manifest_at(find_project_manifest_path())
