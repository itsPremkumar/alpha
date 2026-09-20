"""Sentinel — autonomous monitor / diagnose / fix / verify / commit loop.

The loop is:

    OBSERVE -> DIAGNOSE -> FIX -> VERIFY -> COMMIT

Each stage is bounded and reversible. A stage that cannot complete escalates
rather than guessing.

The one rule that must never break: **a red or unknown verification result
reverts and escalates. It never commits.**

See ``docs/SENTINEL_AUTONOMOUS_AGENT_PLAN.md`` for the full design.
"""

from agent_workspace.runtime.sentinel import checkpoint as checkpoint
from agent_workspace.runtime.sentinel import commit as commit
from agent_workspace.runtime.sentinel import loop as loop
from agent_workspace.runtime.sentinel import verify as verify
from agent_workspace.runtime.sentinel.checkpoint import Checkpoint, CheckpointManager
from agent_workspace.runtime.sentinel.commit import (
    CommitResult,
    Committer,
    SafetyError,
    build_commit_message,
    is_forbidden_path,
    validate_paths,
)
from agent_workspace.runtime.sentinel.loop import KNOWN_KINDS, LoopOutcome, SentinelLoop
from agent_workspace.runtime.sentinel.runner import (
    RunReport,
    SentinelRunner,
    make_default_fix_fns,
)
from agent_workspace.runtime.sentinel.signals import (
    DEFAULT_SEVERITY,
    SEVERITIES,
    SOURCES,
    AttemptRecord,
    Signal,
    SignalTracker,
    compute_fingerprint,
    dedupe,
    normalize_message,
    sort_by_severity,
)
from agent_workspace.runtime.sentinel.verify import CheckResult, Verifier, VerifyReport

__all__ = [
    "AttemptRecord",
    "CheckResult",
    "Checkpoint",
    "CheckpointManager",
    "CommitResult",
    "Committer",
    "DEFAULT_SEVERITY",
    "KNOWN_KINDS",
    "LoopOutcome",
    "RunReport",
    "SEVERITIES",
    "SentinelRunner",
    "SOURCES",
    "SafetyError",
    "SentinelLoop",
    "Signal",
    "SignalTracker",
    "Verifier",
    "VerifyReport",
    "build_commit_message",
    "checkpoint",
    "commit",
    "compute_fingerprint",
    "dedupe",
    "is_forbidden_path",
    "loop",
    "make_default_fix_fns",
    "normalize_message",
    "sort_by_severity",
    "validate_paths",
    "verify",
]
