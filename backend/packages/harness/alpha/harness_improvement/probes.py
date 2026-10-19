"""Probe workspaces and matched-environment fingerprints (guide §7 step 5, §12.2).

A probe is an execution of the same task under a different bundle, in its own
disposable workspace, with the environment recorded so a later contrast can
*detect* a confounded comparison instead of assuming every probe ran
identically.

The fingerprint deliberately excludes the workspace path (each probe *must*
have its own), and includes everything else that could change behavior: model,
runtime/tool/policy versions, network mode, isolation flags, OS/Python, and the
seed. Two candidate probes whose fingerprints differ produce a ``confounded``
contrast — the code never averages across them.

Workspaces are materialized from the immutable bundle's content, never edited
in place: ``harness/`` is a *projection* of the bundle, so a probe that writes
into its own tree cannot touch the active harness or another candidate's.
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from alpha.harness_improvement.components import validate_component_id
from alpha.harness_improvement.models import ComponentKind, ComponentRef, HarnessBundle, canonical_json, sha256_hex
from alpha.harness_improvement.store import utc_now


@dataclass(frozen=True, slots=True)
class ProbeEnvironment:
    """Everything held constant for matched probes, plus what could not be.

    ``notes`` is where "could not hold the working tree constant" goes. The
    guide's point is not that environments are deterministic; it is that the
    confounders are logged (guide §7 step 1).
    """

    role: str
    model: str | None = None
    alpha_runtime_version: str = "unknown"
    tool_schema_version: str = "unknown"
    policy_version: str = "unknown"
    network_mode: str = "deny_by_default"
    isolate_filesystem: bool = True
    isolate_memory_writes: bool = True
    allow_external_side_effects: bool = False
    python_version: str = field(default_factory=lambda: f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    platform_name: str = field(default_factory=lambda: platform.system().lower() or "unknown")
    seed: int | None = None
    notes: tuple[str, ...] = ()

    def fingerprint_payload(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "alpha_runtime_version": self.alpha_runtime_version,
            "tool_schema_version": self.tool_schema_version,
            "policy_version": self.policy_version,
            "network_mode": self.network_mode,
            "isolate_filesystem": self.isolate_filesystem,
            "isolate_memory_writes": self.isolate_memory_writes,
            "allow_external_side_effects": self.allow_external_side_effects,
            "python_version": self.python_version,
            "platform_name": self.platform_name,
            "seed": self.seed,
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self.fingerprint_payload()
        payload["role"] = self.role
        payload["notes"] = list(self.notes)
        payload["fingerprint"] = environment_fingerprint(self)
        return payload


def environment_fingerprint(environment: ProbeEnvironment) -> str:
    """Stable digest of the held-constant environment (workspace excluded)."""
    return f"sha256:{sha256_hex(canonical_json(environment.fingerprint_payload()))}"


def environment_confounders(baseline: ProbeEnvironment, candidate: ProbeEnvironment) -> tuple[str, ...]:
    """Keys whose values differ between two probes, in stable order."""
    base = baseline.fingerprint_payload()
    cand = candidate.fingerprint_payload()
    return tuple(sorted(key for key in set(base) | set(cand) if base.get(key) != cand.get(key)))


#: Where each component kind is projected inside a probe workspace.
def relative_path_for(ref: ComponentRef) -> str:
    """The workspace-relative file a component materializes to.

    Paths are derived from the *validated* logical component id only, so a
    component id that passed validation cannot escape the workspace through
    this function.
    """
    validate_component_id(ref.component_id)
    namespace, _, name = ref.component_id.partition("/")
    name = name or namespace
    if ref.kind is ComponentKind.SKILL:
        return f"harness/skills/{name}/SKILL.md"
    if ref.kind is ComponentKind.SUBAGENT:
        return f"harness/subagents/{name}/AGENT.md"
    if ref.kind is ComponentKind.SCRIPT:
        suffix = "" if name.endswith(".py") else ".py"
        return f"harness/scripts/{name}{suffix}"
    if ref.kind is ComponentKind.MIDDLEWARE:
        suffix = "" if name.endswith(".py") else ".py"
        return f"harness/middleware/{name}{suffix}"
    if ref.kind is ComponentKind.TOOL_GUIDANCE:
        return f"harness/tool_guidance/{name}.md"
    if ref.kind is ComponentKind.MEMORY_REFERENCE:
        return f"harness/memory/{name}.md"
    if ref.kind is ComponentKind.GUARDRAIL_GUIDANCE:
        return "harness/guardrails_guidance.md"
    return "harness/system_prompt.md"


@dataclass(slots=True)
class ProbeWorkspace:
    """One probe's disposable tree.

    ``harness/`` is the bundle projection, ``outputs/`` is where the task's
    deliverables land, ``scratch/`` is free space, ``logs/`` holds captured
    evidence. Only ``harness/`` is written by this module.
    """

    root: Path
    candidate_id: str
    run_id: str
    reused: bool = False

    @property
    def harness_dir(self) -> Path:
        return self.root / "harness"

    @property
    def outputs_dir(self) -> Path:
        return self.root / "outputs"

    @property
    def scratch_dir(self) -> Path:
        return self.root / "scratch"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "candidate_id": self.candidate_id,
            "run_id": self.run_id,
            "reused": self.reused,
            "created_at": utc_now(),
        }

    def write_json(self, relative: str, payload: Any) -> Path:
        """Write a JSON artifact under ``outputs/`` (confined)."""
        from alpha.harness_improvement.store import atomic_write_json

        target = self._confined(relative)
        atomic_write_json(target, payload)
        return target

    def write_text(self, relative: str, text: str) -> Path:
        target = self._confined(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="")
        return target

    def _confined(self, relative: str) -> Path:
        from alpha.harness_improvement.validators import confined_path

        resolved, refusal = confined_path(self.outputs_dir, relative)
        if resolved is None:
            raise ValueError(f"refusing to write outside the probe outputs directory: {refusal}")
        return resolved


def create_probe_workspace(run_workspace_root: Path, *, run_id: str, candidate_id: str) -> ProbeWorkspace:
    """Create (or reuse, on resume) one candidate's probe workspace.

    Reuse is deliberate: a crash between "workspace created" and "probe
    finished" must not lose the partial artifacts the run record references.
    """
    safe_candidate = candidate_id.replace(":", "_").replace("/", "_")
    root = Path(run_workspace_root) / safe_candidate
    reused = root.exists()
    for directory in (root / "harness", root / "outputs", root / "scratch", root / "logs"):
        directory.mkdir(parents=True, exist_ok=True)
    return ProbeWorkspace(root=root, candidate_id=candidate_id, run_id=run_id, reused=reused)


def materialize_bundle(workspace: ProbeWorkspace, bundle: HarnessBundle, contents: Mapping[str, str]) -> dict[str, str]:
    """Project *bundle* into ``workspace/harness/`` and return written paths.

    Missing content for a referenced component is refused: a probe that runs
    against half a harness would produce evidence about neither bundle.
    """
    written: dict[str, str] = {}
    for ref in bundle.components:
        content = contents.get(ref.component_id)
        if content is None:
            raise ValueError(f"bundle {bundle.bundle_id} references component {ref.component_id!r} whose content is not available")
        relative = relative_path_for(ref)
        target = workspace.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="")
        written[ref.component_id] = relative
    return written


def cleanup_probe_workspace(workspace: ProbeWorkspace) -> tuple[bool, str]:
    """Remove a probe's scratch tree, keeping harness/outputs/logs evidence.

    Returns ``(removed, detail)`` — a cleanup that fails is reported, never
    swallowed, and the evidence directories are never deleted because the run
    record references them.
    """
    target = workspace.scratch_dir
    if not target.exists():
        return True, "scratch directory was already absent"
    try:
        shutil.rmtree(target)
        return True, "scratch directory removed"
    except OSError as exc:
        return False, f"scratch directory could not be removed: {exc}"


def probe_summary(outcome: Any) -> dict[str, Any]:
    """A compact projection of a probe outcome for run listings and UI."""
    from alpha.harness_improvement.validators import validator_summary

    return {
        "run_id": outcome.run_id,
        "candidate_id": outcome.candidate_id,
        "bundle_id": outcome.bundle_id,
        "errored": outcome.errored,
        "error": outcome.error,
        "tool_error_count": outcome.tool_error_count,
        "tool_calls": outcome.tool_calls,
        "model_turns": outcome.model_turns,
        "elapsed_ms": outcome.elapsed_ms,
        "token_usage": outcome.token_usage.to_dict(),
        "validators": validator_summary(outcome.validator_results),
        "environment": dict(outcome.environment),
    }


def current_platform_name() -> str:
    """The lowest common denominator of the platform for fingerprints."""
    return platform.system().lower() or os.name
