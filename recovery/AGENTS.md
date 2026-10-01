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
  with every probe, lock and destructive action stubbed. Two invariants that
  test owns and that are easy to break by eye: **a deferral must not feed
  `$script:ConsecutiveFailures`** (it counts against `$script:DeferPasses`
  instead, which is why that counter is what "Deferring recovery xN" logs), and
  **a `return` must sit on its own line** — PowerShell parses a bare `return`
  trailing another statement on the same line as a positional *argument* to
  that statement, which silently turned the cooldown branch into a fall-through
  into Tier 2/3. Keep the three extraction markers (`$ErrorActionPreference =
  "SilentlyContinue"`, `function Get-StackSnapshot`, `function
  Invoke-HealthCheck`, `function Invoke-WatchdogOfWatchdog`) stable.

- **`../start.ps1`** (Layer 2, the launcher) — three behaviours the watchdog's
  honesty depends on, pinned by `backend/tests/test_launcher_diagnostics.py`:

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
