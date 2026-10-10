<div align="center">

# 🦁 Alpha

### Alpha — The King of AI Agents

**The open-source, self-hosted autonomous multi-agent AI operating system.**

**Alpha is a self-hosted, local-first AI agent platform that plans and executes
long-horizon work — and reports honestly when a result is unverified.** It runs a LangGraph agent runtime behind a FastAPI
Gateway with a Next.js 15 web workspace and a Windows desktop app — combining deep
research, multi-agent swarms, sandboxed code execution, persistent memory, 136 native
tools, MCP extensions, and 24 public skills, with a single Nginx entry point and no
proprietary backend.

[![CI](https://github.com/itsPremkumar/alpha/actions/workflows/backend-unit-tests.yml/badge.svg?branch=main)](https://github.com/itsPremkumar/alpha/actions/workflows/backend-unit-tests.yml)
[![Lint](https://github.com/itsPremkumar/alpha/actions/workflows/lint-check.yml/badge.svg?branch=main)](https://github.com/itsPremkumar/alpha/actions/workflows/lint-check.yml)
[![Frontend](https://github.com/itsPremkumar/alpha/actions/workflows/frontend-unit-tests.yml/badge.svg?branch=main)](https://github.com/itsPremkumar/alpha/actions/workflows/frontend-unit-tests.yml)
[![E2E](https://github.com/itsPremkumar/alpha/actions/workflows/e2e-tests.yml/badge.svg?branch=main)](https://github.com/itsPremkumar/alpha/actions/workflows/e2e-tests.yml)
[![Drift Gate](https://github.com/itsPremkumar/alpha/actions/workflows/generated-drift-gate.yml/badge.svg?branch=main)](https://github.com/itsPremkumar/alpha/actions/workflows/generated-drift-gate.yml)
[![Windows Installer](https://github.com/itsPremkumar/alpha/actions/workflows/windows-installer.yml/badge.svg?branch=main)](https://github.com/itsPremkumar/alpha/actions/workflows/windows-installer.yml)
[![Release](https://img.shields.io/github/v/release/itsPremkumar/alpha?label=release&logo=github)](https://github.com/itsPremkumar/alpha/releases)
[![Stars](https://img.shields.io/github/stars/itsPremkumar/alpha?style=social)](https://github.com/itsPremkumar/alpha/stargazers)
[![Forks](https://img.shields.io/github/forks/itsPremkumar/alpha?style=social)](https://github.com/itsPremkumar/alpha/network/members)
[![Issues](https://img.shields.io/github/issues/itsPremkumar/alpha?style=social)](https://github.com/itsPremkumar/alpha/issues)
[![License: MIT](https://img.shields.io/badge/License-MIT-10b981.svg)](https://opensource.org/licenses/MIT)

[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](./backend/pyproject.toml)
[![LangGraph](https://img.shields.io/badge/LangGraph-Agent_Runtime-1f7a5c?logo=langgraph)](./backend/pyproject.toml)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi&logoColor=white)](./backend/pyproject.toml)
[![Next.js](https://img.shields.io/badge/Next.js-15-000000?logo=next.js&logoColor=white)](./frontend/package.json)
[![React](https://img.shields.io/badge/React-19-087ea4?logo=react&logoColor=white)](./frontend/package.json)
[![Node.js](https://img.shields.io/badge/Node.js-22%2B-339933?logo=node.js&logoColor=white)](./Makefile)
[![Electron](https://img.shields.io/badge/Electron-Windows_Desktop-47848F?logo=electron&logoColor=white)](./electron/README.md)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](./docker/docker-compose.yaml)
[![MCP](https://img.shields.io/badge/MCP-Model_Context_Protocol-8A2BE2)](./docs/EXTENSIONS.md)

**Quick links:** [60-Second Quickstart](#60-second-quickstart) ·
[Alpha vs. other agent frameworks](#alpha-vs-other-agent-frameworks) ·
[Use cases](#what-can-you-build-with-alpha) ·
[Documentation](#documentation) ·
[FAQ](#faq) ·
[`llms.txt`](./llms.txt) (for AI agents) ·
[Releases](https://github.com/itsPremkumar/alpha/releases)

**Author:** [Prem Kumar](https://github.com/itsPremkumar) ·
**Repository:** [github.com/itsPremkumar/alpha](https://github.com/itsPremkumar/alpha) ·
**License:** [MIT](./LICENSE)

</div>

---

## Table of contents

- [What is Alpha?](#what-is-alpha)
- [Alpha at a glance](#alpha-at-a-glance)
- [Why Alpha?](#why-alpha)
- [Alpha vs. other agent frameworks](#alpha-vs-other-agent-frameworks)
- [What can you build with Alpha?](#what-can-you-build-with-alpha)
- [60-second quickstart](#60-second-quickstart)
- [Feature catalog](#feature-catalog)
- [Architecture in one diagram](#architecture-in-one-diagram)
- [Deployment options](#deployment-options)
- [Configuration](#configuration)
- [Documentation](#documentation)
- [Extending Alpha](#extending-alpha)
- [Quality gates](#quality-gates)
- [Alpha Network: agent-to-agent communication](#alpha-network-agent-to-agent-communication)
- [FAQ](#faq)
- [Contributing](#contributing)
- [Security](#security)
- [Project provenance](#project-provenance)
- [Citation](#citation)

---

## What is Alpha?

**Alpha is an open-source autonomous multi-agent AI operating system** — a
self-hosted platform where LLM agents plan and execute long-horizon tasks against
your real tools, files, and the web — reporting honestly when a result is unverified
— instead of only answering a chat prompt.

It is **not** a chat wrapper and **not** a hosted SaaS. You run it on your own
machine or your own server, bring your own model API keys, and every artifact
(thread, memory, checkpoint, artifact, audit record) stays on infrastructure you
control.

In one sentence:

> Alpha is a LangGraph-based agent operating system: a Python/FastAPI Gateway runs
> the agent runtime and 136 native tools, a Next.js 15 workspace and an Electron
> Windows app are the front ends, and a single Nginx port is the only thing you
> expose.

### The 60-second version

| Question | Answer |
| :--- | :--- |
| **What does it do?** | Turns one prompt into a multi-step execution: research, plan, delegate to subagents, run sandboxed code, and write files. Execution completion and verified delivery are reported separately; external delivery depends on configured integrations and policy. |
| **How is it different from a chatbot?** | It has a durable run lifecycle, a sandbox, a memory plane, budgets, approval gates, and a full audit trail — it keeps working after you close the laptop. |
| **How is it different from a coding CLI?** | It is not tied to a repo or a language. Research, ops, data, docs, and messaging are first-class, not afterthoughts. |
| **Do I need a paid backend?** | No. MIT throughout, no Alpha-operated cloud, no broker, no telemetry requirement. You pay only for the model provider you configure. |
| **Which models?** | Any provider you put in `config.yaml` — OpenRouter, OpenAI, Anthropic, Google, DeepSeek, Moonshot, Ollama, and self-hosted endpoints. |
| **Does it run offline?** | Local models, local speech (Whisper + Piper), and local SQLite/PostgreSQL are all supported. |
| **What happens when the internet drops?** | Alpha measures its own connectivity and says so — the workspace header shows the link state and its measured round-trip. Work that needs the link is *parked*, not lost, and the backend keeps re-probing automatically (5s → 300s backoff, no give-up) until it returns, then resumes on its own. A **Retry** button on that reading asks for an immediate measurement. See [durable runtime](./docs/architecture/durable-runtime.md). |
| **Is it a framework or an app?** | Both. Use it as a finished app, or import `alpha-harness` (`import alpha.*`) and build your own agent runtime on the same engines. |

---

## Alpha at a glance

| | |
| :--- | :--- |
| **Current version** | `2.1.0` |
| **Language / runtime** | Python 3.12+ (backend), TypeScript (frontend) |
| **Agent runtime** | LangGraph (async, checkpointed, interruptible) |
| **Gateway** | FastAPI 0.115+ / Starlette / Uvicorn — 68 routers |
| **Frontend** | Next.js 15 (App Router) + React 19 + Tailwind |
| **Desktop app** | Electron (Windows), self-contained runtimes, one-click NSIS installer |
| **Edge** | Nginx reverse proxy on `:2026` (the only public port) |
| **Persistence** | SQLite or PostgreSQL, vector memory, AES-GCM-encrypted checkpoints |
| **Sandboxing** | Local subprocess, Docker container, or Kubernetes provisioner |
| **Native tools** | 136 (`contracts/feature_manifest.json`, generated) |
| **Middleware layers** | 44 |
| **Background supervisor loops** | 10 |
| **Public skills** | 24 in `skills/public/` |
| **Integrations** | Telegram, Slack, Feishu/Lark, WeChat, WeCom, DingTalk, Discord, Buzz, Signal, GitHub webhooks, MCP, generic REST |
| **API compatibility** | OpenAI-compatible `POST /api/compat/openai/chat/completions` |
| **Harness subsystems** | 119 engine packages under `backend/packages/harness/alpha/` (count is generated: `contracts/feature_manifest.json`) |
| **Backend tests** | pytest suite under `backend/tests/` (1,000+ test modules) |
| **License** | MIT |

### Architecture in one diagram

```mermaid
flowchart TB
    subgraph Presentation ["Presentation"]
        Electron["Electron Windows app"]
        Web["Next.js 15 workspace :3000"]
        IM["Omnichannel hub: Slack · Telegram · Feishu · WeChat · WeCom · DingTalk · Discord · Buzz · Signal"]
        OpenAICompat["OpenAI-compatible API"]
    end

    subgraph Edge ["Edge"]
        Nginx["Nginx reverse proxy :2026 — the only public port"]
    end

    subgraph Runtime ["Agent runtime"]
        Planner["Planner + 8-dimension task evaluator"]
        Orchestrator["Swarm / subagent orchestrator (A2A, DM, DAG)"]
        Research["Deep research engine (5-pass, citation-verified)"]
        Harness["Continuous execution harness (goal engine, Ralph loop, checkpoints)"]
        Cognition["Cognitive plane (AVO, MoA, ToM, dreaming)"]
        CodeCore["Code agentic core (AST-grep, repo twin, auto-repair)"]
        Tools["136 tools + 44 middlewares + MCP + 24 skills"]
    end

    subgraph Security ["Security & governance"]
        Astra["Astra enclave + scoped credential vault"]
        Approval["Approval gate + emergency stop"]
        Audit["Trajectory flight recorder + token spend meter"]
    end

    subgraph Storage ["Persistence & memory"]
        DB[("SQLite / PostgreSQL")]
        Mem[("Vector memory + knowledge graph")]
        CKPT[("Encrypted checkpoints + artifact archive")]
    end

    Electron --> Nginx
    Web --> Nginx
    IM --> Nginx
    OpenAICompat --> Nginx
    Nginx --> Runtime
    Runtime --> Security
    Runtime --> Storage
```

---

## Why Alpha?

Most agent frameworks give you a loop and leave the hard parts to you. Alpha ships
the parts that decide whether an autonomous agent is usable in production.

| Production problem | How Alpha solves it |
| :--- | :--- |
| **The agent dies mid-task** | Durable Boulder checkpoints, worker-lease fencing, and automatic resume after a crash, expired lease, or model error. Browser refresh never cancels work. |
| **It hallucinates that it finished** | A Goal Engine with verifiable completion criteria plus a finish-first evidence matrix. A run can complete and still be honestly reported as *unverified*. |
| **It burns your budget** | Token, tool-call, wall-clock, task, and replan budgets per run; explicit `budget_exhausted` / `stalled` states; cache-aware spend telemetry. |
| **It runs dangerous commands** | A risk-scoring approval gate, a scoped credential vault, per-thread sandbox isolation, and an emergency stop (Estop) — ⚠️ though Estop currently gates only the RSI cycle, so do not treat it as a fleet kill switch. |
| **You can't tell what it did** | End-to-end artifact lineage tracing — hash-linked provenance from prompt to output (stored locally; not a cryptographic attestation) — plus a run-event feed and `X-Trace-Id` correlation on every log line. ⚠️ The trajectory flight recorder's writer is not installed in production, so span-level tracing is off by default and not reachable from `config.yaml`. |
| **It can't use your tools** | 136 native tools, MCP over stdio/HTTP/SSE, a documented extension contract, and an OpenAI-compatible endpoint for third-party clients. |
| **It forgets everything** | A layered memory plane: working memory, episodic replay, semantic knowledge graph, and idle-time dreaming consolidation. |
| **It only works in a terminal** | Web workspace, Windows desktop app, and eight messaging platforms — all driving the same agent runtime. |
| **You can't evaluate it** | A benchmarks registry, a skill quality reviewer, a 5-pass research citation contract, and a generated `feature_manifest.json` that fails CI on registry drift. |

**Alpha is honest about its limits.** Where a capability is a local projection
rather than a real execution, or where a guarantee is restart-recoverable for one
process but not cross-process, the documentation says so. That is a design
decision, not a gap in the docs.

---

## Alpha vs. other agent frameworks

If you are evaluating agent frameworks, this is the section to read.

**Read the ⚠️ column before you decide.** Alpha is unusually candid about its own
limits, and a comparison table that only lists strengths is marketing. Every ⚠️
below was established by reading the implementation, not the documentation — the
sources are cited inline and the audit that produced them is
`ALPHA_AUDIT_REPORT*.md` in the repository root.

Comparison targets are grouped by what they actually are. **Harness** = a library
you build an app with. **Agent** = a finished agent you install and run. **App
platform** = a visual builder.

### Self-hostable agents you install and run

| | **Alpha** | **OpenClaw** | **Hermes Agent** |
| :--- | :--- | :--- | :--- |
| **What it is** | Multi-agent OS: runtime, UI, OS/desktop app, deploy | Self-hosted personal agent + local gateway | Self-hosted personal agent harness |
| **Origin** | `itsPremkumar/alpha` | Peter Steinberger (PSPDFKit), MIT | Nous Research, MIT |
| **Ready-to-run web UI** | ✅ Next.js 15 workspace | ✅ via its local gateway | ⚠️ terminal-first; no shipped workspace UI |
| **Windows desktop app** | ✅ Electron, per-user one-click installer | ❌ | ⚠️ Windows via WSL2 per its own docs |
| **Messaging channels** | ✅ 9 platforms (Feishu, Slack, Telegram, Discord, DingTalk, …) | ✅ 50+ channels, widest reach | ✅ Telegram, Discord, Slack, WhatsApp |
| **Skill/plugin ecosystem** | ✅ 24 public skills + `alpha-harness` package | ✅ large third-party skill marketplace | ✅ first-party skills; can drive other harnesses as sub-agents |
| **Local-model support** | ✅ Ollama / vLLM / LM Studio presets | ✅ Ollama | ✅ five backends: local, Docker, SSH, Singularity, Modal |
| **Multi-agent swarms** | ✅ lease-fenced DAG, budgets, consensus | ⚠️ messaging/collab, not a leased DAG | ✅ delegates to other harnesses as sub-agents |
| **Persistent memory** | ✅ owner-scoped tiers with provenance | ⚠️ context/memory files | ✅ tiered, designed to survive restarts |
| **Code execution isolation** | ✅ Docker/AIO/K8s; ⚠️ the *local* provider is a path convention, **not** a security boundary | ⚠️ host-level tools | ✅ container hardening + namespace isolation |
| **Durable long-running runs** | ✅ checkpoints + lease recovery; ⚠️ the message *event feed* defaults to volatile | ⚠️ not a documented run-durability model | ⚠️ long-running sessions, not a run journal |
| **Autonomous coding → PR → merge** | ❌ **see Known gaps** — the worktree and PR libraries are built but unwired, nothing commits or merges | ⚠️ shell-driven | ⚠️ delegates to Claude Code / Codex / OpenCode |

### Libraries and app platforms

| | **Alpha** | **LangGraph** | **AutoGen** | **CrewAI** | **OpenHands** | **Dify** |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Shape** | Full agent OS / app | Low-level graph library | Actor/actor library | Agent + flow framework | Coding-agent product | Low-code LLM app platform |
| **You get** | Running system, UI, deploy, docs | A graph primitive | Message-passing actors | Crews & Flows abstractions | A software-engineering agent | A visual builder + runtime |
| **Ready-to-run web UI** | ✅ | ❌ | ❌ (Studio separate) | ❌ | ✅ | ✅ |
| **Windows desktop app** | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **MCP client** | ✅ stdio / HTTP / SSE | Via LangChain | Community | ✅ | ✅ | ✅ |
| **Deep research with citation contract** | ✅ 5-pass built in | Build it | Build it | Build it | ❌ | ❌ |
| **Self-host without a cloud account** | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠️ optional tiers |
| **Language** | Python + TypeScript | Python + JS/TS | Python (+ .NET) | Python | Python + TS | TypeScript + Python |

> Sources for the OpenClaw and Hermes columns: their public project documentation
> as of October 2026, cross-checked across several independent write-ups. Star
> counts are deliberately omitted — they moved 3× in six months across the sources
> I read, so a number in this table would be stale within weeks. Claims about
> *their* internals are **not** source-audited the way Alpha's rows are; treat them
> as vendor-described, and verify before depending on them.

### ⚠️ Known gaps — what Alpha does not do

This is the part most comparison tables omit. Each item is a verified finding, and
none of it is hypothetical.

| Area | Status | Evidence |
| :--- | :--- | :--- |
| **Autonomous code → PR → merge** | ❌ Not operational. `sandbox/worktrees.py` is 338 lines of correct, hardened code with **no tool, route, or caller**. The one production use drops the model's diff text into a `.patch` and never applies it. Nothing commits, reviews, or merges; the prompts tell the LLM to call `gh pr create` itself. | `swarm/worker.py:397-447`; no `git commit` in the codebase |
| **Emergency stop (ESTOP)** | ❌ Advertised to the model as pausing "all background tasks and subagents"; in fact it gates **only** the RSI cycle. Nothing in the run worker, run manager, admission controller, or the 8 autonomy loops reads it. The tool returns *"Fleet execution paused."* | `rsi/switchboard.py:51` is the sole consumer; the repo's own test is named `test_estop_engagement_refuses_cycle` |
| **PR checklist** | ❌ **Fixed** for the checklist. It printed `- [x] Automated unit and regression test suite passed` for every PR having run nothing; boxes are now derived from real evidence receipts, and required-but-missing evidence produces an explicit "this PR is not verified / must not be merged" callout. ⚠️ The *signatures* behind it are still a dict literal and three regexes. | `65464c1`; `projects/pr_synthesizer.py` |
| **Concurrent subagent writes** | ❌ All subagents in a thread share one workspace directory. A per-path lock serialises writes but there is no shared version counter, so the second writer silently overwrites the first — and both report success. | `subagents/executor.py:1360-1363`; `file_operation_lock.py:20-27` |
| **Goal hierarchy** | ⚠️ The run loop's goal is one flat objective string with no children. The versioned plan store that *does* model this has no run-loop caller. | `agents/goal_state.py:22-31`; `goals/` unimported by `runtime/` |
| **Distribution tracing** | ⚠️ Off by default and **not reachable from `config.yaml` at all** — there is no `observability:`/`trace:` key in `AppConfig`. The run-event feed and `X-Trace-Id` correlation do work. | `observability/config.py:68`; `config/app_config.py:258-509` |
| **Behavioural evaluation** | ❌ 26,933 tests, none of which asks whether the *agent* works. Loop-detection and duplicate-work guards have unit tests; plan success and tool-failure recovery have no behavioural eval. | `alpha/benchmarks/suites.py` evaluators are pure functions with fixtures |
| **Mixture-of-Agents tool** | ❌ **Fixed.** It injected a `mock_worker` returning a fixed string and never called a model. It now resolves every named model through the real model factory: an unconfigured name is reported as a failed perspective, an all-failed round is an explicit refusal rather than a consensus, and a partial round reports `1/2 models answered`. | `65464c1`; `models/moa/workers.py` |
| **In-process REPL** | ✅ Fixed. `python_repl` `exec()`s in the Gateway process and previously bypassed `allow_host_bash: false`. Now default-off behind `sandbox.allow_in_process_repl`. | `39416bf`, `tests/test_python_repl_boundary.py` |
| **RSI preview honesty** | ✅ Fixed. `run_rsi_cycle` hardcoded `0.72 -> 0.88` and `0.80 -> 0.89`, so it always reported an improvement. Scores are now `null` with the reason disclosed, and `regressed` is `null` rather than `false` — an unrun suite is not a clean one. It still only *proposes*; a real verdict needs the hidden-suite harness. | `65464c1`, `rsi/engine.py` |
| **Irreversible git** | ✅ Fixed. `force_push` and `git_push_protected` were already classified as approval-requiring by the autonomy guard, but `assert_requires_approval` had no production caller — so nothing stopped a force-push over `main`. The `bash` tool now refuses both, with `sandbox.allow_protected_git_push: true` to restore it. | `sandbox/git_push_guard.py` |
| **Code-tool workspace escape** | ✅ Fixed. `auto_test_and_repair` ran a model-supplied string through `shell=True` with no gate while `bash` required `allow_host_bash`, and `manage_code_checkpoint` wrote `root / target_files[i]` after `mkdir(parents=True)`, so `../..` both read and wrote outside the workspace. Roots are bounded (`sandbox.workspace_roots`) and a caller-supplied `test_command` now needs the same opt-in `bash` does. | `sandbox/workspace_boundary.py` |
| **Local sandbox as a boundary** | ⚠️ `LocalSandbox` is a path convention running at full user privilege; Windows has no Job Object. Real isolation requires AIO/E2B/BoxLite. The code says so itself. | `sandbox/AGENTS.md`: *"This is not a host filesystem security boundary."* |

### When to pick something else

Alpha is a large system. Choose a smaller tool when:

- **You only need a graph primitive inside an existing app** → use LangGraph
  directly, and optionally lift Alpha's harness engines as a library.
- **You want the widest messaging reach with the least setup** → OpenClaw.
- **You want a terminal-first personal agent with strong memory and container
  hardening** → Hermes Agent.
- **Your job is autonomous code review or PR fixing** → OpenHands is leaner, and
  today it is genuinely leaner on exactly that axis (see Known gaps).
- **You want a pure library with zero UI and zero services** → LangGraph, Pydantic
  AI, or Agno.

### When to pick Alpha

Pick Alpha when you need *several* of these at once, self-hosted, on your own
infrastructure: long-horizon autonomous execution · deep research with verifiable
citations · multi-agent delegation with leased work and budgets · sandboxed code ·
persistent memory with provenance · a real UI *and* a Windows desktop app ·
messaging-platform reach · cost ceilings and safety controls you can audit.

Pick something else if the thing you need most is **shipping code through a review
and merge pipeline** — that is Alpha's largest honest gap, not a footnote.

> Detailed, cited comparison: **[docs/COMPARISON.md](docs/COMPARISON.md)**.

---

## What can you build with Alpha?

Concrete, end-to-end jobs that ship on this platform — each maps to a documented
subsystem.

| Goal | What Alpha does | Read |
| :--- | :--- | :--- |
| **Autonomous research reports** | 5-pass search (discovery → evidence → falsification → verification → synthesis), gap filling, and an explicit `[S1]`-style citation contract with per-source status | [docs/DEEP_RESEARCH.md](docs/DEEP_RESEARCH.md) |
| **Autonomous coding & repair** | AST-verified edits, git shadow checkpoints with 1-click rollback, test-and-repair loops, repo twin previewing, AST-grep search/rewrite | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| **Goal-driven control plane** | **APEX Autopilot**: dispatch an objective through the Gateway `RunManager`, observe run status and token usage, submit measured evidence for every acceptance criterion, and let an administrator request a bounded replan after reviewing a terminal failure. Goal trees and linked session decisions preserve owner boundaries. Every enabled profile has unlimited token, tool-call, and runtime spending ceilings; operational capacity, approvals, and the shared emergency stop remain. Child calls inherit the persisted APEX policy; durable batch leases share a per-session cap across batches and Gateway workers, while ordinary task and batch counts are not yet combined. Automatic evidence collectors are not included | [docs/APEX_AUTOPILOT.md](docs/APEX_AUTOPILOT.md) |
| **Event-driven execution governance** | The Alpha Mod Kernel runs ordered policy middleware on lead-agent tool and model lifecycle events, with first-party emergency-stop, risk, evidence, routing, and failure controllers. Persistent approval holds and durable event replay are not implemented; see the [production inventory](docs/PRODUCTION_READINESS_INVENTORY.md) | [docs/ALPHA_MOD_KERNEL_MASTER_SPECIFICATION.md](docs/ALPHA_MOD_KERNEL_MASTER_SPECIFICATION.md) |
| **A team of agents on one project** | Bot roster, SOUL protocol, private inboxes, DMs, group chat rooms, live Kanban board, project constitutions, ADRs, resource locks | [docs/WORKFORCE.md](docs/WORKFORCE.md) |
| **A community of agent groups** | Nest group rooms inside group rooms at any time, staff them by rule instead of by name, inherit membership from a parent, and split direct / inherited / rule-matched members in the roster | [AGENTS.md](AGENTS.md#nested-groups-the-community-shape) |
| **Scheduled / recurring agents** | Cron scheduler with wake gates, blueprints, incident tracking, and auto-pause; GitHub webhook triggers | [docs/PRODUCTION.md](docs/PRODUCTION.md) |
| **A support/ops agent on chat** | Telegram, Slack, Feishu/Lark, WeChat, WeCom, DingTalk, Discord, Buzz, Signal | [docs/API.md](docs/API.md) |
| **An OpenAI-compatible endpoint** | Drop-in `POST /api/compat/openai/chat/completions` for your own clients | [docs/API_REFERENCE.md](docs/API_REFERENCE.md) |
| **Hands-free voice** | Local Whisper transcription + local Piper speech (Kokoro natural voice on faster hosts) + openWakeWord wake word, sentence-level streaming, no paid speech API | [docs/VOICE_CONVERSATION.md](docs/VOICE_CONVERSATION.md) |
| **A local-first agent mesh** | Alpha-to-Alpha peer network: LAN UDP discovery, explicit pairing, HTTP/WebSocket delivery, SQLite conversations | [docs/ALPHA_PEER_NETWORK.md](docs/ALPHA_PEER_NETWORK.md) |
| **Literature reviews & papers** | PRISMA-compliant systematic review and academic peer-review skills | [skills/public/](skills/public/) |
| **Data and chart work** | Sandboxed Python/REPL, data-analysis skill, chart-visualization skill | [skills/public/](skills/public/) |

More, including research, ops, and content workflows:
**[docs/USE_CASES.md](docs/USE_CASES.md)**.

---

## 60-second quickstart

### Windows — one click

```bat
git clone https://github.com/itsPremkumar/alpha.git
cd alpha
install.bat
```

`install.bat` provisions everything itself — uv, CPython 3.12, pnpm, and the
nginx build that provides the `:2026` entry point — installs both dependency
trees, creates the config files with a generated secret, offers to register
Windows autostart, then **starts Alpha and verifies it** (Gateway health, the
launcher process, and the watchdog chain) before reporting success. No
administrator rights; nothing is written outside this folder.

While running, the Windows launcher restarts a service when its listening port
disappears, even if a wrapper process is still alive; the independent watchdog
continues to supervise the launcher and full stack.

Open **<http://localhost:2026>**. Later: `start.bat` and `stop.bat`.

### macOS / Linux / manual

```bash
git clone https://github.com/itsPremkumar/alpha.git
cd alpha

make config      # config.yaml (app + every model setting) + .env + extensions_config.json from templates
make install     # backend (uv) + frontend (pnpm) dependencies
make dev         # start Gateway :8001, Frontend :3000, Nginx :2026
```

You need Python 3.12+, Node.js 22+, `uv`, `pnpm` and Git first. Full matrix and
the production paths: [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md).

### Add a model

Alpha boots with a placeholder model, so open **<http://localhost:2026>**, add a
provider and API key under `models:` in `config.yaml` (e.g. OpenRouter), and
restart. The file is created for you and is gitignored.

If nginx could not be installed, Alpha still runs: Gateway on `:8001`, web UI on
`:3000`. The launcher says which ports are live rather than advertising `:2026`.

### Docker (the whole stack, reproducibly)

```bash
git clone https://github.com/itsPremkumar/alpha.git && cd alpha
cp .env.production.example .env     # optional: on a fresh clone `make up` generates secrets
make config
make prod-check                    # pre-flight: versions, config files, secrets
make up                            # build + start; waits for the Gateway health probe
# browser: http://localhost:2026
make down                          # stop
```

### Windows desktop app

```powershell
cd electron
npm install
npm run dist
# -> electron\dist\Alpha-Setup-2.1.0.exe  (per-user, no admin rights)
```

The installer bundles its own Node.js and `uv`; end users need nothing
pre-installed. First launch provisions CPython and creates the backend venv under
Electron's per-user data directory. That directory is derived at runtime from
`app.getPath('userData')`, so rather than hardcoding a path that changes between
dev and packaged runs, read it from the app: the tray/menu **User data** entry
opens it directly, and the project config lives at `<userData>/project/config.yaml`.
Details: [electron/README.md](electron/README.md).

### Non-interactive / CI setup

```bash
ALPHA_SETUP_PROVIDER=openrouter \
ALPHA_SETUP_API_KEY="$OPENROUTER_API_KEY" \
make setup SETUP_ARGS=--non-interactive
```

### Interactive setup wizard

```bash
make setup     # guided: prerequisites, config, deps, first launch
make doctor    # verify the environment
```

---

## Feature catalog

<details open>
<summary><b>1. Autonomous deep research &amp; knowledge synthesis</b></summary>

- **5-pass search pipeline** — Discovery, Specific Evidence, Adversarial
  Contradiction, Fact Verification, Strategic Synthesis.
- **Targeted knowledge-gap filling** — detects missing metrics, benchmarks, or
  architectural trade-offs and runs a bounded follow-up stage.
- **Adversarial source juxtaposition** — pairs supporting and falsification
  evidence for review without claiming a contradiction that was not established.
- **Explicit citation contract** — deterministic anchors (`[S1]`, `[S2]`, …) with
  each source marked verified / unsupported / unverified / not-checked.
- **`deep_research` tool** — callable by any agent or workflow; writes artifacts
  under the thread's outputs directory.
- **AgentEye live-source plane** — a curated 39-function free-source catalog
  (academic, developer, package, government, scientific, knowledge, media,
  social) with operator allowlists, bounded fan-out, SSRF-safe fetching, and
  honest provider failures plus DDGS fallback.
- **`deep-research` subagent preset** — direct delegation with an expanded
  150-turn budget and strict citation guidelines.

→ [docs/DEEP_RESEARCH.md](docs/DEEP_RESEARCH.md)
</details>

<details>
<summary><b>2. Swarm orchestration &amp; multi-agent workforce</b></summary>

- **Bot roster & SOUL protocol** — registered autonomous bots with distinct
  personalities, isolated system prompts, private inboxes.
- **Transactional bot forge** — `bot_roster(action="forge")` builds a complete
  bot (persona, approvals, routines, journal) as a single transaction: it
  refuses duplicate roles and unaffordable schedules *before* creating
  anything, rolls the whole build back if any step fails, and only reports a
  bot alive after it answers a smoke test. Approvals and the sandbox are
  written into its SOUL at birth.
- **Work journal & blocker roll-up** — every bot keeps an append-only work
  journal that refuses credentials and chain-of-thought;
  `bot_roster(action="waiting_on")` answers "anything waiting on me?" across
  the whole roster in one line.
- **Secret-scanned bot templates** — export a bot to a shareable
  `.alphabot.json` (design only: never chats, operator facts, or journals) and
  import one back through the same scanner at both doors; a detected secret
  blocks the transfer and names the field, never the value.
- **Bot Forge Doctor** — `bot_roster(action="doctor")` checks the installation
  itself: open blockers, over-frequent routines, shell-capable bots running on
  the real host, missing journals, and unusable sandbox backends.
- **Bot mode DMs** — `POST /api/bots/{name}/dm`, fire-and-forget, server-side
  attribution.
- **Per-bot model configuration** — each bot chooses its own primary model, an
  ordered fallback chain, a model-counselling panel and a mixture panel, edited
  on its detail page with a live resolved-plan preview. `config.yaml` stays the
  only place models are *declared*: a profile may only name models from
  `models[]`, and a name that is not there is refused with every other problem
  in the block in a single response. See
  [docs/BOT_MODEL_CONFIG.md](docs/BOT_MODEL_CONFIG.md).
- **Multi-agent group chat & swarms** — collaborative rooms where specialized
  bots challenge assumptions and produce unified deliverables.
- **Swarm v2 DAG runtime** — atomic checkpoints, ordered JSONL audit events,
  owner-scoped admission, idempotent creation, lease-fenced task attempts, retry
  backoff, restart recovery.
- **Automatic topology selection** — `mode="auto"` measures the DAG it builds and
  picks the shape (map-reduce, scatter/gather, hierarchical) from measured width,
  depth, and coupling, then escalates coordination machinery only as complexity
  warrants. Every choice is recorded with the candidates it rejected, so the
  selection is auditable rather than a silent default. An explicit `mode` is
  always honored as given.
- **Deliberation & failure telemetry** — a sequential test that stops early once
  evidence is decisive and reports *unresolved* rather than guessing, guarded
  against one-sided panels and copy-cat votes; plus budget, rework-loop, and
  information-gain signals that say "unknown" when they genuinely cannot be
  measured.
- **Bounded execution** — token / tool-call / wall-clock / task / replan budgets,
  adaptive provider concurrency, watchdog recovery, pause / resume / cancel, and
  explicit `budget_exhausted` / `stalled` states.
- **Typed communication & consensus** — a bounded blackboard of untrusted
  observations; evidence-backed votes, leader election, and acceptance
  verification that separates *execution success* from *verified delivery*.
- **Swarm operations surface** — REST + SSE for claims, leases, revisions, events,
  messages, replans, progress, and leader/resource telemetry; the `swarm` tool
  exposes the same to authorized agents, plus `strategy` (why this mode was
  chosen), `telemetry`, and `trace` (ranked advisory context workers leave each
  other).
- **Live collaborative Kanban** — agents create, assign, transition, and audit
  cards on a shared board.
- **Agent-to-Agent (A2A) messaging** — structured protocol for peer requests and
  distributed coordination.
- **Project workforce layer** — agent↔project membership, presence, resource
  locking, project constitutions, and ADR memory.

Swarm state is atomic JSON checkpoints plus append-only JSONL events —
restart-recoverable for one Gateway process. A multi-worker deployment must
provide a shared SQL lease repository before claiming cross-process
exactly-once execution.

→ [docs/WORKFORCE.md](docs/WORKFORCE.md)
</details>

<details>
<summary><b>3. Continuous execution harness, planners &amp; loops</b></summary>

- **Autonomous one-prompt planner** — turns an unformatted, complex prompt into a
  structured multi-step plan.
- **8-dimension plan mode** — clarity, safety, feasibility, reversibility, resource
  intensity, architectural impact, empirical evidence, mission alignment.
- **Mission hierarchy & work-queue DAG** — macro goals decomposed into
  dependency-ordered work queues.
- **Continuous goal engine & integrity gate** — enforces verifiable completion
  criteria and blocks premature or hallucinated exits.
- **Ralph loop** — test-driven iterative self-healing until the suite passes and
  architectural invariants hold.
- **Boulder checkpointing & safe durable replay** — multi-session snapshots;
  disconnects never cancel work; resumes after crash, expired lease, restart, or
  recoverable model failure. Ambiguous external effects pause for confirmation so
  irreversible side effects are not duplicated.
- **Effect journal & reconciliation console** — the **Effects** view works the
  side-effect queue that pause exists to feed: counts by status and by level, the
  oldest unaccounted-for effect's age, and `POST /api/side-effects/{id}/reconcile`
  to record `confirmed_success` / `confirmed_failure` / `undetermined` with a
  required reason (`GET /api/side-effects`, `/summary`, `/{id}`). Digests only —
  arguments and results never cross the API — and the console **reads and
  reconciles only**: it never cancels, resumes or replays a run. Rows are
  owner-scoped, the verdict is admin-only, and an entry somebody already settled
  refuses with the ledger's own words rather than taking a second verdict. One
  effect family (durable MCP-task submit) announces to the ledger today; the
  remaining families stay named in the ratchet that keeps that gap honest.
- **Sentinel repair loop** — the **Sentinel** view reads the autonomous repair
  loop end to end: the aggregate reading (`GET /api/autonomy/sentinel/analytics`)
  folds the durable journal into totals, a per-fault-kind **verdict** derived
  from the statuses that kind actually reached, and the fingerprints that came
  back; `GET /sentinel/kinds` declares which fault kinds carry a repair
  strategy; and `GET /sentinel/escalations` works the human handoff queue with
  admin-only `acknowledge` / `resolve`. A repair pass is opt-in per click,
  checkpoints before it edits, and reverts anything that comes back red — an
  unknown fault kind is escalated, never guess-fixed, and auto-push is off.
  Numbers nobody measured read as *not reported*, never as `0`
  ([docs/SENTINEL.md](docs/SENTINEL.md)).
- **Kibitzer metacognitive supervision** — background supervision that detects
  loops, thrashing, and prompt drift.
- **Dynamic workflow plane** — intent perception, capability discovery, task
  decomposition, bounded DAG waves, typed graph patches, approval gates,
  retry/replan, evidence-gated replay, and saga compensation via
  `POST /api/workflows/dynamic/{perceive,execute}`. Node attempts hold a
  **durable, fence-checked lease**, so a worker that dies mid-node is
  reconciled instead of stranding the run, a late result is discarded rather
  than written through, and `POST /api/workflows/runs/{run_id}/recover` folds
  the journal back into a live run after a crash. Failures are classified into
  nineteen retry-decision classes that bridge onto the existing recovery and
  work-unit reason vocabularies, and `GET .../plans/{version}/diff?base={n}`
  reports structural vs runtime changes between plan revisions. A node's
  declared `verification_cmd` now **executes** before its result reaches the
  run: it resolves to a host-registered verifier or an allowlisted `alpha.`
  path, a shell command runs only through an operator-bound executor (absent by
  default, so it reports `not_run` rather than spawning anything), and a
  `failed` or `unresolved` verdict blocks the node while `not_run` completes it
  without ever being recorded as a pass. The built-in
  digest executor is deliberately a **local graph projection**, disclosed as
  `execution_label="local_digest_projection"` with `acceptance_passed=false`
  until a real executor is bound.

→ [docs/RUN_RECOVERY.md](docs/RUN_RECOVERY.md) ·
[docs/architecture/durable-runtime.md](docs/architecture/durable-runtime.md) ·
[docs/DYNAMIC_WORKFLOWS.md](docs/DYNAMIC_WORKFLOWS.md) ·
[docs/ALPHA-WORKFLOW-ARCHITECTURE.md](docs/ALPHA-WORKFLOW-ARCHITECTURE.md) ·
[docs/ALPHA-WORKFLOW-CURRENT-STATE.md](docs/ALPHA-WORKFLOW-CURRENT-STATE.md)
</details>

<details>
<summary><b>4. Frontier cognitive intelligence &amp; optimization</b></summary>

- **Agentic Variation Operators (AVO)** — evolutionary prompt and strategy
  mutation driven by compiler-grounded feedback.
- **Mixture of Agents (MoA)** - fans a question out to several models in parallel,
  redacts PII and secrets on the way in and out, and aggregates the independent
  answers. Every model named must exist in `config.yaml` -> `models[]`; one that
  does not resolve is reported as a failed perspective rather than replaced by
  invented text, and an all-failed round returns a refusal instead of a consensus.
  expectations, and downstream receiver perspectives.
- **Epistemic belief evaluation** — checks claims against an empirical
  ground-truth base to flag unsubstantiated assumptions.
- **Dreaming consolidation** — folds ephemeral episodic traces into a semantic
  knowledge graph during idle periods.
- **Strategic discipline council** — multi-perspective invariant verification, gap
  analysis, and policy compliance audits.
- **Quality council & evidence matrix** — deliberates artifact quality and
  validates finish-first empirical evidence before declaring work complete.
- **Consequence simulation** — models negative outcomes, side effects, and blast
  radius before an irreversible action runs.
- **System One decision models** — provider-neutral typed `choice` / `score` /
  `noul` decisions (hosted Jev or self-hosted Laya) with confidence gating and
  deterministic fallback.
- **Staged group deliberation (the War Room engine)** — when one agent's answer is
  not enough, a bounded room runs several agents through a staged sequence under a
  quorum policy (`all` / `any` / `majority` / `supermajority`). Agreement is
  measured over the *claims* participants state, so silence is never counted as
  agreement, the minority view is preserved verbatim, and correlated voters on one
  model collapse to a single voice. Ten deliberation strategies are available
  (`council`, `debate`, `red_team`, `expert_panel`, …) and `auto` routes through the
  deliberation classifier. Open the **Deliberation** tab to read every stage,
  receipt, quorum tally, dissent and transcript of a run.
  A run is never reported as *verified* — a quorum establishes that agents agreed,
  not that they were right.
- **Cross-agent taint screening** — every contribution is screened for
  instruction-override and exfiltration patterns, a detected payload is redacted
  before it re-enters another participant's prompt, and an unaddressed match
  downgrades the run instead of letting it report a clean pass.
- **Intelligence control plane** — open the **Intelligence** view to read loop
  health, evidence ledger, journal integrity, replay reservoir and goal counts
  in one panel (`GET /api/intelligence/control-plane`). Every metric travels
  with its basis — `measured`, `unmeasured`, `unavailable` or `unowned` — so a
  figure nobody measures (mission success, cost per success) shows the reason
  it has no owner rather than a reassuring `0`, and an unreadable subsystem
  shows its failure reason rather than an empty card.

→ [docs/COGNITIVE_ENGINES.md](docs/COGNITIVE_ENGINES.md) ·
[docs/SYSTEM_ONE.md](docs/SYSTEM_ONE.md) ·
[docs/CONTINUAL_INTELLIGENCE.md](docs/CONTINUAL_INTELLIGENCE.md)
</details>

<details>
<summary><b>5. Code agentic core &amp; developer tooling</b></summary>

- **Pre-commit AST guardrail** — intercepts `write_file`, `str_replace`, and
  `hashline_edit` before disk commit, statically verifying Python / JSON / YAML
  syntax and rejecting broken edits with compiler feedback.
- **Git shadow checkpoints & 1-click rollback** — `refs/alpha-checkpoints/<cid>`
  before risky mutations, with `GET/POST /api/checkpoints`,
  `POST /api/checkpoints/{id}/rollback`, and `GET /api/checkpoints/{id}/diff`.
- **Automated test & repair** — runs test commands, parses tracebacks, isolates
  root cause, applies verified fixes.
- **Repository map AST generator** — whole-codebase structural dependency maps.
- **AST-grep search and rewrite** — structural search and rewrite for TypeScript,
  Python, Go, Rust, and C++.
- **Repo twin inspection** — a shadow sandbox twin previews changes before they
  touch your working tree.
- **Hashline line-level editing** — deterministic line-based read/edit that avoids
  multi-line merge drift.
- **Sandboxed Python & Bash REPL** — secure execution for analysis, scripts, and
  system commands.
- **Visual artifact & UI verification** — renders and inspects generated widgets,
  canvases, and media artifacts.

→ [docs/EXTENSIONS.md](docs/EXTENSIONS.md)
</details>

<details>
<summary><b>6. Security enclave &amp; governance</b></summary>

- **Astra security enclave & scoped credential vault** — credentials are scoped
  per tool, never handed to the model in the clear.
- **Smart command approval gate** — risk scoring with explicit operator
  verification for high-impact terminal commands.
- **Emergency stop (Estop)** — a global pause sentinel. ⚠️ **Scope is narrower than
  the name suggests: today it gates the recursive-self-improvement cycle only.** It
  does not stop the run worker, subagents, the admission controller, or the
  background loops; see *Known gaps* above. It is a freeze switch for one
  subsystem, not a fleet kill switch.
- **Trajectory flight recorder** — the recorder, taxonomy and coverage table exist
  (18 layers) and are exercised by tests, but ⚠️ **no writer is installed in
  production**, so nothing is recorded by default. The run-event feed and
  `X-Trace-Id` correlation *do* work; span-level tracing is off and not reachable
  from `config.yaml`.
- **Universal artifact lineage** — hash-linked provenance of every generated
  file, code, and document (SHA-256 content digests, stored locally; not a
  cryptographic attestation).
- **Deterministic token budgeting & cost telemetry** — per-run ceilings and
  real-time spend with cache-aware pricing.

→ [docs/SECURITY.md](docs/SECURITY.md)
</details>

<details>
<summary><b>7. Omnichannel messaging hub &amp; integrations</b></summary>

- **Nine channels** — Telegram, Slack, Feishu/Lark, WeChat, WeCom, DingTalk,
  Discord, Buzz, Signal — bidirectional, driving the same agent runtime.
- **Scheduled automations & blueprints** — cron schedules, pre-flight wake gates,
  incident tracking, and auto-pause.
- **OpenAI-compatible chat gateway** —
  `POST /api/compat/openai/chat/completions` for drop-in client compatibility.
- **GitHub webhook automations** — agent invocations from pull requests, issues,
  comments, and releases.

→ [docs/API_REFERENCE.md](docs/API_REFERENCE.md)
</details>

<details>
<summary><b>8. Presentation layer: desktop &amp; web UI</b></summary>

- **One-click Windows desktop app** — Electron shell with bundled Node.js and
  `uv`; launches straight into chat with no setup wizard.
- **Next.js 15 web workspace** — Chat, Overview, Workforce, Projects, Kanban,
  Skills, Peers, and Settings views.
- **Agent response workspace** — readable terminal, file-diff, search, browser,
  artifact, and subagent cards; transcript search and Markdown export; and a
  side pane for the files, commands, pages, and artifacts a run produced.
- **Complete on-device chat history** — uncapped IndexedDB archive, full server
  history pagination, migration from the old capped cache, and honest degraded
  states (a banner when you are reading the local copy, a partial-load notice, and
  streaming answers archived without being painted into a conversation you
  switched away from). JSON backup/restore moves the whole archive between
  machines.
- **Bot-led multi-bot projects** - every bot profile can create a project with
  itself as lead; other bot profiles can be added, assigned a role, and managed
  together, with every change confirmed by re-reading the server roster.
- **Projects tab, list to end to end** - every project on the installation is
  listed, with single-agent and team crews distinguished from the roster the
  server reports. `View more` reads one project in full: lifecycle state, crew
  and shared room, coordination policy, every conversation, shared memory and
  the role-filtered context, constitution, decision log and event feed, locks,
  handoffs, approvals, checkpoints, the 20-subsystem War Room aggregate, and the
  autonomy subsystems. Each subsystem is read independently, so one that fails
  says so instead of hiding the rest; counts the server did not send stay
  unknown rather than showing as zero.
- **Free local real-time voice** — browser mic streaming, local Whisper interim +
  final transcription, VAD turn endpointing, normal SSE agent streaming,
  sentence-level local Piper playback, an optional more natural Kokoro voice on
  faster hosts, on-device openWakeWord wake word, hands-free resume, and no
  paid speech API.
- **Milo the lion companion** — a local inline-SVG companion with articulated
  animation, bounded roaming, petting, and an optional always-on-top desktop
  window that forwards no prompt or conversation data.
- **Guarded GitHub source auto-update** — published-release discovery, clean
  worktree + fast-forward enforcement, backup refs, detached restart, health
  verification, and automatic rollback. Disabled by default.
- **Interactive canvas & generative UI** — inline interactive HTML/React widgets,
  live SVG diagrams, KaTeX math, and data tables.

→ [docs/VOICE_CONVERSATION.md](docs/VOICE_CONVERSATION.md) ·
[docs/LION_COMPANION.md](docs/LION_COMPANION.md) ·
[docs/AUTO_UPDATE.md](docs/AUTO_UPDATE.md)
</details>

<details>
<summary><b>9. Self-knowledge, durability &amp; autonomous operations</b></summary>

- **Self-inventory plane (self-knowledge)** — one bounded answer to "what can Alpha
  do?": eleven read-only registries (tools, skills, MCP, models, bots, commands,
  capabilities, engines, wiring, memory) behind the `alpha_capability` tool and
  `GET /api/intelligence/inventory`. A source that cannot be read reports
  `count: null`, never `0`, and every descriptor carries `health: unverified`,
  because listing a capability is not executing it.
- **Code index** — bounded, query-driven symbol lookup returning names, signatures,
  `path:line` and one-line docstrings — deliberately **never a function body** (an
  interface map measured 67.8% reuse against 29.2% from a source dump), with
  TypeScript rows labelled `extraction="regex"` so a regex signature is never
  presented as parser-derived.
- **Cognitive memory** — owner-server-resolved storage under
  `users/{owner}/cognitive_memory`, with a per-owner/per-directory process cache
  and atomic fsync-backed snapshots; a store that cannot be read fails closed
  instead of bootstrapping over existing data.
- **ReasoningBank** — durable, evidence-gated procedure memory: strategies that
  worked are stored with their verdict and measured wins/attempts, then recalled
  deterministically for similar tasks. The `ralph_loop` self-improvement tool is
  its production consumer.
- **APEX executive control plane** — one autonomy contract, one bounded decision
  cycle, one read-only status projection and one control surface over engines that
  already exist: additive and deliberately thin, never a second execution path. The
  emergency stop, terminal-state and approval-gate refusals are enforced rather
  than suggested.
- **Durable runtime** — the guarantee that a process, UI, network, provider or
  Windows restart never becomes a task failure: session lifecycle, connectivity as
  a first-class state, a per-effect `UNKNOWN` + reconciliation ledger, a
  crash-loop-bounded process supervisor, and an ordered, honestly reported shutdown.
- **Workflow fork, dry run & templates** — fork a new run from any point in an
  event history without repeating inherited work, dry-run a graph on a throwaway
  engine (zero side effects, zero tokens, labelled `dry_run_simulation`), and gate
  templates through `draft → verified → promoted`.
- **Failure classification** — 19 deterministic `NodeFailureClass` values (auth,
  rate limit, security block, worker lost, …) answering "retry — and is this the
  same fault?", each disclosing the rule that matched and bridging onto the
  existing recovery vocabulary instead of adding an error-code taxonomy.
- **Company OS** — durable multi-tenant organizations *indexed over* real
  subsystems rather than duplicating them: employees are bot profiles, projects are
  project rows, work items are Kanban cards, rooms are group rooms — and every
  figure carries a `MeasurementBasis`, because a proposal is never an action.
- **Autonomous AI Software Enterprise** — C-Suite hierarchy with department
  capability contracts, mission-to-sprint DAG execution, RFC/blackboard consensus
  gating, a token treasury with circuit breakers, and multi-sig release
  attestations.
- **Nested groups** — "visibility may fan out to many parents; authority is at most
  ONE": `parents` is a list while `authority_parent` is a single room, so policy
  inheritance is a pointer rather than a negotiation between rooms. Relay copies a
  message with `forwarded_from` provenance (never reusing the source id) and is
  bounded by a hop guard.
- **Alpha-to-Alpha peer network** — cross-installation identity, discovery,
  pairing, transport, delivery receipts and DM bridging scoped to one
  installation. Discovery grants no credentials, pairing uses a high-entropy
  out-of-band code, and libp2p reports *unavailable* until a real authenticated
  adapter is wired.
- **Free model gateways & fail-closed routing** — keyless `alpha-free` routing
  over daily-refreshed free catalogs (free is **computed**, not trusted), with
  automatic failover, while a routing table that resolves to nothing returns an
  empty chain plus a reason instead of an invented model.
- **System One computer control** — opt-in Windows desktop control where the model
  returns only semantic element indexes — never coordinates, selectors, keys or
  typed text — which are re-observed and locally resolved before a sentinel-guarded
  dispatch; low-confidence, stale or shadow decisions dispatch nothing. Disabled by
  default.

→ [docs/SELF_AWARENESS.md](docs/SELF_AWARENESS.md) ·
[docs/MEMORY.md](docs/MEMORY.md) · [docs/APEX_AUTOPILOT.md](docs/APEX_AUTOPILOT.md) ·
[docs/SENTINEL.md](docs/SENTINEL.md) ·
[docs/ALPHA_PEER_NETWORK.md](docs/ALPHA_PEER_NETWORK.md) ·
[docs/architecture/durable-runtime.md](docs/architecture/durable-runtime.md) ·
[docs/DYNAMIC_WORKFLOWS.md](docs/DYNAMIC_WORKFLOWS.md) ·
[docs/WORKFORCE.md](docs/WORKFORCE.md)
</details>

<details>
<summary><b>Full subsystem map (119 harness engines)</b></summary>

Each of the 119 engine packages under `backend/packages/harness/alpha/` (counted by
`backend/scripts/generate_feature_manifest.py`; `backend/` and `scratch/` sit there
but are not engines) is a dedicated engine:

`action` `agency` `agent` `agents` `authz` `autoconfig` `avo` `benchmarks`
`blackboard` `bots` `browser` `canvas` `capabilities` `channels` `coding`
`commands` `community` `company` `company_os` `computer_use` `config` `consequence` `context`
`council` `critic` `debugging` `deepagent` `deliberation` `diagnostics` `editing`
`enterprise` `epistemics` `errors` `evaluation` `events` `evidence` `evolution`
`extensions` `goals` `governance` `groups` `guardrails` `harness` `integrations`
`jobs` `kanban` `knowledge` `learning` `ledger` `lineage` `mcp` `media` `memory`
`metacognition` `metacompiler` `mission` `missions` `models` `multimodal`
`observability` `ops` `orchestration` `orchestrator` `peer_network` `perpetual`
`persistence` `planning` `policy` `projects` `protocols` `reasoning` `recovery`
`reflection` `reproduction` `research` `rsi` `rules` `runtime` `safety` `sandbox`
`scheduler` `script_bridge_child` `security` `selfrepair` `skills` `state`
`streamjson` `subagents` `supervision` `swarm` `synthesis` `system1` `testing`
`tools` `tracing` `trajectory` `tui` `uploads` `utils` `verification`
`wire_contracts` `workflow` `workspace_changes`

Verify the list against the tree at any time:

```bash
ls backend/packages/harness/alpha | grep -v __pycache__ | wc -l
```

The authoritative, machine-checked capability registry is
[`contracts/feature_manifest.json`](contracts/feature_manifest.json), generated by
`backend/scripts/generate_feature_manifest.py`; CI fails on drift. Per-engine
detail: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
</details>

<details>
<summary><b>24 public skills</b></summary>

`academic-paper-review` · `bootstrap` · `chart-visualization` ·
`claude-to-alpha` · `code-documentation` · `consulting-analysis` ·
`data-analysis` · `deep-research` · `find-skills` · `frontend-design` ·
`github-deep-research` · `image-generation` · `music-generation` ·
`newsletter-generation` · `podcast-generation` · `ppt-generation` ·
`project-cartographer` · `skill-creator` · `skill-reviewer` · `surprise-me` ·
`systematic-literature-review` · `vercel-deploy-claimable` · `video-generation` ·
`web-design-guidelines`

→ [docs/SKILLS.md](docs/SKILLS.md) and [`skills/public/`](skills/public/)
</details>

---

## Deployment options

| Option | Command | Best for | Public port |
| :--- | :--- | :--- | :--- |
| **Docker Compose** | `make up` / `make down` | Reproducible team/server deploys | `2026` |
| **Windows desktop** | `electron/` → `npm run dist` | Single-user desktop use | `2026` (loopback) |
| **Bare metal** | `make dev` | Active development with hot reload | `2026` |
| **Kubernetes** | `deploy/helm/alpha` | Cluster deployment | via ingress |
| **CI / headless** | `make setup SETUP_ARGS=--non-interactive` | Automated provisioning | — |

### Service topology

| Service | Port | Role |
| :--- | :--- | :--- |
| **Nginx** | `2026` | **The only public entry point** — open this in the browser |
| **Gateway API** | `8001` | FastAPI REST API + embedded LangGraph-compatible agent runtime |
| **Frontend** | `3000` | Next.js workspace |
| **Provisioner** | `8002` | Optional — only when the sandbox runs in provisioner/K8s mode |

Nginx proxies `/api/*` to the Gateway (rewriting `/api/langgraph/*` onto native
routes) and serves the frontend. Both compose files publish nginx as
`"${BIND_HOST:-127.0.0.1}:${PORT:-2026}:2026"` — **loopback by default**; a bare
`"${PORT}:2026"` binds `0.0.0.0`. Do not add a published port without an explicit
bind address.

→ [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)

---

## Configuration

Three files, three responsibilities:

| File | Scope | Contents | Committed? |
| :--- | :--- | :--- | :--- |
| `config.yaml` | Platform core | Models, presets, tool groups, sandbox tier, databases, IM channels, autonomy loops | ❌ generated by `make config` |
| `extensions_config.json` | Extensions | MCP servers + enabled skills (API-editable at runtime) | ❌ generated by `make config` |
| `.env` | Secrets | API keys, DB connection strings, encryption secrets | ❌ **never commit** |

Copy the templates first:

```bash
make config   # config.example.yaml -> config.yaml, extensions_config.example.json -> extensions_config.json
```

Rules of thumb:

- Reference secrets as `$ENV_VAR`, never as literals in `config.yaml`.
- A `models:` list with at least one entry is required or the runtime will not
  start.
- Third-party Python extensions go in the top-level `plugins:` list in
  `config.yaml` — deliberately **not** in the API-writable
  `extensions_config.json`, because that list causes code to be imported.
  Every mutation needs a Gateway restart.
- Background autonomy loops are gated by `config.yaml -> autonomy.loops`; an
  absent id means disabled, and all flags off means zero tasks.

→ [docs/CONFIGURATION.md](docs/CONFIGURATION.md)

### Reasoning effort (thinking depth)

The composer has an effort picker next to the model selector, so you can choose
how hard a model thinks per run instead of editing config: `Off`, `Minimal`,
`Low`, `Medium`, `High`, `Extra High`, `Max`, plus a `Default` that sends
nothing and lets the model decide. It is the same control as Claude Code's
`/effort`, Codex's `model_reasoning_effort`, and OpenCode's `/variants`, spoken
as one vocabulary.

You get the picker by declaring which levels a model actually serves:

```yaml
models:
  - name: claude-opus-4-7
    use: langchain_anthropic:ChatAnthropic
    model: claude-opus-4-7
    max_tokens: 32000        # hard cap on thinking PLUS reply
    supports_thinking: true
    reasoning_efforts: [low, medium, high, xhigh, max]
    default_reasoning_effort: high
```

That list is the contract, not a hint. The picker offers exactly those levels, a
request above the top is lowered to it (and logged), and a level you did not
declare is never sent — so you cannot pick something the provider rejects. Leave
`reasoning_efforts` out and the picker reports that the model has no effort
control, rather than showing a menu that would do nothing.

Alpha translates the level into whichever shape your provider wants —
`reasoning_effort` for OpenAI-compatible endpoints, `output_config.effort` for
Anthropic, `thinking_level` for Gemini, `reasoningConfig` for Bedrock, chat
template kwargs for vLLM — so you never configure the wire format yourself. One
caveat worth knowing: on Anthropic, `max_tokens` caps thinking *and* the reply,
so a small cap at High or above truncates the reasoning itself. Alpha warns when
it sees that.

→ [Reasoning effort in docs/CONFIGURATION.md](docs/CONFIGURATION.md#reasoning-effort-thinking-depth)

---

## Documentation

| Start here | For |
| :--- | :--- |
| [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) | Install and first run, every platform |
| [docs/FAQ.md](docs/FAQ.md) | Short, citable answers |
| [docs/COMPARISON.md](docs/COMPARISON.md) | Alpha vs LangGraph / AutoGen / CrewAI / OpenHands / Dify |
| [docs/USE_CASES.md](docs/USE_CASES.md) | End-to-end jobs and the subsystem behind each |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Runtime planes, middleware chain, engine map |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Every setting, resolved order, env vars |
| [docs/API_REFERENCE.md](docs/API_REFERENCE.md) | All 62 Gateway routers, SSE events, auth |
| [docs/SECURITY.md](docs/SECURITY.md) | Enclave, approvals, sandbox boundaries, threat model |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Docker, Helm, Nginx, Windows installer |
| [docs/PRODUCTION.md](docs/PRODUCTION.md) | Runbook, probes, `/api/ops/*`, disaster recovery |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Diagnostic flows and lock contention |
| [docs/MEMORY.md](docs/MEMORY.md) | Layered memory architecture |
| [docs/EXTENSIONS.md](docs/EXTENSIONS.md) | MCP + the extension contribution contract |
| [docs/WORKFORCE.md](docs/WORKFORCE.md) | Bots, DMs, swarms, projects, Kanban |
| [docs/GLOSSARY.md](docs/GLOSSARY.md) | Every Alpha term defined in one page |
| [docs/INDEX.md](docs/INDEX.md) | The complete, generated document index |
| [`llms.txt`](./llms.txt) | Compact machine context for AI agents |
| [`llms-full.txt`](./llms-full.txt) | Expanded machine context for AI agents |
| [`AGENTS.md`](./AGENTS.md) | Contributor guide (source of truth) |

Deep reference material lives in [`references/`](./references/README.md) — 45+
papers and architecture studies on agent harnesses, AVO loops, recursive
self-improvement, frontier coding agents, and multi-agent SDLC.

---

## Extending Alpha

Three extension surfaces, in increasing order of power:

1. **Skills** — a `SKILL.md` plus optional resources. Add to
   `extensions_config.json`. Lowest privilege, no restart needed for discovery.
2. **MCP servers** — any Model Context Protocol server over stdio, HTTP, or SSE.
   Configure in `extensions_config.json`; API-editable at runtime.
3. **Python extension packages** — contribute middleware, task lifecycle hooks,
   system-model observers, Gateway services, and FastAPI routers. Declared in the
   operator-controlled `plugins:` list in `config.yaml`; requires a Gateway
   restart, and both build hooks and extension code run with Gateway privileges.

A complete, runnable reference implementation of all five contribution kinds:
[`examples/alpha-extension-example/`](examples/alpha-extension-example/).

```bash
make extension-list
make extension-install SOURCE=...
make extension-enable NAME=...
make extension-disable NAME=...
make extension-upgrade SOURCE=...
make extension-remove NAME=...
```

→ [docs/EXTENSIONS.md](docs/EXTENSIONS.md)

---

## Quality gates

```bash
# Backend
cd backend && make test              # default suite (excludes live + blocking-IO)
cd backend && make test-blocking-io  # strict blocking-IO gate
cd backend && make lint              # ruff check
cd backend && make format            # ruff format

# Frontend
cd frontend && pnpm verify           # typecheck + unit tests
cd frontend && pnpm dev              # dev server (Webpack; --turbopack for Turbopack)

# Repository-level contracts
python scripts/generate_feature_manifest.py     # regenerate the capability manifest
python backend/scripts/check_tool_schemas.py    # after any tool signature change
python scripts/generate_docs_index.py --check   # docs index drift
bash scripts/verify_versions.sh                 # version lockstep gate
```

Three contracts are worth calling out because they are unusual and load-bearing:

- **`contracts/feature_manifest.json`** is generated from the live registries and
  pins all 136 tools, 68 routers, 44 middlewares, and 11 supervisor loops. CI fails
  on drift, so the documented capability counts cannot silently rot.
- **Tool runtime injection** — any `@tool` needing runtime access must declare
  `runtime: Runtime` as a bare required first parameter. Writing
  `Runtime | None = None` makes pydantic schema-generate `ToolRuntime`'s
  `Callable` fields and breaks the entire tool list for the model. Run
  `check_tool_schemas.py` after any tool signature change.
- **Event-loop discipline** — Gateway entry points are `async`, so a synchronous
  filesystem or config read on that path stalls every concurrent run in the
  process. `make test-blocking-io` is the strict gate that fails the build for it;
  offload such work with `asyncio.to_thread` (see `backend/AGENTS.md` →
  *Event-loop discipline*).

→ [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) · [CONTRIBUTING.md](CONTRIBUTING.md)

---

## Alpha Network: agent-to-agent communication

Alpha includes a separate **Alpha Network** workspace for discovering, pairing, and
messaging independently installed Alpha agents. The default free path is **LAN UDP
discovery + direct HTTP/WebSocket delivery + SQLite conversations** — no
Alpha-operated broker, VPS, or paid database. Optional mDNS (`zeroconf`) and a
GitHub Agent Card rendezvous extend the reach.

Supports direct, one-to-many, many-to-one, many-to-many, and broadcast sessions
with per-recipient delivery receipts. **Discovery is untrusted and never grants
access; pairing is explicit and uses a high-entropy out-of-band code.**

→ [docs/ALPHA_PEER_NETWORK.md](docs/ALPHA_PEER_NETWORK.md)

---

## FAQ

**Q: What is Alpha?**
**A:** An open-source autonomous multi-agent AI operating system: a LangGraph agent
runtime behind a FastAPI Gateway, with a Next.js 15 workspace and an Electron
Windows app, running long-horizon work with sandboxed execution, persistent
memory, 136 native tools, MCP extensions, and 24 public skills. MIT licensed,
self-hosted, no proprietary backend.

**Q: How do I install and run it?**
**A:** `make config` → `make install` → `make dev`, then open
<http://localhost:2026>. For a containerized stack use `make up`. For Windows,
build the Electron installer. Add at least one entry under `models:` in
`config.yaml` before the runtime will start.

**Q: Do I need a paid API key?**
**A:** Not necessarily. Alpha works with any provider you configure, including
local models through Ollama and self-hosted OpenAI-compatible endpoints. Local
speech (Whisper + Piper) needs no speech API. The only component that may cost
money is the model provider you choose.

**Q: Which LLM providers are supported?**
**A:** Any provider expressible in `config.yaml` — the shipped baseline is
OpenRouter's `unbiased/pareto` through `langchain_openai:ChatOpenAI`, and
OpenAI, Anthropic, Google Gemini, DeepSeek, Moonshot AI, MiniMax, StepFun, and
Ollama are all wired. Multi-provider routing, load balancing, and fallbacks are
built in.

**Q: Is Alpha production ready?**
**A:** Partially, and the repository says exactly where the line is. The run
lifecycle, thread-scoped auth, health probes (`/health`, `/health/ready`), sandbox
tiers, and registry drift gates are implemented and tested. Open items —
independent evidence verification, centralized policy grants, backup/restore
drills, and release scorecards — are tracked in
[docs/PRODUCTION_READINESS_INVENTORY.md](docs/PRODUCTION_READINESS_INVENTORY.md).
Read it before exposing a deployment.

**Q: How is Alpha different from CrewAI, AutoGen, or LangGraph?**
**A:** Those are libraries/primitives; Alpha is a finished, self-hostable agent
product built on the same ideas, with a UI, deployment, safety controls, memory
plane, research engine, and messaging channels included. Full matrix:
[docs/COMPARISON.md](docs/COMPARISON.md).

**Q: How is Alpha different from OpenHands or Claude Code?**
**A:** Those are coding agents. Alpha is not repo-bound or language-bound: research,
data, ops, docs, and chat platforms are first-class, and coding is one capability
among many.

**Q: What is the relationship to DeerFlow?**
**A:** Alpha is a re-architecture of the MIT-licensed DeerFlow base into a full
operating system. Upstream copyright and license notices are preserved. See
[Project provenance](#project-provenance).

**Q: How much does it cost to run?**
**A:** Alpha itself is free and has no hosted tier. Your costs are (a) your model
provider, (b) your own compute, and (c) optional extras such as ~550 MB of local
speech models or a provisioner VM. Alpha enforces per-run token ceilings and
reports cache-aware spend so this stays bounded.

**Q: Can I use it with my own tools?**
**A:** Yes — three ways: write a skill, connect an MCP server, or write a Python
extension package that contributes middleware, services, and routers. There is a
runnable reference implementation in `examples/`.

**Q: Is my data sent anywhere?**
**A:** No Alpha-operated backend exists. Prompts, threads, memory, checkpoints,
and artifacts stay in your runtime home / database; only the model providers you
configure receive the prompts you send them.

**Q: How do I get help?**
**A:** Run `make doctor` and `make prod-check`, read
[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md), then open an issue with the
redacted bundle from `make support-bundle`, the `X-Trace-Id` header, and relevant
Gateway/Frontend logs.

More answers: [docs/FAQ.md](docs/FAQ.md) · terminology:
[docs/GLOSSARY.md](docs/GLOSSARY.md)

---

## Contributing

Contributions are welcome, including first-time ones.

```bash
git clone https://github.com/itsPremkumar/alpha.git
cd alpha
make install
cd backend && make test
```

- **Read [`AGENTS.md`](./AGENTS.md) first.** It is the source of truth for repo
  orientation, commands, and conventions (`CLAUDE.md` is a thin import shim).
- Backend changes ship with tests in `backend/tests/` — TDD is mandatory there.
- Frontend changes ship with tests and must pass `pnpm verify`.
- Run `make format` (backend) before pushing; CI enforces `ruff format --check`.
- Keep docs in sync in the same change set: `README.md` for user-facing changes,
  the relevant `AGENTS.md` for architecture changes, `CHANGELOG.md` for releases.
- Version strings must stay identical across `backend/pyproject.toml`,
  `frontend/package.json`, and `deploy/helm/alpha/Chart.yaml`.

Good first issues are labelled on the [issue tracker](https://github.com/itsPremkumar/alpha/issues).
→ [CONTRIBUTING.md](CONTRIBUTING.md) · [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)

---

## Security

Do not open a public issue for a vulnerability. Use GitHub's private vulnerability
reporting on this repository, or contact the maintainer directly.
Security architecture, threat model, and hardening: [docs/SECURITY.md](docs/SECURITY.md) ·
[SECURITY.md](SECURITY.md).

---

## Project provenance

Alpha is built on and credits the following open-source work:

- **DeerFlow** (MIT) — the base project Alpha re-architects. Original copyright
  and license notices are preserved in [LICENSE](./LICENSE).
- **[LangGraph](https://github.com/langchain-ai/langgraph)** / LangChain — async,
  checkpointed, stateful agent graphs.
- **[OpenHands](https://github.com/All-Hands-AI/OpenHands)** — autonomous
  software-engineering agent patterns, reproduction, and episodic memory.
- **[Pydantic](https://github.com/pydantic/pydantic)** · **[FastAPI](https://github.com/fastapi/fastapi)** ·
  **[Next.js](https://github.com/vercel/next.js)** · **[Electron](https://github.com/electron/electron)** ·
  **[Tailwind CSS](https://github.com/tailwindlabs/tailwindcss)** — runtime and UI foundations.
- **Model providers** — OpenAI, Anthropic, Google Gemini, DeepSeek, OpenRouter,
  Moonshot AI, MiniMax, StepFun, Ollama.

Attribution research: [`references/`](./references/README.md) · subsystem notices:
[docs/THIRD_PARTY_MEMORY_NOTICES.md](docs/THIRD_PARTY_MEMORY_NOTICES.md).

---

## Citation

If you use Alpha in research or write about it, please cite it:

```bibtex
@misc{alpha2026,
  title        = {Alpha: An Open-Source Autonomous Multi-Agent AI Operating System},
  author       = {Kumar, Prem},
  year         = {2026},
  howpublished = {\url{https://github.com/itsPremkumar/alpha}},
  license      = {MIT},
  note         = {LangGraph agent runtime, FastAPI Gateway, Next.js 15 workspace}
}
```

Machine-readable metadata: [`CITATION.cff`](./CITATION.cff).

---

<div align="center">

**Alpha** — *Autonomous agentic intelligence, self-hosted and auditable.*
Architected and maintained by [Prem Kumar](https://github.com/itsPremkumar) ·
[MIT licensed](./LICENSE)

</div>
