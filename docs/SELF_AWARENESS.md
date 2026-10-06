# Self-awareness: how Alpha knows what it is and what it can do

Alpha can report its own capability inventory. One model tool call returns the
whole picture — every tool, skill, MCP server, model, bot profile, slash command,
engine package, wired API router, memory subsystem, and the public repository it
came from — plus a read-only report of what is currently misconfigured.

It exists because the alternative was guessing. Before it, answering "do you have
a browser tool?" required already knowing that there were five separate inventory
planes, two of which the model could not reach at all. So the honest answer was a
plausible guess, and **a guessed capability claim is the most damaging error an
agent can make about itself** — it routes real work to something that does not
exist.

## The one call

```
alpha_capability(action="inventory")
```

```jsonc
{
  "success": true,
  "schema_version": "alpha.self-inventory.v1",
  "totals": { "sections": 11, "read_sections": 11, "entries_read": 931, "available_read": 518 },
  "unreadable_sections": [],
  "sections": [
    { "name": "identity", "status": "ok", "count": 4,    "available_count": 4,   "entries": [...] },
    { "name": "tools",    "status": "ok", "count": 132,  "available_count": 132, "entries": [...] },
    { "name": "skills",   "status": "ok", "count": 24,   "available_count": 24,  "entries": [...] },
    { "name": "mcp",      "status": "ok", "count": 6,    "available_count": 0,   "entries": [...] },
    // … models, bots, commands, capabilities, engines, wiring, memory
  ],
  "notes": ["availability is what the source of truth declares…"]
}
```

Every section is bounded, so one call cannot flood a context window. 466 commands
and 131 tools is roughly 40k characters that nobody should pay for to answer one
yes/no question.

## Actions

| Action | Answers | Notes |
| --- | --- | --- |
| `inventory` | What exists, across all 11 registries | `sections=` filters; `detail=full` adds source addresses |
| `identity` | Which public repository am I, what version/commit | Reads the shipped project manifest |
| `capability` | One entry, with its real source address | `kind=` + `entry_id=`; this is the drill-down |
| `search` | Is there anything called roughly like this? | Lexical, spans every registry kind |
| `status` | Can I introspect myself right now? | Cheap; no full read |
| `diagnose` | What is misconfigured, and what would fix it | **Read-only.** Never writes config |
| `symbols` | Where is this function/class actually defined? | Searches Alpha's own source tree |
| `symbol` | Every symbol under a name in one file | File-scoped drill-down |
| `symbols_status` | Roots, limits and pruning for the symbol index | |

### Identity

```
alpha_capability(action="identity")
```

```jsonc
"repository_facts": {
  "repository_provider": "github",
  "repository_owner": "itsPremkumar",
  "repository_name": "alpha",
  "repository_defaultBranch": "main",
  "repository_url": "https://github.com/itsPremkumar/alpha",
  "agentId": "alpha-e702e655",
  "os": "Windows", "arch": "AMD64",
  "gitCommit": "d603cc2",
  "alphaVersion": "2.1.0"
}
```

These are **read**, never composed. `alpha/evolution/identity.py` already refuses
to fabricate a commit: a failed `git rev-parse` yields `gitCommit: "unknown"` with
`gitCommitSource: "unavailable"` and the real reason as the note, and that shape is
passed through verbatim rather than smoothed into a null. An undeclared repository
field produces `unavailable` with a reason, never a guessed GitHub URL — a
confidently wrong provenance claim is worse than no claim.

### Symbols

```
alpha_capability(action="symbols", query="get_available_tools")
```

```jsonc
{ "name": "get_available_tools", "kind": "function", "language": "python",
  "path": "backend/packages/harness/alpha/tools/tools.py", "line": 429,
  "signature": "def get_available_tools",
  "doc": "Get all available tools from config.",
  "extraction": "ast" }
```

**Names, kinds, signatures, `path:line`, one-line docstrings — never a function
body.** To read a body, use `hashline_read` or `read_file` on the returned address:
an explicit, auditable act. This is a measured design choice, not an omission. A
3,000-turn ablation in `alpha/grounding/manifest.py` found an interface map drove
**67.8%** self-reuse while a full source dump drove **29.2%** — *worse than
receiving nothing*, because the agent copied structure whose invariants it could
not see.

Python signatures are AST-derived. TypeScript/TSX have no parser in the standard
library and none is added, so they use bounded regular expressions and every row
says `extraction: "regex"`. Presenting a regex signature beside an AST one without
labelling which is which would be exactly the quiet asymmetry this repo's honesty
rules exist to prevent. A regex can only *miss* a symbol; it cannot invent one.

A repo-wide search reads every source file (~2300 files, ~20 MB), so it costs
**seconds, not milliseconds**. Narrow it with `path_prefix`. Every response carries
a `coverage` block (`files_scanned`, `files_with_query`, `candidate_limit_reached`,
`parse_errors`) so a bounded answer says it was bounded.

## HTTP

The same projection is available over HTTP for operators and the frontend:

| Route | Purpose |
| --- | --- |
| `GET /api/intelligence/inventory` | Full inventory. `?sections=`, `?detail=`, `?query=`, `?limit=` |
| `GET /api/intelligence/inventory/status` | Which registries are readable right now |
| `GET /api/workflows/system/registries` | Per-kind descriptors + health, for the DWE planner |

`/api/intelligence/inventory` and `/api/intelligence/inventory/status` are declared
in that order for the same reason the skills and dynamic-workflow routers document
their ordering: Starlette matches routes in registration order, so a
single-segment catch-all declared first would answer `"Expert 'status' not found"`.

## The honesty contract

This is the part that matters more than the feature list.

**A failure is disclosed, never flattened.** Every section is computed through a
wrapper that converts a raising registry into `status: "unavailable"` with the real
exception text. One misconfigured MCP server must not make the whole inventory
useless.

**A count is never a claim.** `count` is measured from descriptors that call
actually read. `available_count` is a *separate* field, because "128 tools exist"
and "128 tools are usable" are different statements and only the first is ever true
from a static read. Nothing reports `health` as anything but `unverified` — no
registry executes what it lists.

**"Could not look" ≠ "looked and found nothing."** An unreadable section reports
`count: null`, never `0`. Those lead to opposite decisions.

**Version is only set where a source declares one.** Every descriptor carries
`version: null` except the runtime identity row, which resolves it from the
installed distribution. An invented version string is worse than none.

**A disabled capability is not a defect.** `diagnose` reports importable-but-disabled
optional subsystems at `info` severity, because a deliberate operator choice should
not train operators to ignore warnings.

**A proposal is never an action.** `diagnose` produces findings with remediation
text and writes nothing. See below.

## Why `diagnose` has no apply step

`config.yaml` and `extensions_config.json` are already API-writable through the
Gateway under the dual write locks in `alpha/extensions`. A model tool that could
rewrite them would be an **unaudited fourth writer** competing for the same files,
holding the agent's authority rather than the operator's — and the repo already has
a bounded principle for this: *a Bot may configure itself, but may not widen
itself*. So the diagnosis stops at a proposal and says so in every payload.

### What it checks

| Finding | Severity | Meaning |
| --- | --- | --- |
| `missing_config_file` | blocker | No usable `config.yaml`; the Gateway will not boot |
| `unreadable_config_file` | blocker | Carries the real validation error and field |
| `no_models_configured` | blocker | Alpha starts and cannot answer anything |
| `default_model_not_in_models` | blocker | A hot edit pointed at a removed model |
| `missing_api_key` | warning | Enabled provider, unset `api_key_env`. **Name only** |
| `sandbox_not_configured` | warning | File and shell tools have no execution boundary |
| `feature_manifest_missing` | warning | Engine and wiring counts cannot be reported |
| `unwired_manifest_row` | warning | A declared router/middleware/loop nothing references |
| `disabled_capability` | info | Importable but not enabled — an operator choice |
| `slash_command_without_handler` | info | The catalog advertises rows that cannot execute |

`missing_api_key` reports **only the environment variable name**. Reading a value
would put a credential into a model-facing payload, which is the one thing this
module must never do.

## Architecture

```
alpha/workflow/registry/          the 11 read-only sources, one descriptor shape
  base.py                         CapabilityDescriptor / RegistryHealth / protocol
  manifest_source.py              the ONE loader for contracts/feature_manifest.json
  identity.py models.py bots.py commands.py engines.py wiring.py
  capabilities.py tools.py skills.py mcp.py memory.py      (pre-existing)

alpha/intelligence/self_inventory.py    aggregate projection + bounded payloads
alpha/knowledge/code_index.py           bounded address-only symbol lookup
alpha/ops/config_diagnosis.py           read-only diagnosis + proposals
alpha/tools/builtins/alpha_capability_tool.py   the one model-facing tool

app/gateway/routers/intelligence.py      GET /inventory, /inventory/status
app/gateway/routers/workflows.py         GET /system/registries (discloses truncation)
```

### Two rules that decide the shape

**One source of truth per fact, never a re-derivation.** `engines` and `wiring`
read the generated `contracts/feature_manifest.json`; `models` reads the live
`AppConfig`. Every capability count in this repo is generated and drift-gated by
`scripts/check_generated_drift.py`, so a registry that formed its own opinion would
be an unreviewed second answer to a question the build already settles — the exact
drift class that has bitten this repo five times (89 → 97 → 99 engines, 127 → 130 →
134 tools, 55 → 57 → 60 → 61 routers, 8 → 9 loops).

**Availability is what the source says; health is what we probed.** A *declared*
model is `available` with `health: "unverified"`. An *enabled* MCP server is
`available` with `health: "unverified"`. Collapsing those two words is how
"configured" becomes "working" in a status line nobody checked.

## Two failures found and fixed while building this

Both were caught by measurement, not review, and both are the kind of silent
incompleteness this feature is supposed to make impossible.

**A whole-repo symbol index was unusable.** The first implementation built and
cached a symbol index on first use. It took **195 seconds**, and it hit its symbol
cap at 20,000 while walking roots **alphabetically** — so it truncated inside
`alpha/swarm/` and never reached `alpha/tools/`. A search for `get_available_tools`
returned **zero results**, from an index that reported itself as successfully built.
A silent partial index is worse than no index: the caller cannot tell a confident
absence from a bounded one. The index was replaced with a query-driven design
(bytes prefilter, then parse only real hits) that cut a query to ~5s and removed the
truncation class entirely, because completeness now depends only on whether the
string occurs in the file.

**The registries endpoint silently truncated.** `GET /api/workflows/system/registries`
had a bare `[:100]` descriptor slice. That was fine for five small registries and
wrong the moment `commands` (466 rows) and `engines` (115) joined: the slice dropped
rows with nothing in the response saying so. Every kind now reports `returned`
beside its measured `count` and sets `truncated`.

## Tests

`backend/tests/test_self_inventory_plane.py` — 75 tests in one file, because the
three layers only mean anything together: a registry's honesty contract is
invisible without the inventory that reports it, and the inventory is unreachable
without the tool the model calls. Split across three files, each test would be
asserting against mocks of its own neighbours.

Pinned there: no descriptor ever claims `health != "unverified"`; every
`unavailable` row carries a reason; `version` is set only on the runtime identity
row; an unreadable registry yields `count: null` and never `0`; the tool is
registered exactly once and is *not* in `SUBAGENT_TOOLS`; and `symbols` returns
address fields only — no `source`, no `body`, no `code`.

```bash
cd backend && PYTHONPATH=. uv run pytest tests/test_self_inventory_plane.py -v
```