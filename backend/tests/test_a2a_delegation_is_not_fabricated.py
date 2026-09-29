"""A delegation is never reported as performed unless it actually was.

This suite pins a defect that was reachable from both a registered router and a
real agent tool: `A2AProtocolAdapter.delegate()` carried the comment "Simulate or
dispatch delegation deliverable" and then only simulated. It built a deliverable
by interpolating the caller's own objective back into a sentence of success,
returned ``status="completed"``, and attached evidence reading
``"Verified by A2A endpoint: local-bus"`` whenever the agent card carried no
URL — for work that was never dispatched to anyone.

The adapter imports no HTTP client and has never had a transport, so the claim
was structurally impossible to make true. The fix is that it refuses instead.

The general rule this pins: **a response asserting that work happened must be
produced by the component that did the work.** Everything else in this repository
can be inert — an absent capability is merely a gap — but a reachable endpoint
that reports success for work it did not do is a lie told to whoever called it.
"""

from __future__ import annotations

from typing import Any

import pytest

from alpha.protocols.a2a import (
    A2ADelegationRequest,
    A2ADelegationResponse,
    A2AProtocolAdapter,
    AgentCapabilityCard,
)


def _card(**over: Any) -> AgentCapabilityCard:
    base = {
        "agent_id": "remote-1",
        "name": "Remote One",
        "description": "A registered remote agent",
        "skills": ["analysis"],
        "availability": "available",
    }
    base.update(over)
    return AgentCapabilityCard(**base)  # type: ignore[arg-type]


def _request(**over: Any) -> A2ADelegationRequest:
    base = {
        "request_id": "req-1",
        "sender_agent_id": "local-1",
        "target_agent_id": "remote-1",
        "task_objective": "Summarise the incident",
    }
    base.update(over)
    return A2ADelegationRequest(**base)  # type: ignore[arg-type]


def test_a_registered_but_undispatchable_agent_is_never_reported_completed() -> None:
    adapter = A2AProtocolAdapter()
    adapter.register_card(_card())

    response = adapter.delegate(_request())

    assert response.status != "completed", (
        "a delegation that was not dispatched must never be reported as completed"
    )
    assert response.status == "not_dispatched"
    assert response.error is not None
    assert "not dispatched" in response.error.lower()


def test_no_deliverable_is_invented() -> None:
    """The old defect echoed the requested objective back as a success sentence."""
    adapter = A2AProtocolAdapter()
    adapter.register_card(_card())

    response = adapter.delegate(_request(task_objective="Delete the production database"))

    assert response.deliverable is None, (
        "a deliverable must come from a transport; interpolating the caller's own "
        "objective into a success sentence is not a deliverable"
    )
    # And specifically: the objective must not appear back inside a success claim.
    rendered = f"{response.deliverable} {response.error}"
    assert "Successfully resolved" not in rendered


def test_no_evidence_is_fabricated() -> None:
    """`Verified by A2A endpoint: ...` was emitted for work that never ran."""
    adapter = A2AProtocolAdapter()
    adapter.register_card(_card(endpoint_url=None))

    response = adapter.delegate(_request())

    assert response.evidence == [], "no transport ran, so there is no evidence to report"
    joined = " ".join(response.evidence) + (response.error or "")
    assert "Verified by" not in joined


def test_a_card_with_an_endpoint_url_still_does_not_claim_completion() -> None:
    """A URL on a card is not a transport. This is the exact shape that lied."""
    adapter = A2AProtocolAdapter()
    adapter.register_card(_card(endpoint_url="https://example.invalid/agent"))

    response = adapter.delegate(_request())

    assert response.status == "not_dispatched"
    assert "Verified by" not in " ".join(response.evidence)


def test_the_response_model_does_not_default_to_success() -> None:
    """A default that assumes success is a default that lies when a caller forgets."""
    bare = A2ADelegationResponse(request_id="r")
    assert bare.status == "rejected"
    assert bare.evidence == []
    assert bare.deliverable is None


def test_a_missing_agent_is_still_rejected() -> None:
    adapter = A2AProtocolAdapter()
    response = adapter.delegate(_request(target_agent_id="nobody"))
    assert response.status == "rejected"
    assert "not found" in (response.error or "").lower()


def test_an_offline_agent_is_still_rejected() -> None:
    adapter = A2AProtocolAdapter()
    adapter.register_card(_card(availability="offline"))
    response = adapter.delegate(_request())
    assert response.status == "rejected"
    assert "offline" in (response.error or "").lower()


class _RecordingTransport:
    """A real, injected transport is the only thing allowed to claim completion."""

    def __init__(self, status: str = "completed") -> None:
        self.calls: list[tuple[str, str]] = []
        self._status = status

    def dispatch(self, card: AgentCapabilityCard, request: A2ADelegationRequest) -> A2ADelegationResponse:
        self.calls.append((card.agent_id, request.task_objective))
        return A2ADelegationResponse(
            request_id=request.request_id,
            status=self._status,
            deliverable={"summary": f"transport did it for {card.name}"},
            evidence=[f"transport receipt {card.endpoint_url or 'injected'}"],
        )


def test_an_injected_transport_is_what_makes_completion_possible() -> None:
    transport = _RecordingTransport()
    adapter = A2AProtocolAdapter(transport=transport)
    adapter.register_card(_card())

    response = adapter.delegate(_request())

    assert transport.calls == [("remote-1", "Summarise the incident")]
    assert response.status == "completed"
    assert response.deliverable == {"summary": "transport did it for Remote One"}


def test_a_transport_response_is_honoured_including_failure() -> None:
    """The adapter must not upgrade a failure the transport reported."""
    transport = _RecordingTransport(status="failed")
    adapter = A2AProtocolAdapter(transport=transport)
    adapter.register_card(_card())

    response = adapter.delegate(_request())

    assert response.status == "failed", "the adapter must not rewrite a transport's verdict"


def test_history_records_the_refusal() -> None:
    adapter = A2AProtocolAdapter()
    adapter.register_card(_card())
    adapter.delegate(_request())
    stored = adapter.get_response("req-1")
    assert stored is not None
    assert stored.status == "not_dispatched"


def test_the_wire_shape_is_unchanged_for_callers() -> None:
    """Fixing the lie must not break the envelope callers already depend on."""
    adapter = A2AProtocolAdapter()
    adapter.register_card(_card())
    response = adapter.delegate(_request())
    for field in ("request_id", "status", "deliverable", "evidence", "error", "execution_seconds", "timestamp"):
        assert hasattr(response, field)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
