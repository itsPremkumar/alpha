# Alpha Agent Tooling — Research Findings and Corrected Plan

**Status:** Phase 2 (command policy gate) **implemented**. Phases 1, 4, 5, and 8 of the
original draft turned out to be **already built** and are documented below rather than
rebuilt. Phase 3 (typed `git` tool) is the main genuine gap that remains.

**Original scope of the request:** give the Alpha super-agent first-class `git`,
`browser use`, `PowerShell`, `terminal`, and `cmd` capability.

> **Read this first.** The first draft of this document was written from `AGENTS.md` alone
> and claimed that shell, browser, and context-budget capabilities were *absent*. They were
> not. A full audit of the tree corrected that, and the corrections are in §2. The research
> synthesis in §1 is unaffected and is the durable value here.

---

## 0. The short version

**You already have four of the five things you asked for**, and they are built well:

| You asked for | Reality | Where |
|---|---|---|
| `terminal` / `bash` | Exists, mature, with real Windows hardening | `alpha/sandbox/tools.py:2049` |
| `PowerShell` | Already the resolved shell on Windows | `alpha/sandbox/local/local_sandbox.py:502` |
| `cmd` | Already supported on Windows | same, via `_is_cmd_shell` |
| `browser use` | Exists twice over (Playwright + CDP) | `alpha/community/browser_automation/`, `alpha/browser/` |
| `git` | **Fragmented, no model-facing tool** — this is the real gap | see §3.1 |

So the original plan was ~70% wrong. Rebuilding any of it would have duplicated working,
tested systems. What was genuinely missing — and is now built — is the **command-content
permission gate**: nothing reviewed what the `bash` tool was about to run.

---

## 1. Research synthesis

This section is the durable research output and is independent of Alpha's current state.

### 1.1 Shell execution — the state of the art

| Pattern | Who | Note |
|---|---|---|
| Single shell tool, shell implicit per-platform | Claude Code | One `Bash` tool. Installs Git-for-Windows on Windows so the tool *is* bash; falls back to PowerShell when Git Bash is absent. |
| Single terminal tool + shell-integration signals | VS Code | **Refuses `cmd` on Windows and `sh` on macOS/Linux by default**: shell integration is unsupported there, so the agent gets no run-start/run-finish signal and "needs to rely on timeouts and watching for the terminal to idle. This leads to a slow and flaky experience." |
| Shell snapshot (aliases/functions/cwd re-injected) | Claude Code | `utils/bash/shellSnapshot.ts`. This is why `cd X && …` and aliases work. |
| Background execution | VS Code | Non-negotiable; a dev server must not block the loop. |
| `ProcessStartInfo.ArgumentList`, never a shell string | SafeCommands | Prevents injection when no shell is needed. |
| Persistent PTY session | TUICommander, better-agent-terminal | On Windows this needs ConPTY — expensive, and Alpha's `persistent_shell_sessions = False` deliberately declines it. |

**The `cmd` finding is the important one.** VS Code and Claude Code independently converged
on *avoiding* `cmd` on Windows for the same reason. Exposing `bash`/`powershell`/`cmd` as
peer tools would make the model guess a dialect and multiply schema cost. Alpha already
gets this right by resolving the shell per-host rather than exposing it as a tool parameter.

**Dialect confusion is the second risk.** PowerShell uses `&` as the call operator, `;` to
separate, `$` to interpolate, backtick to escape. cmd uses `%VAR%`, `^`, `&` chaining. bash
uses `$VAR`, backslash, `&&`. A tool that forwards a string into whichever shell is available
mis-handles the model's output because the model writes the dialect it was trained on.

**Windows-specific failure modes found in the wild:**
- Claude Code v2.1.233 on Windows 11 *silently exits the process* right after Bash/PowerShell
  tool dispatch in long or resumed sessions; the transcript ends at `assistant -> tool_use
  Bash` with no `tool_result`. Related: Windows Git Bash crash after shell snapshot/spawn,
  native/Bun segfaults. Alpha's `taskkill /T /F` process-tree kill plus
  `CREATE_NEW_PROCESS_GROUP` already addresses the orphaned-child half of this.
- PowerShell output mojibake unless `[Console]::OutputEncoding`, `$OutputEncoding`, and
  `PYTHONIOENCODING=utf-8` are forced.
- PowerShell backtick escapes (`` `a ``, `` `b ``, `` `f ``) decode into **raw control
  bytes**. One stray backtick writes `0x07` into a tracked file. With CRLF handling this
  produces the `0D 0D 0A` (CRCRLF) corruption class. This is a real, unaddressed risk for
  this repo's own `SKILL.md`/`.md` corpus — see §3.3.

### 1.2 The permission gate — four verdicts and structural roles

The most consistent finding across ~10 independent projects.

**Four verdicts, not two:** `allow` / `ask` / `deny` / **`defer`**, where `defer` means
"unclassified — let a human decide." Strictly better than a binary allow/deny for an agent
that legitimately needs unusual operations. (`allowlister`, `command_shield`, `prodagent`.)

**Precedence and fragment composition.** Per fragment: `deny > ask > allow`, else `defer`.
Across fragments: any `deny` → `deny`; else any `ask` → `ask`; else all `allow` → `allow`;
else `defer`.

**Structural role tagging — the best idea in the batch.** Don't ask "is `head` safe?"; ask
what role it plays:

```
gh pr list | head -20   →  gh=standalone, head=pipe_filter   ⇒ allow
head /etc/passwd        →  head=standalone                   ⇒ defer
```

One rule covers both. The engine never sees pipelines — only `(argv, role, redirections)`.

**Interpreters are never auto-allowable.** `bash`, `sh`, `pwsh`, `sudo`, `xargs`, `env`,
`exec`, `eval`, plus inline-exec flags (`node -e`, `python -c`). These exist specifically to
escape the gate, so an allowlist entry for one must be a no-op.

**Monotonicity as a proven invariant.** Project config may only *tighten* policy.
`prodagent` has 23 Kani formal-verification harnesses proving it, plus "parse errors never
produce an allow" and "Deny survives all aggregation."

**Fail direction is genuinely contested.** Some projects fail-open, some fail-closed. The
resolution that survives a security review is to separate two different failures:
- **Gate unavailable** (parser missing, backend crashed) → fail closed, do not execute.
- **Parse failure** (unparseable construct) → `defer` to a human. Not a hard deny (that
  bricks legitimate work), not a silent allow (that is the bypass).

**Keep the hard-deny list tiny.** A deny that cannot be overridden will eventually block a
real workflow, and the operator will switch the whole gate off.

**Permission syntax bug worth not copying.** Prefix matching is subtly wrong: `Bash(git
diff*)` also matches `git diff-index`. The space in `Bash(git diff *)` is load-bearing.

### 1.3 Git — "git was never designed for agents"

The sharpest framing available:

> Read and write operations live in the same binary, one flag apart. `git log` is safe.
> `git push --force` rewrites history. There's no separation between "inspect the repo" and
> "mutate the repo." Giving an agent access to `git` for status checks implicitly gives it
> access to every destructive operation git offers.

`git reset --hard` is named the **#1 cause of data loss in AI agents**. Documented incident:
an agent ran `git checkout --` on files holding hours of uncommitted work from another agent
and destroyed it silently, recovered only via `git fsck --lost-found`.

Four cheap, high-value additions: **worktree binding** per agent (so parallel agents stop
stomping one checkout and can't move *your* HEAD), **provenance trailers** via
`prepare-commit-msg` + a `reference-transaction` journal, a **pre-push secret scan** over the
*outgoing commit range*, and **uncommitted-work snapshots** — because `reset --hard` of
uncommitted work has *no* undo in plain git; reflog doesn't cover it.

### 1.4 Browser — do not nest an agent loop inside an agent loop

The deciding question is **who owns the loop**:

| Option | Loop owner | Token cost | Verdict |
|---|---|---|---|
| Playwright MCP (MS, 24 tools) | Host LLM | 14.3k tokens/request, constant | Works, expensive at Alpha's tool count |
| Browser Use (Python, ~100k★) | **The library's own Agent** | 100+KB/page (vision) | **Wrong shape** — nests a second planner inside LangGraph |
| `agent-browser` (Rust CLI, CDP) | Host LLM via shell | "Very low"; 93% reduction vs MCP | Best fit for a shell-capable agent |

Alpha already owns the agent loop, so `browser-use`'s `Agent(task, llm).run()` would put an
autonomous LLM loop *inside* Alpha's — two planners, two context windows, two sets of
stopping conditions, and a completion signal Alpha cannot verify. Rejected on architecture
grounds.

**Accessibility tree, not screenshots.** 2-5KB per page versus 100+KB — 20-50x. Deterministic
`[ref=e14]`-style handles mean no pixel guessing and no vision model. The 2026 production
pattern is **hybrid**: a11y tree for ~90% of interactions, screenshot fallback for canvas,
charts, and maps.

Microsoft themselves recommend CLI+skills over MCP for high-throughput coding agents purely
on schema-token cost. With 130 tools already registered, that lands harder on Alpha than on
anyone.

**Measured cost of the wrong choice** (same ~3,000-row scrape): vanilla Playwright 41s;
Playwright MCP 2m18s; Stagehand 4m06s cold → 47s cached; browser-use 6m22s *every run*
because it re-reasons each step.

### 1.5 Context engineering — the measured numbers that matter

- One YNAB-scale tool ≈ **663 tokens** of schema.
- A full Playwright MCP server ≈ **14,300 tokens**, on *every request*, even in sessions
  that never open a browser.
- Tool results were **56%** of context tokens in a Terminal-Bench unpack, tool calls another
  **28%** — **84% of spend is tool-shaped.**

**Three-layer cascade, in cost order:** (1) output truncation to a file + preview, zero LLM
calls; (2) input eviction of large write-args at ~85% fill; (3) LLM summarization as an
expensive backstop.

**Layer 3 should not be the default.** JetBrains' SWE-bench-Verified benchmark: LLM
summarization cut cost 50%+ but caused **15% trajectory elongation**. **Observation
masking** — keep the latest N turns full, replace older tool outputs with structured
placeholders, preserve the action/reasoning skeleton — got **52% with no elongation**.

**Compaction must not compound.** CliffCompaction: only truncate or drop, never rephrase, and
discard the previous compaction so each pass is faithful to original content. Recursive
summarization yields "summary of summaries" where early-session decisions fade.

**Best available: lossless reversible compaction.** ARC keeps an append-only
content-addressed store of every observation and replaces old ones with dereferenceable
citations plus a `_recall <id>` action: **99.40% vs 88.12%** on needle-in-a-haystack at
16k/32k windows, with a uniformly bounded active view. DTOC reaches similar conclusions and
its ablations show **reversibility is load-bearing**.

**Budget target: 30-40% fill.** Above that, "context rot" degrades quality even with window
capacity left.

### 1.6 Agent loop, stopping, and HITL

- **Tool taxonomy:** data / action / orchestration. Classify at design time.
- **Four layered stopping conditions, all required:** max iterations; token/cost budget;
  **no-progress detection**; goal-achievement check. Magentic-One adds a dual loop with
  strategy reset on stall.
- **Plan-and-execute** (LLMCompiler-style) reports a **3.6x** speedup over sequential ReAct.
- **Orchestrator-worker** beat single-agent by **90.2%** on Anthropic's internal research eval.
- **HITL is native to LangGraph.** Two hard rules: the node **restarts from the beginning**
  on resume, so side effects above `interrupt()` must be idempotent or live in a later node;
  and `Command(resume=...)` is the only Command pattern valid as `invoke()` input.
- Never use the `respond` decision to deny a side-effecting tool — the model reads it as a
  *successful* result.

**Programmatic tool calling** (`allowed_callers: ["code_execution_20260120"]`) lets the model
write Python that calls tools and aggregates *inside* the sandbox so intermediate results
never enter context: **+11% on BrowseComp/DeepSearchQA with 24% fewer input tokens.** Blocked
on model support, and `allowed_callers` is guidance, **not** a security boundary.

### 1.7 What the ecosystem agrees is *not* solved

The loop itself is stable. The open problems are **context management**, **multi-loop
coordination**, and **decision auditability**. Debugging a 20-iteration run still means
stitchering together logs, tool traces, and reasoning output. If Alpha wants one
differentiator in this space, **a real execution-trace UI** beats another tool.

---

## 2. Corrected inventory — what Alpha already has

The audit findings that invalidate the original plan.

| Capability | Original claim | Reality |
|---|---|---|
| Shell/bash | absent | `alpha/sandbox/tools.py:2049`. Windows-hardened: `CREATE_NEW_PROCESS_GROUP`, bounded pipe-drain threads (10MB cap), `taskkill /T /F` process-tree kill, 600s `sandbox.bash_command_timeout`, secret masking, middle-truncation that preserves a trailing exit marker. |
| PowerShell | absent | `local_sandbox.py:502` `_get_shell()` resolves `pwsh` → `powershell` → `%SystemRoot%\…\powershell.exe` on Windows, with `-NoProfile -Command`. |
| `cmd` | absent | Same resolver, via `_is_cmd_shell` → `cmd.exe /c`. |
| MSYS/Git Bash | absent | `_is_msys_shell` + `_msys_path_conversion_exclusions()` setting `MSYS2_ARG_CONV_EXCL`. |
| Browser | absent | `alpha/community/browser_automation/` — 8 Playwright tools, stable numeric `[ref]` (`data-df-ref`) re-stamped on every action, SSRF screening, hard `max_sessions` cap, `cdp_url` fails closed without `allow_unguarded_cdp`. Plus a CDP/stealth suite in `alpha/browser/` and a Live WebSocket router. |
| Tool-output budget | absent | `config/tool_output_config.py::prune_tiers` per-tool-class externalization (`{'bash': 65536, 'web_fetch': 16384}`), plus `ToolOutputBudgetMiddleware` and `ToolResultSanitizationMiddleware`. |
| Compaction | absent | `AgentWorkspaceSummarizationMiddleware`, `trajectory_compressor` and `micro_compaction` capabilities, `context_as_data_tool`. |
| Programmatic tool calling | "phase 8" | `alpha/tools/programmatic_calling.py` (`ToolExecutionBridge`, `MAX_STDOUT_BYTES = 50_000`, spills to disk). |
| Progressive tool disclosure | absent | `skills.deferred_discovery`, `tool_search` deferred catalog, `agent_presets` that may only *narrow*. |
| HITL | absent | `ClarificationMiddleware` + `ask_clarification` form cards; `non_interactive` correctly drops it for scheduled runs. |
| Sandbox containment | "optional" | `allow_host_bash: false` **by default**; `LocalSandboxProvider` reports `supports_agent_skill_isolation=False`; AIO/E2B/BoxLite/Tenki/OpenSandbox + a K8s provisioner are all wired. |
| Git | absent | Fragmented across `worktrees.py`, `semantic_git_delta.py`, `evolution/update_engine.py`, and `code_agentic_core.py` checkpoints — all argv-only, `GIT_TERMINAL_PROMPT=0`, `stdin=DEVNULL`. No model-facing typed tool. |
| Command-risk auditing | absent | `alpha/agents/middlewares/sandbox_audit_middleware.py` with `_HIGH_RISK_PATTERNS` — **regex-based and position-sensitive**, and an audit rather than an enforcing gate. |

### 2.1 The one real gap, and the class of bug it fixes

`SandboxAuditMiddleware` is the closest thing to a command gate, and it has two structural
weaknesses that pattern matching cannot avoid:

1. **Position sensitivity.** `$(curl url)` in *command* position is blocked, but
   `x=$(curl url)` and `echo $(curl url)` in *value* position pass. Same construct, opposite
   verdict, decided by where it appears in the string.
2. **No role awareness.** It cannot express "safe in a pipeline, not safe standalone,"
   which is the whole point of `gh pr list | head -20` versus `head /etc/passwd`.

`CommandPolicyProvider` (now implemented) closes both by parsing the command and classifying
each simple command by role, and it plugs in as a `GuardrailProvider` so it reuses the
existing `GuardrailMiddleware` — no parallel authorization system.

---

## 3. Remaining plan

### 3.1 Typed `git` tool — the main gap *(not started)*

Alpha's git capability is real but **invisible to the model**: `git_push` and `git_commit`
are explicitly absent, and `gh` is only an env-var target. The agent can only reach git by
writing a raw `bash` string, which §2.1 shows is the weakest link in the chain.

Ship a typed `git` tool with an explicit action enum and risk tier per action:

- **Read → allow:** `status`, `log`, `diff`, `show`, `blame`, `shortlog`, `rev-parse`,
  `describe`, `ls-files`, `ls-remote`, `cat-file`, `reflog`, `worktree list`, `stash list`.
- **Write → ask:** `add`, `commit`, `branch`, `switch`, `merge`, `rebase`, `pull`, `push`
  (non-force), `fetch`, `stash push|pop`, `tag -a`, `worktree add`.
- **Destructive → ask, naming the safer alternative:** `push --force` (offer
  `--force-with-lease`), `reset --hard` (offer `--soft`), `clean -f` (offer `-n` first),
  `branch -D`, `revert`, `cherry-pick`.
- **Never:** `push --force` to a protected branch, `gh repo delete`.

Return **structured JSON**, not text, so the model reasons over structure and output
truncation cannot silently drop the signal.

Then the four additions from §1.3, in priority order: **pre-push secret scan** over the
outgoing commit range (highest incident-prevention value per line — do this first);
**uncommitted-work snapshots** via `git stash create` into `refs/alpha-snapshots/<ts>`,
before shipping `reset --hard` at all; **worktree binding** per subagent, extending the
existing `sandbox/worktrees.py`; **provenance trailers**.

### 3.2 Byte-corruption guard *(not started)*

Block `0D 0D 0A` (CRCRLF) and forbidden control bytes (`01-08, 0B, 0C, 0E-1F, 7F`; TAB/LF/CR
allowed) on `git add` / `git commit` / pre-push, skipping known binary/font/media/Office
extensions and any stream containing NUL. Directly protects this repo's Markdown corpus
from the PowerShell backtick bug in §1.1. Belongs in the pre-commit path next to the
existing `alpha/safety/ast_syntax_guard.py`.

### 3.3 Ask/deny → real approval queue *(not started)*

`GuardrailDecision.allow` is binary, so `ask` and `defer` currently both surface as a block.
The real verdict is preserved in `decision.metadata['verdict']` and distinguished by reason
code, so wiring an approval queue later needs no breaking change. Until then, an operator
simply allowlists the pattern — which is why the `defer` tier exists.

### 3.4 Execution trace UI *(not started)*

Per §1.7, the highest-leverage differentiator available. Alpha already records guardrail
decisions to `RunJournal` via `MIDDLEWARE_GUARDRAIL_TAG`; the missing piece is surfacing
tool call → args → verdict → result → the model's reading of it, replayably.

---

## 4. What was implemented

### `alpha/guardrails/command_policy.py`

A `GuardrailProvider` that gates **command content** for the `bash` tool, configured through
the normal `guardrails.provider.use` class path.

- **Structural decomposition.** `decompose()` splits a command into fragments carrying
  `(argv, role, redirections)`, handling pipelines, `&&`/`||`/`;`, command substitution
  (`$(…)` and backticks, recursively), and redirections. Stdlib-only, no new dependency.
- **Four-way verdict.** `allow` / `ask` / `deny` / `defer`, with `defer` as the safe default.
- **Parse failures never allow.** Any unparseable input records a problem and forces
  `defer`; the tokenizer only returns `allow` for a command it fully decomposed.
- **Interpreters are never auto-allowable**, checked *before* rule evaluation so no
  configured allow rule can outrank them.
- **Quoted data is opaque.** A commit message is one token and is never pattern-matched, so
  `git commit -m 'fix rm -rf bug'` is not a false positive.
- **No glob selectors.** Rules match on normalized executables plus `has_any` / `has_all` /
  `unless` and roles, deliberately avoiding the `git diff*` vs `git diff *` ambiguity.
- **Backslash is literal, not a POSIX escape**, so Windows paths are not silently rewritten
  into a different string than the shell receives. The error direction is safe: obfuscation
  makes a command *unclassifiable* (`defer`), never allowed.
- **Hard-deny list kept to six entries** that genuinely have no legitimate use.
- Two shipped profiles, `read-only` and `repo-write`, as data rather than code.

### Tests

`backend/tests/test_command_policy.py` — 61 tests covering decomposition, role
classification, the never-allow parse-failure guarantee, interpreter safety, profile
behaviour, fragment composition, rule-order independence, and the false-positive cases.

### Config

`config.example.yaml` gained a documented **Option 4** block for the provider. It is
comments-only, so no `config_version` bump is required. (Note: this checkout's
`config.yaml` is at `config_version: 50` while the example is at 54 — pre-existing drift,
`make config-upgrade` reconciles it.)

### Known limitations, stated rather than hidden

- **`ask` and `defer` both block.** The binary `GuardrailDecision` interface cannot express
  "ask a human" yet. See §3.3.
- **The two profiles are currently identical.** `repo-write` is a separate name so widening
  it later is a permission *increase*, never a surprise tightening.
- **This is a guardrail, not a sandbox.** An allowed command can still do anything its user
  can. Containment is the sandbox provider's job.
- **It complements `SandboxAuditMiddleware` rather than replacing it.** That middleware is
  unconditional and ungated; the policy provider is opt-in via `guardrails.enabled`.
- **Parse coverage is bash-flavoured.** PowerShell and cmd syntax are not modelled; a
  PowerShell-specific parse mode would be a follow-up. On Windows, Git Bash is the default
  host shell, so this covers the common path.

---

## 5. Documentation obligations (per `AGENTS.md`)

- `backend/docs/GUARDRAILS.md` — add the provider to the options list. *(not done)*
- `README.md`, `llms.txt`, `llms-full.txt`, `docs/FAQ.md`, `docs/COMPARISON.md` — capability
  counts. **Unchanged and correct**: this adds no tool, router, middleware, or supervisor
  loop, so `contracts/feature_manifest.json` needs no regeneration. The audit confirmed
  `test_feature_manifest_wiring.py` and `test_no_orphan_modules.py` both pass with this
  module wired.
- `AGENT_TOOLING_IMPLEMENTATION_PLAN.md` (this file) is at the repo root, **not** under
  `docs/`, so `scripts/generate_docs_index.py` does not need a `FILE_OVERRIDES` entry. If it
  is ever moved into `docs/`, it does.

---

## 6. Honest risks

1. **A gate is not containment.** Every project in this space says so. Alpha's real boundary
   is the sandbox provider, and `allow_host_bash` is off by default — keep it that way.
2. **`defer` can be noisy.** A read-only profile will block legitimate work the model
   improvises. That is the correct trade (a human decides), but operators will feel it.
   Widening rules is the fix; do not widen the hard-deny list.
3. **`SandboxAuditMiddleware` still runs and is still regex-based.** Two gates with different
   philosophies can disagree. Decide deliberately whether the audit middleware should be
   narrowed now that a structural gate exists.
4. **Windows is the hard path** — backtick control bytes, PowerShell encoding, and the
   documented class of silent process exit on long sessions.
5. **The typed `git` tool is the bigger prize and is still unbuilt.** Until it exists, every
   git operation is a raw `bash` string, which is exactly the surface §2.1 shows to be the
   weakest link.
