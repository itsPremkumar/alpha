# Deep-agent working plane

Alpha's port of LangChain's [`deepagents`](https://github.com/langchain-ai/deepagents)
state backend. On a long, multi-step task the agent's findings, drafts, decisions
and open questions all live in the message tail — and the tail is exactly what
context compaction discards. A run that spent twenty turns establishing a picture
of your codebase can lose that picture and re-derive it.

The working plane is a bounded, thread-scoped scratch filesystem held in **graph
state**. It survives context compaction, a checkpoint resume and a Gateway
restart, and it is projected back into every model request as an index.

## What the agent sees

One tool, `deepagent_workspace`, with an `action` argument:

| Action | What it does |
| --- | --- |
| `index` | Every file — path, measured size, and the agent's own one-line purpose. No bodies |
| `ls` | Rows under a path |
| `read` | One file, paged by line |
| `write` | Create or replace a file |
| `edit` | Replace `old` with `new` inside one file |
| `delete` | Remove a file |
| `glob` | Paths matching a `/` glob |
| `grep` | Lines matching a regular expression |

The index is the point. It carries **addresses, never bodies** — so a request
that lists ten notes costs the same as one that lists two, and a note the agent
can see but has not opened is not a note it is paying to carry. This repo measured
the alternative: an interface map more than doubles reuse of an agent's own
earlier work, while dumping full source achieves nothing and *raises* duplication.

## Configuration

```yaml
deepagent:
  enabled: true          # master switch; false = previous behaviour exactly
  inject_index: true     # project the index into every model request
  max_files: 64          # most files per thread
  max_file_bytes: 32768  # ceiling on one file
  max_total_bytes: 262144 # ceiling on the whole plane
  max_summary_chars: 160 # ceiling on the per-file one-line purpose; 0 disables summaries
```

Every bound is a **refusal, never a clamp**. An over-bounds write names the number
it hit, so the agent splits the note or deletes another file rather than
silently receiving a truncated file whose bytes nobody wrote. The channel ceiling
is hard; this configuration may only narrow it.

## Where it applies

Registered in the shared runtime base, so the **lead agent and every delegated
subagent** see the same plane. A child that reads what its parent already
established does not re-derive it in an isolated context window — which is the
property that makes delegation useful on a long task rather than a way to lose
context.

## What it is not

Not a fact store, not user memory, and not verified. Nothing in the plane has been
checked; it is the agent's record of its own work, and the tool's own docstring
says so to the model.

For the four load-bearing invariants and the reasoning behind them, see
[`backend/packages/harness/alpha/deepagent/AGENTS.md`](../backend/packages/harness/alpha/deepagent/AGENTS.md).
