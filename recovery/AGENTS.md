# recovery/ — Windows startup, self-healing, and auto-recovery

The whole chain that makes Alpha start on its own and heal itself lives here,
kept out of `scripts/` (which holds unrelated orchestration tooling) so the
recovery surface is one reviewable folder. These are Windows PowerShell 5.1
scripts; **they must keep their UTF-8 BOM** — PowerShell 5.1 misreads a
stripped-BOM file, and `backend/tests/test_launcher_watchdog_budget.py`
asserts it.

## The four layers (no layer is responsible for its own recovery)

| Layer | File | Role |
| --- | --- | --- |
| 4 | `watchdog.ps1 -Once` (`Invoke-WatchdogOfWatchdog`) | Windows Task Scheduler tasks `Alpha_Autostart` / `Alpha_Watchdog` verify that a correct Layer 3 loop exists *for this installation* and recreate it when missing, frozen, or watching another checkout. |
| 3 | `watchdog.ps1` (loop) | Monitors gateway / frontend / launcher; escalates defer → component restart → full stack restart, where the full-stack step is **gated**: it may run only when the launcher cannot act (dead, or heartbeat stale) or a component has exhausted its component-restart budget. A live launcher that is mid-boot is deferred, never killed (the pre-gate code destroyed a booting stack after 28 deferred checks and looped). |
| 2 | `../start.ps1` | Launcher: starts children, monitors, restarts them. |
| 1 | gateway, frontend, workers | The services themselves. |

`stop.ps1` writes `logs/alpha_maintenance.json`; while it exists every layer
stands down. That flag is the only sanctioned way to keep Alpha stopped — a
crash or `taskkill` never creates it and is always recovered.

## Files

- **`watchdog.ps1`** — Layers 3 and 4. `watchdog.ps1` (loop), `-Once` /
  `-StartIfDown` (Layer 4 pass), `-Stop` (stop the loop). Detaches via tiny
  VBScript shims in `logs/`, so killing one layer cannot kill another.
  Threshold budgets are a contract with `start.ps1`, pinned by
  `backend/tests/test_launcher_watchdog_budget.py`.

  The Layer-3 decision table itself — defer → component → stack, the Tier-3
  gate, the supervisor-lock and cooldown branches — is executed (not described)
  by `backend/tests/test_watchdog_decision_table.py`, which extracts the real
  config block, `Get-StackSnapshot` and `Invoke-HealthCheck` under PowerShell
  with every probe, lock and destructive action stubbed. Invariants that test
  owns, each one a bug this file has actually shipped:

  - **A deferral must not feed `$script:ConsecutiveFailures`.** It counts
    against `$script:DeferPasses` instead, which is why that counter is what
    "Deferring recovery xN" logs. Feeding the escalation counter is what let a
    long boot accumulate 28 failures and escalate on the very next check.
  - **A `return` must sit on its own line.** PowerShell parses a bare `return`
    trailing another statement on the same line as a positional *argument* to
    that statement, which silently turned the cooldown branch into a fall-through
    into Tier 2/3.
  - **A successful full-stack spawn opens a BOOT-GRACE window, and that window
    is keyed to `$script:LastStackSpawnUtc`, not to "the launcher looks dead".**
    See "The boot-grace window" below.
  - **A Tier-2 attempt that freed nothing must not consume the restart budget.**
    See "Honest Tier-2 budget" below.

  Keep the three extraction markers (`$ErrorActionPreference =
  "SilentlyContinue"`, `function Get-StackSnapshot`, `function
  Invoke-HealthCheck`, `function Invoke-WatchdogOfWatchdog`) stable.

  The harness drives time with `ALPHA_WD_PASS_GAP_SECONDS`, which back-dates
  `$script:LastActionUtc` and `$script:LastStackSpawnUtc` between passes. The
  shipped loop sleeps `$CheckIntervalSeconds`, so its cooldowns and its grace
  age in wall-clock time; harness passes finish in milliseconds, which silently
  lets the 30 s cooldown swallow every multi-pass scenario — that is why the
  2026-10-14 restart storm was invisible to the suite until the gap existed.
  **A new time-based branch is untestable without one**: add a pass gap rather
  than asserting against a value nothing can move.

### The boot-grace window

`Start-AlphaStack` returns the moment `wscript` has been handed the launcher, but
the launcher is **invisible** until `start.ps1` has taken over the ports and
written `logs\alpha.pid` plus its first heartbeat. Until then
`Get-StackSnapshot` honestly reads `launcher=dead`, and the Tier-3 gate defers
only for a *visible* launcher — so the straight-line conclusion was "nobody can
act, replace the stack". The generic cooldown (30 s, doubling to a 300 s cap)
expires long before a boot does, so it never broke the cycle either.

Measured on this machine, 2026-10-14 (`logs/watchdog.log`):

```
22:05:50 stack :: full_restart -> spawned (… launcher=dead …)
22:06:34 stack :: full_restart -> spawned (… launcher=dead …)
22:07:45 stack :: full_restart -> spawned (… launcher=dead …)
```

Three spawns in ~2 minutes against a stack that needs minutes to come up, each
pass killing the stack the previous pass had just created.

So a successful spawn records `$script:LastStackSpawnUtc`, and while inside
`$StackBootGraceSeconds` **and** `-not $s.LauncherAlive` the watchdog OBSERVES: a
`boot-grace` heartbeat plus a one-time diagnostic line, no escalation. Three
rules keep it from becoming a way to never recover:

- **It requires `-not $s.LauncherAlive`.** A launcher that *is* visible still
  gets its normal Tier-2 component restart. A visible launcher is one that can
  act, and suppressing Tier 2 for it throws away the only recovery left.
- **It requires a spawn this loop actually made.** `$script:LastStackSpawnUtc`
  stays `$null` until `Start-AlphaStack` returns true — a *failed* spawn grants
  no window — so a machine with no launcher, the state where recovery is most
  urgent, is never slowed down.
- **It is bounded, and a bound shorter than the state it absorbs is decoration.**
  `$StackBootGraceSeconds` must stay above the measured 70 s launcher-invisibility
  window and below `$StartupGraceSeconds`. Past the window the shipped decision
  returns unchanged: a dead launcher escalates, exactly as before.

Boot-grace is a **deferral**, so by the rule above it resets
`$script:ConsecutiveFailures` and feeds `$script:DeferPasses`. It releases the
supervisor lock before returning, like the other deferrals, so a pass that
genuinely needs to act is never blocked by one that is only waiting.

### Honest Tier-2 budget

`Stop-PortTree` reports three outcomes, and the caller charges a different budget
for each because they are three different facts:

| outcome | what actually happened | charged to |
| --- | --- | --- |
| `killed` | a bound port was freed — a real restart | `MaxComponentRecoveries` |
| `already-free` | **nothing was there to kill** — a no-op | `MaxComponentNoopPasses` |
| `stuck` | a bound port refused to die — a failed restart | `MaxComponentRecoveries` |

`uvicorn` binds 8001 only *after* the 2–3 minute application import, so **a
perfectly healthy booting gateway makes every Tier-2 attempt a no-op.** Charging
those as restarts exhausted the budget in three passes and then handed the Tier-3
gate its "budget exhausted" case, escalating a stack that was booting normally.
Measured, same log, same incident:

```
22:01:16 gateway :: component_restart -> noop (port 8001 already free before kill)
22:01:48 gateway :: component_restart -> noop (…)
22:04:47 gateway :: component_restart -> noop (…)
22:05:35 [WARN] gateway exceeded 3 component restarts - escalating to full stack
```

Not charging no-ops is therefore only half the fix. If a launcher claims to be
working forever while a port stays closed, every attempt no-ops forever and the
port-tree kill provably cannot help — escalation has to come from a counter of
its own, which must be bounded. Both ceilings must have room to act (`-and`,
never `-or`); joining them with `-or` leaves whichever is not currently charged
unbounded, and a component that always frees its port is then restarted forever.
`$MaxComponentNoopPasses` is sized above the launcher's own longest service boot
so a launcher still legitimately importing is never out-escalated, while
escalation stays eventual.

- **`../start.ps1`** (Layer 2, the launcher) — four behaviours the watchdog's
  honesty depends on, pinned by `backend/tests/test_launcher_diagnostics.py`:

  - **Steady-state service liveness is the listener, not the wrapper PID.** A
    live `uv run`/Uvicorn process with its port closed is wedged just as surely
    as an exited process. The launcher restarts the service when its port is
    absent; otherwise the watchdog defers to the live launcher and both layers
    can leave the service down indefinitely.

  - **Readiness is re-probed every boot-wait pass, never latched.** The old
    `if (-not $gatewayReady)` guards made the first 200 permanent, so a gateway
    that crashed mid-boot-wait stayed "ready" and the loop could break out
    with it dead.
  - **The monitor heartbeat probes serving, not just bound ports.**
    `Write-MonitorHeartbeat` asks `/health/ready` (gateway) and `/` (frontend)
    before writing `status: healthy`; ports-only announced healthy while the
    gateway was still migrating, which is the `status=healthy
    gateway=starting` pair the watchdog used to escalate into a full restart.
  - **Every redirecting spawn sweeps previous incarnations, then archives its
    previous logs first.** `Start-Process -RedirectStandardOutput/-RedirectStandardError`
    *truncates* an existing file, so each restart used to wipe the crash
    evidence it should have preserved. `Archive-ServiceLog` moves them to
    `logs/diagnostics/` (timestamped, newest 20 per log) immediately before
    each spawn. Two live-observed (2026-10-01) failure modes are pinned too:
    a previous incarnation that is *still booting* holds no listener, so
    `Free-PortOrExit` misses it — `Stop-ServiceChainOrphans` tree-kills every
    command-line match carrying our port before the archive runs (and before
    the spawn can be double-bound); and when the holder is nonetheless still
    alive, rename is denied while reads are allowed, so the archive falls
    back to a copy and a failure emits a `Write-Warning` — never a silent
    `catch {}`, which is how `logs/diagnostics/` ended up with frontend
    archives but zero gateway archives. If you add a spawn site, add the
    `Stop-ServiceChainOrphans` + `Archive-ServiceLogs` calls directly above
    it — the 400-char sweep-then-archive pairing is asserted.
  - **A gateway that never binds its port is killed by command line, not waited
    out.** `uvicorn` binds 8001 only *after* the 2–3 minute application import,
    so a **wedged** import is a process that is alive, holds **no listener**, and
    emits nothing useful. That is the one failure state no port-based recovery
    can reach: `Free-PortOrExit` sees nothing, and the watchdog's `Stop-PortTree`
    only kills LISTENING pids, so its Tier-2 restart is a permanent no-op against
    it (this is where the "already free before kill" no-ops in the log above come
    from). Only a command-line match names such a process.

    So the boot wait tracks `$script:GatewaySpawnedUtc` — set at *every* gateway
    spawn site, because `Update-TrackedProcess` can adopt a different pid
    mid-boot, and an unreset clock makes a respawned gateway inherit the previous
    incarnation's age and get killed the instant it binds. When the port has
    never come up and `$GatewayNoListenerSeconds` has elapsed, the launcher calls
    `Stop-ServiceChainOrphans` and respawns instead of consuming the rest of the
    15-minute budget and then calling `Fail-Startup`. Without it a recoverable
    hang became a terminal startup failure (exit 1). The threshold must stay
    **above** the launcher's own 240 s gateway patience and **below** the boot
    wait; both bounds are pinned by `test_launcher_watchdog_budget.py`.
- **`register_autostart.ps1`** — registers the scheduled tasks. **Generated
  `recovery/autostart/*.vbs` launchers bake this machine's absolute path, so
  they are gitignored** (see `.gitignore`) and must be regenerated with
  `powershell -File recovery\register_autostart.ps1 -Force` on a new checkout.
  The `Alpha_Update` task is created only when the operator policy
  (`config/update-policy.json`, or `$ALPHA_UPDATE_POLICY_PATH`) enables it;
  the policy is the kill switch. See `docs/AUTO_UPDATE.md`.
- **`unregister_autostart.ps1`** — removes the tasks, tray icon, and watchdog
  loop; the inverse of the above.
- **`tray_status.ps1`** — the notification-area status icon. Its
  `Get-AlphaState` decision table is extracted and stubbed by
  `backend/tests/test_tray_status_health_truthfulness.py`, so keep the
  config / probe-helper / `Get-AlphaState` block order stable.

  The badge draws the **Alpha mark, not a letter**: `Get-AlphaLogo` picks a
  generated crop and `New-StatusIcon` clips it into a 22px inner circle so the
  state colour stays visible as a ring around it. Two rules are worth keeping:

  - **Face before mane.** `scripts/generate-brand-assets.mjs` switches crop at
    `FACE_MAX_SIZE = 24` — at or below it the face reads, above it the mane
    averages into an unreadable dark blob at tray size. The badge is 22px, so
    the candidates lead with the 24px face layer of `electron/assets/alpha.ico`
    (read straight out of the ICO's PNG payload, since re-rasterising through
    `Icon.ToBitmap` can drop the alpha channel) and fall back through
    `favicon-16x16.png`, then the mane crops.
  - **The poster is never a candidate.** `frontend/src/assets/images/alpha.png`
    carries the ALPHA wordmark and tagline, and no mark may include the type.
    Only the generated crops are eligible.

  The failure badge deliberately keeps `!` instead of the mark: on the one
  state where colour alone is easy to miss at 16px, the glyph carries the
  signal.

  Every load, every rejection, and the fallback are written to
  `logs/tray.log`, so "why is my icon a letter?" is answerable from outside the
  process. **The badge must never go blank** — if no mark loads, the drawn "A"
  is used and the badge is identical to a glyph-only control. Both invariants
  are pinned by `backend/tests/test_tray_status_logo.py`, which renders the
  shipped icon region and compares pixels.
- **`verify_reboot.ps1`** — run *after* logging in again; observes whether
  Windows brought Alpha back on its own and reports PASS/FAIL per layer.
  Starts nothing.
- **`verify_recovery.ps1`** — full recovery-suite check (expects 30/30).

## The gap this layout exists to prevent

The scheduled tasks invoke `recovery/autostart/*.vbs`. Those files are
generated *and* gitignored, so a fresh clone, a `git clean -xfd`, or any
checkout on a new machine has tasks that point at nothing: `wscript` exits
immediately, every task reports `LastResult=1`, and the self-healing chain
never starts — silently, because the layer that would notice is the layer that
failed to launch.

The fix is **not** to re-run registration by hand each time. Make registration
part of any step that can recreate the tasks, and verify after:

```powershell
powershell -ExecutionPolicy Bypass -File recovery\register_autostart.ps1 -Force
powershell -ExecutionPolicy Bypass -File recovery\verify_reboot.ps1
```

A healthy install answers `LastResult=0` for `Alpha_Autostart`,
`Alpha_Watchdog`, and `Alpha_TrayStatus`. Check with:

```powershell
Get-ScheduledTask -TaskName "Alpha_*" | ForEach-Object {
  $i = $_ | Get-ScheduledTaskInfo; "{0} | {1} | {2}" -f $_.TaskName, $i.LastRunTime, $i.LastTaskResult }
```

`LastResult=1` with a missing `recovery/autostart/` directory is exactly this
failure.
