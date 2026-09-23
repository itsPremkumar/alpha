import { get, send, asList, pick } from "./http";

export interface MemoryFact {
  id: string;
  content: string;
  [key: string]: unknown;
}

export interface MemoryData {
  facts: MemoryFact[];
  summary: string;
  raw: Record<string, unknown>;
}

export interface WorkingMemoryItem {
  item_id: string;
  content: string;
  context_tag: string;
  attention_score: number;
  salience: number;
  created_at: number;
  task_id: string;
}

export interface EpisodicTraceItem {
  trace_id: string;
  action: string;
  observation: string;
  outcome: string;
  error_context?: string | null;
  salience: number;
  timestamp: number;
  tags?: string[];
}

export interface SemanticNodeItem {
  node_id: string;
  subject: string;
  predicate: string;
  object_val: string;
  statement: string;
  confidence: number;
  status: "active" | "contested" | "superseded" | "deprecated";
  salience: number;
  revision: number;
  access_count: number;
  evidence?: string[];
  tags?: string[];
}

export interface ProceduralSkillItem {
  skill_id: string;
  name: string;
  description: string;
  trigger_pattern: string;
  steps: string[];
  code_snippet?: string;
  success_count: number;
  failure_count: number;
  success_rate: number;
}

export interface CognitiveOverview {
  tiers: {
    working_memory: { count: number; active_high_attention: number };
    episodic_memory: { flat_traces_count: number; hierarchical_episodes_count: number };
    semantic_graph: {
      total_nodes: number;
      total_edges: number;
      connected_nodes: number;
      active_beliefs: number;
      contested_beliefs: number;
      superseded_beliefs: number;
      deprecated_beliefs: number;
      density: number;
    };
    procedural_memory: { skills_count: number };
    spatio_temporal: { events_count: number };
    associative_network: { links_count: number };
  };
  consolidation: {
    latest_report?: {
      cycle_id: string;
      timestamp: number;
      light_sleep_pruned: number;
      rem_sleep_patterns_discovered: number;
      deep_sleep_beliefs_crystallized: number;
      conflicts_reconciled: number;
      skills_indexed: number;
      decayed_items_count: number;
      summary: string;
    } | null;
    total_cycles: number;
  };
}

export interface ScoredMemoryItem {
  tier: string;
  item_id: string;
  title: string;
  snippet: string;
  composite_score: number;
  bm25_score: number;
  vector_score: number;
  graph_score: number;
  temporal_score: number;
  salience_score: number;
  metadata?: Record<string, unknown>;
}

export interface RecallQueryOptions {
  query: string;
  limit?: number;
  bm25_weight?: number;
  vector_weight?: number;
  temporal_weight?: number;
  graph_weight?: number;
  tier_filter?: string[];
}

export async function fetchMemory(): Promise<MemoryData> {
  const d = await get<Record<string, unknown>>("/memory");
  return {
    facts: asList(d.facts, []).map((f, i) => ({
      ...(f as Record<string, unknown>),
      id: String(pick(f, ["id", "fact_id"], `fact-${i}`)),
      content: String(pick(f, ["content", "text", "fact"], "")),
    })),
    summary: String(pick(d, ["summary", "summary_text"], "")),
    raw: d,
  };
}

export async function reloadMemory(): Promise<MemoryData> {
  await send("/memory/reload", "POST", {});
  return fetchMemory();
}

export async function clearMemory(): Promise<void> {
  await send("/memory", "DELETE");
}

export async function addFact(content: string): Promise<void> {
  await send("/memory/facts", "POST", { content });
}

export async function deleteFact(factId: string): Promise<void> {
  await send(`/memory/facts/${encodeURIComponent(factId)}`, "DELETE");
}

export async function updateFact(factId: string, content: string): Promise<void> {
  await send(`/memory/facts/${encodeURIComponent(factId)}`, "PATCH", { content });
}

export async function memoryStatus(): Promise<Record<string, unknown> | null> {
  try {
    return await get<Record<string, unknown>>("/memory/status");
  } catch {
    return null;
  }
}

// ============================================================================
// Multi-Tier Cognitive Memory API
// ============================================================================

export async function fetchCognitiveOverview(): Promise<CognitiveOverview> {
  return await get<CognitiveOverview>("/memory/cognitive/overview");
}

export async function recallCognitiveMemory(opts: RecallQueryOptions): Promise<ScoredMemoryItem[]> {
  return await send<ScoredMemoryItem[]>("/memory/cognitive/recall", "POST", opts);
}

export async function fetchWorkingMemory(): Promise<WorkingMemoryItem[]> {
  return await get<WorkingMemoryItem[]>("/memory/cognitive/working");
}

export async function addWorkingMemory(content: string, context_tag = "scratch"): Promise<WorkingMemoryItem> {
  return await send<WorkingMemoryItem>("/memory/cognitive/working", "POST", { content, context_tag });
}

export async function clearWorkingMemory(): Promise<void> {
  await send("/memory/cognitive/working", "DELETE");
}

export async function fetchEpisodicMemory(mode: "trace" | "episode" = "trace"): Promise<EpisodicTraceItem[]> {
  return await get<EpisodicTraceItem[]>(`/memory/cognitive/episodic?mode=${mode}`);
}

export async function recordEpisodicTrace(action: string, observation: string, outcome = "success"): Promise<EpisodicTraceItem> {
  return await send<EpisodicTraceItem>("/memory/cognitive/episodic", "POST", { action, observation, outcome });
}

export async function fetchSemanticGraph(): Promise<{
  metrics: CognitiveOverview["tiers"]["semantic_graph"];
  nodes: SemanticNodeItem[];
  edges: Array<{ edge_id: string; source_id: string; target_id: string; relation: string; weight: number }>;
}> {
  return await get<{
    metrics: CognitiveOverview["tiers"]["semantic_graph"];
    nodes: SemanticNodeItem[];
    edges: Array<{ edge_id: string; source_id: string; target_id: string; relation: string; weight: number }>;
  }>("/memory/cognitive/semantic");
}

// Default is the neutral unverified baseline (0.5): a belief the caller does not
// score has no evidence behind it yet, so we never seed a plausible-looking
// fabricated confidence (was 0.85) that the UI would render as a measured one.
export async function addSemanticBelief(subject: string, predicate: string, object_val: string, confidence = 0.5): Promise<SemanticNodeItem> {
  return await send<SemanticNodeItem>("/memory/cognitive/semantic", "POST", { subject, predicate, object_val, confidence });
}

export async function fetchProceduralSkills(): Promise<ProceduralSkillItem[]> {
  return await get<ProceduralSkillItem[]>("/memory/cognitive/procedural");
}

export async function registerProceduralSkill(
  name: string,
  description: string,
  trigger_pattern: string,
  steps: string[]
): Promise<ProceduralSkillItem> {
  return await send<ProceduralSkillItem>("/memory/cognitive/procedural", "POST", { name, description, trigger_pattern, steps });
}

export async function triggerConsolidation(): Promise<CognitiveOverview["consolidation"]["latest_report"]> {
  return await send<CognitiveOverview["consolidation"]["latest_report"]>("/memory/cognitive/consolidate", "POST", {});
}

export async function triggerBeliefReconciliation(): Promise<{ reconciled_count: number; conflicts_detected_count: number; details: Array<{ n1: string; n2: string; reason: string }> }> {
  return await send<{ reconciled_count: number; conflicts_detected_count: number; details: Array<{ n1: string; n2: string; reason: string }> }>("/memory/cognitive/reconcile", "POST", {});
}

export async function deleteSemanticBelief(nodeId: string): Promise<void> {
  await send(`/memory/cognitive/semantic/${encodeURIComponent(nodeId)}`, "DELETE");
}

export async function deleteProceduralSkill(skillId: string): Promise<void> {
  await send(`/memory/cognitive/procedural/${encodeURIComponent(skillId)}`, "DELETE");
}

export async function deleteEpisodicTrace(traceId: string): Promise<void> {
  await send(`/memory/cognitive/episodic/${encodeURIComponent(traceId)}`, "DELETE");
}

