# `alpha.budgets` — spend policy

Answers the question an operator asks before a run starts: **"may this
deployment spend money?"** — and refuses, in code, when the answer is no.

This package is **additive and not a second cost accountant.**

## The gap it closes

Alpha already had:

| Concern | Owner |
|---|---|
| Token metering (process-local) | `alpha.runtime.token_meter.TokenMeter` |
| Scoped per-task token ceiling | `TokenBudgetMiddleware` |
| Durable spend + daily/scope **circuit breaker** | `alpha.models.cost_governor.CostGovernor` |
| Per-1M prices for the console report | `config.yaml -> model_pricing` / `models[].pricing` |

What none of them could answer is *"before this model is constructed, may we
build it at all?"* The source plan's `execution_budget_mode: zero_cost` did not
exist in code — `AppConfig` had no spend section, and `create_chat_model` had no
gate.

## What this package owns

* **`spend.py`** — `SpendMode`, `SpendPolicyConfig`, `FreeVerdict`,
  `PaidCallRefused`, `is_model_free`, `estimate_cost_usd`,
  `refuse_paid_model`, `resolve_spend_policy`.
* Enforcement is **one call site**: `alpha.models.factory.create_chat_model`.

## What this package does NOT own

* **Spend accounting.** `CostGovernor` remains the durable, budget-enforcing
  view. Nothing here records or charges a token.
* **Token budgets.** `TokenBudgetMiddleware` and `TokenMeter` are untouched.
* **Pricing data.** Prices are read from `config.yaml`; this package never holds
  a table of its own beyond the documented per-1k defaults.

## Five load-bearing invariants

1. **An unpriced model reports `None`, never `0.0`.**
   `CostGovernor.MODEL_COST_PER_1K` falls back to a `"default"` rate, so an
   unknown model is charged a *fabricated* figure and a free one is charged a
   real one. `estimate_cost_usd` returns `None` and says why. A caller that
   treats `None` as `0` concludes an unpriced model is free — the one
   conclusion that must never be reached by accident.

2. **An unpriced model is never *assumed* free.** Absence of a price is not a
   price of zero. `is_model_free` returns `source="unpriced"`, `is_free=False`,
   and `zero_cost` refuses it.

3. **The configured keyless router is free by adapter provenance.** The
   `alpha.models.free_router:ChatFreeLLM` adapter may use only the declared
   `free_gateways`; its configured model name (`alpha-free`) is not treated as
   an arbitrary unpriced model. A direct OpenAI-compatible model is recognized
   as free only when both its endpoint host and model id match a declared
   keyless gateway. A `-free` label alone proves nothing.

4. **Free is zero on BOTH axes, computed not trusted.** Providers label
   free-input/paid-output models `:free` too. This is the rule
   `models/discovery.py` already applies; the `:free` suffix alone is honoured
   only through a **declared** `free_gateways` entry.

5. **The default changes nothing.** No `spend:` section means
   `SpendMode.BUDGETED`, which is exactly today's behaviour. A misspelled mode
   is a **load error**, not a silent fallback to the permissive default — a
   typo in `zero_cost` must not read as `budgeted` while the operator believes
   a budget is enforced.

## Pricing: two namespaces, one trap

Both are priced **per million tokens** and differ only in *key names*:

| Namespace | Keys |
|---|---|
| `models[].pricing` | `input_per_million` / `output_per_million` |
| `model_pricing:` | `input` / `output` |

Reading one shape for both is how a configured price silently prices nothing —
the same defect class the `models[].pricing` schema exists to prevent.
`_pricing_table` accepts both and converts once.

`input_cache_hit_per_million` is deliberately **not** consulted: cache-aware
pricing belongs to the console's reporting path, and mixing it into an
admission decision would make the two disagree about the same call.

## Enforcement point

`create_chat_model` gates **every member of the resolved fallback chain**, not
just the primary:

```python
_spend_policy = resolve_spend_policy(config)
if _spend_policy.forbids_paid_models:
    for _member in chain:
        refuse_paid_model(_member.name, policy=_spend_policy, config=config)
```

A free primary with a paid fallback spends nothing until the fallback fires. A
gate that inspected only the primary would enforce nothing exactly when it
matters most. The gate sits **after** chain resolution (you cannot judge a model
before you know its name) and **before** any client is constructed — so a
refused model never reaches a provider.

## Honesty boundary

This package does **not** claim:

* **That `budgeted` mode is a ceiling.** It meters tokens and trips the existing
  `CostGovernor` breaker; `max_cost_usd_per_run` / `max_daily_cost_usd` are
  declared for operator visibility, and the enforced ceilings remain
  `CostGovernor.BudgetConfig`.
* **That a `:free` gateway stays free.** Gateway reachability changes without
  notice; a documented model that stops serving keyless traffic simply fails.
* **Anything about sandboxing or isolation.** Spend policy is orthogonal to
  `alpha.tools.governance` and the authority ceiling.

## Tests

`backend/tests/test_spend_policy.py`:

* `None` vs `0.0` for unpriced models, both directions;
* both pricing key shapes resolving to the **same** number;
* the free-verdict sources, and that the order prefers `local_model`;
* the declared keyless router and direct gateway endpoint/model binding in
  `zero_cost` mode;
* free-input/paid-output is **not** free; a bare `:free` with no declared
  gateway is not free;
* `zero_cost` refusing a paid model, an unpriced model, and **a paid fallback
  member**, driven through the **real** `create_chat_model` with a real
  `AppConfig`;
* the gate inert in `budgeted` mode and with no `spend:` section at all.

## Wiring

* `config/spend_config_schema.py` — typed `SpendConfig` on `AppConfig.spend`.
* `config.example.yaml` — the `spend:` section with the mode semantics inline.
* `alpha.capabilities.catalog` — `spend_policy` (kind `guard`).
