---
name: project-cartographer
description: Use this skill when the user asks to document a codebase, map a repository, or generate AGENTS.md context files. Trigger on queries like "map this repo", "document the codebase", "generate AGENTS.md", "onboard me to this project", or before starting work in an unfamiliar repository. Produces directory-scoped AGENTS.md drafts for human review — never writes to the repository itself.
---

# Project Cartographer Skill

## Overview

Map an unfamiliar codebase and draft directory-scoped `AGENTS.md` context
files so agents (and humans) can navigate it. This skill is **read-only**:
explore with reads, searches, and listings; write every draft to the thread
outputs directory and present it for human approval. Never write `AGENTS.md`
files into the repository being mapped — the human copies approved drafts.

## When to Use This Skill

**Load this skill when:**
- User asks "map this repo", "document the codebase", "generate AGENTS.md"
- Starting work in an unfamiliar repository (onboarding pass)
- A directory has grown complex enough to deserve its own context file
- The user wants project conventions captured for future agent runs

**Do not use for:**
- Answering questions about code (just read the code directly)
- Editing or refactoring (use the coding workflow instead)
- Repositories the user has already mapped (check for existing `AGENTS.md` first)

## Methodology

### Phase 1: Survey (read-only)

1. **Top level first**: list the repo root. Identify the project kind
   (library, app, monorepo, docs site), the build system, the package
   manager, and the test runner from config files — never guess.
2. **Find existing context**: locate every `AGENTS.md`, `CLAUDE.md`,
   `CONTRIBUTING.md`, and README. Read them before forming any conclusion;
   never contradict established conventions.
3. **Map the architecture**: for each top-level directory, record in one
   line what lives there and what depends on it. Follow imports, not folder
   names — names lie, imports do not.
4. **Bound the work**: agree (with yourself, then the user) on which
   directories earn their own `AGENTS.md`. Rule of thumb: a directory earns
   one when it has its own conventions a newcomer would otherwise violate
   (naming, layering, generated code, test layout).

### Phase 2: Draft

For each selected directory, draft an `AGENTS.md` with exactly these sections:

1. **Purpose** — one paragraph: what this directory owns.
2. **Layout** — the important files/subdirectories and their roles.
3. **Conventions** — naming, layering, and workflow rules OBSERVED in the
   code, each with a file pointer. Never invent rules; every convention
   cites at least one path.
4. **Pitfalls** — the three most likely mistakes, each with what to do
   instead.

Keep each draft under 60 lines. Write drafts to the thread outputs
directory as `<dirname>-AGENTS.md-draft.md` and present them with
`present_files`.

### Phase 3: Handoff

Summarize for the user: which directories were mapped, which drafts await
approval, and the three most surprising findings. Tell the user explicitly
to copy approved drafts into the repository themselves.

## Pitfalls

- **Writing into the repo.** The cartographer never creates, edits, or
  moves repository files. Drafts live in outputs until a human moves them.
- **Invented conventions.** If a rule has no file pointer, delete the rule.
- **Mega-documents.** One directory, one short file. A 300-line AGENTS.md
  will be ignored; three focused ones will be read.
- **Stale maps.** Note the mapping date in the handoff summary; maps rot
  as code moves. Re-run the survey when the layout changes.

## Verification

Before presenting drafts, check every file pointer resolves, every cited
convention appears in the cited file, and no draft exceeds 60 lines.
