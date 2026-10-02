"""Wiring tests for :mod:`alpha.agents.middlewares.grounding_middleware`.

The point of these is **wiring**, not unit behaviour. Alpha already has an entire
module (:mod:`alpha.capabilities.honesty`) written because capabilities were
documented as features, passed unit tests, and had no production caller. The
grounding layer's own tests would all pass if the middleware were deleted, which
is exactly the failure mode that module exists to prevent — so these tests assert
that the chain actually contains it and that it actually refuses work.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from alpha.agents.middlewares.grounding_middleware import (
    GROUNDING_CLAIMS_KEY,
    GROUNDING_MANIFEST_KEY,
    GroundingMiddleware,
)
from alpha.grounding.claims import ClaimKind
from alpha.grounding.manifest import Availability, CapabilityEntry, CapabilityManifest, EntrySource
from alpha.grounding.models import GateLayer


def _manifest(*, names: tuple[str, ...] = ("read_file",)) -> CapabilityManifest:
    return CapabilityManifest(
        entries=tuple(
            CapabilityEntry(
                name=name,
                kind="tool",
                summary=f"{name} tool",
                address=f"tool:{name}",
                availability=Availability.AVAILABLE,
                source=EntrySource.PROBE,
            )
            for name in names
        ),
        gaps=("no production write access",),
    )


class TestManifestInjection:
    def test_injects_once_into_the_system_channel(self) -> None:
        """Registry-derived content is framework authority, so it rides system."""
        middleware = GroundingMiddleware(manifest=_manifest())
        update = middleware.before_agent({"messages": [HumanMessage(content="read the file", id="h1")]}, None)
        injected = update["messages"]
        assert len(injected) == 1
        assert isinstance(injected[0], SystemMessage)
        assert injected[0].additional_kwargs[GROUNDING_MANIFEST_KEY] is True
        assert injected[0].additional_kwargs["hide_from_ui"] is True
        assert "read_file" in injected[0].content

    def test_does_not_reinject_on_a_later_turn(self) -> None:
        """The base system prompt stays static for prefix-cache reuse; re-injecting
        every turn would put capability lines in every request forever."""
        middleware = GroundingMiddleware(manifest=_manifest())
        first = middleware.before_agent({"messages": [HumanMessage(content="go", id="h1")]}, None)
        history = first["messages"] + [HumanMessage(content="again", id="h2")]
        assert middleware.before_agent({"messages": history}, None) is None

    def test_gaps_are_disclosed_to_the_model(self) -> None:
        """A manifest with no gap section reads as 'nothing is missing'."""
        middleware = GroundingMiddleware(manifest=_manifest())
        injected = middleware.before_agent({"messages": [HumanMessage(content="go", id="h1")]}, None)["messages"]
        assert "no production write access" in injected[0].content

    def test_claims_ride_the_untrusted_channel(self) -> None:
        """A premise the model itself wrote must not be able to grant itself system
        authority, so blocking claims arrive as a hidden HumanMessage data block."""
        middleware = GroundingMiddleware(manifest=_manifest())
        claim = middleware.service.assert_claim("auth lives in the gateway", kind=ClaimKind.DECISION)
        middleware.service.depend_on([claim.claim_id])

        update = middleware.before_agent({"messages": [HumanMessage(content="go", id="h1")]}, None)
        claims = [m for m in update["messages"] if m.additional_kwargs.get(GROUNDING_CLAIMS_KEY)]
        assert len(claims) == 1
        assert isinstance(claims[0], HumanMessage)
        assert not isinstance(claims[0], SystemMessage)
        assert "auth lives in the gateway" in claims[0].content

    def test_no_injection_when_disabled(self) -> None:
        """`grounding.enabled=false` suppresses context, never a check."""
        middleware = GroundingMiddleware(manifest=_manifest())
        middleware.service.inject_manifest = False
        assert middleware.before_agent({"messages": [HumanMessage(content="go", id="h1")]}, None) is None

    def test_empty_history_is_safe(self) -> None:
        assert GroundingMiddleware(manifest=_manifest()).before_agent({"messages": []}, None) is None

    def test_release_policy_is_json_serialisable(self) -> None:
        """Assembly identity is hashed from this, so it must not carry live
        registry content that changes every time a skill is enabled."""
        import json

        params = GroundingMiddleware(manifest=_manifest()).release_policy_parameters()
        assert json.loads(json.dumps(params))


class TestToolGating:
    def test_refuses_a_tool_that_is_not_installed(self) -> None:
        """Tool-selection hallucination — inventing an API — is the most common
        agent-side error in real deployments, and a set test catches it."""
        middleware = GroundingMiddleware(manifest=_manifest(names=("read_file",)))
        result = middleware._gate({"name": "imaginary_api", "id": "c1"})
        assert result is not None
        assert result.code == "unknown_tool"

    def test_refusal_becomes_an_error_tool_message_with_a_way_out(self) -> None:
        """A block that only says 'not allowed' produces a retry of the same action."""
        middleware = GroundingMiddleware(manifest=_manifest(names=("read_file",)))
        call = {"name": "imaginary_api", "id": "c1"}
        message = middleware._blocked_result(call, middleware._gate(call))
        assert isinstance(message, ToolMessage)
        assert message.status == "error"
        assert message.tool_call_id == "c1"
        assert "manifest" in message.content
        assert message.additional_kwargs["alpha_grounding_block"]["code"] == "unknown_tool"

    def test_allows_an_installed_tool(self) -> None:
        middleware = GroundingMiddleware(manifest=_manifest(names=("read_file",)))
        assert middleware._gate({"name": "read_file", "id": "c1"}) is None

    def test_pipeline_failure_blocks_rather_than_passing(self) -> None:
        """An unrun check has verified nothing. Passing here would be the exact
        defect this package removes."""
        middleware = GroundingMiddleware(manifest=_manifest())

        def explode(subject):  # noqa: ANN001, ANN202 - deliberately wrong arity to force a TypeError
            raise RuntimeError("boom")

        middleware.service.pipeline = type("Boom", (), {"run": staticmethod(lambda subject: explode(subject)), "block": staticmethod(lambda results: None)})()
        result = middleware._gate({"name": "read_file", "id": "c1"})
        assert result is not None
        assert result.code == "gate_error"

    def test_dispatch_counts_toward_the_effort_budget(self) -> None:
        middleware = GroundingMiddleware(manifest=_manifest(names=("read_file",)))
        for _ in range(3):
            middleware._gate({"name": "read_file", "id": "c1"})
        assert middleware.service.effort.steps_used == 3

    def test_model_advisory_cannot_clear_a_block(self) -> None:
        """The structural invariant, at the wiring layer rather than in the pipeline.

        Uses the real :class:`GatePipeline` rather than a hand-rolled double: a
        double would test the double, and the whole point is that
        ``GatePipeline.block`` -- not the caller -- enforces the asymmetry.
        """
        from alpha.grounding.gates import GatePipeline
        from alpha.grounding.models import GateResult

        def optimistic(subject):  # noqa: ANN001, ANN202
            return GateResult("judge", GateLayer.MODEL, True, "looks fine")

        # Judge first, so the model verdict is seen before the deterministic one.
        middleware = GroundingMiddleware(manifest=_manifest(names=("read_file",)))
        middleware.service.pipeline = GatePipeline(gates=(optimistic,))
        assert middleware._gate({"name": "imaginary_api", "id": "c1"}) is not None


def _example_app_config():  # noqa: ANN202 - AppConfig needs a real config document
    """Load the shipped template.

    ``AppConfig()`` with no arguments cannot validate -- ``sandbox`` is required --
    so the chain test reads ``config.example.yaml``, the same way other offline
    chain tests in this suite do.
    """
    from pathlib import Path

    from alpha.config.app_config import AppConfig

    example = Path(__file__).resolve().parents[2] / "config.example.yaml"
    return AppConfig.from_file(example)


class TestChainMembership:
    def test_lead_agent_chain_contains_the_grounding_middleware(self) -> None:
        """# BITE

        Every other test in this file passes if the middleware is removed from the
        chain. This one does not. The defect class Alpha has actually hit is a
        component that works, is tested, and is never called.
        """
        from alpha.agents.lead_agent.agent import build_middlewares

        middlewares = build_middlewares({}, None, app_config=_example_app_config())
        names = [type(m).__name__ for m in middlewares]
        assert "GroundingMiddleware" in names, "the grounding layer is not wired into the lead chain"

    def test_grounding_is_declared_in_the_capability_catalog(self) -> None:
        """So it is discoverable on ``GET /api/ops/integration-health`` rather than
        existing only as an import."""
        from alpha.capabilities.catalog import CAPABILITY_CATALOG

        assert "grounding" in CAPABILITY_CATALOG
        assert CAPABILITY_CATALOG["grounding"].module == "alpha.grounding.service"
        assert "grounding_manifest" in CAPABILITY_CATALOG


class TestConfigSurface:
    def test_config_resolves_to_defaults_without_a_yaml_section(self) -> None:
        from alpha.config.grounding_config import resolve_grounding_config

        config = resolve_grounding_config()
        assert config.enabled is True
        assert config.require_reuse_probe is True

    def test_no_setting_can_switch_off_a_correctness_check(self) -> None:
        """There is no `disable_claim_gate` and no `trust_model_output`. A loop that
        can be satisfied by deleting its assertion is worse than no loop, because it
        manufactures a green result."""
        from alpha.config.grounding_config import GroundingConfig

        forbidden = {"disable_claim_gate", "trust_model_output", "skip_side_effect_check", "allow_weak_claims"}
        assert forbidden.isdisjoint(GroundingConfig.model_fields)

    def test_malformed_section_is_loud_but_absent_is_not(self) -> None:
        """Absent falls back (an older config file must still start); present-but-invalid
        raises, because a silently-ignored setting is one that looks applied while
        doing nothing -- the exact defect class this package removes."""
        from alpha.config.grounding_config import GroundingConfig

        with pytest.raises(Exception):
            GroundingConfig(max_steps=0)
        with pytest.raises(Exception):
            GroundingConfig(unknown_key=True)
        assert GroundingConfig().max_steps > 0

    def test_pipeline_follows_the_policy(self) -> None:
        from alpha.config.grounding_config import GroundingConfig, build_pipeline
        from alpha.grounding.gates import check_reuse_probe

        assert any(g is check_reuse_probe for g in build_pipeline(GroundingConfig()).gates)
        assert not any(g is check_reuse_probe for g in build_pipeline(GroundingConfig(require_reuse_probe=False)).gates)


class TestHarnessBoundary:
    def test_grounding_never_imports_app(self) -> None:
        """App imports alpha; alpha never imports app. Enforced in CI by
        ``tests/test_harness_boundary.py`` and asserted here for the new package."""
        from pathlib import Path

        import alpha.grounding as package

        root = Path(package.__file__).parent
        offenders = [path.name for path in root.glob("*.py") if "from app" in path.read_text(encoding="utf-8") or "import app" in path.read_text(encoding="utf-8")]
        assert not offenders, f"harness layer imported app: {offenders}"
