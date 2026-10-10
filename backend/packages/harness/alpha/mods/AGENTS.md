# Alpha Mod Kernel

This package owns Alpha's native ordered event middleware, mod-scoped capability
context, built-in enforcers, and autonomous controllers. Claude Code Mods are a
research reference only; Alpha mods are Python modules inside Alpha's own
runtime and do not load Claude Code plugins.

## Runtime boundaries

- `ModKernel` composes handlers by stable ascending priority. `ANSWER`, `DENY`,
  `DEFER`, `RETRY`, and `ESCALATE` are terminal outcomes; only explicit
  `CONTINUE`/`OBSERVE` reaches the next handler. Security and emergency faults
  fail closed.
- `required_capabilities` documents a mod's needs; it does not grant them to
  third-party modules. Pass reviewed capabilities to `register_mod` for
  externally supplied code. First-party `alpha.mods.*` declarations are granted
  by the trusted registration path.
- **Registration is guarded by `SecDefaultMod`.** It registers first (KERNEL
  tier, priority 0) so it is outermost, and it also runs as a runtime mod.
  External mods may not register at priority ≤ `GUARDED_PRIORITY_CEILING` (200)
  or be granted undeclared capabilities. `RESERVED_CAPABILITIES = {"estop:control"}`
  may never be granted to an external mod. A violation raises
  `ModRegistrationError` — refused at the door, never silently re-prioritized.
- **Storage read and write are distinct capabilities.** `ctx.storage` requires
  `storage:read` **or** `storage:write`; mutations (`set`/`delete`/`clear`)
  require `storage:write`; reads (`get`/`keys`/`snapshot`) are satisfied by
  either (write implies read, so a counter mod can `get` then `set` under one
  grant).
- Tool and model lifecycle integration belongs in
  `alpha.agents.lead_agent.agent.build_middlewares`. Do not create a second run
  lifecycle: admissions go through `require_mod_admission`, `RunManager` owns
  runs, and `AutonomySupervisor` owns loops.
- A failed or unreadable ESTOP state means engaged. Do not convert a safety
  control read error into permission to continue.

## Manifest and describe

`manifest.py` is Claude Code's `claude plugin validate` equivalent. A mod may
declare a `manifest = ModManifest.create(...)` (the contract it intends to
keep); `describe_mod` returns **declared and observed side by side, never
merged**, plus a `discrepancies` list naming every divergence — a declared hook
the mod does not subscribe to is a claim, a subscription nobody declared is a
surprise. Every built-in declares a manifest whose hooks match its
`subscribed_events`; a fresh discrepancy on a built-in is a regression.
`alpha mod list` prints the discrepancy count, `alpha mod describe [name]` the
JSON projection.

## Durable holds (closing the DEFER gap)

`approvals.py::HoldStore` is the durable approval queue behind a mod `DEFER`:
atomic JSON under `runtime_home()`, idempotency key =
`hash(tool_name, canonical-sorted args, run_id, tool_call_id)`. The load merges
(decided beats undecided, later `decided_at` wins) rather than clobbering
in-memory state. An expired hold voids even an approved decision — `is_expired()`
is time-based, not decision-gated — and the prior verdict is preserved for audit.
This is restart-recoverable for **one** Gateway process; it is not a shared
multi-worker approval store. Approving/rejecting is an authenticated Gateway act;
the CLI `alpha mod holds` is read-only by design.

## Gateway surface

`app/gateway/routers/mods.py` is the operator surface: `GET /api/mods` (fleet +
chain + describe), `/chain`, `/audit` (+ `/export`), `/holds` +
`POST /holds/{id}/approve|reject`, `/state`, `/commands` +
`POST /commands/{name}`, `/ui-cards`, `POST /preview`, and `GET /{mod_name}`.
Collection routes are declared **before** the `/{mod_name}` catch-all for the
same Starlette registration-order reason as skills and workflows. Fleet reads
are authenticated; audit, holds, state and UI cards are admin-only (they carry
other users' payloads and there is no member-scoped dimension to filter on).
An absent audit mod is `503`, never an empty list. A command declaring
`requires_approval` is refused `409` over HTTP — that route has no approval
flow. Hold decisions record the **authenticated caller** as the operator, never
a body-supplied name.

## Command projection into the Gateway catalog

A mod command is a `/command` that runs with **no model turn**. Two registries
exist on purpose: this package's (`commands.py`) is what a mod adds and
withdraws *while the Gateway serves*; `alpha.commands.registry` is the operator
catalog `/api/commands` lists and dispatches. `bindings.bind_mod_commands` is
the single mapping between them, and it runs from the kernel's own registration
lifecycle (register / unregister / unregister_mod / clear) — a one-shot startup
bind would project nothing, because mods register commands when they *run*, and
a row left behind after a withdraw would keep advertising a command that no
longer exists.

- **A bound row is executable, not a catalogue stub.** The projection registers
  a real handler that dispatches back through the kernel, so the catalog's
  `has_handler` / `executable` flag and the dispatch behind it are the same
  fact. A row bound without a handler would advertise a capability that answers
  "nothing was executed".
- **The projection is pruned, never only appended.** Withdrawing or clearing a
  command removes the catalog row in the same act, through
  `SlashCommandRegistry.unregister` (added for this; it removes an exact name
  only, so nothing can sweep the catalog).
- **A mod may not take a catalogued name.** An existing row owned by another
  source — every core command included — is refused with a warning and left
  untouched, so registering `goal` cannot hand a mod the operator's `/goal`.
  The kernel may still hold the name for its own surface; the warning is what
  discloses that the two surfaces can then answer differently for it.
- **Payload shape on this surface is `{"args": <str>, "context": <dict>}`** —
  the catalog dispatches an argument string, not a request body. The mods router
  keeps passing the request body through as `payload` unchanged, so a handler
  reads the surface it was called from.
- **Approval reuses the catalog gate.** `requires_approval` is carried across,
  so over `/api/commands/execute` the command is blocked until the caller
  supplies the same explicit `approved` grant a core command needs, while
  `POST /api/mods/commands/{name}` still refuses it `409` — that route has no
  approval flow at all.
- The catalog dispatches **synchronously** while a mod handler may be `async`;
  `bindings._drive` runs the coroutine on the current thread's loop, or on a
  short-lived thread with its own loop when that thread already owns a running
  one (blocking on your own loop is a deadlock, not a wait).

## Durability and honesty

- The event journal, mod-scoped capability storage, timers, and UI-card store
  are process-local. Do not describe them as durable or cross-worker
  exactly-once. The hold store is durable but single-process.
- A Mod `DEFER` is an honest hold signal. The durable store makes an operator
  decision able to release or reject the same action and its idempotency key
  for one Gateway; it is not a distributed approval protocol.
- `preview.py` (`preview_impact`) **never executes anything**: bounded command
  parsing plus a read-only filesystem walk. `measurable: false` is an honest
  "could not compute", never "nothing at stake".
- Replay records what was on disk verbatim (no newline normalization);
  `alpha mod audit` reports an absent ledger as an error naming it, never an
  empty report.

## Validation

Run the focused suites from `backend/`:

```powershell
uv run pytest tests/test_mod_kernel.py tests/test_mod_context.py tests/test_mod_middleware.py tests/test_mod_enforcers.py tests/test_mod_runtime_wiring.py -q
uv run pytest tests/test_bot_mode_mod.py tests/test_failure_sentinel_mod.py tests/test_task_router_mod.py -q
```

Advanced-feature suites (state, manifest, audit, sec_default, preview, replay,
budget, approvals, commands, the cross-cutting integration, the Gateway router,
the CLI, and the catalog projection) are:

```powershell
uv run pytest tests/test_mod_state.py tests/test_mod_manifest.py tests/test_mod_audit.py tests/test_mod_sec_default.py tests/test_mod_preview.py tests/test_mod_replay.py tests/test_mod_budget.py tests/test_mod_approvals.py tests/test_mod_commands.py tests/test_mod_advanced_integration.py tests/test_mods_router.py tests/test_mod_cli.py tests/test_mod_command_bindings.py -q
```

The end-to-end controller suite is `tests/test_bot_mode_mod_integration.py`;
it must import the current `GroupRunService` and BotRegistry APIs. Run `ruff
check` and `ruff format --check` on changed Python files before commit.
