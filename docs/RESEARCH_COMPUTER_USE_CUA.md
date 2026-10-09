# Research: full-desktop computer control (Cua Driver and the open CUA landscape)

**Slice owner:** `feat/computer-use-cua` worktree.
**URLs retrieved 2026-10-09** unless a different retrieval date is stated inline.
Nothing here is inferred from training data: each external claim carries the URL it
came from. Claims I could not verify are marked **UNVERIFIED**.

> **What this document is.** A decision record: what Alpha already controls natively,
> what the fully-open-source computer-use-agent (CUA) landscape offers, why **Cua
> Driver** is the one external driver worth wiring in, and exactly what that wiring
> does and does not prove.
>
> **What it is not.** Evidence that Cua Driver runs. No `cua-driver` binary was
> executed in this environment; the integration shipped alongside this document is a
> *disabled-by-default configuration entry plus a contract test*. Runtime behaviour is
> **UNVERIFIED here** and the setup runbook below is the thing that would verify it.

---

## 1. Short answer

1. **Alpha already has a native, sentinel-guarded computer-control stack.** Five
   `desktop_*` tools over `alpha/computer_use/` (screenshots, accessibility-tree
   inspection, guarded mouse/keyboard, window management), opt-in and disabled by
   default, Windows-first. See §2 — this is code, not a plan.
2. **The gap is not "can Alpha click", it is coverage and platform.** The native stack
   drives one OS session through `pyautogui`/`pywinauto` and observes through the
   accessibility tree. It has no per-window *background* input, no macOS/Linux
   accessibility backend, and no app/window lifecycle surface beyond its own window
   tool.
3. **Cua Driver** (<https://github.com/trycua/cua>) is the strongest free driver for
   that gap: MIT-licensed (the driver, not everything in that repo — see §4.1),
   cross-platform (macOS 14+/Windows 10/11/x86_64 Linux), background input to a
   chosen window without moving the pointer, ~50 tools over one stdio MCP server, and
   **it never calls a model** — Alpha keeps the loop, exactly the division of labour
   `browser-use` uses.
4. **Wire it as an MCP server, not as a dependency.** A `cua-driver` stdio block ships
   in `extensions_config.example.json`, disabled, mirroring the `browser-use` block.
   Alpha's governance layer then classifies every `cua-driver_*` tool as an elevated
   (untrusted-source) tool by default, so an operator must opt into auto-approval
   rather than out of it. See §6.
5. **Do not install its AGPL extensions.** `cua-perception` (AGPL-3.0-only) and the
   optional `cua-som` package (AGPL-3.0-or-later) stay out; Cua Spaces apps are
   FSL-1.1-MIT source-available, not MIT. Only the driver itself is MIT. See §4.1.

---

## 2. What Alpha already ships (code-evidenced)

Nothing in this section is aspirational; every line points at a file in this tree.

| Layer | Where | What it does |
| --- | --- | --- |
| Package | `backend/packages/harness/alpha/computer_use/` | `screen.py` (`mss` screenshots + Set-of-Marks annotator hook), `accessibility.py` (UIA tree, optional `pywinauto`, honest-unavailable when missing), `dispatcher.py` (guarded mouse/keyboard/process, optional `pyautogui`/`pynput`), `guard.py` (`SentinelGuard`: panic-corner kill switch, window-boundary lock, hotkey blacklist), `system_one_policy.py` (semantic-index decisions) |
| Model surface | `backend/packages/harness/alpha/tools/builtins/os_computer_tool.py` | exactly five tools: `desktop_screenshot`, `desktop_inspect_ui_tree`, `desktop_mouse_action`, `desktop_keyboard_action`, `desktop_window_manage` |
| Facade | `computer_use/__init__.py::LaptopController` | construction performs no OS calls; `availability()` probes per subsystem and reports a reason instead of a false `False` |
| Policy | `alpha/computer_use/system_one_policy.py` | with `system_one.enable_computer_action`, the model returns **semantic element indexes only** — never coordinates, selectors, keys or typed text — and the tool re-observes the tree, re-resolves geometry locally, then dispatches through the sentinel. `None`, malformed, low-confidence, truncated, shadow-mode and stale decisions dispatch nothing |

The design property worth naming, because it is what a naive "add a computer-use SDK"
change would destroy: **the model never sees coordinates and never issues a raw
input event.** Perception and action are separated by a deterministic guard, and the
guard is the thing that decides whether anything moves. See
[docs/SYSTEM_ONE.md](SYSTEM_ONE.md),
[docs/SYSTEM_ONE_LAPTOP_CONTROL_IDEAS.md](SYSTEM_ONE_LAPTOP_CONTROL_IDEAS.md), and
the `System One indexed computer control` section of
`backend/packages/harness/alpha/AGENTS.md`.

**The honest limits of the native stack**, measured from this tree:

- **Windows-first.** `accessibility.py` is UIA/`pywinauto`; there is no macOS AX or
  Linux AT-SPI backend. The README bullet calls it "opt-in Windows desktop control"
  for that reason.
- **Foreground input.** `dispatcher.py` sends global input events; it does not do
  per-window background delivery, so a click requires the target to hold focus.
- **App lifecycle** is only what `desktop_window_manage` offers (list/focus/launch,
  boundary lock). There is no `kill_app`, no `set_window_frame`, no menu-bar
  invocation, no clipboard surface, no multi-window geometry control.
- **All five tools are opt-in** and the whole capability is disabled by default.

Those four lines are the requirement list for anything external. Cua Driver answers
all of them; that is the whole argument.

---

## 3. Cua Driver

Repository: <https://github.com/trycua/cua> · docs: <https://cua.ai/docs/cua-driver> ·
source notes: `libs/cua-driver/README.md` (all retrieved 2026-10-09).

**What it is:** a local driver binary that inspects and operates native desktop apps
and browsers on macOS, Windows and Linux, reachable three ways from one binary: MCP
stdio server (`cua-driver mcp`), long-running daemon (`cua-driver serve`), or one-shot
CLI (`cua-driver <tool> '<json>'` / `cua-driver call <tool> …`). The same code path
serves all three, so `cua-driver list_apps` from a shell and the MCP `list_apps` tool
are the same call.

**What it deliberately is not:** an agent. Its own docs state the split — *"The
harness owns the model and the loop; Cua Driver never calls a model API."*
(<https://cua.ai/docs/cua-driver/guides/connect-your-agent>, retrieved 2026-10-09>).
No API key, no planner, no second context window. This is the same division of labour
Alpha already applies to `browser-use` (see the `Managed browser-use runtime` section
of `backend/packages/harness/alpha/AGENTS.md`): Alpha plans, the external system
executes one bounded class of action.

### 3.1 Tool surface

`cua-driver mcp` serves the tools over stdio; the reference page groups them
(<https://cua.ai/docs/cua-driver/reference/mcp-tools>, retrieved 2026-10-09):

| Group | Representative tools |
| --- | --- |
| Apps & windows | `list_apps`, `list_windows`, `launch_app`, `kill_app`, `bring_to_front`, `set_window_frame`, `invoke_menu` |
| Window state | `get_window_state` (accessibility tree **+** screenshot of one window), `get_accessibility_tree`, `verify_state`, `parse_visual_regions` |
| Screen | `get_desktop_state`, `get_screen_size`, `get_cursor_position`, `zoom` |
| Pointer | `click`, `drag`, `scroll`, `move_cursor` (+ Linux-only `mouse_button_*`, `parallel_mouse_drag`) |
| Keys | `press_key`, `hotkey`, type-text |
| Values & clipboard | `set_value` (by `element_token`), `clipboard_read`, `clipboard_write` |
| Browser | `get_browser_state`, `browser_prepare`, `browser_navigate`, legacy `page` |
| Session / config / maintenance | `start_session`/`end_session`, `get_config`, `set_config`, `check_permissions`, `health_report`, `check_for_update`, `install_extension` |

The macOS reference page counts **56 tools**; the count is per-platform (several
pointer tools are Linux-only, `debug_window_info` is Windows-only), so treat 56 as the
macOS ceiling, not a portable number. Two properties matter more than the count:

- **Grounding is token-based, not coordinate-only.** Input tools address an
  `element_token` from a just-observed window snapshot, which is the same shape as
  Alpha's `[ref]`-indexed browser loop and System One's semantic indexes — the model
  picks from an observation, never invents a selector.
- **`verify_state` is deterministic.** Bounded predicates checked against one exact
  window, i.e. the external driver has its own "did that actually happen" primitive.
  Alpha should consume that rather than re-deriving success from a happy-path return.

### 3.2 Runtime and permission model

- **Install (Windows):** `irm https://cua.ai/driver/install.ps1 | iex`
  (macOS/Linux: `/bin/bash -c "$(curl -fsSL https://cua.ai/driver/install.sh)"`).
  Post-install check: `cua-driver doctor`, `cua-driver --version`,
  `cua-driver call list_apps`.
- **Process model:** on Windows and Linux, bare `cua-driver mcp` owns its runtime and
  shuts it down on stdin EOF (so Alpha's stdio session lifecycle matches it). On macOS
  it proxies to the installed `CuaDriver.app` daemon so Accessibility/Screen Recording
  grants keep the app identity; `--socket` selects an explicit endpoint.
- **Permission mode:** on Windows/Linux, set `CUA_DRIVER_PERMISSION_MODE` in the
  client `env` block. The default `standard` **allows input to every app**; `bounded`
  additionally requires `CUA_DRIVER_CAPABILITY_MANIFEST_FILE` and
  `CUA_DRIVER_CAPABILITY_MANIFEST_APPROVED`. Alpha ships `standard` explicitly in the
  example block so the knob is visible, and does **not** ship a capability manifest —
  a manifest Alpha never wrote would be a policy nobody reviewed.
  (<https://cua.ai/docs/how-to-guides/driver/connect-your-agent>, retrieved 2026-10-09.)
- **Optional skill:** `cua-driver skills install` teaches a shell-capable agent tool
  selection/element addressing conventions. Not required for MCP use; Alpha's own
  prompts already carry the addressing contract.
- **Optional `cua-mcp-filter` wrapper** exists upstream as an allow-list shim over the
  tool set. Alpha does not need it: `tool_search`/routing plus the governance gate
  already bound what the model can see and do.

---

## 4. Licensing — the part that is easy to get wrong

`trycua/cua` is not uniformly MIT. The repo's own `Licensing` section
(<https://github.com/trycua/cua>, retrieved 2026-10-09) says:

| Component | License | Use here |
| --- | --- | --- |
| **Cua Driver** (`libs/cua-driver`), the `cua` SDK/CLI, Lume, CUA-S1 source | **MIT** | ✅ the thing this integration uses |
| Cua Spaces apps, `cua-spacesd`, Keyvault, teleport, streaming client/codecs | **FSL-1.1-MIT** (source-available; each release becomes MIT two years later) | ❌ not used, not redistributed |
| `cua-perception` optional extension | **AGPL-3.0-only** (an OmniParser model artifact + Apache-2.0 PP-OCR + ONNX Runtime, signed per-release) | ❌ **do not install** — redistributing or serving it over a network can trigger AGPL source obligations, and Cua states it cannot relicense it |
| `cua-som` (optional package) | **AGPL-3.0-or-later** | ❌ do not install |
| Kasm (`libs/kasm`) | MIT | not used |

**Rule for this repo:** only the MIT driver surface is used, installed as an external
operator-side binary. No Cua source is vendored into this tree, no Cua artifact is
redistributed, and the two AGPL components stay uninstalled. If that ever changes,
this document changes with it.

---

## 5. The landscape (why not the others)

All rows retrieved 2026-10-09. "Shape" is the deciding column: Alpha already owns an
agent loop, so anything that ships *its own* loop is a second planner.

| Project | License | Shape | Verdict |
| --- | --- | --- | --- |
| **Cua Driver** (<https://github.com/trycua/cua>) | **MIT** (driver; see §4.1) | Driver only — no model, MCP/CLI/SDK | **Chosen.** Background input, cross-platform, ~50 tools, model-free |
| **UFO³ / UFO²** (<https://github.com/microsoft/UFO>) | **MIT** | Full agent framework; UFO² is a deep-Windows GUI+API agent, UFO³ adds multi-device DAG orchestration | Not chosen: ships its own ReAct loop and orchestrator (`Active Development`, README). Adopting it means a second planner, the analysis in `AGENT_TOOLING_IMPLEMENTATION_PLAN.md` §1.4 |
| **Agent-S** (<https://github.com/simular-ai/Agent-S>) | **Apache-2.0** | Autonomous GUI agent; best config leans on UI-TARS as a grounding model | Not chosen: a planner, and its default path wants a hosted grounding model |
| **OpenCUA** (<https://github.com/xlang-ai/OpenCUA>) | **MIT** (code, AgentNet dataset and model weights, per repo) | Research framework + 7B/32B/72B computer-use **models** and an annotation tool | Not chosen *as a driver*: valuable as a model/dataset line, not a Windows driver Alpha can call. (Weights are on Hugging Face; each card carries its own scope) |
| **UI-TARS / UI-TARS-desktop** (<https://github.com/bytedance/ui-tars>, <https://github.com/bytedance/ui-tars-desktop>) | **Apache-2.0** | GUI-agent desktop app driven by UI-TARS/Seed-VL models | Not chosen: a separate desktop application with its own model requirements |
| **OpenAdapt** (<https://github.com/OpenAdaptAI/OpenAdapt>) | **MIT** | Process-recording/replication (capture then replay) | Not chosen: capture-first workflow, not a live driver for an existing loop |
| **Browser Use** (<https://github.com/browser-use/browser-use>) | **MIT** | Browser-only autonomous agent | **Already integrated** (managed subprocess + `browser-use` MCP block). Complementary: DOM/CDP, not the OS desktop |
| **Skyvern**, **Open Interpreter** | AGPL-family | Browser/OS agents | Not chosen: AGPL obligations (see §4) |

Earlier, wider sweeps — dead/renamed products, benchmark-trust audit, the per-agent
tables — live in [RESEARCH_AUTONOMOUS_AGENTS.md](RESEARCH_AUTONOMOUS_AGENTS.md);
this document does not repeat them.

---

## 6. Integration into Alpha

### 6.1 What ships

A `cua-driver` stdio entry in `extensions_config.example.json`, next to `browser-use`,
**disabled by default**:

```json
"cua-driver": {
  "enabled": false,
  "type": "stdio",
  "command": "cua-driver",
  "args": ["mcp"],
  "env": { "CUA_DRIVER_PERMISSION_MODE": "standard" },
  "tool_name_prefix": true,
  "session_init_timeout": 120,
  "tool_call_timeout": 180,
  "routing": { "mode": "prefer", "priority": 55, "keywords": [ ... ] },
  "description": "..."
}
```

Contract-pinned by `backend/tests/test_cua_driver_mcp_integration.py`, which mirrors
`test_browser_use_mcp_integration.py`: exact command/args, disabled default,
prefixing, routing keywords, timeout floors, description honesty.

### 6.2 The five decisions and why

1. **`command: cua-driver`, not `uvx`/`npx`.** The driver is a standalone binary the
   operator installs system-wide; Alpha must not manage its install (contrast
   `browser-use`, whose `==` pins conflict with Alpha's lockfile — that conflict is
   what forced `uvx` there, and it does not apply here).
   *Consequence:* the **file** may express any command — a config file may express
   anything — but the **Gateway API** validates stdio launches against a bare-name
   allowlist defaulting to `{npx, uvx}`
   (`backend/app/gateway/routers/mcp.py::_allowed_stdio_commands`, documented in
   `backend/packages/harness/alpha/mcp/AGENTS.md`). Enabling this block through
   `PUT/PATCH /api/mcp/config` therefore needs
   `ALPHA_MCP_STDIO_COMMAND_ALLOWLIST=cua-driver` in the Gateway environment, or the
   operator edits `extensions_config.json` directly. Both paths are legitimate; the
   API path is deliberately the stricter one.
2. **Disabled by default.** Enabling grants a process that can move input into every
   application in the OS user session. That is an operator decision, taken on a
   machine where the binary is installed and permissions are granted — never a
   default-on capability.
3. **`tool_name_prefix: true`.** Discovered tools appear as `cua-driver_<tool>` so
   they cannot collide with the native `desktop_*` tools or with
   `browser_navigate`-family names. Two surfaces with near-identical names is exactly
   the confusion `test_browser_use_mcp_integration.py` pins against.
4. **Routing `mode: prefer`, desktop-shaped keywords.** Soft prompt hints, not a
   hard gate (`routing` never disables other tools — see the `Extensions
   Configuration` section of `backend/packages/harness/alpha/config/AGENTS.md`).
   Keywords are deliberately *desktop* words (`window`, `mouse`, `keyboard`,
   `application`, `foreground`, `desktop`, …) rather than `screenshot`/`click`, which
   `browser-use` already claims, so the two servers do not both claim the same turns.
5. **Timeouts above the 60s default.** First start spawns a daemon/runtime and may
   wait on permissions; `session_init_timeout: 120` bounds bring-up, and
   `tool_call_timeout: 180` covers multi-step gestures and `get_window_state`
   captures. A hung driver must still be bounded — `None` is not an option here.

### 6.3 Governance — read this before enabling

Alpha's governance classification is **provenance-based**
(`backend/packages/harness/alpha/tools/governance.py`): a tool that declares nothing
from a non-first-party source lands in the elevated class `WRITE` / `ASK` /
`UNKNOWN`-reversibility, with `provenance=elevated:untrusted_source` recorded.
MCP tools arrive exactly that way — they cannot self-classify.

So every `cua-driver_*` tool defaults to **ask-the-operator** with unknown
reversibility. That is the correct default for "click anywhere, type anywhere", and it
means:

- Nothing in §6.1 weakens any existing gate. Enabling the server adds tools that are
  *more* restricted out of the box than the native `desktop_*` tools (which carry
  first-party governance metadata).
- An operator who wants unattended desktop control must opt in explicitly through
  `config.yaml -> tool_governance` per tool name, which is an operator-granted
  authority, not something this document or the example config can grant.
- **`SentinelGuard` does not gate MCP-delivered input.** The sentinel wraps
  `alpha/computer_use/dispatcher.py`; a `cua-driver` click never passes through it.
  The guards on that path are (a) Alpha's confirmation policy above, (b) Cua Driver's
  own `CUA_DRIVER_PERMISSION_MODE`, and (c) the platform's permissions. Treating the
  panic-corner kill switch as if it covered this path would be a fabricated safety
  claim — it does not, and the runbook in §7 says so out loud.

### 6.4 What is deliberately not done

- **No new builtin tool, no new engine, no registry change.** This is a config entry
  plus a test, so `BUILTIN_TOOLS`, `contracts/feature_manifest.json` and every
  capability count in `README.md` / `llms.txt` / `docs/FAQ.md` are untouched. (A
  registry change would oblige regenerating the manifest *and* renumbering those
  documents in one change set — see the root `AGENTS.md` integration-health section.)
- **No vendored Cua code**, no pyproject dependency, no installer hook.
- **No AGPL extension** (§4), no capability manifest, no macOS `--socket` wiring
  (the example targets the Windows-first deployment this repo already documents).
- **No claim of runtime verification.** See §8.

---

## 7. Operator runbook (the part that would verify it)

Order matters; each step has a check.

1. **Install the driver** (Windows): `irm https://cua.ai/driver/install.ps1 | iex`,
   then `cua-driver --version` and `cua-driver doctor`.
2. **Confirm it can see the desktop:** `cua-driver call list_apps`. If this fails,
   no MCP wiring will help.
3. **Enable the server** — either copy the `cua-driver` block from
   `extensions_config.example.json` into your `extensions_config.json` and set
   `"enabled": true`, **or** set
   `ALPHA_MCP_STDIO_COMMAND_ALLOWLIST=cua-driver` on the Gateway and enable it
   through `PUT /api/mcp/config`. Restart is not required for non-task MCP servers
   (hot reload), but the driver binary must be on the Gateway's `PATH`.
4. **Confirm discovery:** `GET /api/mcp/config` should list the server enabled; a
   session should surface `cua-driver_*` tools.
5. **Make one bounded, observable call** and read the result back through a
   *separate* observation (`get_window_state` or Alpha's own `desktop_screenshot`),
   not through the action's own return value. A completed call is not a verified
   effect.
6. **Decide governance per tool** in `config.yaml -> tool_governance` if
   approval-per-click is too heavy for your use case — explicitly, per tool name.

If any step fails, the honest status is "not working", with the failing step named.

---

## 8. Honest limits

- **No runtime evidence in this worktree.** `cua-driver` was not installed or executed
  here; the tool counts, permission-mode names and install commands above are read
  from upstream docs retrieved 2026-10-09 and could drift. The test suite pins the
  *configuration contract*, not the driver's behaviour.
- **Platform columns differ per tool.** The 56-tool figure is the macOS reference
  page; Windows/Linux expose subsets. Do not quote a single tool count as portable.
- **`standard` permission mode allows input to every application.** That is upstream's
  default and it is deliberately visible in the example block; `bounded` needs a
  capability manifest Alpha does not ship.
- **Foreground/background behaviour is platform-dependent** ("when the app and
  platform support it" — upstream's own wording). Windows background delivery is not
  claimed here as measured.
- **No benchmark claim.** Nothing in this document asserts an OSWorld-style score for
  this integration; the benchmark-trust audit in
  [RESEARCH_AUTONOMOUS_AGENTS.md](RESEARCH_AUTONOMOUS_AGENTS.md) explains why
  vendor-run numbers are not treated as evidence.

## 9. Tests

- `backend/tests/test_cua_driver_mcp_integration.py` — the shipped block's contract:
  exact command/args, `enabled: false`, prefixing, routing keywords, timeout floors,
  `$`-style env expansion hygiene, and a description that names the governance
  reality and the sentinel gap rather than implying a safety net that does not exist.
- Existing coverage this document leans on and does not modify:
  `backend/tests/test_browser_use_mcp_integration.py` (the pattern),
  `backend/tests/test_docs_claim_honesty.py` and
  `backend/tests/test_docs_index.py` (this document's own index entry).
