# E2E verification harness

Two scripts that turn "the feature works" into evidence another agent can read
without trusting the author. Both are infrastructure: they add no product
surface and no runtime dependency.

| Script | What it answers |
| --- | --- |
| [`harness.py`](harness.py) | Does a real prompt actually run end to end? |
| [`screenshots.mjs`](screenshots.mjs) | Does each UI route render **without** logging errors or failing requests? |

Run them from the repository root.

## Why they exist

A screenshot cannot show you that a page is logging a 500, and a 200 response
cannot show you that the model never answered. Each script collects the other
signal:

- `harness.py` reads the **SSE stream to its terminal frame** and reports the
  server's own `end` payload. A run that stopped mid-stream is a failure with
  its partial bytes kept, never a quiet success.
- `screenshots.mjs` records **console errors, uncaught exceptions, and every
  4xx/5xx or `requestfailed` response per route**. A page that renders
  perfectly while every `/api/*` call 500s looks identical to a working page in
  a screenshot; only the network log separates them.

## The honesty contract

This is the part that matters, and it is enforced in code, not in a comment.

- **A completed run is never a verified run.** `harness.py` emits
  `verdict_scope` inside the result JSON: `pass` means the Gateway accepted a
  real prompt and streamed it to a terminal frame the *server* marked `ok`. It
  does not mean the answer is correct or that any feature works.
- **A measurement nobody took is `null`, never `0`.** An unreadable token
  endpoint yields `server_reported: null` with the reason beside it — never an
  empty object that reads as "zero tokens used".
- **A blank page is a failure.** `screenshots.mjs` measures rendered text length
  and a DOM root, and refuses to pass a route that rendered nothing. It will
  still write the PNG, because a blank page is evidence — of a bug.
- **Missing prerequisites are `blocked`, not `pass`.** No Playwright, no
  Chromium, no healthy Gateway: each is reported as `blocked` with the exact
  install or start command, never as "0 routes failed".
- **Exit codes are meaningful.** `0` only for `pass`. `fail` and `blocked` both
  exit non-zero, so a CI gate or a wrapping agent cannot miss them.

Nothing existing is ever overwritten. Both scripts allocate a fresh
`docs/evidence/<domain>/<date>/<time>-<n>/` directory and refuse to reuse one.

## 1. The driver

One prompt in, one JSON verdict on disk. Standard library only — it does not
import `alpha`, so it is safe to run while the Gateway is down.

```powershell
backend\.venv\Scripts\python.exe scripts/e2e/harness.py "Say pong"
```

Attach to an already-running stack (the default), or start one:

```powershell
# Wait for an existing Gateway (default base http://127.0.0.1:8001)
backend\.venv\Scripts\python.exe scripts/e2e/harness.py --health-only

# Start the Gateway only if it is not already healthy, then run the prompt
backend\.venv\Scripts\python.exe scripts/e2e/harness.py --start "Say pong"
```

Useful flags:

| Flag | Effect |
| --- | --- |
| `--base URL` | Gateway base (default `http://127.0.0.1:8001`, or `$ALPHA_E2E_BASE`) |
| `--start` / `--start-frontend` | launch a service only when it is not already healthy |
| `--boot-timeout S` | how long to wait for a cold Gateway (default 420 — the boot is minutes) |
| `--timeout S` | stream timeout (default 600) |
| `--model NAME` | a name from `config.yaml` `models[]` (default `union-alpha`) |
| `--domain NAME` | evidence subdirectory under `docs/evidence/` |
| `--keep-thread` | leave the created thread in place |

Outputs into the evidence directory:

- `result.json` — the verdict, the step log, per-frame event index, token
  accounting (client-side and server-reported, kept separate so a disagreement
  is visible), the error frames, and the teardown record.
- `raw.sse` — the complete unmodified stream. `result.json` keeps a bounded
  index for readability and discloses the truncation; the raw file is never
  lossy.

Teardown kills the process **tree** (`taskkill /T /F` on Windows, `killpg`
elsewhere) and only for processes this run spawned. An attached, pre-existing
stack is left alone. `SIGINT`/`SIGTERM`/`SIGBREAK` tear down too.

**Teardown verifies itself rather than assuming.** Killing the tracked PID is
not sufficient on its own: `uv run` → console script → interpreter is a
four-level chain, and a harness that reports "terminated" while a service it
started keeps holding its port has fabricated its own teardown. So after the
tree kill the driver re-checks the port, kills any surviving listener
explicitly, and records `port` / `released` / `killed_pids` under `teardown` in
the result JSON. A port that stays bound is reported as a `WARNING`, not
quietly ignored.

> **Auth.** `--start` sets `ALPHA_AUTH_DISABLED=1`, the repo's documented local
> E2E mode (`backend/app/gateway/auth_disabled.py`). The result records
> `stack.auth_mode` so a reader always knows which identity ran the prompt. Do
> not set it in a shared or production deployment.

## 2. Screenshots + console/network capture

```powershell
node scripts/e2e/screenshots.mjs
```

Captures every workspace view plus the default chat route, writes one PNG per
route into `docs/evidence/screenshots/<date>/<time>-<n>/`, and writes a
`report.json` carrying each route's console errors, page exceptions, failed
requests, HTTP error responses, and its own verdict.

| Flag | Effect |
| --- | --- |
| `--base URL` | frontend base (default `http://127.0.0.1:3000`) |
| `--routes "a,b,c"` | capture an explicit subset |
| `--domain NAME` | evidence subdirectory |
| `--list` | print the route list and exit |
| `--check` | parse and list only, never launch a browser |
| `--settle MS` | post-load settle time (default 2500) |
| `--timeout S` | navigation timeout (default 45) |
| `--allow-console-errors` | report, but do not fail, on console errors |
| `--chromium PATH` | use a specific browser executable |

### The route list is derived, not copied

`WORKSPACE_VIEW_IDS` in `frontend/src/lib/workspace-view.ts` is the single
source. The script parses that array (stripping `//` comments, which quote real
ids and would otherwise inflate the count) instead of keeping its own copy. A
hand-copied list would silently drift, and a drifted id falls back to `chat`
without any error — so a stale route list would screenshot the same page 37
times and report a confident pass.

If the registry cannot be read, the route list contains an explicit blocked
entry rather than quietly shrinking.

### Browser resolution

First hit wins: `--chromium` → a Playwright-bundled browser → the newest
`chromium-*` in the Playwright user cache (`%LOCALAPPDATA%\ms-playwright` on
Windows) → Playwright's own default. If nothing launches, the run is `blocked`
with the install command:

```powershell
pnpm --dir frontend add -D playwright
node frontend/node_modules/playwright/cli.js install chromium
```

No browser is downloaded into the repository automatically.

## Reading a real failure

A genuine run on this machine (OpenRouter account out of credits) produced:

```
verdict : fail
reason  : The server's terminal frame reported status='error' ok=False, not a success.
stream  : HTTP 200 · 25 frames · 53286 bytes · 215.469s
events  : {'metadata': 1, 'heartbeat': 5, '(unnamed)': 5, 'values': 8, 'messages': 4, 'error': 1, 'end': 1}
answer  : The configured LLM provider rejected the request because the account is out of quota...
```

The pipeline worked — the Gateway booted, accepted the prompt, and streamed 25
real frames to a terminal `end`. The **run** failed, at the provider, with
`HTTP 402 ... You requested up to 16384 tokens, but can only afford 190`. That
is a billing limit, not a harness defect, and the harness is what let you tell
those two apart in one command instead of guessing.

Note what it did *not* do: report `pass` because the transport returned 200 and
answer-shaped text was present. The error fallback message *is* answer text.
Only the terminal frame's own `ok` decides it.

## Working with the results

- `result.json` / `report.json` are the source of truth; the PNGs are
  supporting evidence.
- `verdict: "failed"` with `failed_requests` naming the same URL across every
  route usually means one backend is down, not N frontend bugs. Check the
  route that passed — it is the one that made no API calls.
- A `blocked` verdict means nothing was measured. Do not report it as a pass,
  and do not "retry until green" without reading `verdict_reason` first.
- A `fail` verdict with `end_status: "error"` and a populated `error_events`
  is usually an environment limit (credits, quota, provider outage). Check the
  message before suspecting the code under test.