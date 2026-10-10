### Deep-agent working plane (`packages/harness/alpha/deepagent/`)

Alpha's port of LangChain's [`deepagents`](https://github.com/langchain-ai/deepagents)
state backend. Three files, each owning one thing:

| File | Owns |
| --- | --- |
| `workspace.py` | `VirtualWorkspace` protocol + `LocalWorkspace` (root-confined disk). Also `normalize_workspace_parts` — the **one** path rule every backend shares |
| `state.py` | `StateWorkspace` (in-graph-state), `WorkingFile`/`WorkingFileOp`, the `merge_working_files` channel reducer, and the module-level hard bounds |
| `composite.py` | `CompositeWorkspace` path-prefix routing and `Route.deny()` |

`index.py` renders the bounded projection; `config/deepagent_config.py`
configures it; `agents/middlewares/deepagent_context_middleware.py` injects it;
`tools/builtins/deepagent_tool.py` is the model-facing surface.

**One tool, not six.** `deepagents` gives its agent six filesystem tools
(`ls`/`read_file`/`write_file`/`edit_file`/`glob`/`grep`). Alpha exposes the
same capability as **one** tool with an `action` argument. The house rule: a new
capability is an action on an existing surface rather than a new registry entry,
so `BUILTIN_TOOLS` and `contracts/feature_manifest.json` stay comparable across
releases. The tool is appended by `append_deepagent_tools()` only while
`deepagent.enabled` is true — a deployment that wants the previous behaviour
sets one key and gets it, with no partial feature left behind.

**Order is recency; that is the whole design.**
A written path moves to the tail of the list. Eviction therefore drops from the
front, and no clock is needed. A wall-clock `updated_at` would make every
checkpoint non-deterministic and would make two identical runs produce two
different state blobs. Do not "improve" this into a timestamp.

**A delete is an operation, not a tombstone.**
The reducer accepts `WorkingFileOp` entries whose `content` is `None` and drops
the path outright. A stored tombstone could be resurrected by an older
concurrent write folding into the channel afterwards, which is the one failure a
scratch plane cannot have.

**Every bound is a refusal, never a clamp.**
`max_files`, `max_file_bytes`, `max_total_bytes` raise with the bound named, on
both the tool path and the reducer path. Silently truncating content would leave
a file whose bytes nobody wrote, and the model would keep reasoning over it.
Bytes are **UTF-8 measured**, never `len(content)` — `len("é")` is 1 but its size
is 2, so character counting would under-enforce every non-ASCII note.

**The channel ceiling is hard; configuration may only narrow it.**
`StateWorkspace(...)` refuses a bound wider than the module constant, because
the channel reducer has no config access and the state blob must stay bounded
regardless of what an operator set. `max_summary_chars: 0` disables summaries
and degrades the index to bare paths — that is a supported configuration, not a
broken one.

**Addresses only, never source.**
`render_working_index` names each file's path, measured size and the model's own
one-line `summary`, and never its body. Two reasons: a request carrying every
file's content is a prompt that grows with the run instead of staying flat, and
this repo has already measured the alternative — an interface map more than
doubles reuse of an agent's own earlier work while dumping full source achieves
nothing *and raises* duplication (`GroundingMiddleware`, whose test fails if
implementation text reaches the prompt).

**The payload rides the untrusted channel.** The plane is written by the model,
so its paths and summaries are model-supplied data. It is injected as a hidden
`HumanMessage`, never as system text — a summary the model wrote must not be
able to promote itself into a system instruction, even neutralised.
`DEEPAGENT_CONTEXT_MARKER` plus the reserved id prefix are both required before
the middleware sweeps its own message, so a client-chosen id is never droppable.

**What the plane is not.** It is not a fact store, not user memory, and not
verified: nothing here has been checked, and the tool's own docstring says so to
the model. It is the agent's record of its own work, and the reason it is worth
having is that compaction discards the message tail and keeps this channel.

**Registered in the shared runtime base**, so the lead agent *and* every
delegated subagent see the same plane. A child that can read what its parent
already established does not re-derive it in an isolated context window — that
is the property that makes delegation useful on a long task.

Tests: `tests/test_deepagent_working_plane.py`. Wiring gates that must stay
green: `tests/test_feature_manifest_wiring.py`,
`tests/test_no_orphan_modules.py`, `scripts/check_tool_schemas.py`.
