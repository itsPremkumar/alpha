# Specialists

A **specialist** is a declared unit of expertise the rest of Alpha can
schedule. It is not a Bot, not a persona, and not a swarm plan. It is the
leader-authored configuration that says: *work of this shape belongs to this
role, here is what it may touch, here is what it must ask about, and here is
the routing slot it runs on.*

Specialists are declared in **`config.yaml` under `specialists:`**, validated at
load, and read back through `get_app_config().specialists` /
`alpha.config.specialist_config.get_specialist_catalog()`.

---

## Why this is a config section and not a Python table

The repository has one config file, and a standing rule that a hand-maintained
table in Python is a second source of truth that drifts silently. The
`models.yaml` precedent is the reason: a second model file was read
exclusively through a loader that returned an empty catalog when the file was
absent, no first-run step created it, and a fresh install therefore had an
empty keyless-gateway list while the picker still advertised `alpha-free`. The
second file was removed, not deprecated.

The per-Bot role table that already exists —
`alpha.bots.templates.BOT_TEMPLATES` — is exactly such a table, and
`alpha.bots.permissions.DEFAULT_ROLE_RINGS` is the executable half of it. This
section **references** those two vocabularies and validates against them. It
never restates a role, a department, an avatar, a sandbox backend, a skill, or
a model. Adding a role means adding a ring, in the module that enforces it.

## The self-service boundary

`bot_roster` lets a Bot edit `display_name` and `avatar` about itself and
refuses `role`, `model`, `skills`, `capabilities`, `department` and
`reports_to` as leader-only (`_LEADER_ONLY` in
`alpha/tools/builtins/bot_roster_tool.py`).

**Every field in a specialist entry is on the leader-only side of that line.**
A catalogue is leader-authored configuration; it is not a self-edit surface.
Editing a specialist means editing `config.yaml` and reloading — never a
model-visible write. `tests/test_specialist_catalog.py` pins this from both
directions: no specialist field name is in `_SELF_EDITABLE`, and every field
that maps onto a `BotProfile` attribute is one of the leader-only names.

---

## The schema

```yaml
specialists:
  enabled: true          # master switch; declarations still parse and validate when false
  allow_overlap: false   # operator override for the duplicate-territory refusal
  entries:
    - name: code-reviewer            # required, handle-shaped
      title: Code Reviewer           # optional label
      role: architect                # required, resolves to a worker ring
      description: >- ...            # required, territory claim
      not_for: [...]                  # required, >= 1 explicit exclusion
      capabilities: [...]            # required, >= 1 slug
      tool_groups: [file:read]       # must exist in tool_groups[]
      skills: []                     # skill names
      approvals: draft_first         # draft_first | autonomous
      extra_approvals: []            # added to the posture's list
      routing: {category: deep, tier: coding}
      department: qa                 # must be a DEPARTMENTS value
      reports_to: null               # another declared specialist
      agent_preset: null             # built-in or configured preset
      sandbox: null                  # a forge SANDBOX_BACKENDS value
      enabled: true
```

### Why each field exists

| Field | What it decides | Consumer |
| --- | --- | --- |
| `name` | The specialist's identity, and the handle a forged Bot would take. Held to the forge's own `[a-z0-9][a-z0-9_-]{0,63}` rule so the catalogue can never declare a name `forge_bot` would refuse. | `forge_bot(name=...)`, `reports_to` target, selection key |
| `title` | The human label. Presentation only — never a permission. | `BotProfile.display_name`, `to_bot_template()` |
| `role` | **Which tools the specialist may execute.** Must contain one of the worker permission rings in `alpha/bots/permissions.py`, because that ring *is* the permission. An all-tools ring (`lead`, `supervisor`, `admin`) is refused: a worker specialist is not the leader. | `ToolPermissionGate.check_permission`, `BotProfile.role` |
| `description` | The territory claim, and the text a duplicate check and a selection both read. | `territory()`, `to_soul()`, load-time guards |
| `not_for` | The negative scope. Required and non-empty: a specialist that only says what it is good at cannot be told apart from its neighbours, and a prohibition is the half of a routing decision that makes it safe to automate. | `to_soul()` ("Never handle this") |
| `capabilities` | What the specialist is offered — the vocabulary a leader selects on. | `BotProfile.capabilities` |
| `tool_groups` | The declared toolset, narrowed to groups `tool_groups[]` actually declares. | `BotProfile.toolsets` |
| `skills` | Skills to load. | `BotProfile.skills` |
| `approvals` | The approval posture, reusing the forge's two states: `draft_first` composes `forge.DEFAULT_APPROVALS`, `autonomous` is the deliberate `approvals=[]` opt-out. A catalogue cannot invent a third answer, so a declaration cannot quietly lose the checkpoints the forge gives by default. | `build_guardrail_soul()` |
| `extra_approvals` | Additional always-ask items on top of the posture. | `build_guardrail_soul()` |
| `routing` | Model routing **intent**: a `model_routing` category and/or tier. Never a model name. | `ModelRoutingConfig.chain_for_category/tier` |
| `department` | Org placement. | `BotProfile.department`, `get_by_department` |
| `reports_to` | The escalation target and the reporting hierarchy. | `BotProfile.reports_to`, `build_guardrail_soul` |
| `agent_preset` | The named per-session preset bundle. | `BotProfile.agent_preset` |
| `sandbox` | Where a forged Bot's shell would run. | `forge.probe_sandbox`, `build_guardrail_soul` |
| `enabled` (per entry) | Out of rotation, still declared and still inspectable. | `enabled_specialists()` |
| `enabled` (section) | The master switch. Parsing and validation still run when false. | `enabled_specialists()` |

### The routing field, specifically

`routing` names a **slot** — a `model_routing` intent category or cost tier —
and never a model. This is the mistake the repository already made once and
removed: the routers used to carry hardcoded vendor model ids
(`gpt-4o`, `claude-opus-5`) that exist in no operator's `models[]`, so a
routing decision could name a model the factory rejects and the only signal was
a warning as the run silently degraded to the default. `model_routing` is the
single executable routing source and it is validated against `models[]` at load.

There are three honest outcomes, distinguished by what the operator's table
contains:

- **Slot declared in a populated table** — resolves to an executable chain.
- **Slot absent from a populated table** — a **load error**. This is a typo,
  and a specialist that reads as routed and is not is the exact silent
  degradation the routing section exists to remove.
- **No `model_routing` mappings at all, or routing disabled** — accepted and
  **warned** about. `config.example.yaml` ships in this state, so the shipped
  team is in it. The specialist then runs on `default_model`. That is
  disclosed, not assumed.

---

## The thin-query rule, and why it is load-bearing

`alpha.bots.forge.check_overlap` refuses to build a Bot whose job an existing
Bot already covers. But `alpha.bots.survey._overlap_score` normalises the raw
weight against the **query's own ceiling**, so a one-word role (`Security`)
scores 1.0 against any SOUL that merely contains the word. The forge answers
this with a floor: below `survey.MIN_OVERLAP_TERMS` (3) distinct significant
terms, the score is returned as *evidence only* and cannot refuse anything.

A specialist inherits that rule rather than restating it:

- **A description below the floor is a load error.** A one-word description
  scores 1.0 against every neighbour, so the forge's duplicate check would
  silently never protect it. The catalogue refuses it at load instead of
  shipping a declaration whose duplicate-detection is inert. The floor is
  measured over `role + description` — the operator's own text — *not* over the
  generated SOUL, because the heading, the "Never handle this" label and the
  capability slugs are this module's own formatting and must not be allowed to
  launder a one-word description.
- **Two specialists the forge's `check_overlap` would refuse are a load
  error.** The catalogue delegates to the forge's function rather than
  reimplementing the score, so a match and a refusal cannot disagree about what
  "the same job" means. `specialists.allow_overlap` is the operator override,
  mirroring `forge_bot(allow_overlap=...)`.
- **`not_for` is deliberately excluded from the scored text.** The score
  normalises against the query's own ceiling, so folding prohibitions in
  inflates the ceiling and depresses every genuine match. Measured on the
  shipped team: a documentation task scored 0.43 against `security-reviewer`
  purely because both entries mention a configuration change — one as
  competence, the other as a prohibition. Every shipped entry prohibits
  shipping a release and three prohibit touching code; scoring those would make
  five of the six near-duplicates of each other. An exclusion is not a claim of
  competence.

The threshold is one number, shared with the forge:
`SELECTION_OVERLAP_THRESHOLD` is asserted equal to `check_overlap`'s own
default, so a test fails if the two drift apart.

### The shipped team, measured

All 30 ordered pairs (6 × 5, both directions) run through
`forge.check_overlap` with the shipped descriptions:

```
worst pair score: 0.164   threshold: 0.34   refused pairs: 0
```

The worst pair is less than half the threshold, so the team is not scraping
past the rule — it is clear of it. `tests/test_specialist_catalog.py` re-runs
this on every test run, so a future description edit that pushes a pair over is
caught at CI rather than at the next bot build.

---

## The default team

Six specialists, shipped in `config.example.yaml`, with non-overlapping
territories and six distinct capability sets. Every entry is proven selectable
by a task phrased in its own vocabulary, and proven **not** refused by
`forge.check_overlap` against every other entry, in both directions (30
ordered pairs; worst 0.164 against a 0.34 threshold).

Each `description` is a thin_query in the forge's sense: it says what
territory the specialist owns, and `not_for` says what it must never be handed.
A selection task that lands in each territory and the expected owner:

| Specialist | Role (ring) | Department | Capabilities | Approvals | Routing intent |
| --- | --- | --- | --- | --- | --- |
| `researcher` | `researcher` | product | `web_research`, `source_verification`, `evidence_synthesis` | draft-first | `tier: fast` |
| `code-reviewer` | `architect` | qa | `code_review`, `contract_analysis`, `regression_risk` | draft-first | `category: deep`, `tier: coding` |
| `security-reviewer` | `architect` | security | `threat_modelling`, `secret_scanning`, `permission_audit` | draft-first | `category: deep`, `tier: frontier` |
| `test-engineer` | `tester` | qa | `test_design`, `regression_suite`, `failure_triage` | draft-first | `tier: coding` |
| `technical-writer` | `developer` | product | `technical_writing`, `api_reference`, `release_notes` | draft-first | `category: writing` |
| `operations-engineer` | `tester` | operations | `incident_triage`, `health_monitoring`, `rollback_execution` | draft-first | `tier: fast` |

| Task | Selected |
| --- | --- |
| gather external evidence and cite the sources | `researcher` |
| cross-check these two claims against a second source | `researcher` |
| review this diff and report the finding with its file and line | `code-reviewer` |
| find the unhandled failure path in this proposed change | `code-reviewer` |
| audit the exposed route for authentication and authorization gaps | `security-reviewer` |
| scan for committed secret material and untrusted input reaching a shell | `security-reviewer` |
| write the missing regression case and run the test command | `test-engineer` |
| triage this failure to the assertion that broke | `test-engineer` |
| update the setup guide and the api reference pages | `technical-writer` |
| flag a documented option that no longer exists | `technical-writer` |
| read the logs and narrow this alert to the failing component | `operations-engineer` |
| follow the runbook for a rollback | `operations-engineer` |
| review this diff, then write the regression case for the finding | both `code-reviewer` and `test-engineer` |
| bake a lemon cake with a sponge base | *nobody* |

A note on the roles, since they look surprising: the ring decides the tools,
and the shipped rings are coarse. `code-reviewer` and `security-reviewer` both
take `architect` — the narrowest read-only ring (read, search, message; denied
file replacement and shell) — because there is no dedicated review ring.
`test-engineer` and `operations-engineer` both take `tester`, which may run the
suite and read but is denied file replacement, so neither can quietly change
the artefact it is measuring. `technical-writer` takes `developer`, the
narrowest ring that can write files at all, which means **it inherits
`developer`'s shell access**; narrowing that needs a new ring in
`alpha/bots/permissions.py`, not a new specialist. These gaps are called out
here rather than papered over by declaring roles that do not exist.

The team is **flat**, and that is deliberate. The leader is a Bot
(`alpha/bots/alpha_leader.py`), not a specialist, so there is no specialist for
the others to report to. `reports_to` is validated, tested and projected onto
`BotProfile.reports_to` regardless — see the limits below.

---

## Consumers

| Consumer | What it reads |
| --- | --- |
| `AppConfig.specialists` | The declared section. Hot-reloaded; not restart-required. |
| `AppConfig.get_specialist_config(name)` | O(1) lookup, the same shape as `get_model_config` / `get_tool_config`. |
| `alpha.config.specialist_config.get_specialist_catalog()` | The live catalogue via `get_app_config()`. |
| `SpecialistCatalogConfig.select_for_task(task)` | Deterministic selection, ordered by score, carrying the matching terms as evidence. |
| `SpecialistConfig.to_forge_kwargs()` | The `forge_bot` arguments a leader would pass to build this specialist. |
| `SpecialistConfig.to_bot_template()` | A `.alphabot.json` payload, keyed by `alpha.bots.portable.TEMPLATE_FIELDS`. |
| `SpecialistConfig.to_soul()` | The persona text; the forge appends approvals, escalation and sandbox to it. |
| `SpecialistCatalogConfig.summary()` | A JSON-safe projection for a tool reply or an ops payload. |

`select_for_task` is deterministic word matching — the same
`alpha.bots.survey.score_overlap` the forge's refusal and the workspace survey
use. No model call, no tokens, no network, so the same catalogue and the same
task always produce the same order. A specialist below the threshold is **not**
proposed, and a thin task is reported with `judgeable=False` rather than
matched confidently from one shared word.

---

## What a specialist still cannot do

This is the honest limit of a catalogue, and it is the part that matters most.

**A specialist grants nothing by itself.** It is data. It does not:

- **Mint a Bot.** Nothing in this section calls `forge_bot`, and no
  specialist appears on the roster. `to_forge_kwargs()` produces *arguments*;
  the forge's own pre-flight checks (duplicate role, routine cost floor,
  sandbox availability) still run and can still refuse.
- **Schedule anything.** `select_for_task` proposes; it dispatches nothing.
  There is no run created, no lease taken, no budget consumed.
- **Widen a role's authority.** A specialist cannot grant a tool its ring
  withholds. `role` names the ring; the ring is what
  `ToolPermissionGate` and the authority ceiling enforce, and both are
  downstream of the catalogue, not bypassed by it.
- **Bypass the approval gate.** `approvals` composes the forge's list into
  the SOUL. An `autonomous` specialist is the operator's explicit opt-out and
  nothing more — it does not disable the approval gate for a role that has one.
- **Change the roster at runtime.** There is no HTTP route, no model tool
  action, and no writer. Editing `config.yaml` is the only path, and the
  resolved config is never written back.
- **Pin a model.** There is no `model` field, by design. Routing is a slot, and
  `model_routing` is the only executable source of model names.
- **Promote itself.** A specialist's `title` is presentation. Its `role`,
  `capabilities`, `skills`, `department` and `reports_to` are leader-only at
  the profile level too, so a Bot cannot widen itself through a catalogue
  entry any more than it can through `bot_roster`.
- **Create its own manager.** `reports_to` orders the declaration and becomes
  `BotProfile.reports_to`, but a specialist pointing at another specialist that
  was never forged as a Bot has an escalation target nobody can receive the
  escalation. That is why the shipped team is flat.
- **Receive a signal, a broadcast, or a direct message.** The catalogue is
  push-free: a specialist is looked up by name, never told about work. There is
  no inbox, no subscription and no wake-up. That machinery exists for Bots
  (`alpha/bots/inbox.py`, `alpha/bots/dm.py`) and is deliberately not duplicated
  here.
- **Prove it was useful.** The catalogue has no telemetry. Whether a
  specialist earned its slot is a question about observed outcomes, and
  nothing here answers it.

Two smaller gaps, stated rather than hidden: the shipped team inherits
`technical-writer`'s `developer` shell access because no narrow writer ring
exists, and the shipped routing slots are unresolved until an operator
declares `model_routing` mappings.

---

## Operations

```bash
# Is the catalogue reachable from a running Gateway's config?
cd backend
PYTHONPATH=. .venv/Scripts/python.exe -c \
  "from alpha.config.specialist_config import get_specialist_catalog as g; print([e.name for e in g().enabled_specialists()])"
```

`GET /api/ops/integration-health` does **not** report the catalogue: it covers
tools, routers, middlewares, supervisor loops and capability engines, and this
section is none of those. Adding a counter there would mean a claim this
repository would then have to maintain.

Tests: `backend/tests/test_specialist_catalog.py`.

See also: [architecture](./ARCHITECTURE.md) for the team and enterprise layers,
[configuration](./CONFIGURATION.md) for the full `config.yaml` schema, and
[workforce](./WORKFORCE.md) for the runtime that would consume a selection.
