"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  agreeingMembers,
  evaluateWarRoom,
  fetchTriggerPolicy,
  fetchWarRoom,
  fetchWarRoomAnalytics,
  fetchWarRoomTranscript,
  fetchWarRooms,
  formatDuration,
  statusTone,
  summariseRun,
  taintedReceipts,
  unmetStages,
  TAINT_BADGE,
  VERIFICATION_BADGE,
  type Analytics,
  type MemberReceipt,
  type QuorumDecision,
  type RunListItem,
  type StageResult,
  type Transcript,
  type TriggerDecision,
  type TriggerPolicy,
  type Verification,
  type WarRoomRun,
} from "@/lib/war-room";
import { Section, EmptyState, ErrorBox, StatCard, Btn, Badge, SkeletonList, Field } from "@/components/ui";
import { errMsg } from "@/lib/http";
import { Activity, AlertTriangle, BarChart3, Bot, Clock, Layers, Scale, ShieldAlert, Users } from "lucide-react";

type SubTab = "runs" | "analytics" | "trigger";

/** Rendering order for the three surfaces. */
const SUBTABS: readonly SubTab[] = ["runs", "analytics", "trigger"];

/**
 * Operator-facing labels. The ids are route-ish tokens (`trigger`), which read
 * as raw identifiers in a button; the third one especially is not self-
 * explanatory as a bare word beside two nouns.
 */
const SUBTAB_LABELS: Record<SubTab, string> = {
  runs: "Runs",
  analytics: "Analytics",
  trigger: "Trigger Policy",
};

/**
 * The War Room: what the deliberation engine actually did.
 *
 * The layout follows the research finding that multi-agent UI fails when
 * everything is crammed into a transcript (Claude Code issue #24537): the run
 * list, the cross-run analytics and the trigger policy each get their own
 * surface, and only the selected run's evidence is expanded.
 *
 * Three things this screen refuses to do:
 *  - it never calls a run "verified". A quorum established that agents agreed on
 *    a claim, which is a weaker and different statement;
 *  - it never hides dissent. A run that passed while a member was isolated says
 *    so in the summary line, not just in a tooltip;
 *  - it never renders an empty list when a read failed.
 */
export function WarRoomRunsSection() {
  const [subTab, setSubTab] = useState<SubTab>("runs");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [runs, setRuns] = useState<RunListItem[]>([]);
  const [selected, setSelected] = useState<RunListItem | null>(null);
  const [detail, setDetail] = useState<WarRoomRun | null>(null);
  const [transcript, setTranscript] = useState<Transcript | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);

  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const [policy, setPolicy] = useState<TriggerPolicy | null>(null);
  const [probe, setProbe] = useState("");
  const [decision, setDecision] = useState<TriggerDecision | null>(null);
  const [probing, setProbing] = useState(false);

  const loadIndex = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setRuns(await fetchWarRooms());
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadIndex();
  }, [loadIndex]);

  useEffect(() => {
    if (subTab !== "analytics") return;
    fetchWarRoomAnalytics()
      .then(setAnalytics)
      .catch((e) => setError(errMsg(e)));
  }, [subTab]);

  useEffect(() => {
    if (subTab !== "trigger") return;
    fetchTriggerPolicy()
      .then(setPolicy)
      .catch((e) => setError(errMsg(e)));
  }, [subTab]);

  const openRun = useCallback(async (item: RunListItem) => {
    setSelected(item);
    setDetail(null);
    setTranscript(null);
    setDetailError(null);
    try {
      setDetail(await fetchWarRoom(item.run_id, item.room));
    } catch (e) {
      setDetailError(errMsg(e));
      return;
    }
    try {
      setTranscript(await fetchWarRoomTranscript(item.run_id, item.room));
    } catch {
      // The transcript is evidence, not the run. A failed transcript read must
      // not blank out a run that loaded fine, so it degrades on its own.
      setTranscript(null);
    }
  }, []);

  const runProbe = useCallback(async () => {
    if (!probe.trim()) return;
    setProbing(true);
    setDecision(null);
    try {
      setDecision(await evaluateWarRoom(probe.trim(), true));
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setProbing(false);
    }
  }, [probe]);

  return (
    <Section
      title="War Room"
      hint="Staged, clock-bounded group deliberation with a recorded quorum"
      actions={
        <div className="flex flex-wrap gap-1">
          {/* Every button used to render its raw lowercase id with no selected
              state, so three identical controls sat there and none of them said
              which surface was on screen. The selected one now fills, the rest
              outline, and `aria-current` carries the same fact for a reader
              that cannot see the colour. Refresh is an action rather than a
              view, so it stays outlined instead of competing with the tab. */}
          {SUBTABS.map((tab) => {
            const active = subTab === tab;
            return (
              <Btn
                key={tab}
                variant={active ? "primary" : "ghost"}
                ariaCurrent={active ? "page" : undefined}
                onClick={() => setSubTab(tab)}
                title={active ? `${SUBTAB_LABELS[tab]} — current view` : SUBTAB_LABELS[tab]}
              >
                {SUBTAB_LABELS[tab]}
              </Btn>
            );
          })}
          <Btn variant="ghost" onClick={loadIndex} title="Re-read the run list">
            Refresh
          </Btn>
        </div>
      }
    >
      {error && <ErrorBox message={error} onRetry={loadIndex} />}

      {subTab === "runs" && (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.6fr)]">
          <div className="space-y-2">
            <h3 className="flex items-center gap-1.5 text-sm font-semibold">
              <Layers className="size-3.5" /> Runs
            </h3>
            {loading ? (
              <SkeletonList rows={5} />
            ) : runs.length === 0 ? (
              <EmptyState
                title="No war room runs yet"
                hint="A room opens when a model calls the war_room tool, or when the auto-trigger decides one is warranted."
              />
            ) : (
              <ul className="space-y-1.5">
                {runs.map((item) => {
                  const active = selected?.run_id === item.run_id;
                  return (
                    <li key={`${item.room}/${item.run_id}`}>
                      <button
                        type="button"
                        onClick={() => void openRun(item)}
                        className={`w-full rounded-md border px-3 py-2 text-left ${
                          active ? "border-slate-400 bg-slate-50 dark:bg-slate-900" : "border-slate-200 hover:border-slate-300 dark:border-slate-800"
                        }`}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="truncate text-sm font-medium">{item.topic || item.run_id}</span>
                          <Badge tone={toneFor(item.status)}>{item.status}</Badge>
                        </div>
                        <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
                          <span className="inline-flex items-center gap-1">
                            <Clock className="size-3" />
                            {item.started_at || "no timestamp"}
                          </span>
                          {item.strategy && item.strategy !== "legacy_fixed" && <span>{item.strategy}</span>}
                          {item.tainted && (
                            <span className="inline-flex items-center gap-1 text-red-500">
                              <ShieldAlert className="size-3" /> tainted
                            </span>
                          )}
                          {item.verification && (
                            <span className={VERIFICATION_BADGE[item.verification]}>{item.verification}</span>
                          )}
                        </div>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          <div className="min-w-0">
            {!selected ? (
              <EmptyState title="Select a run" hint="Stage-by-stage evidence, quorum tallies, dissent and the transcript appear here." />
            ) : detailError ? (
              <ErrorBox message={detailError} onRetry={() => void openRun(selected)} />
            ) : !detail ? (
              <SkeletonList rows={6} />
            ) : (
              <RunDetail run={detail} transcript={transcript} />
            )}
          </div>
        </div>
      )}

      {subTab === "analytics" && <AnalyticsPanel analytics={analytics} />}
      {subTab === "trigger" && (
        <div className="space-y-4">
          <TriggerPolicyPanel policy={policy} />
          <div className="rounded-md border border-slate-200 p-3 dark:border-slate-800">
            <h3 className="mb-2 flex items-center gap-1.5 text-sm font-semibold">
              <Scale className="size-3.5" /> Would a room open on this?
            </h3>
            <p className="mb-2 text-[11px] text-slate-500">
              Read-only. Simulates the auto-trigger with it switched on, and changes no stored policy.
            </p>
            <Field label="Prompt">
              <div className="flex gap-2">
                <input
                  value={probe}
                  onChange={(e) => setProbe(e.target.value)}
                  placeholder="e.g. should we drop the prod table vs roll back"
                  className="flex-1 rounded-md border border-slate-300 bg-transparent px-2 py-1 text-sm dark:border-slate-700"
                />
                <Btn onClick={runProbe} disabled={probing || !probe.trim()}>
                  {probing ? "Evaluating..." : "Evaluate"}
                </Btn>
              </div>
            </Field>
            {decision && <DecisionCard decision={decision} />}
          </div>
        </div>
      )}
    </Section>
  );
}

// --- pieces ---------------------------------------------------------------

function toneFor(status: string): "green" | "amber" | "gray" | "red" {
  const tone = statusTone(status);
  if (tone === "ok") return "green";
  if (tone === "warn") return "amber";
  if (tone === "bad") return "red";
  return "gray";
}

function RunDetail({ run, transcript }: { run: WarRoomRun; transcript: Transcript | null }) {
  const unmet = unmetStages(run);
  const tainted = taintedReceipts(run);
  const agreeing = agreeingMembers(run);
  const dissentStages = Object.entries(run.minority_dissent ?? {});

  return (
    <div className="space-y-3">
      <div className="rounded-md border border-slate-200 p-3 dark:border-slate-800">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-semibold">{run.topic || run.run_id}</h3>
          <div className="flex items-center gap-1.5">
            <Badge tone={toneFor(run.status)}>{run.status}</Badge>
            {run.verification && <Badge tone="blue">{run.verification as Verification}</Badge>}
          </div>
        </div>
        <p className="mt-1 text-[11px] text-slate-500">{summariseRun(run)}</p>
        {run.strategy && run.strategy !== "legacy_fixed" && (
          <p className="mt-1 text-[11px] text-slate-500">
            <span className="font-medium">strategy:</span> {run.strategy}
            {run.strategy_rationale ? ` - ${run.strategy_rationale}` : ""}
          </p>
        )}
        {unmet.length > 0 && (
          <p className="mt-1 flex items-center gap-1 text-[11px] text-amber-600 dark:text-amber-400">
            <AlertTriangle className="size-3" /> quorum not met at: {unmet.join(", ")}
          </p>
        )}
        {run.failure_reason && <p className="mt-1 text-[11px] text-red-500">{run.failure_reason}</p>}
        {(run.transcript_errors?.length ?? 0) > 0 && (
          <p className="mt-1 text-[11px] text-amber-600">transcript faults recorded: {run.transcript_errors.length}</p>
        )}
        {(run.receipt_loss?.length ?? 0) > 0 && (
          <p className="mt-1 text-[11px] text-amber-600">receipts that could not be persisted: {run.receipt_loss.length}</p>
        )}
      </div>

      {run.synthesis && (
        <div className="rounded-md border border-slate-200 p-3 dark:border-slate-800">
          <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Synthesis</h4>
          <pre className="max-h-56 overflow-auto whitespace-pre-wrap text-xs">{run.synthesis}</pre>
        </div>
      )}

      {tainted.length > 0 && (
        <div className="rounded-md border border-red-300 bg-red-50 p-3 dark:border-red-900 dark:bg-red-950/30">
          <h4 className="mb-1 flex items-center gap-1.5 text-xs font-semibold text-red-600 dark:text-red-400">
            <ShieldAlert className="size-3.5" /> {tainted.length} contribution(s) matched an injection heuristic
          </h4>
          <p className="mb-2 text-[11px] text-red-600/80 dark:text-red-400/80">
            These were redacted before re-entering another participant&apos;s prompt. The run cannot report a clean
            success while one is unaddressed.
          </p>
          <ul className="space-y-1">
            {tainted.map((receipt) => (
              <li key={`${receipt.stage}/${receipt.participant}`} className="text-[11px]">
                <span className="font-medium">{receipt.participant}</span> @ {receipt.stage}:{" "}
                <span className={TAINT_BADGE[receipt.taint]}>{receipt.taint}</span>
                {receipt.taint_categories.length > 0 && (
                  <span className="ml-1 text-slate-500">({receipt.taint_categories.join(", ")})</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {dissentStages.length > 0 && (
        <div className="rounded-md border border-amber-300 bg-amber-50 p-3 dark:border-amber-900 dark:bg-amber-950/30">
          <h4 className="mb-1 text-xs font-semibold text-amber-700 dark:text-amber-400">Preserved minority views</h4>
          <p className="mb-2 text-[11px] text-amber-700/80 dark:text-amber-400/80">
            Non-agreeing positions are kept verbatim. A consensus number with the objection deleted is a quieter lie
            than no number at all.
          </p>
          <div className="space-y-2">
            {dissentStages.map(([stage, members]) => (
              <div key={stage}>
                <div className="text-[11px] font-medium">{stage}</div>
                {Object.entries(members).map(([member, text]) => (
                  <pre key={member} className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap text-[11px] text-slate-600 dark:text-slate-300">
                    {`@${member}: ${text}`}
                  </pre>
                ))}
              </div>
            ))}
          </div>
        </div>
      )}

      {agreeing.length > 0 && (
        <p className="flex items-center gap-1.5 text-[11px] text-slate-500">
          <Users className="size-3" /> agreed at least once: {agreeing.join(", ")}
        </p>
      )}

      <div className="space-y-2">
        {(run.stages ?? []).map((stage) => (
          <StageCard key={stage.name} stage={stage} />
        ))}
      </div>

      <div className="rounded-md border border-slate-200 p-3 dark:border-slate-800">
        <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Transcript</h4>
        {!transcript ? (
          <p className="text-[11px] text-slate-500">No transcript available for this run.</p>
        ) : (
          <>
            <p className="mb-2 text-[11px] text-slate-500">
              {transcript.count} message(s)
              {transcript.gap_free ? " - sequence verified" : " - SEQUENCE GAP DETECTED"}
            </p>
            <ol className="max-h-72 space-y-1 overflow-auto">
              {transcript.messages.map((message, index) => (
                <li key={message.seq ?? index} className="text-[11px]">
                  <span className="text-slate-400">#{message.seq ?? index + 1}</span>{" "}
                  <span className="font-medium">{message.author ?? "?"}</span>{" "}
                  <span className="text-slate-400">[{message.kind ?? "?"}]</span>
                  <pre className="whitespace-pre-wrap text-slate-600 dark:text-slate-300">
                    {message.body ?? message.unparsed ?? ""}
                  </pre>
                </li>
              ))}
            </ol>
          </>
        )}
      </div>
    </div>
  );
}

function StageCard({ stage }: { stage: StageResult }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-md border border-slate-200 dark:border-slate-800">
      <button type="button" onClick={() => setOpen((v) => !v)} className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left">
        <span className="flex items-center gap-2 text-sm font-medium">
          <Activity className="size-3.5" /> {stage.name}
        </span>
        <span className="flex items-center gap-1.5">
          {stage.quorum && (
            <Badge tone={stage.quorum.passed ? "green" : "amber"}>
              {stage.quorum.agree}/{stage.quorum.required_votes}
            </Badge>
          )}
          <Badge tone={toneFor(stage.status)}>{stage.status}</Badge>
        </span>
      </button>
      {open && (
        <div className="space-y-2 border-t border-slate-200 px-3 py-2 dark:border-slate-800">
          {stage.quorum && <QuorumCard quorum={stage.quorum} />}
          {stage.error && <p className="text-[11px] text-red-500">{stage.error}</p>}
          <ul className="space-y-1.5">
            {(stage.receipts ?? []).map((receipt) => (
              <ReceiptRow key={`${receipt.participant}-${receipt.seq}`} receipt={receipt} />
            ))}
          </ul>
          {stage.synthesis && (
            <pre className="max-h-40 overflow-auto whitespace-pre-wrap text-[11px]">{stage.synthesis}</pre>
          )}
        </div>
      )}
    </div>
  );
}

function QuorumCard({ quorum }: { quorum: QuorumDecision }) {
  const c = quorum.consensus;
  return (
    <div className="rounded border border-slate-200 p-2 text-[11px] dark:border-slate-800">
      <div className="mb-1 font-medium">
        policy {quorum.policy}, {quorum.agree} of {quorum.required_votes} required
        {quorum.engine_agrees_with_policy ? "" : " - ENGINE DISAGREES WITH POLICY"}
      </div>
      {c && (
        <>
          <div className="flex flex-wrap gap-3">
            <span className="text-emerald-600 dark:text-emerald-400">agreeing: {c.agreeing.join(", ") || "-"}</span>
            <span className="text-amber-600 dark:text-amber-400">dissenting: {c.dissenting.join(", ") || "-"}</span>
            <span className="text-slate-500">isolated: {c.isolated.join(", ") || "-"}</span>
            <span className="text-slate-400">unparsed: {c.unparsed.join(", ") || "-"}</span>
          </div>
          {c.agreed_claims.length > 0 && (
            <p className="mt-1 text-slate-500">agreed claims: {c.agreed_claims.join(" | ")}</p>
          )}
          {c.correlation_adjusted && (
            <p className="mt-1 text-slate-500">
              correlated voters collapsed:{" "}
              {Object.entries(c.correlated_groups)
                .map(([model, members]) => `${model} (${members.join("+")})`)
                .join(", ")}
            </p>
          )}
        </>
      )}
      {quorum.collusion?.flagged && (
        <p className="mt-1 text-amber-600 dark:text-amber-400">
          collusion signal: {quorum.collusion.reasons.join("; ")}
        </p>
      )}
    </div>
  );
}

function ReceiptRow({ receipt }: { receipt: MemberReceipt }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="rounded border border-slate-200 dark:border-slate-800">
      <button type="button" onClick={() => setOpen((v) => !v)} className="flex w-full items-center justify-between gap-2 px-2 py-1.5 text-left">
        <span className="flex items-center gap-1.5 text-[11px]">
          <Bot className="size-3" /> {receipt.participant}
          {receipt.taint !== "clean" && <span className={TAINT_BADGE[receipt.taint]}>{receipt.taint}</span>}
        </span>
        <span className="flex items-center gap-1.5">
          <span className="text-[10px] text-slate-400">{formatDuration(receipt.duration_ms)}</span>
          <Badge tone={receipt.status === "contributed" ? "green" : receipt.status === "failed" || receipt.status === "timeout" ? "red" : "amber"}>
            {receipt.status}
          </Badge>
        </span>
      </button>
      {open && (
        <div className="px-2 pb-2">
          {receipt.claims.length > 0 && (
            <p className="mb-1 text-[10px] text-slate-500">
              claims: {receipt.claims.join(" | ")}
              {receipt.self_confidence != null && ` (self-reported ${receipt.self_confidence})`}
            </p>
          )}
          <pre className="max-h-40 overflow-auto whitespace-pre-wrap text-[11px]">{receipt.output || receipt.error}</pre>
        </div>
      )}
    </li>
  );
}

function AnalyticsPanel({ analytics }: { analytics: Analytics | null }) {
  if (!analytics) return <SkeletonList rows={4} />;
  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Runs" value={String(analytics.runs)} sub={`${analytics.unreadable} unreadable`} />
        <StatCard label="Tainted runs" value={String(analytics.tainted_runs)} sub="screened contributions flagged" />
        <StatCard label="Stages with dissent" value={String(analytics.stages_with_dissent)} sub="minority views preserved" />
        <StatCard
          label="Duration p95"
          value={formatDuration(analytics.duration_seconds.p95 * 1000)}
          sub={`p50 ${formatDuration(analytics.duration_seconds.p50 * 1000)}`}
        />
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <Distribution title="By status" data={analytics.by_status} />
        <Distribution title="By strategy" data={analytics.by_strategy} />
      </div>
    </div>
  );
}

function Distribution({ title, data }: { title: string; data: Record<string, number> }) {
  const entries = Object.entries(data ?? {}).sort((a, b) => b[1] - a[1]);
  const total = entries.reduce((sum, [, count]) => sum + count, 0) || 1;
  return (
    <div className="rounded-md border border-slate-200 p-3 dark:border-slate-800">
      <h4 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
        <BarChart3 className="size-3.5" /> {title}
      </h4>
      {entries.length === 0 ? (
        <p className="text-[11px] text-slate-500">No runs recorded yet.</p>
      ) : (
        <ul className="space-y-1.5">
          {entries.map(([label, count]) => (
            <li key={label} className="text-[11px]">
              <div className="flex justify-between">
                <span>{label}</span>
                <span className="text-slate-500">{count}</span>
              </div>
              <div className="mt-0.5 h-1.5 rounded bg-slate-200 dark:bg-slate-800">
                <div className="h-1.5 rounded bg-slate-400" style={{ width: `${Math.round((count / total) * 100)}%` }} />
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function TriggerPolicyPanel({ policy }: { policy: TriggerPolicy | null }) {
  if (!policy) return <SkeletonList rows={3} />;
  return (
    <div className="rounded-md border border-slate-200 p-3 dark:border-slate-800">
      <h3 className="mb-2 text-sm font-semibold">Auto-trigger policy</h3>
      <p className="mb-2 text-[11px] text-slate-500">
        Default off. Auto-opening a panel on every turn is worse than never opening one, so the thresholds are
        deliberately conservative and every refusal carries a named reason.
      </p>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        <StatCard label="Enabled" value={policy.enabled ? "yes" : "no"} />
        <StatCard label="Min risk" value={policy.min_risk} />
        <StatCard label="Min difficulty" value={policy.min_difficulty} />
        <StatCard label="Cooldown" value={`${policy.cooldown_seconds}s`} />
        <StatCard label="Duplicate window" value={`${policy.duplicate_window_seconds}s`} />
        <StatCard label="Rooms per turn" value={String(policy.max_rooms_per_turn)} />
      </div>
      {policy.human_only_strategies.length > 0 && (
        <p className="mt-2 text-[11px] text-slate-500">human-only strategies: {policy.human_only_strategies.join(", ")}</p>
      )}
    </div>
  );
}

function DecisionCard({ decision }: { decision: TriggerDecision }) {
  return (
    <div
      className={`mt-3 rounded-md border p-3 text-[11px] ${
        decision.open_room ? "border-emerald-300 bg-emerald-50 dark:border-emerald-900 dark:bg-emerald-950/30" : "border-slate-300 dark:border-slate-700"
      }`}
    >
      <div className="mb-1 flex items-center gap-2">
        <Badge tone={decision.open_room ? "green" : "gray"}>{decision.open_room ? "would open" : "would not open"}</Badge>
        <span className="font-medium">gate: {decision.gate}</span>
      </div>
      <p className="text-slate-600 dark:text-slate-300">{decision.rationale}</p>
      <p className="mt-1 text-slate-500">
        difficulty {decision.difficulty}, risk {decision.risk}, strategy {decision.strategy}
        {decision.needs_confirmation && ", human confirmation required"}
        {decision.duplicate_of && `, reusing "${decision.duplicate_of}"`}
      </p>
      {decision.roster_models.length > 0 && (
        <p className="mt-1 text-slate-500">roster: {decision.roster_models.join(", ")}</p>
      )}
    </div>
  );
}
