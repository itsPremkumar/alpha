# Claude Arena Skill — Complete Research Dossier

> **Subject:** `/arena` — a Claude Code skill by Jake Schincariol (`Jakeschincariol/arena-skill`)
> **Repository:** <https://github.com/Jakeschincariol/arena-skill>
> **Author:** Jake Schincariol — <https://opusjake.ai> · <https://github.com/Jakeschincariol>
> **License:** MIT (Copyright (c) 2026 Jake Schincariol)
> **Primary language:** Python (standard library only)
> **Document compiled:** 10 October 2026

This document is a complete, first-principles distillation of everything publicly discoverable
about the Arena skill: what it is, how it works internally, every strategy card it can deal, the
full judging rubric, the complete CLI of its state machine, its real measured cost, its
limitations, its community reception, and the forks it spawned.

---

## Table of Contents

1. [What it is](#1-what-it-is)
2. [Project identity and repository metrics](#2-project-identity-and-repository-metrics)
3. [Requirements](#3-requirements)
4. [Installation](#4-installation)
5. [Usage and flags](#5-usage-and-flags)
6. [Architecture: the tournament loop](#6-architecture-the-tournament-loop)
7. [The strategy card system](#7-the-strategy-card-system)
8. [The dealer: how cards are dealt](#8-the-dealer-how-cards-are-dealt)
9. [Bracket mechanics](#9-bracket-mechanics)
10. [The attack phase](#10-the-attack-phase)
11. [The defend phase](#11-the-defend-phase)
12. [The judging rubric](#12-the-judging-rubric)
13. [The final check](#13-the-final-check)
14. [Verdict arithmetic and the fatal rule](#14-verdict-arithmetic-and-the-fatal-rule)
15. [`bracket.py` — the complete CLI](#15-bracketpy--the-complete-cli)
16. [On-disk layout of a run](#16-on-disk-layout-of-a-run)
17. [Cost model](#17-cost-model)
18. [Independently measured benchmark](#18-independently-measured-benchmark)
19. [The sub-agent prompt templates](#19-the-sub-agent-prompt-templates)
20. [Rules for the orchestrator](#20-rules-for-the-orchestrator)
21. [Safety model](#21-safety-model)
22. [Honesty and limitations](#22-honesty-and-limitations)
23. [Failure handling and recovery](#23-failure-handling-and-recovery)
24. [The test suite](#24-the-test-suite)
25. [Best practices](#25-best-practices)
26. [Community reception and coverage](#26-community-reception-and-coverage)
27. [Forks and derivatives](#27-forks-and-derivatives)
28. [Sibling skills by the same author](#28-sibling-skills-by-the-same-author)
29. [Known issues and open pull requests](#29-known-issues-and-open-pull-requests)
30. [Design patterns worth stealing](#30-design-patterns-worth-stealing)
31. [Sources](#31-sources)

---

## 1. What it is

**Arena** is a free, MIT-licensed skill for Claude Code that turns one task into a single-elimination
tournament between N copies of the same model.

The pitch, verbatim from the repository:

> "When Claude keeps giving you bad answers, make 100 versions of it fight to the death. Same task,
> 100 different strategies, a bracket, one answer left. Free Claude Code skill."

The problem it addresses is the **re-prompting loop**: you ask Claude something, dislike the
answer, ask again, and get the same answer reworded. Arena replaces "ask again, hope for better"
with "sample the answer distribution 100 times through different reasoning strategies, then have
those answers attack, defend and score each other until only one survives."

### 1.1 The one-paragraph mechanical summary

`/arena` spawns N sub-agents, gives every one the **exact same task text, byte for byte**, plus one
unique **strategy card** (a reasoning mode, a workflow and a strategy). Each writes an independent
solution. Solutions are then paired. In each match, both sides **attack** the other's solution, both
**defend and revise** their own in response, and a separate **judge** sub-agent scores both on a
written rubric. The higher weighted total advances; the loser is eliminated. The field halves every
round (100 → 50 → 25 → 13 → 7 → 4 → 2 → 1) until one solution survives. You get the survivor, the
attacks it beat, its card, and the round count. If you started from an answer you rejected, a final
blind judge compares the champion against your rejected answer and reports the score either way.

### 1.2 What it is *not*

- **Not 100 different models.** All competitors are sub-agents of the model you are already running.
  The diversity comes entirely from the card, not from model choice.
- **Not a verification engine.** The judges are Claude too. A survived tournament is evidence the
  answer withstood adversarial scrutiny from the same model family; it is not proof of correctness.
- **Not autonomous.** It never edits your project. Code changes come back as diffs or full files
  inside the winning answer, and the skill asks before applying anything.

---

## 2. Project identity and repository metrics

| Field | Value |
| --- | --- |
| Full name | `Jakeschincariol/arena-skill` |
| Branch | `main` (default) |
| Created | 2026-09-27 13:20:04 UTC |
| Last push | 2026-09-27 13:20:07 UTC (single commit, whole repo in one commit) |
| Commits | **1** |
| Language | Python |
| License | MIT |
| Size | 37 KB |
| Stars | **403** (as of 2026-10-10) |
| Forks | **57** (50 forks returned by the API paginated at 30 shown; 58 reported by the author's repo listing) |
| Watchers | 5 |
| Open issues | 3 |
| Open PRs | 1 |
| Topics | `arena`, `claude-code`, `claude-skills`, `multi-agent`, `prompting`, `subagents`, `tournament` |
| Plugin name | `arena-skill` |
| Plugin version | `1.0.0` |
| Marketplace category | `productivity` |

### 2.1 Repository file manifest

```
.claude-plugin/
  marketplace.json      # marketplace registration, category "productivity"
  plugin.json           # plugin manifest, v1.0.0, keywords, author, MIT
skills/arena/
  SKILL.md              # 18.6 KB — orchestration steps + the 5 prompt templates
  bracket.py            # 48.3 KB (49,482 bytes) — the tournament state machine, stdlib only
  rubric.md             # 3.6 KB — the five judging criteria, weights and anchors
  strategies.json       # 9.0 KB — 15 reasoning modes, 12 workflows, 12 strategies
tests/
  test_bracket.py       # 19 KB — full test suite
.gitignore              # .DS_Store, __pycache__/, *.pyc, .venv/, *.tmp, .arena/
LICENSE                 # MIT
README.md               # 10.3 KB
```

**Note the count:** the entire skill is four source files. There is no dependency, no package, no
API key, no network call and no build step.

### 2.2 Growth timeline

- **2026-09-27** — repository created, single commit.
- **2026-09-28** — indexed by Skills Directory (security grade A, 100/100).
- **2026-09-29** — a third-party guide measured the repo at **two days old and 65 stars**.
- **2026-09-30** — issue #1 filed: "It will exhaust all your token in first prompt." A community
  member (`mersad31`) published a fully measured benchmark in the issue thread and opened PR #2.
- **2026-10-03** — issue #3 opened ("Claude code").
- **2026-10-08** — first notable derivative fork (`IcaruzSoftware/round-table-skill`).
- **2026-10-10** — 403 stars, 57 forks. Viral on Threads, Instagram, TikTok and LinkedIn under the
  tag `@theaiimpact`.

That is 65 → 403 stars in eleven days: a 6.2× adoption curve, which places Arena among the
fastest-growing of the author's (already highly-starred) skill packs.

---

## 3. Requirements

| Requirement | Detail |
| --- | --- |
| **Claude Code** | Mandatory. Arena spawns sub-agents with the Agent tool (called `Task` in older Claude Code versions). It does **not** work in a plain claude.ai chat. On the Claude desktop app, the **Code** tab is the environment that has this capability. |
| **Python 3.8 or newer** | `bracket.py` is standard-library only. |
| **Nothing to install** | No `pip install`, no package, no API key, no signup, no account, no network. |
| **A paid Claude plan with headroom** | The skill is free; the tokens are yours. See [§17](#17-cost-model) and [§18](#18-independently-measured-benchmark). |
| **Disk write access in the working directory** | All sub-agent output lands in `.arena/` in the directory you ran `init` from. |
| **Recommended: accept-edits mode** | Shift+Tab in Claude Code. Otherwise you approve one file per sub-agent write, which is hundreds of prompts on a full run. |

---

## 4. Installation

There are three supported install paths.

### 4.1 Let Claude do it (recommended)

Paste the repository URL into Claude Code with an instruction:

```text
https://github.com/Jakeschincariol/arena-skill

Install this skill, then confirm /arena works.
```

### 4.2 Manual global copy

```bash
git clone https://github.com/Jakeschincariol/arena-skill.git
cp -r arena-skill/skills/arena ~/.claude/skills/
```

For a **project-local** install instead, copy the same folder into the repository's
`.claude/skills/` directory.

### 4.3 As a Claude Code plugin

```text
/plugin marketplace add Jakeschincariol/arena-skill
/plugin install arena-skill@arena-skill
```

> **Namespacing gotcha:** Claude Code namespaces plugin skills, so a plugin install registers the
> command as **`/arena-skill:arena`**, not `/arena`. If you want the bare `/arena` command, copy
> the folder manually (§4.2).

### 4.4 Programmatic install

Skills Directory publishes an npx one-liner that installs into the current project's
`.claude/skills`:

```bash
npx -y skills add Jakeschincariol/arena-skill --skill arena --agent claude-code
```

### 4.5 Uninstall

Remove `~/.claude/skills/arena` (or the project's `.claude/skills/arena`), or ask Claude in the
Code tab to remove the skill. Run artifacts under `.arena/` are gitignored by the skill's own
`.gitignore` and can be deleted freely.

---

## 5. Usage and flags

### 5.1 Invocation examples

```bash
/arena
/arena --quick write the headline for our pricing page
/arena --agents 32 fix the flaky test in tests/test_api.py
/arena --seed 7 plan my launch week, I have 6 hours a day
```

### 5.2 Flags

| Flag | Meaning | Default |
| --- | --- | --- |
| `--agents N` | Number of competitors. Valid range 1 → 2,160 (the number of distinct cards). | `100` |
| `--quick` | Shorthand for 16 competitors. The everyday setting. | off |
| `--seed S` | Fixes the strategy cards dealt *and* the pairings. Random if omitted, and always recorded. | random |
| `--wave W` | Sub-agents launched per wave. | `10` |
| `--task` | The task as a literal string (used by `init`, not by the slash command). | — |
| `--task-file` | The task, word for word, from a file. | — |
| `--baseline-file` | The answer the user was not satisfied with. | — |
| `--dir` | Explicit run directory instead of the one in `.arena/LATEST`. | — |

Guard rails: `--quick` and `--agents` together is a hard error. `--agents 0` and `--agents 2161`
are hard errors. An empty or whitespace-only task is a hard error.

### 5.3 The three ways it fires

1. **Explicit.** The user typed `/arena` or said "arena" or "make them compete."
2. **Implicit on dissatisfaction.** The user says "that's a bad answer", "try again", "do better."
   In this case, and *only* in this case, the skill asks once before spending anything, and offers
   three options: the full arena (100 agents, 595 sub-agent calls), `--quick` (16 agents, 91
   calls), or an ordinary retry. It waits for an answer.
3. **Bare `/arena`.** With no task text, the skill takes the task to be the user's most recent
   request in the conversation, and the answer to beat is the assistant's last reply to it. The
   rejected answer is written verbatim into `.arena/baseline.md`.

### 5.4 Argument hint (skill frontmatter)

```yaml
---
name: arena
description: >-
  Make 100 versions of Claude fight to the death over one task. Spins up N
  sub-agents (default 100, --quick for 16), gives every one the exact same
  task plus a different strategy card (reasoning mode, workflow, strategy),
  then runs a single-elimination bracket: they attack each other's solutions,
  defend and revise, and a judge scores every match on a written rubric until
  one solution survives. Use when the user is not satisfied with an answer,
  calls it a bad answer, says try again or do better, says "arena", or asks
  to make them compete.
argument-hint: "[--agents N | --quick] [--seed S] <task>"
---
```

The `description` field is the trigger surface: it is written so that dissatisfaction language
("bad answer", "try again", "do better") activates the skill without a slash command.

---

## 6. Architecture: the tournament loop

The skill is split into two responsibilities with a hard boundary between them.

| Component | Responsibility |
| --- | --- |
| **`SKILL.md`** | The orchestrator's instructions. Tells the main Claude session what to do next, how to launch sub-agents, and what never to do. |
| **`bracket.py`** | All bookkeeping. Cards, pairings, state, verdict arithmetic, prompt rendering, reporting. The orchestrator never holds state in context. |

> **The orchestrator never competes, never attacks, never judges, and never picks a winner.** It
> runs the loop and relays results. That separation is stated four separate times in `SKILL.md`.

### 6.1 The five phases

`PHASES = ("spawn", "attack", "defend", "judge", "final")`

1. **Spawn** (once) — every competitor writes its own solution to the task.
2. **Per round**, in fixed order:
   - **attack** — two sub-agent calls per match (each side attacks the other).
   - **defend** — two sub-agent calls per match (each side rebuts/concedes and rewrites).
   - **judge** — one sub-agent call per match.
   - **collect** — `bracket.py` reads every verdict JSON and records the winners.
   - **advance** — `bracket.py` closes the round, eliminates losers, pairs survivors.
3. **final** (once, and only when a baseline exists) — a blind judge compares the champion against
   the answer the user rejected.
4. **DONE** — `bracket.py winner` produces the report.

### 6.2 Call arithmetic

`CALLS_PER_MATCH = 5` (2 attacks + 2 defenses + 1 judge).

For a round starting with `alive` competitors:

- matches `m = alive // 2`
- calls = `5 * m`
- waves = `2 * ceil(2m / wave) + ceil(m / wave)` (attack waves + defend waves + judge waves)

Round 1 of a 100-agent run: m = 50, calls = 250, waves = 25. This is where the README's table comes
from; `bracket.py plan` computes it, and `tests/test_bracket.py::Plan::test_numbers` pins
`(7, 595, 70)` for 100 agents and `(4, 91, 16)` for 16.

### 6.3 Why waves

Claude Code runs at most 10 tool calls concurrently by default, controlled by the
`CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY` setting. So a 100-agent spawn is 10 waves of 10, and the
whole 100-agent tournament is 70 waves (71 including the final check). If you raise the concurrency
limit, pass `--wave` to match, or the orchestrator will over-serialize.

### 6.4 The context-budget design

This is the most interesting architectural decision in the skill.

**The main session never reads the work.** Every sub-agent writes its solution, attack, defense or
verdict to disk and replies with exactly one line:

```text
DONE a017 843            # competitor finished, 843 words
ATTACKED a042 5 (1 fatal)  # 5 attacks, 1 fatal
DEFENDED a017 conceded 2 rebutted 3
WINNER a042 78.4-63.1
```

So a 595-call run adds roughly 595 short lines to the main context instead of 595 documents. The
main context holds **receipts and bookkeeping, not the work**.

The consequence: **the run survives context compaction.** If the conversation is compacted halfway
through, `bracket.py next` reconstructs the next step purely from `arena.json` on disk, and the run
continues. `status` and `pairings` are the only state-inspection commands the orchestrator needs
during a run.

### 6.5 The single source of truth for prompts

The five sub-agent prompt templates are **not** in a separate file. They live inside `SKILL.md`
inside HTML-comment markers:

```html
<!-- template:competitor -->
```text
...prompt body with {{placeholders}}...
```
<!-- /template:competitor -->
```

`bracket.py` extracts them at runtime with
`_TEMPLATE_RE = re.compile(r"<!-- template:([a-z]+) -->\s*```text\n(.*?)\n```\s*<!-- /template:\1 -->", re.S)`
and refuses to run if any of the five is missing:

```python
missing = [t for t in TEMPLATES if t not in found]
if missing:
    raise ArenaError("SKILL.md is missing prompt templates: %s" % ", ".join(missing))
```

`fill()` does a **single-pass** substitution, so a task that itself contains `{{braces}}` passes
through untouched. The full-pipeline test asserts exactly this, with a task containing
`{{name}}` and `{not_a_placeholder}`.

**Why this matters:** the briefs a sub-agent receives are literally the text a human reads in
`SKILL.md`. There is no second, hidden prompt. And a test,

```python
def test_templates_are_all_in_skill_md(self):
    self.assertEqual(sorted(B.load_templates()), sorted(B.TEMPLATES))
```

guarantees the extraction keeps working as `SKILL.md` evolves.

---

## 7. The strategy card system

Every competitor receives exactly one card. A card has three parts.

| Part | Count | Role |
| --- | --- | --- |
| Reasoning mode | **15** | *How* to think about the task. |
| Workflow | **12** | The *process* to execute, in order. |
| Strategy | **12** | The *trade-off posture* to settle every judgement call. |

**15 × 12 × 12 = 2,160 distinct cards.** `bracket.py` deals with no repeats and spreads all three
dimensions as evenly as possible, so a 100-agent arena uses **every** reasoning mode, **every**
workflow and **every** strategy at least six times.

The `about` field in `strategies.json` states the intent plainly:

> "Every competitor gets the same task and exactly one card: one reasoning mode, one workflow, one
> strategy. 15 x 12 x 12 = 2,160 distinct cards. bracket.py deals them with no repeats and spreads
> them evenly, so a 100-agent arena uses every reasoning mode, every workflow and every strategy.
> **Edit freely: add an entry and the dealer picks it up.** Ids must stay unique within a list."

> **This is an extensibility contract.** The dealer reads `strategies.json` at runtime, validates
> that every entry has a unique `id`, a `name` and a `how`, and rejects the file otherwise. Adding a
> 16th reasoning mode immediately changes the deal space to 2,304 cards and requires no code change.
> The maximum competitor count is derived from the card count, so `--agents 2161` is a hard error
> rather than a silent duplicate deal.

### 7.1 The 15 reasoning modes (verbatim)

| # | Id | Name | How |
| --- | --- | --- | --- |
| 1 | `first-principles` | First principles | Strip the task down to the facts and requirements nobody can argue with. Rebuild the answer from those alone, and keep a convention only if it earns its place again. |
| 2 | `inversion` | Inversion | Ask what would make this answer fail. List every way it could, then build the answer that makes each of those failures impossible. |
| 3 | `analogy` | Analogy | Find a problem in a different field that has already been solved and has the same shape. Port its solution over, then fix every place where the analogy breaks. |
| 4 | `adversarial` | Adversarial | Picture the harshest expert reviewer trying to tear the answer apart. Write their attacks down first and build the answer backwards from them. |
| 5 | `constraint-first` | Constraint first | List every hard constraint, stated and implied: time, budget, tools, format, audience, platform. Solve strictly inside that box and let the tightest constraint drive the design. |
| 6 | `worked-example` | Worked example | Solve one concrete, realistic instance end to end first, with real values. Generalise only from what that example actually taught you. |
| 7 | `socratic` | Socratic | Interrogate the task with the questions a sharp expert would ask before starting. Answer each one explicitly and let the answers decide the shape of the solution. |
| 8 | `contrarian` | Contrarian | Write down the obvious default answer, then argue seriously against it. Keep only what survives the argument, even if that takes you the unpopular way. |
| 9 | `systems-thinking` | Systems thinking | Map the moving parts, how they affect each other, and what happens second and third order. Optimise the whole system, not just the one part the task points at. |
| 10 | `decomposition` | Decomposition | Break the task into independent sub-problems. Solve each one completely, then integrate them and test the seams where they meet. |
| 11 | `working-backwards` | Working backwards | Describe the finished result precisely, as if it already exists. Then work backwards, step by step, to where the user is right now. |
| 12 | `probabilistic` | Probabilistic | Treat every claim and choice as uncertain. Weigh what is likely, prefer what holds up across most scenarios, and state your confidence where it changes the decision. |
| 13 | `dialectical` | Dialectical | State the strongest version of one answer, then the strongest opposing answer, then write the synthesis that keeps what is true in both. |
| 14 | `evidence-first` | Evidence first | Ground every claim in something checkable: the task text, the files it points to, the numbers given, documentation. Anything you cannot check gets labelled as an assumption. |
| 15 | `expert-panel` | Expert panel | Answer the task as three different experts would, picking roles that fit it. Take the strongest idea from each and settle every point where they disagree. |

### 7.2 The 12 workflows (verbatim)

| # | Id | Name | How |
| --- | --- | --- | --- |
| 1 | `draft-critique-rewrite` | Draft, critique, rewrite | Write a complete first draft. Critique it line by line against the task as if someone else wrote it. Then rewrite it from scratch using the critique. |
| 2 | `outline-first` | Outline first | Write a skeleton outline, check the outline against every requirement in the task, fix it, and only then fill it in. |
| 3 | `test-first` | Test first | Before writing the answer, write the checks it must pass: acceptance tests for code, pass or fail criteria for anything else. Then write the answer and run every check against it. |
| 4 | `research-then-synthesise` | Research, then synthesise | Gather every relevant fact first: the task, any files or context it points to, what is known about the domain. Write nothing until the research is done, then synthesise. |
| 5 | `three-drafts` | Three drafts, pick one | Write three short, genuinely different approaches. Pick the strongest on the task's own terms and develop only that one fully. |
| 6 | `requirements-checklist` | Requirements checklist | Pull every requirement out of the task into a numbered list. Solve, then tick each item off and say exactly where it is met. |
| 7 | `iterative-deepening` | Iterative deepening | Write a one-paragraph answer first. Then expand the parts that matter most in two more passes, each one deeper than the last. |
| 8 | `build-then-break` | Build, then break | Build the answer. Then throw five hostile inputs or scenarios at it, write down what breaks, and patch every break. |
| 9 | `smallest-version-first` | Smallest version first | Produce the smallest complete version that actually works. Then extend it one step at a time, keeping it working after every step. |
| 10 | `options-matrix` | Options matrix | List the realistic options, score them in a small matrix against the criteria that matter for this task, choose one, and write up the choice. |
| 11 | `open-questions-first` | Open questions first | List every open question the task leaves. Resolve each one with reasoning or a stated assumption, then answer. |
| 12 | `write-then-restructure` | Write, then restructure | Write freely and fast. Then outline what you actually wrote, fix the structure, and rewrite it to the new outline. |

### 7.3 The 12 strategies (verbatim)

| # | Id | Name | How |
| --- | --- | --- | --- |
| 1 | `simplest` | Simplest thing that works | Pick the least complicated answer that fully does the job. Every extra part has to justify itself. |
| 2 | `maximal-rigour` | Maximal rigour | Be exhaustive and exact. Check every step, verify every claim, and leave no hand-waving anywhere. |
| 3 | `user-empathy` | User empathy first | Start from the person who will use this: their situation, their skill level, and what they will actually do next. Shape everything around that. |
| 4 | `edge-cases-first` | Edge cases first | Hunt the edge cases, failure modes and weird inputs first, and design the answer around surviving them. |
| 5 | `speed` | Speed | Optimise for the fastest route to something usable today. Cut anything that does not move the result. |
| 6 | `defensive` | Defensive | Assume the inputs are bad and the conditions are hostile. Make the answer fail safely and loudly rather than quietly. |
| 7 | `clarity` | Clarity above all | Make it the easiest answer to read and act on: plain words, obvious structure, nothing the reader has to decode. |
| 8 | `completeness` | Completeness | Leave out nothing the task asks for or clearly implies. Cover every requirement explicitly. |
| 9 | `fewest-moving-parts` | Fewest moving parts | Remove things until it breaks, then put the last one back. Fewer steps, fewer dependencies, fewer words. |
| 10 | `explicit-trade-offs` | Explicit trade-offs | Name the real trade-offs, choose for the user's most likely constraints, and say plainly what that choice gives up. |
| 11 | `built-to-last` | Built to last | Optimise for how this holds up in six months: easy to maintain, easy to extend, no traps for the next person. |
| 12 | `concrete-specifics` | Concrete specifics | Exact numbers, names, commands, file paths and steps. Zero generic advice. |

### 7.4 How the card is used in the spawn brief

The card is injected into the brief verbatim, and the instructions around it are deliberately
non-negotiable:

```text
=== YOUR STRATEGY CARD ===
Reasoning mode: {{reasoning_name}}. {{reasoning_how}}
Workflow: {{workflow_name}}. {{workflow_how}}
Strategy: {{strategy_name}}. {{strategy_how}}
=== END OF THE CARD ===

How to work:
1. Use the card for real. Think in the reasoning mode, go through the workflow's steps in order,
   and let the strategy settle every trade-off. **A generic answer with the card's name on top
   will lose.**
2. Meet every requirement the task states. The judge scores you against the task, not against
   your card.
```

Note rule 2: the card is a *how*, never a *what*. The rubric's completeness criterion judges against
the task text, so a competitor cannot win by being a faithful practitioner of a strange method.

---

## 8. The dealer: how cards are dealt

`deal(n, seed, data)` is the most mathematically careful function in the file. Its docstring states
the guarantees:

> - no card is dealt twice
> - every reasoning mode, workflow and strategy is dealt as evenly as possible (any two counts
>   differ by at most 1), so 100 agents use all of them
> - while every reasoning mode and every workflow has no more agents than there are strategies
>   (up to 144 agents with the shipped cards): no two agents share a reasoning mode and a workflow,
>   a reasoning mode and a strategy, or a workflow and a strategy. **Any two agents differ in at
>   least two of the three.**

### 8.1 The construction

```python
R, W, S = len(data["reasoning"]), len(data["workflows"]), len(data["strategies"])
total = R * W * S
if n < 1 or n > total:
    raise ArenaError("can deal between 1 and %d cards, not %d" % (total, n))
rng = random.Random("arena-deal:%s" % seed)
pr, pw, ps = list(range(R)), list(range(W)), list(range(S))
rng.shuffle(pr); rng.shuffle(pw); rng.shuffle(ps)
lcm = R * W // math.gcd(R, W)
cells = [(i % R, (i + i // lcm) % W) for i in range(n)]
deg_r = max(sum(1 for r, _ in cells if r == x) for x in range(R))
deg_w = max(sum(1 for _, w in cells if w == x) for x in range(W))
```

Three things are worth calling out:

1. **Seeded by a string, not an int.** `random.Random("arena-deal:%s" % seed)` makes the shuffle
   reproducible across Python versions and platforms for the same seed and the same
   `strategies.json`.
2. **The reasoning × workflow grid is built, not sampled.** `cells[i] = (i % R, (i + i // lcm) % W)`
   walks a lattice with period `lcm(R, W) = lcm(15, 12) = 60`. This never repeats a workflow inside
   a reasoning mode and stays balanced. Every agent therefore occupies a **distinct (reasoning,
   workflow) cell**.
3. **Labels are shuffled before assignment**, so agent `a001` is not systematically the
   "first-principles" competitor.

### 8.2 The edge-colouring step

The strategies are then assigned as a **balanced proper edge colouring** of the bipartite
reasoning × workflow graph:

- nodes on side 0 = the 15 reasoning modes
- nodes on side 1 = the 12 workflows
- edges = agent slots (one per agent, connecting its reasoning node to its workflow node)
- colours = the 12 strategies

`_edge_colour(edges, k)` implements König's theorem: every bipartite graph has a proper edge
colouring with `k` colours where `k` is the maximum degree. Proper means no two edges meeting at
the same node share a colour — so no two agents with the same reasoning mode ever get the same
strategy, and no two agents with the same workflow ever get the same strategy. Combined with
distinct cells, this is exactly the "differ in at least two of the three" guarantee.

The colouring is found with alternating-path swaps, then **balanced** by repeatedly swapping along
paths from the most-used colour to the least-used until every colour class differs by at most one
(de Werra's approach). If it cannot balance, it raises
`ArenaError("could not balance the strategy cards")` rather than returning a skewed deal.

### 8.3 The 144-agent boundary

The proper-colouring path only runs when the graph's maximum degree is at most `S = 12`:

```python
if deg_r <= S and deg_w <= S:
    ... proper edge colouring ...
else:
    # Past that size a repeat pair is unavoidable. Stay balanced and never repeat a card.
```

With 12 workflows, `deg_w = ceil(n / 12) <= 12` requires `n <= 144`. At 145 competitors a repeated
pair becomes unavoidable, so the dealer falls back to a greedy assignment that **never repeats a
full card** and stays as balanced and as pair-sparse as it can:

```python
s = min((c for c in range(S) if (r, w, c) not in used),
        key=lambda c: (count[c] - low, pairs_rs.get((r, c), 0), pairs_ws.get((w, c), 0), c))
```

The README's phrasing — "up to 144 agents, no two agents even share two of their three parts" —
is this exact boundary, and it is a hard fact about the shipped card set, not a tunable.

### 8.4 What the tests pin

```python
def test_card_count(self):
    self.assertEqual(len(self.data["reasoning"]), 15)
    self.assertEqual(len(self.data["workflows"]), 12)
    self.assertEqual(len(self.data["strategies"]), 12)
    self.assertEqual(B.combo_count(self.data), 2160)

def test_guarantees_hold_across_sizes_and_seeds(self):
    for n in (1, 2, 7, 16, 32, 50, 99, 100, 101, 144):
        for seed in range(25):
            self.check(n, seed)
    for n in (145, 180, 500, 2160):
        self.check(n, 1)

def test_deterministic_for_a_seed(self):
    self.assertEqual(B.deal(100, 42, self.data), B.deal(100, 42, self.data))
    self.assertNotEqual(B.deal(100, 42, self.data), B.deal(100, 43, self.data))

def test_pairing_prefers_different_reasoning_modes(self):
    for seed in range(50):
        ...
        self.assertLessEqual(same, 1, "at most one forced same-mode match in a 50-match round")
```

Note the last one: across 50 different seeds, a 100-agent round 1 has **at most one** match where
both competitors share a reasoning mode. In practice, every attack therefore comes from a genuinely
different angle.

---

## 9. Bracket mechanics

### 9.1 Alive-count progression

```python
def bracket_sizes(n):
    """Alive count at the start of each round, ending at 1.
    100 -> 50 -> 25 -> 13 -> 7 -> 4 -> 2 -> 1."""
    sizes = [n]
    while sizes[-1] > 1:
        sizes.append((sizes[-1] + 1) // 2)
    return sizes
```

Each round halves and rounds **up**. The published progression, straight from `bracket.py plan`:

```text
  round  alive  matches  bye  sub-agent calls  waves
  spawn    100        -    -              100     10
      1    100       50    -              250     25
      2     50       25    -              125     13
      3     25       12  yes               60      8
      4     13        6  yes               30      5
      5      7        3  yes               15      3
      6      4        2    -               10      3
      7      2        1    -                5      3
  total                                 595     70

  alive per round: 100 -> 50 -> 25 -> 13 -> 7 -> 4 -> 2 -> 1
```

### 9.2 Pairings

```python
def pair_round(state, rnd, alive):
    """Seeded pairing. An odd pool gives one bye, to an agent with the fewest byes so far.
    Where it can, it pairs agents with different reasoning modes, so every attack comes
    from a genuinely different angle. The first-listed side is random, which is the
    order the judge reads them in."""
```

Five rules:

1. The RNG is derived from the run: `random.Random("arena-pair:%s:%d" % (state["seed"], rnd))`,
   so the same seed rebuilds the same bracket.
2. The pool is sorted, then shuffled — the sort makes the shuffle input deterministic.
3. **One bye per odd round**, given to an agent with the fewest byes so far. A test pins that
   no agent ever receives two byes while anyone else is waiting for one.
4. Match formation is greedy and mode-aware: `j = next((k for k, b in enumerate(pool) if agents[b]["card"]["reasoning"]["id"] != mode), 0)` — prefer a different reasoning mode, and only
   accept a same-mode pairing when no alternative exists (index 0 is the fallback).
5. **The listed side is random** (`if rng.random() < 0.5: a, b = b, a`), which removes the
   first-read position bias from the judge.

Match ids are zero-padded to the round width: `r%d-m%0*d`, e.g. `r3-m07`.

### 9.3 Bye semantics

A bye is a **free pass, not a free ride**:

```python
if rd["bye"]:
    state["agents"][rd["bye"]]["byes"] += 1
    survivors.append(rd["bye"])
```

The bye agent skips all matching sub-agent calls for that round, carries its existing solution
forward, and gets its `byes` counter incremented so the next odd round gives the bye to someone
else. The 100-agent run has byes in rounds 3, 4 and 5.

### 9.4 Advancement

```python
def advance(state):
    """Close the current round: eliminate every loser, carry the winners' revised
    solutions forward, give the bye its free pass, and open the next round."""
```

It refuses to close a round with unrecorded matches:

```python
unrecorded = [m["id"] for m in rd["matches"] if not m["winner"]]
if unrecorded:
    raise ArenaError("round %d has %d unrecorded match(es): %s" % ...)
```

For each match it records `eliminated_in` and `eliminated_by` on the loser, and — importantly —
**re-points both competitors' solution path at their revised file**:

```python
for aid in (m["a"], m["b"]):
    rev = revised_out(state["dir"], rd["n"], m["id"], aid)
    if _has_output(rev):
        state["agents"][aid]["solution"] = rev
```

So a solution that survives a round carries its *revised* text into the next round, not its
original. A competitor is literally "its card plus its current solution file."

### 9.5 Crowning the champion, and arming the final check

```python
def open_round(state, alive):
    alive = sorted(alive)
    if len(alive) == 1:
        state["champion"] = alive[0]
        if state.get("has_baseline") and not state.get("final"):
            rng = random.Random("arena-final:%s" % state["seed"])
            x = "champion" if rng.random() < 0.5 else "baseline"
            state["final"] = {"X": x, "Y": "baseline" if x == "champion" else "champion", "result": None}
        return None
```

The champion/baseline labels are assigned by a **coin flip seeded into the run**. This is the
blinding mechanism for the final check: the judge sees `Solution X` and `Solution Y` and is told
"You are not told which of the two is which."

### 9.6 Agent identifiers

```python
def agent_ids(n):
    width = max(3, len(str(n)))
    return ["a%0*d" % (width, i) for i in range(1, n + 1)]
```

`a001 … a100` for 100 agents; `a0001 … a2160` for the maximum. `norm_agent()` accepts `a042`,
`042` or `A042` and resolves them to the canonical id.

---

## 10. The attack phase

Two sub-agent calls per match. Each competitor attacks the other's current solution **through its
own strategy card** — the card is the lens, not the subject.

### 10.1 The four flaw categories

```text
Find the real problems:
- WRONG: factual errors, logic errors, bugs, false claims.
- MISSING: a requirement the task states that it skips or only half meets. Quote the requirement.
- BREAKS: a concrete input, scenario or edge case where it fails. Give the exact counterexample.
- VAGUE: a place where the user could not act on it without guessing.
```

### 10.2 The rules

| Rule | Text |
| --- | --- |
| Specificity | "Every attack must be specific and checkable: point at the exact part, say what is wrong and why." |
| No padding | "No praise, no summary, and no style nitpicks unless they stop the user from using it." |
| No invented requirements | "Do not invent requirements the task does not state." |
| Attack the work, not the approach | "Do not attack the approach, only what it gets wrong." |
| Budget | "At most 7 attacks, strongest first. If you only find 2 real ones, write 2." |
| Severity labels | "Label each FATAL (wrong or unusable for the task), MAJOR (a real gap) or MINOR." |
| Write confinement | "Do not create, edit or delete any file except the one below." |
| Fair comparison | "Attack theirs on its merits against the task, not for being different from yours." |

The **"if you only find 2 real ones, write 2"** clause is important: it is an explicit
anti-fabrication instruction. Without it, an attacker told "find up to 7" will manufacture seven.

### 10.3 Output format

```text
ATTACK 1 [FATAL|MAJOR|MINOR] <one-line title>
Where: <quote or location>
Problem: <what is wrong, with the counterexample or the missed requirement>
```

Reply line: `ATTACKED <target> <n> (<f> fatal)`

---

## 11. The defend phase

Two sub-agent calls per match. Each competitor takes the attacks against it and rewrites.

### 11.1 The concede / rebut discipline

```text
1. Take every attack in turn and decide honestly. CONCEDE if it is right, and fix it.
   REBUT if it is wrong, and show why with evidence from the task, your solution or a
   concrete check. A rebuttal that only insists you are right counts as a concession.
   **Conceding a real flaw and fixing it scores better than defending it.**
```

This last sentence is the economic incentive that makes the loop improve answers rather than
entrench them. The judge's robustness criterion rewards fixing, and the rubric's anchor text
reinforces it ("A defense that says 'fixed' is not proof. Look.").

### 11.2 The rewrite rules

```text
2. Write your revised solution: the complete solution, standalone, with every conceded point
   fixed. The judge reads only this file, so never write "see the previous version".
3. Fix what was attacked and anything the attacks made you notice. Do not start again from
   scratch and do not copy your opponent.
4. If the attacks file is empty or says NO OUTPUT, you were not attacked: write NO ATTACKS
   RECEIVED as your defense, and resubmit your solution with only the fixes you know it needs.
```

Rule 2's "never write 'see the previous version'" is what keeps a competitor's solution
self-contained for the judge. Rule 3's "do not copy your opponent" prevents convergent collapse of
the two sides into one answer.

### 11.3 Output format

```text
ATTACK 1: CONCEDE|REBUT. <one to three lines>
(one entry per attack)
```

plus the revised solution file. Reply line: `DEFENDED <agent> conceded <n> rebutted <n>`

---

## 12. The judging rubric

The rubric is a separate, human-readable file (`rubric.md`) that the judge reads first. The
weights are mirrored in `bracket.py` as the `WEIGHTS` tuple, and a test cross-checks the two:

```python
def test_weights_match_the_rubric(self):
    with open(os.path.join(SKILL, "rubric.md"), encoding="utf-8") as fh:
        rows = re.findall(r"^\| (\w+) \| (\d+) \|", fh.read(), re.M)
    self.assertEqual([(k.lower(), int(w)) for k, w in rows], list(B.WEIGHTS))
    self.assertEqual(sum(w for _, w in B.WEIGHTS), 100)
```

This is a genuine single-source-of-truth enforcement: you cannot change the weights in
`bracket.py` without breaking a test that parses the Markdown, and vice versa.

### 12.1 The five criteria

Each criterion is scored 0–10. The weight is how much of the 100-point total it is worth.

| Criterion | Weight | The question |
| --- | --- | --- |
| **Correctness** | 30 | Is it right? No false claims, no logic errors, no bugs, nothing that would mislead the user. |
| **Completeness** | 25 | Does it meet every requirement the task actually states? Judged against the task text, not against what the judge would have liked. |
| **Robustness** | 20 | Does it hold up against the attacks raised in this match? Fixed, correctly rebutted, or still standing. |
| **Specificity** | 15 | Could the user act on it right now without guessing? Exact steps, values, names, code. |
| **Clarity** | 10 | Is it easy to read and use, at a length that fits the task? |

Weighted total = `(correctness×30 + completeness×25 + specificity×15 + robustness×20 + clarity×10) / 10`,
a number from 0 to 100.

**Note the shape of the weights:** correctness + completeness = 55 points, i.e. the majority of the
score is "is it right and does it answer the question that was asked." Robustness (20) measures how
the two answers survived *this match's* attacks. Specificity (15) punishes generic advice. Clarity
(10) is deliberately last and small.

### 12.2 The anchors (verbatim)

"Use the whole scale. A 7 is not a polite default."

**Correctness**
- 10: nothing wrong that you can find after checking it yourself.
- 7: minor slips that do not change the outcome for the user.
- 4: at least one real error the user would trip on.
- 0 to 2: wrong at the core, or it would cause harm if used.

**Completeness**
- 10: every stated requirement is met, fully.
- 7: every requirement is touched, one is thin.
- 4: a stated requirement is missing.
- 0 to 2: it answers a different question from the one asked.

**Specificity**
- 10: the user can act on every part of it immediately.
- 7: mostly concrete, with one or two places that need a guess.
- 4: a lot of "consider", "ensure" and "it depends" without the actual answer.
- 0 to 2: generic advice that would fit any task.

**Robustness**
- 10: every attack in this match is fixed in the revised solution or correctly rebutted, and
  nothing new is broken.
- 7: one MINOR attack still standing.
- 4: a MAJOR attack still standing, or a fix that broke something else.
- 0 to 2: a FATAL attack still standing.

> A missing attack file means the opponent raised nothing. Score robustness on the flaws you
> found yourself.

**Clarity**
- 10: the shape makes it obvious how to use it. No padding.
- 7: fine, with some padding or one confusing section.
- 4: the user has to dig for the answer.
- 0 to 2: hard to follow at all.

### 12.3 The fatal rule

> Mark a solution `fatal` only when you have verified a flaw that makes it wrong or unusable for
> the task: code that cannot work, a false central claim, a hard constraint broken, the wrong
> question answered. **A fatal solution cannot beat a solution that is not fatal, whatever the
> totals say.** If both are fatal, the totals decide.

### 12.4 Ties

> There are no draws. On an exact tie, the solution with fewer attacks still standing wins. If that
> is also level, the higher correctness score wins. If that is also level, pick the one you would
> hand to the user, and say why in the reason.

### 12.5 What the judge does not reward

- **Length.** Longer is not better. A tight answer that meets every requirement beats a long one
  that meets the same requirements.
- **Confidence.** A defense that says "fixed" is not proof. Check the revised solution.
- **The approach.** The judge never sees the strategy cards and does not guess at them. It scores
  the work, not the method that produced it.
- **Talking about quality.** "This robust, comprehensive solution" earns nothing. The solution has
  to be robust and comprehensive.
- **Agreement with the judge's own taste** where the task does not ask for it.

The rubric also carries its own copy in the run folder: `init` writes a frozen copy of `rubric.md`
into `.arena/<run>/rubric.md`, with the comment "frozen for this run, and inside the working
directory, so judges can read it without a permission prompt each." Editing the rubric mid-run
therefore cannot change the rules under a competitor.

### 12.6 What the judge is told to do

```text
1. Read both revised solutions in full before you score either one.
2. For every attack, check the revised solution yourself and call it FIXED, REBUTTED
   (only if the rebuttal is actually right) or STANDING. A defense that says "fixed"
   is not proof. Look.
3. Look for flaws the attackers missed, too.
4. Score each criterion from 0 to 10 using the rubric's anchors. Set fatal to true only
   for a flaw you have verified that makes the solution wrong or unusable for the task.
5. The winner is the higher weighted total. A fatal solution cannot beat one that is not fatal.
   On an exact tie, fewer standing attacks wins, then higher correctness.
6. Judge the work, not the writing about the work. Length is not quality. You do not know
   either competitor's strategy and should not guess it.
7. Do not create, edit or delete any file except the verdict. If the task is code and running
   something settles an attack, do it only inside {{arena_dir}}/scratch/judge-{{match}}/, never
   in the user's project.
```

Point 6's "you do not know either competitor's strategy" is a **blindness guarantee**, enforced by
construction (the judge template simply never receives the cards) and pinned by a test:

```python
if phase == "judge":
    self.assertNotIn("Reasoning mode", brief, "judges never see the cards")
```

---

## 13. The final check

Only runs when a baseline exists. Its purpose is anti-self-congratulation: the tournament has a
structural incentive to declare its own survivor the best answer, so the survivor is forced to beat
the answer the user already rejected, blind.

```text
You are the final check in an arena. {{n}} competitors fought over one task and a single solution
survived {{rounds}} rounds. Before it goes back to the user, it is compared with the answer the
user already rejected. **You are not told which of the two is which. Score what is in front of
you. Either one can win.**
```

It uses the same rubric and the same weights, but its robustness criterion is redefined for a
one-off comparison:

```text
2. Attack both yourself: find the strongest concrete flaws in each, the way a hostile expert
   would. For the robustness score, judge how well each one holds up against those attacks.
```

There is no "standing attacks" list here (neither side has a defender), so the output differs:

```json
{
  "scores": {
    "X": {"correctness": 0, "completeness": 0, "specificity": 0, "robustness": 0, "clarity": 0, "fatal": false},
    "Y": {"correctness": 0, "completeness": 0, "specificity": 0, "robustness": 0, "clarity": 0, "fatal": false}
  },
  "winner": "X or Y",
  "reason": "one sentence: the decisive difference",
  "fixed": ["each thing the winner gets right that the other gets wrong, in a few words"]
}
```

`bracket.py winner` then reports it unflinchingly, including the case where you lose:

```python
verdict = ("the winner beats it" if fin["better"] == "champion"
           else "the answer you rejected scored higher. Say so")
print("vs the answer you rejected: winner %s, rejected %s, %s. %s"
      % (_fmt(fin["champion_total"]), _fmt(fin["baseline_total"]), verdict, fin["reason"]))
```

And `SKILL.md` Step 5 requires the orchestrator to relay it honestly: "If the old answer scored
higher, say so plainly and show both."

### 13.1 The spawn-brief warning about the baseline

Competitors are told a baseline exists and why:

```text
The user already got an answer to this task and was NOT satisfied with it. It is at
<run>/baseline.md. Read it first and work out exactly why it fell short. Then beat it.
Do not just polish it: the winner of this arena is compared with that answer at the end.
```

That is a *baseline-aware* spawn without leaking the baseline's content into the task file. It also
means competitors see the rejected answer — an intentional design choice that gives them something
concrete to beat.

---

## 14. Verdict arithmetic and the fatal rule

`bracket.py` computes the winner from the judge's JSON. It never trusts the judge's stated pick
blindly.

```python
def decide(verdict, a, b):
    """Apply the rubric's arithmetic to a judge's verdict.

    The winner is the higher weighted total. A fatal solution cannot beat a
    non-fatal one. On an exact tie: fewer standing attacks, then higher
    correctness, then the judge's own pick. If the judge's pick disagrees with its
    own scores, the scores win and the note says so.
    Returns (winner, {a: total, b: total}, note).
    """
```

Decision order, exactly:

1. **Parse.** `extract_json` tolerates prose and code fences: it tries `json.loads` on the whole
   text, then on the substring from the first `{` to the last `}`. (Test:
   ```python
   self.assertEqual(B.extract_json('```json\n{"winner": "a001"}\n```'), {"winner": "a001"})
   self.assertIsNone(B.extract_json("no json here"))
   ```)
2. **Identify the pick** by regex word-boundary match on the agent id, tolerating case.
3. **Compute totals.** `weighted_total` rejects booleans for a score (so `true` is not coerced to
   1), clamps each criterion to 0–10, sums the weighted products and divides by 10, rounding to 2
   decimals. A missing or non-numeric criterion returns `None` for the whole total.
4. **If either total is unusable**, fall back to the judge's pick if there is one, else raise
   `ArenaError("verdict has neither complete scores nor a clear winner")`.
5. **Fatal asymmetry** → the non-fatal solution wins, regardless of totals.
6. **Higher total** wins.
7. **Exact tie** → fewer standing attacks, then higher correctness, then the judge's own pick,
   else raise.
8. **Disagreement between pick and scores** → the scores win, and the note records it:
   `"judge picked %s but its own scores favour %s, so the scores win"`.

The tests pin each branch:

```python
def test_scores_beat_the_judges_pick(self):
    v = {"scores": {"a001": self.scores(5,5,5,5,5), "a002": self.scores(8,8,8,8,8)}, "winner": "a001"}
    w, totals, note = B.decide(v, "a001", "a002")
    self.assertEqual(w, "a002")
    self.assertIn("scores win", note)

def test_fatal_rule(self):
    v = {"scores": {"a001": self.scores(9,9,9,9,9, fatal=True), "a002": self.scores(4,4,4,4,4)}}
    self.assertEqual(B.decide(v, "a001", "a002")[0], "a002")

def test_tie_goes_to_fewer_standing_attacks(self): ...
def test_incomplete_scores_fall_back_to_the_pick(self): ...
```

### 14.1 Unreadable verdicts

`collect()` treats an unparseable verdict as a **missing job**, not as a guess:

```python
except ArenaError as e:
    os.replace(path, path + ".unreadable")
    results.append((m["id"], "unreadable", str(e)))
    continue
```

The verdict is renamed to `*.unreadable`, so `_has_output` no longer sees it, so `prompts` lists
that judge job as missing again and it gets re-run. The test:

```python
def test_unreadable_verdict_is_set_aside_and_rerun(self):
    ...
    self.assertFalse(os.path.exists(path))
    self.assertTrue(os.path.exists(path + ".unreadable"))
```

### 14.2 The never-decide-a-match-yourself rule

`SKILL.md` is explicit: "A judge that fails twice gets a third, fresh run: **never decide a match
yourself.**" The `record` command exists and is exposed on the CLI, but the orchestrator is told
"`record` is only for fixing bookkeeping when the user asks you to." The design refuses to let the
orchestrator's own opinion leak into the bracket.

---

## 15. `bracket.py` — the complete CLI

Standard-library Python 3.8+. It is the reason the orchestrator never loses track. Its module
docstring is itself the API summary:

```
python3 bracket.py plan --agents 100             # rounds, sub-agent calls and waves. Writes nothing.
python3 bracket.py init --agents 100 --seed 7 --task-file task.md [--baseline-file old.md]
python3 bracket.py init --quick --task "..."     # 16 agents
python3 bracket.py next                          # what to do now, and the exact command for it
python3 bracket.py prompts <phase>               # write the sub-agent briefs, list the jobs left, in waves
python3 bracket.py check <phase>                 # which outputs are still missing
python3 bracket.py pairings                      # this round's matches, including the bye
python3 bracket.py collect                       # read every judge verdict and record the winners
python3 bracket.py record <match_id> <winner_id> [--reason "..."]
python3 bracket.py advance                       # close the round, eliminate the losers, pair the survivors
python3 bracket.py status                        # alive and eliminated, per round
python3 bracket.py winner                        # the survivor, what it beat, the attacks it survived
python3 bracket.py card <agent_id>               # one competitor's strategy card
```

Every command after `init` accepts `--dir`; without it, the run named in `.arena/LATEST` is used.

### 15.1 `plan`

`plan --agents N [--quick] [--wave W] [--json]`

Prints the rounds, sub-agent calls and waves table, plus the alive-per-round progression. Writes
nothing — it is safe to run as many times as you like to size a run before committing tokens.

### 15.2 `init`

```
init --agents N | --quick
      [--seed S]
      --task-file PATH | --task TEXT
      [--baseline-file PATH]
      [--wave W]
      [--dir PATH]
```

What it does, in order:

1. Loads and validates `strategies.json` (unique ids, every entry has `name` and `how`).
2. Rejects `n` outside `1 … combo_count(data)`.
3. Reads and `.strip()`s the task; refuses an empty task.
4. Reads the baseline if given; refuses an empty baseline file.
5. Picks a seed: the given one, or `random.SystemRandom().randrange(1, 1000000)`.
6. Creates the run directory, default `.arena/run-<YYYYMMDD-HHMMSS>-s<seed>`.
7. Refuses to initialise into a directory that already contains `arena.json`.
8. Writes `task.md` (with one trailing newline), `baseline.md` if any, and a **frozen copy of
   `rubric.md`**.
9. Builds the state: deals the cards, opens round 1, saves `arena.json`.
10. Writes `.arena/LATEST` pointing at the run directory (only when `--dir` was not given
    explicitly).
11. Prints the size, the seed, the card count and the totals, and the next command.

### 15.3 `next`

The state-machine entry point. It computes `next_action(state)` and prints the current phase and
the exact command to run. It is the only command the orchestrator needs to drive the whole
tournament. Output shapes:

```text
round 4 of 7, 13 alive
NEXT: attack. 12 of 12 job(s) to run.
  1. python3 "...bracket.py" prompts attack
  2. launch the jobs it lists, one wave of 10 at a time
  3. python3 "...bracket.py" next
```

or `NEXT: collect. Every verdict for this step is on disk.` / `NEXT: advance. Every match in round 4
has a winner.` / `DONE. python3 "...bracket.py" winner`.

### 15.4 `prompts <phase>`

```text
round 1, attack: 100 job(s), 100 still to run, 10 wave(s) of up to 10
wave 1
  r1-m01.a001.attack       C:\...\.arena\run-.../prompts/r1/r1-m01.a001.attack.md
  ...
```

It writes one brief file per job, creates the `scratch/<agent>/` directories on the spawn phase,
creates the blind `final/X.md` and `final/Y.md` copies on the final phase, and prints the launch
convention:

```text
Each job is one Agent call: subagent_type general-purpose, description "arena <job id>",
prompt "Read <prompt path> and follow it exactly. It is your whole brief."
A wave is one message with all of its Agent calls. Let it finish before the next wave.
```

### 15.5 `check <phase>`

Exit code 1 if any job's output is still missing, 0 otherwise. Lists exactly which output files
are missing — the tool for "did wave 3 actually land?"

### 15.6 `pairings`

```text
round 3: 13 alive, 6 match(es), bye: a042
  r3-m01  a003 vs a071  (Contrarian vs Constraint first)  open
  ...
  bye     a042 goes through without a match
```

`--json` dumps the whole round record.

### 15.7 `collect`

Reads every verdict file for the open round (or the final check), applies `decide()`, records the
winners with their totals, reasons and survived-attack lists, and saves state. Returns exit 1 if
any verdict is missing or unreadable.

### 15.8 `record`

Manual override: `record <match_id> <winner_id> [--reason "..."] [--survived "..."] (repeatable)`.
It re-validates that the winner is actually in the match, and reports if it changed a previous
result (`(was a042)`).

### 15.9 `advance`

Closes the round, eliminates the losers, carries the revised solutions forward, gives the bye its
free pass, opens the next round, and reports:

```text
round 3 closed: 7 through, 6 eliminated
round 4 open: 3 match(es), bye a042. python3 "...bracket.py" next
```

or, at the end:

```text
round 7 closed: 1 through, 0 eliminated
CHAMPION: a017. python3 "...bracket.py" next
```

### 15.10 `status`

```text
arena C:\...\.arena\run-20260927-132004-s7
seed 7, 100 agents, waves of 10, 2160 distinct cards available
  round  alive  matches  bye     eliminated  state
      1    100       50  -              50  closed
      2     50       25  -              25  closed
      3     25       12  a042            18  closed
      4     13        6  a017             6  open, 4 of 6 recorded
now: 7 alive, 93 eliminated
```

`--json` emits the same as machine-readable JSON.

### 15.11 `winner`

```text
CHAMPION  a017
card      Expert panel + Options matrix + Concrete specifics
solution  C:\...\.arena\run-.../r5/r5-m02.a017.solution.md
rounds    7 played, 100 agents in, 1 left, 6 match(es) won
  round 1  beat a042, 81.2 to 63.1. clause 4 sidesteps the rate limit entirely
           survived: "assumed 60 req/s is the cap"
  round 2  bye
  ...
vs the answer you rejected: winner 78.4, rejected 81.0, the answer you rejected scored higher. Say so. ...
```

### 15.12 `card`

```text
a042  eliminated in round 3 by a017
  reasoning First principles. Strip the task down to the facts and requirements...
  workflow  Options matrix. List the realistic options, score them in a small matrix...
  strategy  Concrete specifics. Exact numbers, names, commands, file paths and steps...
  solution  C:\...\.arena\run-.../r2/r2-m05.a042.solution.md
```

### 15.13 Error handling and exit codes

All errors funnel through one exception type and one printer:

```python
class ArenaError(Exception):
    pass

def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ArenaError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
```

Every failure is exit code 2 with a message that names the fix. `check` returns 1 when work is
missing, which makes it usable in scripts. `init` refuses to overwrite, refuses `--agents 0`,
refuses `--agents 2161`, refuses `--quick --agents 8` together, and refuses an empty task.

### 15.14 Atomic state writes

```python
def save_state(state):
    path = os.path.join(state["dir"], STATE_FILE)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=1)
        fh.write("\n")
    os.replace(tmp, path)
```

Write-to-temp-then-`os.replace` is atomic on both POSIX and Windows, so a crash mid-write cannot
leave a truncated `arena.json` and orphan a 595-call run.

---

## 16. On-disk layout of a run

Everything a run produces lives under `.arena/` in the directory you ran `init` from.

```text
.arena/
├── LATEST                              # absolute path of the most recent run directory
└── run-20260927-132004-s7/
    ├── arena.json                      # THE state: cards, alive flags, rounds, matches, verdicts
    ├── task.md                         # the task, word for word, as every competitor gets it
    ├── baseline.md                     # the rejected answer (optional)
    ├── rubric.md                       # frozen copy of the judging rubric for this run
    ├── r0/                             # spawn phase: one solution per competitor
    │   ├── a001.md
    │   ├── a002.md
    │   └── ... a100.md
    ├── r1/                             # round 1
    │   ├── r1-m01.a001.attack.md       # a001's attacks on a002
    │   ├── r1-m01.a002.attack.md       # a002's attacks on a001
    │   ├── r1-m01.a001.defense.md      # a001's point-by-point defense
    │   ├── r1-m01.a001.solution.md     # a001's REVISED solution
    │   ├── r1-m01.a002.defense.md
    │   ├── r1-m01.a002.solution.md
    │   ├── r1-m01.verdict.json         # the judge's verdict for r1-m01
    │   ├── r1-m01.verdict.json.unreadable   # a verdict that failed to parse
    │   └── ...
    ├── r2/ ... r7/                     # same shape per round
    ├── final/
    │   ├── X.md                        # blind copy of either the champion or the baseline
    │   ├── Y.md                        # blind copy of the other one
    │   └── final.verdict.json
    ├── scratch/
    │   ├── a001/ ... a100/             # per-competitor scratch space (created at spawn)
    │   └── judge-r1-m01/ ...           # per-match judge scratch space
    └── prompts/
        ├── r0/a001.spawn.md            # the exact brief the a001 spawn sub-agent received
        ├── r1/r1-m01.a001.attack.md
        ├── r1/r1-m01.a001.defend.md
        ├── r1/r1-m01.judge.md
        └── final/final.judge.md
```

Path helpers, for reference:

```python
def spawn_out(d, aid):    return os.path.join(d, "r0", aid + ".md")
def attack_out(d, r, m, a): return os.path.join(d, "r%d" % r, "%s.%s.attack.md" % (m, a))
def defense_out(d, r, m, a): return os.path.join(d, "r%d" % r, "%s.%s.defense.md" % (m, a))
def revised_out(d, r, m, a): return os.path.join(d, "r%d" % r, "%s.%s.solution.md" % (m, a))
def verdict_out(d, r, m):  return os.path.join(d, "r%d" % r, "%s.verdict.json" % m)
def prompt_path(d, r, job): return os.path.join(d, "prompts", sub, job + ".md")
```

Note that `r1/` holds the *round* artifacts and `prompts/r1/` the *briefs* — the `prompts/` subtree
is the audit trail of exactly what each sub-agent was told.

The whole run is gitignored (`.arena/` appears in the repository's own `.gitignore`), and
`SKILL.md` tells the user where it is: "Where the full record lives: the run folder."

---

## 17. Cost model

The skill is free. The tokens are yours, and 100 agents is a lot of them.

### 17.1 Published cost table (waves of 10)

| Agents | Rounds | Sub-agent calls | Waves |
| --- | --- | --- | --- |
| **100** (default) | 7 | **595** | 70 |
| 64 | 6 | 379 | 49 |
| 32 | 5 | 187 | 28 |
| **16** (`--quick`) | 4 | **91** | 16 |
| 8 | 3 | 43 | 10 |

Add **one call** when there is a rejected answer to beat (the final check).

### 17.2 Why the call count scales superlinearly with agents

Every round makes 5 calls per match, and there are `n/2` matches per round — roughly `2.5n` calls
per round over `log₂(n)` rounds. Halving the agent count removes not just a share of the work but a
whole round of attack/defend/judge.

### 17.3 The other cost multiplier: task size

"Every call reads the task and one or two solutions, so the bill grows with the size of the task."
A competitor's brief contains the full task plus its card; an attacker's brief contains the task
plus its own card plus its opponent's full solution; a defender's brief contains the task, its
card, its own solution and all the attacks against it. **The defend phase is the most expensive
brief per call**, and it happens twice per match.

### 17.4 Wall-clock reality

The agents do not all run at once. A 100-agent run is 70 waves, so it takes a while — one
third-party account measured a single prompt running **16 minutes straight**. Do not expect a
100-agent tournament to return in seconds.

### 17.5 The sizing guidance from the author

> "Use `--quick` or `--agents 16` for everyday things and save the full 100 for the answer that
> matters. The skill prints these numbers before it starts, and
> `python3 skills/arena/bracket.py plan --agents N` prints them any time."

---

## 18. Independently measured benchmark

The most valuable third-party contribution to this project is a measurement published by GitHub user
**`mersad31`** in issue #1 ("It will exhaust all your token in first prompt"), 2026-09-30. It is
reproduced here because it is the only public, independent, quantitative evaluation of the skill.

### 18.1 Setup

- **Task:** a self-contained Python exact-arithmetic expression evaluator with explicit safety
  requirements (deep nesting, huge exponents, no `eval`).
- **Arena:** `--agents 8`, 3 rounds, **43** sub-agent calls, fixed seed.
- **Baseline:** one plain sub-agent given the identical task file.
- **Independent scoring:** 66 hidden tests, a hostile-input stress script, and one blind judge using
  the skill's own `final` template.

### 18.2 Measured token cost (per-agent usage reported by Claude Code)

| Run | Calls | Tokens |
| --- | --- | --- |
| Single agent | 1 | **~49k** |
| Arena, 8 agents | 43 | **~2.83M** (~66k per call) |

**By phase:** spawn 0.48M, attack 0.87M, defend 1.00M, judge 0.49M.
**Attack + defend are ~65% of the bill.**

**Linear extrapolation to the shipped sizes:**

| Configuration | Calls | Estimated tokens |
| --- | --- | --- |
| `--quick` (16 agents) | 91 | **~6M** |
| Default (100 agents) | 595 | **~39M** |

That last number is the explanation for issue #1: a default run is a ~39 million token commitment.
39M / 49k ≈ **800×** a single careful answer.

### 18.3 Measured quality

| Finding | Detail |
| --- | --- |
| Correctness | **Both** the baseline and the champion passed 66/66 hidden tests. |
| Robustness | The arena's real win. A short hostile input `(10^10000)^10000` **hung the baseline for more than 20 seconds**; the champion rejected it instantly. |
| Preference | The blind judge scored the champion **92** against the baseline's **81**. |
| Self-correction | The attacks were concrete and checkable, and they **fixed the one buggy initial solution — which then went on to win.** |
| Diversity | **Lower than the cards suggest: 6 of 8 solutions converged on the same algorithm.** |
| Granularity | Only the single winner is returned, not several alternatives. |

### 18.4 Measured verdict, and the efficiency playbook

> "For tasks a single agent already answers well, it cost ~58x for the same functional result."
> (2.83M / 49k ≈ 57.8)

The commenter's recommendations, which align closely with the author's own guidance:

1. Run `bracket.py plan --agents N` first and pick N deliberately. **4 agents (19 calls) or 8 agents
   (43 calls) is enough for most decisions.** Keep 16+ for rare, high-stakes ones.
2. Keep the task file **short and self-contained**. Every call re-reads it, so cost scales with task
   length. A tight 1–2 page brief beats pasting whole documents.
3. Use it **where it pays off**: security-sensitive code, edge-case-heavy logic, or decisions that
   are expensive to get wrong.
4. If you already have a candidate answer, pass it as `--baseline-file`. The blind final check then
   tells you whether the tournament actually beat it.
5. **Read the losing spawn solutions in `.arena/` too.** If you want options rather than one answer,
   they are the cheapest part of the run.

### 18.5 What the measurement means for the skill's honest claims

The benchmark validates the skill's own framing precisely:

- It confirms "what you get is the strongest answer this tournament found, not a proof that it is
  right" — the champion won on *robustness*, not on test-passing, because the baseline passed the
  same tests.
- It confirms "the dealer's guarantees" produce real variety of *card*, but that the model's own
  inductive bias partly overrides it (6 of 8 converging).
- It confirms the cost framing: the default size is genuinely a first-order token commitment.

---

## 19. The sub-agent prompt templates

These are reproduced verbatim from `SKILL.md`, because they are the exact text every sub-agent
receives. `bracket.py` extracts them with the `<!-- template:X -->` markers and substitutes the
`{{placeholders}}` in a single pass.

### 19.1 Competitor (spawn phase)

```text
You are competitor {{agent}} in an arena of {{n}}. All {{n}} competitors got the exact same task, word for word. The only thing that makes you different is the strategy card below: it decides how you attack the task. Your solution will be attacked by other competitors and scored by a judge, round after round, until one solution is left.

=== THE TASK (identical for every competitor) ===
{{task}}
=== END OF THE TASK ===

{{baseline_note}}

=== YOUR STRATEGY CARD ===
Reasoning mode: {{reasoning_name}}. {{reasoning_how}}
Workflow: {{workflow_name}}. {{workflow_how}}
Strategy: {{strategy_name}}. {{strategy_how}}
=== END OF THE CARD ===

How to work:
1. Use the card for real. Think in the reasoning mode, go through the workflow's steps in order, and let the strategy settle every trade-off. A generic answer with the card's name on top will lose.
2. Meet every requirement the task states. The judge scores you against the task, not against your card.
3. You cannot ask the user anything. Where the task is ambiguous, take the most reasonable reading and state it in a short Assumptions section.
4. Expect attacks: concrete flaws, counterexamples, missed requirements. Close those holes before you submit.
5. Do not create, edit or delete anything outside {{arena_dir}}. Read whatever the task points to. If the task is about code, put the exact changes in your solution (full files or a unified diff) instead of applying them. If your workflow needs scratch space, use {{arena_dir}}/scratch/{{agent}}/.

Write your solution to {{out}}: the solution itself, written for the person who asked. Leave out your drafts and your working. Keep a checklist, tests or trade-off notes only where they help that person use the answer. Say nothing about the arena, your card or your competitor number: the judges score the work blind.

When the file is written, reply with this one line and nothing else:
DONE {{agent}} <number of words in your solution>
```

Note the final instruction: competitors are told to **say nothing about the arena, their card or
their competitor number** in the solution. The judge therefore sees pure work product with no
metadata contamination.

### 19.2 Attacker (every round)

```text
You are competitor {{agent}} in round {{round}} of an arena, match {{match}}. Your opponent is {{target}}. Only one of you gets out of this match. Right now your job is to attack your opponent's solution.

=== THE TASK (identical for every competitor) ===
{{task}}
=== END OF THE TASK ===

Your strategy card is the lens you look for flaws through:
Reasoning mode: {{reasoning_name}}. {{reasoning_how}}
Workflow: {{workflow_name}}. {{workflow_how}}
Strategy: {{strategy_name}}. {{strategy_how}}

Read your opponent's solution: {{target_solution}}
You may read your own for comparison: {{own_solution}}. Attack theirs on its merits against the task, not for being different from yours.

Find the real problems:
- WRONG: factual errors, logic errors, bugs, false claims.
- MISSING: a requirement the task states that it skips or only half meets. Quote the requirement.
- BREAKS: a concrete input, scenario or edge case where it fails. Give the exact counterexample.
- VAGUE: a place where the user could not act on it without guessing.

Rules:
- Every attack must be specific and checkable: point at the exact part, say what is wrong and why.
- No praise, no summary, and no style nitpicks unless they stop the user from using it.
- Do not invent requirements the task does not state. Do not attack the approach, only what it gets wrong.
- At most 7 attacks, strongest first. If you only find 2 real ones, write 2.
- Label each FATAL (wrong or unusable for the task), MAJOR (a real gap) or MINOR.
- Do not create, edit or delete any file except the one below.

Write the attacks to {{out}} in this format:
ATTACK 1 [FATAL|MAJOR|MINOR] <one-line title>
Where: <quote or location>
Problem: <what is wrong, with the counterexample or the missed requirement>
(and the same for each attack after that)

When the file is written, reply with this one line and nothing else:
ATTACKED {{target}} <number of attacks> (<number that are FATAL> fatal)
```

### 19.3 Defender (every round)

```text
You are competitor {{agent}} in round {{round}} of an arena, match {{match}}. Your opponent {{attacker}} has attacked your solution. Now you defend it and revise it. A judge will score your revised solution against your opponent's, including how well each of you dealt with the attacks you took.

=== THE TASK (identical for every competitor) ===
{{task}}
=== END OF THE TASK ===

Your strategy card. Keep your approach: it is why you are still here.
Reasoning mode: {{reasoning_name}}. {{reasoning_how}}
Workflow: {{workflow_name}}. {{workflow_how}}
Strategy: {{strategy_name}}. {{strategy_how}}

Your current solution: {{own_solution}}
The attacks against it: {{attacks}}

Do this:
1. Take every attack in turn and decide honestly. CONCEDE if it is right, and fix it. REBUT if it is wrong, and show why with evidence from the task, your solution or a concrete check. A rebuttal that only insists you are right counts as a concession. Conceding a real flaw and fixing it scores better than defending it.
2. Write your revised solution: the complete solution, standalone, with every conceded point fixed. The judge reads only this file, so never write "see the previous version". Say nothing about the arena or your card.
3. Fix what was attacked and anything the attacks made you notice. Do not start again from scratch and do not copy your opponent.
4. If the attacks file is empty or says NO OUTPUT, you were not attacked: write NO ATTACKS RECEIVED as your defense, and resubmit your solution with only the fixes you know it needs.
5. Do not create, edit or delete anything outside {{arena_dir}}.

Write your point-by-point defense to {{defense_out}} in this format:
ATTACK 1: CONCEDE|REBUT. <one to three lines>
(one entry per attack)

Write your revised solution to {{solution_out}}.

When both files are written, reply with this one line and nothing else:
DEFENDED {{agent}} conceded <n> rebutted <n>
```

### 19.4 Judge (one per match)

```text
You are the judge of match {{match}}, round {{round}}, in an arena. Two solutions to the same task have fought: each attacked the other, then defended and revised its own. Score both against the rubric. The one with the higher score goes through and the other is eliminated.

=== THE TASK (identical for every competitor) ===
{{task}}
=== END OF THE TASK ===

Read the rubric first: {{rubric}}

Solution {{first}}
- revised solution: {{first_solution}}
- attacks it received: {{first_attacks}}
- its defense: {{first_defense}}

Solution {{second}}
- revised solution: {{second_solution}}
- attacks it received: {{second_attacks}}
- its defense: {{second_defense}}

How to judge: [see §12.6]

Write this JSON, and nothing else, to {{out}}:
{
  "match": "{{match}}",
  "scores": {
    "{{first}}": {"correctness": 0, "completeness": 0, "specificity": 0, "robustness": 0, "clarity": 0, "fatal": false},
    "{{second}}": {"correctness": 0, "completeness": 0, "specificity": 0, "robustness": 0, "clarity": 0, "fatal": false}
  },
  "winner": "{{first}} or {{second}}",
  "reason": "one sentence: the decisive difference",
  "survived": ["each attack the winner took and beat, in a few words"],
  "standing": {"{{first}}": ["attacks still standing"], "{{second}}": ["attacks still standing"]}
}

When the file is written, reply with this one line and nothing else:
WINNER <winner id> <winner total>-<loser total>
```

The judge receives **both sides' attacks, defenses and revised solutions** — a complete, symmetric
evidence package — and never receives either strategy card.

### 19.5 Final check (only when there is an answer to beat)

Reproduced in full in [§13](#13-the-final-check).

---

## 20. Rules for the orchestrator

These are the constraints the main Claude session operates under. They are the load-bearing rules
of the whole design.

| # | Rule | Why it exists |
| --- | --- | --- |
| 1 | "You run the tournament. You do not compete, attack or judge, and **you never pick a winner.**" | Removes the orchestrator's own opinion from the bracket. |
| 2 | "`collect` records what the judges decided. `record` is only for fixing bookkeeping when the user asks you to." | Same reason, operationally. |
| 3 | "Every sub-agent gets the task through its brief, which `prompts` builds from the one task file, **byte for byte the same for everyone.** Never paraphrase the task for one agent or add a hint to one agent's call." | Preserves the controlled experiment. A hint to one agent is a broken arena. |
| 4 | "**Do not read solutions, attacks or verdicts during the run.** There are hundreds of them. The state is on disk, and `next`, `status` and `pairings` are all you need." | Context preservation. Also prevents the orchestrator from forming favourites. |
| 5 | "If your context gets compacted mid-run, nothing is lost. Run `ARENA status`, then `ARENA next`, and carry on." | Crash/compaction recovery. |
| 6 | "Run every `ARENA` command from the directory you ran `init` in. That is where `.arena/LATEST` lives." | State resolution. |
| 7 | "Sub-agents only write inside `.arena/`. **If one wrote anywhere else, tell the user.**" | Containment breach reporting. |
| 8 | "If the user says stop, stop. `ARENA status` shows where it got to, and `ARENA next` resumes it later." | Abort/resume. |
| 9 | "After each `advance`, give the user one line... **Nothing more. Never paste pairings, attacks, verdicts or solutions into the chat.**" | Context preservation, and no spoilers that could bias the user. |
| 10 | "Do not change the user's settings yourself" (about accept-edits mode). | Consent. |
| 11 | "If the solution changes files in the user's project, do not apply it. Ask: apply it, or change it?" | Non-destructiveness. |
| 12 | "Never edit a brief for a single agent." | Experiment integrity. |

### 20.1 Step 2 is called out as decisive

`SKILL.md` literally labels the task-file step as the one that decides the result:

> "This is the step that decides the result. **Sub-agents cannot see this conversation.** Every
> competitor, attacker and judge knows only what is in the task file."

And it imposes two explicit prohibitions:

> - **Do not add requirements the user never gave.**
> - **Do not write your own view of the right answer into it: that pushes 100 agents the same way,
>   which is the opposite of the point.**

The required contents of `.arena/task.md`:

- The request, in the user's own words where you can.
- Every requirement and constraint the user stated **anywhere in the conversation**: audience,
  length, format, tone, stack, deadline, what must not change.
- The context a stranger would need: absolute paths of the files that matter, pasted data, what the
  product is, the conventions in the codebase.
- What "done" looks like, if the user said.
- If there is an answer to beat: what the user disliked about it, in their words.

The test that enforces the byte-for-byte property:

```python
if phase == "spawn":
    task_block = brief.split("=== THE TASK (identical for every competitor) ===\n")[1]
    task_block = task_block.split("\n=== END OF THE TASK ===")[0]
    self.assertEqual(task_block, self.TASK)
```

and

```python
self.assertEqual(blocks, {self.TASK}, "every competitor gets the exact same task text")
self.assertEqual(len(cards), 16, "every competitor gets a different card")
```

---

## 21. Safety model

What Arena guarantees about your machine:

| Guarantee | Mechanism |
| --- | --- |
| **Never edits your project** | Every sub-agent brief says "Do not create, edit or delete anything outside `{{arena_dir}}`." Code changes come back as diffs or full files inside the winning answer. |
| **All writes confined to `.arena/`** | The brief is explicit, and the orchestrator is required to report a breach: "If one wrote anywhere else, tell the user." |
| **Judge sandbox** | "If the task is code and running something settles an attack, do it only inside `{{arena_dir}}/scratch/judge-{{match}}/`, never in the user's project." |
| **Nothing leaves your machine** | No API key, no network call, no telemetry, no account. The skill file is four files. |
| **No standing permissions** | It cannot change your permission mode. It only *suggests* accept-edits and says "Do not change the user's settings yourself." |
| **Per-competitor scratch** | `scratch/<agent>/` directories are created for each competitor, so workflows that need workspace do not collide. |
| **Run isolation** | `init` refuses to reuse a directory that already has an `arena.json`. |
| **Rubric immutability per run** | A frozen rubric copy lands in the run folder, so no rule changes mid-tournament. |

### 21.1 A practical security note

The confinement is a **prompt-level instruction**, not an OS-level sandbox. Sub-agents run with
whatever permissions Claude Code grants them. The mitigation built into the skill is the
`accept-edits` suggestion combined with the breach-reporting rule. If you want a stronger boundary,
run Arena in a dedicated working directory (a third-party guide recommends exactly this: run it in
a folder you created only for Arena).

---

## 22. Honesty and limitations

The skill documents its own limits at length, which is unusual and is the reason it survives
critical reading. This is the complete "fine print" list, with commentary.

| Claim | Reality |
| --- | --- |
| **"100 versions of Claude"** | Means 100 sub-agents **of the model you are running**. Not 100 different models. What makes them different is the card. |
| **"A competitor is its card plus its solution file."** | Sub-agents remember nothing between calls. When `a017` attacks in round 3, that is a **fresh** sub-agent handed `a017`'s card and `a017`'s latest solution. 100 competitors, 595 calls. |
| **"The best answer"** | Means **the one that survived every match**. The judges are Claude too. What you get is the strongest answer this tournament found, **not a proof that it is right**. |
| **Parallelism** | 100 agents do not all run at the same moment. Claude Code caps concurrent tool calls at 10 by default (`CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY`), so the arena runs in waves. |
| **Permission prompts** | Expect them unless you allow edits — one approval per file. |
| **Sub-agents cannot see your chat** | The skill writes a standalone task file, and that file is all 100 agents ever know. **If a requirement never made it into the file, all 100 miss it.** |
| **It never edits your project** | Code changes come back as a diff or full files inside the winning answer. Applying them is your call, and the skill asks. |
| **Seed determinism** | "The same seed gives the same cards and the same bracket. **Not the same answers.** The model is not deterministic." |
| **Garbage in, garbage out** | "**It does not fix a bad task.** Vague task in, 100 flavours of vague out." |

### 22.1 Additional limitations identified in the wild

- **Diversity is partially illusory.** The independent measurement found 6 of 8 solutions converged
  on the same algorithm despite 8 distinct strategy cards. The cards change the *process*; they do
  not overcome the model's inductive bias.
- **One answer, not a portfolio.** Only the single winner is returned. If you wanted to see the
  finalists side by side, you have to open `.arena/` yourself.
- **It is slow.** A default run is 70 waves. One user measured 16 minutes for a single prompt.
- **It can exhaust a Claude budget in one prompt.** This is issue #1, and the default run extrapolates
  to roughly 39M tokens.
- **It does not run in plain claude.ai.** It needs the Agent tool, i.e. Claude Code (the desktop
  app's Code tab, or the CLI).

---

## 23. Failure handling and recovery

`SKILL.md` defines a precise, degenerate-failure policy so a 595-call run cannot silently corrupt
itself:

| Failure | Response |
| --- | --- |
| A sub-agent produces no output | Re-run that job once. |
| It fails **twice** | Write the single line `NO OUTPUT` into each of its output files (`ARENA check <phase>` lists them) and move on. |
| **Missing attack** | "A missing attack counts as no attacks." The defender is told `NO ATTACKS RECEIVED` and resubmits. |
| **Missing solution** | "A missing solution loses its match." |
| **Judge fails twice** | "Gets a third, fresh run: **never decide a match yourself.**" |
| Unreadable verdict JSON | Renamed to `.unreadable`, so the job reappears as missing and the judge re-runs. |
| Incomplete scores, no clear winner | `ArenaError`, exit 2, job re-runs. |
| Round with unrecorded matches | `advance` refuses: "round %d has %d unrecorded match(es): %s". |
| Context compaction mid-run | `ARENA status` then `ARENA next`, and carry on. State is on disk. |
| Duplicate `init` into a used directory | Hard error: "already has an arena in it. Pick another --dir". |

The `NO OUTPUT` sentinel is the elegant part: `_has_output` only checks that the file exists and is
non-empty, so writing `NO OUTPUT` *promotes a failed job to a completed job* with a known degenerate
semantic, letting the tournament finish rather than deadlocking on a permanently missing file.

---

## 24. The test suite

`tests/test_bracket.py` — standard library `unittest`, no fixtures required.

```bash
python3 -m unittest discover -s tests -v
```

### 24.1 Test classes

| Class | What it proves |
| --- | --- |
| `TempDir` | Shared tempdir fixture. |
| `SimulatedTournament` | Drives full tournaments **through the real CLI** (`init`, `pairings`, `record`, `advance`, `status`, `winner`) with random winners, and asserts each ends with exactly one survivor. |
| `Dealer` | The card-dealing guarantees. |
| `Plan` | The published cost arithmetic. |
| `Verdicts` | The rubric arithmetic and the decision rules. |
| `FullPipelineWithFakeAgents` | Every phase, end to end, with stand-in sub-agents writing the files real ones would. |
| `Guards` | Refusals and invariants. |

### 24.2 The headline test

```python
def test_100_agents_reach_exactly_one_survivor(self):
    d, state, sizes = self.run_tournament(100, 7)
    self.assertEqual(sizes, [100, 50, 25, 13, 7, 4, 2, 1])
    self.assertEqual(len(state["rounds"]), 7)
    alive = [a for a, v in state["agents"].items() if v["alive"]]
    self.assertEqual(alive, [state["champion"]])
    self.assertEqual(len(dead), 99)
    self.assertTrue(all(v["eliminated_in"] and v["eliminated_by"] for v in dead))
    self.assertEqual(sum(len(rd["matches"]) for rd in state["rounds"]), 99)
    self.assertEqual([rd["bye"] is not None for rd in state["rounds"]],
                     [False, False, True, True, True, False, False])
    self.assertTrue(all(v["byes"] <= 1 for v in state["agents"].values()),
                    "no agent gets two byes")
```

99 matches total for 100 agents (one per eliminated competitor) — a nice invariant.

### 24.3 Other notable assertions

- **Sizes 16, 7, 1, and many others (2, 3, 5, 9, 31, 64, 101, 144, 250) always end with one
  survivor.**
- `test_1_agent_is_already_the_winner` — the degenerate case returns immediately with
  `0 played, 1 agents in, 1 left`.
- **Every brief placeholder is filled:**
  `self.assertNotRegex(brief.replace("{{name}}", ""), r"\{\{\w+\}\}", "unfilled placeholder")`
- **Judges never see the cards:** `self.assertNotIn("Reasoning mode", brief)`
- **The champion carries its revised solution:** `self.assertIn(".solution.md", champ["solution"])`
- **A task containing literal braces survives the single-pass fill.**
- **No em dashes or en dashes anywhere in the repository**, enforced by
  `test_no_em_dashes_anywhere` walking every file:

```python
def test_no_em_dashes_anywhere(self):
    for root, dirs, files os.walk(REPO):
        for name in files:
            ...
            self.assertNotIn(EM_DASH, text, "em dash in %s" % os.path.relpath(path, REPO))
            self.assertNotIn(EN_DASH, text, "en dash in %s" % os.path.relpath(path, REPO))
```

That last one is a deliberate house-style invariant: the author's anti-"AI slop" voice policy is
enforced by CI, not by convention. (It is also why this dossier's own quoted text reads as it
does.)

---

## 25. Best practices

A synthesis of the author's guidance and the community's measured experience.

### 25.1 Sizing

| Situation | Command | Cost |
| --- | --- | --- |
| Quick sanity check of how a task behaves | `/arena --quick` | 16 agents, 91 calls |
| Most real decisions | `--agents 8` or `--agents 4` | 43 or 19 calls |
| A moderately hard problem | `--agents 32` | 187 calls |
| The one answer that really matters, where being wrong is expensive | `/arena` (100) | 595 calls |

Rule of thumb from the benchmark: **4–8 agents is enough for most decisions; keep 16+ for rare,
high-stakes ones.** Always run `bracket.py plan --agents N` first — it writes nothing and prints
the exact numbers.

### 25.2 Writing a task file

This is the highest-leverage thing you control.

- **Be self-contained.** Sub-agents never see your chat. Include audience, length, format, tone,
  stack, deadline and what must not change.
- **Use the user's own words** for the request where you can.
- **Give context a stranger needs**: absolute paths of relevant files, pasted data, product
  description, codebase conventions.
- **Say what "done" looks like**, if you know.
- **Keep it tight (1–2 pages).** Every one of the 595 calls re-reads it. Long task files scale
  the bill linearly.
- **Start from a checkable task** while learning. A headline with a clear brief, a bug you can
  reproduce, a fix you can test.
- **Do not let the orchestrator's own opinion into the file.** If your view of the right answer is
  in there, all 100 agents converge on it and the arena is theatre.

### 25.3 Setup

- Run it in **accept-edits mode** (Shift+Tab) for the duration of the run.
- Run it in a **dedicated working directory** you do not mind seeing a `.arena/` folder in.
- Prefer the manual skill copy if you want the bare `/arena` command rather than
  `/arena-skill:arena`.
- If you have an answer you already dislike, **always** pass it as the baseline. The final check
  then tells you honestly whether the tournament beat it.

### 25.4 Reading the result

1. Read the champion's solution in full — that is the deliverable.
2. Read the **attacks it survived**. That is where its weak spots were, and it is free information
   about what to watch for.
3. Note the **card** it won with. It tells you which posture suited this task.
4. Check the **final check** score honestly, including when you lose.
5. Browse the **losing spawn solutions** in `r0/` if you want options rather than one answer —
   they are the cheapest part of the run.

### 25.5 Where Arena pays for itself

| Good use | Poor use |
| --- | --- |
| Security-sensitive code | Simple, well-understood questions a single pass answers well |
| Edge-case-heavy logic | Everyday writing and small fixes (use `--quick`) |
| Decisions that are expensive to get wrong | Tasks you have not specified clearly yet |
| Outputs you can independently check | Anything where you cannot verify the result yourself |
| When you have already rejected an answer | When you are near your usage limit |

---

## 26. Community reception and coverage

### 26.1 The viral loop

The skill spread primarily through short-form social video and LinkedIn, mostly via the
`@theaiimpact` account and a set of "comment ARENA and I'll DM you the link" posts. Documented
coverage includes:

| Platform | Source |
| --- | --- |
| Threads | `@theaiimpact` — "The Arena Skill for Claude Code gives the same task to 100 sub-agents using different strategies. They critique, compete and get eliminated..." |
| Instagram | Multiple reels by `@theaiimpact` ("ARENA SKILL is crazy good!", "Comment ARENA and i'll send you the setup...") |
| TikTok | `@theaiimpact` — "Claude's Arena Skill Makes 100 AI Agents Compete" |
| LinkedIn | Rananjay Raj — "I made 100 Claudes compete over the same task..." |
| Claude Code Club | A 6-minute written guide updated October 2026, with install steps, size table, best practices and two ready prompts |
| Skills Directory | Listing with security grade **A / 100**, scanned 2026-09-28 |
| dijitalburak.com | A Turkish/English/Chinese walkthrough (D-039) with a full FAQ and install instructions for the desktop app's Code tab |
| SkillsLLM | Side-by-side comparison entry against `mattpocock/skills` |

### 26.2 The critical voice

The most substantive community contribution is not praise but measurement. Issue #1 ("It will
exhaust all your token in first prompt") was opened by a user who ran one prompt, waited 16
minutes, exhausted their token budget and got no output. The response from another user
(`mersad31`) was a full benchmark (§18) plus PR #2 proposing that the skill always print a token
estimate and ask before large runs. PR #2 is **open and unmerged** as of 2026-10-10.

The PR's proposal, in its own words:

> "After: the skill always states a rough token cost next to the size. Runs over 100 sub-agent
> calls, including the default of 100 agents, ask once first and offer `--quick` and `--agents 8`
> as cheaper options. `--quick` and small runs still start without a question.
> How: only the 'Step 1' section of `skills/arena/SKILL.md` changes. The estimate is sub-agent calls
> times about 60k tokens. It comes from a measured 8-agent run: 43 calls and about 2.8M tokens,
> against about 50k for one agent on the same task."

Note the practical implication for a user today: **`/arena` with no flags starts a ~39M token run
with only a one-line preface.** Send `--quick` until you trust the task file.

### 26.3 What independent commentators emphasise

- **It is the same model 100 times.** Every explainer had to correct the "100 different AIs"
  misreading first.
- **The dollar cost is the real story.** "The skill is free. The tokens are yours."
- **The task file is the actual skill.** "A vague task gives you 100 flavors of vague."
- **The winner is not proven correct.** The judges are Claude too, and Arena will tell you when
  your rejected answer scored higher.
- **Start with output you can check.** A headline with a clear brief is the recommended first run.

---

## 27. Forks and derivatives

The repository has **57 forks**, with the earliest a few days after launch and a heavy tail through
October 2026. The vast majority are plain clones (install-and-forget). One fork is a genuine
engineering extension and is worth documenting in full.

### 27.1 `IcaruzSoftware/round-table-skill` — "The Round Table"

**Created:** 2026-10-08 · License: MIT · Python, stdlib only

> "Claude Code plugin: many sub-agents compete in a bracket, the best survivors merge their ideas at
> a round table into one answer."

It keeps Arena's bracket, strategy cards, rubric and attack/defend/judge loop, and replaces the
"one champion" ending with a **consensus synthesis**:

1. **Tournament.** Single elimination from `n` down to `k`, where each round plays
   `min(alive // 2, alive - k)` matches; the rest take byes.
2. **Pitch.** Each survivor writes its three strongest ideas, the attacks it conceded, and what it
   thinks the others missed.
3. **Table.** Each round, every seat reads the current draft and all pitches, then contributes up to
   five items: **ADOPT** an idea from another seat, **AMEND** a section, or **OBJECT** with a
   concrete counterexample. A **scribe** merges contributions into the next draft and logs every
   rejected item with its reason. Seats then vote ACCEPT or OBJECT. The table stops early when no
   objection stands.
4. **Red team.** Two fresh attackers who never sat at the table attack the draft; seats defend once;
   the scribe writes the final version.
5. **Final check.** A blind judge compares the table's answer with the bracket champion, and the
   better one is delivered.

Its settings: `--n N` (default 100), `--k K` (survivors at the table, default 10), `--table-rounds R`
(max 5, default 3), `--quick` (n = 16), `--auto` (a sizing agent proposes n, k and table rounds),
`--seed`, `--wave`, `--task-file/--task`, `--baseline-file`, and **per-role model assignment**:

| Role | Model |
| --- | --- |
| competitor, defender | haiku |
| attacker, judge, red team, sizer | sonnet |
| table seat, scribe, final check | opus |

The default run (n 100, k 10, 3 table rounds) is about **637 sub-agent calls** — roughly 7% more
than Arena's default, for a materially different output: one merged answer rather than one survivor.

**Why this fork matters:** it directly addresses the two most-cited weaknesses of Arena — that only
one answer is returned, and that independent measurement found solutions converging anyway. Merging
`k` survivors converts convergent diversity into a feature.

### 27.2 Other forks

Several plain forks exist (`asad4ntrp2/claude-agents-arena-skill`, `noeng-ellana/fork-arena-skill`,
etc.) with no additional content. Given the MIT license and zero dependencies, forking-as-install
is the expected pattern.

---

## 28. Sibling skills by the same author

Arena is one pack in a large, consistently-designed family. Context matters, because the same
patterns recur.

| Repository | Stars | What it is |
| --- | --- | --- |
| `arena-skill` | **403** | This skill. |
| `duel-skill` | 9 | **The 1v1 cousin of Arena.** `/duel` sends an answer to an OpenAI model for critique. Claude defends, revises, and returns a claim ledger (retained / revised / dropped / unresolved). Up to 3 rounds; `--quick` for one. Requires `OPENAI_API_KEY` and Python 3.9+. |
| `founder-skill` | 200 | 11 business skills. Its `/founder-consumer` is a direct Arena descendant: "spins up a swarm of buyer sub-agents (default 100, --quick for 20) trained on the user's target customer, each with a different income, age, buying behaviour and objection." |
| `replica-skill` | 1448 | 11 skills that clone any app. |
| `linkedin-agent-skill` | 1697 | 11 LinkedIn account skills. |
| `instagram-agent-skill` | 770 | 13 Instagram account skills. |
| `youtube-agent-skill` | 943 | 11 YouTube channel skills. |
| `x-agent-skill` | 11 | 11 X account skills. |
| `master-skills` | 34 | video editing, virality, and a general "build anything" skill. |
| `promptmaster-skill` | 21 | Rewrites a raw request into an optimised prompt and audits it against 35 credit-waste patterns. |

**The `/duel` comparison is instructive.** Where Arena runs an *internal* tournament against the same
model, `/duel` runs an *external* cross-model critique against a genuine different model, and
deliberately offers **no neutral judge** ("There is no neutral judge and no guarantee the resulting
answer is correct"). The author ships both, and each is explicit about which verification claim it
does and does not make.

**About the author:** Jake Schincariol, GitHub handle `Jakeschincariol`, blog
<https://opusjake.ai>, bio "Always vibes, never slop." He describes himself as an AI operator and
implementation consultant. GitHub profile: 18 public repos, 608 followers, account created
January 2025. He also runs the "Opus Lab" Skool community.

---

## 29. Known issues and open pull requests

| # | Title | State | Summary |
| --- | --- | --- | --- |
| 1 | **It will exhaust all your token in first prompt** | open | "Installed this skill into my Claude and ran a singular prompt, it ran for 16 mins straight and exhausted all the token and didnt even gave any output. optimization may require for longer detailed prompts." |
| 2 (PR) | **Show a token estimate and ask before large runs** | open, unmerged | Proposes always stating a rough token cost, and asking once before any run over 100 sub-agent calls. Changes only SKILL.md "Step 1". Related to #1. |
| 3 | **Claude code** | open | No body; a single reply of "Hey". Effectively unresolved chatter. |

**Assessment:** the repository is one commit, three months-ish of viral growth, and no releases.
The author is shipping skill packs at a high rate rather than maintaining this one at depth. Treat
the default 100-agent size as an experimental default rather than a tuned one, and read issue #1
before your first full run.

---

## 30. Design patterns worth stealing

Independently of whether you use Arena, several of its patterns are directly reusable.

1. **Disk as the source of truth, context as the cursor.** Put all state in one JSON file, have every
   sub-agent write to disk and reply with one line, and have the orchestrator ask a CLI "what next?"
   instead of holding state. A 595-step process then survives compaction.
2. **`next` as a pure function of state.** The orchestrator's entire decision procedure is
   `next_action(state)`. No branching logic in the LLM at all. This is the single most valuable idea
   in the repo.
3. **Encode the prompts in the instruction file, extract them at runtime.** The templates live in
   `SKILL.md` inside HTML-comment fences and are pulled out by regex. What you read is what the
   agent gets, and a test guarantees the extraction still finds all five.
4. **Blind the judge by construction, not by instruction.** The judge template never receives the
   cards. A test asserts `"Reasoning mode" not in judge_brief`.
5. **Coin-flip the labels.** `random.Random("arena-final:%s" % seed)` decides which of
   champion/baseline is "X". Blinding is seeded and recorded, not improvised.
6. **Make the arithmetic authoritative and the model advisory.** `decide()` recomputes the winner
   from the rubric and *overrides the judge's own pick* when they disagree, recording a note. The
   model proposes; the code disposes.
7. **`NO OUTPUT` as a first-class degenerate signal.** A sentinel that promotes a permanently failed
   job to "completed, empty" with defined semantics, so the pipeline terminates instead of
   deadlocking.
8. **Rename unreadable artifacts so they re-enter the queue.** `os.replace(path, path + ".unreadable")`
   turns a parse failure into a missing job automatically.
9. **Write state atomically.** temp file + `os.replace`, so a crash cannot truncate run state.
10. **Cross-check code constants against the human-readable spec with a test.**
    `test_weights_match_the_rubric` parses the Markdown table of `rubric.md` and asserts it equals
    the `WEIGHTS` tuple in the code. Two representations, one truth, enforced.
11. **Validate the extensible data file at load.** `load_strategies` rejects duplicate ids and
    entries missing `name` or `how`, so a bad edit fails immediately and legibly.
12. **Ship the fine print in the README.** Every limitation is stated in the product's own words,
    including the ones that make it look worse. That is why the critical reception of this skill is
    measurement rather than backlash.

---

## 31. Sources

All findings above are traceable to these public sources, retrieved 10 October 2026.

### Primary (the skill itself)

| Source | URL |
| --- | --- |
| Repository | <https://github.com/Jakeschincariol/arena-skill> |
| README | <https://raw.githubusercontent.com/Jakeschincariol/arena-skill/main/README.md> |
| `skills/arena/SKILL.md` | <https://raw.githubusercontent.com/Jakeschincariol/arena-skill/main/skills/arena/SKILL.md> |
| `skills/arena/bracket.py` | <https://raw.githubusercontent.com/Jakeschincariol/arena-skill/main/skills/arena/bracket.py> |
| `skills/arena/strategies.json` | <https://raw.githubusercontent.com/Jakeschincariol/arena-skill/main/skills/arena/strategies.json> |
| `skills/arena/rubric.md` | <https://raw.githubusercontent.com/Jakeschincariol/arena-skill/main/skills/arena/rubric.md> |
| `tests/test_bracket.py` | <https://raw.githubusercontent.com/Jakeschincariol/arena-skill/main/tests/test_bracket.py> |
| `.claude-plugin/plugin.json` | <https://raw.githubusercontent.com/Jakeschincariol/arena-skill/main/.claude-plugin/plugin.json> |
| `.claude-plugin/marketplace.json` | <https://raw.githubusercontent.com/Jakeschincariol/arena-skill/main/.claude-plugin/marketplace.json> |
| LICENSE (MIT) | <https://raw.githubusercontent.com/Jakeschincariol/arena-skill/main/LICENSE> |
| GitHub API (repo, issues, PRs, forks, tree) | `https://api.github.com/repos/Jakeschincariol/arena-skill` |

### Primary (issue thread with the independent benchmark)

| Source | URL |
| --- | --- |
| Issue #1 | <https://github.com/Jakeschincariol/arena-skill/issues/1> |
| Measured benchmark comment | Comment id 5910504055 on issue #1 |
| PR #2 | <https://github.com/Jakeschincariol/arena-skill/pull/2> |
| Issue #3 | <https://github.com/Jakeschincariol/arena-skill/issues/3> |

### Secondary (coverage and directories)

| Source | URL |
| --- | --- |
| Claude Code Club guide | <https://www.claudecodeclub.ai/free-resources/if-claude-keeps-giving-bad> |
| Skills Directory listing | <https://www.skillsdirectory.com/skills/jakeschincariol-arena> |
| Skills Directory author page | <https://www.skillsdirectory.com/authors/jakeschincariol> |
| SkillsLLM comparison | <https://www.skillsllm.com/compare/arena-skill-vs-mattpocock-skills> |
| dijitalburak.com walkthrough | <https://dijitalburak.com/en/depo/claude-arena-skill/> |
| LinkedIn post (Rananjay Raj) | <https://www.linkedin.com/posts/rananjayraj_i-made-100-claudes-compete-over-the-same-activity-7514331452466130945-lChv> |
| Threads post | <https://www.threads.com/@theaiimpact/post/Dd8ZXujlCUY/here-is-the-arena-skill/> |
| TikTok | <https://www.tiktok.com/@theaiimpact/video/7691486038061059335> |
| Instagram reel | <https://www.instagram.com/reel/Dd5nMvRyb1d/> |
| Author site | <https://opusjake.ai> |
| Author GitHub profile | <https://github.com/Jakeschincariol> |

### Derivative

| Source | URL |
| --- | --- |
| `IcaruzSoftware/round-table-skill` | <https://github.com/IcaruzSoftware/round-table-skill> |
| `Jakeschincariol/duel-skill` (sibling) | <https://github.com/Jakeschincariol/duel-skill> |

---

## Appendix A — quick reference card

```
INSTALL     paste https://github.com/Jakeschincariol/arena-skill into Claude Code
            and say: "Install this skill, then confirm /arena works."
RUN         /arena --quick <task>         16 agents, 4 rounds, 91 calls
            /arena --agents 32 <task>     32 agents, 5 rounds, 187 calls
            /arena <task>                100 agents, 7 rounds, 595 calls
PLAN        python3 skills/arena/bracket.py plan --agents 100
CARDS       15 reasoning modes x 12 workflows x 12 strategies = 2,160
RUBRIC      correctness 30, completeness 25, robustness 20, specificity 15,
            clarity 10  (0-10 each, 0-100 weighted total)
FATAL       a verified fatal flaw cannot beat a solution without one
FLOW        spawn -> [attack, defend, judge, collect, advance] x R -> final -> winner
OUTPUT      .arena/run-<timestamp>-s<seed>/   (arena.json, task.md, r0..rN, final, prompts)
REQUIRES    Claude Code + Python 3.8+ ; nothing to install ; tokens are yours
LICENSE     MIT, (c) 2026 Jake Schincariol
```

---

*Compiled from public sources on 10 October 2026. All quoted text is verbatim from the repository,
its issue tracker, or the cited third-party coverage. Code snippets are abridged from
`bracket.py`, `tests/test_bracket.py` and `SKILL.md`.*





