# Alpha Desktop (Windows · Electron)

A one-click Windows app for Alpha. It opens **straight into the 2.0 chat**
— no login screen, no setup wizard, no landing page — and runs everything
locally: the AI Gateway API plus the chat UI inside a single native window.

## Install (end users)

1. Run **`Alpha-Setup-2.1.0.exe`** (in `electron/dist/` after a build).
2. If Windows SmartScreen warns about an unrecognized app (the installer is
   unsigned), choose **More info → Run anyway**. The installer works
   per-user — no administrator rights needed.
3. Launch **Alpha** from the Start menu or desktop shortcut.

**You need nothing pre-installed.** The installer bundles its own Node.js
and `uv` runtimes; on first launch the app automatically provisions Python
and the backend environment (one-time download, a few minutes depending on
your connection — the splash screen reports progress).

First launch in short:

1. Splash screen → Gateway starts (port 8201) → chat UI starts (port 3000).
2. The app opens on a fresh chat composer.
3. If no AI model is configured yet, the app says so and offers to open the
   config folder: add at least one model API key to
   `<userData>\project\config.yaml`, restart the app, and chat.

Your data (config, threads, memory, logs) lives per-user under Electron's
`userData` directory, which the app resolves at runtime from
`app.getPath('userData')` — `%APPDATA%\Alpha\` by default on
Windows. Read the exact path from the app's **User data** menu entry, which opens
the folder directly, rather than hardcoding it:

| Location | Contents |
| -------- | -------- |
| `project\` | `config.yaml`, `extensions_config.json` — edit your keys here |
| `alpha-home\` | threads, memory, SQLite state |
| `backend-venv\`, `python\` | auto-provisioned backend environment (do not touch) |
| `logs\` | `main.log`, `desktop-events.jsonl`, `gateway.log`, `frontend.log` |

To uninstall, use Windows **Settings → Apps** (per-user install, removes the
app; your `<userData>\` folder is kept — delete it
manually for a full wipe).

## Share it with the world

The installer is verified end-to-end: silent `/S` install, first launch on a
virgin machine profile (bundled Node + `uv`, auto-provisioned Python and
venv, no login, chat opens), and silent uninstall. To publish:

1. `npm run dist` and take `electron/dist/Alpha-Setup-<ver>.exe`.
2. Create a GitHub Release (e.g. tag `v2.1.0`) and attach the exe.
   Anything that serves the file works too (company drive, S3, …).
3. Tell users: download → **More info → Run anyway** (unsigned) → launch →
   when prompted, add one model API key to
   `<userData>\project\config.yaml` (the app opens this folder for you) →
   restart → chat.
   Internet is required on first launch (one-time Python/package download).

**Or skip the local build entirely:** push a `v*` tag, or run the
*Windows Desktop Installer* workflow manually, and GitHub's own Windows runner
builds the installer and attaches it to the Release — no local packaging needed.
See `.github/workflows/windows-installer.yml`. This is the recommended route on
machines where packaging is impractical (low memory, or a sandbox that restricts
writes under `node_modules`).

## Build from source (developers)

Prerequisites (build machine only): **Node.js 22+**, **uv**, and Git.
`uv` provisions Python automatically; end users never need any of this.

**Build machine resources.** Packaging is memory- and network-heavy: `npm install`
pulls Electron (~150 MB) plus electron-builder, and `dist` additionally downloads
portable Node and uv into `build/runtime`. Expect ~2 GB of free disk and several
GB of free RAM, on an unrestricted shell. On a sandbox that denies writes under
`node_modules`, or a machine with under ~1 GB free RAM, the install may report
success while leaving packages partially extracted and the Electron binary
missing — verify `node_modules\electron\dist\electron.exe` exists before running
`npm run dist`, otherwise rerun the install on a less constrained machine.

```powershell
git clone <repo-url> alpha
cd alpha\electron
npm install              # electron + electron-builder

# Desktop Gateway (same files `make dev` needs, at the repo root):
cd ..
copy config.example.yaml config.yaml
copy extensions_config.example.json extensions_config.json
cd electron
```

> Note: run `corepack pnpm …` from inside `frontend/` — the repo root has no
> `packageManager` pin, and a parent folder on your machine may pin a
> different one (yarn), which makes bare `corepack pnpm` fail with
> “configured to use yarn” from other directories. `build:frontend` below
> handles this for you.

```powershell
npm run dev    # hot-reload: dev frontend + Gateway (attaches to healthy :3000/:8201 if present)
npm start      # production: standalone frontend + Gateway (needs build:frontend first)
```

### Useful flags

```powershell
npx electron . -- --skip-backend --skip-frontend          # attach to everything already running
npx electron . -- --frontend-url=http://127.0.0.1:2026   # attach to `make dev` (nginx)
npx electron . -- --frontend-port=3100 --gateway-port=8101
npx electron . -- --require-login                        # keep login + admin-setup screens
npx electron . -- --show-lion-pet                        # open Milo on the Windows desktop at startup
npx electron . -- --verbose                              # mirror service logs to the console and use debug logs
npx electron . -- --log-level=debug                    # full startup, probe, IPC, and shutdown detail
```

`main.log` is human-readable; `desktop-events.jsonl` carries the same events as
machine-readable JSON with timestamps, PIDs, session IDs, durations, ports,
service states, and redacted context. API keys, tokens, private keys, and URL
credentials are redacted before either file is written. Use **Tools → Open logs
folder** from the installed app.

> Attaching to a Gateway you started yourself (e.g. `make dev` on :8001)
> keeps that Gateway's auth mode: if it enforces login, the login screen
> appears. The direct-open behavior applies to Gateways the app spawns
> itself. `--require-login` forces the auth screens even for app-spawned
> services.

### Make the installer

```powershell
npm run verify     # assert the packaging inputs exist and agree (also runs as predist)
npm run dist       # fetch-runtime → build:frontend → Alpha-Setup-<ver>-x64.exe into dist/
npm run dist:dir   # same inputs, unpacked folder instead (faster smoke test)
```

`dist:dir` deliberately runs `fetch-runtime` and `build:frontend` first. It used
to run `electron-builder` alone, which produced an unpacked app with no Node
runtime and no frontend — a smoke test of a package that could never start.

Produces, in `dist/`:

| Artifact | Purpose |
| -------- | ------- |
| `Alpha-Setup-<ver>-x64.exe` | the installer |
| `latest.yml` | the update feed `electron-updater` reads |
| `Alpha-Setup-<ver>-x64.exe.blockmap` | differential update, so an update downloads only the changed blocks |

Both `latest.yml` and the installer are attached to the GitHub Release by
`.github/workflows/windows-installer.yml` on a `v*` tag. A feed that is built but
not published means no installed client can ever update, so they ship together.

The app icon needs no build step: `assets/alpha.ico` and `assets/alpha-mark.png`
are tracked, not generated at package time (see [Branding](#branding)).

What gets bundled: the Electron shell, the tracked brand marks, the standalone
frontend (marketing showcase fixtures excluded — `/showcase/*` 404s in the app,
everything else identical), the Python backend sources, config templates, and
the self-contained Node.js + `uv` runtimes (`scripts/fetch-runtime.mjs`,
pinned in `desktop-config.json`).

Port note: the desktop Gateway defaults to **8201** (not 8001) so it never
fights a separately running `make dev` stack. Next.js bakes `/api` rewrite
targets at build time, so before spawning a production frontend the app
patches the bundled `routes-manifest.json` to the effective Gateway URL
(only loopback destinations are touched; failures are loud, never silent).

### Branding

Every Alpha mark — the Windows executable, the Start Menu and desktop
shortcuts, the taskbar and window icons, the splash screen, the browser tab,
the Apple touch icon and the PWA/Android icons — is the same lion, derived from
the single real logo at `frontend/src/assets/images/alpha.png`.

```bash
node scripts/make-icon.mjs            # desktop marks (delegates to the generator)
node ../scripts/generate-brand-assets.mjs   # every mark, web included
node ../scripts/generate-brand-assets.mjs --check   # fail if they are stale
```

The generator writes two crops of the poster, because one crop cannot serve
both jobs: the **mane circle** is the mark at 32px and up, and the **face** is
the favicon at 16px and 24px, where the whole mane would average out to a dark
blob. The results are **tracked in git** under `assets/` (here) and
`frontend/public/`, not generated at package time, so a fresh clone already has
the right icon and `npm run dist` cannot silently produce a placeholder again.
`assets/alpha.ico` is a real multi-resolution icon (16/24/32/48/64/128/256) so
each Windows surface picks a legible size.

To change the logo, replace `frontend/src/assets/images/alpha.png`, re-run the
generator, and commit the regenerated marks. Do not hand-edit a mark, and do not
rasterize a logo anywhere else — one generator, one source.

### Microphone and speaker access

The desktop shell grants microphone-only media capture to the exact local Alpha
frontend origin. Camera requests and mixed audio/video requests are denied. The
voice controls still require the user to click a microphone or real-time button;
the first explicit control also unlocks the shared Web Audio speaker output. Use
**Test speaker and enable autoplay** in the composer to hear a local confirmation
phrase. If microphone capture is blocked, check the operating system's input
device and Windows privacy settings, then click the voice control again.

## Lion companion

Alpha includes **Milo**, a local lion companion drawn as inline SVG with
articulated legs, layered fur and facial detail, and six looks. In the app,
right-click the lion to open its controls: pet it, resize it, move it, choose
one of six looks, trigger walk/run/jump/roar/pounce/play/sleep/stretch/prowl/hunt/shake/spin
actions (walk, run, prowl, and hunt travel within the desktop work area), enable automatic
idle actions or sound cues, hide it, or return to chat. It reacts to Alpha's
bounded run states (`thinking`, `working`, `waiting`,
`success`, and `error`) without putting prompts, responses, or thread data into
the pet.

In the Windows desktop build, choose **Detach** in the lion menu (or
**Tools → Show desktop lion**) to open a transparent, always-on-top companion
window. The native window is draggable, has Walk, Run, Action, Look, and a small
return-to-Alpha button, and uses the same state channel; closing the main Alpha
window closes the companion and stops the local services. The overlay is optional and local-only—it does not
make requests or persist conversation content.


1. Single-instance lock — a second launch just focuses the open window.
2. Per-user data dir prepared; default configs seeded (never overwritten).
3. Gateway: a **verified** Alpha health endpoint on the preferred port is
   reused, otherwise the next free port is taken and the Gateway spawned.
   A service that crashes during startup aborts boot immediately with the
   exit code and a pointer to `gateway.log` (no silent 10-minute hangs).
4. Frontend: same reuse-or-spawn, with its `/api` rewrites pointed at the
   effective Gateway, then the window opens on the chat composer.
5. Advisory checks after opening: a warning if a machine-wide production
   marker would re-enable login, and first-run guidance when zero AI models
   are configured.
6. Closing the window stops both services — no orphan Python/Node processes
   (the whole process trees are terminated).
7. Logs rotate (`main.log` and `desktop-events.jsonl` at 5 MB with three backups each; service logs at 20 MB with one backup each).

## How this app is verified

Three gates, because a desktop app fails in ways a unit test cannot see.

### 1. `npm test` — the decisions, as pure functions

284 assertions over `lib/`. Every module there is Electron-free and
dependency-injected, so the interesting behaviour is tested directly rather than
inferred from source text:

| Module | What it owns |
| ------ | ------------ |
| `lib/ipc-routes.js` | the one channel table; a duplicate is refused |
| `lib/boot-sequence.js` | phase order, and reuse-vs-spawn |
| `lib/service-supervisor.js` | bounded restart supervision |
| `lib/restart-policy.js` | backoff, jitter, and the crash-loop budget |
| `lib/lifecycle.js` | ordered, bounded, observable shutdown |
| `lib/window-policy.js` | navigation lockdown, bounds, permissions, CSP |
| `lib/app-info.js` | the single status shape |
| `lib/diagnostics.js` | preflight findings (never throws) |
| `lib/updater.js` | version compare, check cadence, update honesty |
| `lib/auto-start.js` | preference vs OS login-item state |
| `lib/app-logger.js` | detailed human/JSON logs, levels, rotation, redaction |
| `lib/backend-warmup.js` | first-launch provisioning progress narration |
| `lib/support-bundle.js` | redacted end-to-end diagnostics bundle and failure summary |
| `lib/child-process.js` | spawning, log capture, verified tree kills |
| `lib/service-probe.js` | "is this port actually our service?" |
| `lib/ports.js` | bounded free-port search |

`tests/boot-contract.test.mjs` covers what cannot be pure: that `main.js` wires
each route, locks the windows down, recovers a crashed renderer, and opens its
log before anything that can report an error.

### 2. `npm run smoke` — does the app actually launch?

This is the gate that did not exist before, and it is the one that matters.
`main.js` on `main` registered four IPC channels twice each; Electron throws
`Attempted to register a second handler for 'alpha:status'` at module load and
exits **0** — no window, no splash, no dialog, nothing in any log. Every unit
test passed, because they are pure Node and never load `main.js`.

`scripts/smoke-boot.mjs` starts two stub services that identify themselves as
Alpha, launches the real Electron binary against a throwaway `userData` profile,
and fails unless the app reaches a live window. It also asserts the app did not
spawn a Gateway it had decided to reuse, and that a reused service survived the
shutdown. It runs in CI (`npm run smoke`).

### 3. `npm run verify` — are the packaging inputs real?

`scripts/verify-build.mjs` refuses to package missing or disagreeing inputs:
a `build/runtime` without real binaries, a missing `.next/standalone`, a missing
`uv.lock`, a `publish:` block that disagrees with `desktop-config.json`, or
version sources that have drifted. Each of those produced an installer that
looked fine and failed on a user's machine. Wired as `predist`, so it runs before
every build.

## Troubleshooting

- **Stuck on splash** — open `<userData>\logs\gateway.log` (the app's **User
  data** menu entry opens that folder).
  First launch provisions Python + ~200 packages in its own warmup phase with a
  15-minute budget and splash progress; slow connections take several minutes.
  The usual real failure is a missing/invalid model API key in
  `project\config.yaml`.
- **Startup failed** — use **Tools → Collect diagnostics bundle** (or run
  `npm run diagnose` from `electron/` without opening the app). It writes a
  redacted `diagnostics-*.json` next to the logs and names the cause instead of
  only showing the dialog.
- **“process exited during startup (code=…)”** — the named service crashed;
  the tail of its log (`gateway.log` / `frontend.log`) has the cause.
- **Login screen appears** — you attached to a Gateway that enforces login
  (e.g. your own `:8001`), or a machine-wide `ALPHA_ENV`/`ENVIRONMENT`
  is set to production (the app warns about this), or you passed
  `--require-login`.
- **Port already in use** — the app reuses a verified-healthy Alpha
  service and otherwise moves to the next free port. `Tools → Copy app URLs`
  shows the actual URLs.
- **“frontend build missing”** (on `npm start`) — run `npm run build:frontend`.
- **Blank window** — `File → Force Reload`, or `Toggle Developer Tools` to
  inspect the console.

## Stability

What the app now does that it did not before:

- **Self-healing services.** A Gateway or frontend that dies mid-session is
  restarted with exponential backoff and jitter, inside a sliding budget. A
  service that keeps dying ends in a visible `failed` state naming its log and
  a way to retry, rather than a silently respawning loop. A service that stops
  *answering* is restarted too — an exit-only watcher sits on a dead window
  forever, because a hung process never fires `exit`.
- **Ordered, verified shutdown.** The frontend stops first so it can drain
  before the Gateway goes, each step has a deadline, the whole thing is bounded,
  and the kill is confirmed rather than assumed. A forced kill is reported as
  such instead of being described as a clean exit.
- **A renderer that crashes reloads itself**, and a window that never paints is
  bounded instead of leaving the splash up forever.
- **Navigation lockdown.** The renderer holds a privileged bridge, so
  `window.open`, `will-navigate`, `will-redirect` and `<webview>` are all closed
  off the local Alpha origin. External links go to the browser; `file:`,
  `data:`, `blob:` and `javascript:` are refused outright. Non-media permissions
  are an explicit allowlist rather than a blanket grant.
- **Auto-updates** via `electron-updater` against a GitHub Release feed, with a
  minimum interval between checks so a bad build is not pushed to everyone at
  once, and an unreadable feed reported as *unavailable* rather than *up to
  date*.
- **Window state** is remembered, and restored onto a display that still exists
  — undocking a laptop no longer produces an invisible window.
- **First-launch warmup.** Backend provisioning runs before the Gateway health
  clock starts, with splash progress and a 15-minute budget. A failed start
  terminates the whole backend tree, so a later launch cannot find an orphan
  Gateway holding 8201.
- **One diagnostics bundle.** Service states, install contents, health checks,
  and redacted log tails ship as a single JSON file with a named-problem
  summary. Secrets stay redacted; config contents and environment values are
  never included.

`--no-updates` disables the update check for one run.

## Roadmap

1. Pre-synced backend venv (or PyInstaller gateway) to shorten first launch.
   First launch still needs internet: `uv` provisions CPython and ~200 wheels.
2. Code-signed installer, to skip the SmartScreen warning. The build already
   reads `CSC_LINK` / `CSC_KEY_PASSWORD` when they are present.
3. `nsis.deleteAppDataOnUninstall` as an opt-in checkbox. Today uninstalling
   keeps `<userData>\` (config, threads, memory, the venv and the provisioned
   Python — order of gigabytes), which is the right default and a footgun in the
   other direction.
