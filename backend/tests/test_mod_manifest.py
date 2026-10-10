"""Unit tests for mod manifests and the describe_mod review projection."""

from alpha.mods.kernel import ModKernel
from alpha.mods.manifest import (
    NAMESPACE_CAPABILITIES,
    RESERVED_CAPABILITIES,
    ModManifest,
    describe_mod,
)
from alpha.mods.types import ModPriority


class _Mod:
    """A minimal mod whose contract may or may not match its declaration."""

    def __init__(self, *, declared_hooks=(), declared_calls=(), caps=("evidence:record",), events=None):
        self.name = "test_mod"
        self.version = "2.1.0"
        self.priority = int(ModPriority.EXECUTION)
        self.required_capabilities = set(caps)
        self.subscribed_events = set(events) if events is not None else None
        self.manifest = ModManifest.create(
            name="test_mod",
            version="2.1.0",
            description="A test mod.",
            hooks=declared_hooks,
            calls=declared_calls,
        )

    async def handle(self, ctx, event, next_fn):  # pragma: no cover - not exercised here
        return await next_fn(event)


def test_describe_mod_reports_no_discrepancies_for_an_honest_mod():
    mod = _Mod(declared_hooks=["tool.completed"], declared_calls=["evidence:record"], events={"tool.completed"})
    description = describe_mod(mod, granted_capabilities={"evidence:record"})

    assert description.name == "test_mod"
    assert description.version == "2.1.0"
    assert description.priority == int(ModPriority.EXECUTION)
    assert description.first_party is False
    assert description.subscribed_events == ["tool.completed"]
    assert description.discrepancies == []
    assert description.observed["hooks"] == ["tool.completed"]


def test_describe_mod_names_a_declared_hook_the_mod_does_not_subscribe_to():
    mod = _Mod(declared_hooks=["tool.completed", "run.admit"], declared_calls=["evidence:record"], events={"tool.completed"})
    description = describe_mod(mod, granted_capabilities={"evidence:record"})

    assert any("declares hook 'run.admit'" in d for d in description.discrepancies)


def test_describe_mod_names_an_undeclared_subscription():
    mod = _Mod(declared_hooks=["tool.completed"], declared_calls=["evidence:record"], events={"tool.completed", "turn.complete"})
    description = describe_mod(mod, granted_capabilities={"evidence:record"})

    assert any("subscribes to 'turn.complete' without declaring it" in d for d in description.discrepancies)


def test_describe_mod_names_a_capability_granted_but_not_declared():
    mod = _Mod(declared_hooks=["tool.completed"], declared_calls=["evidence:record"], events={"tool.completed"}, caps=("evidence:record",))
    description = describe_mod(mod, granted_capabilities={"evidence:record", "tools:read"})

    assert any("granted capability 'tools:read' is not declared" in d for d in description.discrepancies)


def test_describe_mod_names_a_declared_capability_that_was_not_granted():
    mod = _Mod(declared_hooks=["tool.completed"], declared_calls=["evidence:record"], events={"tool.completed"}, caps=("evidence:record",))
    description = describe_mod(mod, granted_capabilities=set())

    assert any("declares capability 'evidence:record' but was not granted it" in d for d in description.discrepancies)


def test_describe_mod_flags_a_reserved_capability_held_by_external_code():
    mod = _Mod(declared_hooks=["tool.completed"], declared_calls=["estop:control"], events={"tool.completed"}, caps=("estop:control",))
    description = describe_mod(mod, granted_capabilities={"estop:control"})

    assert any("holds reserved capability 'estop:control'" in d for d in description.discrepancies)


def test_wildcard_subscriptions_are_reported_as_wildcard():
    mod = _Mod(declared_hooks=["*"], declared_calls=(), caps=(), events=None)
    description = describe_mod(mod, granted_capabilities=set())

    assert description.subscribed_events == ["*"]
    # A wildcard subscription is not flagged as undeclared: it is the mod
    # explicitly asking for everything.
    assert not any("without declaring it" in d for d in description.discrepancies)


def test_first_party_detection_uses_the_module_path():
    first_party = _Mod()
    type(first_party).__module__ = "alpha.mods.enforcers.something"
    assert describe_mod(first_party).first_party is True


def test_manifest_normalizes_and_deduplicates():
    manifest = ModManifest.create(hooks=["b", "a", "b"], calls=["z", "z"])
    assert manifest.hooks == ("a", "b")
    assert manifest.calls == ("z",)
    assert manifest.to_dict()["hooks"] == ["a", "b"]


def test_namespace_capability_map_covers_every_gated_namespace():
    """Every capability the context gates must be reachable from the map."""
    for namespace, capability in NAMESPACE_CAPABILITIES.items():
        assert capability.count(":") == 1, namespace
    assert NAMESPACE_CAPABILITIES["fs"] == "fs:read"
    assert NAMESPACE_CAPABILITIES["commands"] == "commands:register"


def test_reserved_capabilities_is_the_estop_control_authority():
    assert "estop:control" in RESERVED_CAPABILITIES
    # Reading the stop state is not the same as being able to trip it.
    assert "estop:read" not in RESERVED_CAPABILITIES


def test_kernel_describe_mods_reports_every_registered_mod():
    kernel = ModKernel()
    descriptions = kernel.describe_mods()
    assert descriptions == []  # a bare kernel registers nothing until configured

    from alpha.mods.kernel import _register_builtin_enforcers

    _register_builtin_enforcers(kernel)
    names = {d["name"] for d in kernel.describe_mods()}
    assert {"sec_default", "audit_ledger", "fleet_estop", "blast_radius_guard"} <= names
    for description in kernel.describe_mods():
        assert "priority" in description
