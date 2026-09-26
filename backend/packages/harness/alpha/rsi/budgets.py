"""RSI cycle budget controller (plan WP-D2, feature #21).

Hard per-cycle resource budgets that candidate payloads can never override:

* Budget *values* come only from :class:`CycleBudget` defaults (spec §104) or
  an explicit operator config mapping through ``CycleBudget.from_config`` —
  that classmethod accepts **only** the five known fields and rejects every
  other key, so a candidate payload (``score``, ``confidence``, ``auto_promote``,
  ...) has no code path into a budget (enforcement pin, plan §3 WP-D2). No
  config-schema field is added either: unknown keys are refused loudly rather
  than absorbed (a schema field would require a ``config_version`` bump).
* :class:`BudgetTracker` accounts *measured* consumption only: real
  :meth:`BudgetTracker.charge` amounts and a real du-style filesystem walk
  (:meth:`BudgetTracker.workspace_usage`) — never estimates or projections.
* The wall-time budget is read through the module-level clock seam
  :func:`monotonic_now` (production default :func:`time.monotonic`); tests
  stub the seam instead of sleeping, so wall-time tests are deterministic.
* Exhaustion **aborts** the cycle: :class:`BudgetExceeded` and the plan's
  exact reason wording ``budget_exhausted: <which> <actual>/<limit>`` carry
  the true figures and nothing else. The abort record deliberately contains
  no ``score``/``confidence``/``pass`` fields (honesty pin, plan §5.6): a
  budget abort is a refusal with real numbers, never a silent downgrade to a
  smaller "successful" run.

Persistence is additive and stateless from this module's side: the per-cycle
record travels under the optional ``budget`` key of
``runtime_home()/rsi/state/cycle.json`` (see :mod:`alpha.rsi.state`); this
module holds no global state.

Kind vocabulary (the ``charge(kind, amount)`` names; each maps to exactly one
``CycleBudget`` field and nothing else is accepted):

=================  ====================
kind               budget field
=================  ====================
``wall_time_s``    ``max_wall_time_s``
``candidate``      ``max_candidates``
``changed_file``   ``max_changed_files``
``diff_line``      ``max_diff_lines``
``workspace_mb``   ``max_workspace_mb``
=================  ====================
"""

from __future__ import annotations

import contextlib
import logging
import math
import os
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, fields
from pathlib import Path

logger = logging.getLogger(__name__)

# Ordered kind -> CycleBudget field mapping. Order is fixed so dict rebuilds
# (used/remaining/snapshot) are deterministic.
_KIND_FIELD: dict[str, str] = {
    "wall_time_s": "max_wall_time_s",
    "candidate": "max_candidates",
    "changed_file": "max_changed_files",
    "diff_line": "max_diff_lines",
    "workspace_mb": "max_workspace_mb",
}
_KINDS: tuple[str, ...] = tuple(_KIND_FIELD)
BUDGET_KINDS = frozenset(_KINDS)

_MIB = 1024 * 1024  # "MB" here is the spec §104 budget unit: bytes / 1024^2


def monotonic_now() -> float:
    """Injected clock seam for wall-time accounting (plan WP-D2).

    Production default is :func:`time.monotonic`. Deterministic tests stub this
    module-level function (``monkeypatch.setattr(budgets, "monotonic_now", fake)``)
    instead of sleeping; every wall-time figure flows through this seam.
    """
    return time.monotonic()


def exhaustion_reason(kind: str, actual: float, limit: float) -> str:
    """Plan §3 WP-D2 exact wording: ``budget_exhausted: <which> <actual>/<limit>``.

    Both figures are the true numbers of the refusal — never rounded down,
    never replaced by a neutral placeholder.
    """
    return f"budget_exhausted: {kind} {actual}/{limit}"


class BudgetExceeded(RuntimeError):
    """A charge would push measured usage past a hard per-cycle limit.

    The message is the plan's ``f"{kind} {amount} exceeds {limit}"`` with the
    true figures; :attr:`actual` is the usage the charge *would* have reached
    (accepted usage is not overwritten by a refused charge).
    """

    def __init__(self, kind: str, actual: float, limit: float) -> None:
        self.kind = kind
        self.actual = actual
        self.limit = limit
        super().__init__(f"{kind} {actual} exceeds {limit}")

    @property
    def reason(self) -> str:
        """The abort reason recorded for the cycle (:func:`exhaustion_reason`)."""
        return exhaustion_reason(self.kind, self.actual, self.limit)


def _validate_budget_values(budget: CycleBudget) -> None:
    """Value validation shared by the constructor and ``from_config``."""
    for name in ("max_candidates", "max_changed_files", "max_diff_lines", "max_workspace_mb"):
        value = getattr(budget, name)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{name} must be a non-negative int, got {value!r}")
        if value < 0:
            raise ValueError(f"{name} must be non-negative, got {value!r}")
    wall = budget.max_wall_time_s
    if isinstance(wall, bool) or not isinstance(wall, (int, float)):
        raise ValueError(f"max_wall_time_s must be a number, got {wall!r}")
    if not math.isfinite(wall) or wall < 0:
        raise ValueError(f"max_wall_time_s must be a finite non-negative number, got {wall!r}")


@dataclass(frozen=True)
class CycleBudget:
    """Immutable hard per-cycle limits (defaults echo spec §104).

    Frozen so a built budget cannot be mutated in place; overridable only by
    the operator through :meth:`from_config`. This module never reads a
    candidate payload — see module docstring for the enforcement pin.
    """

    max_wall_time_s: float = 2700
    max_candidates: int = 4
    max_changed_files: int = 15
    max_diff_lines: int = 1200
    max_workspace_mb: int = 1024

    def __post_init__(self) -> None:
        _validate_budget_values(self)

    @classmethod
    def from_config(cls, mapping: Mapping[str, object] | None) -> CycleBudget:
        """Build a budget from an explicit operator config mapping.

        Accepts **only** the five known fields; every other key raises
        ``ValueError`` naming it — a candidate payload can never smuggle a
        budget override (or a fake ``score`` field) past this seam. Values are
        validated by :meth:`__post_init__`. ``None``/empty mapping means
        defaults.
        """
        if mapping is None:
            return cls()
        if not isinstance(mapping, Mapping):
            raise TypeError(f"budget config must be a mapping, got {type(mapping).__name__}")
        known = {f.name for f in fields(cls)}
        unknown = [key for key in mapping if not isinstance(key, str) or key not in known]
        if unknown:
            names = ", ".join(sorted(repr(key) for key in unknown))
            known_names = ", ".join(sorted(known))
            raise ValueError(
                f"unknown budget config key(s): {names}; known keys: {known_names} "
                "(budgets are operator-config only; candidate payload keys are never accepted)"
            )
        return cls(**{key: mapping[key] for key in mapping})  # type: ignore[arg-type]


class BudgetTracker:
    """Accounts one cycle's usage against a :class:`CycleBudget`.

    Usage only ever grows (a refused charge does not overwrite accepted
    usage), so ``remaining()`` can never go negative. Call :meth:`start` once
    per cycle; it resets all counters.
    """

    def __init__(self) -> None:
        self.budget: CycleBudget | None = None
        self.used: dict[str, float] = {kind: 0 for kind in _KINDS}
        self._wall_mark: float | None = None

    def start(self, budget: CycleBudget) -> CycleBudget:
        """Begin a cycle under ``budget``, resetting every counter."""
        if not isinstance(budget, CycleBudget):
            raise TypeError(
                f"start() requires a CycleBudget built from operator config, got {type(budget).__name__} "
                "(raw mappings/candidate payloads are not budgets)"
            )
        self.budget = budget
        self.used = {kind: 0 for kind in _KINDS}
        self._wall_mark = monotonic_now()
        return budget

    def _require_started(self) -> CycleBudget:
        if self.budget is None:
            raise RuntimeError("budget tracker not started; call start(budget) first")
        return self.budget

    def limit_for(self, kind: str) -> float:
        """Configured limit for ``kind`` (validates kind + started state)."""
        budget = self._require_started()
        field = _KIND_FIELD.get(kind)
        if field is None:
            raise ValueError(f"unknown budget kind {kind!r}; known kinds: {', '.join(_KINDS)}")
        return getattr(budget, field)

    def charge(self, kind: str, amount: float) -> None:
        """Record ``amount`` additional measured consumption of ``kind``.

        Raises :class:`BudgetExceeded` *before* recording when the charge would
        pass the limit — the message and :attr:`BudgetExceeded.reason` carry
        the true attempted total and the true limit. All amounts are
        incremental (never cumulative totals); wall time normally flows through
        :meth:`charge_wall_time` / :func:`guard`.
        """
        if isinstance(amount, bool) or not isinstance(amount, (int, float)):
            raise TypeError(f"charge amount for {kind!r} must be a number, got {amount!r}")
        if not math.isfinite(amount) or amount < 0:
            raise ValueError(f"charge amount for {kind!r} must be a finite non-negative number, got {amount!r}")
        limit = self.limit_for(kind)
        actual = self.used[kind] + amount
        if actual > limit:
            raise BudgetExceeded(kind, actual, limit)
        self.used[kind] = actual

    def charge_wall_time(self) -> float:
        """Charge wall time elapsed since start / previous mark through the clock seam.

        Returns the charged delta (0 when the clock did not advance). This is
        the only automatic budget; every other kind is charged explicitly by
        the factory/evaluation loops (plan §3 WP-D2 integration).
        """
        self._require_started()
        now = monotonic_now()
        mark = now if self._wall_mark is None else self._wall_mark
        self._wall_mark = now
        delta = now - mark
        if delta > 0:
            self.charge("wall_time_s", delta)
        return delta

    def remaining(self) -> dict[str, float]:
        """Remaining allowance per kind — clamped at 0, never negative."""
        budget = self._require_started()
        return {
            kind: max(0, getattr(budget, _KIND_FIELD[kind]) - self.used[kind])
            for kind in _KINDS
        }

    def snapshot(self) -> dict[str, dict[str, float]]:
        """Persistable per-cycle record: real ``limits``/``used``/``remaining`` only.

        Safe for the ``budget`` key of ``runtime_home()/rsi/state/cycle.json``.
        Deliberately contains no score/confidence/pass-like fields: a budget
        record is accounting, never an evaluation result.
        """
        budget = self._require_started()
        return {
            "limits": {kind: getattr(budget, _KIND_FIELD[kind]) for kind in _KINDS},
            "used": dict(self.used),
            "remaining": self.remaining(),
        }

    def abort_record(
        self,
        exc: BudgetExceeded,
        *,
        cycle_id: str | None = None,
        stage: str | None = None,
    ) -> dict[str, object]:
        """Honest budget-abort payload for the cycle record.

        ``reason`` is the plan's ``budget_exhausted: <kind> <actual>/<limit>``
        with real numbers; the record carries accounting figures only — no
        ``score``/``confidence``/``pass`` fields, no implied success
        (honesty pin, plan §3 WP-D2 test plan).
        """
        if not isinstance(exc, BudgetExceeded):
            raise TypeError(f"abort_record expects a BudgetExceeded, got {type(exc).__name__}")
        self._require_started()
        record: dict[str, object] = {
            "reason": exc.reason,
            "exceeded": {"kind": exc.kind, "actual": exc.actual, "limit": exc.limit},
            **self.snapshot(),
        }
        if cycle_id is not None:
            record["cycle_id"] = cycle_id
        if stage is not None:
            record["stage"] = stage
        return record

    def workspace_usage(self, path: str | os.PathLike[str]) -> float:
        """Measured size of ``path`` in MB (bytes / 1024²) — a real du-style walk.

        Symlinks are skipped (not followed: no double counting, no escaping
        the measured tree); unreadable entries are skipped with a warning that
        states the count — the returned figure is what was actually measured,
        never an estimate, and a missing path raises ``FileNotFoundError``
        rather than reporting a fabricated 0.
        """
        target = Path(path)
        if not target.exists():
            raise FileNotFoundError(f"workspace usage path does not exist: {target}")
        total = 0
        unreadable = 0

        def _onerror(_err: OSError) -> None:
            nonlocal unreadable
            unreadable += 1

        for root, dirs, files in os.walk(target, followlinks=False, onerror=_onerror):
            dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
            for name in files:
                file_path = os.path.join(root, name)
                if os.path.islink(file_path):
                    continue
                try:
                    total += os.path.getsize(file_path)
                except OSError:
                    unreadable += 1
        if unreadable:
            logger.warning(
                "workspace_usage(%s): %d entrie(s) unreadable — measured total excludes them "
                "(symlinked entries are also excluded by design and not counted; no estimate substituted)",
                target,
                unreadable,
            )
        return total / _MIB


@contextlib.contextmanager
def guard(tracker: BudgetTracker) -> Iterator[None]:
    """Context helper for factory/evaluation loops (plan §3 WP-D2).

    Charges wall time (through the injected clock seam) on entry and again on
    clean exit, so a loop that overruns ``max_wall_time_s`` aborts with the
    real figures even if it never calls :meth:`BudgetTracker.charge` itself.
    :class:`BudgetExceeded` raised inside or by the guard propagates unchanged;
    any other in-flight exception is never masked by a wall-time check.
    """
    if not isinstance(tracker, BudgetTracker):
        raise TypeError(f"guard() requires a BudgetTracker, got {type(tracker).__name__}")
    tracker.charge_wall_time()
    try:
        yield
    except BaseException:
        raise  # never mask an in-flight failure; wall time is re-checked on the next guard entry
    else:
        tracker.charge_wall_time()
