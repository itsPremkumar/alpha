# Use cases: what people build with Alpha

Each use case below names the outcome, the Alpha subsystem that delivers it, and
the documentation that specifies it. If your goal is not listed, the
[feature catalog](../README.md#feature-catalog) and the
[89-engine subsystem map](../README.md#feature-catalog) cover the long tail.

---

## Knowledge work

### Autonomous research reports

**Outcome:** a cited, multi-source briefing on any question, without stitching
together five browser tabs.

Alpha runs a five-pass search pipeline — discovery, specific evidence, adversarial
contradiction, fact verification, strategic synthesis — then runs a bounded
follow-up stage for whatever evidence is still missing. Sources receive
deterministic `[S1]`, `[S2]` anchors and are reported as **verified**,
**unsupported**, **unverified**, or **not checked**, so the reader always knows how
much weight a claim carries.

A pinned 39-function free-source catalog (academic, developer, package,
government, scientific, knowledge, media, social) backs the search with operator
allowlists, bounded fan-out, and SSRF-safe fetching.

→ [DEEP_RESEARCH.md](DEEP_RESEARCH.md) · tool: `deep_research`

### Systematic literature reviews

**Outcome:** a PRISMA-compliant review with a formal citation matrix, from the
`systematic-literature-review` skill; critical methodology assessment from
`academic-paper-review`.

→ [`skills/public/`](../skills/public/)

### Competitive and repository intelligence

**Outcome:** a full audit of any GitHub repository — commit history, contributor
structure, open issue triage, and a structural dependency map — from
`github-deep-research` and `project-cartographer`, which walks the codebase and
builds an abstract-syntax-tree dependency graph.

→ [ARCHITECTURE.md](ARCHITECTURE.md)

### Strategy work

**Outcome:** SWOT, Porter's Five Forces, BCG Matrix, and MECE trees, from the
`consulting-analysis` skill, with consequence simulation before any recommended
irreversible action is taken.

→ [COGNITIVE_ENGINES.md](COGNITIVE_ENGINES.md)

---

## Software engineering

### Autonomous code repair

**Outcome:** a failing test suite turned green, with a root-cause trace and a
verified fix.

Alpha runs the test command, parses the traceback, isolates the cause, and applies
a fix — then re-runs. Every file write passes a pre-commit AST guardrail that
statically verifies Python, JSON, and YAML syntax and rejects broken edits with
compiler feedback *before* they touch disk.

→ [ARCHITECTURE.md](ARCHITECTURE.md)

### Safe refactoring with instant rollback

**Outcome:** large structural changes without the fear of losing your work.

Before a risky mutation, Alpha captures a lightweight git shadow reference under
`refs/alpha-checkpoints/<cid>`. Roll back, or inspect the diff, over REST:

```
GET  /api/checkpoints
POST /api/checkpoints
POST /api/checkpoints/{id}/rollback
GET  /api/checkpoints/{id}/diff
```

A **repo twin** shadow sandbox previews the change against a copy of the
repository before it is applied to your working tree.

→ [RUN_RECOVERY.md](RUN_RECOVERY.md)

### Structural search and rewrite

**Outcome:** find and change code by *shape* rather than by text, across
TypeScript, Python, Go, Rust, and C++, with AST-grep. Line-level **hashline
editing** gives deterministic reads and edits that do not drift on multi-line
changes.

→ [ARCHITECTURE.md](ARCHITECTURE.md)

### Continuous self-healing

**Outcome:** a loop that keeps working until the code passes and its architectural
invariants hold — the **Ralph loop** — supervised by a metacognitive monitor that
detects looping, thrashing, and prompt drift.

→ [ARCHITECTURE.md](ARCHITECTURE.md)

---

## Data and analysis

### Sandboxed analysis

**Outcome:** run Python or shell against real data without giving the model a shell
on your machine.

Three sandbox tiers are available: a local subprocess, a Docker container, or a
Kubernetes provisioner. The tier is chosen in `config.yaml`.

→ [SECURITY.md](SECURITY.md) · [CONFIGURATION.md](CONFIGURATION.md)

### Charts and dashboards

**Outcome:** interactive charts, telemetry graphs, and dashboards from the
`chart-visualization` skill, rendered inline in the workspace canvas.

→ [`skills/public/`](../skills/public/)

### Data analysis

**Outcome:** tabular processing, statistical modelling, pattern recognition, and
trend forecasting from the `data-analysis` skill.

→ [`skills/public/`](../skills/public/)

---

## Multi-agent work

### A team of agents on one project

**Outcome:** several specialised agents collaborating on a real project, each with
its own personality, isolated system prompt, and private inbox.

Bot profiles form a roster. They exchange direct messages, meet in group chat rooms,
and post to a shared Kanban board. A project holds membership, presence, resource
locks, a constitution, and an ADR history. Underneath, the Swarm v2 DAG runtime
provides atomic checkpoints, lease-fenced task attempts, retry backoff, restart
recovery, and explicit budget-exhausted and stalled states.

→ [WORKFORCE.md](WORKFORCE.md)

### Verified consensus

**Outcome:** a conclusion you can defend, not just a vote count.

A bounded blackboard carries untrusted observations and task results. Votes must be
evidence-backed; a leader is elected; acceptance is verified; and aggregation is
acceptance-sensitive, so *the task ran* and *the result was delivered and verified*
are reported as different things.

→ [WORKFORCE.md](WORKFORCE.md)

### Prompt-to-workflow

**Outcome:** a typed, evidence-gated workflow graph built from a plain-language
request — intent perception, capability discovery, decomposition, bounded DAG
waves, graph patches, approval gates, retry/replan, evidence-gated replay, and
saga compensation.

Preview with no side effects via `POST /api/workflows/dynamic/perceive`; execute via
`POST /api/workflows/dynamic/execute`. The built-in digest executor is deliberately
a **local graph projection** and discloses `execution_label="local_digest_projection"`
with `acceptance_passed=false` until a real executor is bound.

→ [DYNAMIC_WORKFLOWS.md](DYNAMIC_WORKFLOWS.md)

---

## Operations and automation

### Scheduled agents

**Outcome:** work that happens whether or not anyone is watching.

A cron scheduler runs occurrences behind pre-flight wake gates, with blueprint
workflows, incident tracking, and automatic pausing when a scheduled job starts
failing. A busy occurrence is persisted as `queued` and bounded by
`scheduler.queue_timeout_seconds`; it is never silently skipped.

→ [PRODUCTION.md](PRODUCTION.md)

### Event-driven agents from GitHub

**Outcome:** an agent invoked by a pull request, issue, comment, or release, via
GitHub webhook automations.

→ [API_REFERENCE.md](API_REFERENCE.md)

### Always-on supervision

**Outcome:** background loops that keep the system healthy without a human.

A single supervisor owns the lifecycle of five loops — sentinel, perpetual,
review queue, skill curator, and enterprise heartbeat. Loops are declared
explicitly, gated by `config.yaml -> autonomy.loops`, restart inside a budget and
then park, and publish lifecycle events on the in-process bus. All flags off means
zero tasks.

→ [ARCHITECTURE.md](ARCHITECTURE.md)

---

## Communication

### An agent in your chat app

**Outcome:** the same agent, reachable where your team already talks.

Nine bidirectional channels: **Telegram, Slack, Feishu/Lark, WeChat, WeCom,
DingTalk, Discord, Buzz, and Signal.**

→ [API.md](API.md)

### A drop-in OpenAI-compatible endpoint

**Outcome:** point an existing client at Alpha instead of at a hosted provider.

```
POST /api/compat/openai/chat/completions
```

→ [API_REFERENCE.md](API_REFERENCE.md)

### Hands-free voice

**Outcome:** talk to the agent and hear it answer, with no paid speech API.

Browser microphone streaming, local Whisper interim and final transcription,
voice-activity turn endpointing, the normal SSE agent stream, sentence-level local
Piper playback while the rest of the answer is still generating, and hands-free
resume. The only component that may cost money is your model provider.

→ [VOICE_CONVERSATION.md](VOICE_CONVERSATION.md)

---

## Knowledge management

### Memory that survives the session

**Outcome:** an agent that remembers your preferences, your project's decisions,
and what worked last time.

A layered memory plane: working memory for the current turn, episodic traces with
replay, a semantic knowledge graph, and idle-time **dreaming** that consolidates
traces into higher-level knowledge. Production cognitive memory is scoped to a
server-resolved owner and fails closed when the owner is missing or state is
corrupt.

→ [MEMORY.md](MEMORY.md) · [MEMORY_TYPES.md](MEMORY_TYPES.md)

### Skills that write themselves

**Outcome:** a capability captured once and reused forever.

The skill forge synthesises new skills from successful execution traces. A curator
keeps the library healthy, moving skills through active, stale, and archived states
with quarantine trust tiers, and `skill-reviewer` audits skills for security and
compliance before they are trusted.

→ [SKILLS.md](SKILLS.md)

---

## Content production

| Outcome | Skill |
| :--- | :--- |
| Publication-grade industry digest or newsletter | `newsletter-generation` |
| Structured slide deck with speaker notes | `ppt-generation` |
| Multi-speaker podcast script and audio storyboard | `podcast-generation` |
| Scene-by-scene video script and storyboard | `video-generation` |
| Styled image prompts and generation pipeline | `image-generation` |
| Musical structure, BPM/key, and audio prompts | `music-generation` |
| Architecture guides, docstrings, and API references | `code-documentation` |
| Accessible, production-grade UI components | `frontend-design` |
| Modern web design heuristics and WCAG guidance | `web-design-guidelines` |
| Full repository scaffolding and environment setup | `bootstrap` |
| Deliberately open-ended exploration | `surprise-me` |

→ [`skills/public/`](../skills/public/)

---

## Private and local

### Everything on your own infrastructure

**Outcome:** nothing leaves your machine except the prompts you send the model
provider you chose.

Local speech models, local SQLite or your own PostgreSQL, local or self-hosted
models through Ollama and OpenAI-compatible endpoints, and no Alpha-operated
backend of any kind.

→ [DEPLOYMENT.md](DEPLOYMENT.md)

### An agent mesh across your own machines

**Outcome:** independently installed Alpha instances discovering and messaging
each other on your LAN — with no broker, VPS, or paid database.

LAN UDP discovery plus direct HTTP/WebSocket delivery and SQLite conversations, with
optional mDNS and an opt-in GitHub Agent Card rendezvous. Direct, one-to-many,
many-to-one, many-to-many, and broadcast sessions all carry per-recipient delivery
receipts. **Discovery is untrusted and never grants access; pairing is explicit and
uses a high-entropy out-of-band code.**

→ [ALPHA_PEER_NETWORK.md](ALPHA_PEER_NETWORK.md)

---

## Before you deploy

Whatever you build, read
[PRODUCTION_READINESS_INVENTORY.md](PRODUCTION_READINESS_INVENTORY.md) first. It
records exactly which guarantees are implemented and tested, and which are still
open — so you can decide what your risk tolerance allows.

→ [PRODUCTION.md](PRODUCTION.md) · [SECURITY.md](SECURITY.md) ·
[TROUBLESHOOTING.md](TROUBLESHOOTING.md)
