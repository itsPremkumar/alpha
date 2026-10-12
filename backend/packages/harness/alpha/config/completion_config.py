"""Configuration for the Alpha Completion Engine (``config.yaml -> completion``).

The section is opt-in *per request*: nothing in the normal chat, run, bot or
scheduler paths creates a completion scope, so enabling the engine never taxes
a turn that did not ask for completion discipline. That is the plan's rollout
rule ("a task mode/policy used for substantial work, not a global interaction
tax") expressed as configuration rather than as a convention.

Two knobs deserve their own sentence:

* ``mode`` — ``enforce`` lets ``finalize`` move a scope to ``complete`` only
  when every blocker is gone; ``observe`` computes exactly the same decision
  and **records it without changing the scope's status**, so an operator can
  run the engine alongside existing workflows and measure what it would have
  blocked. Observe mode never reports a completion it did not apply
  (``FinalReport.enforced`` is ``false``).
* ``preapproved_commands`` — executable basenames the operator trusts for
  ``command``/``pytest`` gates. It is empty by default on purpose: the
  command policy's ``ask`` verdict then requires a per-definition approval
  (bound to the gate's definition digest), which is a click in the UI rather
  than a silent widening of what a generated gate may execute. See
  ``alpha/completion/gates.py``.

Restart-required fields must be registered in
``alpha/config/reload_boundary.py::STARTUP_ONLY_FIELDS``; none of these are,
so a ``config.yaml`` edit applies on the next engine call.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CompletionConfig(BaseModel):
    """``completion:`` block of ``config.yaml``."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=True, description="Expose the Completion Engine API and its UI. Scopes are still opt-in per request.")
    mode: Literal["observe", "enforce"] = Field(
        default="enforce",
        description="observe = compute and record decisions without changing scope status; enforce = the finalization guard may promote to complete.",
    )
    workspace_root: str = Field(
        default="",
        description="Root every path check and command is confined to. Empty resolves to the project root (alpha/checkout).",
    )
    store_path: str = Field(
        default="",
        description="JSON ledger location. Empty resolves to <ALPHA_HOME>/completion_store/state.json (single-process, advisory-locked).",
    )
    gate_timeout_seconds: float = Field(default=300.0, gt=0, le=3600, description="Hard per-check wall-clock limit.")
    max_output_bytes: int = Field(default=65536, ge=1024, le=1_048_576, description="Captured output ceiling; over-limit output is invalid evidence, never a truncated match.")
    max_tasks: int = Field(default=40, ge=1, le=1000, description="Scope-level task ceiling; refused, never clamped silently.")
    max_depth: int = Field(default=4, ge=1, le=8, description="Maximum task-tree depth.")
    max_concurrency: int = Field(default=2, ge=1, le=16, description="Rolling dispatch limit; conservative default for a modest host.")
    max_gate_runs: int = Field(default=200, ge=1, description="Gate executions per scope before the budget reports exhaustion.")
    wall_clock_seconds: float = Field(default=3600.0, gt=0, description="Scope deadline measured from scope creation.")
    no_progress_limit: int = Field(
        default=3,
        ge=1,
        le=20,
        description="Finalization attempts with an unchanged progress fingerprint before the engine reports handoff_required instead of looping.",
    )
    allow_network_checks: bool = Field(default=False, description="Permit api/http gates. Off by default: a network check is an external side effect.")
    preapproved_commands: list[str] = Field(
        default_factory=list,
        description="Executable basenames (e.g. python, pytest) the operator pre-approves for command gates; empty = every non-read-only command needs an approval bound to its definition digest.",
    )
    ownership_lease_seconds: float = Field(default=1800.0, gt=0, description="Path-claim lease; an expired claim is swept rather than blocking forever.")
    max_scopes: int = Field(default=200, ge=1, le=5000, description="Retained scopes; the oldest terminal scope is evicted beyond this.")
    max_events: int = Field(default=2000, ge=100, le=100_000, description="Retained structured completion events (bounded ring).")


__all__ = ["CompletionConfig"]
