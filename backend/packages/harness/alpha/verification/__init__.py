"""``alpha.verification`` — the bounded verification controller.

Alpha already owned every component of an honest self-verification loop: the
verify tools named in ``FinishFirstVerifierMiddleware._VERIFY_TOOLS``, the
claim-binding acceptance checker in ``alpha.subagents.acceptance_checks``, the
budgeted non-looping retry, and the anti-thrash loop detector. What it did not
own was the thing that *connects* them. The middleware noticed a missing
verification and asked for one; nothing ran the tests, read the failure, fixed
it, re-ran, and decided. This package is that controller.

The seam the middleware owner wires is one call, in
:mod:`alpha.verification.integration`; the design note for it lives in that
module's docstring and in ``docs/VERIFICATION_LOOP.md``.

Three rules everything here is built around:

* **Evidence is real or it is nothing.** A
  :class:`~alpha.verification.execution.RecordedExecution` cannot be
  constructed by hand — it is sealed by a module-private token — and the
  production executor gets its evidence from the real ``bash`` tool and the real
  harvester. A synthesised transcript is not a degraded pass, it is a
  construction error.
* **A repair may not reduce coverage.** The test surface is captured before and
  after every repair and a reduced one is refused, leaving the loop UNVERIFIED.
  A self-repair loop that can be satisfied by deleting an assertion is worse
  than no loop, because it manufactures a green result.
* **Undecidable is UNVERIFIED, never a pass.** Three outcomes —
  :data:`~alpha.verification.contract.VERIFIED`,
  :data:`~alpha.verification.contract.FAILED`,
  :data:`~alpha.verification.contract.UNVERIFIED` — and the third is the default
  with a reason attached. A completed run is not a verified run.

What it cannot do is written down in ``docs/VERIFICATION_LOOP.md``, and it is
not short.
"""

from __future__ import annotations

from alpha.verification.budget import AttemptBudget
from alpha.verification.contract import (
    FAILED,
    UNVERIFIED,
    VERIFIED,
    LoopState,
    UnverifiedReason,
    VerificationOutcome,
    VerificationReport,
    render_report,
)
from alpha.verification.controller import (
    LoopDirective,
    RepairOutcome,
    VerificationController,
    render_verification_report,
)
from alpha.verification.execution import (
    BashToolExecutor,
    CommandExecutor,
    RecordedExecution,
    record_execution,
)
from alpha.verification.integration import (
    drive_verification,
    drive_verification_from_turn,
    infer_verification_command,
)
from alpha.verification.surface import (
    FileSurface,
    SurfaceComparison,
    TestSurface,
    capture_surface,
    compare_surfaces,
    surface_from_changes,
)

__all__ = [
    "FAILED",
    "UNVERIFIED",
    "VERIFIED",
    "AttemptBudget",
    "BashToolExecutor",
    "CommandExecutor",
    "FileSurface",
    "LoopDirective",
    "LoopState",
    "RecordedExecution",
    "RepairOutcome",
    "SurfaceComparison",
    "TestSurface",
    "UnverifiedReason",
    "VerificationController",
    "VerificationOutcome",
    "VerificationReport",
    "capture_surface",
    "compare_surfaces",
    "drive_verification",
    "drive_verification_from_turn",
    "infer_verification_command",
    "record_execution",
    "render_report",
    "render_verification_report",
    "surface_from_changes",
]
