# Installing Alpha on Windows

This document is self-contained. If you are installing Alpha on a Windows
machine, this is the only file you need to read. It does not assume you have
seen any other Alpha document, and it does not assume you already have Python,
Node.js, Git, or Docker.

- [What you are installing](#what-you-are-installing)
- [Requirements](#requirements)
- [Which install path should I use?](#which-install-path-should-i-use)
- [Install: the native path (no Docker)](#install-the-native-path-no-docker)
- [Install: the container path (Docker)](#install-the-container-path-docker)
- [What ends up on my machine, and how big is it?](#what-ends-up-on-my-machine-and-how-big-is-it)
- [Verifying an install](#verifying-an-install)
- [Uninstalling](#uninstalling)
- [Security posture](#security-posture)
- [Failure modes, troubleshooting and what each one means](#failure-modes-troubleshooting-and-what-each-one-means)
- [Every version this installer pins](#every-version-this-installer-pins)
- [Every step the installer performs](#every-step-the-installer-performs)
- [Frequently asked questions](#frequently-asked-questions)

---

## What you are installing

Alpha is a local AI agent system with two long-running pieces:

| Piece | What it is | Default port |
|---|---|---|
| **Gateway** | A Python/FastAPI service (uvicorn) that holds the agent runtime, tools and persistence | `8001` |
| **Web UI** | A Next.js server (Node) that serves the interface and proxies API calls to the gateway | `3000` |

Both run on **your machine only**. By default Alpha talks to no server other
than the ones you configure yourself, and it stores its data in a local SQLite
file, so there is **no database server to install and no cloud account to
create**.

The single most important fact for understanding the size of an Alpha install:

> The installer is small. The Python dependencies are what take up space.
> The installer script is 57.7 KB; the whole installer folder is 83.2 KB. The
> Python virtual environment it provisions is 671.2 MB. Anyone quoting an
> "installer size" without the installed footprint is quoting the wrong number.

---

## Requirements

| Requirement | Value | Why |
|---|---|---|
| **Operating system** | Windows 10 build 19041 (2004) or newer, or Windows 11 | The desktop shell uses the WebView2 runtime that ships with these builds |
| **Architecture** | x64 (AMD64) or ARM64 | 32-bit Windows is **not** supported: the pinned CPython and Node builds do not exist for it |
| **Free disk space** | 6 GB minimum, plus 1 GB headroom (7 GB total) | Measured: uv binary + CPython + Python dependencies + Node + prebuilt frontend + source (see [footprint table](#what-ends-up-on-my-machine-and-how-big-is-it)) |
| **PowerShell** | 5.1 or later | Present by default on Windows 10/11. The installer is PowerShell. If yours is missing, install it from <https://aka.ms/powershell> and re-run |
| **Git** | Any recent version, **only for the native path** | The installer shallow-clones a pinned release tag. Docker users do not need Git on the host |
| **Administrator** | **Not** required | Alpha installs to `%LOCALAPPDATA%\Alpha`, which is a per-user folder |

The installer checks all of the above before it downloads anything, and if a
check fails it **stops and tells you which one and why**, for example:

```
 XX FAILED at stage 'preflight/disk': volume C:\ has only 3800 MB free; Alpha needs
    at least 6144 MB plus 1024 MB of headroom (7168 MB total).
 XX How to proceed: Free up disk space (or point -InstallRoot at a larger volume)
    and re-run. Nothing was downloaded or changed.
```

It never fails with a generic "setup failed".

---

## Which install path should I use?

There are two. Both are supported. **Neither replaces the other.**

| | **Native** (default) | **Container** |
|---|---|---|
| Needs Docker | **No** | Yes |
| Needs Git on the host | Yes | No |
| Downloaded once | ~1.1 GB | ~1.5-2.5 GB (images) |
| Startup after install | Slower (reads from disk) | Faster (image is prebuilt) |
| Can you edit the source? | Yes, in place | No, rebuild the image |
| Best for | A laptop you own | A machine that already runs Docker |

> **Docker is not required.** Requiring Docker is exactly what makes most
> "lightweight" installers heavy. If you do not already have Docker and use it
> every day, use the native path.

---

## Install: the native path (no Docker)

### Step 1 — get the installer

Download the `alpha-native-bootstrapper` artefact from the project's Releases
page. It is a folder of six small text files. Extract it anywhere you like
(`%LOCALAPPDATA%\Alpha-Installer` is a fine place) and keep the folder together:
`bootstrap.ps1` reads its version pins and its helper module from its siblings.

If you got here from a source checkout, the same files are at `installer/` in the
repository and you can run them in place.

### Step 2 — see what it will do, without changing anything

```powershell
powershell -ExecutionPolicy Bypass -File bootstrap.ps1 -DryRun
```

`-DryRun` performs **every check** and prints what it **would** do, and creates
nothing at all. This is the recommended first command. A real transcript:

```
========================================================
        Alpha - unattended Windows installer
========================================================
   DRY RUN: every check runs, nothing on this machine changes.

     Alpha installer starting. Pins: repo=https://github.com/itsPremkumar/alpha.git@v2.1.0 uv=0.11.1 python=3.12 node=22.17.0
 ??  DRY RUN active: no file on this machine will be created or changed.
==> Preflight: checking this machine can host Alpha
       PowerShell 5.1.22621.2506 available
       Microsoft Windows Windows 11 Home Single Language build 22631 (>= 19041 required)
       CPU architecture AMD64
       C:\ has 31968 MB free (>= 7168 MB required)
     Preflight passed
==> [1/8] Provisioning the pinned uv toolchain
 ??  WOULD: install pinned uv 0.11.1
==> [2/8] Provisioning CPython through uv (no Python installer is ever run)
 ??    WOULD run: uv python install 3.12 (uv provisions CPython; no Python installer is ever run)
==> [3/8] Resolving the pinned release
 ??    WOULD confirm that https://github.com/itsPremkumar/alpha.git publishes the tag v2.1.0 (pass -VerifyRemote to actually check)
==> [4/8] Obtaining the source at pinned tag v2.1.0
 ??    WOULD shallow-clone https://github.com/itsPremkumar/alpha.git at the pinned tag v2.1.0 into C:\Users\You\AppData\Local\Alpha\alpha
==> [5/8] Installing Python dependencies from the committed lock
 ??    WOULD run: uv sync --locked --no-dev  (in C:\Users\You\AppData\Local\Alpha\alpha\backend)
==> [6/8] Provisioning the Node runtime and prebuilding the frontend
 ??    WOULD install the pinned Node 22.17.0 runtime and prebuild the Next.js frontend in ...\alpha\frontend
==> [7/8] Preparing user configuration (never overwriting yours)
 ??  WOULD: create config.yaml from the shipped example (SQLite backend, no database server required)
 ??  WOULD: create .env with secrets generated on this machine by secrets.token_urlsafe
 ??  WOULD: create extensions_config.json (every bundled MCP server ships disabled)
 ??  WOULD: create frontend/.env pinned to a production build
==> [8/8] Creating shortcuts and finalising
 ??    WOULD create the Start Menu entry and a desktop shortcut for ...\alpha
 ??    WOULD write the completed marker so a second run is a no-op
==> Post-install verification: starting Alpha and checking health
 ??    dry run: not starting Alpha

========================================================
   DRY RUN COMPLETE - nothing on this machine was changed.
========================================================
   Re-run without -DryRun to perform the install.
```

### Step 3 — install

```powershell
powershell -ExecutionPolicy Bypass -File bootstrap.ps1
```

Expect roughly 10-25 minutes on a normal broadband connection, most of it
`uv sync` and the Next.js production build. The installer prints each step as it
goes. It is unattended: no prompts, no "press Enter".

**What the installer does not do, on purpose:**

- It does not install a second copy of Python. It installs one pinned `uv`
  binary and lets `uv python install` provision the interpreter, so there is
  exactly one tool responsible for the Python that runs Alpha.
- It does not install from a moving branch. The release is a **pinned tag**. If
  the tag does not exist, the installer tells you and stops; it never silently
  falls back to `main`.
- It does not create a database. SQLite is the default, so no database server is
  needed. Postgres is opt-in and you supply the credentials.
- It does not install Git, Python, Node or Docker for you, and it does not
  modify anything outside `%LOCALAPPDATA%\Alpha`.

### Step 4 — the installer proves it works before it claims success

The last thing the installer does is **actually start Alpha and poll its health
endpoint**. If the gateway does not answer, the installer prints
`INSTALL VERIFICATION FAILED`, names which check failed, points you at the log,
and **exits non-zero**. A silent install that leaves a broken app is worse than
no installer, so this step is not optional.

```
---------------- INSTALL VERIFICATION ----------------
  [PASS] start.ps1 present
  [PASS] gateway /health answers (127.0.0.1:8001)
  [PASS] gateway /health/ready answers - status=200 body={"status":"healthy",...}
  [PASS] frontend answers (127.0.0.1:3000) - status=200
     INSTALL VERIFICATION PASSED: Alpha is running and healthy.
```

### Step 5 — open Alpha

- Double-click the **Alpha** shortcut on your desktop or in the Start Menu, or
- open <http://127.0.0.1:3000> in a browser, or
- run `start.ps1` in the install folder.

To stop it: `stop.ps1` in the install folder (or the Alpha shortcut's install
folder). To start it again: `start.ps1`.

### Options

| Option | Effect |
|---|---|
| `-DryRun` | Check everything, change nothing |
| `-Yes` | Never prompt (for unattended/CI use) |
| `-InstallRoot <path>` | Install somewhere other than `%LOCALAPPDATA%\Alpha` |
| `-Tag <vX.Y.Z>` | Install a different published release tag. Must be a tag, never a branch |
| `-RepoUrl <url>` | Install from a fork |
| `-WithPostgres` | Opt in to Postgres. You must supply the database and credentials |
| `-SkipVerification` | Do not start Alpha or check health afterwards. Use for CI images |
| `-VerifyRemote` | In `-DryRun`, actually contact GitHub to confirm the pinned tag exists |
| `-Uninstall` | Run the uninstaller instead of the installer |

---

## Install: the container path (Docker)

Use this if you already run Docker. Alpha is **prebuilt in the image**, so
nothing is compiled on your machine the first time you start it.

```powershell
docker compose -f build/compose.installer.yaml --profile alpha-installer up -d
```

That is the whole command. Then:

- Web UI: <http://127.0.0.1:3000>
- Gateway: <http://127.0.0.1:8001/health>
- Logs: `docker compose -f build/compose.installer.yaml --profile alpha-installer logs -f`
- Stop: `docker compose -f build/compose.installer.yaml --profile alpha-installer down`

**What lands on your machine:** two named Docker volumes
(`alpha-installer-data`, and `alpha-installer-pgdata` only if you use Postgres)
and the Docker images. Your data lives in a named volume, not scattered across
the host filesystem.

**Postgres is strictly opt-in and never starts implicitly.** The
`alpha-installer-postgres` service is behind its own profile, and Compose
**refuses to start it** unless you set `ALPHA_PG_PASSWORD`. The container file
ships no password and the installer never creates a database for you.

**Contributors:** a dev container is included. With VS Code and Dev Containers
installed, "Reopen in Container" gives you a working Alpha environment with the
dependencies synced and the frontend already built.

---

## What ends up on my machine, and how big is it?

### Measured numbers

Everything in the table below was **MEASURED**, not estimated, on:
**Windows 11 Home Single Language, build 22631, AMD64, PowerShell 5.1.22621.2506.**
The measurement method is in [the next subsection](#how-these-numbers-were-measured).
Numbers in **bold** are measured. Anything labelled *estimated* or *not verified*
is called out explicitly.

| Component | Size | Files | What it is |
|---|---:|---:|---|
| `installer/bootstrap.ps1` | **59,111 bytes (57.7 KB)** | 1 | The installer itself |
| `installer/uninstall-alpha.ps1` | **8,255 bytes (8.1 KB)** | 1 | |
| `installer/lib/Alpha.Installer.psm1` | **10,704 bytes (10.5 KB)** | 1 | Shared helpers |
| `installer/create-shortcuts.ps1` | **3,006 bytes (2.9 KB)** | 1 | |
| `installer/pins.json` | **2,057 bytes (2.0 KB)** | 1 | Every version pin |
| `installer/size-budget.json` | **2,091 bytes (2.0 KB)** | 1 | The enforced budgets |
| **Total installer (all 6 shipped files)** | **85,224 bytes (83.2 KB)** | 6 | **What you actually download** |
| uv binary | **48.7 MB** | 1 | Measured for uv **0.12.5**, the copy already on this machine. The **pinned 0.11.1 release asset was not downloaded and its size is NOT VERIFIED**; expect the same order of magnitude |
| CPython 3.12, provisioned by uv | **63.4 MB** | 3,546 | Measured (a single CPython 3.12 install, not the sum of all uv pythons on the machine) |
| **Python dependencies, production install** | **671.2 MB** (logical) | **51,975** | Measured. This is the dominant term by far |
| &nbsp;&nbsp;…real disk consumed | **324 MB** | | Measured from the free-space delta. uv hardlinks wheels out of its cache, so the venv costs roughly half its logical size |
| Node.js runtime | **89.5 MB** | 17 | Measured for the Node install on this machine, which is **v24.21.0**. The **pinned 22.17.0 was not installed and its size is NOT VERIFIED**; expect the same order of magnitude |
| Prebuilt Next.js frontend | **78.5 MB** | 2,470 | Measured (`.next/standalone`) |
| Source checkout, shallow, tag `v2.1.0` | **≤ 256 MB** | | **NOT VERIFIED** for the published tag. Bounded by an enforced budget; the enforcement is real, the exact value is not measured |
| **TOTAL, logical** | **~951 MB + source** | | Sum of the measured terms. Dominated by Python dependencies |
| **TOTAL, real disk** | **~604 MB + source** | | The same sum with the venv counted at its 324 MB real cost. This is the number that matters for "will it fit" |

### For comparison: the existing Electron desktop installer

Also measured, same machine:

| Artefact | Size | Files |
|---|---:|---:|
| `Agent-Workspace-Setup-2.1.0.exe` (NSIS installer) | **195.4 MB** | 1 |
| `win-unpacked` (installed tree) | **585.8 MB** | 7,323 |
| &nbsp;&nbsp;…of which portable Node + uv runtime | **121.9 MB** | 4 |
| &nbsp;&nbsp;…of which prebuilt frontend | **78.5 MB** | 2,470 |

### How these numbers were measured

- **Direct measurement:** every tree was walked file-by-file summing
  `st_size` (logical bytes), and file counts are real counts. The **real disk
  consumed** figure is different and is stated separately: it is the free-space
  delta on `C:` measured immediately before and after creating a throwaway
  virtual environment, which is the only way to see past uv's hardlinks.
- **The production Python environment** was built for real, from the committed
  lockfile, using `uv export --frozen --no-dev` and a clean
  `uv venv` + `uv pip install`, so it is exactly what the installer provisions.
  It took **51.2 seconds** to install 221 distributions.
- **The dev environment** (1,346.5 MB / 73,457 files) is a *developer* venv with
  **every optional extra installed**. It is **not** what a user gets, and it is
  675 MB larger than the production install. This distinction is the single most
  useful measurement in this document.

### Prune list: what is declared but not needed, and what it costs

The production install carries some dependencies that a basic Alpha user never
executes. These are **measured** sizes in the production virtual environment,
and the "why" column comes from reading the dependency graph with
`uv tree --invert` (which package requires it) and grepping the backend source
for imports.

| Package / subtree | Size | Reached via | On a live path? |
|---|---:|---|---|
| `markitdown[all,xlsx]` subtree | **~120 MB** | `agent-workspace-harness` (core dep) | **Mostly not.** Pulls in `magika` (an ML file-type detector → `onnxruntime` 31.6 MB), `speechrecognition` 42.6 MB (audio transcription), `python-pptx`, `youtube-transcript-api` 8.6 MB, `pdfminer` 8.1 MB, `xlrd`, `xlsxwriter`. Alpha's own tools do not transcribe audio or read MP3s. **This is the single largest prune opportunity.** |
| `langchain` subtree | ~55 MB | harness core | Partly. `sympy` 25.4 MB, `babel` 29.4 MB, `numpy` 18.8 + `numpy.libs` 20 MB are pulled in as transitive dependencies of the LLM client libraries, not used directly by Alpha |
| `kubernetes` | 16.1 MB | harness core | Only for the Kubernetes sandbox provider. A local-only user never imports it |
| `lark_oapi` | 16.6 MB | Alpha core dep | Live, but only if you use the Lark/Feishu channel |
| `volcenginesdk*` (4 packages) | ~29 MB | via `markitdown[all]` | Not on any Alpha path; document conversion only |
| `PIL` / `pillow` | 14.0 MB | `markitdown[all]` | Not on any Alpha path |

**Optional extras that a default install correctly does NOT get** (measured by
comparing the dev venv against the production venv — 675 MB of avoidable
weight):

| Extra | Size | What it is |
|---|---:|---|
| `opencv-python` (`cv2`) | **112.4 MB** | Vision. Largest single package in the whole environment |
| `playwright` | 104.1 MB | Browser automation. Also needs `playwright install chromium` (a further ~150 MB of browsers) |
| `av.libs` (PyAV) | 62.6 MB | FFmpeg bindings for audio/video |
| `ctranslate2` + `faster-whisper` | 59.4 MB | Speech-to-text model runtime |
| `piper-tts` | 43.9 MB | Text-to-speech |
| `rapidocr-onnxruntime` | 15.6 MB | OCR |
| `jieba` | 37.7 MB | Chinese word segmentation for memory search |
| `pandas` | 33.0 MB | Pulled in by `markitdown[all]` |

**Conclusion:** a default Alpha install leaves roughly **675 MB** on the table
purely because a developer's environment enables every extra. The installer
uses `uv sync --locked --no-dev`, so a user gets the lean set. Cutting the
`markitdown[all]` subtree would be the next meaningful reduction, but it changes
a core dependency in `backend/packages/harness/pyproject.toml`, which is outside
this document's scope and needs its own decision.

### Reinstalling is safe and does not grow the disk

`uv sync` is convergent, not additive: running it against the same lockfile
leaves the environment in the same state, so re-running the installer does not
accumulate megabytes.

---

## Verifying an install

### The quick way

```powershell
# 1. The gateway's health endpoint
curl http://127.0.0.1:8001/health
# {"status":"healthy","service":"agent-workspace-gateway"}

# 2. The readiness endpoint (probes the database and the checkpointer)
curl http://127.0.0.1:8001/health/ready
# 200 with status "healthy" when persistence is reachable

# 3. The web UI
curl -o NUL -w "%{http_code}" http://127.0.0.1:3000/
# 200
```

### The thorough way

```powershell
# Which release is installed, and with which toolchain
Get-Content $env:LOCALAPPDATA\Alpha\.alpha-install-complete

# The installer log: every step, its exit code and how long it took
Get-Content $env:LOCALAPPDATA\Alpha\logs\alpha-installer.log -Tail 40

# The app's own logs
Get-Content $env:LOCALAPPDATA\Alpha\alpha\logs\gateway.err.log -Tail 40
Get-Content $env:LOCALAPPDATA\Alpha\alpha\logs\frontend.err.log -Tail 40
```

`.alpha-install-complete` should name the pinned tag and the toolchain versions:

```
completed_utc=2026-09-26T12:34:56Z
pinned_tag=v2.1.0
uv_version=0.11.1
python_series=3.12
node_version=22.17.0
database_backend=sqlite
```

### Checking your install is reproducible

```powershell
cd $env:LOCALAPPDATA\Alpha\alpha\backend
& $env:LOCALAPPDATA\Alpha\tools\uv\uv.exe sync --locked --check
```

This verifies the installed environment is exactly what the committed
`uv.lock` describes, using the same pinned uv. If it passes, your install is
byte-for-byte the environment CI tested.

---

## Uninstalling

```powershell
powershell -ExecutionPolicy Bypass -File uninstall-alpha.ps1
```

It asks for confirmation and then:

1. Stops Alpha (via the app's own `stop.ps1`, then sweeps the ports it uses).
2. Removes the Start Menu and desktop shortcuts.
3. Removes the `Alpha_Autostart` / `Alpha_Watchdog` / `Alpha_TrayStatus`
   scheduled tasks, if the autostart layer was ever registered.
4. Removes `%LOCALAPPDATA%\Alpha` entirely.

**What it deliberately keeps:** your `config.yaml`, your `.env` (and therefore
your generated secrets) and your agent data. Those are yours, not the
installer's, so removing them needs its own separate decision:

```powershell
powershell -ExecutionPolicy Bypass -File uninstall-alpha.ps1 -Yes -PurgeData
```

`-PurgeData` deletes `config.yaml`, `.env` and the runtime data directory. If
you keep them, re-installing to the same folder restores your setup exactly as
it was.

Nothing outside `%LOCALAPPDATA%\Alpha` is ever touched.

---

## Security posture

### Where secrets come from

**They are generated on your machine, at install time, and they are never
shipped.**

The installer calls Python's standard-library CSPRNG:

```python
import secrets
secrets.token_urlsafe(48)
```

Two independent values are generated (`BETTER_AUTH_SECRET` and `CSRF_SECRET`)
and appended to `%LOCALAPPDATA%\Alpha\alpha\.env`.

- **No secret is present in the installer, in any release artefact, or in any
  file in the repository.** If a secret travelled with the installer it would be
  the same secret for every user on earth.
- **No secret is ever printed** to the console, to the installer log, or to any
  other log. A contract test asserts that a generated secret does not appear in
  the installer's captured output.
- **`.env` is gitignored** by the repository, so a generated secret cannot be
  committed even by accident.
- **Re-running the installer never regenerates an existing `.env`.** Your
  secrets stay yours across reinstalls and upgrades.

### What is never shipped

| Never shipped | Why |
|---|---|
| API keys, tokens, passwords | Generated locally, or you supply them |
| A Postgres password | The container file refuses to start without `ALPHA_PG_PASSWORD` |
| A pre-built `.env` | Only `.env.example`, whose secret slots are placeholders |
| A database | Alpha defaults to SQLite, a local file |
| A login prompt you cannot disable | The local install is single-user and bound to `127.0.0.1` |

### Network exposure

By default Alpha binds to **`127.0.0.1`**, the loopback interface. It is not
reachable from the local network. Do not port-forward these ports to the
internet without putting authentication in front of them.

### Supply chain

| Pin | Value | Enforced by |
|---|---|---|
| Alpha release | A **git tag**, never a branch | The installer **refuses** a branch name and exits non-zero. `installer/pins.json` |
| `uv` | `0.11.1` | Must equal `backend/Dockerfile`'s `UV_IMAGE`. Three separate tests fail the build if these drift |
| Python | `3.12` series | `uv` resolves the exact patch |
| Node.js | `22.17.0` (exact) | Must equal `electron/desktop-config.json` |
| pnpm | `10.26.2` | Must equal the root `package.json` |
| Python dependencies | `uv.lock`, `--locked` | `--locked` fails rather than re-resolving |
| MCP servers | All disabled by default | The shipped `extensions_config.example.json` |

Every one of these pins lives in **one greppable file**,
`installer/pins.json`. Nothing else pins a version.

---

## Failure modes, troubleshooting and what each one means

Every failure below exits **non-zero** and names the real reason. This table
tells you what the reason actually means.

| What you see | What it means | What to do |
|---|---|---|
| `FAILED at stage 'preflight/os': this is ... build < 17763, but Alpha requires Windows 10 build 19041` | Windows is too old for the WebView2 runtime the desktop shell uses | Run Windows Update until the build is at least 19041 |
| `FAILED at stage 'preflight/arch': CPU architecture 'x86' is not supported` | 32-bit Windows. The pinned CPython and Node builds do not exist for it | Reinstall 64-bit Windows, or use the container path |
| `FAILED at stage 'preflight/disk': volume C:\ has only N MB free` | Not enough disk. Checked **before** anything is downloaded, so nothing is half-installed | Free space, or `-InstallRoot` a bigger volume |
| `FAILED at stage 'preflight/powershell': Windows PowerShell 5.1 or later is required` | A stripped/Server Core image with no PowerShell | Install PowerShell 5.1 from <https://aka.ms/powershell> |
| `FAILED at stage 'pinned-tag': the pinned tag 'vX.Y.Z' could not be read from ...` | Either that release was never published, or this machine cannot reach GitHub | If you published it, check your network. The installer deliberately will **not** fall back to a branch |
| `'vX.Y.Z' is a moving branch, not a release` | You passed `-Tag main` (or similar) | Pass an immutable tag |
| `FAILED at stage 'clone': ... exited 128` | Git is missing, or the network blocked the clone | Install Git, or use the container path (needs no host Git) |
| `FAILED at stage 'uv-install': could not install the pinned uv 0.11.1 from ...` | The uv release download failed | Check access to `github.com/astral-sh/uv` releases, or a proxy/antivirus is blocking it |
| `FAILED at stage 'python': uv python install 3.12 failed` | uv could not download CPython | Check network; `uv` caches downloads, so re-running resumes |
| `FAILED at stage 'uv-sync': ... exited N. The committed uv.lock is not what uv 0.11.1 can resolve` | A **lockfile/uv version mismatch**, not a machine problem | Use the uv version in `installer/pins.json`. The container path pins uv in the image and cannot hit this |
| `uv sync ... did not finish within its 1800s timeout` | A slow mirror or a hung network | Re-run. `uv` caches what it already downloaded, so the retry is faster |
| `FAILED at stage 'pnpm-install': frontend dependency install failed` | npm registry unreachable | Check network. The backend is already installed, so re-running is safe |
| `FAILED at stage 'frontend-build': the Next.js production build failed` | Usually out of memory. The build wants ~1.5 GB free RAM | Close other applications and re-run |
| `INSTALL VERIFICATION FAILED: 1 check(s) did not pass` | **Alpha is installed but is NOT working.** The named check says which part | Read `logs\gateway.err.log` and `logs\frontend.err.log`, then re-run the installer (safe) |
| `INSTALL VERIFICATION FAILED ... frontend answers` but the gateway passed | The gateway is up; the Node/Next.js frontend is not | Usually the frontend build did not complete. Check `logs\frontend.err.log` |
| `Alpha is already installed at ...` | You ran the installer twice. **This is success**, not an error: it exits 0 and changes nothing | Nothing. Use `uninstall-alpha.ps1 -Yes` first for a clean reinstall |
| `An incomplete Alpha install already exists at ...; resuming` | A previous run stopped partway (for example the network dropped) | Just re-run. It resumes without re-cloning and without touching your config |
| Container `Alpha` will not start: `ALPHA_PG_PASSWORD` unset | You asked for the Postgres profile without supplying a password | Set it, or drop the profile: SQLite needs no database server |

### Re-running is always safe

The installer is **idempotent**, and that is enforced by tests, not by promise:

- An existing install is detected by a marker file and the installer exits 0
  without doing anything.
- An existing checkout is **never re-cloned**, never fetched, never reset.
- `config.yaml`, `.env` and `extensions_config.json` are created **only when
  absent**, so an upgrade can never revert your settings or rotate your secrets.
- A partial install **resumes** rather than restarting.

---

## Every version this installer pins

All in `installer/pins.json`, the single source of truth.

| Thing | Pinned to | Notes |
|---|---|---|
| Alpha release | `v2.1.0` | A tag. Must be `vMAJOR.MINOR.PATCH`; a branch is rejected |
| Repository | `https://github.com/itsPremkumar/alpha.git` | Shallow, single-branch |
| `uv` | `0.11.1` | **Must equal `backend/Dockerfile`'s `UV_IMAGE`** |
| Python | `3.12` | Minor series; uv picks the patch |
| Node.js | `22.17.0` | Exact. **Must equal `electron/desktop-config.json`** |
| pnpm | `10.26.2` | **Must equal the root `package.json` `packageManager`** |
| Windows build | `19041`+ | Windows 10 2004 |
| Architectures | `AMD64`, `ARM64` | |
| Free disk | `6144` MB + `1024` MB margin | |
| Clone timeout | 900 s | |
| `uv sync` timeout | 1800 s | |
| Node/pnpm timeout | 900 s | |
| Health timeout | 600 s | |

---

## Every step the installer performs

| # | Step | What it does | Time (measured) |
|---|---|---|---|
| 0 | **Preflight** | PowerShell ≥ 5.1, Windows build, CPU architecture, free disk, writable install root. Exits non-zero naming the real reason | ~1 s |
| — | **Idempotency gate** | If `%LOCALAPPDATA%\Alpha\.alpha-install-complete` exists, stop here and exit 0 | instant |
| 1 | **Pinned `uv`** | Download the pinned `uv` release and unpack `uv.exe` into `%LOCALAPPDATA%\Alpha\tools\uv`. Skipped if the right version is already there | ~5 s |
| 2 | **Pinned CPython** | `uv python install 3.12`. Python is **never** installed from python.org | ~20 s |
| 3 | **Resolve the release** | `git ls-remote` to prove the pinned tag exists, so a missing tag fails in one clear line instead of deep inside a clone | ~5 s |
| 4 | **Shallow clone** | `git clone --depth 1 --single-branch --branch <tag>`. **Skipped entirely** if a checkout already exists | ~60 s |
| 5 | **`uv sync --locked --no-dev`** | Provision the production Python environment from the committed lock. `--locked` fails rather than re-resolving; `--no-dev` drops pytest/ruff/hypothesis | **51.2 s** (measured) |
| 6 | **Node + frontend** | Install the pinned Node if absent, `pnpm install --frozen-lockfile`, then a Next.js **production** build so the first launch is fast | ~5-15 min |
| 7 | **User configuration** | Create `config.yaml` (from the shipped example), `.env` (with locally generated secrets), `extensions_config.json` and `frontend/.env` — **each only if absent** | ~1 s |
| 8 | **Shortcuts + marker** | Start Menu and desktop shortcuts, then write `.alpha-install-complete` | ~2 s |
| 9 | **Post-install verification** | **Start Alpha**, poll `/health`, `/health/ready` and the frontend. Exit non-zero if any check fails | ~30-120 s |

Every subprocess is bounded by a named timeout, and every failure names the step
that failed, the real reason, and what to do next.

---

## The desktop shell: why there is no "lightweight" build of Alpha

Some installers replace Electron with a Tauri/WebView2 shell to avoid
bundling Chromium. **Verdict for Alpha: not viable, and not attempted.**

The reason is measured, not aesthetic. Alpha's frontend is a **Next.js server**,
not a static export:

1. `frontend/next.config.mjs` proxies `/api/*` to the gateway with
   `rewrites()`. **A rewrite is a server-side feature; a static export cannot
   perform one.**
2. The frontend has **no route handlers of its own** (no `app/api`, no
   `route.ts` anywhere), so the gateway behind that rewrite *is* the entire API
   surface. Strip the server and there is no API.
3. The existing Electron build proves the point empirically: it ships a Node
   runtime (**121.9 MB** measured) and a Next standalone server
   (**78.5 MB** measured) inside the bundle.

A static bundle would therefore load the interface and then fail **every single
API call**. Shipping one would be shipping a shell that cannot run the app, so
it was not built.

**What is provided instead.** `build/alpha-launcher.ps1` (~7 KB) starts the
stack if it is stopped, proves `/health` answers, and opens the local server.
The size argument is real: the OS handler for `http://` on Windows 10/11 is
Edge, which **is a WebView2 host**, so the renderer is already on the machine and
**0 MB** of Chromium is downloaded or bundled — against **121.9 MB** of portable
Node inside a **585.8 MB** unpacked Electron tree and a **195.4 MB** installer.

**Honest limitation.** Opening the OS browser is **not** a native WebView2
window: there is no app chrome, no tray icon, no frameless frame. That needs a
`WebView2Loader.dll` host, which is a real build artefact, not a cheap
prototype. **Electron remains the supported desktop shell and was not
replaced.** No cold-start number is claimed here, because none was measured.

---

## Frequently asked questions

**Do I need to install Python, Node.js or Docker first?**
No. The installer provisions Python through `uv` and installs a pinned Node
runtime. You need **Git** for the native path (it clones the release) and
**Docker** only for the container path.

**Do I need Administrator rights?**
No. Everything goes into `%LOCALAPPDATA%\Alpha`, which is per-user.

**Why is the download bigger than the installer?**
Because the Python dependencies dominate. See
[the footprint table](#what-ends-up-on-my-machine-and-how-big-is-it): 83.2 KB of
installer, 671.2 MB of Python environment.

**Why does the installer need a Node runtime? The UI is just a web page.**
Because the frontend is a **Next.js server**, not a static export.
`frontend/next.config.mjs` proxies `/api/*` to the gateway using `rewrites()`,
and a rewrite is a server-side feature a static bundle cannot perform. This is
also why there is no "lightweight static Tauri bundle" of Alpha: it would load
and then fail on every API call.

**Can I upgrade to a newer release?**
Install to a new folder with `-Tag vX.Y.Z`, start it, and once you are happy,
uninstall the old one with `-PurgeData` if you want the disk space back. Keeping
the same folder and re-running the installer deliberately does **not** upgrade
you, because re-running must never overwrite your `config.yaml` or rotate your
secrets.

**Can I use Postgres?**
Yes, with `-WithPostgres` on the native path or the `alpha-installer-postgres`
compose profile on the container path. You supply the database and the
credentials; the installer creates no database and ships no password. **SQLite
is the default and needs no database server at all.**

**Is it safe to run the installer twice?**
Yes, and that is a tested guarantee, not a hope. It exits 0, reports that Alpha
is already installed, and changes nothing — verified by comparing SHA-256
hashes of the whole install tree before and after.

**What is in the `.alpha-install-complete` file?**
The release tag, the toolchain versions and the database backend. It is the
marker that makes a re-run a no-op. Deleting it makes the installer resume
against the existing folder (still without re-cloning or clobbering your
config).

**Something failed. Where do I look?**
`%LOCALAPPDATA%\Alpha\logs\alpha-installer.log` has one line per step with its
exit code and duration. Every failure message also names the stage and the fix.

---

## See also

- `installer/pins.json` — every version pin, in one greppable file
- `installer/size-budget.json` — the enforced size budgets and the recorded measurements
- `installer/bootstrap.ps1` — the installer, with the reasoning in comments
- `installer/uninstall-alpha.ps1` — the uninstaller
- `build/compose.installer.yaml` — the container path
- `build/alpha-launcher.ps1` — a ~2 KB launcher that opens the running local server
- `.devcontainer/devcontainer.json` — the contributor dev container
- `installer/tests/test_installer_contract.py` — the tests that enforce every claim above
