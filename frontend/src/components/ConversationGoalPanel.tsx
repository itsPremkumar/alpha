"use client";

import React, { useEffect, useRef, useState } from "react";
import { Clock3, Edit3, PauseCircle, Target, X } from "lucide-react";
import type { ThreadGoal } from "@/lib/threads-ext";

interface ConversationGoalPanelProps {
  threadId: string | null;
  goal: string | null;
  details: ThreadGoal | null;
  detailsOpen: boolean;
  readError: string | null;
  saveError: string | null;
  editing: boolean;
  draft: string;
  onDraftChange: (value: string) => void;
  onEditingChange: (value: boolean) => void;
  onDetailsOpenChange: (value: boolean) => void;
  onSaveErrorClear: () => void;
  onSave: () => void;
  onClear: () => void;
}

function updatedLabel(value: string | null | undefined): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return null;
  return `Updated ${new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date)}`;
}

function formattedTimestamp(value: string | null | undefined): string {
  if (!value) return "Not reported";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "Not reported";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function goalStatus(details: ThreadGoal | null): string {
  return details?.status || "status not reported";
}

function elapsedLabel(createdAt: string | null | undefined, now: number | null): string | null {
  if (!createdAt || now === null) return null;
  const created = new Date(createdAt).getTime();
  if (!Number.isFinite(created) || created > now) return null;
  const minutes = Math.floor((now - created) / 60_000);
  const days = Math.floor(minutes / 1_440);
  if (days >= 365) return `${Math.floor(days / 365)}y ${Math.floor((days % 365) / 30)}mo elapsed`;
  if (days > 0) return `${days}d ${Math.floor((minutes % 1_440) / 60)}h elapsed`;
  if (minutes >= 60) return `${Math.floor(minutes / 60)}h ${minutes % 60}m elapsed`;
  return `${minutes}m elapsed`;
}

export function ConversationGoalPanel({
  threadId,
  goal,
  details,
  detailsOpen,
  readError,
  saveError,
  editing,
  draft,
  onDraftChange,
  onEditingChange,
  onDetailsOpenChange,
  onSaveErrorClear,
  onSave,
  onClear,
}: ConversationGoalPanelProps) {
  const open = detailsOpen || editing;
  const dialogRef = useRef<HTMLElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const [now, setNow] = useState<number | null>(null);
  const age = elapsedLabel(details?.created_at, now);

  useEffect(() => {
    if (!details?.created_at) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => window.clearInterval(timer);
  }, [details?.created_at]);

  useEffect(() => {
    if (!open) return;
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const focusFrame = window.requestAnimationFrame(() => {
      const preferred = dialogRef.current?.querySelector<HTMLElement>("[data-goal-autofocus]");
      (preferred || dialogRef.current?.querySelector<HTMLElement>("button, textarea, input, [tabindex='0']"))?.focus();
    });
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { onEditingChange(false); onDetailsOpenChange(false); }
      if (event.key === "Tab" && dialogRef.current) {
        const controls = Array.from(dialogRef.current.querySelectorAll<HTMLElement>("button:not([disabled]), textarea:not([disabled]), input:not([disabled]), [tabindex='0']"));
        if (!controls.length) return;
        const first = controls[0];
        const last = controls[controls.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.cancelAnimationFrame(focusFrame);
      if (previouslyFocused?.isConnected) previouslyFocused.focus();
    };
    // Setter identities are stable; the dialog lifecycle is keyed only by its open state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, editing]);

  if (!threadId) return null;

  const timestamp = updatedLabel(details?.updated_at);
  const completed = details?.status === "completed";

  return (
    <div className="shrink-0 px-4 pt-2" data-conversation-goal>
      <div className="max-w-4xl mx-auto">
        {goal ? (
          <div className="flex min-w-0 items-center gap-2 rounded-xl border border-primary/25 bg-card/95 px-3 py-2 shadow-sm">
            <Target className="size-4 shrink-0 text-primary" aria-hidden="true" />
            <button
              ref={triggerRef}
              type="button"
              onClick={() => onDetailsOpenChange(true)}
              aria-haspopup="dialog"
              aria-label={`Open goal details: ${goal}`}
              className="min-w-0 flex-1 text-left"
            >
              <span className="grid min-w-0 flex-1 gap-1">
                <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5">
                  <span className="shrink-0 text-[11px] font-semibold text-primary">
                    {completed ? "Goal completed" : "Pursuing goal"}
                  </span>
                  <span className="min-w-0 flex-1 whitespace-normal break-words text-xs leading-relaxed text-foreground [overflow-wrap:anywhere]" title={goal}>{goal}</span>
                </span>
                <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5">
                {details?.status ? (
                  <span className="shrink-0 rounded-full border border-border/60 px-1.5 py-0.5 text-[10px] text-muted-foreground">
                    {details.status}
                  </span>
                ) : null}
                {age ? <span className="hidden shrink-0 text-[10px] text-muted-foreground md:inline" title={details?.created_at || undefined}>{age}</span> : null}
                {timestamp ? (
                  <span className="hidden shrink-0 items-center gap-1 text-[10px] text-muted-foreground sm:inline-flex">
                    <Clock3 className="size-3" aria-hidden="true" />{timestamp}
                  </span>
                ) : null}
                </span>
              </span>
            </button>
            <button
              type="button"
              onClick={() => { onDraftChange(goal); onSaveErrorClear(); onDetailsOpenChange(true); onEditingChange(true); }}
              className="inline-flex shrink-0 items-center gap-1 rounded-lg px-2 py-1 text-[11px] font-medium text-muted-foreground hover:bg-muted hover:text-foreground"
              aria-label="Edit conversation goal"
            >
              <Edit3 className="size-3" aria-hidden="true" /> Edit
            </button>
          </div>
        ) : (
          <button
            ref={triggerRef}
            type="button"
            onClick={() => { onDraftChange(""); onEditingChange(true); }}
            className="inline-flex items-center gap-1.5 rounded-lg px-1 py-1 text-[11px] text-muted-foreground hover:text-foreground"
          >
            <Target className="size-3.5" aria-hidden="true" /> Set a goal for this conversation
          </button>
        )}

        {readError ? (
          <p role="status" className="mt-1 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-1.5 text-[11px] text-destructive">
            Live goal status is unavailable: {readError}. Any displayed goal text is a saved local copy; Gateway status is unknown.
          </p>
        ) : null}

        {open ? (
          <div
            className="fixed inset-0 z-[90] flex items-end justify-center bg-black/45 p-3 sm:items-center sm:p-6"
            onMouseDown={(event) => { if (event.target === event.currentTarget) { onEditingChange(false); onDetailsOpenChange(false); } }}
          >
            <section
              ref={dialogRef}
              role="dialog"
              aria-modal="true"
              aria-labelledby="conversation-goal-title"
              className="max-h-[min(86vh,48rem)] w-full max-w-2xl overflow-y-auto rounded-2xl border border-border/70 bg-background shadow-2xl"
            >
              <header className="sticky top-0 z-10 flex items-center gap-3 border-b border-border/60 bg-background/95 px-5 py-4 backdrop-blur">
                <span className="flex size-9 items-center justify-center rounded-xl bg-primary/10 text-primary"><Target className="size-4" aria-hidden="true" /></span>
                <div className="min-w-0 flex-1">
                  <h2 id="conversation-goal-title" className="text-sm font-semibold">Conversation goal</h2>
                  <p className="text-[11px] text-muted-foreground">Saved with this conversation</p>
                </div>
                <button type="button" onClick={() => { onEditingChange(false); onDetailsOpenChange(false); }} className="rounded-lg p-2 text-muted-foreground hover:bg-muted hover:text-foreground" aria-label="Close goal details">
                  <X className="size-4" aria-hidden="true" />
                </button>
              </header>

              <div className="space-y-5 p-5">
                {editing ? (
                  <div className="space-y-3">
                    <label htmlFor="conversation-goal-draft" className="block text-xs font-medium">What should Alpha accomplish?</label>
                    <textarea
                      id="conversation-goal-draft"
                      value={draft}
                      onChange={(event) => onDraftChange(event.target.value)}
                      maxLength={4000}
                      rows={5}
                      data-goal-autofocus
                      placeholder="Describe the outcome and how you will know it is complete."
                      className="w-full resize-y rounded-xl border border-border bg-card px-3 py-2.5 text-sm leading-relaxed focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                    />
                    {saveError ? (
                      <p role="alert" className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
                        Couldn’t save the goal: {saveError}
                        {saveError.toLowerCase().includes("run in flight") ? " Your draft is still here. Retry after the current run finishes." : " Your draft is still here; correct the issue or retry."}
                      </p>
                    ) : null}
                    <div className="flex items-center justify-between gap-3 text-[11px] text-muted-foreground">
                      <span>Up to 4,000 characters</span><span>{draft.length}/4000</span>
                    </div>
                    <div className="flex justify-end gap-2">
                      <button type="button" onClick={() => { onEditingChange(false); onDetailsOpenChange(false); }} className="rounded-lg border border-border px-3 py-2 text-xs hover:bg-muted">Cancel</button>
                      <button type="button" onClick={onSave} disabled={!draft.trim()} className="rounded-lg bg-primary px-3 py-2 text-xs font-semibold text-primary-foreground disabled:opacity-40">Save goal</button>
                    </div>
                  </div>
                ) : (
                  <>
                    <div>
                      <div className="mb-2 flex flex-wrap items-center gap-2">
                        <span className="rounded-full bg-primary/10 px-2 py-1 text-[11px] font-semibold text-primary">{goalStatus(details)}</span>
                        {timestamp ? <span className="text-[11px] text-muted-foreground">{timestamp}</span> : null}
                      </div>
                      <h3 className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Objective</h3>
                      <p className="mt-1 whitespace-pre-wrap break-words text-sm leading-relaxed">{goal}</p>
                    </div>

                    {details ? (
                      <div className="grid gap-2 sm:grid-cols-2">
                        <Detail label="Automatic continuation turns" value={details.continuation_count !== null && details.max_continuations !== null ? `${details.continuation_count} of ${details.max_continuations}` : "Not reported"} />
                        {details.no_progress_count !== null || details.max_no_progress_continuations !== null ? <Detail label="No-progress turns" value={details.no_progress_count !== null && details.max_no_progress_continuations !== null ? `${details.no_progress_count} of ${details.max_no_progress_continuations}` : "Not fully reported"} /> : null}
                        <Detail label="Goal started" value={formattedTimestamp(details.created_at)} />
                      </div>
                    ) : (
                      <p className="rounded-xl border border-amber-500/25 bg-amber-500/5 p-3 text-xs text-amber-800 dark:text-amber-200">Detailed live goal state is not available. The displayed objective may be a saved local copy.</p>
                    )}

                    {details?.last_evaluation ? (
                      <div className="rounded-xl border border-border/60 bg-muted/30 p-4">
                        <h3 className="text-xs font-semibold">Latest evaluator report</h3>
                        <p className="mt-1 text-sm">{details.last_evaluation.satisfied === true ? "Reports satisfied" : details.last_evaluation.satisfied === false ? "Reports not satisfied" : "Outcome not reported"}{details.last_evaluation.blocker ? ` · ${details.last_evaluation.blocker}` : ""}</p>
                        {details.last_evaluation.reason ? <p className="mt-1 text-xs text-muted-foreground">{details.last_evaluation.reason}</p> : null}
                        {details.last_evaluation.evidence_summary ? <p className="mt-2 text-xs text-muted-foreground">Reported evidence: {details.last_evaluation.evidence_summary}</p> : null}
                        {details.last_evaluation.evaluated_at ? <p className="mt-2 text-[11px] text-muted-foreground">Checked {details.last_evaluation.evaluated_at}{details.last_evaluation.run_id ? ` · Run ${details.last_evaluation.run_id}` : ""}</p> : null}
                        {details.last_evaluation.stand_down_reason ? <p className="mt-1 text-[11px] text-muted-foreground">Stopped: {details.last_evaluation.stand_down_reason}</p> : null}
                        <p className="mt-3 border-t border-border/50 pt-2 text-[11px] text-amber-700 dark:text-amber-300">This evaluator report is not measured acceptance verification.</p>
                      </div>
                    ) : <p className="text-xs text-muted-foreground">Evaluator report: not reported yet. Goal satisfaction has not been measured.</p>}

                    <div className="flex items-start gap-2 rounded-xl border border-border/60 bg-muted/30 p-3 text-xs text-muted-foreground">
                      <PauseCircle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                      <p>This goal is stored for this conversation. Automatic continuation is bounded by the reported turn budget and runtime availability; it does not mean Alpha is continuously working or guaranteed to run for years.</p>
                    </div>

                    <div className="flex justify-between gap-2 border-t border-border/50 pt-4">
                      <button type="button" onClick={onClear} className="rounded-lg px-3 py-2 text-xs text-destructive hover:bg-destructive/10">Clear goal</button>
                      <button type="button" onClick={() => { onDraftChange(goal ?? ""); onSaveErrorClear(); onEditingChange(true); }} className="rounded-lg bg-primary px-3 py-2 text-xs font-semibold text-primary-foreground">Edit goal</button>
                    </div>
                  </>
                )}
              </div>
            </section>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return <div className="rounded-xl border border-border/60 px-3 py-2"><p className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</p><p className="mt-1 text-xs font-medium">{value}</p></div>;
}
