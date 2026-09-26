"""The central capture-composition seam (the write side of the memory wave).

These tests pin the properties that make it safe to turn on several memory
types at once:

1. default-OFF is a genuine no-op - nothing is constructed, nothing is written;
2. admission runs BEFORE any store write, and a policy refusal is recorded as a
   refusal rather than as a lost write;
3. a policy that RAISES refuses rather than admits (fail closed);
4. unknown signals stay unknown - a capture turn never fabricates a zero;
5. one failing type cannot fail the turn, and no replacement write is invented;
6. the caller must supply the PERSISTED record set, which is why the seam takes
   plain dicts instead of re-deriving anything from a pipeline.
"""

from __future__ import annotations

from typing import Any

import pytest

from alpha.config.memory_config import MemoryConfig
from alpha.memory import capture_composition as cc


def _config(**sections: Any) -> MemoryConfig:
    payload: dict[str, Any] = {"enabled": True}
    payload.update(sections)
    return MemoryConfig.model_validate(payload)


def _records(count: int = 2) -> list[dict[str, Any]]:
    return [
        {
            "id": f"r{index}",
            "content": f"The deploy pipeline uses Argo Rollouts for {name}.",
            "type": "fact",
            "priority": 70,
            "scene_name": "deploy",
        }
        for index, name in enumerate(("canary", "production"), start=1)
    ][:count]


class _AllowEngine:
    def evaluate(self, candidate: Any) -> Any:  # noqa: ANN401 - test double
        class _Decision:
            admit = True
            rule_id = "test_allow"
            action = type("A", (), {"value": "store"})()

        return _Decision()


class _DenyEngine:
    def __init__(self) -> None:
        self.seen: list[Any] = []

    def evaluate(self, candidate: Any) -> Any:  # noqa: ANN401 - test double
        self.seen.append(candidate)

        class _Decision:
            admit = False
            rule_id = "test_deny"
            action = type("A", (), {"value": "reject"})()

        return _Decision()


class _RaisingEngine:
    def evaluate(self, candidate: Any) -> Any:  # noqa: ANN401 - test double
        msg = "policy backend unavailable"
        raise RuntimeError(msg)


def test_all_types_off_writes_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Default-OFF must be a real no-op, not an empty write."""

    def explode(*args: object, **kwargs: object) -> tuple[int, str]:  # noqa: ANN401
        pytest.fail("a disabled capture surface must never be constructed")

    monkeypatch.setitem(cc._WRITERS, "entities", explode)
    monkeypatch.setitem(cc._WRITERS, "narrative", explode)

    result = cc.compose_capture(MemoryConfig(), _records(), user_id="u1")
    assert result.wrote_anything is False
    assert {s.surface for s in result.surfaces} == set(cc.CAPTURE_SURFACE_ORDER)
    assert all(s.status == cc.STATUS_DISABLED for s in result.surfaces)


def test_host_switch_off_disables_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(cc._WRITERS, "entities", lambda *a, **k: pytest.fail("constructed"))
    config = MemoryConfig.model_validate({"enabled": False, "entities": {"enabled": True}})
    result = cc.compose_capture(config, _records(), user_id="u1")
    assert result.wrote_anything is False


def test_enabled_surface_writes_and_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def writer(config: Any, records: list[dict[str, Any]], **kwargs: Any) -> tuple[int, str]:
        seen["count"] = len(records)
        return len(records), "ok"

    monkeypatch.setitem(cc._WRITERS, "entities", writer)
    result = cc.compose_capture(
        _config(entities={"enabled": True}), _records(2), user_id="u1", surfaces=("entities",)
    )
    assert result.status_for("entities") == cc.STATUS_WRITTEN
    assert result.wrote_anything is True
    assert seen["count"] == 2
    assert result.rejected_count == 0


def test_empty_record_set_is_empty_not_written(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(cc._WRITERS, "entities", lambda *a, **k: pytest.fail("constructed"))
    result = cc.compose_capture(
        _config(entities={"enabled": True}), [], user_id="u1", surfaces=("entities",)
    )
    assert result.status_for("entities") == cc.STATUS_EMPTY
    assert result.wrote_anything is False


def test_policy_rejection_prevents_the_write(monkeypatch: pytest.MonkeyPatch) -> None:
    """Admission is upstream: a refused candidate never reaches a store."""
    monkeypatch.setitem(cc._WRITERS, "entities", lambda *a, **k: pytest.fail("store was written"))
    engine = _DenyEngine()
    result = cc.compose_capture(
        _config(entities={"enabled": True}),
        _records(2),
        user_id="u1",
        policy_engine=engine,
        surfaces=("entities",),
    )
    assert result.status_for("entities") == cc.STATUS_REJECTED
    assert result.rejected_count == 2
    assert result.wrote_anything is False


def test_a_raising_policy_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unavailable policy must refuse, not wave the write through."""
    monkeypatch.setitem(cc._WRITERS, "entities", lambda *a, **k: pytest.fail("store was written"))
    result = cc.compose_capture(
        _config(entities={"enabled": True}),
        _records(2),
        user_id="u1",
        policy_engine=_RaisingEngine(),
        surfaces=("entities",),
    )
    assert result.status_for("entities") == cc.STATUS_REJECTED
    assert result.wrote_anything is False


def test_admission_candidate_leaves_unknown_signals_unset() -> None:
    """A capture turn must not turn 'unknown importance' into a fabricated 0."""
    from alpha.memory.policy.models import AdmissionCandidate

    seen: list[AdmissionCandidate] = []

    class _Engine:
        def evaluate(self, candidate: AdmissionCandidate) -> Any:  # noqa: ANN401
            seen.append(candidate)

            class _Decision:
                admit = True
                rule_id = "ok"
                action = type("A", (), {"value": "store"})()

            return _Decision()

    cc.compose_capture(
        _config(narrative={"enabled": True}),
        _records(1),
        user_id="u1",
        policy_engine=_Engine(),
        surfaces=("narrative",),
    )
    assert seen, "the policy engine was never consulted"
    candidate = seen[0]
    assert candidate.source == "l1"
    assert candidate.user_id == "u1"
    for signal in cc.UNPROVID_SIGNALS:
        assert getattr(candidate, signal, None) is None, f"{signal} must stay unknown"


def test_a_raising_writer_does_not_break_the_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: object, **kwargs: object) -> tuple[int, str]:
        msg = "store exploded"
        raise RuntimeError(msg)

    monkeypatch.setitem(cc._WRITERS, "entities", boom)
    monkeypatch.setitem(cc._WRITERS, "narrative", lambda *a, **k: (1, "ok"))
    result = cc.compose_capture(
        _config(entities={"enabled": True}, narrative={"enabled": True}),
        _records(1),
        user_id="u1",
    )
    assert result.status_for("entities") == cc.STATUS_ERROR
    detail = next(s.detail for s in result.surfaces if s.surface == "entities")
    assert detail == "RuntimeError"
    # The healthy surface still ran, and nothing invented a stand-in write.
    assert result.status_for("narrative") == cc.STATUS_WRITTEN
    assert result.wrote_anything is True


def test_missing_user_id_and_unknown_surface_are_rejected() -> None:
    with pytest.raises(ValueError, match="user_id"):
        cc.compose_capture(MemoryConfig(), _records(), user_id="  ")
    with pytest.raises(ValueError, match="unknown capture surface"):
        cc.compose_capture(MemoryConfig(), _records(), user_id="u1", surfaces=("nope",))


def test_record_normalisation_accepts_dicts_and_models() -> None:
    assert cc._record_payload({"a": 1}) == {"a": 1}
    assert cc._record_payload(object()) is None

    class _Model:
        def model_dump(self, mode: str = "python") -> dict[str, Any]:  # noqa: ARG002
            assert mode == "json"
            return {"id": "m1"}

    assert cc._record_payload(_Model()) == {"id": "m1"}
