"""Durable RSI cycle state + resume quarantine (plan WP-A4, feature #17).

Persists the RSI stage machine to ``runtime_home()/rsi/state/cycle.json``
through an atomic unique-tmp + ``os.replace`` write (``atomic_write_json``).

Honesty rules (plan §5.6):
* corrupt / missing / unreadable state returns ``(None, <real error note>)`` —
  ``resume()`` then refuses with ``quarantine: …`` carrying that real cause;
  a failed resume never fabricates prior cycle history;
* ``repo_commit`` comes from ``alpha.evolution.identity.get_runtime_identity()``
  and is honestly ``"unknown"`` (with the failure logged) when identity is
  unavailable — never invented;
* ``advance()`` to ``promoted`` / ``rolled_back`` mechanically requires the
  caller's ``evidence_kind == "measured"`` marker, so simulated evidence can
  never bypass evaluation (spec §101);
* WP-D2 additive ``budget`` key: an optional per-cycle budget record (real
  limits/used/remaining accounting from ``alpha.rsi.budgets`` — no score
  fields) persisted alongside this record; state files without the key load
  unchanged as ``budget=None`` (backward compatible, round-trips with it).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from alpha.config.runtime_paths import runtime_home
from alpha.rsi.models import RSIStage

logger = logging.getLogger(__name__)

STATE_RELATIVE_PATH = Path("rsi") / "state" / "cycle.json"

# Stages that constitute passing/undoing evaluation — gated on measured evidence.
_GATED_STAGES = frozenset({RSIStage.PROMOTED.value, RSIStage.ROLLED_BACK.value})


def state_path() -> Path:
    """Absolute path of the persisted cycle state file."""
    return runtime_home() / STATE_RELATIVE_PATH


def current_repo_commit() -> str:
    """Repo commit for state records — honest ``"unknown"`` when identity fails."""
    try:
        # Local import: the identity chain (manifest/update state) stays out of
        # import time so this module can never break ordinary Alpha startup.
        from alpha.evolution.identity import get_runtime_identity

        commit = get_runtime_identity().get("gitCommit")
    except Exception as exc:
        logger.warning("RSI state could not read the runtime identity (%s); recording repo_commit='unknown'", exc)
        return "unknown"
    if isinstance(commit, str) and commit.strip():
        return commit
    return "unknown"


def fresh_manifest_sha() -> tuple[str | None, str | None]:
    """Fresh evaluator-manifest digest for resume checks (WP-A2 seam).

    Returns ``(sha256:<hex> | None, error | None)``. Wave-mate A2's
    ``alpha.rsi.evaluator_manifest`` may not have landed yet: its absence is
    reported as an honest error plus ``None`` — never a fabricated digest. The
    digest is ``sha256`` over the canonical JSON of the manifest A2 actually
    built (only when that manifest reports ``state == "complete"``).
    """
    try:
        from alpha.rsi import evaluator_manifest
    except ImportError as exc:
        return None, f"evaluator manifest builder unavailable: {exc}"
    builder = getattr(evaluator_manifest, "build_manifest", None)
    if not callable(builder):
        return None, "evaluator manifest builder unavailable: alpha.rsi.evaluator_manifest.build_manifest() is missing"
    try:
        manifest = builder()
    except Exception as exc:
        return None, f"evaluator manifest build failed: {exc}"
    if not isinstance(manifest, dict):
        return None, f"evaluator manifest build returned {type(manifest).__name__}, expected dict"
    if manifest.get("state") != "complete":
        return None, f"evaluator manifest incomplete: missing={manifest.get('missing')!r}"
    canonical = json.dumps({key: manifest[key] for key in ("version", "files", "suite_versions") if key in manifest}, sort_keys=True)
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}", None


@dataclass
class RsiCycleState:
    """One durable RSI cycle record (plan §3 WP-A4)."""

    cycle_id: str = field(default_factory=lambda: f"cycle-{uuid.uuid4().hex[:12]}")
    stage: str = RSIStage.IDLE.value
    repo_commit: str = "unknown"
    evaluator_manifest_sha: str | None = None
    remaining: list[str] = field(default_factory=list)
    updated_at: float = 0.0
    # WP-D2 additive key (plan §3 WP-D2, coordinate D2-appends-only): per-cycle
    # budget record (real limits/used/remaining figures from
    # ``alpha.rsi.budgets.BudgetTracker.snapshot()``, never scores). Optional:
    # state files written before WP-D2 lack it and still load as ``None``.
    budget: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def advance(self, stage: str, *, evidence_kind: str | None = None) -> None:
        """Move to ``stage`` (validated against ``RSIStage``) and stamp ``updated_at``.

        ``promoted`` / ``rolled_back`` additionally require the caller's
        ``evidence_kind == "measured"`` marker — simulated or missing evidence
        raises ``ValueError`` and leaves the stage untouched (fail-closed:
        the evaluation arrow can never be bypassed, spec §101).
        """
        try:
            target = RSIStage(stage)
        except (TypeError, ValueError):
            valid = ", ".join(s.value for s in RSIStage)
            raise ValueError(f"unknown RSI stage {stage!r}; valid stages: {valid}") from None
        if target.value in _GATED_STAGES and evidence_kind != "measured":
            raise ValueError(
                f"stage {target.value!r} requires evidence_kind='measured' (got {evidence_kind!r}); "
                "simulated or unverified evidence cannot advance past evaluation (spec §101)"
            )
        self.stage = target.value
        self.updated_at = time.time()


def new_state(*, evaluator_manifest_sha: str | None = None, remaining: list[str] | None = None) -> RsiCycleState:
    """Build a fresh ``IDLE`` state recording the current commit and manifest digest.

    When ``evaluator_manifest_sha`` is not supplied it is taken from
    ``fresh_manifest_sha()``; if the (possibly not-yet-landed) builder is
    unavailable the field stays honestly ``None`` with the real error logged.
    """
    sha = evaluator_manifest_sha
    if sha is None:
        sha, err = fresh_manifest_sha()
        if err is not None:
            logger.warning("RSI state has no evaluator manifest digest: %s", err)
    return RsiCycleState(
        stage=RSIStage.IDLE.value,
        repo_commit=current_repo_commit(),
        evaluator_manifest_sha=sha,
        remaining=list(remaining or []),
        updated_at=time.time(),
    )


def save_state(state: RsiCycleState) -> Path:
    """Atomically persist ``state`` (unique tmp file + ``os.replace``) and return its path."""
    if not isinstance(state, RsiCycleState):
        raise TypeError(f"save_state expects an RsiCycleState, got {type(state).__name__}")
    try:
        RSIStage(state.stage)
    except (TypeError, ValueError):
        raise ValueError(f"refusing to persist unknown RSI stage {state.stage!r}") from None
    state.updated_at = time.time()
    # Local import: keeps the identity chain out of import time (§5.9 startup safety).
    from alpha.evolution.identity import atomic_write_json

    path = state_path()
    atomic_write_json(path, state.to_dict())
    return path


def state_from_dict(payload: Any) -> RsiCycleState:
    """Strictly validate a persisted payload; raises ``ValueError`` naming the real defect."""
    if not isinstance(payload, dict):
        raise ValueError(f"state payload must be a JSON object, got {type(payload).__name__}")
    required = ("cycle_id", "stage", "repo_commit", "evaluator_manifest_sha", "remaining", "updated_at")
    missing = [key for key in required if key not in payload]
    if missing:
        raise ValueError(f"state payload missing field(s): {', '.join(missing)}")
    cycle_id = payload["cycle_id"]
    if not isinstance(cycle_id, str) or not cycle_id:
        raise ValueError("cycle_id must be a non-empty string")
    stage = payload["stage"]
    try:
        RSIStage(stage)
    except (TypeError, ValueError):
        raise ValueError(f"unknown RSI stage {stage!r} in persisted state") from None
    if not isinstance(payload["repo_commit"], str):
        raise ValueError("repo_commit must be a string")
    sha = payload["evaluator_manifest_sha"]
    if sha is not None and not isinstance(sha, str):
        raise ValueError("evaluator_manifest_sha must be a string or null")
    remaining = payload["remaining"]
    if not isinstance(remaining, list) or not all(isinstance(item, str) for item in remaining):
        raise ValueError("remaining must be a list of strings")
    updated_at = payload["updated_at"]
    if isinstance(updated_at, bool) or not isinstance(updated_at, (int, float)):
        raise ValueError("updated_at must be a number")
    # WP-D2 additive budget key: optional (absent -> None, pre-D2 files load
    # unchanged); when present it must be a JSON object with string keys.
    budget = payload.get("budget")
    if budget is not None:
        if not isinstance(budget, dict):
            raise ValueError(f"budget must be a JSON object when present, got {type(budget).__name__}")
        if not all(isinstance(key, str) for key in budget):
            raise ValueError("budget keys must be strings")
    return RsiCycleState(
        cycle_id=cycle_id,
        stage=str(stage),
        repo_commit=payload["repo_commit"],
        evaluator_manifest_sha=sha,
        remaining=list(remaining),
        updated_at=float(updated_at),
        budget=dict(budget) if budget is not None else None,
    )


def load_state() -> tuple[RsiCycleState | None, str | None]:
    """Load the persisted cycle state.

    Returns ``(state, None)`` on success and ``(None, <real error note>)`` for
    missing, unreadable or corrupt state — the caller decides fail-closed
    (``resume()`` quarantines). This function never invents a clean state.
    """
    path = state_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, f"no saved RSI cycle state at {path}"
    except OSError as exc:
        return None, f"unreadable RSI cycle state at {path}: {exc}"
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        return None, f"corrupt RSI cycle state at {path}: {exc}"
    try:
        return state_from_dict(payload), None
    except (TypeError, ValueError) as exc:
        return None, f"corrupt RSI cycle state at {path}: {exc}"


def resume() -> tuple[bool, str]:
    """Re-open the saved cycle only while repo commit + evaluator manifest still match.

    Every failure path returns ``(False, "quarantine: …")`` with the real
    reason. A manifest digest that was never recorded (A2 builder unavailable
    at save time) is disclosed as *unverified* rather than claimed as a match.
    """
    state, err = load_state()
    if state is None:
        return False, f"quarantine: cannot resume RSI cycle ({err})"

    current_commit = current_repo_commit()
    if state.repo_commit != current_commit:
        return False, (
            f"quarantine: repo commit changed for cycle {state.cycle_id} "
            f"(recorded {state.repo_commit}, current {current_commit})"
        )

    fresh_sha, sha_err = fresh_manifest_sha()
    if state.evaluator_manifest_sha is not None:
        if fresh_sha is None:
            return False, f"quarantine: evaluator manifest for cycle {state.cycle_id} could not be re-verified ({sha_err})"
        if fresh_sha != state.evaluator_manifest_sha:
            return False, (
                f"quarantine: evaluator manifest changed for cycle {state.cycle_id} "
                f"(recorded {state.evaluator_manifest_sha}, current {fresh_sha})"
            )
        return True, f"resume ok: cycle {state.cycle_id} at stage {state.stage}; repo commit and evaluator manifest match"

    # No digest was recorded at save time — disclose the gap, never paper over it.
    if fresh_sha is None:
        return True, (
            f"resume ok: cycle {state.cycle_id} at stage {state.stage}; repo commit matches; "
            f"evaluator manifest integrity UNVERIFIED ({sha_err})"
        )
    return True, (
        f"resume ok: cycle {state.cycle_id} at stage {state.stage}; repo commit matches; "
        f"evaluator manifest digest was not recorded at save time (current {fresh_sha} unverified against history)"
    )


# Backward-compatibility aliases
load_cycle_state = load_state
save_cycle_state = save_state

