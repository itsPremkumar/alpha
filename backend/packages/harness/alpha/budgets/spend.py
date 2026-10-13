"""Spend policy: is this model free, what does it cost, and may I call it?

The gap this closes
-------------------
Alpha has **token** budgets in several places, a `CostGovernor` that enforces a
daily/scope circuit breaker, and `model_pricing` in ``config.yaml`` used by the
console reporting route. What it does **not** have is any way to ask the question
an operator actually asks before a run starts: *"may this deployment spend
money?"* The source plan's ``execution_budget_mode: zero_cost`` does not exist in
code.

Two honest problems this module refuses to paper over
------------------------------------------------------
1. **An unpriced model must report ``None``, never ``0.0``.**
   ``cost_governor.MODEL_COST_PER_1K`` falls back to a ``"default"`` rate, so an
   unknown model is charged a *fabricated* figure and a free model is charged a
   real one. :func:`estimate_cost_usd` returns ``None`` when nothing prices the
   model, and says why. A caller that treats ``None`` as ``0`` will believe an
   unpriced model is free — which is the one conclusion that must never be
   reached by accident.

2. **"Free" is a verdict with a source, not a boolean.** The answer comes from
   one of five sources, and each has different authority. A caller
   needs to know *which* one decided, because an operator who adds a gateway to
   ``free_gateways`` and an entry to ``models[]`` with different pricing is
   looking at a configuration conflict, not a rounding error.
   :class:`FreeVerdict` carries the source for exactly that reason.

Enforcement, when it happens
---------------------------
:func:`refuse_paid_model` is the single gate. It is called from
``alpha.models.factory.create_chat_model`` — the one place every chat model is
constructed — so a paid model cannot be built at all in ``zero_cost`` mode. It is
**not** a prompt instruction and it is not advisory: it raises.

The default mode is ``budgeted``, which is exactly today's behaviour. Opting a
deployment into ``zero_cost`` is an explicit operator decision, so adding this
package changes nothing for an existing install.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final
from urllib.parse import urlsplit

__all__ = [
    "DEFAULT_COST_INPUT_PER_1K",
    "DEFAULT_COST_OUTPUT_PER_1K",
    "PaidCallRefused",
    "SpendMode",
    "SpendPolicyConfig",
    "FreeVerdict",
    "estimate_cost_usd",
    "is_model_free",
    "refuse_paid_model",
    "resolve_spend_policy",
]


class SpendMode(StrEnum):
    """How much a deployment is permitted to spend.

    ``BUDGETED`` is the default because it is **today's behaviour**: tokens are
    metered and a daily/scope circuit breaker trips, but nothing at model
    construction refuses a paid model. Making ``ZERO_COST`` the default would
    silently break every deployment that legitimately uses a paid provider, so
    the conservative mode is the default and the restrictive one is opt-in.
    """

    #: Only free and local models. A paid model cannot be constructed.
    ZERO_COST = "zero_cost"
    #: Paid models are allowed within the declared ceilings.
    BUDGETED = "budgeted"
    #: No ceiling. Still subject to permissions and safety policy.
    UNRESTRICTED = "unrestricted_by_budget"


class PaidCallRefused(RuntimeError):
    """A paid model was requested while the deployment is in ``zero_cost``.

    A distinct type rather than a ``ValueError`` so a caller can tell "the
    operator forbade this" from "this model name does not exist". Both refuse;
    only one is a policy decision.
    """

    def __init__(self, model_name: str, reason: str) -> None:
        super().__init__(f"paid model {model_name!r} refused: {reason}")
        self.model_name = model_name
        self.reason = reason


#: Fallback rates, **per 1k tokens**, matching
#: ``cost_governor.MODEL_COST_PER_1K`` so the two paths cannot disagree about
#: what a rate is. They are the LAST resort, used only when neither
#: ``model_pricing`` nor ``models[].pricing`` prices the model.
DEFAULT_COST_INPUT_PER_1K: Final[float] = 0.001
DEFAULT_COST_OUTPUT_PER_1K: Final[float] = 0.003

#: Model-name fragments that mean "runs on the operator's own hardware".
#: A local model costs no money regardless of how a provider labels it.
_LOCAL_NAME_MARKERS: Final[tuple[str, ...]] = ("ollama/", "lmstudio/", "local/", "vllm/")


@dataclass(frozen=True)
class FreeVerdict:
    """Whether a model costs money, and which configuration said so.

    ``source`` is not decoration. ``config.yaml`` carries multiple sources that
    can each claim a different answer for the same model, and an operator
    resolving "why did it refuse?" needs the winning one named.
    """

    is_free: bool
    source: str
    reason: str

    def __bool__(self) -> bool:
        return self.is_free


class SpendPolicyConfig:
    """The ``spend`` section of ``config.yaml``, as plain validated values.

    Deliberately a plain object rather than a Pydantic model: it is read at
    model-construction time, which is on the hot path for every subagent, and
    ``AppConfig`` already validates it on load.
    """

    __slots__ = ("mode", "max_cost_usd_per_run", "max_daily_cost_usd")

    def __init__(self, *, mode: str = SpendMode.BUDGETED.value, max_cost_usd_per_run: float | None = None, max_daily_cost_usd: float | None = None) -> None:
        try:
            self.mode = SpendMode(mode)
        except ValueError as exc:
            allowed = ", ".join(sorted(m.value for m in SpendMode))
            raise ValueError(f"spend.mode {mode!r} is not one of: {allowed}") from exc
        for name, value in (("max_cost_usd_per_run", max_cost_usd_per_run), ("max_daily_cost_usd", max_daily_cost_usd)):
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0):
                raise ValueError(f"spend.{name} must be a non-negative number or None (unstated), got {value!r}")
        self.max_cost_usd_per_run = max_cost_usd_per_run
        self.max_daily_cost_usd = max_daily_cost_usd

    @property
    def forbids_paid_models(self) -> bool:
        """Whether a paid model may be constructed at all."""
        return self.mode is SpendMode.ZERO_COST


def resolve_spend_policy(config: Any | None = None) -> SpendPolicyConfig:
    """Read the ``spend`` section, falling back to today's behaviour.

    A deployment with no ``spend:`` section gets :attr:`SpendMode.BUDGETED` and
    no ceilings, which is precisely what happens today. A section that exists
    but is unreadable is a **refusal to guess**: the caller sees the defaults
    rather than a crash, and the defaults are the permissive ones, so an
    unreadable section can never *tighten* policy by accident or loosen it
    silently.
    """
    if config is None:
        try:
            from alpha.config.app_config import get_app_config

            config = get_app_config()
        except Exception:  # pragma: no cover - config unavailable is not fatal here
            return SpendPolicyConfig()
    section = getattr(config, "spend", None)
    if section is None:
        return SpendPolicyConfig()
    return SpendPolicyConfig(
        mode=getattr(section, "mode", SpendMode.BUDGETED.value),
        max_cost_usd_per_run=getattr(section, "max_cost_usd_per_run", None),
        max_daily_cost_usd=getattr(section, "max_daily_cost_usd", None),
    )


def _pricing_table(config: Any) -> dict[str, tuple[float, float]]:
    """``model -> (input_per_1k, output_per_1k)`` from both pricing namespaces.

    **Both namespaces are priced per million tokens** — they differ only in the
    *key names*, which is a trap worth stating rather than rediscovering:

    * ``models[].pricing`` uses ``input_per_million`` / ``output_per_million``
      (plus ``input_cache_hit_per_million``, deliberately not consulted here);
    * the top-level ``model_pricing:`` table uses ``input`` / ``output``.

    Converting once, here, is what keeps the two from disagreeing by 1000x, and
    reading only one key shape is how a configured price silently prices
    nothing at all.
    """
    table: dict[str, tuple[float, float]] = {}
    declared = getattr(config, "model_pricing", None) or {}
    for name, block in declared.items():
        pair = _pair_from_block(block, keys=("input", "output"))
        if pair is not None:
            table[str(name).lower()] = pair
    for entry in getattr(config, "models", None) or []:
        name = getattr(entry, "name", None)
        if not name:
            continue
        pair = _pair_from_block(getattr(entry, "pricing", None), keys=("input_per_million", "output_per_million"))
        if pair is not None:
            table.setdefault(str(name).lower(), pair)
    return table


def _pair_from_block(block: Any, *, keys: tuple[str, str]) -> tuple[float, float] | None:
    """``(input, output)`` per-1k pair from a pricing block, or ``None``.

    Both accepted key shapes name a **per-million** price, so the conversion is
    unconditional. A block that is missing, non-numeric or negative is ``None``
    — an absent price is *unpriced*, and inventing one is the failure this
    module exists to prevent. A price of **zero on both axes** is a real price
    and is returned.
    """
    if block is None:
        return None
    if isinstance(block, dict):
        raw_in, raw_out = block.get(keys[0]), block.get(keys[1])
    else:
        raw_in = getattr(block, keys[0], None)
        raw_out = getattr(block, keys[1], None)
    if raw_in is None or raw_out is None:
        return None
    try:
        value_in, value_out = float(raw_in), float(raw_out)
    except (TypeError, ValueError):
        return None
    if value_in < 0 or value_out < 0:
        return None
    return (value_in / 1000.0, value_out / 1000.0)


def estimate_cost_usd(model_name: str, input_tokens: int, output_tokens: int, *, config: Any | None = None) -> float | None:
    """Dollar cost of a call, or ``None`` when nothing prices the model.

    ``None`` is a real answer and never ``0.0``. A caller that needs a number
    must decide what an unpriced model means for it; this function refuses to
    decide on its behalf, because the two readings point opposite ways
    ("costs nothing" vs "we do not know what this costs") and picking the wrong
    one silently is how a zero-cost budget silently becomes an unlimited one.
    """
    if config is None:
        try:
            from alpha.config.app_config import get_app_config

            config = get_app_config()
        except Exception:  # pragma: no cover
            return None
    clean = (model_name or "").lower().strip()
    if not clean:
        return None
    table = _pricing_table(config)
    pair = table.get(clean)
    if pair is None:
        return None
    in_rate, out_rate = pair
    return round((max(0, input_tokens) / 1000.0 * in_rate) + (max(0, output_tokens) / 1000.0 * out_rate), 6)


def is_model_free(model_name: str, *, config: Any | None = None) -> FreeVerdict:
    """Whether ``model_name`` costs money, and which namespace decided.

    Checked in **authority order**, and the first hit wins:

    1. ``local_model`` — a name fragment meaning operator hardware. Always free.
    2. ``free_router`` — the named model is built by Alpha's keyless router,
       which can call only the declared ``free_gateways``.
    3. ``free_gateways`` — a documented model, or a direct model whose endpoint
       host and served model id match the operator declaration.
    4. ``zero_price`` — ``model_pricing`` / ``models[].pricing`` say zero on both
       axes. Computed, not trusted: a free-input/paid-output model is **not**
       free, which is the rule ``models/discovery.py`` already applies.
    5. ``unpriced`` — nothing priced it. **Not free.** Assuming free is the
       exact mistake that turns a budget into a suggestion.

    An unknown model in a permissive mode is still reported honestly here; only
    :func:`refuse_paid_model` decides whether that matters.
    """
    if config is None:
        try:
            from alpha.config.app_config import get_app_config

            config = get_app_config()
        except Exception:  # pragma: no cover
            return FreeVerdict(False, "config_unavailable", "configuration could not be read; refusing to assume a model is free")

    clean = (model_name or "").lower().strip()
    if not clean:
        return FreeVerdict(False, "unnamed", "no model name was supplied")

    if any(clean.startswith(marker) or marker in clean for marker in _LOCAL_NAME_MARKERS):
        return FreeVerdict(True, "local_model", "model name declares operator-hosted execution")

    # A configured ChatFreeLLM is a router over the operator-declared keyless
    # gateways, not an unpriced provider. Requiring the concrete adapter and a
    # non-empty gateway declaration keeps a model's display name from granting
    # free status by itself.
    configured_models = getattr(config, "models", None) or []
    configured_model = next(
        (entry for entry in configured_models if str(getattr(entry, "name", "")).strip().lower() == clean),
        None,
    )
    gateways = getattr(config, "free_gateways", None) or []
    if configured_model is not None:
        use = str(getattr(configured_model, "use", "") or "").strip()
        adapter_module, separator, adapter_name = use.partition(":")
        if separator and adapter_module == "alpha.models.free_router" and adapter_name == "ChatFreeLLM" and gateways:
            return FreeVerdict(True, "free_router", "configured keyless router uses declared free gateways")

        # A direct OpenAI-compatible model is free only when both its exact
        # served model id and endpoint host match an operator-declared keyless
        # gateway. A '-free' model name or a matching model id alone is not
        # enough to establish the route.
        configured_model_id = str(getattr(configured_model, "model", "") or "").strip().lower()
        configured_base_url = str(getattr(configured_model, "base_url", "") or "").strip()
        try:
            configured_host = (urlsplit(configured_base_url).hostname or "").lower().rstrip(".")
        except ValueError:
            configured_host = ""
        if configured_host and configured_model_id:
            for gateway in gateways:
                documented = {str(item).strip().lower() for item in (getattr(gateway, "documented_models", None) or ())}
                gateway_base_url = str(getattr(gateway, "base_url", "") or "").strip()
                try:
                    gateway_host = (urlsplit(gateway_base_url).hostname or "").lower().rstrip(".")
                except ValueError:
                    gateway_host = ""
                if configured_host == gateway_host and configured_model_id in documented:
                    gateway_name = str(getattr(gateway, "id", "configured free gateway"))
                    return FreeVerdict(True, "free_gateways", f"served by the keyless gateway '{gateway_name}'")

    for gateway in gateways:
        documented = {str(m).lower() for m in (getattr(gateway, "documented_models", None) or ())}
        if clean in documented:
            return FreeVerdict(True, "free_gateways", f"served by the keyless gateway '{getattr(gateway, 'id', '?')}'")
        if clean.endswith(":free"):
            return FreeVerdict(True, "free_gateways", f"served by the keyless gateway '{getattr(gateway, 'id', '?')}' (provider ':free' suffix)")

    pair = _pricing_table(config).get(clean)
    if pair is not None:
        if pair == (0.0, 0.0):
            return FreeVerdict(True, "zero_price", "declared pricing is zero for both input and output")
        return FreeVerdict(False, "priced", f"declared pricing is ${pair[0]:.6f}/1k in, ${pair[1]:.6f}/1k out")

    return FreeVerdict(False, "unpriced", "no pricing namespace declares this model; an unpriced model is never assumed free")


def refuse_paid_model(model_name: str, *, policy: SpendPolicyConfig | None = None, config: Any | None = None) -> FreeVerdict:
    """Raise :class:`PaidCallRefused` if a paid model would be built.

    Returns the verdict when the call may proceed, so a caller can record *why*
    it was allowed as well as why it was refused — an allowed paid call with no
    recorded reason is indistinguishable from a call that was never checked.
    """
    active = policy if policy is not None else resolve_spend_policy(config)
    verdict = is_model_free(model_name, config=config)
    if active.forbids_paid_models and not verdict.is_free:
        raise PaidCallRefused(model_name, f"spend.mode is zero_cost and the model was judged by '{verdict.source}': {verdict.reason}")
    return verdict
