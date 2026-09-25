# Memory types — canonical taxonomy and Alpha coverage

One page that answers "what kinds of memory does this agent have, where does
each one live, and what is still missing?" The taxonomy is the union of the
CoALA framework (Sumers et al., 2023, arXiv:2309.02427: working / episodic /
semantic / procedural), the cognitive-science extensions used across agent
memory work (prospective, spatial, affective, autobiographical), and the
categories the 2026 frameworks converged on (entity/graph, shared/collective,
narrative, scene/contextual, directive). Coverage below is measured against
this repository, not aspirational.

Status values: **HAVE** (implemented and wired), **PARTIAL** (implemented but
narrower than the type implies), **WAVE** (being built in the current wave),
**GAP** (honestly absent — no fake claims).

| # | Type | What it holds | Where it lives in Alpha | Status |
|---|---|---|---|---|
| 1 | Working / in-context | Active scratchpad for the current task: hypotheses, goals, in-flight observations | `alpha/memory/cognitive/working_memory.py`; injected as the "Active Cognitive Scratchpad" block (`prompt.py`) | HAVE |
| 2 | Episodic (flat) | Time-stamped traces of what the agent did, with outcomes | `alpha/memory/cognitive/episodic_memory.py` (`EpisodicTrace`) | HAVE |
| 3 | Episodic (hierarchical) | Multi-level abstracted episodes distilled from traces | `alpha/memory/cognitive/episodic_memory.py` (`HierarchicalEpisode`) | HAVE |
| 4 | Semantic / factual | Subject-predicate-object beliefs with confidence, provenance, conflict reconciliation | `alpha/memory/cognitive/semantic_graph.py`; DeerMem facts; L1 typed records | HAVE |
| 5 | Personal-semantic | Stable facts about the user (preferences, identity, constraints) | L1 `persona` records + synthesized profile (`l1/persona.py`); DeerMem user scope | HAVE |
| 6 | Procedural | Skills, playbooks, SOPs, behavioral rules, success/failure statistics | `alpha/memory/cognitive/procedural_memory.py`; `alpha/skills/` (forge/proposals); L1 `work_method` | HAVE |
| 7 | Directive / instruction | Standing orders the user gave ("always…", "never…"), including strict sentinels | L1 `instruction` type with the `-1` strict-order priority | HAVE |
| 8 | Contextual / scene | Where/when a memory happened; scene segmentation and continuity | L1 scene segments (`scene_name`, `last_scene` cursor per thread) | PARTIAL — scenes tag records but there is no first-class scene index or scene-scoped recall |
| 9 | Spatio-temporal | Where things are and how state changed; point-in-time facts and intervals | `alpha/memory/cognitive/spatio_temporal.py` (Graphiti/Letta-inspired interval validity) | HAVE |
| 10 | Associative | Hebbian links and spreading activation across tiers | `alpha/memory/cognitive/associative_memory.py` | HAVE |
| 11 | Reflective / consolidative | Sleep-style consolidation, decay, salience, belief reconciliation | `alpha/memory/cognitive/consolidation.py`; `alpha/memory/dreaming/` | HAVE |
| 12 | Hybrid retrieval | BM25 + vector + graph + recency, fused with RRF | `alpha/memory/cognitive/retrieval.py`; L1 store hybrid scoring | HAVE |
| 13 | Typed working memory (L1) | Scene-segmented extraction with priorities, batch conflict detection, quotas, provenance, retention, persona | `alpha/agents/memory/l1/` + `l1_memory_middleware.py` + `<memory>` injection | HAVE |
| 14 | Prospective / intentional | Future commitments and reminders: "remember to X", due dates, trigger conditions, completion | — | GAP → **WAVE** (`alpha/memory/prospective/`) |
| 15 | Affective | Emotional tone of interactions: valence, arousal, intensity, mood trajectory, mood-aware recall | — | GAP → **WAVE** (`alpha/memory/affective/`) |
| 16 | Autobiographical / narrative | A life story: ordered, human-readable timeline synthesized from episodes and records | — (persona profile is a summary, not a narrative) | GAP → **WAVE** (`alpha/memory/narrative/`) |
| 17 | Entity / graph-linked | Named entities extracted from memories with alias resolution and entity-scoped recall | Semantic graph stores SPO beliefs; no entity-extraction/link index over raw memories | GAP → **WAVE** (`alpha/memory/entities/`) |
| 18 | Social / shared | Who the agent deals with, relationships, and team-shared knowledge with audience scoping | L1 `work_*` types are team-shared; no per-audience or inter-agent relationship model | GAP → **WAVE** (`alpha/memory/social/`) |
| 19 | Scenario-conditioned recall | Which memory surfaces matter for the kind of work in progress (coding vs research vs ops vs planning) | Memory is injected once per session or fetched by explicit search | GAP → **WAVE** (`alpha/memory/scenarios/`) |

## Rules this project holds itself to

- **Additive only.** A new type never replaces or deletes an existing store;
  types compose at the recall seam.
- **Wired or absent.** A type that no lifecycle path can reach is not shipped
  as "done" — the wave that adds each type includes its capture trigger, its
  recall surface, its configuration, and a test proving the wiring fires.
- **Honest degradation.** If a type cannot be read or written (no model, no
  key, corrupt store), the failure is disclosed; it never degrades to an empty
  success.
- **No copied code without a license.** Implementation follows the
  permissive-licensed references recorded in
  `docs/THIRD_PARTY_MEMORY_NOTICES.md`; copyleft sources are read for ideas
  only.

## Sources for the taxonomy

- CoALA: Cognitive Architectures for Language Agents (arXiv:2309.02427) —
  working / episodic / semantic / procedural.
- "Memory in the Age of AI Agents" (arXiv:2512.13564) — consolidation
  pathways, multi-agent memory governance.
- Zep/Graphiti — bi-temporal facts, entity linking; Letta — tiered
  core/recall/archival memory; Mem0 — user (personal-semantic) memory and
  graph entity linking; LangMem — procedural memory; the 2026 framework
  surveys' shared taxonomy (working, episodic, semantic, procedural,
  long-term, shared).
