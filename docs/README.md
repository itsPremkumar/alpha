# Alpha documentation

**Alpha** is an open-source autonomous multi-agent AI operating system: a LangGraph
agent runtime behind a FastAPI Gateway, with a Next.js 15 web workspace and an
Electron Windows desktop app. It executes long-horizon work — deep research, planning,
multi-agent delegation, sandboxed code, persistent memory — with 130 native tools,
MCP extensions, and 24 public skills, behind a single Nginx entry point, with no
proprietary backend.

> **Canonical repository:** <https://github.com/itsPremkumar/alpha> ·
> **Maintainer:** [Prem Kumar](https://github.com/itsPremkumar) ·
> **License:** [MIT](../LICENSE) · **Version:** 2.1.0

---

## Start here

| If you want to… | Read |
| :--- | :--- |
| **Run it** | [Getting started](GETTING_STARTED.md) |
| **Understand what it is** | [README](../README.md#what-is-alpha) · [FAQ](FAQ.md) · [Glossary](GLOSSARY.md) |
| **Decide whether it fits** | [Comparison](COMPARISON.md) · [Use cases](USE_CASES.md) |
| **Deploy it** | [Deployment](DEPLOYMENT.md) · [Configuration](CONFIGURATION.md) |
| **Operate it** | [Production runbook](PRODUCTION.md) · [Troubleshooting](TROUBLESHOOTING.md) |
| **Secure it** | [Security](SECURITY.md) |
| **Extend it** | [Extensions & MCP](EXTENSIONS.md) · [Skills](SKILLS.md) |
| **Integrate with it** | [API reference](API_REFERENCE.md) · [API overview](API.md) |
| **Know its limits** | [Production readiness inventory](PRODUCTION_READINESS_INVENTORY.md) |
| **Contribute** | [Development](DEVELOPMENT.md) · [Contributing](../CONTRIBUTING.md) · [AGENTS.md](../AGENTS.md) |

For machine-readable context, see [`/llms.txt`](llms.txt) (this directory),
[`/llms.txt`](../llms.txt) (repository), and
[`/llms-full.txt`](../llms-full.txt) (expanded).

---

## Core architecture and strategy

- **[Architecture](ARCHITECTURE.md)** — the runtime planes, the middleware chain,
  sandbox tiers, and the 99 harness engine packages under
  `backend/packages/harness/alpha/`.
- **[Deep research](DEEP_RESEARCH.md)** — the five-pass search pipeline, bounded
  knowledge-gap filling, adversarial source juxtaposition, and the explicit citation
  contract.
- **[Cognitive engines](COGNITIVE_ENGINES.md)** — Agentic Variation Operators,
  Mixture of Agents, Theory of Mind, epistemic belief tracking, dreaming
  consolidation, and the System One decision layer.
- **[Dynamic workflows](DYNAMIC_WORKFLOWS.md)** — the typed, evidence-gated
  workflow runtime, and exactly what its local digest projection does and does not
  do.
- **[Run recovery](RUN_RECOVERY.md)** — durable Boulder checkpoints, worker-lease
  fencing, and safe replay after a crash, restart, or recoverable model failure.
- **[Self-documentation](SELF_DOCUMENTATION.md)** — free, local, authority-aware
  retrieval over the project's own guidance and docs, returning line-addressable
  snippets with SHA-256 evidence and digest-checked reads.

## Multi-agent workforce

- **[Workforce](WORKFORCE.md)** — bot roster, SOUL protocol, private inboxes, DMs,
  group chat rooms, the Swarm v2 DAG runtime, project constitutions, ADRs, resource
  locks, and the live Kanban board.
- **[Skills](SKILLS.md)** — how skill packages work, the 24 public skills, the
  curator lifecycle (active / stale / archived), quarantine trust tiers, and the
  built-in read-only skill reviewer.

## API and integration

- **[API reference](API_REFERENCE.md)** — all 60 Gateway routers, authentication,
  and SSE event streaming.
- **[API overview](API.md)** — entry points, base URLs, routing, and versioning.
- **[Extensions & MCP](EXTENSIONS.md)** — MCP over stdio, HTTP, and SSE, plus the
  Python extension contract (middleware, task lifecycle, model observers, Gateway
  services, FastAPI routers).

## Memory

- **[Memory](MEMORY.md)** — the layered memory plane, vector memory, episodic replay,
  and idle-time dreaming consolidation.
- **[Memory types](MEMORY_TYPES.md)** — the canonical taxonomy and Alpha's coverage
  of it.
- **[Memory fabric plan](MEMORY_FABRIC_PLAN.md)** — fabric boundaries and deferred
  work.
- **[Third-party memory notices](THIRD_PARTY_MEMORY_NOTICES.md)** — subsystem-level
  attribution for adapted memory code.

## Security, operations, and governance

- **[Security](SECURITY.md)** — the Astra enclave and scoped credential vault, the
  smart command approval gate, emergency stop, the trajectory flight recorder,
  artifact lineage, and the threat model.
- **[Production runbook](PRODUCTION.md)** — health and readiness probes,
  `/api/ops/*` operator endpoints, monitoring, and disaster recovery.
- **[Production readiness inventory](PRODUCTION_READINESS_INVENTORY.md)** — what is
  implemented and tested versus what is still open, with evidence. **Read this
  before exposing a deployment.**
- **[Production readiness transfer guide](PRODUCTION_READINESS_TRANSFER_GUIDE.md)** —
  production foundations and the operator handoff plan.
- **[Configuration](CONFIGURATION.md)** — every setting in `config.yaml`,
  `extensions_config.json`, and `.env`, with resolution order.
- **[Auto-update](AUTO_UPDATE.md)** — the guarded source updater, its policy file,
  and its kill switch.
- **[Troubleshooting](TROUBLESHOOTING.md)** — diagnostic flows, stuck-process
  resolution, and lock contention.
- **[Autonomy truth and recovery](AUTONOMY_TRUTH.md)** — fail-closed readiness
  derived only from server-owned evidence, explicit capability boundaries,
  redacted failure classification, and bounded activity digests.
- **[Reversible file quarantine](REVERSIBLE_DELETE.md)** — approval-gated local
  deletion planning, quarantine, receipts, and restore.

## Presentation and interaction

- **[Voice conversation](VOICE_CONVERSATION.md)** — real-time local voice: Whisper
  transcription, VAD endpointing, sentence-level Piper playback, no paid speech API.
- **[Lion companion](LION_COMPANION.md)** — the local-first Milo companion and its
  presentation contract.

## Developer and governance

- **[Development](DEVELOPMENT.md)** — local developer workflows, hot reloading, test
  suites, and contribution standards.
- **[Contributing](../CONTRIBUTING.md)** · **[Code of conduct](../CODE_OF_CONDUCT.md)** ·
  **[Security policy](../SECURITY.md)**
- **[AGENTS.md](../AGENTS.md)** — the monorepo orientation layer and the
  contributor source of truth. `CLAUDE.md` is a thin import shim.
- **[Discoverability](DISCOVERABILITY.md)** — how this project is engineered for
  classic SEO, generative engine optimization (GEO), and answer engine
  optimization (AEO), with a maintenance checklist.

## Reference

- **[Index](INDEX.md)** — the complete, generated index of every document in
  `docs/`, grouped by area.
- **[FAQ](FAQ.md)** — short, quotable answers.
- **[Comparison](COMPARISON.md)** — Alpha vs LangGraph, AutoGen, CrewAI, OpenHands,
  and Dify.
- **[Use cases](USE_CASES.md)** — end-to-end jobs mapped to subsystems.
- **[Glossary](GLOSSARY.md)** — every Alpha term defined in one page.
- **[Research library](../references/README.md)** — 45+ papers and architecture
  studies on agent harnesses, AVO loops, recursive self-improvement, and frontier
  coding agents.
- **[Changelog](../CHANGELOG.md)** · **[Citation metadata](../CITATION.cff)**

---

*Maintained and architected by [Prem Kumar](https://github.com/itsPremkumar) ·
GitHub: [itsPremkumar/alpha](https://github.com/itsPremkumar/alpha)*
