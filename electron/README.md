# Alpha Desktop (Windows · Electron)

A one-click Windows app for Alpha. It opens **straight into the 2.0 chat**
— no login screen, no setup wizard, no landing page — and runs everything
locally: the AI Gateway API plus the chat UI inside a single native window.

## Install (end users)

1. Run **`Agent-Workspace-Setup-2.1.0.exe`** (in `electron/dist/` after a build).
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
   `%APPDATA%\agent-workspace-desktop\project\config.yaml`, restart the app, and chat.

Your data (config, threads, memory, logs) lives per-user under
`%APPDATA%\agent-workspace-desktop\`:

| Location | Contents |
| -------- | -------- |
| `project\` | `config.yaml`, `extensions_config.json` — edit your keys here |
| `alpha-home\` | threads, memory, SQLite state |
| `backend-venv\`, `python\` | auto-provisioned backend environment (do not touch) |
| `logs\` | `main.log`, `gateway.log`, `frontend.log` |

To uninstall, use Windows **Settings → Apps** (per-user install, removes the
app; your `%APPDATA%\agent-workspace-desktop\` data folder is kept — delete it
manually for a full wipe).

## Share it with the world

The installer is verified end-to-end: silent `/S` install, first launch on a
virgin machine profile (bundled Node + `uv`, auto-provisioned Python and
venv, no login, chat opens), and silent uninstall. To publish:

1. `npm run dist` and take `electron/dist/Agent-Workspace-Setup-<ver>.exe`.
2. Create a GitHub Release (e.g. tag `desktop-v2.1.0`) and attach the exe.
   Anything that serves the file works too (company drive, S3, …).
3. Tell users: download → **More info → Run anyway** (unsigned) → launch →
   when prompted, add one model API key to
   `%APPDATA%\agent-workspace-desktop\project\config.yaml` → restart → chat.
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
git clone <repo-url> agent-workspace
cd agent-workspace\electron
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
npx electron . -- --verbose                              # mirror service logs to the console
```

> Attaching to a Gateway you started yourself (e.g. `make dev` on :8001)
> keeps that Gateway's auth mode: if it enforces login, the login screen
> appears. The direct-open behavior applies to Gateways the app spawns
> itself. `--require-login` forces the auth screens even for app-spawned
> services.

### Make the installer

```powershell
npm run dist       # fetch-runtime → build:frontend → Agent-Workspace-Setup-<ver>.exe into dist/
npm run dist:dir   # unpacked folder instead (faster smoke test)
```

What gets bundled: the Electron shell, the standalone frontend (marketing
showcase fixtures excluded — `/showcase/*` 404s in the app, everything else
identical), the Python backend sources, config templates, and the
self-contained Node.js + `uv` runtimes (`scripts/fetch-runtime.mjs`,
pinned in `desktop-config.json`).

Port note: the desktop Gateway defaults to **8201** (not 8001) so it never
fights a separately running `make dev` stack. Next.js bakes `/api` rewrite
targets at build time, so before spawning a production frontend the app
patches the bundled `routes-manifest.json` to the effective Gateway URL
(only loopback destinations are touched; failures are loud, never silent).

The installer/taskbar icon (`build/icon-512.png`) is generated from
shapes-only SVG via `node scripts/make-icon.mjs` (needs the `sharp`
package: `npm install --no-save sharp`). The repo's `deer.svg`
illustration does not rasterize usefully outside a browser, hence the
geometric mark. Re-run the script after editing it, then rebuild.

## How startup works (and fails safely)

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
7. Logs rotate (`main.log` at 5 MB, service logs at 20 MB, one backup each).

## Troubleshooting

- **Stuck on splash** — open `%APPDATA%\agent-workspace-desktop\logs\gateway.log`.
  First launch downloads Python + ~200 packages; slow connections take
  several minutes (progress is in the log). The usual real failure is a
  missing/invalid model API key in `project\config.yaml`.
- **“process exited during startup (code=…)”** — the named service crashed;
  the tail of its log (`gateway.log` / `frontend.log`) has the cause.
- **Login screen appears** — you attached to a Gateway that enforces login
  (e.g. your own `:8001`), or a machine-wide `AGENT_WORKSPACE_ENV`/`ENVIRONMENT`
  is set to production (the app warns about this), or you passed
  `--require-login`.
- **Port already in use** — the app reuses a verified-healthy Alpha
  service and otherwise moves to the next free port. `Tools → Copy app URLs`
  shows the actual URLs.
- **“frontend build missing”** (on `npm start`) — run `npm run build:frontend`.
- **Blank window** — `File → Force Reload`, or `Toggle Developer Tools` to
  inspect the console.

## Roadmap

1. Pre-synced backend venv (or PyInstaller gateway) to shorten first launch.
2. `electron-updater` + private feed for auto-updates.
3. Hand-tuned multi-size `.ico` for sharper small taskbar icons.
4. Code-signed installer to skip the SmartScreen warning.
