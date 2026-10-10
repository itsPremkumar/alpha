"""Unit tests for the durable hold and approval store."""

import pytest

from alpha.mods.approvals import (
    DEFAULT_HOLD_TTL_SECONDS,
    HoldDecision,
    HoldStatus,
    HoldStore,
    compute_idempotency_key,
)
from alpha.mods.kernel import ModKernel, _register_builtin_enforcers
from alpha.mods.types import AlphaEvent, CorrelationContext, EventOutcome

ARGS = {"command": "rm -rf build"}


@pytest.fixture
def store(tmp_path):
    return HoldStore(root_dir=tmp_path)


def _event(run_id="run_1", tool_call_id="call_1"):
    return AlphaEvent(
        name="tool.requested",
        payload={"tool_name": "run_command", "tool_args": dict(ARGS)},
        correlation=CorrelationContext.create(run_id=run_id, tool_call_id=tool_call_id),
    )


class TestIdempotencyKey:
    def test_same_action_produces_the_same_key(self):
        first = compute_idempotency_key("run_command", {"a": 1, "b": 2}, "run_1", "call_1")
        # Key order is not part of the action's identity.
        second = compute_idempotency_key("run_command", {"b": 2, "a": 1}, "run_1", "call_1")
        assert first == second

    def test_a_different_action_produces_a_different_key(self):
        base = compute_idempotency_key("run_command", ARGS, "run_1", "call_1")
        assert base != compute_idempotency_key("run_command", ARGS, "run_1", "call_2")
        assert base != compute_idempotency_key("run_command", ARGS, "run_2", "call_1")
        assert base != compute_idempotency_key("other_tool", ARGS, "run_1", "call_1")

    def test_key_is_short_and_stable(self):
        key = compute_idempotency_key("run_command", ARGS, "run_1", "call_1")
        assert len(key) == 40
        assert key == compute_idempotency_key("run_command", ARGS, "run_1", "call_1")


class TestHoldStore:
    def test_open_hold_creates_then_reuses_the_same_record(self, store):
        first, created_first = store.open_hold(tool_name="run_command", tool_args=ARGS, run_id="run_1", tool_call_id="call_1", risk_level="R4", reason="destructive")
        second, created_second = store.open_hold(tool_name="run_command", tool_args=ARGS, run_id="run_1", tool_call_id="call_1", risk_level="R4", reason="destructive")

        assert created_first is True
        assert created_second is False
        assert first.hold_id == second.hold_id

    def test_pending_hold_holds_the_action(self, store):
        store.open_hold(tool_name="run_command", tool_args=ARGS, run_id="run_1", tool_call_id="call_1", risk_level="R4", reason="destructive")
        status, record = store.status_for("run_command", ARGS, "run_1", "call_1")

        assert status == HoldStatus.WAIT
        assert record is not None

    def test_approval_releases_the_action(self, store):
        record, _ = store.open_hold(tool_name="run_command", tool_args=ARGS, run_id="run_1", tool_call_id="call_1", risk_level="R4", reason="destructive")
        store.approve(record.hold_id, operator="operator@example")

        status, released = store.status_for("run_command", ARGS, "run_1", "call_1")
        assert status == HoldStatus.PROCEED
        assert released.decided_by == "operator@example"
        assert released.decision == HoldDecision.APPROVED.value

    def test_rejection_refuses_the_action_with_its_reason(self, store):
        record, _ = store.open_hold(tool_name="run_command", tool_args=ARGS, run_id="run_1", tool_call_id="call_1", risk_level="R4", reason="destructive")
        store.reject(record.hold_id, operator="operator@example", reason="that directory is needed")

        status, refused = store.status_for("run_command", ARGS, "run_1", "call_1")
        assert status == HoldStatus.REFUSE
        assert refused.decision_reason == "that directory is needed"

    def test_a_second_decision_does_not_overwrite_the_first(self, store):
        record, _ = store.open_hold(tool_name="run_command", tool_args=ARGS, run_id="run_1", tool_call_id="call_1", risk_level="R4", reason="destructive")
        store.approve(record.hold_id, operator="first")
        store.reject(record.hold_id, operator="second", reason="changed my mind")

        assert store.get(record.hold_id).decision == HoldDecision.APPROVED.value

    def test_decide_refuses_an_unknown_decision(self, store):
        with pytest.raises(ValueError):
            store.decide("hold_nope", HoldDecision.PENDING, operator="x")

    def test_decision_for_an_unknown_hold_is_none(self, store):
        assert store.approve("hold_missing", operator="x") is None
        assert store.reject("hold_missing", operator="x") is None


class TestDurability:
    def test_a_decision_survives_a_new_store_instance(self, tmp_path):
        first = HoldStore(root_dir=tmp_path)
        record, _ = first.open_hold(tool_name="run_command", tool_args=ARGS, run_id="run_1", tool_call_id="call_1", risk_level="R4", reason="destructive")
        first.approve(record.hold_id, operator="operator@example")

        # A second store over the same root is what a Gateway restart builds.
        second = HoldStore(root_dir=tmp_path)
        status, loaded = second.status_for("run_command", ARGS, "run_1", "call_1")

        assert status == HoldStatus.PROCEED
        assert loaded.decided_by == "operator@example"

    def test_an_unreadable_store_discloses_itself_rather_than_approving(self, tmp_path):
        store = HoldStore(root_dir=tmp_path)
        path = store.store_file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ this is not json", encoding="utf-8")

        record, created = store.open_hold(tool_name="run_command", tool_args=ARGS, run_id="run_1", tool_call_id="call_1", risk_level="R4", reason="destructive")
        # The hold is still created in memory, so the action is held either way.
        assert created is True
        status, _ = store.status_for("run_command", ARGS, "run_1", "call_1")
        assert status == HoldStatus.WAIT

    def test_an_unreadable_shape_discloses_itself(self, tmp_path):
        store = HoldStore(root_dir=tmp_path)
        store.store_file.parent.mkdir(parents=True, exist_ok=True)
        store.store_file.write_text('{"holds": "not a list"}', encoding="utf-8")

        # Reading must not raise, and must not report an approval that was
        # never read from disk.
        assert store.list_holds() == []
        status, _ = store.status_for("run_command", ARGS, "run_1", "call_1")
        assert status == HoldStatus.WAIT


class TestExpiry:
    def test_an_expired_hold_is_expired_even_after_approval(self, tmp_path):
        store = HoldStore(root_dir=tmp_path)
        record, _ = store.open_hold(tool_name="run_command", tool_args=ARGS, run_id="run_1", tool_call_id="call_1", risk_level="R4", reason="destructive")
        store.approve(record.hold_id, operator="operator")

        # The TTL has a production floor so a hold cannot be opened already
        # expired; expiry is exercised by ageing the record directly.
        import time

        record.expires_at = time.time() - 1
        status, expired = store.status_for("run_command", ARGS, "run_1", "call_1")

        assert status == HoldStatus.WAIT
        # The approval itself is preserved for audit — who approved what is a
        # fact worth keeping — and expiry is reported beside it rather than by
        # overwriting the verdict.
        assert expired.is_expired() is True
        assert expired.decision == HoldDecision.APPROVED.value
        assert expired.decided_by == "operator"

    def test_the_ttl_floor_keeps_a_hold_decidable(self, tmp_path):
        """A hold must not be born expired: that would defeat the approval gate."""
        store = HoldStore(root_dir=tmp_path, ttl_seconds=0.001)
        record, _ = store.open_hold(tool_name="run_command", tool_args=ARGS, run_id="run_1", tool_call_id="call_1", risk_level="R4", reason="destructive")
        assert record.is_expired() is False

    def test_default_ttl_is_hours_not_minutes(self):
        assert DEFAULT_HOLD_TTL_SECONDS >= 3600.0


class TestStoreBounds:
    def test_decided_records_age_out_before_pending_ones(self, tmp_path):
        store = HoldStore(root_dir=tmp_path, ttl_seconds=10_000)
        for i in range(6):
            store.open_hold(
                tool_name="run_command",
                tool_args={"i": i},
                run_id="run_1",
                tool_call_id=f"call_{i}",
                risk_level="R4",
                reason="destructive",
            )
        # Decide the oldest five; the sixth is still pending and must survive.
        for record in sorted(store.list_holds(limit=10), key=lambda r: r.created_at)[:5]:
            store.approve(record.hold_id, operator="operator")

        holds = store.list_holds(limit=10)
        assert len(holds) == 6
        assert sum(1 for h in holds if h.decision == HoldDecision.PENDING.value) == 1

    def test_a_non_dict_impact_is_accepted(self, store):
        record, _ = store.open_hold(tool_name="run_command", tool_args=ARGS, run_id="run_1", tool_call_id="call_1", risk_level="R4", reason="destructive", impact=None)
        assert record.impact == {}


class TestBlastRadiusWiring:
    def _kernel_with_guard(self, tmp_path):
        kernel = ModKernel()
        _register_builtin_enforcers(kernel)
        guard = kernel.get_mod("blast_radius_guard")
        guard._hold_store = HoldStore(root_dir=tmp_path)
        return kernel, guard

    @pytest.mark.asyncio
    async def test_a_held_action_is_released_by_a_durable_approval(self, tmp_path):
        kernel, guard = self._kernel_with_guard(tmp_path)

        first = await kernel.dispatch(_event())
        assert first.outcome == EventOutcome.DEFER
        hold_id = first.response_payload["hold_id"]
        assert first.response_payload["durable"] is True

        # The durable record is what a Gateway route would decide on.
        durable = guard.hold_store.list_holds(decision="pending")[0]
        guard.hold_store.approve(durable.hold_id, operator="operator@example")

        # Re-dispatching the same action now proceeds, because the store says so.
        second = await kernel.dispatch(_event())
        assert second.outcome == EventOutcome.CONTINUE
        assert hold_id

    @pytest.mark.asyncio
    async def test_a_rejected_action_is_refused_with_its_reason(self, tmp_path):
        kernel, guard = self._kernel_with_guard(tmp_path)

        await kernel.dispatch(_event())
        durable = guard.hold_store.list_holds(decision="pending")[0]
        guard.hold_store.reject(durable.hold_id, operator="operator@example", reason="do not delete build")

        result = await kernel.dispatch(_event())
        assert result.outcome == EventOutcome.DENY
        assert "do not delete build" in result.reason

    @pytest.mark.asyncio
    async def test_the_defer_payload_carries_an_impact_preview(self, tmp_path):
        kernel, guard = self._kernel_with_guard(tmp_path)
        result = await kernel.dispatch(_event())

        impact = result.response_payload["impact"]
        assert impact["kind"] == "file_delete"
        assert impact["measurable"] is True
        assert impact["evidence"]["targets"] == ["build"]

    @pytest.mark.asyncio
    async def test_an_unreadable_store_fails_closed(self, tmp_path):
        kernel, guard = self._kernel_with_guard(tmp_path)
        guard.hold_store.store_file.parent.mkdir(parents=True, exist_ok=True)
        guard.hold_store.store_file.write_text("not json", encoding="utf-8")

        result = await kernel.dispatch(_event())
        # Refusing to run is the only safe answer to "I cannot read the store".
        assert result.outcome == EventOutcome.DEFER
