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
- Tool and model lifecycle integration belongs in
  `alpha.agents.lead_agent.agent.build_middlewares`. Do not create a second run
  lifecycle: admissions go through `require_mod_admission`, `RunManager` owns
  runs, and `AutonomySupervisor` owns loops.
- A failed or unreadable ESTOP state means engaged. Do not convert a safety
  control read error into permission to continue.
- Current event journal, held-action state, capability key/value storage, and
  timer tasks are process-local. They do not provide restart durability,
  multi-worker coherence, exactly-once dispatch, durable resume, or event replay.
  Document these as partial until a shared durable store and recovery protocol
  exist.
- A Mod `DEFER` is an honest hold signal. It is not a complete approval flow
  until an authenticated operator decision can durably release or reject the
  same action and its idempotency key.

## Validation

Run the focused suites from `backend/`:

```powershell
uv run pytest tests/test_mod_kernel.py tests/test_mod_context.py tests/test_mod_middleware.py tests/test_mod_enforcers.py tests/test_mod_runtime_wiring.py -q
uv run pytest tests/test_bot_mode_mod.py tests/test_failure_sentinel_mod.py tests/test_task_router_mod.py -q
```

The end-to-end controller suite is `tests/test_bot_mode_mod_integration.py`;
it must import the current `GroupRunService` and BotRegistry APIs. Run `ruff
check` and `ruff format --check` on changed Python files before commit.
