"""The dispatcher refuses a duplicate event id instead of recording it.

``_new_event_id`` is a timestamp plus a counter, so a duplicate cannot happen by
chance -- this test *forces* the failure the guard exists for: freeze the clock
AND the counter so two back-to-back emits resolve to the same address, then pin
that the second emit is refused by name and never reaches the log.

A duplicate address is a corrupt one: a fork point resolving by id would match
the first of the two, truncate its prefix early, and repeat a side effect it
had reported as inherited.
"""

from __future__ import annotations

from datetime import UTC, datetime

from alpha.workflow.events import WorkflowEventDispatcher

_FROZEN = datetime.now(UTC)


class _FrozenClock:
    """Return one frozen UTC instant for every clock read."""

    @staticmethod
    def now(_tz=None):
        return _FROZEN


class _StuckSequence:
    """A counter that never advances (clock congested, or a sequence wrap)."""

    def __iter__(self):
        return self

    def __next__(self) -> int:
        return 0


def test_back_to_back_emits_never_share_an_id(monkeypatch):
    """Refuse the collision instead of recording a non-unique address."""
    import alpha.workflow.events as ev_mod

    monkeypatch.setattr(ev_mod, "datetime", _FrozenClock)
    monkeypatch.setattr(ev_mod, "_EVENT_ID_SEQUENCE", _StuckSequence())
    dispatcher = WorkflowEventDispatcher(durable_sink=None)

    def try_emit(event_type: str) -> str:
        try:
            dispatcher.emit(event_type, "run-1", node_id="n")
            return "ok"
        except RuntimeError:
            return "refused"

    first = try_emit("node_attempt_started")
    second = try_emit("node_completed")

    assert first == "ok", first
    assert second == "refused", second
    assert len(dispatcher._event_log) == 1, "the refused event must never enter the log"
    assert dispatcher._event_log[0].event_type == "node_attempt_started"
