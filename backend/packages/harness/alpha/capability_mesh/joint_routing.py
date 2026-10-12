"""Tool-and-model joint routing: one execution unit, not a model dropdown (Feature M / plan §14).

Plan §14.1: "Select a **model + agent instructions + skills + toolset +
context slice + verification method** as a single execution configuration."
A model is never dispatched on its own here — an :class:`ExecutionConfiguration`
is the unit, and :class:`JointRouter` resolves each candidate unit to exactly
ONE :class:`Availability` state, naming the :class:`JointComponent` that
failed.

The motivating example is §14.2's: **for exact arithmetic, an inexpensive model
plus a deterministic calculator, rather than several chat agents.** This module
exists because model selection alone cannot express that answer — the right
executor's identity is a *joint* property, so a "best model" ranking silently
misses it. The router makes the joint unit inspectable. It does **not** know
any price: `capabilities`, `credentials`, `allowed_tools`,
`verification_executable`, `endpoint_up` and `runtime_ok` are the whole
input, and there is no cost, budget or token parameter anywhere on the
surface — picking the cheap-but-correct unit is the caller's reasoning, made
visible, not a number this router invented.

Four rules decide the shape of this module:

* **One unit, one state, one named component.** Every candidate resolves to a
  single :class:`Availability`; a state that is not ``AVAILABLE`` REQUIRES a
  ``failed_component``, and an ``AVAILABLE`` row forbids one. The checks run in
  the plan's own order (capability → endpoint → credentials → tool → runtime →
  verification), and the order is stated rather than incidental: the first
  failure is the one reported, so a unit that is both endpoint-down and
  tool-missing reads ``ENDPOINT_DOWN``.
* **Unknown is not fine.** A model with no endpoint record, no credential
  record, or no runtime row is *unknown*, and unknown fails closed
  (``ENDPOINT_DOWN`` / ``CREDENTIALS_INVALID`` / ``RUNTIME_LIMIT``) with a
  reason that says "unknown" — a missing record never means "assumed
  working". The one dimension that is not fail-closed is a router built with no
  runtime registry at all (``runtime_ok=None``), and its ``AVAILABLE`` reason
  says in words that runtime limits were *not checked*.
* **"Installed" and "permitted" stay distinct tool failures.** A required tool
  absent from the unit's own toolset is ``TOOL_MISSING``; a required tool the
  unit carries but ``allowed_tools`` does not permit is ``PERMISSION_DENIED``.
  Only *required* tools are gated — an unused, unpermitted tool in a wider
  toolset must not fail a unit the task does not need it for.
* **Nothing here executes anything.** These are pure checks over injected
  registries. No test calls a model, a tool, a verifier or an endpoint; this is
  a tested planner, not a dispatch decision any runtime makes yet.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

#: Placeholder instructions reference used by the router's synthesised
#: baseline unit (see :class:`JointRouter`). It is a literal "none supplied",
#: never a reference to an instructions file that may or may not exist.
UNSPECIFIED_INSTRUCTIONS_REF = "unspecified"

#: Placeholder verification method for the synthesised baseline unit. A unit
#: that names no verification method has nothing runnable to verify itself
#: with, and reports ``VERIFICATION_UNRUNNABLE`` — the honest state, not
#: "available, verification not applicable".
NO_VERIFICATION_METHOD = "none"


class JointComponent(StrEnum):
    """The parts of one joint execution unit (plan §14.1), in its order."""

    MODEL = "model"
    INSTRUCTIONS = "instructions"
    SKILLS = "skills"
    TOOLSET = "toolset"
    CONTEXT_SLICE = "context_slice"
    VERIFICATION_METHOD = "verification_method"


class Availability(StrEnum):
    """The distinct states the plan requires a registry to separate.

    Plan §14.1 asks the registry to tell "model can perform an operation in
    principle" apart from "endpoint is currently available", "tool is
    installed and permitted", "credentials are valid", "runtime can meet
    limits", and "verification method is executable". The first of those is
    :attr:`MODEL_LACKS_CAPABILITY` — a unit whose model does not provide a
    required capability fails for a reason none of the operational states
    describes, and folding it into another (a "runtime limit", say) would
    misreport *why* a unit cannot run.
    """

    MODEL_LACKS_CAPABILITY = "model_lacks_capability"
    AVAILABLE = "available"
    ENDPOINT_DOWN = "endpoint_down"
    CREDENTIALS_INVALID = "credentials_invalid"
    TOOL_MISSING = "tool_missing"
    PERMISSION_DENIED = "permission_denied"
    RUNTIME_LIMIT = "runtime_limit"
    VERIFICATION_UNRUNNABLE = "verification_unrunnable"


def _non_empty(value: str, field: str) -> str:
    """Validate a non-empty string field."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string, got {value!r}")
    return value


def _names(values: Sequence[str], field: str) -> tuple[str, ...]:
    """Materialise a tuple of non-empty names, refusing empty entries."""
    entries = tuple(values)
    for entry in entries:
        if not isinstance(entry, str) or not entry.strip():
            raise ValueError(f"{field} entries must be non-empty strings, got {entry!r}")
    return entries


@dataclass(frozen=True)
class ExecutionConfiguration:
    """One joint execution unit: model + instructions + skills + toolset + context + verification.

    ``model_config``, ``instructions_ref`` and ``verification_method`` are
    REQUIRED non-empty: a unit that names no model is not a configuration, and
    one that names no verification method cannot be verified — an absent one is
    refused rather than defaulted to "whatever". The tuple fields
    (``skills``, ``toolset``, ``context_slice_ids``) default to empty, which is
    an honest statement that the unit declares nothing there.

    This record is a *declaration*. It claims nothing about whether the model
    can be reached, the credentials work, or the verification runs; that is
    exactly what :class:`JointRouter` resolves separately, one state at a time.
    """

    model_config: str
    instructions_ref: str
    skills: tuple[str, ...] = ()
    toolset: tuple[str, ...] = ()
    context_slice_ids: tuple[str, ...] = ()
    verification_method: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "model_config", _non_empty(self.model_config, "model_config"))
        object.__setattr__(self, "instructions_ref", _non_empty(self.instructions_ref, "instructions_ref"))
        object.__setattr__(self, "verification_method", _non_empty(self.verification_method, "verification_method"))
        object.__setattr__(self, "skills", _names(self.skills, "skills"))
        object.__setattr__(self, "toolset", _names(self.toolset, "toolset"))
        object.__setattr__(self, "context_slice_ids", _names(self.context_slice_ids, "context_slice_ids"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_config": self.model_config,
            "instructions_ref": self.instructions_ref,
            "skills": list(self.skills),
            "toolset": list(self.toolset),
            "context_slice_ids": list(self.context_slice_ids),
            "verification_method": self.verification_method,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionConfiguration:
        return cls(
            model_config=str(data["model_config"]),
            instructions_ref=str(data["instructions_ref"]),
            skills=tuple(str(skill) for skill in data.get("skills", ())),
            toolset=tuple(str(tool) for tool in data.get("toolset", ())),
            context_slice_ids=tuple(str(slice_id) for slice_id in data.get("context_slice_ids", ())),
            verification_method=str(data["verification_method"]),
        )


@dataclass(frozen=True)
class JointEligibility:
    """One joint unit's single resolved availability state, with the reason.

    ``failed_component`` is the point of the record: a state that is not
    ``AVAILABLE`` REQUIRES it (naming *which* part of the joint unit failed),
    and an ``AVAILABLE`` row REFUSES one — "available" and "something inside
    failed" cannot both be true of one unit.

    ``reason`` is REQUIRED non-empty and names the failing component, so a
    caller reading the reason alone learns what to change.
    """

    configuration: ExecutionConfiguration
    availability: Availability
    failed_component: JointComponent | None
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.configuration, ExecutionConfiguration):
            raise ValueError(f"configuration must be an ExecutionConfiguration, got {self.configuration!r}")
        try:
            object.__setattr__(self, "availability", Availability(str(self.availability)))
        except ValueError as error:
            allowed = ", ".join(state.value for state in Availability)
            raise ValueError(f"unknown availability {self.availability!r}; expected one of: {allowed}") from error
        if self.failed_component is not None:
            try:
                object.__setattr__(self, "failed_component", JointComponent(str(self.failed_component)))
            except ValueError as error:
                allowed = ", ".join(component.value for component in JointComponent)
                raise ValueError(f"unknown failed_component {self.failed_component!r}; expected one of: {allowed}") from error
        if self.availability is Availability.AVAILABLE and self.failed_component is not None:
            raise ValueError(f"availability '{self.availability.value}' must not carry a failed_component ({self.failed_component.value}): one unit has one state")
        if self.availability is not Availability.AVAILABLE and self.failed_component is None:
            raise ValueError(f"availability '{self.availability.value}' requires a failed_component: an ineligible unit that names no failing component gives a caller nothing to fix")
        _non_empty(self.reason, "reason")

    @property
    def is_available(self) -> bool:
        """Shorthand for ``availability is AVAILABLE``."""
        return self.availability is Availability.AVAILABLE

    def to_dict(self) -> dict[str, Any]:
        return {
            "configuration": self.configuration.to_dict(),
            "availability": self.availability.value,
            "failed_component": None if self.failed_component is None else self.failed_component.value,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JointEligibility:
        return cls(
            configuration=ExecutionConfiguration.from_dict(data["configuration"]),
            # Coerced (and validated with the friendly message) in __post_init__.
            availability=data["availability"],
            # Coerced (and validated with the friendly message) in __post_init__.
            failed_component=data.get("failed_component"),
            reason=str(data["reason"]),
        )


def _bool_flags(flags: Mapping[str, bool], field: str) -> dict[str, bool]:
    """Validate a boolean state mapping. A non-bool value is a caller bug:
    ``"yes"`` is not a record of a credential being valid, so accepting a
    truthy string here would make ``credentials[model]`` mean something
    different depending on its type.
    """
    state = dict(flags)
    for key, value in state.items():
        _non_empty(key, f"{field} keys")
        if not isinstance(value, bool):
            raise ValueError(f"{field}[{key!r}] must be a bool, got {value!r}: a state nobody recorded is unknown, not true")
    return state


class JointRouter:
    """Resolve candidate joint units to exactly one availability state each.

    The router reads six injected registries and knows nothing else — no
    price, no budget, no latency model. For each candidate unit
    :meth:`evaluate` applies the plan's checks in this order and reports the
    **first** failure, so the state it names is the reason the unit cannot run
    right now:

    1. the model provides every required capability (``MODEL_LACKS_CAPABILITY``);
    2. its endpoint is up (``ENDPOINT_DOWN``; no record = unknown = down);
    3. its credentials are valid (``CREDENTIALS_INVALID``; no record = unknown
       = not valid);
    4. every *required* tool is in the unit's toolset (``TOOL_MISSING``) and
       permitted (``PERMISSION_DENIED``);
    5. the runtime is within limits (``RUNTIME_LIMIT``; no registry at all
       means the check is skipped and the ``AVAILABLE`` reason says so, while a
       registry with no row for this model fails closed);
    6. the verification method is executable here
       (``VERIFICATION_UNRUNNABLE``).

    A unit that passes all six is ``AVAILABLE`` — and its reason says what was
    *not* checked: instructions, skills and the context slice are declarations
    this router has no registry for, so it reports them as declared, never as
    verified.

    When no ``configurations`` are supplied the router synthesises one
    baseline unit per model it knows about, using the caller's required
    capabilities and tools as the unit's skills and toolset and the literal
    placeholders :data:`UNSPECIFIED_INSTRUCTIONS_REF` /
    :data:`NO_VERIFICATION_METHOD` for the two fields it cannot invent. Those
    placeholders are honest absences, not invented references — and the
    synthesised unit consequently reports ``VERIFICATION_UNRUNNABLE``. Pass
    real :class:`ExecutionConfiguration` rows for any decision that matters.
    """

    def __init__(
        self,
        capabilities: Mapping[str, Sequence[str]],
        credentials: Mapping[str, bool],
        allowed_tools: frozenset[str],
        verification_executable: frozenset[str],
        endpoint_up: Mapping[str, bool],
        runtime_ok: Mapping[str, bool] | None = None,
        configurations: Sequence[ExecutionConfiguration] | None = None,
    ) -> None:
        models = {str(model): _names(tuple(provided), f"capabilities[{model!r}]") for model, provided in dict(capabilities).items()}
        if not models:
            raise ValueError("capabilities must name at least one model config: a router with no models routes nothing")
        for model in models:
            _non_empty(model, "capabilities keys")
        self._capabilities = models
        self._credentials = _bool_flags(credentials, "credentials")
        self._allowed_tools = frozenset(_names(tuple(allowed_tools), "allowed_tools"))
        self._verification_executable = frozenset(_names(tuple(verification_executable), "verification_executable"))
        self._endpoint_up = _bool_flags(endpoint_up, "endpoint_up")
        self._runtime_ok = None if runtime_ok is None else _bool_flags(runtime_ok, "runtime_ok")
        if configurations is None:
            self._configurations: tuple[ExecutionConfiguration, ...] | None = None
            return
        rows = tuple(configurations)
        if not rows:
            raise ValueError("configurations must be non-empty when supplied: an empty candidate list would evaluate to no rows")
        for row in rows:
            if not isinstance(row, ExecutionConfiguration):
                raise ValueError(f"configurations must contain ExecutionConfiguration rows, got {row!r}")
        self._configurations = rows

    def _candidates(self, required_capabilities: tuple[str, ...], required_tools: tuple[str, ...]) -> tuple[ExecutionConfiguration, ...]:
        if self._configurations is not None:
            return self._configurations
        return tuple(
            ExecutionConfiguration(
                model_config=model,
                instructions_ref=UNSPECIFIED_INSTRUCTIONS_REF,
                skills=required_capabilities,
                toolset=required_tools,
                verification_method=NO_VERIFICATION_METHOD,
            )
            for model in self._capabilities
        )

    def _resolve(self, configuration: ExecutionConfiguration, required_capabilities: tuple[str, ...], required_tools: tuple[str, ...]) -> JointEligibility:
        model = configuration.model_config

        provided = self._capabilities.get(model)
        if provided is None:
            return JointEligibility(
                configuration=configuration,
                availability=Availability.MODEL_LACKS_CAPABILITY,
                failed_component=JointComponent.MODEL,
                reason=f"{JointComponent.MODEL.value} component: model {model!r} is not in the capability registry (unknown is not capable)",
            )
        for capability in required_capabilities:
            if capability not in provided:
                return JointEligibility(
                    configuration=configuration,
                    availability=Availability.MODEL_LACKS_CAPABILITY,
                    failed_component=JointComponent.MODEL,
                    reason=f"{JointComponent.MODEL.value} component: model {model!r} does not provide required capability {capability!r}",
                )

        endpoint = self._endpoint_up.get(model)
        if endpoint is not True:
            state = "no endpoint record for this model (unknown, so not up)" if endpoint is None else "endpoint is down"
            return JointEligibility(configuration=configuration, availability=Availability.ENDPOINT_DOWN, failed_component=JointComponent.MODEL, reason=f"{JointComponent.MODEL.value} component: {state}")

        credential = self._credentials.get(model)
        if credential is not True:
            state = "no credential record for this model (unknown, so not valid)" if credential is None else "credentials are invalid"
            return JointEligibility(configuration=configuration, availability=Availability.CREDENTIALS_INVALID, failed_component=JointComponent.MODEL, reason=f"{JointComponent.MODEL.value} component: {state}")

        for tool in required_tools:
            if tool not in configuration.toolset:
                return JointEligibility(
                    configuration=configuration,
                    availability=Availability.TOOL_MISSING,
                    failed_component=JointComponent.TOOLSET,
                    reason=f"{JointComponent.TOOLSET.value} component: required tool {tool!r} is not in the configuration's toolset (not installed for this unit)",
                )
            if tool not in self._allowed_tools:
                return JointEligibility(
                    configuration=configuration,
                    availability=Availability.PERMISSION_DENIED,
                    failed_component=JointComponent.TOOLSET,
                    reason=f"{JointComponent.TOOLSET.value} component: required tool {tool!r} is in the configuration's toolset but is not permitted",
                )

        if self._runtime_ok is not None and self._runtime_ok.get(model) is not True:
            recorded = self._runtime_ok.get(model)
            state = "no runtime record for this model (unknown, so not within limits)" if recorded is None else "runtime is not within the memory/latency limits"
            return JointEligibility(configuration=configuration, availability=Availability.RUNTIME_LIMIT, failed_component=JointComponent.MODEL, reason=f"{JointComponent.MODEL.value} component: {state}")

        if configuration.verification_method not in self._verification_executable:
            return JointEligibility(
                configuration=configuration,
                availability=Availability.VERIFICATION_UNRUNNABLE,
                failed_component=JointComponent.VERIFICATION_METHOD,
                reason=f"{JointComponent.VERIFICATION_METHOD.value} component: {configuration.verification_method!r} is not executable in this environment",
            )

        checked = "capabilities, endpoint, credentials, tools"
        if self._runtime_ok is not None:
            checked += ", runtime"
        return JointEligibility(
            configuration=configuration,
            availability=Availability.AVAILABLE,
            failed_component=None,
            reason=(
                f"available: {checked} and verification method {configuration.verification_method!r} all pass"
                + ("" if self._runtime_ok is not None else " (runtime limits not checked: no runtime registry supplied)")
                + f"; {JointComponent.INSTRUCTIONS.value}, {JointComponent.SKILLS.value} and {JointComponent.CONTEXT_SLICE.value} are declared, not verified here"
            ),
        )

    def evaluate(self, required_capabilities: Sequence[str], required_tools: Sequence[str]) -> tuple[JointEligibility, ...]:
        """Resolve every candidate unit against one task's requirements.

        Returns one :class:`JointEligibility` per candidate unit, in candidate
        order, so the result is deterministic and a caller can diff two
        evaluations. ``required_capabilities`` must be non-empty: with nothing
        required, every unit would report ``AVAILABLE`` and the check would
        certify units it was never asked about. ``required_tools`` may be empty
        — a model-only unit is a real configuration — in which case the tool
        gate is vacuous and says so by being silent.
        """
        capabilities = _names(tuple(required_capabilities), "required_capabilities")
        if not capabilities:
            raise ValueError("required_capabilities must be non-empty: a joint router with nothing to check would call every unit available")
        tools = _names(tuple(required_tools), "required_tools")
        return tuple(self._resolve(configuration, capabilities, tools) for configuration in self._candidates(capabilities, tools))


__all__ = [
    "NO_VERIFICATION_METHOD",
    "UNSPECIFIED_INSTRUCTIONS_REF",
    "Availability",
    "ExecutionConfiguration",
    "JointComponent",
    "JointEligibility",
    "JointRouter",
]
