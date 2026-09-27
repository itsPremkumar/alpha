/**
 * War Room domain model and the honesty rules that decide what a reader is told.
 *
 * This module imports NOTHING on purpose. The rules below are the ones that stop
 * a screen from overstating a deliberation, so they are kept pure and separately
 * testable without a bundler or a network.
 *
 * The central rule: **agreement is not correctness, and a completed run is not a
 * verified one.** A quorum establishes that some number of agents stated a
 * shared claim. It says nothing about whether that claim is true, and the
 * vocabulary here never pretends otherwise.
 */

export type Taint = "clean" | "suspect" | "infected";

/** Never "verified": agreement is not correctness. */
export type Verification = "consensus_supported" | "consensus_degraded" | "tainted" | "unverified";

export type RunStatus = "pending" | "running" | "succeeded" | "partial" | "failed" | "timeout" | "cancelled";

export interface Consensus {
  agreeing: string[];
  dissenting: string[];
  /** Members nobody else's position touches: not agreement, not opposition. */
  isolated: string[];
  /** Members that stated nothing parseable. Excluded from every side. */
  unparsed: string[];
  agreed_claims: string[];
  dissent_text: Record<string, string>;
  correlated_groups: Record<string, string[]>;
  correlation_adjusted: boolean;
  agreement_ratio: number;
}

export interface CollusionSignal {
  flagged: boolean;
  similarity: number;
  reasons: string[];
  redundant_members: string[];
}

export interface TaintFinding {
  stage: string;
  participant: string;
  taint: Taint;
  categories: string[];
}

export interface QuorumDecision {
  stage: string;
  policy: string;
  required_votes: number;
  eligible_voters: number;
  agree: number;
  disagree: number;
  amend: number;
  total_votes: number;
  passed: boolean;
  proposal_id: string;
  engine_status: string;
  engine_agrees_with_policy: boolean;
  consensus: Consensus;
  collusion: CollusionSignal;
  taint_findings: TaintFinding[];
  human_line: string;
}

export interface MemberReceipt {
  stage: string;
  participant: string;
  status: string;
  output: string;
  error: string;
  error_type: string;
  duration_ms: number;
  seq: number;
  persistence_error: string;
  claims: string[];
  self_confidence: number | null;
  taint: Taint;
  taint_categories: string[];
  model_id: string;
  tainted: boolean;
}

export interface StageResult {
  name: string;
  status: string;
  budget: Record<string, number>;
  receipts: MemberReceipt[];
  quorum: QuorumDecision | null;
  synthesis: string;
  error: string;
  started_at: string;
  finished_at: string;
}

export interface WarRoomRun {
  run_id: string;
  room: string;
  status: RunStatus | "unreadable";
  strategy: string;
  strategy_rationale: string;
  topic?: string;
  synthesis: string | null;
  error: string;
  failure_reason: string;
  started_at: string;
  finished_at: string;
  killed_by: string;
  stages: StageResult[];
  minority_dissent: Record<string, Record<string, string>>;
  taint_findings: TaintFinding[];
  tainted: boolean;
  final_quorum: QuorumDecision | null;
  transcript_errors: string[];
  receipt_loss: string[];
  verification?: Verification;
  error_detail?: string;
}

export interface RunListItem {
  run_id: string;
  room: string;
  status: RunStatus | "unreadable";
  strategy?: string;
  started_at?: string;
  finished_at?: string;
  tainted?: boolean;
  verification?: Verification;
  error?: string;
  topic?: string;
}

export interface TranscriptMessage {
  seq?: number;
  author?: string;
  body?: string;
  kind?: string;
  created_at?: string;
  metadata?: Record<string, unknown>;
  unparsed?: string;
}

export interface Transcript {
  run_id: string;
  room: string;
  count: number;
  /** False when the sequence check failed. Surfaced, never assumed true. */
  gap_free: boolean;
  messages: TranscriptMessage[];
}

export interface TriggerPolicy {
  enabled: boolean;
  require_interactive: boolean;
  min_risk: string;
  min_difficulty: string;
  open_on_contested: boolean;
  cooldown_seconds: number;
  duplicate_window_seconds: number;
  max_rooms_per_turn: number;
  human_only_strategies: string[];
  require_confirmation: boolean;
}

export interface TriggerDecision {
  open_room: boolean;
  strategy: string;
  difficulty: string;
  risk: string;
  gate: string;
  rationale: string;
  duplicate_of: string;
  needs_confirmation: boolean;
  roster_models: string[];
  blockers: string[];
}

export interface Analytics {
  runs: number;
  unreadable: number;
  by_status: Record<string, number>;
  by_strategy: Record<string, number>;
  tainted_runs: number;
  stages_with_dissent: number;
  total_agreeing_members: number;
  duration_seconds: { count: number; p50: number; p95: number; max: number };
}

// --- presentation helpers -------------------------------------------------

export const TAINT_BADGE: Record<Taint, string> = {
  clean: "text-slate-400",
  suspect: "text-amber-500",
  infected: "text-red-500",
};

export const VERIFICATION_BADGE: Record<Verification, string> = {
  consensus_supported: "text-emerald-500",
  consensus_degraded: "text-amber-500",
  tainted: "text-red-500",
  unverified: "text-slate-400",
};

export function statusTone(status: string): "ok" | "warn" | "bad" | "idle" {
  switch (status) {
    case "succeeded":
      return "ok";
    case "partial":
      return "warn";
    case "failed":
    case "timeout":
      return "bad";
    default:
      return "idle";
  }
}

/** Every stage whose quorum was not met. Empty means the room decided cleanly. */
export function unmetStages(run: WarRoomRun): string[] {
  return (run.stages ?? [])
    .filter((stage) => stage.quorum && !stage.quorum.passed)
    .map((stage) => stage.name);
}

/** Every receipt the taint screen flagged, across all stages. */
export function taintedReceipts(run: WarRoomRun): MemberReceipt[] {
  return (run.stages ?? []).flatMap((stage) => stage.receipts.filter((receipt) => receipt.tainted));
}

/** Members that agreed anywhere in the run, de-duplicated and sorted. */
export function agreeingMembers(run: WarRoomRun): string[] {
  const names = new Set<string>();
  for (const stage of run.stages ?? []) {
    for (const member of stage.quorum?.consensus?.agreeing ?? []) names.add(member);
  }
  return [...names].sort();
}

/** Every preserved non-agreeing view, as flat `stage -> member -> text`. */
export function dissentEntries(run: WarRoomRun): Array<{ stage: string; member: string; text: string }> {
  return Object.entries(run.minority_dissent ?? {}).flatMap(([stage, members]) =>
    Object.entries(members).map(([member, text]) => ({ stage, member, text })),
  );
}

/**
 * A one-line summary that refuses to overstate the run.
 *
 * "agreed" appears only alongside the required threshold, and dissent/isolation
 * counts are always shown when they are non-zero - "2 agreed" out of five is the
 * number a reader most needs and least expects.
 */
export function summariseRun(run: WarRoomRun): string {
  if (run.status === "unreadable") return `unreadable record: ${run.error ?? "parse failed"}`;
  const stages = run.stages ?? [];
  const lastQuorum = [...stages].reverse().find((stage) => stage.quorum)?.quorum ?? run.final_quorum;
  const strategy = run.strategy && run.strategy !== "legacy_fixed" ? run.strategy : "fixed 3-stage";
  if (!lastQuorum) return `${run.status} - no quorum recorded (${strategy})`;

  const consensus = lastQuorum.consensus;
  const agree = consensus?.agreeing?.length ?? 0;
  const dissenting = consensus?.dissenting?.length ?? 0;
  const isolated = consensus?.isolated?.length ?? 0;
  const unparsed = consensus?.unparsed?.length ?? 0;

  const parts = [`${agree}/${lastQuorum.required_votes} agreed`];
  if (dissenting) parts.push(`${dissenting} dissenting`);
  if (isolated) parts.push(`${isolated} isolated`);
  if (unparsed) parts.push(`${unparsed} unparsed`);
  if (run.tainted) parts.push("tainted");
  return `${run.status} - ${parts.join(", ")} (${strategy})`;
}

/** Milliseconds to a short human duration. */
export function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "-";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const minutes = Math.floor(ms / 60_000);
  return `${minutes}m ${Math.round((ms % 60_000) / 1000)}s`;
}
