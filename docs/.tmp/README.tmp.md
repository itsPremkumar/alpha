# Working documents

Files here are **working material**, not published documentation.

## What belongs here

Audits, critiques, bug boards, implementation reports, and walkthroughs —
documents that record a point-in-time investigation and are kept for history and
review rather than read as current reference.

```
docs/.tmp/
├── ALPHA_AUDIT_REPORT.tmp.md          the end-to-end audit
├── ALPHA_AUDIT_REPORT_PART2.tmp.md    agent harness, persistence, memory
├── ALPHA_AUDIT_REPORT_PART3.tmp.md    security
├── ALPHA_AUDIT_REPORT_PART4.tmp.md    coding agent, synthesis, roadmap
├── AGI_ROADMAP_TRIAGE.tmp.md          AGI/ASI roadmap mapped to measured reality
└── FRONTIER_SUITE_WALKTHROUGH.tmp.md  frontier suite walkthrough
```

## Why the `.tmp.md` suffix is required, not cosmetic

`scripts/generate_docs_index.py` fails closed on any Markdown file under `docs/`
it cannot classify, and its skip list is:

```python
SKIP_FILE_PATTERNS = ("*.tmp.md",)
```

That suffix is what keeps these documents **out of the published
`docs/INDEX.md`** while leaving them in the repository for history. A file in
this folder named `AUDIT.md` would fail the generator; named `AUDIT.tmp.md` it is
skipped by design.

Two things enforce this and must agree:

- `scripts/generate_docs_index.py` — the generator, which owns the skip rules.
- `backend/tests/test_docs_claim_honesty.py::test_index_covers_every_document_in_docs`
  — asserts no real document is silently dropped. It **imports the generator's own
  skip constants** rather than hardcoding its own walk, because the test once
  disagreed with the tool it audits (it reported the correctly-skipped working
  documents as dropped).

If you add a document here, rename it to `*.tmp.md` and run
`python scripts/generate_docs_index.py`.

## What does *not* belong here

Anything describing how the system currently works. That belongs in `docs/`
properly classified, because the generated index is what search engines and
answer engines read.

## The other two root-level document locations

Documents are sorted by what they *are*, not by topic:

| Location | Holds |
| --- | --- |
| repository root | Convention-required files a reader expects there — `README.md`, `AGENTS.md`, `CHANGELOG.md`, `SECURITY.md`, `CONTRIBUTING.md`, `RELEASING.md`, `CODE_OF_CONDUCT.md` — plus files that **code or a test reads by name** (see below) |
| [`docs/`](..) | Current, published documentation. Every file is classified into `docs/INDEX.md`. |
| `docs/.tmp/` | This folder. Working material, indexed nowhere. |
| [`references/`](../../references) | Research and design material that informs the work but is not current project documentation. |

### Root files that cannot move, and why

A root document is load-bearing when something reads it by name. These were
verified by a reference scan of 3,932 files and stay put deliberately:

| File | Read by |
| --- | --- |
| `ENGINE_INVENTORY.md` | `scripts/generate_engine_inventory.py` writes it; `backend/AGENTS.md` links it |
| `Install.md` | `scripts/configure.py`, `AGENTS.md`, `CHANGELOG.md`, `self_documentation._ROOT_PRODUCT_DOCS` |
| `walkthrough.md` | `alpha/knowledge/self_documentation.py::_ROOT_PRODUCT_DOCS` |
| `BUG_FIX_BOARD.md` | `test_enc_audit_repo_text_integrity.py` uses it as a repository-surface fixture |
| `END_TO_END_CRITIQUE.md` | `test_docs_claim_honesty.py` `QUOTATION_ALLOWED` fixture |
| `IDEA.md` | `docs/INDEX.md` and `docs/llms.txt` link it |
| `MULTI_AGENT_PLAN.md`, `IMPROVEMENT_PLAN.md`, `AGENT_TRANSFER_GUIDE.md`, `AGENT_TOOLING_IMPLEMENTATION_PLAN.md` | Linked from other documents and/or named in source docstrings |

Moving any of them is a code change, not a file move. `self_documentation.py`
describes the product to the agent at runtime, so its `_ROOT_PRODUCT_DOCS` set is
an allowlist the agent sees — renaming a member changes agent-visible behaviour.