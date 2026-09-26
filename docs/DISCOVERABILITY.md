# Discoverability: SEO, GEO, and AEO strategy

How this repository is engineered to be **found and cited** — by classical search
engines, by AI answer engines, and by AI agents that read the repository directly.

This document is a maintained artifact, not a one-time checklist. When you add a
document, change a public claim, or add a capability, update the relevant section
here in the same change set.

Owner: repository maintainer · Applies to: `README.md`, `docs/`, `llms.txt`,
`llms-full.txt`, `LICENSE`, `CITATION.cff`, `SECURITY.md`, `CODE_OF_CONDUCT.md`

---

## Why three disciplines

| | Optimizes for | Success signal |
| :--- | :--- | :--- |
| **SEO** | Ranking and click-through in classical search | Organic position, qualified visits |
| **AEO** (Answer Engine Optimization) | Extraction into answer-first surfaces — Google AI Overviews, Bing Copilot, Perplexity | Being the extracted answer, not just a link |
| **GEO** (Generative Engine Optimization) | Citation inside LLM-generated answers — ChatGPT, Claude, Gemini, Copilot | Being cited, with attribution |

They are complementary, not competing. A page that is structured for extraction (AEO)
and carries verifiable, attributable evidence (GEO) usually also ranks better
(SEO), because all three reward the same underlying properties: clarity,
structure, freshness, provenance, and machine readability.

A fourth surface matters for a developer project specifically:

| | Optimizes for | Success signal |
| :--- | :--- | :--- |
| **AEO for agents** | Coding and chat agents fetching the repo as context | Correct API usage on the first attempt |

---

## Layer 1 — Repository-level SEO

### 1.1 The GitHub profile is the landing page

- **Repository name and description.** `alpha` with a description that leads with
  the category words a developer would actually search: *open-source autonomous
  multi-agent AI operating system*, *LangGraph*, *agent runtime*, *deep research*,
  *sandboxed execution*, *MCP*.
- **Topics.** The single highest-leverage GitHub SEO control. Intended set:
  `ai-agent`, `agentic-ai`, `multi-agent`, `langgraph`, `llm`, `ai-agents`,
  `autonomous-agents`, `mcp`, `model-context-protocol`, `deep-research`,
  `ai-coding-assistant`, `agent-framework`, `open-source`, `self-hosted`,
  `fastapi`, `nextjs`, `rag`, `ai`, `llmops`, `swarm`, `task-automation`.
  Topics are set through the GitHub API or the repository settings UI — they are
  not expressible in a tracked file, so they must be set and re-checked manually
  when the capability set changes.
- **Social preview image.** Set through repository settings. A readable title,
  the word *open source*, and the word *AI agent* outperform a plain logo.

### 1.2 Legibility and trust signals

Search ranking and LLM citation both weight whether a project looks maintained and
lawful:

| Signal | Where | Status |
| :--- | :--- | :--- |
| License | [`LICENSE`](../LICENSE) | ✅ MIT, present and machine-detectable |
| README "About" blurb | repository settings | Keep aligned with the README's first paragraph |
| Code of conduct | [`CODE_OF_CONDUCT.md`](../CODE_OF_CONDUCT.md) | ✅ |
| Security policy | [`SECURITY.md`](../SECURITY.md) | ✅ with private reporting |
| Citation metadata | [`CITATION.cff`](../CITATION.cff) | ✅ |
| Issue and PR templates | `.github/ISSUE_TEMPLATE/`, `.github/pull_request_template.md` | ✅ |
| CI badges | README header | ✅ 6 workflows, all passing required |
| Release history | GitHub Releases | Tag with SemVer; a repo with releases ranks and is cited differently from one without |

### 1.3 Naming hygiene

Every absolute link points at `https://github.com/itsPremkumar/alpha`. Upstream
ByteDance URLs are retained **only** where they are genuine attribution
(`CHANGELOG.md` history, `references/` citations, subsystem notices). Any *clone
instruction* or *troubleshooting pointer* that still names an upstream repository
is a conversion leak and must be corrected.

---

## Layer 2 — AEO: answer-first structure

### 2.1 The definition paragraph

The first prose block under the H1 must be a **self-contained definition** that is
correct when quoted alone. This is the single most-cited sentence in the document
for both humans and LLMs, so it must contain: what the project *is*, the stack, the
scale, and the licensing/self-hosting posture.

### 2.2 Question-shaped headings

Headings should mirror how people search, not how the code is organized. Applied
in this repository:

| Intent | Heading used |
| :--- | :--- |
| Definition | *What is Alpha?* |
| Comparison | *Alpha vs. other agent frameworks* |
| Job-to-be-done | *What can you build with Alpha?* |
| Time-to-first-value | *60-second quickstart* |
| Selection | *Which deploy option should I choose?* |
| Obstacle | *Troubleshooting*, *Security*, *Known boundaries* |

### 2.3 Answer-first formatting rules

- **Lead with the answer.** The first sentence under any heading answers the
  heading's question. No throat-clearing.
- **Prefer tables for enumerable facts.** Ports, capability counts, comparison
  matrices, config layers, and deploy options are all tables.
- **Prefer lists over prose** for anything scannable.
- **One idea per section**, so a retrieved chunk is still coherent.
- **No unresolved placeholders** in published documentation.

### 2.4 Collapsible depth

The README keeps exhaustive catalogs — 89 engines, 24 skills, the full
architecture — inside `<details>` blocks, so the answer-first content dominates the
first screen while the reference depth stays one click away. This serves humans and
retrieval-based agents equally.

### 2.5 Freshness

LLM answer engines preferentially cite recently-updated pages. Keep
`CHANGELOG.md` current, stamp releases, and revise the README's numbers in the same
change that changes the registries.

---

## Layer 3 — GEO: being cited, not just ranked

### 3.1 Evidence-backed claims

The strongest GEO lever available to a code project is that **its claims are
checkable**. Every capability count in the README is generated from the live
registries and enforced by CI:

```bash
python backend/scripts/generate_feature_manifest.py   # -> contracts/feature_manifest.json
```

`contracts/feature_manifest.json` pins 130 tools, 60 routers, 42 middlewares, and 8
supervisor loops, and the generated-drift gate fails the build if the docs and the
registries disagree. A reader (or an LLM) can therefore verify any number in the
README in one command. That verifiability *is* the authority signal.

### 3.2 Explicit citation contract applied to ourselves

The repository practices what it ships:

- **Primary sources** are linked, not paraphrased.
- **Upstream provenance** is credited explicitly in the README's *Project
  provenance* section and in `docs/THIRD_PARTY_MEMORY_NOTICES.md`.
- **Machine-readable citation** is available as `CITATION.cff` and a BibTeX block
  in the README.
- **Quotable answers** live in `docs/FAQ.md` in a Q/A form that can be lifted
  verbatim.

### 3.3 Transparency about limits

LLM answer engines suppress sources that overstate. Alpha documents its boundaries
in four places — the README's honest-limits paragraph, `docs/COMPARISON.md`'s *What
Alpha does not claim*, the llms files' *Known boundaries* sections, and
`docs/PRODUCTION_READINESS_INVENTORY.md`. This is both an ethics requirement and a
citation strategy: a hedged, accurate source gets quoted; an overclaiming one gets
dropped.

### 3.4 Named entities and disambiguation

The first paragraph always names the project, the author, the repository, the
stack, and the license. An answer engine asked "what is Alpha" can resolve the
entity without guessing.

---

## Layer 4 — Agent-readable surfaces (`llms.txt` family)

Per the [`llms.txt` specification](https://llmstxt.org/), which follows the same
convention as `robots.txt` and `sitemap.xml`: a small, curated, LLM-readable
overview at a conventional path, with detail behind links.

| File | Purpose |
| :--- | :--- |
| [`/llms.txt`](../llms.txt) | The compact canonical overview. H1, one-paragraph blockquote, then `##` sections of curated links with one-line notes. Ends with provenance and citing guidance. |
| [`/llms-full.txt`](../llms-full.txt) | Expanded single-file context: architecture, commands, configuration, capability taxonomy, API surface, known boundaries, and load-bearing repository contracts. |
| [`/docs/llms.txt`](llms.txt) | Docs-scoped index, covering only the `docs/` path. |

Conventions this repository follows:

- Only the H1 is mandatory; every other section is a curated link list.
- Each link carries a short, informative note after a colon — never a bare URL.
- Secondary material goes in an `## Optional` section so a short-context agent can
  skip it.
- Absolute `https://github.com/itsPremkumar/alpha/blob/main/...` URLs are used, so
  a citation survives being copied out of context.
- Files are tested the way the spec recommends: hand `llms.txt` to an agent, ask it
  questions, and check the answers.

`llms.txt` and `llms-full.txt` are also **plain-text files that GitHub indexes**,
so they function as classic SEO content as well as agent context.

---

## Layer 5 — Content architecture

The documentation is structured as a hub-and-spoke graph, which is what both
crawlers and retrieval systems traverse well.

```
README.md                      ← hub: definition, comparison, quickstart, FAQ
├── docs/COMPARISON.md         ← "Alpha vs X" (highest-intent page class)
├── docs/USE_CASES.md          ← job-to-be-done long tail
├── docs/GLOSSARY.md           ← terminology entity graph
├── docs/FAQ.md                ← quotable Q/A
├── docs/GETTING_STARTED.md    ← task completion
├── docs/ARCHITECTURE.md       ← depth
├── docs/API_REFERENCE.md      ← reference
├── docs/SECURITY.md           ← trust
├── docs/DEPLOYMENT.md         ← operational
├── docs/TROUBLESHOOTING.md    ← obstacle
└── docs/INDEX.md              ← generated, complete
```

Rules:

- **Every new document gets an entry** in `scripts/generate_docs_index.py`'s
  `FILE_OVERRIDES` map. That generator fails closed on an unclassified Markdown
  file, which is what keeps this graph from rotting.
- **One question per document.** If a document answers two questions, it is two
  documents.
- **Link with intent.** Every link says what the target contains, so the anchor text
  itself carries retrieval keywords.

---

## Layer 6 — Off-site authority

Documentation alone cannot manufacture authority. The compounding channels, in
order of return for a project like this:

| Channel | Action | Why it works |
| :--- | :--- | :--- |
| **Comparison-page citation** | Publish a factual, cited comparison; get it referenced by framework aggregators | Comparison queries are the highest commercial intent in this category |
| **Launch platforms** | Hacker News Show HN, Reddit (r/LocalLLaMA, r/AI_Agents, r/opensource), Product Hunt | Reddit, Wikipedia, and YouTube are among the most-cited sources across AI answer engines |
| **Technical writing** | Publish the deep material already in `references/` — AVO loops, RSI safety, swarm consensus | Original research is the most reliably cited content class |
| **Integration listings** | MCP registries, `awesome-*` lists, LangChain/LangGraph ecosystem listings | Ecosystem lists are heavily crawled and frequently cited |
| **Video and demos** | Screen recordings of the workspace, the Windows app, and voice mode | YouTube is a top-cited source across AI engines |
| **Community** | Discord, GitHub Discussions, issue threads with resolved answers | Forum threads are cited and are genuinely useful |

Consistency rule: the *same* description, the *same* category words, and the *same*
canonical URL everywhere. Entity fragmentation is the most common self-inflicted
GEO failure.

---

## Layer 7 — Measurement

| Question | Where to look |
| :--- | :--- |
| Am I ranking? | Google Search Console on the repository and any docs site; GitHub repo traffic (Insights → Traffic) |
| Am I being cited by ChatGPT / Perplexity / Gemini? | A recurring prompt set: "what is Alpha", "Alpha vs LangGraph", "best open source multi-agent OS", "self-hosted AI agent platform". Run monthly and record verbatim answers plus citations. |
| Is the off-site profile coherent? | Audit the canonical name, description, and URL across every launch/listing/profile |
| Are my docs being read? | GitHub content traffic per file; `llms.txt` fetches from bots in web logs |
| Are the numbers still true? | `python backend/scripts/generate_feature_manifest.py` and the generated-drift gate |

**Prompt-set discipline.** A fixed, versioned set of test prompts is the only way to
detect citation change over time, because answer engines vary output run to run.
Keep the set small, keep it stable, and record the date of every measurement.

---

## Maintenance checklist

Run through this in the same change set as any user-facing or capability change.

- [ ] Capability numbers in the README still match `contracts/feature_manifest.json`
- [ ] `README.md` first paragraph is still a correct standalone definition
- [ ] New/changed/removed docs have entries in `scripts/generate_docs_index.py`
- [ ] `python scripts/generate_docs_index.py` runs clean
- [ ] `llms.txt` and `llms-full.txt` mention the change
- [ ] `llms.txt` link list still resolves — no moved or renamed documents
- [ ] `docs/FAQ.md` answers are still true
- [ ] `CHANGELOG.md` has an entry
- [ ] No new `bytedance/agent-workspace` URL in a clone or support instruction
- [ ] GitHub topics and the "About" blurb still match the capability set
- [ ] Version strings still agree across `backend/pyproject.toml`,
      `frontend/package.json`, and `deploy/helm/agent-workspace/Chart.yaml`

---

## Related reading

- [llmstxt.org — the `/llms.txt` file specification](https://llmstxt.org/)
- [FAQ.md](FAQ.md) · [COMPARISON.md](COMPARISON.md) · [USE_CASES.md](USE_CASES.md)
- [README.md](../README.md) · [CONTRIBUTING.md](../CONTRIBUTING.md)
