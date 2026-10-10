"""Durable workflow triggers: schedules as data, fired by a host, never a cron loop.

Covers the three honesty properties the module exists to guarantee:

* an unrunnable schedule is refused at creation (unparsable, reversed,
  out-of-range, unsatisfiable), so no stored trigger can silently never fire;
* the Vixie day-of-month/day-of-week rule (restricted both → either matches)
  is implemented, because the AND misreading is the classic cron bug;
* firing starts a run through an injected engine and only then advances the
  schedule, so a refused start leaves the trigger due instead of consuming a
  fire — and ``fire_count``/``max_fires`` are durable totals.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from alpha.workflow.triggers import (
    TriggerFireResult,
    TriggerKind,
    TriggerStore,
    TriggerStoreError,
    WorkflowTrigger,
    cron_next_after,
    fire_trigger_on_engine,
    parse_cron_expression,
)


def _epoch(text: str) -> float:
    from datetime import UTC, datetime

    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC).timestamp()


# --------------------------------------------------------------------- cron parsing


def test_parse_accepts_every_documented_shape():
    minutes, hours, days, months, dows = parse_cron_expression("*/15 9-17/2 1,15 * 0-4")
    assert minutes == frozenset({0, 15, 30, 45})
    assert hours == frozenset({9, 11, 13, 15, 17})
    assert days == frozenset({1, 15})
    assert months == frozenset(range(1, 13))
    assert dows == frozenset({0, 1, 2, 3, 4})


def test_parse_normalizes_sunday_seven_to_zero():
    _, _, _, _, dows = parse_cron_expression("0 0 * * 7")
    assert dows == frozenset({0})


@pytest.mark.parametrize(
    "expression",
    [
        "",
        "0 9 * *",
        "0 9 * * * *",
        "60 9 * * *",
        "0 24 * * *",
        "0 9 0 * *",
        "0 9 * 13 *",
        "0 9 * * 8",
        "0 9 * * mon",
        "*/0 9 * * *",
        "9-5 9 * * *",
        "0 9 , * *",
        "0 9 a-b * *",
    ],
)
def test_parse_refuses_malformed_expressions_naming_nothing_silently(expression):
    with pytest.raises(ValueError):
        parse_cron_expression(expression)


def test_next_after_walks_to_the_declared_minute():
    # 2026-10-10 is a Saturday.
    base = _epoch("2026-10-10T08:30:00Z")
    assert cron_next_after("0 9 * * *", base) == _epoch("2026-10-10T09:00:00Z")


def test_next_after_honors_step_from_the_current_minute():
    base = _epoch("2026-10-10T08:31:00Z")
    assert cron_next_after("*/15 * * * *", base) == _epoch("2026-10-10T08:45:00Z")


def test_day_of_month_and_day_of_week_are_or_not_and():
    """``0 9 1 * 1`` means 09:00 on the 1st OR on Mondays (Vixie cron).

    The AND misreading fires only when the 1st is a Monday, which for the
    horizon below would skip 2026-10-12 entirely and land in November.
    """
    expression = "0 9 1 * 1"
    saturday_1000 = _epoch("2026-10-10T10:00:00Z")
    assert cron_next_after(expression, saturday_1000) == _epoch("2026-10-12T09:00:00Z")


def test_unsatisfiable_expression_returns_none():
    assert cron_next_after("0 0 30 2 *", _epoch("2026-10-10T00:00:00Z")) is None


# --------------------------------------------------------------------- the store


def _store(tmp_path: Path) -> TriggerStore:
    return TriggerStore(tmp_path / "workflow_store")


def _cron_trigger(**overrides) -> WorkflowTrigger:
    now = time.time()
    fields = {
        "trigger_id": "trg_1",
        "workflow_id": "wf_1",
        "owner_id": "owner-1",
        "kind": TriggerKind.CRON,
        "expression": "0 9 * * *",
        "next_fire_at": now + 3600.0,
    }
    fields.update(overrides)
    return WorkflowTrigger(**fields)


def test_add_persists_and_reloads(tmp_path: Path):
    store = _store(tmp_path)
    store.add(_cron_trigger())
    reloaded = TriggerStore(tmp_path / "workflow_store")
    assert reloaded.get("trg_1") is not None
    assert reloaded.get("trg_1").expression == "0 9 * * *"


def test_add_refuses_duplicate_id(tmp_path: Path):
    store = _store(tmp_path)
    store.add(_cron_trigger())
    with pytest.raises(ValueError, match="already exists"):
        store.add(_cron_trigger())


def test_add_refuses_unsatisfiable_cron_before_storing(tmp_path: Path):
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="never fire"):
        store.add(_cron_trigger(expression="0 0 30 2 *"))
    assert store.list() == []


def test_add_refuses_interval_outside_bounds(tmp_path: Path):
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="interval_seconds must be between"):
        store.add(_cron_trigger(kind=TriggerKind.INTERVAL, expression=None, interval_seconds=1.0, next_fire_at=None))
    with pytest.raises(ValueError, match="interval_seconds must be between"):
        store.add(_cron_trigger(kind=TriggerKind.INTERVAL, expression=None, interval_seconds=10**9, next_fire_at=None))


def test_add_refuses_event_trigger_without_event_name(tmp_path: Path):
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="event_name"):
        store.add(_cron_trigger(kind=TriggerKind.EVENT, expression=None, next_fire_at=None, event_name="  "))


def test_list_is_owner_scoped(tmp_path: Path):
    store = _store(tmp_path)
    store.add(_cron_trigger(trigger_id="mine", owner_id="owner-1"))
    store.add(_cron_trigger(trigger_id="theirs", owner_id="owner-2"))
    assert [t.trigger_id for t in store.list(owner_id="owner-1")] == ["mine"]
    assert len(store.list()) == 2  # unscoped administrative read


def test_due_triggers_skips_event_triggers_and_respects_the_clock(tmp_path: Path):
    store = _store(tmp_path)
    now = time.time()
    store.add(_cron_trigger(trigger_id="due", next_fire_at=now - 1.0))
    store.add(_cron_trigger(trigger_id="future", next_fire_at=now + 600.0))
    store.add(_cron_trigger(trigger_id="event", kind=TriggerKind.EVENT, expression=None, next_fire_at=None, event_name="pr.opened"))
    due = store.due_triggers(now)
    assert [t.trigger_id for t in due] == ["due"]


def test_due_triggers_stops_reporting_a_max_fires_schedule(tmp_path: Path):
    store = _store(tmp_path)
    store.add(_cron_trigger(trigger_id="capped", next_fire_at=time.time() - 1.0, max_fires=1))
    store.record_fire("capped")
    capped = store.get("capped")
    assert capped.fire_count == 1
    assert capped.enabled is False
    assert "max_fires" in (capped.disabled_reason or "")
    assert store.due_triggers(time.time()) == []


def test_record_fire_advances_cron_from_the_due_time_not_now(tmp_path: Path):
    store = _store(tmp_path)
    base = _epoch("2026-10-10T08:00:00Z")
    store.add(_cron_trigger(trigger_id="daily", expression="0 9 * * *", next_fire_at=base))
    store.record_fire("daily", fired_at=base)
    advanced = store.get("daily")
    # Firing late must not push the next fire out by the lateness, and must
    # not reset it to "now" either: the daily 09:00 schedule survives.
    assert advanced.next_fire_at == _epoch("2026-10-10T09:00:00Z")
    assert advanced.fire_count == 1


def test_record_fire_computes_the_next_rare_cron_fire(tmp_path: Path):
    store = _store(tmp_path)
    base = _epoch("2026-10-10T08:00:00Z")
    store.add(_cron_trigger(trigger_id="rare", expression="0 9 29 2 *", next_fire_at=base))
    store.record_fire("rare", fired_at=base)
    # The next leap day after the base is 2028-02-29: a schedule rarer than
    # yearly must still resolve a real next fire rather than being refused or
    # parked, which is what the month/day-skipping walk buys.
    advanced = store.get("rare")
    assert advanced.next_fire_at == _epoch("2028-02-29T09:00:00Z")
    assert advanced.enabled is True


def test_corrupt_store_fails_closed(tmp_path: Path):
    root = tmp_path / "workflow_store"
    root.mkdir(parents=True)
    (root / "triggers.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(TriggerStoreError, match="not valid JSON"):
        TriggerStore(root)


def test_absent_store_is_not_degraded(tmp_path: Path):
    store = _store(tmp_path)
    assert store.list() == []
    assert (tmp_path / "workflow_store" / "triggers.json").exists() is False


# --------------------------------------------------------------------- firing


class _StubEngine:
    def __init__(self, *, known: set[str] | None = None, boom: bool = False):
        self.known = known if known is not None else set()
        self.boom = boom
        self.calls: list[tuple[str, str | None]] = []

    def start_run(self, workflow_id, *, initial_state=None, owner_id=None):  # noqa: ANN001
        self.calls.append((workflow_id, owner_id))
        if self.boom:
            raise RuntimeError("engine refused")
        if workflow_id not in self.known:
            raise KeyError(f"Workflow definition '{workflow_id}' not found.")
        return type("Run", (), {"run_id": "run_stub"})()


def test_fire_starts_the_run_before_advancing_the_schedule(tmp_path: Path):
    store = _store(tmp_path)
    store.add(_cron_trigger(next_fire_at=time.time() - 1.0))
    engine = _StubEngine(known={"wf_1"})
    result = fire_trigger_on_engine(engine, store, "trg_1")
    assert isinstance(result, TriggerFireResult)
    assert result.fired is True
    assert result.run_id == "run_stub"
    assert engine.calls == [("wf_1", "owner-1")]
    assert store.get("trg_1").fire_count == 1


def test_fire_refusal_does_not_consume_the_schedule(tmp_path: Path):
    store = _store(tmp_path)
    store.add(_cron_trigger(next_fire_at=time.time() - 1.0))
    engine = _StubEngine(known=set())  # workflow not registered
    result = fire_trigger_on_engine(engine, store, "trg_1")
    assert result.fired is False
    assert "not registered" in result.reason
    assert store.get("trg_1").fire_count == 0
    assert store.get("trg_1").enabled is True
    # The schedule is still due: a refused fire must leave the work pending,
    # not consume it.
    assert [t.trigger_id for t in store.due_triggers(time.time() + 1.0)] == ["trg_1"]


def test_fire_refusal_when_the_engine_raises(tmp_path: Path):
    store = _store(tmp_path)
    store.add(_cron_trigger(next_fire_at=time.time() - 1.0))
    result = fire_trigger_on_engine(_StubEngine(known={"wf_1"}, boom=True), store, "trg_1")
    assert result.fired is False
    assert "RuntimeError" in result.reason


def test_fire_refuses_a_disabled_trigger(tmp_path: Path):
    store = _store(tmp_path)
    store.add(_cron_trigger(next_fire_at=time.time() - 1.0))
    store.set_enabled("trg_1", False, reason="operator hold")
    result = fire_trigger_on_engine(_StubEngine(known={"wf_1"}), store, "trg_1")
    assert result.fired is False
    assert "disabled" in result.reason


def test_fire_refuses_an_unknown_trigger(tmp_path: Path):
    store = _store(tmp_path)
    result = fire_trigger_on_engine(_StubEngine(known={"wf_1"}), store, "nope")
    assert result.fired is False
    assert result.reason == "trigger not found"


def test_forget_workflow_drops_its_triggers(tmp_path: Path):
    store = _store(tmp_path)
    store.add(_cron_trigger(trigger_id="a"))
    store.add(_cron_trigger(trigger_id="b", workflow_id="wf_other"))
    assert store.forget_workflow("wf_1") == 1
    assert [t.trigger_id for t in store.list()] == ["b"]
