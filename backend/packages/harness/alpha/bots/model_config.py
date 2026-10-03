"""Per-bot LLM model configuration: declaration, validation, resolution.

Every Bot profile may carry its own ``model_config`` block, so one roster can
run a flagship model on the lead, a cheap model on the triage bot and a
mixture-of-agents panel on the reviewer — without touching ``config.yaml``.

Design rules, all of them load-bearing:

- **``config.yaml`` stays the only place a model *exists*.** This block names
  models from ``models[]``; it never declares one. A name that resolves to
  nothing is an error, never a silent degradation to the default (the exact
  bug ``model_routing`` already had to remove).
- **Fail closed, report everything.** ``validate_bot_model_config`` collects
  *every* error before raising, because a UI that reveals one problem per save
  costs one round trip each.
- **Resolution is pure and side-effect free.** ``resolve_model_plan`` reads the
  profile plus the request and returns the answer *and where each part came
  from*. The precedence lives in one function instead of being re-derived by
  every caller, and the ``*_source`` fields are what makes the "full detail"
  view honest rather than a rendering guess.
- **Secrets and egress never live here.** Bot profiles serialise to JSON, ride
  ``.alphabot.json`` exports and are written by API. Sampling settings are
  therefore scalar-only, and credential-shaped keys plus ``base_url`` are
  refused: BYO endpoints are screened once, at the operator config layer.

Precedence, strongest first::

    request  >  bot.model_config  >  bot.model  >  custom agent  >  global default
"""

from __future__ import annotations

import json
import logging
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar

from alpha.config.reasoning_effort import is_effort, normalize_effort

logger = logging.getLogger(__name__)

#: Ordered fallbacks a bot may declare. Mirrors ``ModelConfig.fallbacks``'s
#: ``max_length`` so a bot cannot build a chain the factory would refuse.
MAX_FALLBACKS = 5

#: Reference models in a mixture panel, and the aggregator fan-out pool.
MAX_MIXTURE_REFERENCES = 8

#: ``strategy`` values a mixture may declare. ``parallel`` fans out at once;
#: ``sequential`` feeds each reference the previous one's answer (a pipeline).
MIXTURE_STRATEGIES = ("parallel", "sequential")

#: Alpha metadata that must never arrive as a sampling override — these steer
#: factory resolution and would divert into the request payload if they reached
#: a provider constructor (see ``factory._NON_CONSTRUCTOR_MODEL_KEYS``).
_SETTINGS_METADATA_KEYS = frozenset({"name", "model", "use", "provider", "fallbacks", "pricing", "display_name", "description"})

#: Credential- and egress-shaped keys. A bot profile is untrusted-ish state:
#: it serialises to JSON, travels in exports and is writable over the API.
_SECRET_KEY_MARKERS = ("api_key", "apikey", "api-key", "token", "secret", "password", "passwd", "authorization", "credential", "key_env", "key_cmd", "headers", "base_url", "endpoint")

#: Scalar-only settings keep the block JSON-safe and stop a nested document
#: from being smuggled into a provider constructor.
_SETTING_SCALAR_TYPES = (str, int, float, bool)

#: Well-known decoding parameters. These are checked *before* the credential
#: screen, because the naive substring markers above would refuse
#: ``max_tokens`` for containing "token" — the exact false positive that would
#: force operators to give up per-bot sampling entirely. Anything not on this
#: list still faces the credential/egress screen (fail closed).
_SAFE_SAMPLING_KEYS = frozenset(
    {
        "temperature",
        "top_p",
        "top_k",
        "top_a",
        "min_p",
        "typical_p",
        "max_tokens",
        "max_completion_tokens",
        "max_output_tokens",
        "min_tokens",
        "max_input_tokens",
        "presence_penalty",
        "frequency_penalty",
        "repetition_penalty",
        "length_penalty",
        "penalty_scale",
        "seed",
        "stop",
        "stop_sequences",
        "n",
        "logit_bias",
        "logprobs",
        "top_logprobs",
        "best_of",
        "include_stop_sequences",
        "response_format",
        "structured_outputs",
        "tool_choice",
        "parallel_tool_calls",
        "user",
        "service_tier",
        "safety_settings",
        "candidate_count",
    }
)


class BotModelConfigError(ValueError):
    """Raised when a bot's declared ``model_config`` cannot be accepted.

    Carries every problem found (``issues``), each with a machine-readable
    ``code``, the ``field`` it sits on and a human message naming the value.
    """

    def __init__(self, issues: list[ModelConfigIssue]):
        self.issues = list(issues)
        summary = "; ".join(f"{i.field}: {i.message}" for i in self.issues) or "invalid model_config"
        super().__init__(summary)


@dataclass(frozen=True)
class ModelConfigIssue:
    """One finding about a declared or resolved bot model configuration."""

    code: str
    field: str
    message: str
    severity: str = "error"  # "error" | "warning"

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "field": self.field, "message": self.message, "severity": self.severity}


@dataclass
class MixtureConfig:
    """Mixture-of-agents / multi-model panel declared by one Bot.

    ``references`` answer in parallel (or in sequence); ``aggregator`` synthesises
    their perspectives into one answer. Disabled by default so an untouched
    profile spends nothing it did not already spend.
    """

    enabled: bool = False
    references: list[str] = field(default_factory=list)
    aggregator: str | None = None
    strategy: str = "parallel"
    max_workers: int = 4

    @property
    def is_empty(self) -> bool:
        return not self.enabled and not self.references and not self.aggregator

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "references": list(self.references),
            "aggregator": self.aggregator,
            "strategy": self.strategy,
            "max_workers": self.max_workers,
        }

    @classmethod
    def from_dict(cls, data: Any) -> MixtureConfig:
        if not isinstance(data, dict):
            return cls()
        references = data.get("references")
        return cls(
            enabled=bool(data.get("enabled", False)),
            references=[str(r).strip() for r in references if str(r).strip()] if isinstance(references, list) else [],
            aggregator=(str(data["aggregator"]).strip() or None) if data.get("aggregator") else None,
            strategy=str(data.get("strategy") or "parallel").strip().lower(),
            max_workers=int(data.get("max_workers") or 4),
        )


#: Panel members a bot may convene for model counselling (consensus), and the
#: deliberation rounds they may run. Both bounds keep a bot from spawning an
#: unbounded fan-out of paid calls from a profile edit.
MAX_COUNSEL_MEMBERS = 5
MAX_COUNSEL_ROUNDS = 5

#: Pool a mixture panel may draw its fan-out workers from.
MAX_MIXTURE_WORKERS = 8


@dataclass
class CounselConfig:
    """Model counselling / consensus settings declared by one Bot.

    The panel members answer independently and their agreement is measured;
    ``quorum`` is the number of agreeing members required. Disabled by default
    so an untouched profile never convenes a panel.
    """

    enabled: bool = False
    members: list[str] = field(default_factory=list)
    rounds: int = 1
    quorum: int = 0  # 0 = simple majority of the panel
    effort: str | None = None

    @property
    def is_empty(self) -> bool:
        return not self.enabled and not self.members and not self.effort

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "members": list(self.members),
            "rounds": self.rounds,
            "quorum": self.quorum,
            "effort": self.effort,
        }

    @classmethod
    def from_dict(cls, data: Any) -> CounselConfig:
        if not isinstance(data, dict):
            return cls()
        members = data.get("members")
        effort = data.get("effort")
        return cls(
            enabled=bool(data.get("enabled", False)),
            members=[str(m).strip() for m in members if str(m).strip()] if isinstance(members, list) else [],
            rounds=int(data.get("rounds") or 1),
            quorum=int(data.get("quorum") or 0),
            effort=(str(effort).strip() or None) if effort else None,
        )


@dataclass
class BotModelConfig:
    """Validated per-bot model configuration.

    Nothing here *declares* a model: every string names an entry the operator
    already put in ``config.yaml`` ``models[]``. An empty config means "inherit
    everything", which is what an untouched profile resolves to.
    """

    primary: str | None = None
    fallbacks: tuple[str, ...] = ()
    counsel: CounselConfig | None = None
    mixture: MixtureConfig | None = None
    sampling: dict[str, Any] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not (self.primary or self.fallbacks or self.counsel or self.mixture or self.sampling)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.primary:
            out["primary"] = self.primary
        if self.fallbacks:
            out["fallbacks"] = list(self.fallbacks)
        if self.counsel and not self.counsel.is_empty:
            out["counsel"] = self.counsel.to_dict()
        if self.mixture and not self.mixture.is_empty:
            out["mixture"] = self.mixture.to_dict()
        if self.sampling:
            out["sampling"] = dict(self.sampling)
        return out

    @classmethod
    def from_dict(cls, data: Any) -> BotModelConfig:
        """Tolerant read for legacy/adjacent callers.

        Strict validation lives in :func:`validate_bot_model_config`; this only
        shapes whatever is there into the typed form so a stored profile never
        crashes a reader.
        """
        if not isinstance(data, Mapping):
            return cls()
        primary = data.get("primary")
        fallbacks = data.get("fallbacks")
        sampling = data.get("sampling")
        counsel = data.get("counsel")
        mixture = data.get("mixture")
        return cls(
            primary=str(primary).strip() or None if primary else None,
            fallbacks=tuple(str(f).strip() for f in fallbacks if str(f).strip()) if isinstance(fallbacks, Sequence) and not isinstance(fallbacks, str) else (),
            counsel=CounselConfig.from_dict(counsel) if isinstance(counsel, Mapping) else None,
            mixture=MixtureConfig.from_dict(mixture) if isinstance(mixture, Mapping) else None,
            sampling=dict(sampling) if isinstance(sampling, Mapping) else {},
        )


_TOP_LEVEL_KEYS = frozenset({"primary", "fallbacks", "counsel", "mixture", "sampling"})

#: Sampling keys that steer reasoning rather than raw decoding; validated
#: against the canonical effort ladder instead of being passed through.
_EFFORT_KEYS = frozenset({"effort", "reasoning_effort"})


def _names(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def _opt_str(value: Any) -> str | None:
    """Trimmed non-empty string, or ``None`` for anything else."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    return text or None


def validate_bot_model_config(
    raw: Any,
    *,
    known_models: Collection[str],
    field_prefix: str = "model_config",
) -> BotModelConfig:
    """Validate a raw ``model_config`` block, collecting **every** error.

    Raises :class:`BotModelConfigError` carrying one :class:`ModelConfigIssue`
    per problem, so the UI can render the whole list in one save instead of
    revealing one issue per round trip.

    ``known_models`` is the operator's ``models[]`` name set. A name absent
    from it is an error, never a silent degradation to the default — the same
    fail-closed rule ``model_routing`` validation already applies at load.
    """
    issues: list[ModelConfigIssue] = []

    def err(code: str, field: str, message: str) -> None:
        issues.append(ModelConfigIssue(code=code, field=field, message=message, severity="error"))

    if raw is None:
        return BotModelConfig()
    if not isinstance(raw, Mapping):
        err("not_mapping", field_prefix, "model_config must be an object")
        raise BotModelConfigError(issues)

    known = {str(m).strip() for m in known_models}

    def check_name(kind: str, name: str, field: str) -> None:
        if name not in known:
            err(
                "unknown_model",
                field,
                f"{kind} '{name}' is not declared in config.yaml models[]" + (" (empty models list: declare models first)" if not known else ""),
            )

    for key in raw:
        if key not in _TOP_LEVEL_KEYS:
            err("unknown_key", f"{field_prefix}.{key}", f"unknown key '{key}' (accepted: {', '.join(sorted(_TOP_LEVEL_KEYS))})")

    primary_name: str | None = None
    if raw.get("primary"):
        value = raw.get("primary")
        if isinstance(value, (list, tuple, dict, set)) or isinstance(value, bool):
            err("bad_primary", f"{field_prefix}.primary", "primary must be a single model name")
        else:
            candidate = _opt_str(value)
            if candidate is None:
                err("bad_primary", f"{field_prefix}.primary", "primary must be a single model name")
            else:
                primary_name = candidate
                check_name("primary model", primary_name, f"{field_prefix}.primary")

    fallbacks = _names(raw.get("fallbacks"))
    if len(fallbacks) > MAX_FALLBACKS:
        err("too_many_fallbacks", f"{field_prefix}.fallbacks", f"at most {MAX_FALLBACKS} fallbacks allowed, got {len(fallbacks)}")
    if len(set(fallbacks)) != len(fallbacks):
        err("duplicate_fallback", f"{field_prefix}.fallbacks", "fallbacks must be unique")
    if primary_name and primary_name in fallbacks:
        err("fallback_cycle", f"{field_prefix}.fallbacks", f"primary '{primary_name}' cannot also be a fallback")
    for i, name in enumerate(fallbacks):
        check_name("fallback", name, f"{field_prefix}.fallbacks[{i}]")

    counsel_raw = raw.get("counsel")
    counsel: CounselConfig | None = None
    if counsel_raw is not None:
        if not isinstance(counsel_raw, Mapping):
            err("bad_counsel", f"{field_prefix}.counsel", "counsel must be an object")
        else:
            unknown = set(counsel_raw) - {"enabled", "members", "rounds", "quorum", "effort"}
            for key in sorted(unknown):
                err("unknown_key", f"{field_prefix}.counsel.{key}", f"unknown key '{key}'")
            cand = CounselConfig.from_dict(dict(counsel_raw))
            members = _names(counsel_raw.get("members"))
            if len(members) > MAX_COUNSEL_MEMBERS:
                err("too_many_members", f"{field_prefix}.counsel.members", f"at most {MAX_COUNSEL_MEMBERS} panel members allowed, got {len(members)}")
            if len(set(members)) != len(members):
                err("duplicate_member", f"{field_prefix}.counsel.members", "panel members must be unique")
            for i, name in enumerate(members):
                check_name("panel member", name, f"{field_prefix}.counsel.members[{i}]")
            if not (1 <= cand.rounds <= MAX_COUNSEL_ROUNDS):
                err("bad_rounds", f"{field_prefix}.counsel.rounds", f"rounds must be between 1 and {MAX_COUNSEL_ROUNDS}, got {cand.rounds}")
            if cand.quorum < 0 or (members and cand.quorum > len(members)):
                err("bad_quorum", f"{field_prefix}.counsel.quorum", f"quorum must be between 0 (majority) and {len(members)}, got {cand.quorum}")
            if cand.effort is not None and not is_effort(cand.effort):
                err("bad_effort", f"{field_prefix}.counsel.effort", f"'{cand.effort}' is not a canonical reasoning effort rung")
            if cand.enabled and not members:
                err("counsel_no_members", f"{field_prefix}.counsel.members", "an enabled counsel panel requires at least one member")
            counsel = cand

    mixture_raw = raw.get("mixture")
    mixture: MixtureConfig | None = None
    if mixture_raw is not None:
        if not isinstance(mixture_raw, Mapping):
            err("bad_mixture", f"{field_prefix}.mixture", "mixture must be an object")
        else:
            unknown = set(mixture_raw) - {"enabled", "references", "aggregator", "strategy", "max_workers"}
            for key in sorted(unknown):
                err("unknown_key", f"{field_prefix}.mixture.{key}", f"unknown key '{key}'")
            references = _names(mixture_raw.get("references"))
            strategy = str(mixture_raw.get("strategy") or "parallel").strip().lower()
            try:
                cand_m = MixtureConfig.from_dict(dict(mixture_raw))
            except (TypeError, ValueError) as exc:
                err("bad_mixture", f"{field_prefix}.mixture", str(exc))
                cand_m = None
            if strategy not in MIXTURE_STRATEGIES:
                err("bad_strategy", f"{field_prefix}.mixture.strategy", f"strategy must be one of {', '.join(MIXTURE_STRATEGIES)}, got '{strategy}'")
            if len(references) > MAX_MIXTURE_REFERENCES:
                err("too_many_references", f"{field_prefix}.mixture.references", f"at most {MAX_MIXTURE_REFERENCES} references allowed, got {len(references)}")
            if len(set(references)) != len(references):
                err("duplicate_reference", f"{field_prefix}.mixture.references", "references must be unique")
            for i, name in enumerate(references):
                check_name("reference", name, f"{field_prefix}.mixture.references[{i}]")
            aggregator = str(mixture_raw.get("aggregator") or "").strip()
            if aggregator:
                check_name("aggregator", aggregator, f"{field_prefix}.mixture.aggregator")
            if references and not aggregator:
                err("mixture_no_aggregator", f"{field_prefix}.mixture.aggregator", "a mixture with references requires an aggregator to synthesise them")
            workers = int(mixture_raw.get("max_workers") or 4)
            if not (1 <= workers <= MAX_MIXTURE_WORKERS):
                err("bad_workers", f"{field_prefix}.mixture.max_workers", f"max_workers must be between 1 and {MAX_MIXTURE_WORKERS}, got {workers}")
            if cand_m is not None and cand_m.enabled and not references:
                err("mixture_no_references", f"{field_prefix}.mixture.references", "an enabled mixture requires at least one reference")
            mixture = cand_m

    sampling_raw = raw.get("sampling")
    sampling: dict[str, Any] = {}
    if sampling_raw is not None:
        if not isinstance(sampling_raw, Mapping):
            err("bad_sampling", f"{field_prefix}.sampling", "sampling must be an object")
        else:
            for key, value in sampling_raw.items():
                field = f"{field_prefix}.sampling.{key}"
                k = str(key).strip().lower()
                if not k:
                    err("empty_sampling_key", field, "sampling keys must be non-empty")
                    continue
                if k in _SETTINGS_METADATA_KEYS:
                    err("metadata_key", field, f"'{key}' steers factory resolution and cannot be a sampling override")
                    continue
                # A known decoding parameter is already screened; anything else
                # faces the credential/egress screen (fail closed).
                if k not in _SAFE_SAMPLING_KEYS and any(marker in k for marker in _SECRET_KEY_MARKERS):
                    err("secret_key", field, f"'{key}' looks credential- or endpoint-shaped; secrets and BYO endpoints never live in a bot profile")
                    continue
                if isinstance(value, bool) or not isinstance(value, _SETTING_SCALAR_TYPES):
                    err("non_scalar", field, f"sampling values must be scalars (str/int/float/bool), got {type(value).__name__}")
                    continue
                if k in _EFFORT_KEYS:
                    if not is_effort(value):
                        err("bad_effort", field, f"'{value}' is not a canonical reasoning effort rung")
                        continue
                    sampling[str(key)] = normalize_effort(value)
                    continue
                sampling[str(key)] = value

    if issues:
        raise BotModelConfigError(issues)
    return BotModelConfig(
        primary=primary_name,
        fallbacks=tuple(fallbacks),
        counsel=counsel,
        mixture=mixture,
        sampling=sampling,
    )


# ── Resolution ─────────────────────────────────────────────────────────────
#
# Pure and side-effect free: in goes the profile plus whatever the request
# carried, out comes the plan *and where every part of it came from*. The
# precedence is decided exactly once, here; a caller re-deriving it is how the
# UI and the runtime start disagreeing about which model a bot runs.


@dataclass(frozen=True)
class ModelPlan:
    """The resolved model plan for one run, with per-value provenance."""

    primary: str | None
    primary_source: str
    fallbacks: tuple[str, ...]
    fallbacks_source: str
    counsel: CounselConfig | None
    counsel_source: str
    mixture: MixtureConfig | None
    mixture_source: str
    sampling: dict[str, Any]
    sampling_source: str

    #: Precedence labels, strongest first, in their canonical order. Exposed so
    #: the UI renders the ladder instead of inventing its own wording.
    SOURCES: ClassVar[tuple[str, ...]] = (
        "request",
        "bot.model_config",
        "bot.model",
        "custom_agent",
        "default",
    )

    @property
    def effective_model(self) -> str | None:
        return self.primary

    @property
    def has_bot_override(self) -> bool:
        return self.primary_source in {"request", "bot.model_config", "bot.model"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary": self.primary,
            "primary_source": self.primary_source,
            "fallbacks": list(self.fallbacks),
            "fallbacks_source": self.fallbacks_source,
            "counsel": self.counsel.to_dict() if self.counsel and not self.counsel.is_empty else None,
            "counsel_source": self.counsel_source,
            "mixture": self.mixture.to_dict() if self.mixture and not self.mixture.is_empty else None,
            "mixture_source": self.mixture_source,
            "sampling": dict(self.sampling),
            "sampling_source": self.sampling_source,
        }


def resolve_model_plan(
    *,
    request_model: str | None = None,
    bot: Any = None,
    custom_agent_model: str | None = None,
    default_model: str | None = None,
    bot_model_config: BotModelConfig | None = None,
) -> ModelPlan:
    """Resolve the model plan for one run.

    Precedence, strongest first::

        request  >  bot.model_config  >  bot.model  >  custom agent  >  default

    ``bot`` is anything carrying ``model`` and (optionally) ``model_config`` —
    a ``BotProfile`` or a plain dict — so callers do not have to normalise
    first. Pass ``bot_model_config`` pre-validated to skip the tolerant read.

    Every resolved value carries its source; nothing is guessed downstream.
    """
    if bot_model_config is None:
        bot_model_config = BotModelConfig.from_dict(getattr(bot, "model_config", None)) if bot is not None else None
    bot_model_config = bot_model_config or BotModelConfig()

    bot_model = _opt_str(getattr(bot, "model", None)) if bot is not None else None

    # ── primary ──
    if _opt_str(request_model):
        primary, primary_source = _opt_str(request_model), "request"
    elif bot_model_config.primary:
        primary, primary_source = bot_model_config.primary, "bot.model_config"
    elif bot_model:
        primary, primary_source = bot_model, "bot.model"
    elif _opt_str(custom_agent_model):
        primary, primary_source = _opt_str(custom_agent_model), "custom_agent"
    else:
        primary, primary_source = _opt_str(default_model), "default"

    # ── fallbacks ──
    # A bot-declared chain *replaces* the primary model's own chain rather than
    # appending to it: mixing the two would make the effective order depend on
    # declaration order nobody can see from the UI.
    if bot_model_config.fallbacks:
        fallbacks, fallbacks_source = bot_model_config.fallbacks, "bot.model_config"
    else:
        fallbacks, fallbacks_source = (), "primary_model"

    # ── counselling / mixture / sampling: bot config only, else inherited ──
    counsel = bot_model_config.counsel
    counsel_source = "bot.model_config" if counsel and not counsel.is_empty else "default"
    mixture = bot_model_config.mixture
    mixture_source = "bot.model_config" if mixture and not mixture.is_empty else "default"
    sampling = dict(bot_model_config.sampling)
    sampling_source = "bot.model_config" if sampling else "default"

    return ModelPlan(
        primary=primary,
        primary_source=primary_source,
        fallbacks=fallbacks,
        fallbacks_source=fallbacks_source,
        counsel=counsel,
        counsel_source=counsel_source,
        mixture=mixture,
        mixture_source=mixture_source,
        sampling=sampling,
        sampling_source=sampling_source,
    )


def describe_model_plan(plan: ModelPlan, *, bot_name: str = "") -> dict[str, Any]:
    """Full-detail description of a resolved plan for the UI.

    Includes the precedence ladder itself, so the detail panel can show *why*
    a value won rather than only what won.
    """
    return {
        "bot": bot_name,
        "plan": plan.to_dict(),
        "precedence": list(ModelPlan.SOURCES),
        "limits": {
            "max_fallbacks": MAX_FALLBACKS,
            "max_mixture_references": MAX_MIXTURE_REFERENCES,
            "max_counsel_members": MAX_COUNSEL_MEMBERS,
            "max_counsel_rounds": MAX_COUNSEL_ROUNDS,
            "max_mixture_workers": MAX_MIXTURE_WORKERS,
            "mixture_strategies": list(MIXTURE_STRATEGIES),
        },
    }


def fingerprint_surface(bot_model_config: BotModelConfig | None) -> dict[str, Any] | None:
    """Epoch-fingerprint contribution of a bot's model config, or ``None``.

    Returns ``None`` when the config is empty so existing profiles hash
    byte-identically: an untouched bot must not churn its capability epoch the
    moment this field exists.
    """
    if bot_model_config is None or bot_model_config.is_empty:
        return None
    return json.loads(json.dumps(bot_model_config.to_dict(), sort_keys=True))
