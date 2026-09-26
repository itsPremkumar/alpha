# Offline Self-Documentation Retrieval

Alpha's `search_project_docs` built-in gives the agent a grounded source of
truth for Alpha's own configuration, commands, architecture, and operational
contracts. It is deliberately local and free: the implementation uses only the
Python standard library and makes zero network, embedding, model, or database
calls. The tool's hidden runtime and server-side root discovery prevent the model
from selecting an arbitrary filesystem tree, and the model cannot force repeated
index rebuilds.

## Why this exists

Alpha has a large configuration surface and many subsystem contracts. An agent
that answers from model memory can invent a setting or describe a design
proposal as shipped behaviour. The self-documentation index closes that gap by
retrieving current, line-addressable repository evidence before the agent
answers.

The design follows patterns observed in current local-first agent projects:

- [Probe](https://github.com/zeroentropy-ai/probe) demonstrates ranked project
  documentation/code search, status, refresh, line references, bounded reads,
  and secret exclusion. Alpha deliberately uses lexical ranking rather than
  Probe's optional cloud embedding/reranking path.
- [Context](https://neuledge.com/context) demonstrates portable SQLite FTS/BM25
  documentation retrieval with no runtime network dependency.
- [exxperts](https://github.com/EXXETA/exxperts) demonstrates why local state
  should be inspectable, provenance-aware, and governed rather than hidden.
- [Kun](https://github.com/KunAgent/Kun) demonstrates the value of a local-first
  workspace where plans, evidence, and execution remain inspectable.

The product-specific proposal is recorded in
[`references/06-workbuddy-and-tenant-harness/borrowed-ideas-and-gap-map.md`](../references/06-workbuddy-and-tenant-harness/borrowed-ideas-and-gap-map.md)
as the C6 self-documentation gap.

## Tool actions

### Search current product evidence

```json
{
  "query": "scheduler queue timeout",
  "action": "search",
  "max_results": 5
}
```

Each result contains:

- relative `path`, `section`, and `start_line` / `end_line`;
- an authority label and human-readable authority note;
- a bounded `snippet` and matched terms;
- a SHA-256 `sha256` digest of the source content;
- `reference_only: false` for current shipped/product guidance.

The index uses a small BM25-style lexical ranker with authority weighting. It
requires at least one meaningful query term, so a random sentence does not
retrieve a document merely because it contains the project name.

### Read a verified source range

Pass a search result's `path` and `sha256` to `action: "read"`:

```json
{
  "action": "read",
  "path": "docs/CONFIGURATION.md",
  "start_line": 350,
  "max_lines": 40,
  "expected_sha256": "…"
}
```

The read is confined to the allowlisted source map. If the file changed after
search, the tool returns `source_changed` and asks the agent to search again
rather than silently serving mixed evidence. Reads are capped at 200 lines.

### Inspect index health

```json
{"action": "status"}
```

Status reports the schema version, content-derived `index_id`, source/chunk
counts, authority counts, skipped-file reasons, and explicit
`network_calls: 0` / `embedding_calls: 0` values.

## Authority and safety boundaries

| Source class | Default | Meaning |
| :--- | :--- | :--- |
| `agent_guidance` | Included | Current `AGENTS.md` guidance. |
| `config_schema` | Included | Shipped example configuration and package/schema metadata. |
| `product_docs` | Included | Current product, deployment, and operational docs. |
| `capability_docs` | Included | Public skill documentation. |
| `design_reference` | Opt-in | `references/` research and future design material. |

Reference results are always labelled `reference_only: true` and carry a
non-authoritative warning. They must not be used alone to claim that a feature
is implemented.

The scanner excludes runtime configuration (`.env`, `config.yaml`,
`extensions_config.json`), private/custom skills, dependency trees, generated
output, VCS metadata, symlinks, and files outside the discovered project root.
It also enforces file, total-byte, source-count, chunk-line, chunk-character,
result-count, and read-line ceilings. Every read re-resolves the indexed path,
rejects symlink/root escape or post-scan growth, and decodes UTF-8. A read/decode
failure is reported as a skipped source or structured read failure rather than
crashing a run.

## Cache and freshness

The process keeps a bounded in-memory cache keyed by project root and whether
references are enabled. Allowlisted file path, size, and modification time form
the refresh fingerprint; content SHA-256 values form the evidence/index ID.
Source-stat changes invalidate the cache automatically. The model-visible tool
has no root-selection or forced-refresh argument. No index is written into the
repository and no user data is persisted by this feature.

## Tests

`backend/tests/test_self_documentation_index.py` covers ranking, authority
labels, UTF-8, reference opt-in, secret/runtime exclusion, path confinement,
digest freshness, cache refresh, bounded reads, tool registration, and the
zero-network status contract. Run it with:

```bash
cd backend
uv run pytest tests/test_self_documentation_index.py -q
```
