# Alpha FAQ

Short, quotable, citable answers about [Alpha](https://github.com/itsPremkumar/alpha).
For agent-friendly context see [`/llms.txt`](../llms.txt) and
[`/llms-full.txt`](../llms-full.txt). Long-form guides live in the
[documentation index](README.md).

---

## Definition and scope

### What is Alpha?

Alpha is an open-source autonomous multi-agent AI operating system. An asynchronous
Python 3.12+ backend built on LangGraph and served by a FastAPI Gateway, paired with
a Next.js 15 web workspace and an Electron Windows desktop app. It executes
long-horizon tasks with sandboxed code execution, persistent memory, subagent
delegation, 130 native tools, MCP extensions, and 24 public skills, behind a single
Nginx reverse proxy on port `2026`. Maintained by
[Prem Kumar](https://github.com/itsPremkumar) under the MIT license.

### Is Alpha a framework or a finished application?

Both. It ships as a finished, self-hostable application, and its agent framework is
also importable as `agent-workspace-harness` (import name `alpha.*`) with 99 engine
modules you can use to build your own runtime.

### Is Alpha a chatbot?

No. A chatbot answers one prompt. Alpha has a durable run lifecycle, checkpointing
and recovery, a sandbox, a memory plane, budgets, approval gates, and an audit trail,
so it can keep working on a task after you close your laptop.

### What is Alpha *not*?

It is not a hosted SaaS, it is not repo-bound or language-bound like a coding CLI,
and it has no proprietary backend. No Alpha-operated broker, VPS, database, or
required telemetry exists.

---

## Requirements and installation

### What are the prerequisites?

Python 3.12+, Node.js 22+, `uv`, `pnpm`, and Git. Docker 24+ is optional and needed
for the containerized stack or the Docker sandbox tier. Windows users additionally
need PowerShell 5.1+ (built-in) and Git Bash. Full matrix:
[Getting started](GETTING_STARTED.md).

### How do I run Alpha locally?

```bash
make config     # copy config.example.yaml -> config.yaml, extensions_config.example.json -> extensions_config.json
make install    # install backend + frontend dependencies
make dev        # Gateway :8001, Frontend :3000, Nginx :2026
```

Open <http://localhost:2026>. Without `config.yaml` the services will not boot, and
you need at least one entry under `models:`. Full guide:
[Getting started](GETTING_STARTED.md).

### Which deployment option should I choose?

- **Windows desktop (Electron)** — a one-click app in `electron/` that bundles its
  own Node.js and `uv`. Best for single-user desktop use.
- **Docker Compose** — `make up` / `make down`, browser at `http://localhost:2026`.
  Best for reproducible team and server deployments.
- **Bare metal** — `make dev` with hot reload. Best for active development.
- **Kubernetes** — `deploy/helm/agent-workspace`. Best for cluster deployment.
- **CI / headless** — `make setup SETUP_ARGS=--non-interactive` with
  `AGENT_WORKSPACE_SETUP_*` environment variables.

### What are the ports and routes?

Nginx `:2026` is the single public entry point. It proxies `/api/*` to the Gateway
(`:8001`) — rewriting `/api/langgraph/*` onto native Gateway routes — and serves the
Frontend (`:3000`). The Provisioner (`:8002`) is optional and present only in
provisioner/Kubernetes sandbox mode. Direct access without Nginx: Gateway
`http://localhost:8001`, Frontend `http://localhost:3000`.

### What is the `make setup` wizard?

An interactive first-run wizard that checks prerequisites, generates config, installs
dependencies, and gets you to a running stack. Unattended:
`make setup SETUP_ARGS=--non-interactive`.

---

## Models, cost, and privacy

### Which LLM providers are supported?

Any provider you configure in `config.yaml`. The shipped baseline is `union-alpha`
via `langchain_openai:ChatOpenAI`, using OpenRouter's API slug
`stealth/union-alpha` with `$OPENROUTER_API_KEY` — note that the CLI-qualified
`openrouter/stealth/union-alpha` is *not* a valid OpenRouter API slug. OpenAI,
Anthropic, Google Gemini, DeepSeek, Moonshot AI, MiniMax, StepFun, and Ollama are
also wired, with multi-provider routing, load balancing, and fallbacks.

### Do I need a paid API key?

Not necessarily. Alpha works with local models through Ollama and with any
self-hosted OpenAI-compatible endpoint. Local speech (Whisper + Piper) needs no
speech API at all. The only component that may cost money is the model provider you
choose.

### How much does Alpha itself cost?

Nothing. Alpha is MIT licensed with no hosted tier. Your costs are the model
provider you configure, your own compute, and optional extras such as ~550 MB of
local speech models or a provisioner VM. Alpha enforces deterministic per-run token
ceilings and reports cache-aware spend so usage stays bounded.

### Where does my data go?

Nowhere Alpha-operated. Prompts, threads, memory, checkpoints, and artifacts stay in
your runtime home and database. Only the model providers you configure receive the
prompts you send them.

### Does Alpha collect telemetry?

There is no required telemetry and no Alpha-operated service to collect it.

---

## Capabilities

### What can the agent actually do?

Autonomous deep research (five-pass search with a citation contract and gap
filling); multi-agent swarms with group chat, DMs, and a live Kanban board;
one-prompt planning with an eight-dimension task evaluator; code repair with AST
verification, git shadow checkpoints, and one-click rollback; sandboxed Python and
Bash; browser automation; scheduled and webhook-triggered runs; local real-time
voice; and delivery to Telegram, Slack, Feishu/Lark, WeChat, WeCom, DingTalk,
Discord, Buzz, and Signal.

### How many tools, skills, routers, and middlewares ship?

130 native tools, 60 Gateway routers, 42 middleware layers, and 8 background
supervisor loops, plus 24 public skills and 99 harness engine packages. These
numbers are generated into
[`contracts/feature_manifest.json`](../contracts/feature_manifest.json) and enforced
by a CI drift gate, so they cannot silently rot.

### What is deep research, concretely?

A five-pass pipeline: discovery, specific evidence, adversarial contradiction, fact
verification, and strategic synthesis — followed by a bounded stage that targets
evidence still missing. Sources get deterministic `[S1]`, `[S2]` anchors, and each
is reported as verified, unsupported, unverified, or not checked, so a reader always
knows how much weight a claim carries.
→ [Deep research](DEEP_RESEARCH.md)

### What is the swarm runtime?

An owner-scoped, lease-fenced DAG runtime with durable atomic checkpoints, ordered
JSONL audit events, idempotent creation, retry backoff, restart recovery, and
explicit `budget_exhausted` / `stalled` states. It separates "the task ran" from
"the result was delivered and verified" via acceptance-sensitive aggregation.
→ [Workforce](WORKFORCE.md)

### What is System One?

A fast, structured, provider-neutral decision layer returning typed `choice`,
`score`, and `noul` results, with confidence gating and a deterministic fallback. It
uses a hosted model (Jev) or a self-hosted open-weights alternative (Laya).
→ [System One](SYSTEM_ONE.md)

---

## Extending Alpha

### How do I add models, MCP servers, or skills?

- **Models** go in `config.yaml` under `models:`, with keys referenced as `$ENV_VAR`.
- **MCP servers and skills** go in `extensions_config.json`, which is editable at
  runtime through the UI or `PUT`/`PATCH /api/mcp/config`.
- **Third-party Python extensions** go in the top-level `plugins:` list in
  `config.yaml`. That list is operator-controlled on purpose, because it causes code
  to be imported, and it is deliberately kept out of the API-writable
  `extensions_config.json`. Every mutation requires a Gateway restart.

→ [Extensions & MCP](EXTENSIONS.md)

### What can a Python extension contribute?

Middleware, task lifecycle hooks, system-model observers, Gateway services, and
FastAPI HTTP routers. A runnable reference package demonstrating all five is in
[`examples/agent-workspace-extension-example/`](../examples/agent-workspace-extension-example/).
Manage extensions with `alpha extensions install|upgrade|list|enable|disable|remove`
or the root `make extension-*` wrappers.

### Is it safe to install a third-party extension?

Only install from sources you trust. Build hooks and extension code execute with
Gateway privileges, so an extension is effectively trusted code. See
[Security](SECURITY.md).

### What are the 24 public skills?

`academic-paper-review`, `bootstrap`, `chart-visualization`,
`claude-to-agent-workspace`, `code-documentation`, `consulting-analysis`,
`data-analysis`, `deep-research`, `find-skills`, `frontend-design`,
`github-deep-research`, `image-generation`, `music-generation`,
`newsletter-generation`, `podcast-generation`, `ppt-generation`,
`project-cartographer`, `skill-creator`, `skill-reviewer`, `surprise-me`,
`systematic-literature-review`, `vercel-deploy-claimable`, `video-generation`, and
`web-design-guidelines`. → [Skills](SKILLS.md)

---

## Configuration and operations

### How is configuration layered?

Three files. `config.yaml` holds platform-core settings (models, tool groups, sandbox
tier, databases, IM channels, autonomy loops). `extensions_config.json` holds MCP
servers and enabled skills and is API-editable at runtime. `.env` holds secrets and
must never be committed. Copy both templates with `make config`; reference secrets
as `$ENV_VAR`, never as literals in YAML.
→ [Configuration](CONFIGURATION.md)

### How do I use the OpenAI-compatible API?

Point any OpenAI-compatible client at
`POST /api/compat/openai/chat/completions`. See the
[API reference](API_REFERENCE.md).

### How do I schedule recurring work?

The background scheduler, gated by `config.yaml -> scheduler.enabled`, with cron
occurrences, pre-flight wake gates, blueprint workflows, incident tracking, and
automatic pausing. A busy occurrence is persisted as `queued` and bounded by
`scheduler.queue_timeout_seconds` — it is never silently skipped, and waiting rows do
not count against `max_concurrent_runs`. → [Production runbook](PRODUCTION.md)

### What are the background supervisor loops?

`sentinel`, `perpetual`, `review_queue`, `skill_curator`, and `enterprise_heartbeat`,
all owned by a single lifecycle supervisor. They are declared in
`register_default_loops()` and gated by `config.yaml -> autonomy.loops`; an absent id
means disabled, and all flags off means zero tasks. `swarm` is telemetry-only and
on-demand. → [Architecture](ARCHITECTURE.md)

---

## Security and production readiness

### How is security handled?

An Astra security enclave with a scoped credential vault (secrets go to tools, never
into prompts), a risk-scoring command approval gate, an emergency stop, a
cryptographic trajectory flight recorder, artifact lineage tracing, deterministic
per-run token budgets, and per-thread sandbox isolation with three tiers (local
subprocess, Docker, Kubernetes). Never put secrets in `config.yaml` literals — use
`$VAR` plus `.env`. → [Security](SECURITY.md)

### Is Alpha production ready?

**Partially, and the repository is explicit about where the line is.** Implemented
and tested: the run lifecycle, thread-scoped auth and ownership, health and
readiness probes (`/health`, `/health/ready`), sandbox tiers, capability registry
wiring, and the strict blocking-IO gate. Still open: independent evidence
verification, centralized policy grants, backup/restore drills, and release
scorecards. Read
[Production readiness inventory](PRODUCTION_READINESS_INVENTORY.md) and
[Production runbook](PRODUCTION.md) before exposing a deployment.

### What are the known limits I should be careful about?

- Swarm and dynamic-workflow state is restart-recoverable for **one Gateway
  process**; a multi-worker deployment must supply a shared SQL lease repository
  before cross-process exactly-once execution can be claimed.
- The dynamic-workflow digest executor is a **local graph projection** and discloses
  `execution_label="local_digest_projection"` with `acceptance_passed=false` until a
  real executor is bound.
- A run can complete successfully and still be **unverified** until independently
  checked evidence is recorded.
- Peer-network SQLite is installation-scoped, not cross-process exactly-once.
- Agentic browser sessions are process-local; the Gateway refuses
  `GATEWAY_WORKERS > 1` when `browser_navigate` is configured.
- The current `frontend/package.json` declares `typecheck` and `verify`, not
  `check`, lint, or test scripts — use `pnpm verify`.

### How do I report a vulnerability?

Use GitHub's private vulnerability reporting on
[itsPremkumar/alpha](https://github.com/itsPremkumar/alpha), or contact the
maintainer directly. Do not open a public issue with exploit details.
→ [Security policy](../SECURITY.md)

---

## Comparisons and lineage

### How does Alpha differ from CrewAI, AutoGen, or LangGraph?

Those are libraries and primitives; Alpha is a finished, self-hostable agent product
built on the same ideas, adding a Gateway, a web workspace, a Windows desktop app, a
research engine, a memory plane, safety and budget controls, and nine messaging
channels. Full matrix and a selection guide: [Comparison](COMPARISON.md).

### How does Alpha differ from OpenHands or Claude Code?

Those are coding agents. Alpha is not repo-bound or language-bound: research, data,
operations, documents, and chat platforms are first-class, and coding is one
capability among many. → [Comparison](COMPARISON.md)

### What is the relationship to DeerFlow?

Alpha is a re-architecture of the MIT-licensed DeerFlow base into a full agent
operating system. Upstream copyright and license notices are preserved in
[LICENSE](../LICENSE), and attribution is tracked in the README's *Project
provenance* section and
[third-party memory notices](THIRD_PARTY_MEMORY_NOTICES.md).

### What is Alpha Network?

A separate workspace for discovering, pairing, and messaging independently installed
Alpha agents. The default free path is LAN UDP discovery plus direct
HTTP/WebSocket delivery with SQLite conversations — no broker, VPS, or paid database.
Optional mDNS and an opt-in GitHub Agent Card rendezvous extend the reach.
**Discovery is untrusted and never grants access; pairing is explicit and uses a
high-entropy out-of-band code.**
→ [Alpha Network](ALPHA_PEER_NETWORK.md)

---

## Contributing and support

### How do I contribute?

Read [`AGENTS.md`](../AGENTS.md) first — it is the contributor source of truth.
Then: `make install`, run `cd backend && make test`, and open a pull request.
Backend changes ship with tests in `backend/tests/` (TDD is mandatory there), and
frontend changes must pass `cd frontend && pnpm verify`. Run `make format` before
pushing.
→ [Contributing](../CONTRIBUTING.md)

### How do I get help?

Run `make doctor` and `make prod-check`, then work through
[Troubleshooting](TROUBLESHOOTING.md). If that does not resolve it, open an issue at
[github.com/itsPremkumar/alpha](https://github.com/itsPremkumar/alpha) with the
redacted bundle from `make support-bundle`, the `X-Trace-Id` header, and the relevant
Gateway and Frontend logs.

### What is `llms.txt`?

A compact, LLM-readable overview of the project following the
[llmstxt.org](https://llmstxt.org/) specification — the same convention as
`robots.txt` and `sitemap.xml`. Use [`/llms.txt`](../llms.txt) for the repository
overview, [`/llms-full.txt`](../llms-full.txt) for expanded context, and
[`docs/llms.txt`](llms.txt) for the documentation index.
→ [Discoverability](DISCOVERABILITY.md)

---

## Related

- [README](../README.md) · [Comparison](COMPARISON.md) · [Use cases](USE_CASES.md) ·
  [Glossary](GLOSSARY.md) · [Full index](INDEX.md)
- [Changelog](../CHANGELOG.md) · [Citation metadata](../CITATION.cff) ·
  [Releases](https://github.com/itsPremkumar/alpha/releases)
