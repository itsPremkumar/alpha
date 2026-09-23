"""Tests for the RSI cycle budget controller (plan WP-D2, feature #21).

Honesty pins (plan §3 WP-D2 test plan + §5.6, binding):

* exceeding each limit raises with the **real** numbers — the message must
  contain both the actual and the limit (parametrized over all five kinds);
* ``CycleBudget.from_config`` rejects unknown keys (candidate-override pin):
  a candidate payload can never redefine a budget;
* ``remaining()`` is never negative (usage only grows; refused charges do not
  overwrite accepted usage);
* the wall-time budget flows through the injected module-level clock seam —
  deterministic, no sleeps;
* an exhaustion payload contains **no fabricated scores**: no
  ``score``/``confidence``/``pass`` fields anywhere — budget exhaustion
  aborts the cycle with ``budget_exhausted: <kind> <actual>/<limit>``, never
  a silent downgrade to a smaller "successful" run;
* ``workspace_usage`` is a measured du-style walk, not an estimate;
* the per-cycle budget record persists additively under the ``budget`` key of
  ``runtime_home()/rsi/state/cycle.json`` and round-trips; a state file
  without the key still loads (backward compatible).

Tests pin ``AGENT_WORKSPACE_HOME`` to ``tmp_path`` (autouse) so nothing here
touches the real runtime home.
"""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from alpha.rsi import budgets as budgets_module
from alpha.rsi.budgets import (
    BudgetExceeded,
    BudgetTracker,
    CycleBudget,
    exhaustion_reason,
    guard,
)
from alpha.rsi.models import RSIStage
from alpha.rsi.state import RsiCycleState, load_state, save_state, state_from_dict, state_path

# (kind, default limit under CycleBudget()) — every limit gets its own honesty pin.
LIMITS: list[tuple[str, int]] = [
    ("wall_time_s", 2700),
    ("candidate", 4),
    ("changed_file", 15),
    ("diff_line", 1200),
    ("workspace_mb", 1024),
]


@pytest.fixture(autouse=True)
def _isolated_workspace(tmp_path, monkeypatch):
    """Pin AGENT_WORKSPACE_HOME to a per-test temp dir (the env does not isolate it)."""
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))


class _FakeClock:
    """Deterministic stand-in for the module-level clock seam.

    Integer values are kept as integers so asserted figures stay exact
    (``11`` rather than ``11.0``) — same seam, no rounding surprises.
    """

    def __init__(self, now: float | int = 0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float | int) -> None:
        self.now += seconds


def _started(budget: CycleBudget | None = None) -> BudgetTracker:
    tracker = BudgetTracker()
    tracker.start(budget if budget is not None else CycleBudget())
    return tracker


# ---------------------------------------------------------------------------
# CycleBudget: spec §104 defaults, immutability, from_config validation pins
# ---------------------------------------------------------------------------


def test_cycle_budget_defaults_echo_spec_104():
    budget = CycleBudget()
    assert budget.max_wall_time_s == 2700
    assert budget.max_candidates == 4
    assert budget.max_changed_files == 15
    assert budget.max_diff_lines == 1200
    assert budget.max_workspace_mb == 1024


def test_cycle_budget_is_immutable_once_built():
    budget = CycleBudget()
    with pytest.raises(FrozenInstanceError):
        budget.max_candidates = 999  # type: ignore[misc]


def test_from_config_applies_known_fields_and_defaults():
    budget = CycleBudget.from_config({"max_candidates": 2, "max_diff_lines": 100})
    assert (budget.max_candidates, budget.max_diff_lines) == (2, 100)
    # untouched fields keep the spec §104 defaults
    assert (budget.max_wall_time_s, budget.max_changed_files, budget.max_workspace_mb) == (2700, 15, 1024)
    assert CycleBudget.from_config({}) == CycleBudget()
    assert CycleBudget.from_config(None) == CycleBudget()


def test_from_config_rejects_unknown_keys_candidate_override_pin():
    # Candidate-payload-shaped mappings have no code path into a budget.
    for payload in ({"score": 0.93}, {"confidence": 1.0}, {"auto_promote": True}, {"max_candidates": 4, "pass": True}):
        with pytest.raises(ValueError) as excinfo:
            CycleBudget.from_config(payload)
        assert "unknown budget config key" in str(excinfo.value)
    # the offending key is named in the error (real defect, not a generic refusal)
    with pytest.raises(ValueError, match="score"):
        CycleBudget.from_config({"score": 0.93})
    # a known key alone is accepted — only unknown keys are rejected
    assert CycleBudget.from_config({"max_candidates": 4}).max_candidates == 4
    # direct construction from a candidate payload also refuses unknown keys
    with pytest.raises(TypeError):
        CycleBudget(score=0.93)  # type: ignore[call-arg]
    # a raw mapping is not a budget (start() enforces the same seam)
    with pytest.raises(TypeError, match="must be a mapping|CycleBudget"):
        BudgetTracker().start({"max_candidates": 4})  # type: ignore[arg-type]


def test_from_config_rejects_invalid_values():
    for payload in ({"max_candidates": -1}, {"max_candidates": True}, {"max_wall_time_s": "soon"}, {"max_diff_lines": 1.5}, {"max_wall_time_s": float("inf")}):
        with pytest.raises(ValueError) as excinfo:
            CycleBudget.from_config(payload)
        assert "must be" in str(excinfo.value)


# ---------------------------------------------------------------------------
# charge(): exceeding each limit raises with real actual + limit figures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind,limit", LIMITS)
def test_exceeding_each_limit_raises_with_real_numbers(kind, limit):
    tracker = _started()  # defaults echo spec §104
    tracker.charge(kind, limit)  # exactly at the limit is allowed
    assert tracker.used[kind] == limit
    assert tracker.remaining()[kind] == 0
    with pytest.raises(BudgetExceeded) as excinfo:
        tracker.charge(kind, 1)
    exc = excinfo.value
    message = str(exc)
    # true figures: message contains BOTH the actual and the limit
    assert (exc.kind, exc.actual, exc.limit) == (kind, limit + 1, limit)
    assert str(exc.actual) in message
    assert str(exc.limit) in message
    assert message == f"{kind} {exc.actual} exceeds {exc.limit}"
    # plan §3 WP-D2 exact reason wording
    assert exc.reason == f"budget_exhausted: {kind} {exc.actual}/{exc.limit}"
    assert exhaustion_reason(kind, exc.actual, exc.limit) == exc.reason
    # a refused charge never drives usage or remaining negative
    assert tracker.used[kind] == limit
    assert tracker.remaining()[kind] == 0


def test_partial_charges_report_accumulated_actual():
    tracker = _started()
    tracker.charge("candidate", 3)
    with pytest.raises(BudgetExceeded) as excinfo:
        tracker.charge("candidate", 3)  # 3 accepted + 3 attempted = 6 of 4
    assert excinfo.value.actual == 6
    assert str(excinfo.value) == "candidate 6 exceeds 4"
    assert excinfo.value.reason == "budget_exhausted: candidate 6/4"
    assert tracker.used["candidate"] == 3  # accepted usage untouched by the refusal
    assert tracker.remaining()["candidate"] == 1


def test_remaining_never_negative():
    tracker = _started()
    fresh = tracker.remaining()
    assert set(fresh) == set(budgets_module.BUDGET_KINDS)
    for kind, limit in LIMITS:
        assert fresh[kind] == limit
    tracker.charge("diff_line", 1200)
    assert tracker.remaining()["diff_line"] == 0
    with pytest.raises(BudgetExceeded):
        tracker.charge("diff_line", 1)
    assert tracker.remaining()["diff_line"] == 0  # never -1
    for value in tracker.remaining().values():
        assert value >= 0


def test_charge_before_start_fails_loudly():
    tracker = BudgetTracker()
    with pytest.raises(RuntimeError, match="not started"):
        tracker.charge("candidate", 1)


def test_start_resets_usage_per_cycle():
    tracker = _started()
    tracker.charge("candidate", 4)
    with pytest.raises(BudgetExceeded):
        tracker.charge("candidate", 1)
    tracker.start(CycleBudget(max_candidates=8))  # a new cycle starts clean
    assert tracker.used["candidate"] == 0
    assert tracker.remaining()["candidate"] == 8


def test_charge_unknown_kind_rejected():
    tracker = _started()
    with pytest.raises(ValueError, match="unknown budget kind"):
        tracker.charge("score", 1)  # there is no "score" accounting at all
    assert "score" not in tracker.used


def test_charge_amount_validation():
    tracker = _started()
    with pytest.raises(TypeError, match="must be a number"):
        tracker.charge("candidate", "many")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="must be a number"):
        tracker.charge("candidate", True)  # a bool is not a quantity
    with pytest.raises(ValueError, match="non-negative"):
        tracker.charge("candidate", -1)
    with pytest.raises(ValueError, match="finite"):
        tracker.charge("candidate", float("inf"))


# ---------------------------------------------------------------------------
# Wall time: injected clock seam — deterministic, no sleeps
# ---------------------------------------------------------------------------


def test_wall_time_guard_uses_injected_clock_deterministically(monkeypatch):
    def run_scenario() -> list[str]:
        clock = _FakeClock(0)
        monkeypatch.setattr(budgets_module, "monotonic_now", clock)
        tracker = _started(CycleBudget(max_wall_time_s=10))
        observed: list[str] = []
        with guard(tracker):  # entry charges 0; clean exit charges 5
            clock.advance(5)
        observed.append(str(tracker.used["wall_time_s"]))
        clock.advance(5)  # total elapsed = 10 = limit (allowed, not exceeded)
        with guard(tracker):
            pass
        observed.append(str(tracker.used["wall_time_s"]))
        clock.advance(1)  # total elapsed = 11 > 10
        with pytest.raises(BudgetExceeded) as excinfo:
            with guard(tracker):
                pass
        exc = excinfo.value
        observed.append(str(exc))
        observed.append(exc.reason)
        return observed

    first = run_scenario()
    second = run_scenario()
    assert first == second  # fully deterministic — no wall-clock dependence
    assert first[0] == "5"
    assert first[1] == "10"  # within-budget wall time accumulates honestly
    assert first[2] == "wall_time_s 11 exceeds 10"  # true figures
    assert first[3] == "budget_exhausted: wall_time_s 11/10"
    # nothing outside the clock seam was consulted for time
    assert budgets_module.monotonic_now is not budgets_module.time.monotonic


def test_guard_does_not_mask_body_exceptions(monkeypatch):
    monkeypatch.setattr(budgets_module, "monotonic_now", _FakeClock(0))
    tracker = _started()
    with pytest.raises(RuntimeError, match="real failure"):
        with guard(tracker):
            raise RuntimeError("real failure")
    # a BudgetExceeded from inside the body propagates unchanged
    with pytest.raises(BudgetExceeded, match="candidate 5 exceeds 4"):
        with guard(tracker):
            tracker.charge("candidate", 5)


def test_guard_requires_tracker():
    with pytest.raises(TypeError, match="BudgetTracker"):
        with guard("not-a-tracker"):  # type: ignore[arg-type]
            pass


# ---------------------------------------------------------------------------
# workspace_usage: measured walk, never an estimate
# ---------------------------------------------------------------------------


def test_workspace_usage_is_measured_walk(tmp_path):
    workspace = tmp_path / "ws"
    (workspace / "nested").mkdir(parents=True)
    (workspace / "a.txt").write_bytes(b"x" * 1000)
    (workspace / "nested" / "b.bin").write_bytes(b"y" * 248)
    usage = BudgetTracker().workspace_usage(workspace)
    assert usage == pytest.approx((1000 + 248) / (1024 * 1024))
    empty = tmp_path / "empty"
    empty.mkdir()
    assert BudgetTracker().workspace_usage(empty) == 0.0  # measured zero
    with pytest.raises(FileNotFoundError):
        BudgetTracker().workspace_usage(tmp_path / "does-not-exist")  # honest failure, never a fabricated 0


def test_workspace_over_budget_aborts_with_measured_actual(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "big").write_bytes(b"z" * (1024 * 1024 + 512 * 1024))  # exactly 1.5 MiB
    tracker = _started(CycleBudget(max_workspace_mb=1))
    usage = tracker.workspace_usage(workspace)
    assert usage == 1.5
    with pytest.raises(BudgetExceeded) as excinfo:
        tracker.charge("workspace_mb", usage)
    exc = excinfo.value
    assert str(exc) == "workspace_mb 1.5 exceeds 1"
    assert exc.reason == "budget_exhausted: workspace_mb 1.5/1"


# ---------------------------------------------------------------------------
# Exhaustion payload: real reason, NO fabricated scores (honesty pin)
# ---------------------------------------------------------------------------


def test_budget_abort_record_has_no_fabricated_scores():
    tracker = _started()
    for _ in range(4):
        tracker.charge("candidate", 1)
    with pytest.raises(BudgetExceeded) as excinfo:
        tracker.charge("candidate", 1)
    exc = excinfo.value
    record = tracker.abort_record(exc, cycle_id="cycle-abort-1", stage=RSIStage.CANDIDATE_CREATED.value)
    # the cycle ABORTS with the real reason — never a disguised success
    assert record["reason"] == "budget_exhausted: candidate 5/4"
    assert str(record["reason"]).startswith("budget_exhausted:")
    assert record["exceeded"] == {"kind": "candidate", "actual": 5, "limit": 4}
    # exact payload shape: accounting figures only
    assert set(record) == {"reason", "exceeded", "limits", "used", "remaining", "cycle_id", "stage"}
    assert record["used"]["candidate"] == 4
    assert record["remaining"]["candidate"] == 0
    assert record["limits"]["candidate"] == 4
    text = json.dumps(record, sort_keys=True).lower()
    for banned in ("score", "confidence", "pass"):
        assert banned not in text, f"budget-abort payload must not contain {banned!r}: {text}"
    with pytest.raises(TypeError, match="BudgetExceeded"):
        tracker.abort_record({"reason": "budget_exhausted: candidate 5/4"})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# State integration: additive `budget` key in runtime_home()/rsi/state/cycle.json
# ---------------------------------------------------------------------------


def test_budget_snapshot_round_trips_through_cycle_state():
    tracker = _started()
    tracker.charge("candidate", 2)
    tracker.charge("diff_line", 120)
    snapshot = tracker.snapshot()
    assert set(snapshot) == {"limits", "used", "remaining"}
    state = RsiCycleState(
        cycle_id="cycle-budget-1",
        stage=RSIStage.CANDIDATE_CREATED.value,
        repo_commit="deadbeef",
        budget=snapshot,
    )
    path = save_state(state)
    assert path == state_path()  # same file A4 owns: runtime_home()/rsi/state/cycle.json
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["budget"] == snapshot  # additive key persisted...
    loaded, err = load_state()
    assert err is None
    assert loaded is not None
    assert loaded.budget == snapshot  # ...and round-trips
    text = json.dumps(raw["budget"], sort_keys=True).lower()
    for banned in ("score", "confidence", "pass"):
        assert banned not in text


def test_cycle_state_budget_key_optional_but_validated():
    base = {
        "cycle_id": "c1",
        "stage": RSIStage.IDLE.value,
        "repo_commit": "abc",
        "evaluator_manifest_sha": None,
        "remaining": [],
        "updated_at": 1.0,
    }
    # pre-WP-D2 state file without the budget key still loads (backward compatible)
    legacy = state_from_dict(base)
    assert legacy.budget is None
    with_budget = state_from_dict({**base, "budget": {"used": {"candidate": 1}}})
    assert with_budget.budget == {"used": {"candidate": 1}}
    # present-but-invalid budgets fail closed with the real defect
    with pytest.raises(ValueError, match="budget must be a JSON object"):
        state_from_dict({**base, "budget": [1, 2]})
    with pytest.raises(ValueError, match="budget keys must be strings"):
        state_from_dict({**base, "budget": {1: 2}})
