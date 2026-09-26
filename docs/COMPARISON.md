# Alpha vs. other AI agent frameworks

A factual, citable comparison for anyone choosing an open-source agent platform in
2026. Every Alpha claim here is traceable to a file in this repository; every
competitor claim is traceable to that project's own public documentation.

**Alpha's canonical repository:** <https://github.com/itsPremkumar/alpha>
**Alpha's license:** MIT · **Version:** 2.1.0

---

## The 30-second answer

| If you need… | Use |
| :--- | :--- |
| A graph primitive inside an app you already have | **LangGraph** |
| Message-passing actors as a library primitive | **AutoGen** |
| A no-code visual builder for non-engineers | **Dify** |
| Autonomous PR review and issue fixing, nothing else | **OpenHands** |
| A finished, self-hosted agent **product** with UI, desktop app, research engine, memory plane, safety controls, and nine messaging channels | **Alpha** |

Alpha is not a competitor to every row. It is a **superset**: it is built on the
same ideas (LangGraph graphs, subagent delegation, tool use) and additionally ships
the product surface around them.

---

## Full comparison matrix

| | **Alpha** | LangGraph | AutoGen | CrewAI | OpenHands | Dify |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Category** | Agent operating system (app + library) | Low-level graph library | Actor/actor library | Agent + flow framework | Coding-agent product | Low-code LLM app platform |
| **Primary abstraction** | Runs + swarms + skills + tools | `StateGraph` nodes/edges | Actors and messages | `Crew` (autonomy) and `Flow` (control) | Agent + runtime + event stream | Visual workflow graph |
| **Delivered as** | Working system you deploy | Python/JS package you import | Python (+ .NET) package | Python package | Deployed service + UI | Hosted or self-hosted platform |
| **Ready-to-run web UI** | ✅ Next.js 15 workspace | ❌ | ⚠️ AutoGen Studio, separate | ❌ | ✅ | ✅ |
| **Windows desktop app** | ✅ Electron, one-click installer | ❌ | ❌ | ❌ | ❌ | ❌ |
| **Messaging platforms** | ✅ 9 (Telegram, Slack, Feishu/Lark, WeChat, WeCom, DingTalk, Discord, Buzz, Signal) | ❌ | ❌ | ❌ | ❌ | ❌ |
| **MCP client** | ✅ stdio / HTTP / SSE | Via LangChain | Community | ✅ | ✅ | ✅ |
| **Multi-agent swarms** | ✅ Lease-fenced DAG, budgets, typed consensus | Build it | ✅ Core primitive | ✅ Crews | Limited | Workflow nodes |
| **Deep research with citation contract** | ✅ 5-pass, built in | Build it | Build it | Build it | ❌ | ❌ |
| **Sandboxed code execution** | ✅ local / Docker / Kubernetes | ❌ | ❌ | ❌ | ✅ Docker | ❌ |
| **Persistent memory across sessions** | ✅ Layered plane: working, episodic, semantic, dreaming | Basic checkpointer | Basic | Basic | Episodic | App memory |
| **Durable long-running runs** | ✅ Boulder checkpoints + worker-lease recovery | ✅ Durable execution | Partial | Partial | ✅ | Partial |
| **Human-in-the-loop approval** | ✅ Risk-scoring gate + emergency stop | ✅ Interrupts | ✅ | Partial | ✅ | Partial |
| **Audit / provenance trail** | ✅ Flight recorder + artifact lineage | LangSmith (opt-in SaaS) | Logging | Logging | Event stream | Logs |
| **OpenAI-compatible endpoint** | ✅ | Via LangServe | ❌ | ❌ | ❌ | ✅ |
| **Self-host with no cloud account** | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠️ optional tiers |
| **Python** | ✅ 3.12+ | ✅ | ✅ | ✅ | ✅ | ⚠️ runtime only |
| **TypeScript** | ✅ Next.js 15 frontend | ✅ LangGraph.js | ⚠️ limited | ❌ | ✅ | ✅ |

---

## Alpha vs. LangGraph

**Relationship:** Alpha *is built on* LangGraph. They are not substitutes.

LangGraph is deliberately a **low-level** orchestration primitive: you get durable
execution, checkpointing, and interrupts, and you build everything else. Alpha
consumes that same primitive and adds:

- a **Gateway** (60 routers) with auth, thread ownership, health probes, SSE, and
  an OpenAI-compatible surface;
- a **UI** — Next.js 15 workspace plus an Electron Windows app;
- a **research engine** with an explicit citation contract;
- a **memory plane** with dreaming consolidation;
- **safety and budget controls** — approval gate, Estop, token ceilings, flight
  recorder;
- **nine messaging channels** and GitHub webhook triggers;
- **130 native tools**, 24 public skills, and MCP.

**Choose LangGraph when** you are embedding an agent inside a larger application,
you want the smallest possible dependency, and you are content to build the UI,
persistence, and safety layers yourself.

**Choose Alpha when** you want the whole system, self-hosted, on day one.

Alpha also ships the harness as an importable package
(`agent-workspace-harness`, import name `alpha.*`) with 89 engine modules, so
"LangGraph plus Alpha's engines, no Alpha UI" is a supported shape.

→ [ARCHITECTURE.md](ARCHITECTURE.md)

---

## Alpha vs. AutoGen

AutoGen (and its successor Microsoft Agent Framework) is a **library** built around
message-passing actors. It is excellent when you want to model a conversation
between agents and nothing else.

Alpha takes a different posture: agents are workers under a **lead orchestrator**
with a mission hierarchy, a work-queue DAG, lease-fenced task attempts, explicit
budgets, and a bounded blackboard that carries untrusted observations. Consensus is
evidence-backed and separates *execution success* from *verified delivery* — a
distinction AutoGen does not model.

| | AutoGen | Alpha |
| :--- | :--- | :--- |
| Unit of composition | Actor / message | Run / mission / task attempt |
| Coordination | Conversational | Hierarchical + DAG, lease-fenced |
| Failure semantics | Caller-defined | Budgets, retries, watchdog, recovery, explicit terminal states |
| Delivery UI | Studio (separate) | Built into the workspace |
| Deployment | Library | Deployed system |

**Choose AutoGen when** multi-agent conversation *is* your product.
**Choose Alpha when** agents must complete unattended work with observable budgets.

---

## Alpha vs. CrewAI

CrewAI's mental model is **Crews** (autonomous role-based collaboration) plus
**Flows** (event-driven, deterministic control). It is a strong, lightweight Python
framework — and Alpha's swarm layer uses a very similar division of labour.

Where they differ:

| | CrewAI | Alpha |
| :--- | :--- | :--- |
| Scope | Framework | Operating system |
| Deep research | Build it | ✅ 5-pass with citation contract |
| Sandboxed execution | ❌ | ✅ local / Docker / Kubernetes |
| Memory | Basic | Layered, with dreaming consolidation |
| Web UI | ❌ | ✅ |
| Desktop app | ❌ | ✅ |
| Messaging channels | ❌ | ✅ 9 |
| Safety controls | Guardrails (library) | Approval gate, Estop, enclave, flight recorder |
| Durability | Checkpointing | Boulder checkpoints + lease recovery |
| Deploy target | Your app | `make up` / Helm / installer |

**Choose CrewAI when** you want a clean, small, well-documented library for
role-based agent collaboration inside Python code you own.
**Choose Alpha when** you also need the runtime, UI, deployment, memory, research,
and governance around it.

---

## Alpha vs. OpenHands

OpenHands (formerly OpenDevin) is the most focused of the group: an autonomous
**software-engineering** agent. If your entire job is "fix this issue in this repo,"
OpenHands is leaner and will get you there faster.

Alpha treats coding as one of many capabilities and adds the surrounding platform:
research, data analysis, document generation, browser automation, scheduled and
webhook-triggered runs, nine chat channels, a peer network, and a full memory
plane. Alpha also ships pre-commit AST verification, git shadow checkpoints with
1-click rollback, a repo twin preview sandbox, AST-grep structural rewriting, and
line-level hashline editing.

| | OpenHands | Alpha |
| :--- | :--- | :--- |
| Primary domain | Software engineering | Any knowledge work |
| Repo-bound | ✅ | Optional |
| Research with citations | ❌ | ✅ |
| Non-code deliverables | ❌ | ✅ |
| Chat platforms | ❌ | ✅ |
| Windows desktop | ❌ | ✅ |

**Choose OpenHands for** engineering throughput on a codebase.
**Choose Alpha for** an organization-wide agent platform.

---

## Alpha vs. Dify

Dify is a **low-code platform**: a visual builder plus a runtime for LLM apps
(chatflows, agents, RAG pipelines, model fine-tuning). It is the right answer when
the people building the app are not engineers.

Alpha is a **code-first agent operating system**. Everything is YAML, Python, and
TypeScript in a git repository, which means it is reviewable, diffable, and
CI-testable — the same properties you want from application code.

| | Dify | Alpha |
| :--- | :--- | :--- |
| Building interface | Visual canvas | Code + git |
| Audience | Non-engineers, product teams | Engineers and platform teams |
| Reviewability | Platform state | Pull requests, code review, CI |
| Autonomous long-horizon runs | Limited | ✅ Core design |
| Swarms | Workflow nodes | ✅ Lease-fenced DAG |
| Provenance / audit | Logs | Flight recorder + artifact lineage |

They also compose: run Dify apps as tools, expose Alpha over its
OpenAI-compatible endpoint, or bridge both through MCP.

---

## Decision guide

Answer in order and stop at the first match.

1. **Do you need a no-code visual builder?** → **Dify**
2. **Do you only need a graph primitive inside an existing app?** → **LangGraph**
3. **Is your only job autonomous code review / PR fixing?** → **OpenHands**
4. **Is multi-agent conversation itself the product?** → **AutoGen**
5. **Do you want a small, clean Python framework for role-based crews?** → **CrewAI**
6. **Otherwise** — you need a finished, self-hosted, auditable agent platform
   with a UI, a desktop app, deep research, sandboxes, memory, budgets, and
   messaging reach → **[Alpha](https://github.com/itsPremkumar/alpha)**

---

## What Alpha does *not* claim

Intellectual honesty is part of the product. Alpha does **not** claim:

- cross-process exactly-once execution for swarms or dynamic workflows — local
  JSON checkpoints and JSONL events are restart-recoverable for one Gateway
  process, and a multi-worker deployment must supply a shared SQL lease repository;
- that the built-in dynamic-workflow digest executor performed a real domain task
  — it discloses `execution_label="local_digest_projection"` and
  `acceptance_passed=false` until a real executor is bound;
- that a completed run is verified — verification requires independently checked
  evidence, tracked in
  [PRODUCTION_READINESS_INVENTORY.md](PRODUCTION_READINESS_INVENTORY.md);
- that installation-scoped peer-network SQLite is cross-process exactly-once
  storage;
- full production readiness — read
  [PRODUCTION_READINESS_INVENTORY.md](PRODUCTION_READINESS_INVENTORY.md) before
  exposing a deployment.

---

## Reproducing these numbers

Every Alpha figure in this document is generated, not hand-maintained:

```bash
python backend/scripts/generate_feature_manifest.py   # -> contracts/feature_manifest.json
```

The manifest pins **130 tools, 60 routers, 42 middlewares, and 8 supervisor
loops**, and `tests/test_feature_manifest_wiring.py` plus the generated-drift CI
gate fail the build if the documentation and the live registries disagree.

---

## Further reading

- [README.md](../README.md) — the feature catalog and quickstart
- [USE_CASES.md](USE_CASES.md) — end-to-end jobs this comparison enables
- [ARCHITECTURE.md](ARCHITECTURE.md) — how the planes fit together
- [FAQ.md](FAQ.md) — short answers
- [GLOSSARY.md](GLOSSARY.md) — every term used above, defined
- [SECURITY.md](SECURITY.md) — the security model in depth
