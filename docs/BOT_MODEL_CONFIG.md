# Per-bot model configuration

Every model Alpha knows is declared once, in `config.yaml` → `models[]`. A bot
profile may only **name** those models — it never declares one. On top of that
shared catalog, each bot may carry its own `model_config` block choosing a
primary model, an ordered fallback chain, a counselling panel and a mixture
panel, all editable and inspectable per bot in the UI.

This document is the contract. The implementation lives in
`backend/packages/harness/alpha/bots/model_config.py` (validation +
resolution), `backend/app/gateway/routers/bots.py` (the four routes) and
`frontend/src/lib/bot-model-config.ts` + `frontend/src/components/bots/BotModelConfigPanel.tsx`
(the panel).

## The one rule

> `config.yaml` declares models. A bot **names** them.

`validate_bot_model_config(..., known_models=...)` fails closed: any name not
in `models[]` is an error, along with every other problem in the block, and all
of them are reported at once so one save surfaces the whole list rather than
one round trip per mistake.

## The block

```yaml
# stored on the bot profile, written only through PUT /api/bots/{name}/model-config
model_config:
  primary: flagship            # optional; empty means "inherit"
  fallbacks: [cheap, local-model]   # ordered; replaces the primary's own chain
  sampling:                    # provider scalars only
    temperature: 0.2
    reasoning_effort: high
  counsel:                     # model counselling / consensus — off by default
    enabled: true
    members: [cheap, flagship]
    rounds: 2
    quorum: 2                  # 0 = simple majority
    effort: high
  mixture:                     # mixture-of-agents — off by default
    enabled: true
    references: [cheap, local-model]
    aggregator: flagship
    strategy: parallel         # parallel | sequential
    max_workers: 4
```

An **empty block means inherit everything**, which is the same "empty means
unset" convention the rest of the profile uses. Both panels are disabled by
default so an untouched profile convenes no panel and spawns no extra paid
calls.

### Bounds

| Limit | Value |
| --- | --- |
| `fallbacks` | 5 |
| `mixture.references` | 8 |
| `counsel.members` | 5 |
| `counsel.rounds` | 5 |
| `mixture.max_workers` | 8 |
| `mixture.strategy` | `parallel`, `sequential` |

A value past its bound is **refused with the bound named**, never clamped —
silently truncating a chain would change which model answers the next request.

### What may not be declared

`sampling` is an allowlist of provider scalars. Credential-shaped keys
(`api_key`, `token`, `secret`, `headers`, `base_url`, …) are refused with a
`secret_key` issue before they can reach a log, and metadata keys that would
fake a catalog entry (`name`, `model`, `use`, `provider`, `fallbacks`,
`pricing`, `display_name`, `description`) are refused as `reserved_key`.
`effort` / `reasoning_effort` are explicitly allowed and canonicalised.

## Resolution: one ladder, decided once

`resolve_model_plan()` is pure and is the *only* place precedence is decided.
A caller re-deriving it is how the UI and the runtime start disagreeing about
which model a bot runs.

```
request  >  bot.model_config  >  bot.model  >  custom_agent  >  default
```

Every resolved value carries its source: `primary_source`, `fallbacks_source`,
`counsel_source`, `mixture_source`, `sampling_source`. Two consequences worth
stating:

- **A bot-declared chain *replaces* the primary's declared chain.** Appending
  to it would make the effective order depend on a declaration nobody can see
  from the UI. With no bot chain, `fallbacks_source` is `primary_model`.
- **A cycle back to the primary is a failure, not a silent dedupe.** The
  override walk runs inside the primary's own stack frame, so `A → B → A`
  raises rather than quietly producing a shorter chain.

## Failure behaviour

A bot whose stored block is invalid does **not** run on a half-parsed plan.
The lead agent sets `bot_model_config = BotModelConfig()` (an *empty* config,
deliberately not `None` — `None` would re-read the raw dict tolerately) and
logs every issue at ERROR, so the plain `bot.model` field applies and the log
says why. The configuration never fails a run; it degrades to the next rung of
the ladder, loudly.

## API

| Route | Purpose |
| --- | --- |
| `GET /api/bots/{name}/model-config` | stored block + resolved plan + `known_models` + issues |
| `PUT /api/bots/{name}/model-config` | validate against `models[]`, store the canonical form |
| `DELETE /api/bots/{name}/model-config` | clear the block (inherit everything) |
| `POST /api/bots/{name}/model-config/preview` | validate + resolve **without saving** |

- `PUT` and `DELETE` require admin. `GET` and `preview` are reads.
- `PUT` stores `cfg.to_dict()` — the canonical form (trimmed names, normalised
  effort rungs), not the caller's spelling, so the epoch hashes what will be
  executed.
- A refusal is `422` with `detail = {message, issues}`, each issue
  `{code, field, message, severity}`.
- `known_models` is the validated `models[]` name set, sorted. The picker uses
  *this* rather than `GET /api/models`, which is a wider surface; absent (an
  older Gateway) maps to `null` in the client so "not reported" and "zero
  declared models" stay distinguishable.
- `model_config` is deliberately **not** writable through the generic
  `PATCH /api/bots/{name}`: Pydantic reserves `model_config` as a class
  attribute, and an unvalidated generic write would bypass the fail-closed
  check. The dedicated `PUT` is the only write path.

## The panel

`BotModelConfigPanel` is mounted on the bot detail page (`/bots/[name]`) and
renders four states distinctly — loading, failed-with-reason, present, and
"unsaved changes" — so a failed read never looks like an empty configuration.

- The **resolved plan** block is read-only: each value beside its provenance,
  then the precedence ladder and the server's own limits.
- **Preview never saves.** It posts to the read-only preview route and shows
  the plan that *would* resolve.
- **Save is gated** on the draft differing from the last server-confirmed
  config, and a rejected save renders every issue per field from
  `ModelConfigValidationError` — never a bare status line.
- **Clear** is destructive and sits behind an explicit confirm.
- A panel left off by default reads as off, with the reason it is off.

## Tests

- `backend/tests/test_bot_model_config.py` — validation, bounds, resolution,
  provenance, cycles.
- `backend/tests/test_bot_model_config_wiring.py` — profile/registry/factory,
  the four routes, and the lead-agent plan wiring.
- `frontend/src/lib/bot-model-config.test.mjs` — routes, verbs, CSRF, envelope
  mapping, and the 422 issue list surviving to the panel.
- `frontend/src/lib/bot-model-config-view.test.mjs` — mount, states, and the
  rendered honesty claims.
