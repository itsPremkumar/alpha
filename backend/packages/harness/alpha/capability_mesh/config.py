"""Capability mesh configuration: default-off, read/preview-only.

The mesh is a **measurement and preview plane**, not an execution plane. Every
surface it exposes (the ``capability_mesh`` tool, ``/api/capability-mesh``)
calls the package's pure functions and nothing else: a routing plan, a team
plan, a composition and an explanation are all computed *locally* and
**nothing is dispatched**. There is no executor wiring anywhere in this
package, so flipping ``enabled: true`` cannot start an agent run — it can only
make an already-tested read surface reachable.

The defaults are therefore the conservative ones:

* ``enabled: False`` — inert until an operator turns it on. Nothing binds,
  nothing is written, and the tool answers with the reason named.
* ``directory: ""`` — resolves to ``runtime_home()/capability_mesh`` at *read*
  time (never at config-load time, which would stat the filesystem during a
  hot reload). The resolution is lazy for that reason.
* ``default_profile_version`` — every aggregation in the mesh is scoped to
  exactly one rubric version (a v1 sample must never be averaged into a v2
  answer), so the caller needs a declared default rather than an implicit
  "whatever". It is a *label*, not a measurement.
* ``exploration_budget_fraction`` — the operator's declared share of a budget
  that may be spent probing an under-measured candidate. It is reported, not
  spent: nothing in this package executes a probe.

The mesh's durable documents are single-process JSON/JSONL: atomic against a
crash in one process, **never** cross-process exactly-once. See the package
``AGENTS.md``.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from alpha.capability_mesh.memory import DEFAULT_MAX_RECORDS
from alpha.config.runtime_paths import runtime_home

#: Directory name under the runtime home holding every mesh document.
CAPABILITY_MESH_DIR_NAME = "capability_mesh"

#: The declared default rubric identity. Every aggregation is scoped to exactly
#: one profile version, so "unscoped" is not an option the mesh offers; this is
#: the label a fresh installation aggregates under until an operator declares a
#: different one.
DEFAULT_PROFILE_VERSION = "v1"

#: Mirrors ``alpha.capability_mesh.evaluation.should_explore``'s own default so
#: the operator-facing number and the library's number cannot drift.
DEFAULT_EXPLORATION_BUDGET_FRACTION = 0.1


class CapabilityMeshConfig(BaseModel):
    """``config.yaml -> capability_mesh`` settings."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Bind the capability_mesh tool and router. Off = every surface refuses with the reason named. On = read and preview surfaces only; nothing is dispatched.",
    )
    directory: str = Field(
        default="",
        description="Directory holding passports.json, capability_memory.jsonl, pair_estimates.json and shadow.json. Empty resolves to the runtime home under capability_mesh/.",
    )
    max_records: int = Field(
        default=DEFAULT_MAX_RECORDS,
        ge=1,
        description="Bounded retention for the capability memory's JSONL tail; refused below 1, never clamped.",
    )
    default_profile_version: str = Field(
        default=DEFAULT_PROFILE_VERSION,
        description=(
            "Rubric identity every mesh aggregation is scoped to. A v1 sample never earns a v2 answer. "
            "An empty value is NOT refused at load — it is caught one layer later, where it actually "
            "matters: a record carrying a quality with no rubric behind it is refused by name instead of being stored."
        ),
    )
    exploration_budget_fraction: float = Field(
        default=DEFAULT_EXPLORATION_BUDGET_FRACTION,
        ge=0.0,
        le=1.0,
        description="Declared share of a budget that may be spent probing an under-measured candidate. Reported by the surfaces; never spent here (nothing in this package executes a probe).",
    )

    def resolved_directory(self) -> Path:
        """The mesh directory for this configuration.

        Resolved lazily: an operator-declared relative path stays relative to
        the process working directory (matching the rest of the config plane),
        and an unset ``directory`` lands under the runtime home so a fresh
        install never writes beside the source tree.
        """
        declared = self.directory.strip()
        if declared:
            return Path(declared).expanduser()
        return runtime_home() / CAPABILITY_MESH_DIR_NAME


def capability_mesh_config(app_config: object | None = None) -> CapabilityMeshConfig:
    """Resolve the mesh config from an ``AppConfig``.

    ``None`` (no config loaded) yields the defaults, so the surfaces are
    importable and testable without a ``config.yaml`` — and, because the
    default is ``enabled: False``, an unresolved config is *inert*, which is
    the safe direction for a plane that must not dispatch anything.
    """
    if app_config is None:
        return CapabilityMeshConfig()
    mesh = getattr(app_config, "capability_mesh", None)
    if isinstance(mesh, CapabilityMeshConfig):
        return mesh
    return CapabilityMeshConfig()


__all__ = [
    "CAPABILITY_MESH_DIR_NAME",
    "DEFAULT_EXPLORATION_BUDGET_FRACTION",
    "DEFAULT_PROFILE_VERSION",
    "CapabilityMeshConfig",
    "capability_mesh_config",
]
