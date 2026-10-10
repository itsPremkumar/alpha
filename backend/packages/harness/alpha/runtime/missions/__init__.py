"""Durable mission memory: the substrate behind multi-day, drift-free runs.

Why this exists
--------------
Alpha's agent loop is already durable at the process layer --
:mod:`alpha.runtime.sessions`, :mod:`alpha.runtime.network`, the side-effect
ledger, :func:`alpha.runtime.supervisor` and the planned shutdown mean an
outage, a crash or a Windows restart does not become a task failure. What was
missing is the *cognition* of durability: a run that survives a compaction or a
restart with nothing to re-anchor on drifts. It keeps producing work, but the
work detaches from the objective.

This package supplies the re-anchor. It is a re-read of the technique behind the
publicly documented multi-hour autonomous coding runs: the durable file stack
(spec, plan, runbook, live status, scratchpad) that the agent revisits again and
again, each milestone verified before the next begins. Kept as first-class,
owner+thread-scoped, restart-recoverable state, it composes with Alpha's durable
runtime rather than reimplementing any of it.

What this package is, and is not:

* It is **memory + a completion contract**. :mod:`.manager` owns durable,
  path-safe, atomic storage of a :class:`~.manager.MissionStack`;
  :mod:`.milestones` segments the objective into evidence-verified checkpoints
  with a structural stop-and-fix rule; :mod:`.scratchpad` holds bounded durable
  reasoning; :mod:`.anchor` projects it into the compact per-turn anchor; and
  :mod:`.verify` turns a measured :class:`EvidenceRecord` (collected by the
  caller, normally from :mod:`alpha.mission.acceptance`) into a milestone
  verdict -- ``UNVERIFIED`` when no record was measured.
* It is **not** a second lifecycle owner. It does not admit, cancel, dispatch or
  recover runs -- :func:`alpha.runtime.runs.manager.RunManager` stays the sole
  lifecycle owner and :func:`alpha.runtime.sessions` the sole state vocabulary.
* It is **not** a verifier of arbitrary natural-language criteria, and it does
  not run a command. A milestone's ``validation`` names a surface (a command, a
  file, a benchmark); who *runs* and *reads* that surface is the caller (the
  mission tool boundary, or an operator), which records a measured result here.
  This never infers an outcome from a model's summary.
* ``verified`` never means "the work was correct" -- only that the named check
  held. Broader correctness stays an overlay, exactly as acceptance is.

Deliberate non-goals:

* No cross-process exactly-once. Single-process JSON, restart-recoverable; the
  atomic write turns a crash into a clean prior state, never a torn one.
* No background loop. Nothing here ticks. The durable-runtime loops and APEX own
  scheduling; this package is the memory those loops dispatch *into*.
* No hard dependency on :mod:`alpha.mission.acceptance`. :mod:`.verify` consumes
  an evidence record by duck-type, so the caller imports the collectors lazily at
  its own boundary and this package never pulls in a cycle.
"""

from alpha.runtime.missions.anchor import (
    ANCHOR_HEADER,
    MISSION_NOTICE,
    render_anchor,
)
from alpha.runtime.missions.manager import (
    MAX_MISSION_FILE_BYTES,
    MissionManager,
    MissionStack,
)
from alpha.runtime.missions.milestones import (
    MAX_MILESTONES,
    InvalidMilestonePlan,
    Milestone,
    MilestonePlan,
    MilestoneStatus,
)
from alpha.runtime.missions.scratchpad import (
    DEFAULT_MAX_SCRATCH_ENTRIES,
    Scratchpad,
)
from alpha.runtime.missions.verify import (
    EvidenceLike,
    MilestoneVerdict,
    MilestoneVerification,
    verify_milestone,
)

__all__ = [
    "ANCHOR_HEADER",
    "DEFAULT_MAX_SCRATCH_ENTRIES",
    "EvidenceLike",
    "InvalidMilestonePlan",
    "MAX_MILESTONES",
    "MAX_MISSION_FILE_BYTES",
    "MISSION_NOTICE",
    "Milestone",
    "MissionManager",
    "MilestonePlan",
    "MilestoneStatus",
    "MilestoneVerification",
    "MilestoneVerdict",
    "MissionStack",
    "Scratchpad",
    "render_anchor",
    "verify_milestone",
]
