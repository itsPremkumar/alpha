"""Typed contracts for scenario-conditioned memory recall.

The scenario router deliberately deals in names, weights, and costs.  It does
not know how a memory subsystem is implemented or how its content is fetched.
That boundary is what lets independently-owned memory packages register a
surface without importing one another.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Scenario(StrEnum):
    """The closed set of work contexts understood by the router."""

    CODING = "coding"
    RESEARCH = "research"
    OPERATIONS = "operations"
    PLANNING = "planning"
    LEARNING = "learning"
    CONVERSATION = "conversation"
    INCIDENT = "incident"
    GENERAL = "general"
    DEFAULT = "general"


SCENARIOS: tuple[Scenario, ...] = (
    Scenario.CODING,
    Scenario.RESEARCH,
    Scenario.OPERATIONS,
    Scenario.PLANNING,
    Scenario.LEARNING,
    Scenario.CONVERSATION,
    Scenario.INCIDENT,
    Scenario.GENERAL,
)
DEFAULT_SCENARIO = Scenario.GENERAL

# These are aliases accepted only at tolerant input boundaries (for example a
# hand-written YAML override).  Model output and the public Scenario enum stay
# closed to the values above.
_SCENARIO_ALIASES: dict[str, Scenario] = {
    "code": Scenario.CODING,
    "coding": Scenario.CODING,
    "research": Scenario.RESEARCH,
    "investigation": Scenario.RESEARCH,
    "ops": Scenario.OPERATIONS,
    "operation": Scenario.OPERATIONS,
    "operations": Scenario.OPERATIONS,
    "plan": Scenario.PLANNING,
    "planning": Scenario.PLANNING,
    "learn": Scenario.LEARNING,
    "learning": Scenario.LEARNING,
    "study": Scenario.LEARNING,
    "chat": Scenario.CONVERSATION,
    "conversation": Scenario.CONVERSATION,
    "incident": Scenario.INCIDENT,
    "incident_response": Scenario.INCIDENT,
    "outage": Scenario.INCIDENT,
    "general": Scenario.GENERAL,
    "default": Scenario.GENERAL,
}


def parse_scenario(value: Any, *, default: Scenario | None = None) -> Scenario | None:
    """Parse a scenario without ever inventing a label.

    ``default`` is returned only for an unknown/missing value.  Callers that
    need the documented safe fallback should pass :data:`DEFAULT_SCENARIO`.
    """

    if isinstance(value, Scenario):
        return value
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not text:
        return default
    return _SCENARIO_ALIASES.get(text, default)


def _as_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "yes", "on", "1"}:
        return True
    if text in {"false", "no", "off", "0", ""}:
        return False
    return default


def _as_text(value: Any, *, limit: int = 2000) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if text else None


def _as_terms(value: Any, *, limit: int = 128) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values: list[Any] = [value]
    elif isinstance(value, (set, frozenset)):
        values = sorted(value, key=str)
    elif isinstance(value, (list, tuple)):
        values = list(value)
    else:
        values = [value]
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = str(item or "").strip().lower()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text[:128])
        if len(result) >= limit:
            break
    return result


def _clamp_float(value: Any, low: float, high: float, default: float) -> tuple[float, bool]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default, True
    if not math.isfinite(number):
        return default, True
    bounded = max(low, min(high, number))
    return bounded, bounded != number


def _clamp_cost(value: Any, default: int = 1) -> tuple[int, bool]:
    try:
        number = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default, True
    bounded = max(1, number)
    return bounded, bounded != number


class ScenarioSignals(BaseModel):
    """Facts observed for the current turn, supplied by the host runtime.

    The model contains no callbacks or store handles.  A middleware can build
    it from ordinary request data, while tests can inject the same signals
    directly and remain hermetic.
    """

    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    declared_intent: str | None = None
    tools_used: list[str] = Field(default_factory=list)
    file_kinds: list[str] = Field(default_factory=list)
    has_error: bool = False
    has_diff: bool = False
    is_incident: bool = False
    time_of_day: str | None = None
    project_hints: list[str] = Field(default_factory=list)
    explicit_override: Scenario | str | None = None

    @model_validator(mode="before")
    @classmethod
    def _accept_signal_aliases(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        aliases = {
            "explicit_user_override": "explicit_override",
            "user_override": "explicit_override",
            "override": "explicit_override",
            "project_hint": "project_hints",
            "project": "project_hints",
            "files": "file_kinds",
            "file_types": "file_kinds",
            "tools": "tools_used",
            "tool_names": "tools_used",
        }
        for source, target in aliases.items():
            if target not in data and source in data:
                data[target] = data[source]
        if "declared_intent" in data and isinstance(data["declared_intent"], Scenario):
            data["declared_intent"] = data["declared_intent"].value
        return data

    @field_validator("tools_used", "file_kinds", "project_hints", mode="before")
    @classmethod
    def _normalise_terms(cls, value: Any) -> list[str]:
        return _as_terms(value)

    @field_validator("declared_intent", "time_of_day", mode="before")
    @classmethod
    def _normalise_text(cls, value: Any) -> str | None:
        if isinstance(value, Scenario):
            return value.value
        return _as_text(value)

    @field_validator("explicit_override", mode="before")
    @classmethod
    def _normalise_override(cls, value: Any) -> Scenario | str | None:
        if isinstance(value, Scenario):
            return value
        text = _as_text(value)
        if text is None:
            return None
        return parse_scenario(text, default=None) or text

    @field_validator("has_error", "has_diff", "is_incident", mode="before")
    @classmethod
    def _normalise_bool(cls, value: Any) -> bool:
        return _as_bool(value)

    @property
    def explicit_user_override(self) -> Scenario | str | None:
        """Compatibility/readability alias for the explicit override field."""

        return self.explicit_override

    @explicit_user_override.setter
    def explicit_user_override(self, value: str | Scenario | None) -> None:
        self.explicit_override = value.value if isinstance(value, Scenario) else value

    @property
    def has_explicit_override(self) -> bool:
        return bool(self.explicit_override)

    @property
    def normalized_project_hints(self) -> tuple[str, ...]:
        return tuple(self.project_hints)

    def as_prompt_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly view for an injected model seam."""

        return self.model_dump(mode="json")


class MemorySurface(BaseModel):
    """A named recall surface and its routing metadata.

    ``scenarios`` is deliberately a closed set of names.  Registration never
    imports or instantiates the subsystem that owns the surface.
    """

    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    name: str
    description: str = ""
    scenarios: list[Scenario] = Field(default_factory=lambda: list(SCENARIOS))
    base_weight: float = 0.5
    cost_units: int = 1
    priority: int = 0
    disclosures: list[str] = Field(default_factory=list)
    clamped_fields: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalise_surface(cls, value: Any) -> Any:
        if isinstance(value, MemorySurface):
            value = value.model_dump()
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        aliases = {
            "serves": "scenarios",
            "served_scenarios": "scenarios",
            "scenario_names": "scenarios",
            "weight": "base_weight",
            "budget_units": "cost_units",
            "cost": "cost_units",
            "rank": "priority",
        }
        for source, target in aliases.items():
            if target not in data and source in data:
                data[target] = data[source]

        disclosures = [str(item) for item in (data.get("disclosures") or []) if str(item).strip()]
        clamped: list[str] = []
        weight, changed = _clamp_float(data.get("base_weight", 0.5), 0.0, 1.0, 0.5)
        if changed:
            clamped.append("base_weight")
            disclosures.append("base_weight clamped to [0, 1]")
        data["base_weight"] = weight
        cost, changed = _clamp_cost(data.get("cost_units", 1), 1)
        if changed:
            clamped.append("cost_units")
            disclosures.append("cost_units clamped to at least 1")
        data["cost_units"] = cost
        try:
            data["priority"] = int(data.get("priority", 0))
        except (TypeError, ValueError, OverflowError):
            data["priority"] = 0
            clamped.append("priority")
            disclosures.append("priority replaced with 0")

        raw_scenarios = data.get("scenarios", SCENARIOS)
        if isinstance(raw_scenarios, (str, Scenario)):
            raw_scenarios = [raw_scenarios]
        parsed: list[Scenario] = []
        unknown: list[str] = []
        for item in raw_scenarios or []:
            scenario = parse_scenario(item)
            if scenario is None:
                unknown.append(str(item))
            elif scenario not in parsed:
                parsed.append(scenario)
        if unknown:
            disclosures.append("unknown scenarios ignored: " + ", ".join(sorted(unknown)))
        data["scenarios"] = [item for item in SCENARIOS if item in parsed]
        data["disclosures"] = _merge_disclosures(disclosures)
        data["clamped_fields"] = _merge_disclosures(clamped)
        return data

    @field_validator("base_weight", mode="before")
    @classmethod
    def _clamp_base_weight(cls, value: Any) -> float:
        return _clamp_float(value, 0.0, 1.0, 0.5)[0]

    @field_validator("cost_units", mode="before")
    @classmethod
    def _clamp_surface_cost(cls, value: Any) -> int:
        return _clamp_cost(value, 1)[0]

    @field_validator("name", mode="before")
    @classmethod
    def _normalise_name(cls, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("memory surface name must not be empty")
        return text[:128]

    @field_validator("description", mode="before")
    @classmethod
    def _normalise_description(cls, value: Any) -> str:
        return str(value or "").strip()[:1000]

    @property
    def serves(self) -> list[Scenario]:
        """Alias used by registry clients that speak in terms of service."""

        return self.scenarios

    @property
    def scenario_names(self) -> tuple[str, ...]:
        return tuple(item.value for item in self.scenarios)

    @property
    def weight(self) -> float:
        return self.base_weight

    @property
    def cost(self) -> int:
        return self.cost_units

    @property
    def was_clamped(self) -> bool:
        return bool(self.clamped_fields)

    @property
    def clamp_disclosure(self) -> str:
        return "none" if not self.disclosures else "; ".join(self.disclosures)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class SurfaceRoute(BaseModel):
    """One selected surface with its effective weight and charged budget."""

    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    name: str
    weight: float
    budget_units: int
    disclosures: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalise_route(cls, value: Any) -> Any:
        if isinstance(value, SurfaceRoute):
            value = value.model_dump()
        if isinstance(value, (tuple, list)) and len(value) == 3:
            value = {"name": value[0], "weight": value[1], "budget_units": value[2]}
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        if "budget_units" not in data:
            for alias in ("cost_units", "budget", "cost"):
                if alias in data:
                    data["budget_units"] = data[alias]
                    break
        disclosures = [str(item) for item in (data.get("disclosures") or []) if str(item).strip()]
        weight, changed = _clamp_float(data.get("weight", data.get("base_weight", 0.0)), 0.0, 1.0, 0.0)
        if changed:
            disclosures.append("weight clamped to [0, 1]")
        cost, changed = _clamp_cost(data.get("budget_units", 1), 1)
        if changed:
            disclosures.append("budget_units clamped to at least 1")
        data["weight"] = weight
        data["budget_units"] = cost
        data["disclosures"] = _merge_disclosures(disclosures)
        return data

    @field_validator("weight", mode="before")
    @classmethod
    def _clamp_route_weight(cls, value: Any) -> float:
        return _clamp_float(value, 0.0, 1.0, 0.0)[0]

    @field_validator("budget_units", mode="before")
    @classmethod
    def _clamp_route_budget(cls, value: Any) -> int:
        return _clamp_cost(value, 1)[0]

    @field_validator("name", mode="before")
    @classmethod
    def _normalise_route_name(cls, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("routed surface name must not be empty")
        return text[:128]

    def __iter__(self) -> Iterator[Any]:  # type: ignore[override]
        """Allow ``name, weight, budget = route`` tuple-style unpacking."""

        yield self.name
        yield self.weight
        yield self.budget_units

    def __getitem__(self, index: int) -> Any:
        return (self.name, self.weight, self.budget_units)[index]

    def as_tuple(self) -> tuple[str, float, int]:
        return self.name, self.weight, self.budget_units

    def __eq__(self, other: object) -> bool:
        if isinstance(other, (tuple, list)) and len(other) == 3:
            return self.as_tuple() == tuple(other)
        return super().__eq__(other)

    @property
    def cost_units(self) -> int:
        return self.budget_units


class RoutingPlan(BaseModel):
    """The deterministic, inspectable result of one routing decision."""

    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    scenario: Scenario = DEFAULT_SCENARIO
    confidence: float = 0.0
    surfaces: list[SurfaceRoute] = Field(default_factory=list)
    total_budget_units: int = 0
    reason: str = ""
    status: str = "fallback"
    enabled: bool = True
    budget_limit_units: int = 0
    omitted_surfaces: list[str] = Field(default_factory=list)
    disclosures: list[str] = Field(default_factory=list)
    clamped_fields: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalise_plan(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        disclosures = data.get("disclosures")
        if not isinstance(disclosures, (list, tuple)):
            disclosures = []
        disclosures = list(disclosures)
        clamped = data.get("clamped_fields")
        if not isinstance(clamped, (list, tuple)):
            clamped = []
        clamped = list(clamped)
        if "confidence" in data:
            confidence, changed = _clamp_float(data.get("confidence"), 0.0, 1.0, 0.0)
            if changed:
                disclosures.append("confidence clamped to [0, 1]")
                clamped.append("confidence")
            data["confidence"] = confidence
        data["disclosures"] = _merge_disclosures(disclosures)
        data["clamped_fields"] = _merge_disclosures(clamped)
        parsed = parse_scenario(data.get("scenario", DEFAULT_SCENARIO), default=DEFAULT_SCENARIO)
        if parsed is None:
            parsed = DEFAULT_SCENARIO
        data["scenario"] = parsed
        routes: list[Any] = []
        for item in data.get("surfaces", []) or []:
            if isinstance(item, str):
                routes.append({"name": item, "weight": 0.0, "budget_units": 1})
            else:
                routes.append(item)
        data["surfaces"] = routes
        return data

    @model_validator(mode="after")
    def _account_for_routes(self) -> RoutingPlan:
        computed = sum(route.budget_units for route in self.surfaces)
        if computed != self.total_budget_units:
            object.__setattr__(
                self,
                "disclosures",
                _merge_disclosures([*self.disclosures, f"total_budget_units recomputed to selected cost {computed}"]),
            )
            object.__setattr__(
                self,
                "clamped_fields",
                _merge_disclosures([*self.clamped_fields, "total_budget_units"]),
            )
            object.__setattr__(self, "total_budget_units", computed)
        if self.total_budget_units < 0:
            object.__setattr__(self, "total_budget_units", 0)
        return self

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp_plan_confidence(cls, value: Any) -> float:
        return _clamp_float(value, 0.0, 1.0, 0.0)[0]

    @property
    def surface_names(self) -> tuple[str, ...]:
        return tuple(route.name for route in self.surfaces)

    @property
    def chosen_surfaces(self) -> tuple[str, ...]:
        return self.surface_names

    @property
    def surface_tuples(self) -> list[tuple[str, float, int]]:
        return [route.as_tuple() for route in self.surfaces]

    @property
    def is_fallback(self) -> bool:
        return self.scenario is DEFAULT_SCENARIO or self.status in {
            "fallback",
            "no_signal",
            "ambiguous",
            "below_threshold",
            "model_error",
            "invalid_model_output",
        }

    @property
    def was_clamped(self) -> bool:
        return bool(self.clamped_fields)

    @property
    def clamp_disclosure(self) -> str:
        return "none" if not self.disclosures else "; ".join(self.disclosures)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def explain(self) -> str:
        return self.reason or f"scenario {self.scenario.value} selected without a recorded reason"


class ClassificationResult(BaseModel):
    """Closed-status result returned by :class:`ScenarioClassifier`."""

    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    scenario: Scenario = DEFAULT_SCENARIO
    confidence: float = 0.0
    status: str = "fallback"
    reason: str = ""
    source: str = "deterministic"
    model_status: str = ""
    disclosures: list[str] = Field(default_factory=list)
    clamped_fields: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalise_result(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        parsed = parse_scenario(data.get("scenario", DEFAULT_SCENARIO), default=DEFAULT_SCENARIO)
        data["scenario"] = parsed or DEFAULT_SCENARIO
        confidence, changed = _clamp_float(data.get("confidence", 0.0), 0.0, 1.0, 0.0)
        data["confidence"] = confidence
        disclosures = data.get("disclosures")
        if not isinstance(disclosures, (list, tuple)):
            disclosures = []
        disclosures = list(disclosures)
        clamped = data.get("clamped_fields")
        if not isinstance(clamped, (list, tuple)):
            clamped = []
        clamped = list(clamped)
        if changed:
            disclosures.append("confidence clamped to [0, 1]")
            clamped.append("confidence")
        data["disclosures"] = _merge_disclosures(disclosures)
        data["clamped_fields"] = _merge_disclosures(clamped)
        return data

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp_result_confidence(cls, value: Any) -> float:
        return _clamp_float(value, 0.0, 1.0, 0.0)[0]

    @property
    def deciding_signal(self) -> str:
        return self.reason

    @property
    def was_clamped(self) -> bool:
        return bool(self.clamped_fields)

    @property
    def clamp_disclosure(self) -> str:
        return "none" if not self.disclosures else "; ".join(self.disclosures)

    @property
    def ok(self) -> bool:
        return self.status in {"ok", "override", "signals", "intent", "model"}


# Descriptive aliases keep the wire contract discoverable for callers that use
# ``surface``/``outcome`` terminology.
RoutedSurface = SurfaceRoute
SurfaceSelection = SurfaceRoute
ClassificationOutcome = ClassificationResult


def _merge_disclosures(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


__all__ = [
    "DEFAULT_SCENARIO",
    "SCENARIOS",
    "ClassificationOutcome",
    "ClassificationResult",
    "MemorySurface",
    "RoutingPlan",
    "Scenario",
    "ScenarioSignals",
    "RoutedSurface",
    "SurfaceSelection",
    "SurfaceRoute",
    "parse_scenario",
]
