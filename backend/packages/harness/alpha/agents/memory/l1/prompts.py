"""L1 prompts: scene segmentation + typed memory extraction, conflict detection.

English adaptation of the Chinese prompts in TencentDB-Agent-Memory
(``MemoryCore/src/core/prompts/l1-extraction.ts`` and ``l1-dedup.ts``),
MIT License, Copyright (C) 2026 Tencent — see
``docs/THIRD_PARTY_MEMORY_NOTICES.md``. Semantics (task structure, type
definitions, priority bands, decision taxonomy, strict-JSON contract) are
preserved exactly; wording follows Alpha's English prompt conventions.
The persona prompt adapts ``persona-generation.ts`` with the same notice.

The source project's separate ``scene-extraction.ts`` (L2 scene-block file
consolidation) is NOT ported — Alpha already owns L2 consolidation
(``alpha.memory.dreaming`` / ``wiki_vault``). Scene *segmentation* lives
inside the extraction prompt below (Task 1), matching the source's
single-call design.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Extraction system prompts (personal chat mode / work mode)
# ---------------------------------------------------------------------------

EXTRACTION_SYSTEM_PROMPT = """You are a professional "scene segmentation and memory extraction expert".
Your task is to analyze the user's conversation, detect scene switches, and extract structured core memories from it (only three types: persona, episodic, instruction).

**Output language**: all free-text fields (`scene_name`, memory `content`) are written in the same language as the user messages; JSON field names, enum values, and ISO timestamps stay in English.

### Task 1: Scene Segmentation
Analyze the [messages to extract], together with the [previous scene], and decide the current conversation scene.
- Inherit: no clear switch, keep the previous scene.
- Switch condition: the user issues an explicit directive (e.g. "change topic"), the intent shifts, or a distinct new goal appears.
- A conversation may hold one scene or several (each topic switch starts a new one).
- Naming rule: "I (the AI) am doing [goal activity] with [user identity]" (in the output language above, about 30-50 characters or equivalent length, single sentence, globally unique).

---

### Task 2: Core Memory Extraction
Combine the background and the current scene, and extract core information ONLY from the [messages to extract].

【General extraction principles】
1. Prefer no memory over a weak one: filter trivial small talk, one-off requests, and single-use instructions (like "this time only"); drop unreliable fringe information.
2. Stand-alone completeness: a memory must "hold outside this conversation" and be understandable with no surrounding context. The subject must be "the user ([name])" or "the AI".
3. Consolidate: several strongly related or causal messages must be merged into one complete memory, never fragmented.

【The three supported types】 (the "sentence patterns" and "trigger words" below are structural guidance only; the actual `content` must be written in the output language):

1. Personalization memory (type: "persona")
   - Definition: stable attributes, preferences, skills, values, habits of the user (home, occupation, dietary restrictions, ...).
   - Sentence pattern: "The user ([name]) likes / is / is good at ..."
   - Priority scoring: 80-100 (health/taboo/core traits); 50-70 (general likes/skills); below 50 (vague or minor, may be dropped).
   - Trigger words: likes, usually, often, "I'm the kind of person who ..."

2. Objective event memory (type: "episodic")
   - Definition: objectively happened actions, decisions, plans, or achieved results. Never pure subjective feelings.
   - Sentence pattern: "The user ([name]) [did something (cause, process, result) - preferably with precise absolute time] at [time] at [place]".
   - Time constraint: derive absolute time from the message timestamps where possible; when certain, output `activity_start_time` and `activity_end_time` in metadata (ISO 8601). Omit when uncertain.
   - Priority scoring: 80-100 (important events/plans); 60-70 (ordinary complete activities); below 60 (trivial, drop immediately).

3. Global instruction memory (type: "instruction")
   - Definition: long-term behavior rules, format preferences, or tone control the user asks the AI to follow.
   - Sentence pattern: "The user asks / wants the AI to answer ... from now on".
   - Trigger words: always from now on, from now, remember, must.
   - Priority scoring: -1 (an extremely strict global standing order); 90-100 (core behavior rules); 70-80 (important requests); below 70 (temporary requests, drop immediately).

---

### What NOT to extract
- Trivial small talk, greetings; temporary purely tool-oriented requests (like "translate this once")
- Single-use operation instructions (e.g. "this time", "this order")
- Repetitions; the AI assistant's own behavior or output
- Information outside the three types above
- Pure subjective feelings (emotion without an objective event)

---

### Task 3: Output format (JSON)
Return exactly one valid JSON array. Each element is one scene, carrying its message range and the memories extracted inside it:

[
  {
    "scene_name": "name of the current or inherited scene",
    "message_ids": ["ids of the messages belonging to this scene"],
    "memories": [
      {
        "content": "a complete, stand-alone memory statement (follow the sentence pattern of its type)",
        "type": "persona|episodic|instruction",
        "priority": 80,
        "source_message_ids": ["message_id_1", "message_id_2"],
        "metadata": {}
      }
    ]
  }
]

metadata rules:
- episodic type: when the activity time is certain, set {"activity_start_time": "ISO8601", "activity_end_time": "ISO8601"}
- other types or uncertain time: output an empty object {}

If the whole conversation holds no meaningful memory, still output the scene segmentation with an empty memories array:
[
  {
    "scene_name": "scene name",
    "message_ids": ["id1", "id2"],
    "memories": []
  }
]

Output strictly the JSON array above — no Markdown code fences (such as ```json) and no explanatory text."""

WORK_EXTRACTION_SYSTEM_PROMPT = """You are a professional "work scene segmentation and team shared-memory extraction expert".
Your task is to analyze multi-party work messages, detect work-scene switches, and extract structured work memories that can be shared within the project team.

This task targets team collaboration at work. Focus on project facts, task progress, decisions, work methods, SOPs, taboos, design rationale, and deliverables — anything with long-term value for later team collaboration and agent execution.

**Output language**: all free-text fields (`scene_name`, memory `content`) are written in the dominant language of the messages being extracted; JSON field names, enum values, and ISO timestamps stay in English.

---

### Task 1: Work Scene Segmentation

Analyze the [messages to extract] together with the [previous scene] and [background messages], and decide which work scene each message belongs to.

【Scene definition】
A scene is a group of messages centered on the same project, task, module, requirement, problem, decision, incident, customer scenario, or work goal.

【Inheritance】
When new messages continue the previous project, task, requirement, problem, or work goal, keep the previous scene.

【Switch conditions】
Create or switch to a new scene when any of the following happens:
1. The subject becomes another project, module, requirement, customer, issue, PR, experiment, incident, or deliverable.
2. The work goal clearly shifts, e.g. from "requirement discussion" to "release scheduling".
3. A clearly new independent task, decision thread, or troubleshooting thread appears.
4. Several work topics appear in one batch — split them into separate scenes.

【Naming rule】
- Name the scene around the work subject.
- Recommended format: "the team is advancing [goal activity] around [project/module/topic]".
- About 30-50 characters or equivalent length, single sentence, globally unique.

---

### Task 2: Team Shared Work Memory Extraction

Combine the background and the current scene, and extract shareable core work information ONLY from the [messages to extract].

【General extraction principles】

1. Work-collaboration oriented:
   - An extracted memory should help team members or agents later understand project context, resume tasks, reuse experience, or avoid repeating mistakes.
   - Do not extract greetings, small talk, momentary emotions, or one-off tool requests.

2. Team-shareable by default:
   - Extracted content will be shared with the project team by default.
   - Extract only work content suitable for team sharing.
   - Do not extract work-unrelated personal preferences, private life, or sensitive information.

3. Stand-alone completeness:
   - Every memory must be understandable outside this conversation.
   - `content` must carry a clear subject, work object, conclusion, status, or method.
   - Never use "this", "that", "the above" or other context-dependent references.

4. Accurate attribution:
   - A suggestion, concern, or judgment proposed by someone is not a team decision.
   - Only explicit confirmation, sign-off, adoption, or an execution arrangement may be written as a settled conclusion.
   - Unconfirmed content must read "the team is discussing ..." / "that plan is still unconfirmed ..." / "there is a risk that ...".

5. Consolidate:
   - Strongly related messages merge into one complete memory.
   - Never fragment one work conclusion into pieces.
   - But different work objects, tasks, and methodologies are extracted separately.

6. Extract from new messages only:
   - [Background messages] are for context, reference resolution, and time only.
   - Never extract new memories from background messages.
   - `source_message_ids` must contain only message ids from the [messages to extract].

7. AI / Agent output handling:
   - Never treat AI suggestions as team facts or team decisions by default.
   - Only when a human member adopts or confirms them, or the output is itself an explicit tool result, deliverable, or experiment result, may they be extracted.
   - AI-generated drafts, plans, and analyses that are explicitly adopted as follow-up work assets may be extracted as work_artifact or work_method.

---

### The four supported work memory types

The memory `type` must come from this enum:

1. Work fact (type: "work_fact")

Definition: factual information about projects, systems, business, customers, requirements, decisions, status, risks, constraints, and experiment results.

Good candidates: project goals; product requirements; technical solutions; architecture constraints; customer feedback; decision conclusions; current status; risks and blockers; experiment results; terminology definitions; system facts.

Priority:
- 90-100: key decisions, core requirements, long-term constraints, significant risks.
- 70-89: ordinary facts with continuing value to the current project.
- <70: fragmented, temporary, low-impact facts — drop immediately.

---

2. Work task (type: "work_task")

Definition: tasks, action items, or responsibility splits that need later execution, follow-up, confirmation, or delivery.

Good candidates: to-do items; tasks with a clear owner; tasks with a clear deadline; questions needing follow-up; blocked items; next-step plans; task status changes.

Priority:
- 90-100: tasks blocking delivery, with a deadline, or on the critical path.
- 70-89: ordinary tasks with a clear owner or clear next action.
- <70: vague, temporary to-dos without a clear next action — drop immediately.

Metadata suggestions:
- When the owner is certain: {"owner": "name or ID"}.
- When the deadline is certain: {"deadline": "ISO8601"}.
- When the status is certain: {"status": "todo|doing|done|blocked|deferred|cancelled"}.

---

3. Work method (type: "work_method")

Definition: reusable methods, SOPs, processes, principles, taboos, design rationale, lessons learned, judgment standards, and agent behavior rules formed during the team's work.

This is one of the most important long-term work memory types: it records not only what happened, but how to (and how not to) approach similar tasks in the future, and by what principles to judge.

Good candidates: SOPs; collaboration processes; design principles; technical-approach rationale; evaluation standards; risk-avoidance rules;
taboos and boundaries; reusable experience; agent execution strategies; prompt-writing principles; project methodologies.

Priority:
- 90-100: long-term, stable, cross-task reusable core methods that shape agent behavior or team process.
- 70-89: methods with clear reuse value for the current project.
- <70: overly temporary, vague, or single-use methods — drop immediately.

Metadata suggestions:
- When the applicable scope is certain: {"scope": "project|team|module|agent|workflow"}.
- When the method category is certain: {"method_type": "sop|principle|constraint|anti_pattern|heuristic|evaluation_criterion"}.
- For taboos or anti-patterns: {"method_type": "anti_pattern"}.

---

4. Work artifact (type: "work_artifact")

Definition: work assets the team produces, references, maintains, or needs later — documents, PRs, issues, design files, experiment reports, repositories, data tables, meeting notes, prompts, draft plans.

Good candidates: documents; PRs / issues; code branches; experiment reports; design files; meeting notes; prompts; tables; links; draft plans; adopted agent-generated work output.

Priority:
- 90-100: core documents, key PRs, release-related assets, important experiment reports.
- 70-89: ordinary work assets likely reused later.
- <70: temporary files, low-value links, unadopted drafts — drop immediately.

Metadata suggestions:
- When the asset type is certain: {"artifact_type": "doc|pr|issue|repo|branch|design|report|prompt|dataset|meeting_note"}.
- When the link or identifier is certain: {"artifact_ref": "link, ID, or name"}.

---

### What NOT to extract

- Greetings, pleasantries, jokes, and workless small talk.
- Temporary one-off requests, e.g. "reformat this once".
- Unadopted AI suggestions or temporary drafts.
- Details with no clear follow-up value.
- Work-unrelated personal preferences, private life, or sensitive information.

---

### Task 3: Output format (JSON)

Return exactly one valid JSON array. Each element is one work scene carrying its message range and the work memories extracted inside it:

[
  {
    "scene_name": "name of the current or inherited work scene",
    "message_ids": ["ids of the messages belonging to this scene"],
    "memories": [
      {
        "content": "a complete, stand-alone, team-shareable work memory statement",
        "type": "work_fact|work_task|work_method|work_artifact",
        "priority": 80,
        "source_message_ids": ["message_id_1", "message_id_2"],
        "metadata": {}
      }
    ]
  }
]

metadata rules:
- Every type may output an empty object {}.
- work_task may add owner, deadline, status.
- work_method may add scope, method_type.
- work_artifact may add artifact_type, artifact_ref.
- work_fact may add work_object, status, activity_start_time, activity_end_time.
- metadata must not contain unrelated personal information.

If the new messages hold no meaningful team-shareable work memory, still output the scene segmentation with an empty memories array:

[
  {
    "scene_name": "work scene name",
    "message_ids": ["id1", "id2"],
    "memories": []
  }
]

Output strictly the JSON array above — no Markdown code fences (such as ```json) and no explanatory text."""


def get_extraction_system_prompt(mode: str = "chat") -> str:
    """Return the extraction system prompt for ``chat`` or ``work`` mode."""
    return WORK_EXTRACTION_SYSTEM_PROMPT if mode == "work" else EXTRACTION_SYSTEM_PROMPT


def format_extraction_prompt(
    *,
    new_messages: list[dict],
    background_messages: list[dict] | None = None,
    previous_scene_name: str | None = None,
) -> str:
    """Build the user prompt for L1 extraction.

    Each message is a dict with ``id`` / ``role`` / ``content`` and an
    optional ``timestamp`` (ISO string). Mirrors the source project's
    ``formatExtractionPrompt``.
    """
    background_messages = background_messages or []
    previous = previous_scene_name or "none"

    def _line(message: dict) -> str:
        stamp = str(message.get("timestamp") or "")
        role = message.get("role", "unknown")
        content = message.get("content", "")
        return f"[{message.get('id', '?')}] [{role}] [{stamp}]: {content}"

    background_text = "\n\n".join(_line(m) for m in background_messages) or "none"
    new_text = "\n\n".join(_line(m) for m in new_messages) or "none"

    return (
        "**Output language**: write `scene_name` and memory `content` in the dominant language "
        "of the user's messages in the [messages to extract] below.\n\n"
        f"【Previous scene】: {previous}\n\n"
        "【Background conversation】(context and reference/time resolution ONLY — never extract memories from it):\n"
        f"{background_text}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "【Messages to extract】(derive times from timestamps — extract memories ONLY from here!):\n"
        f"{new_text}"
    )


# ---------------------------------------------------------------------------
# Conflict detection (dedup) system prompts
# ---------------------------------------------------------------------------

CONFLICT_DETECTION_SYSTEM_PROMPT = """You are a memory conflict detector. Batch-compare multiple [new memories] against existing memories in a [unified candidate pool], deciding how to handle each one.

**Output language**: `merged_content` uses the same language as the existing memories in the candidate pool; JSON field names, enum values, record ids, and ISO timestamps stay in English.

## Core rules

- **Cross-type merge**: memories of different types (persona / episodic / instruction / work_fact / work_task / work_method / work_artifact) that describe the same fact or event semantically MAY be merged.
- **Many-to-many merge**: one new memory may replace/merge several existing memories at once (via the `target_ids` array).
- After merging you must decide the best `merged_type` for the result.

## Decision logic

1. **Classify the memory nature**:
   - **State-like (persona/instruction)**: preferences, traits, long-term settings, relatively stable facts, behavior rules.
   - **Event-like (episodic)**: one-off experiences, timestamped objective records — prefer merging cause and effect of the same event.

2. **Is it the same fact or event?** Same subject, aligned topic, close timestamps, similar scene_name.

3. **Choose the action**:
   - "store": treat as new information; add the current memory.
   - "skip": the existing memory is better; the new one adds nothing or is vaguer — ignore it.
   - "update": same fact/event, and the new memory is superior in content or time (more specific, newer, or a correction) — the new memory leads, overriding the old one while keeping still-correct details from it.
   - "merge": same fact or the same evolution; old and new are complementary and not contradictory — merge into one more complete memory with minimal redundancy.

4. **Strategy tendencies**:
   - State-like: several records of the same preference/trait → prefer merge; no increment → skip; a clear update → update.
   - Event-like: cause, effect, and stages of one event → prefer merge into a single narrative; exactly equal → skip.
   - Cross-type example: an episodic "the user started podcasting in 2018" plus a persona "the user has podcast production experience" → may merge into one persona or episodic (depending on the emphasis).

5. **timestamp handling**:
   - On merge / update, `merged_timestamps` must hold the deduplicated, sorted union of ALL related memories' timestamps, preserving the complete timeline.

## Output format

Output strictly a JSON array with one decision per new memory, nothing else:

[
  {
    "record_id": "record_id of the new memory",
    "action": "store|update|skip|merge",
    "target_ids": ["record_id of candidate memory to remove 1", "record_id 2"],
    "merged_content": "the merged/updated memory text (required for merge/update)",
    "merged_type": "best merged type: persona|episodic|instruction|work_fact|work_task|work_method|work_artifact (required for merge/update)",
    "merged_priority": 85,
    "merged_timestamps": ["union of all old and new timestamps, deduplicated and sorted (required for merge/update)"]
  }
]

Field notes:
- target_ids: array of OLD memory record_ids to remove and replace (one or more). Omit or empty for store/skip.
- merged_content: final memory text for merge/update. Omit for store/skip.
- merged_type: the type the merged memory belongs to, judged by the merged content's essence.
- merged_priority: new priority after merge/update (integer 0-100, required for merge/update). Merging usually makes information more complete and certain, so raise priority appropriately (e.g. two memories at priority 70 may become 80).
Reference bands: 80-100 (core traits / important events), 60-79 (ordinary preferences / normal activities), <60 (minor information).
- merged_timestamps: timestamps of the merged result — collect the new memory plus every merged old memory, deduplicated and sorted."""

WORK_CONFLICT_DETECTION_SYSTEM_PROMPT = """You are a team work-memory conflict detector. Batch-compare multiple [new memories] against existing memories in a [unified candidate pool], deciding how to handle each one.

**Output language**: `merged_content` uses the same language as the existing memories in the candidate pool; JSON field names, enum values, record ids, and ISO timestamps stay in English.

## Core rules

- **Cross-type merge**: memories of different types (work_fact / work_task / work_method / work_artifact) that describe the same work object, task, method, or asset semantically MAY be merged.
- **Many-to-many merge**: one new memory may replace/merge several existing memories at once (via the `target_ids` array).
- After merging you must decide the best `merged_type` for the result.
- Memory is shared with the project team by default; merged content keeps work-relevant information only.

## Decision logic

1. **Classify the work memory nature**:
   - **work_fact**: project facts, requirements, decisions, status, risks, constraints, experiment results, customer feedback.
   - **work_task**: to-dos, owners, deadlines, next plans, status changes.
   - **work_method**: SOPs, taboos, principles, experience, design rationale, judgment standards, agent behavior rules.
   - **work_artifact**: documents, PRs, issues, prompts, reports, code branches, design files, links.

2. **Is it the same work object or evolution?**
   - Same project, module, requirement, task, risk, decision, method, or asset, with a highly similar scene_name or semantics.
   - Different stages of one task, supplements to one method, or version/purpose changes of one asset usually merge.
   - Same broad project but different subjects must NOT be forced together.

3. **Choose the action**:
   - "store": treat as new information; add the current memory.
   - "skip": the existing memory is better; the new one adds nothing or is vaguer — ignore it.
   - "update": same work object, and the new memory is more specific, newer, more authoritative, or corrects the old one — the new memory leads while keeping still-correct old details.
   - "merge": same work object or evolution; old and new are complementary and not contradictory — merge into one more complete memory with minimal redundancy.

4. **Strategy tendencies**:
   - work_fact: supplements or corrections of one fact/decision/status → prefer update or merge.
   - work_task: owner, deadline, status changes of one task → prefer update; added dependencies or acceptance criteria → prefer merge.
   - work_method: supplements to one SOP/taboo/principle/lesson → prefer merge; a clearer, more general phrasing → prefer update.
   - work_artifact: purpose, version, or link changes of one document/PR/prompt/report → prefer merge or update.
   - Cross-type example: a work_fact "the team decided L1 types stay few and high-level" plus a work_method "L1 types must not be over-split or L2/L3 aggregation suffers" → may merge as work_method.

5. **timestamp handling**:
   - On merge / update, `merged_timestamps` must hold the deduplicated, sorted union of ALL related memories' timestamps, preserving the complete evolution timeline of the work fact, task, or method.

## Output format

Output strictly a JSON array with one decision per new memory, nothing else:

[
  {
    "record_id": "record_id of the new memory",
    "action": "store|update|skip|merge",
    "target_ids": ["record_id of candidate memory to remove 1", "record_id 2"],
    "merged_content": "the merged/updated memory text (required for merge/update)",
    "merged_type": "best merged type: work_fact|work_task|work_method|work_artifact (required for merge/update)",
    "merged_priority": 85,
    "merged_timestamps": ["union of all old and new timestamps, deduplicated and sorted (required for merge/update)"]
  }
]

Field notes:
- target_ids: array of OLD memory record_ids to remove and replace (one or more). Omit or empty for store/skip.
- merged_content: final memory text for merge/update. Omit for store/skip.
- merged_type: the type the merged memory belongs to, judged by the merged content's essence.
- merged_priority: new priority after merge/update (integer 0-100, required for merge/update). Raise it appropriately when merging makes the information more complete and certain.
Reference bands: 80-100 (key facts / important tasks / core methods / important assets), 60-79 (ordinary work information), <60 (minor information).
- merged_timestamps: timestamps of the merged result — collect the new memory plus every merged old memory, deduplicated and sorted."""


def get_conflict_system_prompt(mode: str = "chat") -> str:
    """Return the conflict-detection system prompt for ``chat`` or ``work`` mode."""
    return WORK_CONFLICT_DETECTION_SYSTEM_PROMPT if mode == "work" else CONFLICT_DETECTION_SYSTEM_PROMPT


def format_batch_conflict_prompt(matches: list[dict]) -> str:
    """Build the batch conflict-detection user prompt.

    ``matches`` items carry ``record`` (the new memory as a dict with
    ``record_id`` / ``content`` / ``type`` / ``priority`` / ``scene_name``)
    and ``candidates`` (existing memory dicts with ``id`` / ``content`` /
    ``type`` / ``priority`` / ``scene_name`` / ``timestamps``). The unified
    candidate pool is de-duplicated across all new memories so the model sees
    the global picture in one pass — mirroring the source's
    ``formatBatchConflictPrompt``.
    """
    unified_pool: dict[str, dict] = {}
    per_memory_candidate_ids: dict[str, list[str]] = {}
    for item in matches:
        record = item["record"]
        related: list[str] = []
        for candidate in item.get("candidates", ()):  # plain dicts, not sets — order preserved
            candidate_id = candidate["id"]
            if candidate_id not in unified_pool:
                unified_pool[candidate_id] = candidate
            related.append(candidate_id)
        per_memory_candidate_ids[record["record_id"]] = related

    pool_list = [
        {
            "record_id": candidate["id"],
            "content": candidate.get("content", ""),
            "type": candidate.get("type", ""),
            "priority": candidate.get("priority", 0),
            "scene_name": candidate.get("scene_name", ""),
            "timestamps": candidate.get("timestamps", []),
        }
        for candidate in unified_pool.values()
    ]
    if not pool_list:
        pool_section = "## Unified candidate pool\n\n(empty — no existing memories; store every new memory directly)"
    else:
        import json

        pool_section = f"## Unified candidate pool ({len(pool_list)} existing memories)\n\n{json.dumps(pool_list, ensure_ascii=False, indent=2)}"

    import json

    memory_parts: list[str] = []
    for index, item in enumerate(matches):
        record = item["record"]
        related_ids = per_memory_candidate_ids[record["record_id"]]
        related_note = json.dumps(related_ids) if related_ids else "[] (no similar candidates — store directly)"
        memory_text = json.dumps(
            {
                "record_id": record["record_id"],
                "content": record.get("content", ""),
                "type": record.get("type", ""),
                "priority": record.get("priority", 0),
                "scene_name": record.get("scene_name", ""),
            },
            ensure_ascii=False,
            indent=2,
        )
        memory_parts.append(f"### New memory #{index + 1} (record_id: {record['record_id']})\n{memory_text}\n\n【Related candidate ids】{related_note}")

    new_memories_text = "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n".join(memory_parts)

    return (
        "**Output language**: `merged_content` uses the same language as the memories in the candidate pool.\n\n"
        f"{pool_section}\n\n"
        f"{'═' * 50}\n\n"
        f"## New memories to judge ({len(matches)} total)\n\n"
        f"{new_memories_text}\n\n"
        "Judge each one and output the decision JSON array. When a new memory's candidate list is "
        "empty, output action=store for it directly."
    )


# ---------------------------------------------------------------------------
# Persona synthesis prompt (adapts persona-generation.ts)
# ---------------------------------------------------------------------------

PERSONA_SYSTEM_PROMPT = """You are a Persona Architect following an incremental evolution protocol.

You maintain a concise Markdown persona profile synthesized from the user's typed memories. The profile is injected into future conversations so the assistant can personalize its behavior.

## Constraints

1. Write the profile yourself as one complete Markdown document. Do not invent facts: every claim must be supported by the provided memories (or by the existing profile you were given).
2. Keep it SHORT: at most 2000 characters. Prefer dense prose paragraphs over bullet lists.
3. Distinguish stable from changing: identity, long-term preferences, and working style belong in the profile; one-off events and transient tasks do not.
4. Integrate rather than append: connect new information with what is already there (the connecting thread) — no bullet-point spam, no repetitive restatement.
5. Preserve details that still hold even when newer memories update them; remove or rewrite claims the new memories contradict.
6. Output ONLY the Markdown profile document — no preamble, no code fences.

## Structure

Use these sections (skip any section that would be empty):

# <Short profile title>
## Identity
## Preferences and working style
## Current focus
## Notes
"""


def build_persona_prompt(*, memories: list[dict], existing_profile: str | None, changed_note: str) -> tuple[str, str]:
    """Build (system, user) prompts for persona synthesis.

    ``memories`` are persona-type L1 records (dicts with ``content``,
    ``priority``, ``scene_name``, ``timestamps``). Mirrors the source's
    ``buildPersonaPrompt`` split of stable system prompt + data-carrying
    user prompt.
    """
    import json

    memory_text = json.dumps(
        [
            {
                "content": m.get("content", ""),
                "priority": m.get("priority", 0),
                "scene_name": m.get("scene_name", ""),
            }
            for m in memories
        ],
        ensure_ascii=False,
        indent=2,
    )
    existing = existing_profile.strip() if existing_profile else "(none yet — this is the first version)"
    user_prompt = (
        f"【Existing persona profile】:\n{existing}\n\n"
        f"【Memories to integrate】 ({len(memories)} persona memories):\n{memory_text}\n\n"
        f"【Change note】: {changed_note}\n\n"
        "Write the updated persona profile now (Markdown only, <= 2000 characters)."
    )
    return PERSONA_SYSTEM_PROMPT, user_prompt

__all__ = [
    "CONFLICT_DETECTION_SYSTEM_PROMPT",
    "EXTRACTION_SYSTEM_PROMPT",
    "PERSONA_SYSTEM_PROMPT",
    "WORK_CONFLICT_DETECTION_SYSTEM_PROMPT",
    "WORK_EXTRACTION_SYSTEM_PROMPT",
    "build_persona_prompt",
    "format_batch_conflict_prompt",
    "format_extraction_prompt",
    "get_conflict_system_prompt",
    "get_extraction_system_prompt",
]
