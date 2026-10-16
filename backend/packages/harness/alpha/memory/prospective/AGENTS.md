# Prospective memory lifecycle

`ProspectiveStore` owns per-user reminder/commitment records and their lifecycle.
The normal lead-agent integration is `agents/middlewares/prospective_memory_middleware.py`:
it captures only explicit user-authored “remind me to …” / “don't let me forget
to …” requests after a turn, and only when both `memory.enabled` and
`memory.prospective.enabled` are true. It requires a runtime-resolved non-default
owner and stable source identity; the source message ID/run ID generates a
deterministic record ID so retries are idempotent. `agent_name` remains a filter
inside the owner's partition.

Recall is composed through `memory/recall_composition.py`, with the same two
gates and an explicit user ID. Rendering is bounded and recalled content is
treated as untrusted data by the composition seam. Capture stamps a bounded due
time via `dates.py::parse_due_at`: an unambiguous explicit phrase (relative
offset, self-disambiguating clock phrase, day cue, weekday, `next week/month`)
becomes `item.due_at` plus `due_at`/`due_phrase`/`due_rule` metadata; ambiguous
or false-matched phrases return `None` and the reminder stays undated — never
dated by guesswork. This slice still does not schedule notifications, deliver
due-time reminders, guarantee cross-process exactly-once writes, or capture
commitments expressed without the supported explicit phrases. Keep the gates
default-off.

Regression coverage: `tests/test_prospective_dates.py`,
`tests/test_prospective_memory_middleware.py`,
`tests/test_memory_prospective.py`, and `tests/test_memory_recall_composition.py`.
