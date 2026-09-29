# Coding Agents — Terminal-First and IDE-First: Live Landscape Survey and Alpha Gap Analysis

**Retrieval date for every external source in this document: 2026-09-29** unless a
different date is stated inline. Today is 2026-09-29; several systems in this
landscape changed ownership or product name *within the last four months*, and this
document deliberately prefers the newest primary source in each case and says what
it replaced.

Author: research subagent, `research/r1-coding` worktree. No product code was
changed. The only file added is this report.

---

## 1. Method

### 1.1 What I did

1. Read the repository's operating plan (`MULTI_AGENT_PLAN.md`) and both agent
   guides (`AGENTS.md`, `frontend/AGENTS.md`) before touching anything.
2. Fetched **primary sources** for every system: official documentation sites,
   official repositories, official changelogs, and peer-reviewed papers. Where a
   vendor publishes both an HTML and a `.md` variant I fetched the `.md` variant,
   because it is the machine-checkable one.
3. Read Alpha's source, not its docs. Concretely I opened:
   - `backend/packages/harness/alpha/tools/builtins/` (130 modules — full listing),
     `tools/tools.py` (the binding and gating site),
     `tools/selection.py`, `tools/builtins/tool_search_tool.py`.
   - `backend/packages/harness/alpha/agents/middlewares/` (57 modules — full
     listing) and the two composition sites: `agents/lead_agent/agent.py`
     (`build_middlewares`) and
     `agents/middlewares/tool_error_handling_middleware.py`
     (`_build_runtime_middlewares`, `build_lead_runtime_middlewares`,
     `build_subagent_runtime_middlewares`).
   - `agents/lead_agent/agent.py`, `agents/assembly_descriptor.py`.
   - `subagents/` (24 modules), `subagents/executor.py`,
     `subagents/acceptance_checks.py`, `subagents/registry.py`,
     `subagents/lifecycle.py`.
   - `sandbox/` (26 modules), `sandbox/security.py`, `sandbox/sandbox.py`,
     `sandbox/middleware.py`, `sandbox/tools.py`, `sandbox/worktrees.py`.
   - `skills/` (60 modules), `skills/types.py`.
   - `models/` (30 modules), `models/fallback.py`, `models/factory.py`,
     `models/effort_translation.py`, `models/cost_governor.py`.
   - `backend/app/gateway/routers/console.py`, `thread_runs.py` (SSE path),
     `subagents.py`, `mcp.py`, `skills.py`.
4. Every Alpha claim in sections 4 and 5 carries a `file:line`. Claims I could
   not verify by reading are marked **UNVERIFIED** and are not used to support a
   recommendation.

### 1.2 What I could not reach, and what I therefore did not claim

- **No live verification of any product.** I was forbidden from starting a stack
  and I ran none. There is **no** statement in this document about how Alpha
  performs at runtime. Every Alpha statement is a statement about *code*, not
  about *observed behaviour*.
- **I did not execute the OpenCode, Claude Code, or Codex CLIs.** All claims
  about them are documentation claims. Where I quote a source that is itself a
  marketing page, I label it marketing.
- **I did not read Alpha's own existing landscape document**
  (`docs/AGENT_LANDSCAPE_AND_ROADMAP.md`) before starting. That was deliberate:
  the assignment is a from-scratch live survey, and reading a prior synthesis
  first would have contaminated it. I discovered the file exists only because
  the docs-index generator requires a `FILE_OVERRIDES` entry. **I did not add
  one** — that is a product-code edit, and my constraints forbid it. See §7.
- **Vendor pages that are JS-rendered were not executed.** Where I needed one I
  used a primary source that is plain text (e.g. a GitHub raw README, a repo
  doc, or the arXiv HTML) and said so.
- **I did not verify pricing or benchmark numbers against primary sources for
  every vendor.** Where a number came from a vendor's own comparison page
  (Augment, Qodo) I say so and treat it as a claim, not a fact.

### 1.3 A note on how much the landscape moved recently

Four systems in the requested list are no longer what they were:

| Was | Is now | Source |
|---|---|---|
| Amazon Q Developer CLI (open source, MIT/Apache-2.0) | **Kiro CLI**, closed source | [aws/amazon-q-developer-cli README](https://raw.githubusercontent.com/aws/amazon-q-developer-cli/main/README.md) |
| Gemini CLI (open source, TypeScript) | **Antigravity CLI**, closed-source Go binary; Gemini CLI stops serving consumer tiers 2026-06-18 | [Google Developers Blog, 2026-05-19](https://developers.googleblog.com/en/an-important-update-transitioning-gemini-cli-to-antigravity-cli/) |
| Windsurf (independent IDE) | **Devin Desktop**; Cognition acquired Windsurf 2025-07-14 | [cognition.com/blog/windsurf](https://cognition.com/blog/windsurf) |
| Continue (open source, Apache-2.0, 34.3k stars) | **Acquired by Cursor**, product wound down, data deleted after 2026-07-15 | [continue.dev](https://www.continue.dev/) and [The New Stack, 2026-06-22](http://thenewstack.io/cursor-acquires-continue-coding) |

And one further: **Cursor (Anysphere) was itself acquired by SpaceX for $60B
all-stock on 2026-06-16** ([CNBC, 2026-06-16](https://www.cnbc.com/2026/06/16/spacex-spcx-cursor-acquisition-ipo.html)).
This is a real consolidation event, not a rounding error: an open-source coding
agent (Continue) was absorbed into a proprietary IDE that was absorbed into a
$2T public company in the same month.

**The pattern this reveals is the most important framing fact for the whole
document:** between February 2026 and September 2026 the terminal-agent category
consolidated hard in *both* directions. Amazon and Google both **closed** the
source of their flagship terminal agents. Cursor **bought and shut down** the
leading open-source alternative. The category's new entrants (Antigravity, Kiro,
Devin Desktop, Kiro Crew) are *products*, not *projects*.

---

## 2. Landscape table

Cells are deliberately short. Detail and sourcing are in §3.

| System | Architecture | Distinguishing capability | Verification story | Sandbox / permission | Extensibility | License |
|---|---|---|---|---|---|---|
| **OpenCode** | Primary + subagent loop, client-side; hidden `compaction`/`title`/`summary` system agents | Ordered permission **ruleset** (`allow`/`ask`/`deny` × action × resource, last match wins) applied identically to built-ins, custom tools and MCP tools | `doom_loop` permission fires when a tool call repeats 3× with identical input; `steps` cap forces a summarising final turn | **No OS sandbox.** Permissions are the whole model. `external_directory` + `.env` deny are the only hard walls; `--auto` approves everything not explicitly denied | MCP (local/remote, OAuth+DCR), `SKILL.md` skills, TypeScript custom tools, markdown agents | Open source; repo `anomalyco/opencode` |
| **Codex CLI** | Rust; single loop + `AGENTS.md`; Rust binary | **OS-native enforced sandbox** (Seatbelt / bubblewrap / Windows sandbox) crossed with a separate approval policy | Rules allow/deny/ask per command prefix outside the sandbox; `--full-auto` = `workspace-write` + `on-request` | Three modes (`read-only`/`workspace-write`/`danger-full-access`) × three approval policies; `writable_roots`; optional network domain allow-list. **Plus an `auto_review` reviewer agent that pre-approves escalation requests** | MCP server, skills, `AGENTS.md`, custom commands, hooks | Apache-2.0 |
| **Claude Code** | Single agent loop; Agent tool → subagent fan-out; Team Mode | **Hooks with four handler types** — `command`, `prompt` (LLM yes/no), **`agent` (spawns a subagent to verify)**, and HTTP — plus `isolation: worktree` per subagent | `StopFailure` hook, `PostToolUseFailure`, `retry: true` on `PreToolUse` denial, `PreCompact`/`PostCompact` | Permission mode + allow/ask/deny per tool; `PermissionDenied` hook; managed-settings policy can force hooks only | Plugins (skills/agents/hooks/MCP), skills, hooks, MCP, Agent SDK, marketplace | Proprietary product; SDK public |
| **Cursor** | Editor-first; Agent/Plan/Ask modes; Cloud Agents | **Cloud Agents + "Spaces"** that share context between local and cloud agents; Grok Bot; Cursor Review / merge queue | `--force`/`--yolo` for unattended; terminal agent inside the editor; PR review product | `chat.agent.sandbox.enabled`; allow/deny list | Plugins, rules, skills, **subagents**, hooks, MCP, SDK (TS/Python), CLI with ACP | Proprietary (SpaceX subsidiary) |
| **Windsurf / Devin Desktop** | Rebuilt as Devin Desktop (2026-06-02); "Devin Local" is a **from-scratch Rust rewrite** of Cascade, claimed 30% more token-efficient | **Agent Command Center**: a Kanban view over every local *and* cloud agent from one place; **Spaces** for shared context across sessions | Cascade replaced, not extended | Unknown — closed | ACP support means it hosts Codex, Claude, OpenCode, in-house agents | Proprietary (Cognition) |
| **Cline** | Plan/Act dual mode; Task class; subagent fan-out | **Checkpoints**: a Git-backed snapshot after *every* tool execution, with three restore granularities (files / task / both) — the precondition that makes auto-approve safe | Built on checkpoints: "checkpoints make auto-approve practical" | Every action requires approval; auto-approve with a read/command toggle; subagents are read-only | Plugins via `@cline/sdk`, MCP, skills, hooks, workflows, agents.yaml, headless CLI, ACP | Open source (Apache-2.0) |
| **Roo Code** | Mode-persona system; Boomerang/Orchestrator delegation | **Mode-level tool-group scoping** — Orchestrator has *no* direct tool access at all, only `new_task`; Architect can edit markdown only | Checkpoints; auto-approve list | Tool groups per mode + auto-approving actions list | Custom modes (YAML), MCP, custom instructions, rules | Open source |
| **Aider** | Architect/editor **two-model split**; git-native | **Repo map via PageRank over a tree-sitter symbol graph**, binary-searched to a `--map-tokens` budget (default 1k) | Git auto-commit; `apply_patch` per hunk; `--test-cmd`; the *architect/editor* split is itself a self-correction mechanism | `yolo` mode; no OS sandbox | Edit formats, conventions files, `/architect`; no first-class MCP in the core loop | Apache-2.0 |
| **Goose** | Rust, **MCP-native** — the agent has almost no tools of its own; everything is an extension | **Recipes**: a shareable session setup (prompts, enabled extensions, model, retry/validation) that deliberately excludes memory, keys and global settings | Recipe-level **retry logic and success validation config**; Developer UI can read linter/compiler output and self-correct | Per-extension permission prompts; `PAT` / session allow-lists | **MCP is the extension mechanism** — the whole design. Recipes, subagents, subrecipes | Open source (Apache-2.0), under AAIF |
| **OpenHands** | **Stateless, event-sourced** Agent; LLM → parse → ActionEvent → execute → ObservationEvent | **Event-sourcing as an architecture** with an explicit `Condenser` context policy and a `SecurityAnalyzer`; MIT, paper-published at MLSys 2026 | Tests run in the container; the paper reports 68.0% SWE-bench Verified with Sonnet 4 and a 15-day production study showing **61% fewer system-attributable failures** vs V0 | `WAITING_FOR_CONFIRMATION` status gates actions; container workspace | Skills (renamed from "microagents" in 2026), MCP, LLM-agnostic | MIT |
| **SWE-agent** | Single loop over a deliberately *narrow* ACI | **The ACI paper itself**: LM-friendly commands, a **linter gate on every edit** (edit is refused if the code doesn't parse), a 100-line file viewer, and "your command ran successfully and did not produce any output" on empty output | The linter-on-edit is the verification mechanism; the maintainers now **recommend `mini-swe-agent` instead** | Docker/Apptainer; no per-tool permission model | Tool bundles; mini-swe-agent, SWE-ReX, SWE-smith, sb-cli | MIT |
| **Devin** | Cloud sessions, parallel; SWE 1.5 / 1.7 model family | The **long-running asynchronous cloud session** as the product: you assign an issue and review a PR later; DeepWiki; multi-agent Missions | "Persona"-based eval: *Software Engineering, Product, Design, Scientific Research, Docs* — the honest number is **38% of SWE-bench tasks needing 1–2 steps**; 13.86% unassisted on the 2024 full set | `Droid Shield` (static+dynamic analysis), sandbox, org `maxAutonomyLevel` cap | Custom Droids, missions, integrations, enterprise deployment | Proprietary |
| **Zed** | Three agent paths: Zed Agent / **External Agents (ACP)** / Terminal Threads | **ACP host** — Zed invented the Agent Client Protocol and is now the reference client; External Agents run as separate processes over JSON-RPC | Checkpoints per thread; steer-queue interrupts at the next step boundary (Zed Agent only) | `agent.always_allow_tool_actions`; Agent Profiles gate which tools | Agent Profiles, Skills, Instructions, MCP, worktree isolation per thread | Editor: **GPL-3.0**; ACP: Apache-2.0 |
| **Gemini / Antigravity** | Gemini CLI ReAct loop → **replaced by Antigravity CLI** (Go, closed) sharing a harness with Antigravity 2.0 | **Jules' CI-failure fix loop**: "operates in a loop — fixing, committing, and resubmitting — so your PRs keep moving forward"; plus a **Planning Critic** agent that reviews all plans not requiring human intervention | Jules runs existing tests or writes new ones in a Cloud VM; Antigravity agent auto-compacts at ~135k tokens | `coreTools` / `excludeTools` allow/deny by tool+command; yolo mode | Skills, hooks, subagents, extensions→plugins, MCP, A2A remote subagents, remote sandbox | Gemini CLI: Apache-2.0 (source of record). **Antigravity CLI: closed source** |
| **Kiro (ex-Amazon Q)** | Q CLI rebranded; spec-driven development | **Spec-driven development as the shipped default**: prompts → requirements → architectural designs → sequenced tasks, then implemented by parallel agents; plus **property-based tests** ("catch the edge cases that pass unit tests but break in production") | Spec approval gate; Kiro Crew learns across sessions | Terminal command allow/deny list; IAM/SSO; cloud sessions run in a managed sandbox | Custom agents, hooks, "powers", MCP, AGENTS.md, Skills.md, **ACP** | **Closed source.** Amazon Q CLI was dual MIT/Apache-2.0 and is now unmaintained (security fixes only) |
| **Copilot** | VS Code agent mode; **coding agent** in a GitHub Actions container; SDK sub-agents; Fleet mode | **Fleet mode** + `.agent.md` custom agents with `handoffs` (a suggested *next* agent after each reply, with its own model) and a `target: vscode \| github-copilot` field so one file drives both surfaces | `@copilot` on a PR to iterate; `Code Review` product; separate "review your own custom agents" tooling | Ephemeral GitHub Actions env; one PR per task; cannot cross repos; a documented firewall/domain allow-list | `.agent.md` agents, skills, **plugins** (installable packages of agents+skills+hooks+integrations), hooks, MCP, SDK | Proprietary |
| **Factory Droid** | Normal / **Spec** / **Mission** modes + an **Autonomy Level** (Off/Low/Medium/High) | **Two orthogonal axes** — interaction mode *and* risk-scored autonomy, where every command and MCP tool has a low/medium/high risk level and the autonomy level is a threshold on it; Mission Mode runs a mission orchestrator with worker agents | Spec plan must be approved; Missions require High autonomy | Per-tool risk levels; **blocklist commands never run at any level**; Droid Shield; sandbox | Custom Droids, AGENTS.md, skills, MCP, hooks, droid-exec, BYOM/managed Droid Computers | Proprietary |
| **Qodo** | Ingest → graph+vector knowledge layer → multi-agent review | **AST chunking** (not token windows) into a graph DB + vector DB, and a `Relevance` scorer that ranks findings; PR-Agent is MIT and self-hostable | Qodo Cover executes generated regression tests; `Relevance` prunes noise | Zero data retention claim; on-prem K8s deployment available | MCP-first: everything surfaced through MCP | Proprietary product; **PR-Agent is MIT** |
| **Tabnine** | *Not deeply verified — see §7* | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED |
| **Sourcegraph Cody** | *Not deeply verified — see §7* | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED |
| **Augment** | **Cosmos** platform: triggers → agents → runtime/scheduling/isolation → **Context Engine** | **A persistent real-time index of the whole codebase, sold as a standalone MCP server** so *any* agent can use it; claims 33% lower token cost and a $920.70/run saving vs Opus 4.7 on SWE-bench Pro | Benchmark is Terminal-Bench 2.0 with a published cost × pass-rate scatter | Enterprise: VPC, CMEK, single-tenant, sandboxed execution, ZDR | Cosmos experts, expert registry, HITL escalation, context-services SDK, Auggie CLI | Proprietary; Context Services SDK open source |
| **Kilo Code** | *Not deeply verified — see §7* | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED |
| **Void** | *Not deeply verified — see §7* | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED |
| **PearAI** | *Not deeply verified — see §7* | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED |

---

## 3. Per-system notes

Each subsection answers the eight questions. Every substantive claim carries its
URL. Retrieval date 2026-09-29 throughout.

### 3.1 OpenCode

**1. What it does / architecture.** A single client-side agent loop with two agent
*classes*: `primary` (human-facing, switchable with Tab) and `subagent`
(invocable by the model via the Task tool, or by the human via `@name`). There
are also three hidden `primary` system agents — `compaction`, `title`, `summary`
— that "run automatically and are not selectable in the UI"
([agents docs](https://opencode.ai/docs/agents.md)). So context management is not
a middleware; it is a *model call to a hidden agent*.

**2. Distinguishing capability.** The permission **ruleset**. Unlike everyone
else's boolean/ternary per-tool gate, OpenCode's is an ordered array of
`{action, resource, effect}` where the resource is matched against the *concrete
input*:

```json
"permissions": [
  { "action": "shell", "resource": "git status *", "effect": "allow" },
  { "action": "read",   "resource": "*.env",           "effect": "deny"  }
]
```

"Rules are evaluated in order, and the **last matching rule wins**"
([permissions docs](https://opencode.ai/docs/permissions.md)). Two consequences
nobody else has: (a) a patch touching multiple files is denied if *any* resource
denies; (b) the same syntax works identically for built-in tools, custom tools,
and MCP tools, so `"mymcp_*": "deny"` disables a whole MCP server. The source
confirms the ordering and inheritance model directly
([`agent/subagent-permissions.ts`](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/agent/subagent-permissions.ts)):
a subagent inherits the parent's `external_directory` rules and all parent
`deny` rules, plus default `todowrite`/`task` denies — but *not* the parent's
`allow` rules. There is an open issue (#12566) reporting that a parent
configured `"*": "allow"` does not propagate permissiveness to subagents, so
subagent calls that evaluate to `ask` block forever in unattended mode. **That
bug is real and documented, and it is a warning about the design's complexity.**

**3. Context management.** Three separate concerns: hidden `compaction` agent
for long sessions; `step: N` per agent, which on hitting the limit injects a
special system prompt requiring "a summarization of its work and recommended
remaining tasks"; and child-session navigation (`session_child_first`,
`session_child_cycle`, `session_parent`) so the human can drop into a subagent's
session and back out
([agents docs](https://opencode.ai/docs/agents.md)).

**4. Verification / self-correction.** Thin, and honestly so. There is no test
runner integration and no evidence ledger. What exists is one loop guard:
`doom_loop` — "triggered when the same tool call repeats 3 times with identical
input" — which prompts a recovery message
([permissions docs](https://opencode.ai/docs/permissions.md)). Contrast with the
local-provider comparison in §3.3: a third-party table explicitly lists
OpenCode's OS-level sandbox as **"No (wrap in a container if you need it)"**
([agents.cli, undated, low-authority](https://www.agentscli.com/foundations/permissions/)).
I did not find a public benchmark or eval methodology for OpenCode. **UNVERIFIED:
whether OpenCode ships any eval harness.**

**5. Sandbox / permission.** No OS-level sandbox. Everything rests on the
permission ruleset. Defaults are permissive: "Most permissions default to
`allow`", with `doom_loop` and `external_directory` defaulting to `ask`, and
`read` defaulting to allow *except* `*.env` / `*.env.*` which are denied
([permissions docs](https://opencode.ai/docs/permissions.md)). `opencode --auto`
auto-approves everything not explicitly denied. The approval prompt offers
`once` / `always` / `reject`, and "the set of patterns that `always` would
approve is provided by the tool". **Approval outcomes are per-session only** —
there is no persistence layer, so `--auto` is the only unattended path.

**6. Extensibility.** Four mechanisms, all first-class: MCP (local via command
array, remote via URL, full OAuth with Dynamic Client Registration RFC 7591,
`opencode mcp auth|list|logout|debug`); `SKILL.md` skills discovered from six
directories including `.claude/skills/` and `.agents/skills/`, exposed through a
native `skill` tool that lists `<available_skills>` in the tool description
([skills docs](https://opencode.ai/docs/skills.md)); custom tools as
TypeScript/JavaScript files in `.opencode/tools/` using a `tool()` Zod helper
([custom-tools docs](https://opencode.ai/docs/custom-tools.md)); and markdown
agents in `~/.config/opencode/agents/` or `.opencode/agents/`. Skills carry the
same `allow`/`ask`/`deny` permission model as tools, per-agent overridable.

**7. Cost / latency.** No public benchmark. I did find a claim that the
compact-mode toggle "reduce[s] vertical spacing", which is UI, not cost. The MCP
docs do carry a genuinely useful honest warning: "MCP servers add to your
context, so you want to be careful with which ones you enable… Certain MCP
servers, like the GitHub MCP server, tend to add a lot of tokens and can easily
exceed the context limit"
([mcp-servers docs](https://opencode.ai/docs/mcp-servers.md)). **UNVERIFIED:
OpenCode publishes no latency or cost benchmark.**

**8. License.** Open source. The repository is
[`anomalyco/opencode`](https://github.com/anomalyco/opencode) (195K stars per
the site's own footer, 2026-09-02). It is simultaneously a **product**
(TUI/desktop/server) and a **framework** (`@opencode-ai/plugin` for custom
tools). A v2 exists at `v2.opencode.ai` with a **breaking config schema**:
`permissions` (array) replaces `permission`, `shell` replaces `bash`, `subagent`
replaces `task`, and `"doom_loop and lsp are not current V2 Core permission
actions"` ([v2 permissions](https://v2.opencode.ai/docs/permissions)). V1
actively reads v2 config fields for compatibility
([changelog v1.18.24, 2026-08-28](https://opencode.ai/changelog)).

### 3.2 OpenAI Codex CLI

**1. What it does / architecture.** Rust binary, single loop, `AGENTS.md` for
project instructions. The docs list a `skills.md` doc and `agents_md.md` in the
repo ([repo docs listing](https://github.com/openai/codex/tree/main/docs)).
Supports interactive TUI and non-interactive `exec`. Key CLI surface:
`--add-dir` (extra writable roots), `--ask-for-approval`, `--profile`,
`--dangerously-bypass-approvals-and-sandbox` (`--yolo`), `--oss`
([CLI reference](https://developers.openai.com/codex/cli/reference)).

**2. Distinguishing capability.** The cleanest **two-axis permission model**:
the *sandbox* defines technical boundaries (`read-only` / `workspace-write` /
`danger-full-access`), the *approval policy* defines when to stop
(`untrusted` / `on-request` / `never`) — "Sandboxing and approvals are different
controls that work together" ([sandboxing](https://developers.openai.com/codex/concepts/sandboxing)).
Two things on top of that no other agent has:

- **`approvals_reviewer = "auto_review"`** — "eligible approval prompts go to a
  reviewer agent before Codex runs the request", and critically, "Prompt-build,
  review-session, and parse failures **fail closed**. Timeouts are surfaced
  separately, but the action still does not run"
  ([agent approvals & security](https://learn.chatgpt.com/docs/agent-approvals-security)).
  An **agent that reviews the agent's own privilege escalations**, failing
  closed, is genuinely novel and directly comparable to Alpha's `agent` hooks
  and `FinishFirstVerifierMiddleware`.
- A **network proxy with a domain allow-list**:
  `features.network_proxy.domains = { "api.openai.com" = "allow", "example.com" = "deny" }`
  (same source). Per-domain network policy at the sandbox layer.

**3. Context management.** Context compaction; `[sandbox_workspace_write]`;
`summarization` — the docs mention the local file is overwritten by
compaction, which is how `rm summarization.txt` is the documented way to force
it ([config](https://developers.openai.com/codex/concepts/sandboxing), retrieved
2026-09-29; treat the exact key as **UNVERIFIED** because I read it in a
secondary rendering). The Antigravity successor publishes a concrete number —
automatic compaction "triggered at ~135k tokens"
([Gemini API Antigravity agent](https://ai.google.dev/gemini-api/docs/antigravity-agent)).
**UNVERIFIED: Codex CLI's exact compaction trigger threshold.**

**4. Verification / self-correction.** Codex does not claim to run your tests
itself. The verification mechanism is a **rules** system: "Rules let you allow,
prompt, or forbid command prefixes outside the sandbox", and the auto-review
agent above. There is a published eval: OpenAI ships a `Codex Security` plugin,
a `Codex Security` CLI, and a `Codex Security` cloud
([sandboxing nav](https://developers.openai.com/codex/concepts/sandboxing)) —
**UNVERIFIED whether this evaluates general code-change correctness or only
security findings.** Cognition's SWE-bench technical report notes Codex was one
of the systems they compared against, which is at least third-party signal.

**5. Sandbox / permission.** The strongest in the category and the only one I
found that is **OS-enforced by default** rather than advisory. macOS: Seatbelt,
out of the box. Linux/WSL2: `bubblewrap`, with a documented AppArmor
`bwrap-userns-restrict` workaround on Ubuntu 24.04 and a warning at startup when
missing. Windows: the native Windows sandbox under PowerShell, the Linux
implementation under WSL2. Defaults: `workspace-write` with **network access
off**, write limited to the active workspace
([sandboxing](https://developers.openai.com/codex/concepts/sandboxing);
[approvals](https://learn.chatgpt.com/docs/agent-approvals-security)). Note one
change worth recording: "Codex and ChatGPT Work no longer support `untrusted`
as a selectable approval policy."

**6. Extensibility.** MCP server, skills (`docs/skills.md`), slash commands
(`docs/slash_commands.md`), `AGENTS.md`, hooks (there is a
`--dangerously-bypass-hook-trust` flag and a persisted "hook trust" concept),
`config.toml` with named profiles, and a full SDK + App Server. **The v1→v2
direction is toward a client/server split**, which is architecturally the same
move as OpenCode's V2.

**7. Cost / latency.** Publishes pricing and model tiers, not agent benchmarks.
**UNVERIFIED: no Codex CLI agent-level benchmark methodology found.** The
model list on the developers site names GPT-5.4, GPT-5.3-Codex and reasoning
effort as user-selectable.

**8. License.** Apache-2.0, open source, actively maintained. Product *and*
framework (SDK, App Server, MCP Server, GitHub Action).

### 3.3 Claude Code

**1. What it does / architecture.** One agent loop; the Agent tool spawns
subagents with isolated context; "a lead agent coordinates the work, assigns
subtasks, and merges results". There is a **background-agents** view
("run several full sessions in parallel and watch them from one screen") and a
Team Mode, referenced from a first-party blog post — "A harness for every task:
dynamic workflows in Claude Code: how the Claude Code team uses dynamic
workflows to orchestrate many subagents at once"
([overview](https://docs.claude.com/en/docs/claude-code/overview)). Surfaces:
terminal, VS Code, JetBrains, desktop, web, mobile, Slack/Discord/Telegram
channels, and CI via GitHub Actions / GitLab CI-CD.

**2. Distinguishing capability.** **The hook system's four handler types.** The
hooks reference is explicit: "Prompt hooks (`type: "prompt"`): send a prompt to
a Claude model for single-turn evaluation. The model returns a yes/no decision as
JSON. **Agent hooks (`type: "agent"`): spawn a subagent that can use tools like
Read, Grep, and Glob to verify conditions before returning a decision**"
([hooks](https://code.claude.com/docs/en/hooks)). A general-purpose
*verification subagent invoked from a lifecycle hook* — and this is available to
skills and subagents in their own frontmatter, scoped to the component's
lifetime. Also genuinely distinguishing: `isolation: worktree` on a subagent, so
"A subagent with `isolation: worktree` runs its Bash and PowerShell commands
inside its worktree" ([sub-agents](https://code.claude.com/docs/en/sub-agents)).

**3. Context management.** Auto memory ("Claude also builds auto memory as it
works, saving learnings across sessions without you writing anything"),
`CLAUDE.md` at project root, and `AGENTS.md` read "on its own or alongside
`CLAUDE.md`" ([overview](https://docs.claude.com/en/docs/claude-code/overview)).
`PreCompact` / `PreCompact` hooks give the user a place to archive the full
transcript before summarization. The Agent SDK reports partial output when a
subagent hits `maxTurns` — "Claude Code marks the output in the Agent tool
result as partial, so Claude knows the run is unfinished"
([SDK subagents](https://code.claude.com/docs/en/agent-sdk/subagents)). That
partial-vs-complete distinction in the tool result is a small thing every other
agent gets wrong.

**4. Verification / self-correction.** The richest lifecycle surface I found.
Distinct events exist for the *failure* cases, not just success:
`PostToolUseFailure` (after a tool call fails), `StopFailure` ("the turn ends
with an API error instead of a normal stop"), `PermissionDenied` (fires when
"Auto mode denies a tool call, including denials without a classifier verdict";
the docs note "Claude Code ignores `retry: true` for no-verdict denials").
Critically: `PreToolUse` can return `{ "retry": true }` "to tell the model it may
retry the denied tool call"
([hooks](https://code.claude.com/docs/en/hooks)). The verification loop is
therefore *user-authored*: the framework provides the mechanism, the team writes
the check. Claude Code does not claim to run your test suite by default.

**5. Sandbox / permission.** Permission mode plus per-tool allow/ask/deny. A
third-party comparison table records **"OS-level sandbox: No"** for Claude Code
([agents.cli](https://www.agentscli.com/foundations/permissions/) — low
authority, but the docs consistently describe permissions rather than
enforcement, and the hooks docs are built around a *prompt* the user answers).
Enterprise `managed-settings` can force hooks and enable
`allowManagedHooksOnly`. Native Windows install works and falls back to
PowerShell as the shell tool if Git for Windows is absent
([install](https://docs.claude.com/en/docs/claude-code/overview)).

**6. Extensibility.** The most complete extension story found: **plugins**
(installable packages bundling slash commands, agents, skills, hooks and MCP
servers, with a marketplace), skills, hooks (command/prompt/agent/HTTP), MCP, the
Agent SDK in TypeScript and Python, and the Agent Client Protocol (Claude Code
is an ACP agent — Zed shipped "Claude Code: Now in Zed" 2025-10-02,
[zed.dev/acp](https://zed.dev/acp)). Plugin hooks get `${user_config.*}`
substitution **in exec form only**; "a shell-form plugin hook whose `command`
references `${user_config.*}` fails with an error instead of running" — an
honest sharp edge documented in the reference.

**7. Cost / latency.** No agent-level benchmark published. The SDK exposes
`maxTurns` and the partial-result marking described above, which is the cost
control. **UNVERIFIED: no published cost-per-task figure.**

**8. License.** Proprietary product. The Agent SDK and hooks surface are public
and documented, so it is a product with a framework surface, not an open project.
`claude-code --version` is a documented check; the CLI is distributed via
install script, Homebrew cask (`claude-code` stable / `claude-code@latest`), and
WinGet.

### 3.4 Cursor

**1. What it does / architecture.** Editor-first, but now with a real terminal:
`cursor.com/cli` ships `agent` with interactive mode, three modes (Agent / Plan /
Ask), non-interactive print mode with `--output-format text|json|stream-json`
([CLI overview](https://cursor.com/docs/cli/overview)). Cloud Agents, an Agents
Window, Agent Review, a Merge Queue, and Grok Bot round out the surface
([docs nav](https://cursor.com/docs)).

**2. Distinguishing capability.** **Cloud Agents + Spaces.** "Spaces, a new way
to share context between agents while grouping sessions, PRs, files, and
context" — no other vendor ships a *shared context object* between a local
editor session and a cloud agent. A second real differentiator: **Grok Bot**,
the only xAI-model-native surface among the IDE agents in this survey
([docs nav](https://cursor.com/docs), 2026-09-29).

**3. Context management.** The tokenizer surface in the docs sidebar shows
"Off Context: 0/200k" with a percentage — an explicit, *measured* context meter
rather than an estimate. Max Mode per model (200k default, 1M on several).
**UNVERIFIED: Cursor's exact compaction policy — the docs I reached describe the
meter, not the trigger.**

**4. Verification / self-correction.** A separate review product, not a loop
feature: **Cursor Review** (closed beta) with a Pull Request Inbox, Pull Request
Page, and Merge Queue. Unattended execution is explicit rather than emergent:
`--force`/`--yolo` is required to modify files in print mode ("Without
`--force`, changes are only proposed, not applied")
([headless CLI](https://cursor.com/docs/cli/headless)). **UNVERIFIED: whether
Agent runs your tests by default.**

**5. Sandbox / permission.** Per the same third-party table, **"Yes (Seatbelt on
macOS, Cursor v2.0+) — a layer on top of the run modes, not a mode of its own"**
([agents.cli](https://www.agentscli.com/foundations/permissions/)). **Low-authority
source; I could not verify the Seatbelt claim against Cursor's own docs, so treat
it as UNVERIFIED.**

**6. Extensibility.** Plugins, rules, skills, subagents, hooks, MCP, SDK
(TypeScript and Python), and a CLI with **ACP** mode. Cursor itself joined the
ACP registry and is live in JetBrains IDEs (2026-03-04,
[jetbrains.com/acp](http://jetbrains.com/acp)) — which is how it can be hosted in
*other people's* IDEs.

**7. Cost / latency.** Publishes a full model table with default context, max
context and capabilities
([models & pricing](https://cursor.com/docs)), and an on-screen "Off Context"
percentage. **UNVERIFIED: no agent benchmark.**

**8. License.** Proprietary. **SpaceX acquired Anysphere (Cursor) for $60B
all-stock, announced 2026-06-16, expected to close Q3 2026**
([CNBC](https://www.cnbc.com/2026/06/16/spacex-spcx-cursor-acquisition-ipo.html)).
It also acquired **Continue** and, earlier, Supermaven and Graphite
([The New Stack](http://thenewstack.io/cursor-acquires-continue-coding)).

### 3.5 Windsurf → Devin Desktop

**1. What it does / architecture.** Rebuilt. "We're building on the IDE
foundation of Windsurf to introduce the command center for managing all your
agents in one place" (2026-06-02,
[devin.ai/blog/windsurf-is-now-devin-desktop](https://devin.ai/blog/windsurf-is-now-devin-desktop)).
The local agent was replaced: "**Devin Local**, which is the successor to Cascade
as our primary local agent. The Cognition team has completely rewritten the
local agent from scratch in Rust… Devin Local is up to 30% more token efficient"
(same source). **So the honest 2026 answer is: the thing called "Windsurf" is a
Rust agent called Devin Local inside an IDE called Devin Desktop.**

**2. Distinguishing capability.** The **Agent Command Center** — "a single Kanban
view" over every local and cloud agent, with **Spaces** for shared context
between agents (same source). This is the most *managerial* surface in the
category: the differentiator is orchestration of heterogeneous agents, not any
single agent's capability.

**3. Context management.** The 30% token-efficiency claim is a vendor number for
the Rust rewrite. Spaces are the context-sharing mechanism. **UNVERIFIED: no
independent measurement of the 30% figure.**

**4. Verification / self-correction.** Devin (cloud) — see §3.11. Devin Desktop
inherits it. **UNVERIFIED: whether Devin Local runs tests by default.**

**5. Sandbox / permission.** **UNVERIFIED** — Devin Desktop docs were not
reachable in a form I could read without JS. Devin (cloud) has Droid Shield and
sandbox, but that is Cognition's other product.

**6. Extensibility.** **ACP is the hook.** "Devin Desktop launches today with
support for the Agent Client Protocol (ACP)… At launch, Devin Desktop supports
**Codex, Claude Agent, OpenCode**, and any other ACP-compatible agents —
including agents built by your team in-house" (same source). Third-party agents
"get the same interface as Devin: they show up in the Kanban view, run inside
Spaces, and share context with other agents." This is the most aggressive
"anyone's agent in my IDE" position taken by a commercial vendor.

**7. Cost / latency.** Windsurf moved from credits to quotas on 2026-03-19
(Pro $20/mo, Teams $40/seat/mo, Max $200/mo) with published message-count
estimates by model tier — e.g. Premium Plus on Max: "42-170 messages / day"
([devin.ai/blog/windsurf-pricing-plans](https://devin.ai/blog/windsurf-pricing-plans)).
This is a **quota** methodology, not a quality benchmark, and I do not think it
should be read as one.

**8. License.** Proprietary (Cognition). Cognition raised $400M at $10.2B in
September 2025 ([CNBC](https://www.cnbc.com/2025/09/08/cognition-valued-at-10point2-billion-two-months-after-windsurf-.html))
and has since shipped SWE 1.5 and SWE 1.7 models.

### 3.6 Cline

**1. What it does / architecture.** A `Task` class driving a plan/act loop, now
also a full CLI (`cline -p`, `--json`, `--acp`, `-i/--tui`), plus a
**coordinator/multi-agent team mode** where "a coordinator agent breaks the work
into subtasks and delegates to specialist agents, each with their own tools and
context. Team state persists across sessions"
([README](https://raw.githubusercontent.com/cline/cline/HEAD/README.md)).
Also: scheduled agents (`cline schedule create "PR summary" --cron …`), Slack
socket-mode connection, and a `@cline/sdk` (`new Agent({ tools: [...] })`,
`createTool`) for programmatic embedding.

**2. Distinguishing capability.** **Checkpoints**, and the reasoning attached to
them. "Every time Cline modifies a file or runs a command, it saves a snapshot of
your project files" with three restore granularities: *Restore Files*, *Restore
Task Only*, *Restore Files & Task*
([checkpoints](https://docs.cline.bot/core-workflows/checkpoints)). The
design argument is the interesting part: "Checkpoints make auto-approve
practical. Without checkpoints, it feels risky because Cline can make many
changes before you notice a problem. With checkpoints, you can let Cline work
autonomously and roll back if something goes wrong." That is the cleanest
statement of the *undo-log as the precondition for autonomy* argument I found,
and it is the reason Cline's auto-approve is not reckless.

**3. Context management.** Subagents each get "its own prompt and context
window and token budget", which "keeps the main agent's context clean"
([subagents](https://docs.cline.bot/features/subagents)). "Auto Compact" is a
listed feature. **UNVERIFIED: Cline's exact compaction trigger.**

**4. Verification / self-correction.** Two mechanisms, both structural.
Checkpoints (above). And the subagent capability *repertoire* is deliberately
read-only: subagents "Can read files, search code, list directories, run
read-only commands, and use skills" but "**Cannot write files, apply patches,
use the browser, access MCP servers, or perform web searches. They also cannot
spawn their own subagents**" — and commands run by subagents "are restricted to
read-only operations. Subagents will not run commands that modify files or system
state" (same source). The main agent's self-correction is described as watching
linter and compiler output: "It monitors linter and compiler errors as it works,
fixing issues like missing imports, type mismatches, and syntax errors before you
even see them" ([README](https://raw.githubusercontent.com/cline/cline/HEAD/README.md)).
That claim is a README claim; **I did not find a primary source describing the
mechanism, so treat the mechanism as UNVERIFIED while believing the claim is
plausible.**

**5. Sandbox / permission.** "Every action requires your explicit approval"
([home](https://docs.cline.bot/home)). Auto-approve is a named feature with
toggles (notably "Read project files"). Subagents "follow the Read project files
auto-approve permission" — a permission an *indirect* actor inherits explicitly.
**UNVERIFIED: whether Cline has any OS-level sandbox.** The docs I read never
mention one.

**6. Extensibility.** Plugins via `@cline/sdk` — "Using the SDK, register tools
and lifecycle hooks programmatically through the plugin system for logging,
auditing, policy enforcement, or adding domain-specific capabilities" (README) —
plus MCP, skills, hooks, workflows, `agents.yaml`, `.clineignore`, and
**ACP** for editor integration.

**7. Cost / latency.** **UNVERIFIED: no published benchmark or eval
methodology.**

**8. License.** Open source, Apache-2.0. Product *and* framework (SDK, plugins).

### 3.7 Roo Code

**1. What it does / architecture.** A **mode-persona** system rather than an
agent-per-task system: Code (default), Ask, Architect, Debug, and **Orchestrator**
(a.k.a. Boomerang). Boomerang Tasks: the parent task pauses, a subtask starts in
a different mode, and results are passed back as a `result` parameter to
`attempt_completion` ([boomerang tasks](https://docs.roocode.com/features/boomerang-tasks)).

**2. Distinguishing capability.** **Tool-group scoping at the mode level, taken
to its limit.** The docs answer the question directly: "Why can't Orchestrator
mode read files, write files, call MCPs, or run commands?" — because
**Orchestrator's tool access is *no direct tool access at all***; it has
`new_task` and nothing else
([using modes](https://docs.roocode.com/basic-usage/using-modes)). Architect is
limited to "read, mcp, and restricted edit (markdown files only)". A planner that
*physically cannot* act is a stronger guarantee than a prompt that asks it not
to. **Alpha's equivalent — `read_before_write`, plan mode, the `plan` agent's
`edit: deny` — is policy, not a missing tool.**

**3. Context management.** Boomerang's stated purpose is context hygiene: "This
prevents the parent task from becoming cluttered with the detailed execution
steps (like code diffs or file analysis results), allowing it to focus efficiently
on the high-level workflow and manage the overall process based on concise
summaries from completed subtasks." Modes are **sticky** — each mode remembers
its last-used model, and mode selection persists between sessions.

**4. Verification / self-correction.** Checkpoints are a documented feature
page. **UNVERIFIED: I did not read the checkpoint granularity or any
test-runner integration.** Boombang's `attempt_completion` result channel is the
primary verification surface.

**5. Sandbox / permission.** Per-mode tool groups plus an "Auto-Approving
Actions" list. **UNVERIFIED: no OS-level sandbox documented in the pages I read.**

**6. Extensibility.** Custom modes in YAML (slug, name, roleDefinition,
groups, customInstructions), MCP, custom instructions with **context-variable
interpolation** (`{{workspace}}`, `{{mode}}`, `{{language}}`, `{{shell}}`,
`{{operatingSystem}}`), and rules.

**7. Cost / latency.** **UNVERIFIED: no benchmark published.** Install count
(15.4k VS Code installs, 574.1k downloads per the docs site header, retrieved
2026-09-29) is adoption, not quality.

**8. License.** Open source (`RooCodeInc/Roo-Code`).

### 3.8 Aider

**1. What it does / architecture.** Single loop, git-native, with an explicit
**architect/editor two-model split**: "Aider will change your files. An
architect model will *propose* changes and an editor model will *translate* that
proposal into specific file edits"
([chat modes](https://aider.chat/docs/usage/modes.html)). The stated rationale
is model-capability-shaped: "Architect mode is especially useful with OpenAI's
o1 models, which are strong at reasoning but less capable at editing files.
Pairing an o1 architect with an editor model like GPT-4o or Sonnet will give the
best results."

**2. Distinguishing capability.** **The repository map.** Aider builds a
tree-sitter symbol graph over the whole repo and runs **PageRank** on it, with
per-symbol multipliers — ×10 for identifiers mentioned in the chat, ×10 more for
long snake/kebab/camel identifiers, ×0.1 for leading-underscore privates, ×0.1
for more than 5 definers, ×50 for referencers already in the chat — then
distributes each source node's rank across its out-edges by weight, with
`math.sqrt(num_refs)` damping high-frequency mentions
([`aider/repomap.py`](https://github.com/Aider-AI/aider/blob/main/aider/repomap.py),
retrieved 2026-09-29). A **binary search** then trims the ranked tag list to fit
`--map-tokens` (default 1k) within a 15% tolerance
([repomap](https://aider.chat/docs/repomap)). No other agent in this survey has
a public, inspectable, graph-theoretic context-selection algorithm.

**3. Context management.** The repo map *is* the context management, and it is
**static** — computed once, then binary-searched per turn. Aider has no
equivalent of a subagent; the repo map replaces fan-out with retrieval. Chat
modes (`code` / `ask` / `architect` / `help`) can be switched per-message or
stickily.

**4. Verification / self-correction.** Git is the mechanism: "Aider
automatically git commits changes with a sensible commit message" and "You can
easily diff, git manage, and undo AI changes"
([repo](https://github.com/aider-ai/aider)). The repo also ships `aider-swe-bench`
("Harness used to benchmark aider against SWE Bench benchmarks") and
`polyglot-benchmark`. **UNVERIFIED: I did not read a current published score.**

**5. Sandbox / permission.** `yolo` mode exists. **UNVERIFIED: no OS-level
sandbox** — the repo description and docs describe git-based safety, which is
reversibility, not enforcement.

**6. Extensibility.** Edit formats (`diff`, `diff-fenced`, `whole`,
`editor-diff`, `editor-whole`), a `conventions` repo of community convention
files, and aider-specific LLM wrappers. **MCP: UNVERIFIED — I did not find MCP
support documented in aider's current feature set.** If you rely on this, verify
before assuming.

**7. Cost / latency.** `--map-tokens` is an explicit, user-facing cost dial
(default 1024 tokens per request) and the docs state the map "expands
significantly at times, especially when no files have been added to the chat".
This is the most honest cost story of any agent here, because it is a number the
user sets. **UNVERIFIED: no latency benchmark.**

**8. License.** Apache-2.0. Product only (no plugin/SDK framework story found).
Repo shows 48,749 stars, 13,138 commits, last pushed 2026-05-22
([org listing](https://github.com/orgs/Aider-AI/repositories), retrieved
2026-09-29) — **which means Aider is in maintenance mode, not active
development.** That is itself a finding.

### 3.9 Goose

**1. What it does / architecture.** Rust, CLI and desktop, and the defining
architectural choice: **MCP-native**. The agent has almost no tools of its own;
capability comes from MCP extensions
([block.github.io/goose](https://block.github.io/goose/)). A "Recipe" is the
session unit: goals/purpose, suggested activities, enabled extensions and their
configuration, project context, model, and retry/success-validation config
([session recipes](https://goose-docs.ai/docs/guides/recipes/session-recipes/)).
Subagents "handle tasks in parallel — code review, research, file processing —
keeping the main conversation clean" (homepage, 2026-09-29).

**2. Distinguishing capability.** **Recipes with a deliberate, stated exclusion
list.** A recipe is shareable and reusable, and "To protect your privacy and
system integrity, goose excludes: global and local memory; API keys and
personal credentials; system-level goose settings" (session recipes, above).
A reusable-session format that *cannot* leak your memory is a design decision
few competitors have even articulated.

**3. Context management.** Recipes explicitly do **not** carry conversation
history — only initial setup. Combined with parallel subagents, the context
strategy is: keep sessions short, make them shareable, isolate the fan-out.

**4. Verification / self-correction.** Recipe-level **retry logic and success
validation configuration** is a first-class recipe field (session recipes).
Developer-facing surface: the Agent SDK guides, and the AWS blog's line about
"automatically debug issues" for the Q predecessor. **UNVERIFIED: I did not read
goose's retry/validation implementation, so I cannot say how many attempts it
makes or what stops a thrash loop.**

**5. Sandbox / permission.** Per-extension permission prompts; a user PAT and
session allow-lists. The homepage lists "Prompt injection detection, tool
permission controls" under Security
([homepage](https://block.github.io/goose/), 2026-09-29). **UNVERIFIED: no
documented OS-level sandbox.**

**6. Extensibility.** **MCP *is* the extension mechanism** — there is no second
system. A community extension registry, plus Recipes, subagents, and
**subrecipes** (a recipe invoking another recipe).

**7. Cost / latency.** **UNVERIFIED: no published benchmark.** The AWS blog on
the Q predecessor describes `/context show --expand` and a usage readout
("3020 of 200k tokens, or 1.51%"), which is a good transparency pattern but is
from a different product.

**8. License.** Open source, Apache-2.0. Now under **AAIF (Agentic AI
Foundation)**, copyright 2026 — a foundation rather than a vendor, which is a
different long-term governance story from every other OSS agent here.

### 3.10 OpenHands

**1. What it does / architecture.** This is the best-documented architecture in
the survey because it is **peer-reviewed**. The Agent component is "a
**stateless, event-driven** architecture" implementing "a reasoning-action loop
that drives autonomous task execution" with four responsibilities: the
reasoning-action loop, tool orchestration, **context management via
condensers**, and **security validation via a security analyzer**
([docs.openhands.dev/sdk/arch/agent](https://docs.openhands.dev/sdk/arch/agent)).
The step loop is: pending actions → execute → condenser → LLM query →
`ContextExceeded`? → parse response → create ActionEvents → confirmation check
→ execute → create ObservationEvents. Confirmation sets
`WAITING_FOR_CONFIRMATION`. Priority order is documented: "class Reason
primary / class Condense, Security secondary / class Tools tertiary."

**2. Distinguishing capability.** **Event sourcing as a first-class
architecture, with the overhead measured.** The MLSys 2026 paper
([arXiv:2511.03690v2](https://arxiv.org/html/2511.03690v2), submitted
2026-04-22) reports: "a 15-day production comparison shows V1 reduces
system-attributable failures by **61%** relative to V0, and event-sourcing
overhead is negligible", measured by "replay[ing] real payloads from **433
SWE-Bench Verified conversations (39,870 events)** through the production
LocalFileStore path". The `Condenser` is the context policy: the agent can
`Condense` (compact and return) or `UseRaw`.

**3. Context management.** Explicit and pluggable — `Condenser` is a swappable
component with three possible outcomes (`Condensation`, `View`, or fall through
to a raw query), plus a `ContextExceeded` early-exit that emits a request
instead of a provider 400.

**4. Verification / self-correction.** Tests run inside the container as normal
tool actions; the *benchmark* is the verification. The paper's V0-vs-V1 table is
the interesting honesty: "With Claude Sonnet 4, V0 and V1 achieve identical
performance (**68.0%**), confirming that the architectural redesign preserves
baseline agentic capability. The Sonnet 4.5 gain (64.6% → 72.8%) is attributed
to extended thinking support enabled by V1's architecture." They evaluated
**14 language models** (7 closed, 7 open-weights) across **5 benchmarks** —
SWE-Bench Verified, GAIA, SWE-Bench Multimodal, SWT-Bench, Commit0 — and report
SOTA on 3 of 5. This is the most rigorous public eval methodology in the survey.

**5. Sandbox / permission.** A **container workspace** (Docker, remote runtime
API, or Apptainer), and an explicit `SecurityAnalyzer` that "analyzes proposed
actions for safety before execution" — a pre-execution semantic safety check
distinct from a permission prompt. Actions requiring approval park the
conversation in `WAITING_FOR_CONFIRMATION`.

**6. Extensibility.** Skills — note the 2026 rename, "refactor: finish the
microagent→skill rename in the frontend" (OpenHands releases page), which
converges on the industry-wide `SKILL.md` convention. Plus MCP, an
`extensions` repo loaded at build time via `SKILLS_CATALOG`, and a
generated TypeScript client that is "the frontend's only sanctioned way to
reach the agent-server" ([repo AGENTS.md](https://github.com/OpenHands/OpenHands/blob/main/AGENTS.md)).

**7. Cost / latency.** The event-sourcing overhead table is the only
*measured* system-overhead number published by any agent in this survey. The
paper also points to a continuously-updated "OpenHands Index" for all 14 models'
results.

**8. License.** **MIT**, for both the SDK and the benchmark harness. Both
product and framework, and the framework half is the point.

### 3.11 SWE-agent

**1. What it does / architecture.** A single loop over a deliberately
constrained **Agent-Computer Interface**. The paper's core claim is
counterintuitive and is the reason to care: "in contrast to the Linux Shell's
granular, highly configurable action space, SWE-agent's ACI instead offers a
**small set of simple actions** for viewing, searching through and editing
files" ([arXiv:2405.15793v3](https://arxiv.org/html/2405.15793v3)).

**2. Distinguishing capability.** The **ACI design itself as the contribution**,
and the specific findings are actionable:
- "We add a **linter that runs when an edit command is issued, and do not let the
  edit command go through if the code isn't syntactically correct**."
- "a special-built file viewer… works best when displaying just **100 lines** in
  each turn."
- "a special-built full-directory string searching command… important for this
  tool to **succinctly list the matches** — we simply list each file that had at
  least one match. Showing the model more context about each match proved to be
  too confusing."
- "When commands have an empty output we return a message saying **'Your command
  ran successfully and did not produce any output.'**"
([swe-agent.com ACI](https://swe-agent.com/latest/background/aci)).

**3. Context management.** A dedicated `HistoryProcessor` compresses history
"to make the best use of the context window"
([architecture](https://swe-agent.com/latest/background/architecture)), plus
the 100-line viewer as a hard context-discipline rule.

**4. Verification / self-correction.** The **linter-on-edit gate is the
verification mechanism**, and it is a *precondition* gate rather than a
post-hoc check: the edit never enters the file if it does not parse. Combined
with the informative-error-message design, this is arguably the most
cost-effective self-correction mechanism in the entire survey — and it costs one
syntax check.

**5. Sandbox / permission.** Docker/Apptainer via **SWE-ReX**, with a
Deployment abstraction that starts local or remote (modal, aws)
([architecture](https://swe-agent.com/latest/background/architecture)). No
per-tool permission model; the container *is* the boundary.

**6. Extensibility.** Tool bundles, config YAML, templates, custom tools. **And
a succession**: the project now ships **SWE-ReX**, **SWE-smith**,
**mini-swe-agent**, and **sb-cli**, and the docs lead with "We now recommend
mini-swe-agent instead of SWE-agent: Same performance, much more simple &
flexible" (architecture page banner, 2026-09-29). The project has split into
four smaller purpose-built tools.

**7. Cost / latency.** Published: SWE-bench pass@1 **12.5%** (GPT-4 Turbo) and
HumanEvalFix **87.7%** (arXiv:2405.15793v3). Note this is a **2024 number with
a 2024 model**, and the field has moved far since. It is cited here as evidence
of *methodology*, not as a current competitive position.

**8. License.** MIT. Framework/research project, not a product.

### 3.12 Devin

**1. What it does / architecture.** Cloud sessions, SWE 1.5 / SWE 1.7 model
family, "Devin Cloud", DeepWiki, an API, and parallel sessions
([pricing](https://devin.ai/pricing), which lists concurrent-session limits of
"Up to 10" for Free/Pro and "Unlimited" for Max/Team/Enterprise). Mission Mode
runs "a mission orchestrator session with Mission Control. Mission workers use
their own configured model and autonomy settings"
([interaction modes](https://docs.factory.ai/autonomy-and-safety/specification-mode)
— note: that page is Factory's; for Devin see the Cognition blog index).

**2. Distinguishing capability.** The **asynchronous, asynchronous-review
workflow as the product** — assign an issue, review a PR later, and the
"Persona" eval that reports the *distribution* of task difficulty rather than a
single number. Cognition's own framing: "Today's coding benchmarks have
established that models can write correct code. We built a system that measures
the number of human-equivalent hours of Devin's [work]"
([cognition.com/blog](https://cognition.com/blog)).

**3. Context management.** DeepWiki — a precomputed, navigable wiki of the
repository, listed as a plan feature ([pricing](https://devin.ai/pricing)).
**UNVERIFIED: DeepWiki's construction mechanism and refresh cadence.**

**4. Verification / self-correction.** The single most honest number in the
whole survey, and I want to quote it rather than the flattering one. Cognition's
2024 SWE-bench technical report: "In SWE-bench, Devin successfully resolves
**13.86%** of issues, far exceeding the previous highest unassisted baseline of
1.96%." The 2026 framing is more conservative still: their own current
positioning measures how much of the work is 1–2 steps. **Take any vendor's
current coding-agent benchmark claim and ask for the step-count distribution.**

**5. Sandbox / permission.** Devin Cloud is an isolated managed environment
([approvals & security](https://learn.chatgpt.com/docs/agent-approvals-security),
which lists "Codex cloud: Runs in isolated OpenAI-managed containers" as the
comparable standard).

**6. Extensibility.** Integrations (GitHub/GitLab/Bitbucket, Jira, Linear,
Slack, Teams, custom git provider), Devin API, Devin Desktop with **ACP** to host
third-party agents. **UNVERIFIED: whether Devin has a first-class skill or plugin
mechanism comparable to Claude Code's.**

**7. Cost / latency.** Free $0 / Pro $20 / Max $200 / Teams $80+40-per-seat /
Enterprise. A dedicated post explains the pricing philosophy: "why we focus on
building, not on optimizing how to maximize the output from each request"
([cognition.com/blog](https://cognition.com/blog)). SWE-2 is claimed 64% cheaper
than a frontier model at a comparison point — **that figure comes from a
third-party analysis page ([cellcog.ai](https://cellcog.ai/blog/cognition-swe-2)),
not from Cognition, and I am marking it UNVERIFIED.**

**8. License.** Proprietary.

### 3.13 Zed

**1. What it does / architecture.** Three distinct **agent paths**, and being
explicit about the difference is unusual: Zed Agent (Zed's own, uses
Zed-configured models/tools/profiles), **External Agents** (ACP subprocesses
that own their own auth, model, tools, and config), and **Terminal Threads**
(a CLI/TUI in a terminal-backed tab)
([zed.dev/docs/ai/agents](https://github.com/zed-industries/zed/blob/main/docs/src/ai/agents.md)).
The Agent Panel runs multiple threads at once, "each working independently with
its own agent, context window, and conversation history", with a Threads
Sidebar grouped by project
([agent-panel](https://zed.dev/docs/ai/agent-panel)).

**2. Distinguishing capability.** **Zed wrote the Agent Client Protocol and is
now its reference client.** ACP is "an open standard that enables any agent to
integrate seamlessly with any editing environment… re-uses the JSON
representations used in MCP where possible, but includes custom types for useful
agentic coding UX elements, like displaying diffs"
([agentclientprotocol.com](https://agentclientprotocol.com/)). Local agents
are "sub-processes of the code editor, communicating via JSON-RPC over stdio";
remote agents over HTTP/WebSocket ("full support for remote agents is a work in
progress"). The ecosystem is real: JetBrains co-developed it, VS Code, Neovim,
Emacs, Obsidian, marimo and others are clients; the **ACP Agent Registry**
launched 2026-01-28/31 with Auggie CLI, Factory Droid, Gemini CLI, GitHub
Copilot, Mistral Vibe, **OpenCode**, and Qwen Code
([JetBrains blog, 2026-01-31](https://blog.jetbrains.com/ai/2026/01/acp-agent-registry)).

**3. Context management.** "Choose **New From Summary** to start a fresh Zed
Agent thread seeded with a summary of the current conversation — useful for
compacting long threads as you approach the context window limit" — a
*user-initiated* compaction, which is a different and arguably better UX than
silent truncation. Zed also displays live token consumption per thread.

**4. Verification / self-correction.** Checkpoints per thread, and a
deliberately well-reasoned detail: "The checkpoint button appears even if you
interrupt the thread midway through an edit, as this is likely a moment when
you've identified that the agent is not heading in the right direction." Plus
**Steer**: a queued message that "reach[es] the Zed Agent sooner—interrupting it
at its next step (usually between a tool call and a response) rather than waiting
for it to finish" (agent-panel). **Caveat the docs state themselves: "Steering is
only available for the Zed Agent, since Zed can't detect turn boundaries for
external agents."**

**5. Sandbox / permission.** `agent.always_allow_tool_actions` — "if turned to
`false`, will require you to give permission to any editing attempt as well as
tool calls coming from MCP servers". Agent Profiles gate which built-in and MCP
tools are available per thread. **UNVERIFIED: no OS-level sandbox.**

**6. Extensibility.** Agent Profiles, Skills, Instructions, MCP, Zed extensions.
And — the model — Zed **hosting other people's agents** over ACP while keeping
billing, legal terms and data handling clearly between the user and the agent
provider ([external-agents](https://zed.dev/docs/ai/external-agents)). A small,
honest trust boundary.

**7. Cost / latency.** Publishes ACP ecosystem session metrics ("weekly sessions
for the three most-used agents in Zed": Zed Agent, Claude Agent, Codex CLI —
[zed.dev/acp/editor/jetbrains](https://zed.dev/acp/editor/jetbrains)). That is
adoption telemetry, **not a quality benchmark**. No quality numbers published.

**8. License.** Editor **GPL-3.0**; ACP **Apache-2.0**. Zed is the rare case of
a copyleft product hosting a permissively-licensed protocol — and the protocol
is the more strategically valuable artifact.

### 3.14 Gemini / Antigravity

**1. What it does / architecture.** **Gemini CLI is in terminal decline and
Antigravity is the successor.** Google's own blog (2026-05-19):
"Gemini CLI proved the terminal could be an incredible interface for agentic
tasks, but your needs shifted. You now require **multiple agents communicating
with each other** to split up the work." Antigravity CLI is "built in Go… shares
the same agent harness as Antigravity 2.0", with "asynchronous workflows…
Antigravity CLI orchestrates multiple agents for complex tasks in the
background" ([Google Developers Blog](https://developers.googleblog.com/en/an-important-update-transitioning-gemini-cli-to-antigravity-cli/)).
Timeline: Gemini CLI and Code Assist IDE extensions stop serving individuals /
AI Pro / AI Ultra on **2026-06-18**; Gemini CLI remains for paid Gemini and
Gemini Enterprise Agent Platform API keys. Gemini CLI's own loop was a ReAct
loop with built-in tools and MCP servers
([geminicli.com/docs](https://geminicli.com/docs/)) with subagents ("specialized
agents that operate within your main Gemini CLI session… each has its own system
prompt and persona") and **remote subagents over the A2A protocol**
([remote subagents](https://geminicli.com/docs/core/remote-agents/)).

**2. Distinguishing capability.** **Jules' CI-failure fix loop, and the
Planning Critic.** From the Jules changelog: "Jules now automatically detects and
fixes CI failures on pull requests it creates. **Jules now operates in a loop —
fixing, committing, and resubmitting — so your PRs keep moving forward.**" And:
"**We've introduced a secondary agent—the Planning Critic—to review all plans
that do not require human intervention.**"
([jules.google/docs/changelog](https://jules.google/docs/changelog)). A dedicated
critic agent whose sole job is to review plans is a clean separation, and
scoping it to "plans that do not require human intervention" is a well-considered
boundary. Also distinctive: the **Antigravity agent** is exposed as a *managed
API agent* — "A single API call gives you an agent that reasons, executes code,
manages files, and browses the web inside your own secure Linux sandbox" via
the Interactions API ([ai.google.dev](https://ai.google.dev/gemini-api/docs/antigravity-agent)).

**3. Context management.** **The only published compaction threshold in the
survey**: "Context compaction: Automatic context compaction (triggered at
**~135k tokens**) to support long-running, multi-turn sessions without losing
context or hitting token limits" (Antigravity agent, same source). Gemini CLI
also added a `/memory inbox` command "for reviewing and patching skills extracted
during sessions"
([changelogs](https://geminicli.com/docs/changelogs/)).

**4. Verification / self-correction.** Jules: "**Test Suite** — Jules will run
existing tests, or create new ones" and "**Virtual Machine** — Jules clones your
code in a Cloud VM and verifies the changes work" ([jules.google](https://jules.google/)).
That is the strongest *product-level* verification claim in the survey: not
"the agent can run tests" but "the agent verifies your changes work before you
look at them." The fix-commit-resubmit loop then makes it self-correcting against
CI.

**5. Sandbox / permission.** `coreTools: ["TOOL_NAME (COMMAND)"]` and
`excludeTools` for per-tool and per-tool-command exclusion; yolo mode
([agent mode](https://docs.cloud.google.com/gemini/docs/codeassist/use-agentic-chat-pair-programmer)).
Cloud VM for Jules; managed Linux sandbox for the Antigravity agent. IAM/SSO for
enterprise. Note the honest limitation in the docs: "**There isn't an option to
undo changes made to resources outside your IDE in agent mode.**"

**6. Extensibility.** Agent Skills, Hooks, Subagents, Extensions (now Antigravity
plugins), MCP (including a public Gemini docs MCP server at
`https://gemini-api-docs-mcp.dev`), A2A for remote subagents, an SDK, IDE
extensions, and the Gemini Enterprise agent platform. Notably, Google publishes
**its own skills** for coding agents
([ai.google.dev/gemini-api/docs/coding-agents](https://ai.google.dev/gemini-api/docs/coding-agents))
with install instructions for Claude Code, Cursor, Antigravity and Gemini CLI —
i.e. it treats skills as a distribution channel for its own product.

**7. Cost / latency.** Antigravity agent is pay-as-you-go on the underlying
model tokens. Jules publishes concrete quotas: 15 tasks/day and 3 concurrent
(Pro), 100/day and 15 concurrent (Google AI Pro), 300/day and 60 concurrent
(Ultra) ([jules.google](https://jules.google/)). **Quota, not benchmark.**

**8. License.** Gemini CLI: Apache-2.0, and the *source* remains (with the
consumer service ended). **Antigravity CLI: closed-source Go binary** — stated
by a third-party analysis ([botmonster](https://botmonster.com/coding/gemini-cli-dead-migrate-antigravity-cli-2026));
the Google blog does not say "closed source" in the excerpt I read, so I mark
the *binary's* licence **UNVERIFIED** while treating the direction as established.

### 3.15 Kiro (ex-Amazon Q Developer)

**1. What it does / architecture.** Rebrand, not a rewrite. "Kiro turns your
prompts into **requirements, architectural designs, and sequenced tasks**, then
implements them with parallel agents. **Property-based tests** catch the edge
cases that pass unit tests but break in production. With **spec-driven
development**, you ship maintainable code that matches what you specified"
([kiro.dev](https://kiro.dev/)). The migration table is unusually explicit about
what Q never had: terminal command allow/deny list, steering files, hooks, custom
subagents/agent skills, and spec-driven development were all "Q IDE: –" and
"Kiro IDE/CLI: ✓" ([migrating from Q Developer](https://kiro.dev/docs/cli/migrating-from-q-developer/)).

**2. Distinguishing capability.** **Spec-driven development as the shipped
default, plus property-based tests.** Every other agent treats the spec as
something you write when you feel like it; Kiro makes the requirement →
design → task sequence the default shape of a session. The property-based-test
claim is the more interesting half and I did not find it documented in detail —
**UNVERIFIED beyond the marketing page.**

**3. Context management.** Steering files (`AGENTS.md`, `Skills.md` supported),
agent configuration reference, and cloud sessions that "run Kiro CLI in a
managed cloud sandbox instead of on your machine… disconnect while the agent
keeps working and resume later from any machine with `--resume-id`"
([CLI changelog 2.20.1, 2026-08-27](https://kiro.dev/changelog/cli/)).

**4. Verification / self-correction.** Spec approval as the gate, plus the
property-based-testing claim. **UNVERIFIED: no mechanics, no iteration count.**

**5. Sandbox / permission.** Terminal command allow/deny list; hooks; IAM and
SSO; governance and administration controls; cloud sessions in a managed
sandbox; IP indemnity. **UNVERIFIED: whether there is an OS-level sandbox on the
local CLI.** Worth noting the security record: **CVE-2026-9255**, "missing input
source validation in the tool authorization prompt could allow a local actor to
execute arbitrary tools, including shell commands, without user approval by
crafting content that is piped to kiro-cli via stdin", fixed in `kiro-cli`
1.28.0 ([AWS Security Bulletin 2026-035-AWS, 2026-05-22](https://aws.amazon.com/security/security-bulletins/2026-035-aws/)).
This is a genuinely instructive failure: an approval prompt that trusts its
input channel is not a boundary.

**6. Extensibility.** Custom agents, hooks (pre/post command), MCP, steering
files, "powers", **ACP** compatibility (in the ACP registry; also integrated
into JetBrains via AI Assistant), Kiro IDE (Open VSX extensions, themes, VS Code
settings), Kiro Crew (an open-source workspace, separately free and
self-hostable).

**7. Cost / latency.** **UNVERIFIED: no published benchmark.** "Kiro will fund
your usage until it does, up to $10M" appears on Cognition's blog — that is a
Cognition/Windsurf offer, not Kiro's, and I note it here only to avoid
mis-attribution.

**8. License.** **Closed source.** The predecessor
([`aws/amazon-q-developer-cli`](https://raw.githubusercontent.com/aws/amazon-q-developer-cli/main/README.md))
was dual MIT/Apache-2.0 and is now: "**no longer being actively maintained and
will only receive critical security fixes.** Amazon Q Developer CLI is now
available as Kiro CLI, a closed-source product."

### 3.16 GitHub Copilot

**1. What it does / architecture.** Three surfaces: VS Code **agent mode**
(interactive, in-editor), **coding agent** (autonomous, in its own ephemeral
GitHub Actions environment, opens exactly one PR per task), and the **Copilot
SDK** with custom-agent sub-orchestration and Fleet mode
([about coding agent](http://docs.github.com/en/copilot/concepts/agents/coding-agent/about-coding-agent);
[SDK custom agents](https://docs.github.com/en/copilot/how-tos/copilot-sdk/features/custom-agents)).

**2. Distinguishing capability.** **`.agent.md` as a single portable definition
that drives both surfaces**, via a `target` field:
`target: vscode | github-copilot`. Same file, IDE or cloud, no duplication
([custom agents in VS Code](https://code.visualstudio.com/docs/agent-customization/custom-agents)).
Three fields I did not see elsewhere: **`handoffs`** — "Optional list of
suggested next actions or prompts to transition between custom agents. Handoff
buttons appear as interactive suggestions after a chat response completes",
each with its own `handoffs.model`; **`user-invocable: false`** — hide from the
picker while still allowing subagent invocation; and
**`disable-model-invocation: true`** — prevent subagent invocation while keeping
it in the picker. That is a *two-dimensional* visibility control nobody else
exposes, and it is the correct one. Also: **plugins** as installable packages
of agents + skills + hooks + integrations, and an org/enterprise hierarchy
(`.github/agents` → org `.github` repo → enterprise `.github-private`).

**3. Context management.** Custom instructions stored in the repo; the coding
agent gets "its own ephemeral GitHub Actions environment" and, by default, context
limited to the repository it was started in. Agent mode builds the prompt from
"a summarized structure of the workspace (instead of the full codebase to
preserve tokens)" plus machine context and tool descriptions
([agent mode announcement, 2025-02-24](https://code.visualstudio.com/blogs/2025/02/24/introducing-copilot-agent-mode)).
That "summarized workspace structure" is the same idea as aider's repo map, done
by the vendor.

**4. Verification / self-correction.** `@copilot` on a PR to iterate; a
**Code Review** product across the PR lifecycle; and — notably — a documented
*testing-your-own-agents* workflow: "Testing and releasing custom agents in your
organization or enterprise… Ensure your custom agents are performant and
compliant before releasing them" ([docs.github.com/copilot](https://docs.github.com/en/copilot)).
Vendors shipping an agent-evaluation surface is a 2026 capability.

**5. Sandbox / permission.** Ephemeral GitHub Actions container. Documented
limitations are unusually candid: "**Copilot can only make changes in the
repository specified when you start a task**"; "**Copilot can only open one pull
request at a time**"; "**Copilot coding agent doesn't account for content
exclusions**… Copilot will not ignore these files"; "Copilot isn't able to
comply with certain rulesets… access to the agent will be blocked". Plus a
configurable firewall/domain allow-list and a developer-environment customizer.

**6. Extensibility.** `.agent.md` agents, skills, **plugins**, hooks, MCP
(both in the agent and per-agent via `mcp-servers`), the SDK (TypeScript and
.NET shown in the SDK docs), and built-in skills. Hooks are available for
"GitHub Copilot agents" generally.

**7. Cost / latency.** Premium-request metering: "Beginning June 4, 2025,
Copilot coding agent will use one premium request per model request the agent
makes" ([GitHub blog, 2025-05-19](https://github.blog/news-insights/product-news/github-copilot-meet-the-new-coding-agent/)).
**UNVERIFIED: no quality benchmark.**

**8. License.** Proprietary. Product plus a documented SDK.

### 3.17 Factory Droid

**1. What it does / architecture.** Two **orthogonal** axes, which is the whole
design: *interaction mode* (Normal / **Spec** / **Mission**) and *Autonomy
Level* (Off / Low / Medium / High)
([interaction modes](https://docs.factory.ai/autonomy-and-safety/specification-mode)).
Mission Mode "runs a mission orchestrator session with Mission Control. Mission
workers use their own configured model and autonomy settings." Also ships
**Droid Computers** — BYOM or Factory-provisioned long-lived machines, with
`droid computer port-forward` and SSH hardening (root login and password auth
disabled), plus relay mode for "a hard network-level boundary".

**2. Distinguishing capability.** **Risk-scored autonomy.** "Execute commands
and MCP tools have a risk level (`low`, `medium`, or `high`). Droid runs them
automatically when the risk is at or below your Autonomy Level." The rubric is
published: Medium = "reversible workspace changes | `npm install`, `pip install`,
`git commit`, `mv`, `cp`, build tooling"; High = "`docker compose up`, `git push`
if allowed, migrations, custom scripts" ([autonomy level](https://docs.factory.ai/autonomy-and-safety/auto-run)).
A *single scalar that gates every tool by its own assessed risk* is a cleaner
model than any per-tool allowlist, and it composes with MCP tools automatically.

**3. Context management.** Custom Droids (system prompt + pre-selected tool set
+ ideal model per Droid), `AGENTS.md`, skills, Mission Control for larger work.
**UNVERIFIED: Droid's compaction policy.**

**4. Verification / self-correction.** Spec Mode is a hard read-only gate: "Uses
read-only planning behavior, then calls `ExitSpecMode` to ask for approval", and
approving a spec offers "Proceed with implementation" *or* an autonomy level.
Mission Mode explicitly includes "validation". "Transparent review workflow for
every change" is the claim on the product page. **UNVERIFIED: no detail on
whether Droid runs your test suite.**

**5. Sandbox / permission.** **Droid Shield** (a named product surface, static +
dynamic analysis), a sandbox, **blocklist commands that "never run at any level
and have no approval prompt"**, denylist, org `maxAutonomyLevel` cap, and
`--skip-permissions-unsafe` ("unsafe: skips all permission checks; use only in
isolated sandboxes"). Notably, "Missions orchestration requires High autonomy or
`--skip-permissions-unsafe`" — the most dangerous feature is gated behind the
least-safe flag or the highest autonomy level.

**6. Extensibility.** Custom Droids, `AGENTS.md`, skills, MCP, hooks,
`droid exec` (headless), Droid SDK, IDE integrations, Jira/Notion/Slack/GitHub,
BYOK for custom models, and **ACP** (in the registry).

**7. Cost / latency.** `/cost` command for in-session cost tracking. **UNVERIFIED:
no published benchmark.**

**8. License.** Proprietary; SOC-2 with enterprise deployment options.

### 3.18 Qodo

**1. What it does / architecture.** A **three-layer knowledge architecture**,
not an agent loop per se
([platform architecture](https://docs.qodo.ai/qodo-platform-architecture)):
(1) **Ingest agents** — parsing agents for syntactic structure, plus **AST
chunking** "using ASTs rather than arbitrary token windows… preserves logical
boundaries across functions, classes, and modules"; (2) a **knowledge layer** of
graph DB (call graphs, dependency chains) + vector DB (embeddings) + PR/commit
history + auto-generated `.md` module summaries, with embeddings from
Qodo-7b and `text-embedding-3-large`; (3) a **multi-agent review system** with
specialised research agents (`deep-research`, `find-similar`, `deep-issue`,
`ask`).

**2. Distinguishing capability.** **Everything is surfaced through MCP** — "All
surfaced through an MCP interface, making Qodo's capabilities composable with
your existing agents, CI pipelines, and developer tools." Qodo is not competing
to be your agent; it is competing to be your agent's *knowledge layer*. And the
AST-chunking choice is a real, checkable architectural commitment that most
"semantic code search" marketing does not make.

**3. Context management.** The knowledge layer *is* the context. Cross-repo
context is an explicit product claim ("Surface breaking changes, dependency
conflicts, and understand repo relationships").

**4. Verification / self-correction.** **Qodo Cover** "analyzes code to pinpoint
gaps in test coverage, **validates test effectiveness and rolls back tests that
don't increase coverage**, generates and executes high-quality regression tests".
That last clause — *rolling back generated tests that do not increase coverage* —
is a self-correction loop on the agent's own output, and I did not see an
equivalent anywhere else in this survey. Also **Relevance**, which "prioritizes
findings", and Rule Miner, which generates enforcement rules from PR history.

**5. Sandbox / permission.** Zero-data-retention claim
([qodo.ai](https://qodo.ai/), vendor claim, 2026-09-01) and a documented
on-prem Kubernetes deployment. **UNVERIFIED: no agent-side OS sandbox.**

**6. Extensibility.** MCP-first, Git-provider apps (GitHub/GitLab/Bitbucket/Azure
DevOps), Slack, a CLI (`@qodo/command`) that can serve an agent as an HTTP API
or as an MCP service, and a centralized rule system.

**7. Cost / latency.** **UNVERIFIED: no published benchmark.**

**8. License.** Proprietary product. But **PR-Agent, the engine behind Qodo
Merge, is MIT and self-hostable** as a CLI, a GitHub Action, or a webhook server
([qodo-ai/pr-agent](https://github.com/qodo-ai/pr-agent), referenced from the
API-catalog page retrieved 2026-09-29). The open engine is a genuinely useful
starting point.

### 3.19 Augment Code / Cosmos

**1. What it does / architecture.** A **platform** (Cosmos) rather than an
agent: triggers → agents → an agent runtime (scheduling, isolation, sandboxes,
shared filesystem) → the **Context Engine**; plus Auggie CLI
([augmentcode.com](https://www.augmentcode.com/),
[docs intro](https://docs.augmentcode.com/introduction)).

**2. Distinguishing capability.** **The Context Engine is sold as a standalone
MCP server so any agent can use it** — "Augment's Context Engine is now available
for any AI…" ([blog, 2026-02-06](https://www.augmentcode.com/blog/context-engine-mcp-now-live)),
claiming "30–80% quality improvements across leading AI coding agents". This is
the Qodo strategy taken further: the vendor's product is *context as a service*,
and the agent is someone else's. The technical claim is a **persistent real-time
knowledge graph over the codebase** (claimed "1M+ files indexed") with semantic
retrieval, exposed over MCP and over a CLI (`ctxc index` / `ctxc search`) with
hash-based incremental re-indexing and local-FS or S3 state
([Context Connectors how-it-works](https://docs.augmentcode.com/context-services/context-connectors/how-it-works)).

**3. Context management.** The product *is* context. Their argument is that
agents that grep "don't know what they don't know… they find files but miss
architecture", and that most agents "re-send the same candidate files every turn
because they don't know which one matters".

**4. Verification / self-correction.** The published benchmark is the honest
part and the marketing part is separate. **Vendor claim:** a blind study of 500
agent-generated PRs against merged human code in the Elasticsearch repo (3.6M
Java LOC, 2,187 contributors), showing +12.4 overall, +18.2 code reuse, +12.4
best practice ([augmentcode.com/context-engine](http://www.augmentcode.com/context-engine)).
**This is a vendor-run study and I am treating the numbers as claims, not
findings.** Their *cost* benchmark is more falsifiable: Terminal-Bench 2.0 with
a published cost × pass-rate scatter, reporting on SWE-bench Pro vs Opus 4.7
"1.65B vs 3.54B" total tokens and **$920.70 saved per run at +0.4% pass rate**
— and the mechanism is stated honestly: "**Most agents re-send the same
candidate files every turn because they don't know which one matters**"
([augmentcode.com](https://www.augmentcode.com/)). That the saving is
*attributable to cache reads* (1.58B vs 3.42B) is a checkable claim.

**5. Sandbox / permission.** Enterprise controls: VPC deployment,
single-tenant instances, sandboxed agent execution, BYOK, CMEK, data residency,
SAML/OIDC/SCIM, granular RBAC, audit logs + SIEM, ZDR.

**6. Extensibility.** Cosmos experts, an **Expert Registry**, Human-in-the-Loop
with smart escalation, organization knowledge (shared memory), SLDC triggers,
Slack/GitHub/Jira/CI integrations, and a **Context Services SDK that is
open-source** ([docs.augmentcode.com/introduction](https://docs.augmentcode.com/introduction),
which advertises "An open-source library built on the Context Engine SDK").

**7. Cost / latency.** The best public cost story in the survey (§3.19.4).

**8. License.** Proprietary product; Context Services SDK open source.
**UNVERIFIED: the exact SDK licence — I read "open-source" on the docs home
page, not a LICENSE file.**

### 3.20 Systems I did not verify

The following were in my assignment. I reached search results for them but did
**not** read a primary source, and I am not going to fill the table from memory.
Every cell for them is **UNVERIFIED** and no finding in §4 or §6 rests on them:

- **Tabnine** — no primary source read. (Known to me as a long-running
  enterprise-focused product, but that is memory, not evidence.)
- **Sourcegraph Cody** — no primary source read.
- **Kilo Code** — no primary source read.
- **Void** — no primary source read.
- **PearAI** — no primary source read.
- **GitHub Copilot Workspace** specifically — I read the coding agent and agent
  mode docs, not a Workspace doc. **If Copilot Workspace has been folded into
  coding agent mode, I did not verify that.**

Two further items from the assignment list I resolved by substitution rather
than research: **Windsurf** is covered in §3.5 as Devin Desktop because that is
what it now is, and **Gemini Code Assist / Jules** is covered in §3.14.

---

## 4. Alpha gap analysis

All `file:line` references are to `backend/packages/harness/alpha/…` unless the
path is given in full. **PRESENT** means I read the code and it does the thing.
**PARTIAL** means it exists but is missing a named piece. **ABSENT** means I
searched the named subsystem and did not find it.

### 4.1 Gap table

| # | Capability | Competitors that have it | Alpha status | file:line | Effort | Real gap or design choice? |
|---|---|---|---|---|---|---|
| 1 | **OS-enforced sandbox** (Seatbelt / bubblewrap / Windows sandbox) crossed with an approval policy | Codex CLI | **PARTIAL** — Alpha has a *provider abstraction* (local, AIO, E2B, BoxLite, Tenki) and refuses host bash under `LocalSandboxProvider` unless `allow_host_bash: true`, but the local provider is a *host process*, not an enforced boundary | `sandbox/security.py:10-20`, `:35-45`; `sandbox/sandbox.py:44-64` | **L** | **Real gap**, but the right fix is not "add Seatbelt". Codex's insight is the two-axis split. Alpha already has the two axes — provider = boundary, authorization/guardrails = approval — they just aren't *crossed per-tool* the way Codex crosses them. |
| 2 | **Per-tool risk scoring** driving one autonomy scalar | Factory Droid | **ABSENT** | no risk-level concept in `sandbox/` or `authz/` | **M** | **Real gap, and cheap.** Alpha already has a tool allowlist and an authorization provider with roles. Adding a `risk: low\|medium\|high` per tool and a config scalar that gates on it is a config change plus one middleware check. |
| 3 | **Pre-execution semantic safety analysis** of a proposed action | OpenHands `SecurityAnalyzer` | **PARTIAL** — Alpha has `GuardrailMiddleware` + `SandboxAuditMiddleware` + `ReadBeforeWriteMiddleware`, which are *rule*-based, not *semantic* | `agents/middlewares/tool_error_handling_middleware.py:255-321` | **L** | **Deliberate design choice, and defensible.** Alpha's guards are deterministic and inspectable; a semantic analyzer is a second opinion that can be wrong in ways rules are not. Alpha's `guardrails.provider` hook (`tool_error_handling_middleware.py:277-297`) is exactly the extension point for one. Not a gap to close; a gap to be *able* to close. |
| 4 | **Undo/rollback as the precondition for autonomy** | Cline (checkpoints), Zed (checkpoints), Aider (git auto-commit) | **PARTIAL** — Alpha has `reversible_delete_tool`, `boulder_checkpoint_tool`, `worktrees.py` | `sandbox/worktrees.py:26-40`; `tools/builtins/reversible_delete_tool.py` | **M** | **Real gap in *ergonomics*.** Alpha has the primitives but I found no per-tool-step snapshot with a three-granularity restore UI. The primitives are scattered across tools rather than being one checkpoint layer. |
| 5 | **Per-subagent git worktree isolation** | Claude Code (`isolation: worktree`), Zed (worktree picker per thread) | **PARTIAL** — `WorktreeManager` exists | `sandbox/worktrees.py:26-40` | **M** | **Real gap in wiring.** I did not find the executor acquiring a worktree per subagent. `executor.py:1587-1588` sets a *sandbox lease* and *command scope* per subagent, not a filesystem isolation. |
| 6 | **An agent that reviews the agent's own privilege escalations**, failing closed | Codex `approvals_reviewer = "auto_review"` | **ABSENT** | no reviewer agent in `agents/middlewares/` | **M** | **Real gap, and the most valuable one on this list.** Alpha has the *pieces*: `FinishFirstVerifierMiddleware` already does `RemoveMessage` + `jump_to: "model"` with a budgeted retry, and `ClarificationMiddleware` already interrupts for human input. Wiring an automatic reviewer into the authorization escalation path reuses both mechanisms. |
| 7 | **Lifecycle hooks with four handler types** (shell / LLM-prompt / **agent** / HTTP) | Claude Code | **PARTIAL** — Alpha has `HooksBridgeMiddleware` with `PreToolUse`/`PostToolUse` | `agents/middlewares/tool_error_handling_middleware.py:341-344`; `hooks_bridge_middleware.py` (15 KB) | **M** | **Real gap in *kind*, not in presence.** Alpha's hooks are shell-shaped. The `agent` handler type — spawn a subagent to *verify* a condition — is a different capability class and Alpha's middleware stack can express it, but the hook contract does not offer it. |
| 8 | **Hook events for the failure cases** (`PostToolUseFailure`, `StopFailure`, `PermissionDenied`) | Claude Code | **ABSENT** | `hooks_bridge_middleware.py` — no failure events | **S** | **Real gap, and it is the cheapest item on this list.** Alpha's `ToolErrorHandlingMiddleware` converts every exception into a `ToolMessage` and *hides the failure from the hook layer*. A hook that only fires on success cannot write a linter-after-edit. |
| 9 | **Return-to-model on permission denial** (`{"retry": true}`) | Claude Code | **ABSENT** | `agents/middlewares/clarification_middleware.py` handles clarification, not denial | **M** | **Real gap.** Today a denied tool call returns a denial `ToolMessage` and the model must infer what to do. An explicit "you may retry with a different approach" channel is different information. |
| 10 | **A graph-ranked repository map as the default context** | Aider (PageRank over tree-sitter graph), Augment (persistent index), Copilot (vendor-built workspace summary) | **PARTIAL** — Alpha has `repo_twin_tool`, `generate_repo_map`, `program_slicing_tool`, `ast_grep_tool` | `tools/builtins/repo_twin_tool.py`, `generate_repo_map` (imported at `tools/tools.py`) | **L** | **Design choice, partly.** Alpha's deferred-tool catalog (`tool_search_tool.py:26-46`) solves the *tool*-schema context problem, not the *code* context problem. `generate_repo_map` exists; whether it is on the hot path and how it ranks is UNVERIFIED — I did not read its body. |
| 11 | **Persistent cross-file semantic index** | Augment Context Engine, Qodo knowledge layer, Sourcegraph | **ABSENT** | — | **L** | **Deliberate design choice.** This is a *service*, not a harness component, and building it would be building a company. Alpha's answer should be to *consume* one (Augment's Context Engine is already an MCP server). |
| 12 | **Deferred tool-schema loading (progressive disclosure)** | OpenCode's own docs warn MCP tools flood context; Copilot gates with `target` | **PRESENT, and better than most** | `tools/builtins/tool_search_tool.py:26-46` (`catalog_tool_search` / `describe` / `call`); `agents/middlewares/deferred_tool_filter_middleware.py`; `mcp_routing_middleware.py`; asserted ordering at `lead_agent/agent.py:725-727` | — | **Not a gap. Alpha's three-step search→describe→call with a catalog hash and a promotion audit is more disciplined than anything in the survey.** |
| 13 | **Per-tool input-pattern permission rules with last-match-wins** | OpenCode | **PARTIAL** — Alpha has per-tool allow/deny plus role-based authorization, but not OpenCode's ordered `{action, resource, effect}` array matched against concrete tool input | `agents/middlewares/tool_error_handling_middleware.py:255-274` (`GuardrailMiddleware` + `GuardrailAuthorizationAdapter`) | **M** | **Real gap, but borrow carefully.** OpenCode's own issue #12566 shows the complexity has already produced a real bug where subagents block forever unattended. Alpha should take the *idea* (resource-scoped rules) and not the *mechanism*. |
| 14 | **Two-dimensional agent visibility** (hide from picker but allow subagent invocation, *and separately* block subagent invocation) | Copilot (`user-invocable`, `disable-model-invocation`) | **PARTIAL** — Alpha has `available_skills` and `allowed_subagents` allowlists | `lead_agent/agent.py:840-866`; `subagents/registry.py:178-224` | **S** | **Real gap, small.** Two independent booleans instead of two entangled ones. |
| 15 | **Plan/spec mode as a *toolless* planner** | Roo Code (Orchestrator has no tools at all), Cline Plan, Factory Spec, Cursor Plan, Kiro spec-driven | **PARTIAL** — Alpha has `is_plan_mode` gating `TodoMiddleware` | `lead_agent/agent.py:639-643` | **M** | **Real gap in *strength*.** Alpha's plan mode is a todo-list mode, not a mode that *removes* the write tools from the model's binding. Roo Code's answer — the planner physically has no write tool — is strictly stronger and is ~1 day of work. |
| 16 | **A dedicated plan-critic agent** | Jules (Planning Critic reviews all plans not requiring human intervention) | **PARTIAL** — Alpha has `alpha.critic` with `AgentFinishedCritic` and `EmptyPatchCritic` on *terminal* messages | `lead_agent/agent.py:461-481`, `:785`; `finish_first_verifier_middleware.py` | **M** | **Real gap in *scope*.** Alpha verifies the *completion* claim; nobody verifies the *plan*. A plan critic that runs before execution is a different and cheap addition given `alpha.critic` already exists. |
| 17 | **Verification evidence anchored to a specific recorded execution** | (Alpha's own standard) | **PRESENT, and the strongest thing Alpha has** | `subagents/acceptance_checks.py:32-45` — `tests_passed:<command>` must anchor to a matching bash execution with `status=success` **and** a test-summary shape in its output tail; `:103-120` recognises pytest/jest/cargo/go/maven shapes and **zero-test evidence vetoes a pass**; `:99-101` states an output carrying neither shape is UNVERIFIED; `:44-45` anything undecidable renders UNVERIFIED, never a silent pass | — | **Not a gap. No surveyed competitor has anything close.** See §5. |
| 18 | **Budgeted critic retry that cannot loop** | (Alpha's own) | **PRESENT** | `finish_first_verifier_middleware.py:19-20` — "The retry is budgeted once per run so a critic can never create a loop"; `:30-34` — a critic that raises *withholds approval* rather than approving by accident; `TerminalResponseMiddleware` uses the same `RemoveMessage` + `jump_to: "model"` mechanism (`lead_agent/agent.py:769-772`) | — | **Not a gap.** |
| 19 | **Loop / thrash guards** | OpenCode (`doom_loop` at 3 identical calls) | **PRESENT, and denser** | `loop_detection_middleware.py:91-96` — identical-call warn at 3 / hard stop at 5 over a 20-call window, **plus** per-tool-type frequency warn at 30 / hard stop at 50 in a decaying window, plus per-tool overrides; mirrored into the subagent chain at `tool_error_handling_middleware.py:484-488` | — | **Not a gap.** Alpha's is strictly more granular than OpenCode's single rule. |
| 20 | **Per-run token budget with a lead-visible stop reason** | (Alpha's own) | **PRESENT, and applied where the bug was** | `lead_agent/agent.py:754-759`; subagent budget at `tool_error_handling_middleware.py:508-516` with a comment citing the "reported 4.4M-token burn" as the motivating defect, and `_consume_stop_reason` marking the result `token_capped` (`subagents/executor.py:1159`) | — | **Not a gap.** |
| 21 | **An explicit test-runner integration** | Cline (watch linter/compiler output), Jules (runs or writes tests in a Cloud VM), Goose (recipe validation), Qodo Cover (generates and executes regression tests) | **PARTIAL** — `auto_test_and_repair` and `reproduce_and_verify` exist and are named as `_VERIFY_TOOLS` | `finish_first_verifier_middleware.py:89-96` | **M** | **Real gap in *depth*, not existence.** Alpha *has* the tool and evidence binding; what I did not find is a loop that says "you wrote code, run the tests, read the failure, fix it, run again, up to N". `FinishFirstVerifierMiddleware` only *notices* and *asks*. Cline and Jules close the loop. |
| 22 | **Cross-process / cross-agent native protocol** (ACP) | Zed, JetBrains, Cline, Cursor, Factory Droid, Kiro, OpenCode, Gemini CLI, Devin Desktop | **PARTIAL** — Alpha has an **A2A** tool, not ACP | `tools/builtins/a2a_tool.py`; `routers/a2a.py` | **L** | **Strategic, not a real capability gap.** Alpha's Alpha-to-Alpha network is a *peer* protocol; ACP is an *editor-hosting* protocol. They are orthogonal and both are worth having. See recommendation 2. |
| 23 | **Multi-provider reasoning-effort ladder with a fail-closed clamp** | (Alpha's own) | **PRESENT, and unusually correct** | `models/effort_translation.py:76-89` — per-style ladder tables; `:91-101` legacy Anthropic budget ladder; `:32-37` the invariant "never send a rung the resolved ladder cannot express… never disable thinking at a rung that forbids it"; `:244` warns rather than clamping `none`→`low` at Anthropic | — | **Not a gap.** |
| 24 | **Model fallback chain with per-thread attribution** | (Alpha's own) | **PRESENT** | `models/fallback.py:367` `FallbackChatModel`, `_EVENT_HISTORY = 20` bounded per thread, `get_last_effective_model()`, `get_last_failover_events()`, `get_last_credit_exhausted_models()`; chain resolution at `models/factory.py:244` | — | **Not a gap.** |
| 25 | **Cost attribution and circuit breaker** | (Alpha's own) | **PRESENT** | `models/cost_governor.py:153` `CostGovernor`, `:53` `CircuitBreakerTrippedError`, `:424` `UsageAttribution`, `:444` `usage_attribution(project_id, bot_name)` | — | **Not a gap.** |
| 26 | **Published agent benchmark / eval methodology** | OpenHands (14 models × 5 benchmarks, MLSys paper), Aider (`aider-swe-bench`, `polyglot-benchmark`), Qodo (Relevance), Augment (Terminal-Bench 2.0 cost×pass), Copilot (agent testing workflow) | **PARTIAL** — `autonomous_benchmark_tool.py` and `evaluation_benchmark_tool.py` exist as *agent-callable* tools | `tools/builtins/autonomous_benchmark_tool.py`, `evaluation_benchmark_tool.py` | **M** | **Real gap, and a reputational one.** Alpha has benchmark *tools the agent can run*; it has no **published, reproducible benchmark of itself**. Given the repo's own `docs/PRODUCTION_READINESS_INVENTORY.md` honesty contract, this is the one place where the honesty rules have nothing to point at. |
| 27 | **Plugin marketplace / installable bundles** | Claude Code (plugins + marketplace), Copilot (plugins), Cline (plugins) | **PARTIAL** — Alpha has an operator-controlled `plugins:` list and `alpha extensions install` | `examples/alpha-extension-example/`; `backend/packages/harness/alpha/extensions/` | **L** | **Deliberate design choice, and I think correct.** Alpha's list is deliberately out of the API-writable config because "that list causes code to be imported". Claude Code's marketplace does the same thing less explicitly. Alpha's is the more honest design; the missing piece is *distribution*, not *mechanism*. |
| 28 | **Hook/user-defined tool contributions without Gateway restart** | Cline, Copilot, Factory | **ABSENT by design** | every extension mutation "requires a Gateway restart" (root `AGENTS.md`) | — | **Deliberate design choice.** Correct. Do not change. |
| 29 | **Subagent context isolation** | Every agent with subagents | **PRESENT** | `subagents/executor.py:1402+` builds a fresh state and a fresh `SubagentExecutor` per task; a *fresh middleware instance* per task run (documented at `tool_error_handling_middleware.py:496-501`) so parallel subagents cannot cross-contaminate token-budget state despite sharing `thread_id`/`run_id` | — | **Not a gap**, and the reasoning is unusually well-documented. |
| 30 | **Skill mechanism with progressive disclosure + declared secrets** | OpenCode, Claude Code, Copilot, Gemini/Antigravity | **PRESENT, and ahead** | `skills/types.py:83-105` — `allowed_tools`, `required_secrets: tuple[SecretRequirement]`, `secrets_autonomous`; secrets injected as **request-scoped env** into the skill subprocess, never into the prompt, tool args, or the command string (`sandbox/sandbox.py:80-94`); `_is_disabled_skill_path` and skill-path validators in `sandbox/tools.py:226-316` | — | **Not a gap.** The `secrets-autonomous` flag — a secret that binds only on explicit `/skill` activation, not on a model-initiated load — is more careful than anything in the survey. |
| 31 | **A tool-selection / skill-selection *learning* signal** | (Alpha's own) | **PRESENT** | `tools/selection.py:86-123` `_emit_selection` records the *candidate set and scores*, not just the winner, on the argument that "which tool *ran* is already in the tool-call event, and 'which tools were on the table, and why this one won' is the only thing a self-improving system can learn from a selection"; `:278-292` `interleave` guarantees a System One miss can never regress a literal name match | — | **Not a gap.** No competitor instruments *why a tool was chosen*. |
| 32 | **Sealed untrusted-content handling on both entry points** | (Alpha's own) | **PRESENT** | `tool_error_handling_middleware.py:197-210` — `InputSanitizationMiddleware` (outermost), then `ToolOutputBudgetMiddleware`, then `ToolResultSanitizationMiddleware` **inner** so remote web_fetch/web_search output is neutralized *before* truncation | — | **Not a gap.** The ordering is deliberate and commented. |
| 33 | **Observability of a declined credit/quota** | (Alpha's own) | **PRESENT** | `models/fallback.py:212` `is_credit_exhausted_error`, `:300` `CreditExhaustedError`; the fallback docstring records that per-call kwargs are deliberately *not* forwarded to members "because an unknown kwarg would corrupt the provider payload" | — | **Not a gap.** |
| 34 | **Console/SSE operator surface** | (Alpha-specific) | **PRESENT** | `routers/console.py:310-317` `/console/stats`, `:381-388` `/console/runs`, `:452-459` `/console/usage`, `:550-556` `/console/insights`; SSE at `routers/thread_runs.py:1033-1073` (`stream_run`), `:1378-1392` (`join_run`), `:1494+` (`stream_existing_run`) | — | **Not a gap.** |
| 35 | **Named, configurable prompt-level turn cap** | OpenCode `steps`, Claude Code `maxTurns`, Alpha subagent `max_turns` | **PARTIAL** | `subagents/executor.py:93` `resolve_graph_recursion_limit`, `:1526` recursion limit translated from `max_turns` | **S** | **Small real gap.** Alpha translates super-steps→turns correctly, but the *lead* has no equivalent user-facing `steps` cap; it has token budget and loop detection, which are different guards. |

### 4.2 Summary of the gap list

Real gaps, by size:
- **L (large):** OS-enforced sandbox (1), persistent cross-file index (11 — but
  the honest answer is *consume one, don't build one*), ACP (22), plugin
  distribution (27 — design choice, listed for completeness).
- **M (medium):** risk-scored autonomy (2), checkpoint ergonomics (4), worktree
  isolation wiring (5), escalation reviewer (6), agent-type hooks (7),
  denial-retry channel (9), resource-scoped permissions (13), toolless plan mode
  (15), plan critic (16), test-loop closure (21), ACP (22), published benchmark
  (26).
- **S (small):** failure events in hooks (8), two-dimensional agent visibility
  (14), lead turn cap (35).

The **S** items are the highest value-per-effort in the entire report. Items 8,
14, and 35 are each a small, well-understood change with a clear competitor
demanding it.

---

## 5. What Alpha already does that competitors do NOT

This section is as important as §4. Each claim is a `file:line` I read.

**5.1 Acceptance criteria that cannot be self-reported as passing.**
`subagents/acceptance_checks.py:32-45` — the `tests_passed:<command>` criterion
"must anchor to a *specific recorded execution* — a matching bash execution …
with `status=success` and a test-summary shape in its output tail — not merely to
some successful call", and matching "accounts for shell command structure
(operator-separated segments, executables, arguments), so an unrelated command
that merely mentions the criterion string (e.g. inside an `echo` argument or a
comment) cannot anchor the leaf". Then `:103-120` recognises five real test-runner
summary shapes across pytest/jest/unittest/cargo/go/maven/gradle — and
`_TEST_ZERO_SHAPE_RE` at `:115` **vetoes a pass on zero-test evidence**, because
"0 passed" and "[no test files]" match none of the count-bearing alternatives.
`:44-45`: "Anything else is undecidable in code: `checked=False`, rendered
`UNVERIFIED`, never silently passed."

No surveyed competitor has a claim-binding layer. Jules runs your tests; Cline
watches your compiler. Neither has a rule that a *self-report* about a test
result is inadmissible.

**5.2 A vocabulary that keeps the model from conflating evidence with acceptance.**
`subagents/acceptance_checks.py:47-50` — "the leaf booleans are
`checked`/`holds` — never `satisfied`/`verified`/`passed`. Strong-positive words
stay exclusive to the runtime hard gate so the model never conflates
deterministic execution evidence with task acceptance." `:76` puts the same
discipline in the model-visible text: the fixed line "execution evidence only,
does not validate claim correctness". This is prompt design as a *correctness*
mechanism, and it is unusual.

**5.3 A fail-closed answer to "what does this sandbox's shell state look like?"**
`sandbox/sandbox.py:51-64` — `persistent_shell_sessions: bool | None` is
**tri-state and defaults to `None` = undeclared**, with the explicit rule that
"consumers must trust only an explicit `False` and degrade to UNVERIFIED on
`None` exactly as on `True`", because "custom providers are loaded by class path
and may reuse a persistent session, so silence cannot be read as fresh-shell".
The consequence is documented: recorded bash evidence from a session-aware
sandbox is *untrusted* and the acceptance checklist's `tests_passed` matcher
degrades accordingly. Every other agent treats "the command looked clean" as
clean.

**5.4 A verifier that cannot certify on its own failure.**
`finish_first_verifier_middleware.py:22-34` — the module docstring is a
correctness argument, not a comment. Two invariants: (a) the execution history
handed to the critic must report each tool result's *real* status, because
"`ToolMessage.status` is optimistic by construction — a failure carried inside a
`Command` wrapper keeps LangChain's `"success"` default" — so reading
`getattr(message, "status", "success")` certified failed commands as successful.
An absent, blank or unrecognized status now resolves to `"unknown"`, never to
`"success"`"; and (b) "a critic that raises cannot vouch for the claim it was
asked to check, so it withholds approval instead of approving by accident."
Combined with `:19-20` — "The retry is budgeted once per run so a critic can
never create a loop" — this is a *self-verifying verifier* with a bounded,
non-looping correction path. Codex's `auto_review` fails closed on parse
failure; Alpha's critic fails closed on its own exception. Same idea, better
integrated.

**5.5 Tool-choice *telemetry* that keeps the candidate set.**
`tools/selection.py:86-123` — `_emit_selection` records the whole candidate list
with scores, on the explicit reasoning that "which tool *ran* is already in the
tool-call event, and 'which tools were on the table, and why this one won' is the
only thing a self-improving system can learn from a selection". And `:278-292`
`interleave` puts the existing regex scorer first, so a System One miss "can
never regress a literal name match". The ranking *model* is optional
(`rank_candidates` returns `None` = "no signal, keep the regex score",
`:16-20`). No competitor publishes a selection-decision log.

**5.6 A reasoned *order* for the untrusted-content guards, not a list.**
`tool_error_handling_middleware.py:197-210` — the comment explains why
`ToolResultSanitizationMiddleware` sits **inner** of `ToolOutputBudgetMiddleware`:
so it "neutralizes the raw tool output first; the budget wrapper then truncates
the already neutralized text." Truncating first would leave an unneutralized
tail. That is a bug someone will reintroduce unless the comment is there.

**5.7 Subagent cost guards that were added because of a real incident.**
`tool_error_handling_middleware.py:470-516` — the comment cites "the reported
4.4M-token burn" as the motivating defect, explains *why* loop detection was
missing from the subagent chain (`with no loop detection a degenerate subagent
tool loop runs unchecked until max_turns, re-sending a growing context each turn`),
explains the reverse-order dispatch reasoning for why `LoopDetection` must be
registered *before* `SafetyFinishReason` ("LangChain dispatches `after_model`
hooks in REVERSE registration order"), and documents the cross-contamination
argument for fresh-per-task middleware instances. This is a codebase that knows
why its invariants exist.

**5.8 A skill secret that cannot leak into the prompt.**
`skills/types.py:83-105` — `required_secrets` with an `optional` flag and a
`secrets_autonomous` flag whose semantics are stated exactly: a declared secret
"may bind when the skill is in-context via an autonomous model load, or only on
explicit `/slash` activation". And `sandbox/sandbox.py:80-94` — the secret rides
in the sandbox's per-call `env` dict, "without placing them in the prompt, tool
arguments, or the command string", with POSIX-name validation enforced *in the
abstract base class* (`sandbox.py:14-41`) precisely so "a future shell-using
implementation must not have to re-derive its own rule". No competitor in this
survey has a first-class declared-secret mechanism at all.

**5.9 A local-sandbox provider that refuses to pretend.**
`sandbox/security.py:10-20` — `LocalSandboxProvider` is named as "not a secure
sandbox boundary", host bash is disabled by default under it, and the bash
subagent is disabled too, with a message naming the alternative
(`AioSandboxProvider`) and the exact opt-out. Most "sandbox" claims in the
survey would not survive this test. Alpha's default local mode says out loud
that it is not a sandbox.

**5.10 Skills that cannot be reached by an operator.**
`lead_agent/agent.py:857-866` — "Operator allowlist is a hard ceiling: it applies
regardless of what the caller asked for, so a skill cannot be reached unless an
operator named it." Combined with `skills/types.py:22-43` where the name grammar
(`^[a-z0-9]+(-[a-z0-9]+)*$`, ≤64 chars) is defined in **one** module that both
the parser and the storages import, precisely so "the loader that admits a name
and the path helpers that act on one cannot drift apart". A skill installed
under a directory name the operator cannot type after `/` is a key the operator
cannot reach; the code says so.

---

## 6. Ranked recommendations

Ranked by (value × evidence) ÷ effort, honestly. "Proves demand" means a
shipped competitor does the thing, not that a blog post says they should.

---

**1. Add failure events to the hook contract.** `PostToolUseFailure`,
`StopFailure`, and `PermissionDenied`.
- **Why:** Alpha's `ToolErrorHandlingMiddleware` converts every exception into a
  `ToolMessage` *before* the innermost hook layer sees it
  (`tool_error_handling_middleware.py:333` sits outside `HooksBridgeMiddleware` at
  `:341-344`), so today **a hook cannot observe a failure at all**. That makes
  the most common real use case — "run the linter after every file edit",
  Claude Code's own documented example — unimplementable. It also means a hook
  cannot audit a denial.
- **Proves demand:** Claude Code ships all three
  ([hooks](https://code.claude.com/docs/en/hooks)).
- **Effort:** S. **Risk:** low — additive, and gated on the existing
  `hooks.enabled` flag.
- **Caveat:** this widens the hook contract, which is a compatibility surface
  once third parties write hooks. Do it once, with a version, and do not
  retro-fit meaning onto an existing event.

---

**2. Ship an ACP server for Alpha's lead agent.**
- **Why:** ACP is the only interop standard in this survey with a registry, a
  reference client, and multi-vendor adoption. It is the difference between
  "Alpha works in the Alpha web UI" and "Alpha works in Zed, JetBrains, VS Code,
  Neovim, Emacs, Obsidian, marimo". Alpha already has an agent loop, tools,
  streaming, permissions, and skills — everything ACP needs.
- **Proves demand:** Zed invented it and hosts 3+ agents; JetBrains co-developed
  it and runs a **Registry** with Auggie CLI, Factory Droid, Gemini CLI, GitHub
  Copilot, Mistral Vibe, **OpenCode**, and Qwen Code
  ([JetBrains, 2026-01-31](https://blog.jetbrains.com/ai/2026/01/acp-agent-registry)).
  Nine agents and seven-plus clients is a standard, not a proposal.
- **Effort:** L. **Risk:** medium — you inherit ACP's limits (the agent owns auth
  and model selection; Zed explicitly notes External Agents "usually own their
  own runtime, auth, model selection, tools, and native configuration").
- **Caveat:** ACP is designed around "the user is primarily in their editor". A
  chat-and-IM product with 5 IM channels, projects, crews and war rooms maps
  poorly onto a linear thread model. Do this as a *projection*, not by reshaping
  the product. And note the consolidation risk in §1.3: OpenCode, Cline, Factory
  Droid and Kiro are all in the registry. You would be the *fifteenth* agent
  there unless you differentiate, and the honest differentiator is that Alpha is
  the only one with a cross-installation peer network and a workforce layer.

---

**3. Close the verification loop: run the tests, read the failure, fix, re-run — with a bounded attempt count.**
- **Why:** Alpha has every *piece* — `auto_test_and_repair` and
  `reproduce_and_verify` are named `_VERIFY_TOOLS`
  (`finish_first_verifier_middleware.py:89-96`), the evidence binding in
  `acceptance_checks.py` will accept a *recorded* successful test execution, and
  `LoopDetectionMiddleware` will stop a thrash. What is missing is the
  **controller**: today `FinishFirstVerifierMiddleware` *notices* unverified
  completion and asks the model to verify; it never drives the loop. A closed
  loop is the single highest-leverage change to actual task success.
- **Proves demand:** Jules "now operates in a loop — fixing, committing, and
  resubmitting — so your PRs keep moving forward" and "Jules will run existing
  tests, or create new ones" in a Cloud VM
  ([changelog](https://jules.google/docs/changelog),
  [jules.google](https://jules.google/)). Goose ships retry logic and success
  validation as a first-class recipe field.
- **Effort:** M. **Risk:** medium — a naive loop burns tokens and can oscillate.
- **Caveat, stated plainly:** this is where a coding agent can do real damage, and
  the honest failure mode is an agent that "fixes" a test by weakening it. Alpha
  is *unusually well-placed* to defend against exactly this, because
  `tests_passed` already anchors to a recorded execution with a real test-summary
  shape. Use that. **Do not** implement this as a generic "retry until the model
  stops complaining" loop; implement it as "run the declared command, read the
  recorded result, feed the actual failure output back, cap at N attempts,
  and report the cap as a distinct stop reason" — the same shape
  `token_budget` already uses.

---

**4. Add a per-tool risk level and a single Autonomy-Level scalar.**
- **Why:** Alpha already has a tool allowlist and a role-based authorization
  provider, and already has exactly the right hook point
  (`GuardrailMiddleware` + `GuardrailAuthorizationAdapter`,
  `tool_error_handling_middleware.py:255-274`). Adding a `risk: low|medium|high`
  per tool plus a config scalar that gates on it gives the operator one knob
  instead of N, and it extends to MCP tools automatically.
- **Proves demand:** Factory Droid publishes the whole rubric —
  "Execute commands and MCP tools have a risk level… Droid runs them
  automatically when the risk is at or below your Autonomy Level", with Medium =
  "`npm install`, `pip install`, `git commit`, `mv`, `cp`, build tooling" and
  High = "`docker compose up`, `git push` if allowed, migrations, custom scripts"
  ([autonomy level](https://docs.factory.ai/autonomy-and-safety/auto-run)).
- **Effort:** M. **Risk:** low.
- **Caveat:** risk levels are a *guess about the future*, and a wrong Medium that
  should have been High is exactly the failure that makes autonomy dangerous.
  Publish the levels as operator-editable defaults, keep the denylist separate
  and absolute (Droid's "blocklisted commands never run at any level" is the right
  shape), and do not let a skill's frontmatter *raise* a tool's risk.

---

**5. Make plan mode a toolless mode, and add a plan critic.**
- **Why:** Alpha's `is_plan_mode` currently only gates `TodoMiddleware`
  (`lead_agent/agent.py:639-643`). A planner that still holds the write tools is a
  planner you are asking nicely. Remove the tools and the guarantee is structural.
  Then review the plan before execution, the way Jules does.
- **Proves demand:** Roo Code's Orchestrator has *no direct tool access at all*,
  only `new_task` ([using modes](https://docs.roocode.com/basic-usage/using-modes)).
  Cline Plan, Cursor Plan, Factory Spec Mode and Kiro spec-driven development all
  ship the same gate. Jules ships the mirror image: "a secondary agent—the
  **Planning Critic**—to review all plans that do not require human intervention".
- **Effort:** S (tool removal) + M (critic). **Risk:** low.
- **Caveat:** a toolless planner cannot read a file it was not told about, so
  plan quality becomes sensitive to what the user pasted. Roo Code documents this
  trade-off explicitly and offers a documented override. Do the same, and
  surface the "cannot read files" state in the UI rather than letting the planner
  guess.

---

**6. Add a lead-agent `steps` cap alongside the existing guards.**
- **Why:** Alpha guards the *subagent* loop with `max_turns` → recursion-limit
  translation (`subagents/executor.py:93`, `:1526`) and with token budget and
  loop detection. The **lead** has loop detection and token budget but no
  user-facing iteration cap. A user who wants "at most 20 steps, then tell me
  where you are" has no way to express that.
- **Proves demand:** OpenCode's `steps` — "Control the maximum number of agentic
  iterations an agent can perform before being forced to respond with text
  only. This allows users who wish to control costs to set a limit", and on
  hitting it "the agent receives a special system prompt instructing it to
  respond with a summarization of its work and recommended remaining tasks"
  ([agents](https://opencode.ai/docs/agents.md)). Claude Code's `maxTurns` is
  the same idea with the same partial-result marking.
- **Effort:** S. **Risk:** low.
- **Caveat:** a step cap is a blunt instrument and a bad one truncates a task
  that needed 3 more steps. Emit it as a **distinct, visible stop reason** (Alpha
  already has `token_capped` and `consume_stop_reason` for exactly this) — never
  as a normal completion. The "summarize and recommend remaining tasks" final turn
  is the part worth copying; it converts a truncation into a handoff.

---

**7. Publish a reproducible benchmark of Alpha against SWE-bench Verified and Terminal-Bench 2.0.**
- **Why:** this is the credibility gap. Alpha's own repo guidance makes
  `docs/PRODUCTION_READINESS_INVENTORY.md` "the authority on what is implemented"
  and forbids describing single-process JSON/JSONL state as cross-process
  exactly-once. But on the question "does your agent actually resolve issues",
  there is nothing to point at. Every serious competitor in this survey has
  either a benchmark harness (OpenHands, Aider), a published cost/pass-rate
  scatter (Augment), or a step-count distribution (Cognition). Alpha has
  `autonomous_benchmark_tool.py` — a tool the agent can *call* — which is not the
  same thing.
- **Proves demand:** OpenHands publishes 14 models × 5 benchmarks with an
  MLSys 2026 paper, and — notably — reports the *unflattering* V0-vs-V1 parity
  result (68.0% = 68.0%) alongside the flattering one. Cognition reports 13.86%
  on the full SWE-bench set and 38% of tasks needing only 1–2 steps.
- **Effort:** M (the harness; the runs are compute). **Risk:** **high, and
  correctly so** — you may find the number is bad. That is the point, and it is
  why it must be done before someone else does it.
- **Caveat:** follow OpenHands' discipline, not Devin's marketing. Report the
  matched-model comparison and the step-cost distribution, publish the harness
  and the trajectories, and state the harness's own limitations. If Alpha scores
  badly, the honest number plus the honest harness is worth more than no number.
  Do not ship a benchmark that only runs on prompts Alpha was tuned for.

---

**8. Make the checkpoint story one layer, not three tools.**
- **Why:** Alpha has `reversible_delete_tool`, `boulder_checkpoint_tool`,
  `delta_checkpoint_tool`, `durable_replay_tool` and `worktrees.py`. A user
  cannot answer "can I just try that and roll back?" from that list. Cline's
  three-granularity restore is a *concept* users understand before they read
  documentation.
- **Proves demand:** Cline — "Every time Cline modifies a file or runs a command,
  it saves a snapshot of your project files", with *Restore Files* / *Restore Task
  Only* / *Restore Files & Task*, and the explicit argument that "**Checkpoints
  make auto-approve practical. Without checkpoints, it feels risky because Cline
  can make many changes before you notice a problem**"
  ([checkpoints](https://docs.cline.bot/core-workflows/checkpoints)). Zed
  checkpoints per thread and deliberately keeps the button available mid-edit.
- **Effort:** M. **Risk:** medium — checkpointing on every tool call is a real
  write-amplification problem, and Alpha's `sandbox/file_operation_lock.py` and
  `acquire_serialization.py` suggest this area is already contention-sensitive.
- **Caveat:** this is the recommendation most likely to be a mistake. Alpha is
  multi-tenant and per-thread-isolated; a per-tool-call snapshot at that
  granularity is a different cost profile from a single-user IDE. Do the UX
  unification (one "restore" affordance over the existing primitives) before you
  consider the per-step snapshot. The first is S and worth doing on its own; the
  second is L and may never be right for Alpha.

---

**9. Per-tool resource-pattern permissions — borrow the idea, not the mechanism.**
- **Why:** OpenCode can say `"git push *": "deny"` and `"packages/web/src/**/*.mdx":
  "allow"`, and a patch touching three files is denied if *any* resource denies.
  Alpha's `GuardrailMiddleware` gates by tool and role but not by the concrete
  path argument. Read-before-write already tracks which files were read in their
  current version — the path-scoped rule is a natural extension.
- **Proves demand:** OpenCode's ruleset, with last-match-wins semantics
  ([permissions](https://opencode.ai/docs/permissions.md)).
- **Effort:** M. **Risk:** **medium-high, and I want to be blunt about why.**
- **Caveat:** OpenCode's own issue #12566 documents that subagents do not inherit
  a permissive parent ruleset, so subagent calls that resolve to `ask` "block
  indefinitely since there's no human to respond." That is an unattended-mode
  deadlock in a system whose distinguishing feature is the ruleset. Alpha's
  `ClarificationMiddleware` has the same structural risk. Take resource-scoped
  matching; **do not** take a global last-match-wins array that a rule ordering
  can silently invert.

---

**10. Split the two-dimensional agent-visibility flags, and make "hidden" mean both things.**
- **Why:** Copilot separates "hide from the picker but allow subagent
  invocation" (`user-invocable: false`) from "block subagent invocation but keep
  it in the picker" (`disable-model-invocation: true`). Alpha has
  `available_skills` and `allowed_subagents` as separate allowlists but no
  independent control over *who can invoke* versus *who can see*. For a system
  with 130+ builtin tools and a skill catalog, the difference between "a skill the
  model may activate" and "a skill the model must be told about" is the
  difference between a 200KB prompt and a 20KB one.
- **Proves demand:** Copilot's `.agent.md` frontmatter
  ([custom agents in VS Code](https://code.visualstudio.com/docs/agent-customization/custom-agents)).
- **Effort:** S. **Risk:** low.
- **Caveat:** the two flags only pay off if the *deferred tool catalog*
  (`tool_search_tool.py`) is what drives visibility, not the static binding.
  Verify that `catalog_tool_search` cannot surface a tool the visibility policy
  meant to hide — a hidden tool that is still *discoverable* is not hidden. That
  interaction is a real risk and I did not verify it either way.

---

## 7. Confidence and gaps

### 7.1 Well-sourced (primary source, fetched, quoted)

- **OpenCode's permission ruleset**, including the last-match-wins rule, the
  default deny of `.env`, the `doom_loop` trigger at 3 identical calls, the
  `--auto` semantics, and the absence of an OS sandbox. Three independent primary
  sources: the permissions page, the agents page, and the source file
  `agent/subagent-permissions.ts`. **High confidence.**
- **Codex's two-axis sandbox/approval model and `approvals_reviewer =
  "auto_review"` with fail-closed on parse failure.** Primary: OpenAI's own
  sandboxing and agent-approvals pages. **High confidence.**
- **Claude Code's four hook handler types, including the `agent` type.**
  Primary: Anthropic's hooks reference, quoted three separate times across two
  fetches. **High confidence.**
- **Cline's checkpoints and their stated relationship to auto-approve.** Primary:
  Cline's checkpoints page plus the repo README. **High confidence.**
- **Roo Code's tool-less Orchestrator.** Primary: the modes page, which answers
  the question directly. **High confidence.**
- **Aider's PageRank repo map.** Primary: I read the actual `repomap.py` source,
  not a description of it. **High confidence on the algorithm; UNVERIFIED on
  whether it is on by default in current versions.**
- **OpenHands' stateless event-sourced architecture, the `Condenser`, the
  `SecurityAnalyzer`, and the 61% / 68.0% / 14-models × 5-benchmarks numbers.**
  Primary: MLSys 2026 paper (arXiv HTML) and the SDK architecture docs. **High
  confidence.**
- **SWE-agent's linter-on-edit gate, 100-line viewer, and the four ACI
  findings.** Primary: the ACI docs page plus the paper. **High confidence on
  design; the 12.5% SWE-bench number is a 2024 GPT-4-Turbo result and I flag it
  as such.**
- **Jules' CI-fix-commit-resubmit loop and the Planning Critic agent.** Primary:
  the official changelog. **High confidence.**
- **The Antigravity compaction threshold of ~135k tokens.** Primary: Google's
  Gemini API docs for the Antigravity agent. **High confidence that Google
  published this number; I make no claim that it is well-chosen.**
- **ACP's status.** Primary: `agentclientprotocol.com`, Zed's blog, and
  JetBrains' January 2026 registry post. **High confidence.**
- **The four directional changes** (Q→Kiro, Gemini CLI→Antigravity,
  Windsurf→Devin Desktop, Continue→Cursor, Cursor→SpaceX). Primary for Q→Kiro
  (the repo README) and Gemini→Antigravity (Google's blog); reputable
  secondary for the acquisitions. **High confidence; I dated each.**
- **Every Alpha claim in §4 and §5.** All from files I opened in the worktree.
  Where I describe a mechanism I read the docstring *and* at least the
  surrounding code. **High confidence about the code; zero confidence about
  runtime behaviour, which I did not and must not test.**

### 7.2 Thin — real sources, but I could not go deep

- **Cursor, Devin Desktop, Factory Droid, Qodo, Augment, Copilot, Kiro.** I read
  each vendor's own documentation thoroughly enough to answer the eight
  questions, but I did not read source (it is closed for all of them) and I did
  not test. The "distinguishing capability" for each is a real documented
  differentiator; whether it is *the* one that wins is a judgement I cannot
  support with evidence.
- **The permission/sandbox comparison table** at
  `agentscli.com/foundations/permissions/` — a third-party comparative site
  with no visible methodology, no date, and no author I could identify. I used it
  only where it agreed with primary evidence (OpenCode: no OS sandbox) and I
  explicitly marked the Cursor Seatbelt claim UNVERIFIED because I could not
  confirm it from Cursor's docs.
- **The Cursor "up to 10 / unlimited concurrent sessions" and Jules "15 tasks/day,
  3 concurrent" quota numbers** — vendor self-reported, and quotas are a pricing
  instrument, not a capability measure.
- **Cline's "monitors linter and compiler errors"** — a README claim with no
  primary description of the mechanism. I believe it; I could not verify it.
- **Goose's retry/validation** — the recipe *field* is documented; the
  implementation, attempt count, and thrash protection are not.
- **Kiro's property-based testing** — one sentence on the marketing page and
  nothing in the docs. Marked UNVERIFIED.

### 7.3 Not verified at all

- **Tabnine, Sourcegraph Cody, Kilo Code, Void, PearAI.** No primary source
  read. Table rows are UNVERIFIED. **No finding or recommendation in this
  document depends on any of them.** If one of them has a genuinely
  differentiating capability, this report will have missed it, and that is a
  real gap rather than a claim that they lack one.
- **GitHub Copilot Workspace** as a distinct product — I read coding agent and
  agent mode docs. If Workspace was folded into coding agent mode, I did not
  verify that.
- **The Antigravity CLI binary's licence.** Google does not say "closed source"
  in the blog excerpt I read; a third-party analysis says it. Treated as
  directionally established, formally UNVERIFIED.
- **Whether `generate_repo_map` in Alpha is on the hot path**, how it ranks, and
  whether it is equivalent to aider's PageRank. I read the import at
  `tools/tools.py` and did not read the body. Recommendation 7 in §4
  (repo map, item 10) is therefore deliberately scoped as a *question*, not a
  recommendation.
- **Whether Alpha's hook layer can see a denied tool call** (recommendation 9's
  caveat). I read the middleware *ordering* and the comment about hook placement;
  I did not trace whether a denial short-circuit reaches `HooksBridgeMiddleware`.
  I asserted "a hook cannot observe a failure" from the ordering plus the comment
  at `:335-340` ("PreToolUse fires only for calls that survived every guard
  above (no hook runs for short-circuited calls)") — which is about PreToolUse, so
  the PostToolUse side is an inference. **Flagged as such; verify before acting.**
- **Anything about how Alpha performs.** No stack was started, per my
  constraints. This is a document about code and about competitors' published
  claims, and it cannot tell you whether Alpha resolves issues.

### 7.4 What I deliberately did not do

- **I did not add a `FILE_OVERRIDES` entry to
  `scripts/generate_docs_index.py` for this document.** The repo's
  `docs/DISCOVERABILITY.md` requires it, and the generator "fails closed on any
  unclassified Markdown under `docs/`". **Therefore `python
  scripts/generate_docs_index.py` will fail on this file until someone adds the
  entry.** That is a known, deliberate, one-line break I am handing to the lead
  rather than making, because my assignment forbids product-code edits. The
  entry would be:
  `"RESEARCH_CODING_AGENTS.md": DocumentSpec("plans", "Live survey of coding agents and Alpha gap analysis."),`
  placed in `FILE_OVERRIDES` in
  `scripts/generate_docs_index.py`, alongside the existing
  `AGENT_LANDSCAPE_AND_ROADMAP.md` entry.
- **I did not edit `README.md`, `llms.txt`, or `docs/INDEX.md`.** A new
  user-facing document arguably warrants a mention; that is the lead's call.
- **I did not commit, push, or touch the main worktree.**

### 7.5 The one-line summary of the whole survey

Every serious agent in this category has converged on three things Alpha already
has — a bounded loop, a permission story, and a delegation model — and none of
them has what Alpha has: **a claim-binding verification layer that makes a
self-report inadmissible and an UNVERIFIED answer reachable**. The place Alpha is
genuinely behind is narrower and more embarrassing than "behind on agents": it
is behind on **operating the verification it already built**. The tool exists,
the evidence binder exists, the critic exists with a non-looping budgeted
retry — and nothing connects them into a loop. That is recommendation 3, and it
is worth more than the other nine combined.
