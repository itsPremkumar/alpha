"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Boxes,
  Check,
  Coins,
  FileDiff,
  Gauge,
  History,
  Link2,
  MessagesSquare,
  Package,
  RefreshCw,
  Search,
  Terminal,
} from "lucide-react";
import { Section, EmptyState, ErrorBox, Badge, Btn, SkeletonList, inputCls } from "@/components/ui";
import { errMsg } from "@/lib/http";
import { applyRunFilter, parseRunInspectorHash, runInspectorHash, runPermalink } from "@/lib/runs-inspector-picker";
import { absoluteStamp, clockTime } from "@/lib/time";
import {
  UNKNOWN_VALUE,
  artifactsFrom,
  delegationsForRun,
  deliveryFrom,
  fetchArtifactArchiveManifest,
  fetchRecentRuns,
  fetchOlderRuns,
  fetchRunRecord,
  fetchRunTimeline,
  fetchRunTranscript,
  fetchThreadDelegations,
  fetchThreadTokenUsage,
  fetchWorkspaceChangesForRun,
  finalAnswer,
  formatCount,
  formatPercentage,
  isActive,
  isTerminalDelegation,
  isTerminalSuccess,
  runFailureFrom,
  servingModelsFrom,
  terminalStatusLabel,
  toolCallsFrom,
  transcriptPrompts,
  type ArtifactArchiveManifest,
  type DeliveryReceipt,
  type RunArtifact,
  type RunDelegation,
  type RunFailureReport,
  type RunRecord,
  type ServingModelReport,
  type ThreadTokenUsage,
  type Timeline,
  type ToolCallRecord,
  type Transcript,
  type WorkspaceChanges,
} from "@/lib/runs-inspector";
import { formatTokenCount } from "@/lib/runs";
import { toolStatusView } from "@/components/ToolPill";
import { RunInspectorTimeline } from "./RunInspectorTimeline";
import type { ToolCall } from "@/types/chat";

/* ── presentation helpers ─────────────────────────────────────────────────── */

type Tone = "green" | "amber" | "gray" | "blue" | "purple" | "cyan" | "red" | "indigo";

/**
 * A tone for the run's own status word.
 *
 * The word itself is always rendered verbatim next to the badge. Only the
 * colour is a presentation choice, and an unreported status is grey — never
 * blue (which would claim it is running) and never green.
 */
function statusTone(status: string | null): Tone {
  if (status === null) return "gray";
  if (status === "success" || status === "completed") return "green";
  if (status === "running" || status === "pending" || status === "queued" || status === "in_progress") return "blue";
  if (status === "error" || status === "failed" || status === "timed_out" || status === "interrupted") return "red";
  return "gray";
}

/** A time the server gave us, or the honest "not reported". */
function stamp(value: string | null): string {
  return absoluteStamp(value) ?? "time not reported";
}

function shortTime(value: string | null): string {
  return clockTime(value) ?? "--:--";
}

/**
 * A measured number as a `Measured` value, or `null` for the honest unknown.
 *
 * `formatCount` returns the `—` glyph for `null`, and a `Measured` tile that
 * receives a string cannot tell that glyph from a real value — which is how a
 * summary the Gateway declined to send once rendered as six bare dashes under
 * six confident labels. `null` is what makes the tile say "not reported".
 */
function measured(value: number | null): string | null {
  return value === null ? null : String(value);
}

function Panel(props: { title: string; icon: React.ReactNode; children: React.ReactNode; aside?: React.ReactNode }) {
  return (
    <section className="rounded-2xl border border-border/60 bg-card p-4 space-y-3">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <h3 className="text-sm font-semibold flex items-center gap-1.5">
          {props.icon}
          {props.title}
        </h3>
        {props.aside}
      </div>
      {props.children}
    </section>
  );
}

/**
 * One measured stat, self-describing on its own.
 *
 * Three rules, each from a way this read was previously misread:
 *
 *  - **A value is never a bare number.** `title` and `aria-label` name the unit
 *    and the surface the number came from, so `52.9k` is legible on its own and
 *    a screen reader announces "52.9k, total tokens, from this run's record"
 *    rather than just the digits.
 *  - **`null` says which of the three unknowns it is.** "not reported" (the
 *    Gateway sent no value) is distinct from a measured `0` and from a value
 *    that does not apply to this run. A bare em dash claimed all three at once.
 *  - **The dash is never printed on its own.** `UNKNOWN_VALUE` is a glyph for
 *    use inside prose; alone in a stat tile it is the ambiguity this component
 *    exists to remove.
 */
function Measured(props: {
  label: string;
  value: string | null;
  /** Names the unit and the source, for the tooltip and the a11y name. */
  title?: string;
  render?: (value: string) => React.ReactNode;
}) {
  const unknown = props.value === null;
  // The label already names the scope, so the unknown sentence does not repeat
  // it — "the Gateway sent no total tokens this run for this run" reads like a
  // bug in the copy even when the underlying claim is right.
  const described = unknown
    ? `not reported — the Gateway sent no value for ${props.label}`
    : `${props.value}${props.title ? `, ${props.title}` : `, ${props.label}`}`;
  return (
    <div
      className="rounded-xl bg-muted/40 px-2.5 py-2"
      title={described}
      aria-label={`${props.label}: ${described}`}
      role="group"
    >
      <div className="text-[11px] font-mono break-all">
        {props.value === null ? (
          <span className="italic text-muted-foreground">not reported</span>
        ) : props.render ? (
          props.render(props.value)
        ) : (
          props.value
        )}
      </div>
      <div className="text-[10px] text-muted-foreground mt-0.5">{props.label}</div>
    </div>
  );
}

function Json(props: { value: unknown; label: string; maxChars?: number }) {
  const limit = props.maxChars ?? 4000;
  let body: string | null = null;
  try {
    body = typeof props.value === "string" ? props.value : JSON.stringify(props.value, null, 2);
  } catch {
    body = null;
  }
  if (body === null) {
    return <p className="text-[11px] italic text-muted-foreground">{props.label} could not be read.</p>;
  }
  const truncated = body.length > limit;
  return (
    <div className="space-y-1">
      <p className="text-[10px] font-semibold text-muted-foreground">{props.label}</p>
      <pre className="rounded-lg bg-muted/40 px-2 py-1.5 text-[10px] font-mono whitespace-pre-wrap break-all max-h-72 overflow-y-auto">
        {truncated ? `${body.slice(0, limit)}\n… [+${body.length - limit} chars truncated]` : body}
      </pre>
    </div>
  );
}

/* ── terminal status ──────────────────────────────────────────────────────── */

/**
 * The run's terminal state, from the run record plus the two facts the record
 * cannot carry on its own.
 *
 * A run that is still active is stated as active and nothing below is allowed
 * to imply it finished. A completed run is labelled `completed` in words and is
 * never called verified: this panel reports what the Gateway recorded, and the
 * Gateway records no verification verdict for a chat run.
 *
 * Two fields here used to be collapsed into one each, and each collapse cost
 * the operator a fact:
 *
 *  - `run.model` is the **configured** Alpha model name (`alpha-free`). The
 *    model that actually answered is `response_metadata.model_name` on the
 *    run's own model responses (`opencode-zen:space-bunny-free`). One tile
 *    labelled "model that served the run" printed the alias, so nobody could
 *    tell which model was really on the other end. They are two tiles now.
 *  - The run's `error` string and the error **code** are different claims.
 *    `RUN_QUOTA_EXCEEDED` also claims `RecursionLimit`, so its message can
 *    describe a budget that was never exhausted; the real `error_type` is
 *    shown beside it.
 *
 * `serving` and `failure` are optional so a caller holding a partial read — or
 * an integration that has not adopted them — degrades to the honest unknown
 * instead of throwing and blanking the panel.
 */
export function RunStatusPanel(props: {
  record: RunRecord;
  error: string | null;
  /** Derived from the run's own `response_metadata.model_name` rows. */
  serving?: ServingModelReport;
  /** The coded failure from the run's own `run.error` event, or `null`. */
  failure?: RunFailureReport | null;
}) {
  const { record } = props;
  const active = isActive(record.status);
  const serving: ServingModelReport = props.serving ?? { models: null, differsFromRecord: false };
  const servingLabel = serving.models === null ? null : serving.models.map((entry) => entry.model).join(", ");
  return (
    <Panel
      title="Terminal status"
      icon={<Gauge className="size-4 text-primary" />}
      aside={<Badge tone={statusTone(record.status)}>{record.status ?? "status not reported"}</Badge>}
    >
      {props.error ? (
        <ErrorBox
          message={`The run record could not be read, so this run's own status, error and model are unknown — the panels below are separate reads. (${props.error})`}
        />
      ) : null}
      <p className="text-[11px] text-muted-foreground">
        {active
          ? "The Gateway still reports this run as active. Nothing here is a finished result yet."
          : record.status === null
            ? "The Gateway did not report a status for this run, so whether it finished is unknown."
            : isTerminalSuccess(record.status)
              ? `The Gateway recorded this run as ${record.status}. Completed is not the same as verified: this run carries no verification verdict.`
              : `The Gateway recorded this run as ${record.status}.`}
      </p>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
        <Measured label="run id" value={record.run_id} title="from the run record" />
        <Measured
          label="model the run was configured with"
          value={record.model}
          title="the Alpha model name this run resolved to — not the provider that answered"
        />
        <Measured
          label="model that actually served this run"
          value={servingLabel}
          title="response_metadata.model_name, as the provider reported it on this run's own model responses"
        />
        <Measured label="assistant" value={record.assistant_id} title="from the run record" />
        <Measured label="trace id" value={record.trace_id} title="metadata.alpha_trace_id on the run record" />
        <Measured label="created" value={record.created_at} render={(v) => stamp(v)} title="run record created_at" />
        <Measured
          label="last updated"
          value={record.updated_at}
          render={(v) => stamp(v)}
          title="run record updated_at"
        />
        <Measured label="stop reason" value={record.stop_reason} title="run record stop_reason" />
        <Measured
          label="multitask strategy"
          value={record.multitask_strategy}
          title="run record multitask_strategy"
        />
      </div>
      {serving.differsFromRecord && serving.models !== null ? (
        <p className="text-[11px] text-muted-foreground">
          Those are two different names for two different things.{" "}
          <span className="font-mono">{record.model}</span> is the configured Alpha model this run asked for; the
          model that produced its answers reported itself as{" "}
          {serving.models.map((entry) => entry.model).join(", ")}.
        </p>
      ) : null}
      {record.error ? (
        <div className="rounded-xl border border-destructive/40 bg-destructive/5 px-3 py-2 space-y-1">
          <p className="text-[11px] font-semibold text-destructive flex items-center gap-1.5">
            <AlertTriangle className="size-3.5" /> The Gateway recorded this error for the run
          </p>
          <p className="text-[11px] font-mono whitespace-pre-wrap break-words">{record.error}</p>
        </div>
      ) : (
        <p className="text-[11px] text-muted-foreground">No error was recorded for this run.</p>
      )}
      {props.failure ? (
        <div className="rounded-xl border border-border/60 bg-muted/30 px-3 py-2 space-y-1.5">
          <p className="text-[11px] font-semibold flex items-center gap-1.5">
            <Terminal className="size-3.5" /> The Gateway&apos;s own code for this failure
          </p>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
            <Measured
              label="error code"
              value={props.failure.code}
              title="metadata.error_code on this run's own run.error event"
            />
            <Measured
              label="real error type"
              value={props.failure.errorType}
              title="metadata.error_type — the exception class, which is what actually stopped the run"
            />
            <Measured label="severity" value={props.failure.severity} title="metadata.severity" />
            <Measured label="suggested recovery" value={props.failure.recovery} title="metadata.recovery" />
          </div>
          {props.failure.message ? (
            <p className="text-[11px] text-muted-foreground">
              The wording attached to that code, verbatim:{" "}
              <span className="font-mono text-foreground">&ldquo;{props.failure.message}&rdquo;</span>
            </p>
          ) : null}
          {props.failure.code && props.failure.errorType && props.failure.code !== props.failure.errorType ? (
            <p className="text-[11px] text-amber-700 dark:text-amber-400">
              One code covers several causes, so its wording is not a statement about this run. The exception that
              actually stopped it was <span className="font-mono">{props.failure.errorType}</span> — read the type,
              not the code&apos;s message, to know what happened. For example{" "}
              <span className="font-mono">RUN_QUOTA_EXCEEDED</span> also claims{" "}
              <span className="font-mono">RecursionLimit</span>, so a run that hit LangGraph&apos;s step limit is
              reported as &ldquo;a run or token budget for this thread is exhausted&rdquo; when no budget was touched.
            </p>
          ) : null}
          {props.failure.detail ? (
            <p className="text-[11px] font-mono whitespace-pre-wrap break-words text-muted-foreground">
              {props.failure.detail}
            </p>
          ) : null}
        </div>
      ) : null}
    </Panel>
  );
}

/* ── the conversation ─────────────────────────────────────────────────────── */

function TranscriptBlock(props: { entries: Transcript["entries"]; empty: string }) {
  if (props.entries.length === 0) {
    return <p className="text-[11px] text-muted-foreground">{props.empty}</p>;
  }
  return (
    <ol className="space-y-2">
      {props.entries.map((entry, i) => {
        const isPrompt = entry.kind === "prompt";
        return (
          <li
            key={`${entry.seq ?? "?"}-${i}`}
            className={`rounded-xl border px-3 py-2 ${isPrompt ? "border-primary/40 bg-primary/5" : "border-border/60 bg-muted/20"}`}
          >
            <div className="flex items-center gap-2 flex-wrap text-[10px] text-muted-foreground">
              <Badge tone={isPrompt ? "blue" : "gray"}>{entry.kind}</Badge>
              <span className="font-mono">{entry.eventType}</span>
              {entry.seq !== null && <span className="font-mono">seq {entry.seq}</span>}
              {entry.caller && <span className="font-mono">{entry.caller}</span>}
              {entry.model && <span className="font-mono">{entry.model}</span>}
              <span className="ml-auto font-mono" title={stamp(entry.createdAt)}>
                {shortTime(entry.createdAt)}
              </span>
            </div>
            {entry.text !== null && entry.text !== "" ? (
              <p className="text-[12px] mt-1.5 whitespace-pre-wrap break-words">{entry.text}</p>
            ) : (
              <p className="text-[11px] italic text-muted-foreground mt-1.5">
                {entry.textOpaque
                  ? "This row's content is not plain text, so it is not shown here."
                  : "This row carries no text content."}
              </p>
            )}
          </li>
        );
      })}
    </ol>
  );
}

/** The submitted prompt(s) and the final answer, in the order they happened. */
export function ConversationPanel(props: { record: RunRecord; transcript: Transcript | null; error: string | null }) {
  const prompts = props.transcript ? transcriptPrompts(props.transcript) : [];
  const answer = props.transcript ? finalAnswer(props.transcript) : null;
  return (
    <Panel title="Prompt and answer" icon={<MessagesSquare className="size-4 text-primary" />}>
      <div className="space-y-3">
        <div className="space-y-1.5">
          <p className="text-[11px] font-semibold">What the run was given</p>
          {props.record.input === null ? (
            <p className="text-[11px] italic text-muted-foreground">
              {props.record.input_readable
                ? "The run record carries no input messages."
                : "The run record's input is not in a readable message form, so it is not shown here."}
            </p>
          ) : props.record.input.length === 0 ? (
            <p className="text-[11px] text-muted-foreground">The run was created with an empty message list.</p>
          ) : (
            <div className="space-y-1.5">
              {props.record.input.map((message, i) => (
                <div key={i} className="rounded-xl border border-border/60 bg-muted/20 px-3 py-2">
                  <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
                    <Badge tone="gray">{message.role ?? "role not reported"}</Badge>
                    <span>as submitted with the run</span>
                  </div>
                  {message.text !== null && message.text !== "" ? (
                    <p className="text-[12px] mt-1.5 whitespace-pre-wrap break-words">{message.text}</p>
                  ) : (
                    <p className="text-[11px] italic text-muted-foreground mt-1.5">This message carries no readable text.</p>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="space-y-1.5">
          <p className="text-[11px] font-semibold">
            The user&rsquo;s {prompts.length === 1 ? "prompt" : `prompts (${prompts.length})`} as the run recorded them
          </p>
          {props.error ? (
            <p className="text-[11px] text-destructive">The run's messages could not be read: {props.error}</p>
          ) : props.transcript === null ? (
            <p className="text-[11px] text-muted-foreground">Reading the run&rsquo;s messages…</p>
          ) : (
            <TranscriptBlock entries={prompts} empty="The Gateway recorded no human input event for this run." />
          )}
        </div>

        <div className="space-y-1.5">
          <p className="text-[11px] font-semibold">The final answer</p>
          {props.error ? (
            <p className="text-[11px] text-destructive">The run's messages could not be read: {props.error}</p>
          ) : props.transcript === null ? (
            <p className="text-[11px] text-muted-foreground">Reading the run&rsquo;s messages…</p>
          ) : answer === null ? (
            <p className="text-[11px] text-muted-foreground">
              {props.transcript.entries.length === 0
                ? "The Gateway recorded no messages for this run."
                : "The run recorded assistant steps, but none of them carries answer text."}
            </p>
          ) : (
            <div className="rounded-xl border border-border/60 bg-muted/20 px-3 py-2">
              <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
                <span className="font-mono">{answer.eventType}</span>
                {answer.seq !== null && <span className="font-mono">seq {answer.seq}</span>}
                {answer.model && <span className="font-mono">{answer.model}</span>}
                <span className="ml-auto font-mono" title={stamp(answer.createdAt)}>
                  {shortTime(answer.createdAt)}
                </span>
              </div>
              {answer.text !== null ? (
                <p className="text-[12px] mt-1.5 whitespace-pre-wrap break-words">{answer.text}</p>
              ) : (
                <p className="text-[11px] italic text-muted-foreground mt-1.5">
                  The answer row&rsquo;s content is not plain text, so it is not shown here.
                </p>
              )}
            </div>
          )}
        </div>

        {props.transcript && !props.transcript.complete && (
          <p className="text-[11px] text-amber-600 dark:text-amber-400">
            Partial: the bounded read of this run&rsquo;s messages stopped before the end of its history.
          </p>
        )}
        {props.transcript && props.transcript.hiddenCount > 0 && (
          <p className="text-[11px] text-muted-foreground">
            {props.transcript.hiddenCount} message row{props.transcript.hiddenCount === 1 ? " is" : "s are"} marked by
            the Gateway as hidden from the UI and {props.transcript.hiddenCount === 1 ? "is" : "are"} counted here
            rather than shown.
          </p>
        )}
      </div>
    </Panel>
  );
}

/* ── tool calls ───────────────────────────────────────────────────────────── */

function ToolCallRow(props: { call: ToolCallRecord }) {
  const { call } = props;
  // Reuse the transcript's own resolver so a call cannot read as successful in
  // one surface and failed in the other. `null` is the honest "no result".
  const view = toolStatusView((call.status ?? undefined) as ToolCall["status"]);
  const conflicted = call.statusConflictsOutput;
  return (
    <li className="rounded-xl border border-border/60 bg-muted/20 px-3 py-2 space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <Terminal className="size-3.5 text-primary shrink-0" />
        <span className="text-[12px] font-mono font-semibold break-all">{call.name ?? "name not reported"}</span>
        {conflicted ? (
          <Badge tone="amber">status conflicts with its own output</Badge>
        ) : (
          <Badge tone={view.state === "success" ? "green" : view.isFailure ? "red" : "gray"}>
            {view.state === "not-reported" ? "no result reported" : view.label}
          </Badge>
        )}
        {call.caller && <span className="text-[10px] font-mono text-muted-foreground">{call.caller}</span>}
        <span className="ml-auto text-[10px] font-mono text-muted-foreground">
          {call.callSeq !== null ? `called at seq ${call.callSeq}` : "call sequence not reported"}
          {call.callAt ? ` · ${shortTime(call.callAt)}` : ""}
        </span>
      </div>

      <div className="text-[10px] text-muted-foreground font-mono break-all">
        call id: {call.id ?? "not reported"}
        {call.unattributed && " · result row could not be attributed to a call in this run"}
      </div>

      {call.argsText !== null ? (
        <Json value={call.argsText} label="arguments" maxChars={2500} />
      ) : (
        <p className="text-[11px] italic text-muted-foreground">
          {call.args === null ? "No arguments were recorded for this call." : "The recorded arguments could not be read."}
        </p>
      )}

      <div className="space-y-1">
        <p className="text-[10px] font-semibold text-muted-foreground">
          Result
          {call.resultSeq !== null ? ` · seq ${call.resultSeq}` : ""}
          {call.resultAt ? ` · ${shortTime(call.resultAt)}` : ""}
          {call.statusVerbatim !== null ? ` · run recorded status "${call.statusVerbatim}"` : ""}
        </p>
        {call.resultText !== null && call.resultText !== "" ? (
          <pre className="rounded-lg bg-muted/40 px-2 py-1.5 text-[10px] font-mono whitespace-pre-wrap break-words max-h-72 overflow-y-auto">
            {call.resultText.length > 4000
              ? `${call.resultText.slice(0, 4000)}\n… [+${call.resultText.length - 4000} chars truncated]`
              : call.resultText}
          </pre>
        ) : (
          <p className="text-[11px] italic text-muted-foreground">
            {call.status === null
              ? "The run recorded no result for this call. That is not a success and not a failure."
              : "The result row carries no text content."}
          </p>
        )}
      </div>

      {conflicted && (
        <p className="text-[11px] text-amber-700 dark:text-amber-400">
          The run journalled this result as status &ldquo;{call.statusVerbatim ?? "not reported"}&rdquo;, but the output
          it recorded reads as an error
          {call.shellExitCode !== null ? (
            <>
              {" "}
              — it ends with <span className="font-mono">Exit Code: {call.shellExitCode}</span>, the shell&apos;s own
              failure marker, which every sandbox appends to otherwise ordinary output
            </>
          ) : null}
          . Both facts are shown; the run journal carries no failure verdict for this call, so none is claimed here.
        </p>
      )}
      {call.artifact !== null && <Json value={call.artifact} label="artifact recorded on this result" maxChars={1500} />}
    </li>
  );
}

export function ToolCallsPanel(props: { calls: ToolCallRecord[]; unattributedCount: number; error: string | null }) {
  return (
    <Panel title={`Tool calls${props.error ? "" : ` (${props.calls.length})`}`} icon={<Boxes className="size-4 text-primary" />}>
      {props.error ? (
        <ErrorBox
          message={`This run's tool calls could not be read, so the list is unavailable rather than empty. (${props.error})`}
        />
      ) : props.calls.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">
          The Gateway recorded no tool call for this run. That is the server&rsquo;s answer, not a failed read.
        </p>
      ) : (
        <>
          <ul className="space-y-2">
            {props.calls.map((call, i) => (
              <ToolCallRow key={`${call.id ?? "?"}-${i}`} call={call} />
            ))}
          </ul>
          {props.unattributedCount > 0 && (
            <p className="text-[11px] text-muted-foreground">
              {props.unattributedCount} result row{props.unattributedCount === 1 ? "" : "s"} could not be attributed to
              a call in this run and {props.unattributedCount === 1 ? "is" : "are"} listed rather than dropped.
            </p>
          )}
        </>
      )}
    </Panel>
  );
}

/* ── subagent delegation ───────────────────────────────────────────────────── */

/**
 * The work this run delegated, from the thread's own delegation ledger.
 *
 * The reason this panel exists rather than reading the `subagent.*` events off
 * the run's stream: those events ride `stream_mode: custom` custom chunks, so
 * they are persisted only for a run that streamed that mode. A genuinely
 * successful delegation can therefore record **zero** `subagent.start` /
 * `subagent.step` / `subagent.end` rows on its parent, and a panel that read
 * them would show an empty delegation for a run that really delegated.
 * `ThreadState.delegations` is captured by `DurableContextMiddleware` on every
 * run and tagged with the `run_id` that made it, so it is the ledger that
 * answers the question.
 *
 * The `subagent.*` count is still shown, as a **cross-check**: when the ledger
 * says this run delegated and the event stream says it delegated nothing, that
 * is a fact about the two read paths, and both are printed rather than one
 * quietly standing in for the other.
 */
export function DelegationPanel(props: {
  delegations: RunDelegation[];
  /** Ledger entries with no `run_id`, so not attributable to any run. */
  unattributed: number;
  /** `null` when the thread state carried no `delegations` channel at all. */
  ledger: RunDelegation[] | null;
  /** `subagent.*` rows this run's own event stream carried. */
  streamEventCount: number | null;
  error: string | null;
  loading: boolean;
}) {
  const count = props.delegations.length;
  return (
    <Panel
      title={props.error || props.ledger === null ? "Subagent delegation" : `Subagent delegation (${count})`}
      icon={<Boxes className="size-4 text-primary" />}
      aside={
        props.error || props.ledger === null ? null : count === 0 ? (
          <Badge tone="gray">none recorded for this run</Badge>
        ) : (
          <Badge tone={props.delegations.every((entry) => isTerminalDelegation(entry.status)) ? "gray" : "blue"}>
            {count} delegated
          </Badge>
        )
      }
    >
      {props.error ? (
        <ErrorBox
          message={`The thread's delegation ledger could not be read, so this run's delegations are unknown rather than none. (${props.error})`}
        />
      ) : props.loading ? (
        <p className="text-[11px] text-muted-foreground">Reading the thread&rsquo;s delegation ledger&hellip;</p>
      ) : props.ledger === null ? (
        <p className="text-[11px] text-muted-foreground">
          The thread state carried no <span className="font-mono">delegations</span> channel, so this run&rsquo;s
          delegations are unknown. That is not the same as a run that delegated nothing.
        </p>
      ) : count === 0 ? (
        <div className="space-y-1">
          <p className="text-[11px] text-muted-foreground">
            The thread&rsquo;s delegation ledger records no delegation tagged with this run&rsquo;s id. That is the
            server&rsquo;s answer, not a failed read.
          </p>
          {props.streamEventCount !== null && props.streamEventCount > 0 ? (
            <p className="text-[11px] text-amber-700 dark:text-amber-400">
              This run&rsquo;s own event stream does carry {props.streamEventCount} subagent event
              {props.streamEventCount === 1 ? "" : "s"}, none of which the ledger attributes to this run. Both
              records are shown; neither is treated as the other.
            </p>
          ) : null}
        </div>
      ) : (
        <div className="space-y-2">
          <p className="text-[11px] text-muted-foreground">
            Recorded in the thread&apos;s delegation ledger and tagged with this run&rsquo;s id. Delegating work is
            not the same as that work succeeding — read each row&apos;s own status.
          </p>
          <ul className="space-y-1.5">
            {props.delegations.map((entry, i) => (
              <li key={`${entry.id ?? "?"}-${i}`} className="rounded-lg bg-muted/30 px-2.5 py-1.5 space-y-1">
                <div className="flex items-center gap-2 flex-wrap text-[10px]">
                  <Badge
                    tone={
                      entry.status === "completed"
                        ? "green"
                        : entry.status === null
                          ? "gray"
                          : isTerminalDelegation(entry.status)
                            ? "red"
                            : "blue"
                    }
                  >
                    {entry.status ?? "status not reported"}
                  </Badge>
                  {entry.subagentType ? (
                    <span className="font-mono text-muted-foreground">via {entry.subagentType}</span>
                  ) : (
                    <span className="italic text-muted-foreground">subagent type not reported</span>
                  )}
                  {entry.stopReason && (
                    <span className="font-mono text-amber-600 dark:text-amber-400">stopped: {entry.stopReason}</span>
                  )}
                  {entry.id && <span className="font-mono text-muted-foreground ml-auto">{entry.id}</span>}
                </div>
                <p className="text-[12px]">
                  {entry.description ?? "The ledger recorded no description for this delegation."}
                </p>
                {entry.resultBrief ? (
                  <p className="text-[11px] text-muted-foreground whitespace-pre-wrap break-words">{entry.resultBrief}</p>
                ) : (
                  <p className="text-[11px] italic text-muted-foreground">
                    The ledger recorded no result brief — that is not a statement that the work succeeded.
                  </p>
                )}
              </li>
            ))}
          </ul>
          {props.streamEventCount === 0 ? (
            <p className="text-[11px] text-amber-700 dark:text-amber-400">
              This run&rsquo;s own event stream recorded <span className="font-mono">0</span> subagent events even
              though the ledger records {count} delegation{count === 1 ? "" : "s"} for it. That is expected when the
              run was not streamed with the{" "}
              <span className="font-mono">custom</span> mode those events ride on — the ledger is the record of the
              delegation; the event stream is only a step feed.
            </p>
          ) : null}
          {props.streamEventCount !== null && props.streamEventCount > 0 ? (
            <p className="text-[11px] text-muted-foreground">
              This run&apos;s event stream also carried {props.streamEventCount} subagent event
              {props.streamEventCount === 1 ? "" : "s"}; they are listed under the event timeline below.
            </p>
          ) : null}
        </div>
      )}
      {props.unattributed > 0 ? (
        <p className="text-[11px] text-muted-foreground">
          {props.unattributed} ledger entr{props.unattributed === 1 ? "y carries" : "ies carry"} no{" "}
          <span className="font-mono">run_id</span> (history written before the tag existed) and{" "}
          {props.unattributed === 1 ? "is" : "are"} therefore not attributed to this run.
        </p>
      ) : null}
    </Panel>
  );
}

/* ── workspace + delivery ─────────────────────────────────────────────────── */

export function WorkspacePanel(props: { changes: WorkspaceChanges | null; error: string | null }) {
  const changes = props.changes;
  return (
    <Panel
      title="Workspace changes"
      icon={<FileDiff className="size-4 text-primary" />}
      aside={
        props.error || !changes ? null : changes.available === false ? (
          <Badge tone="amber">not available for this run</Badge>
        ) : (
          <Badge tone="gray">{changes.files.length} file{changes.files.length === 1 ? "" : "s"}</Badge>
        )
      }
    >
      {props.error ? (
        <ErrorBox message={`The workspace-change report could not be read, so changes are unknown rather than none. (${props.error})`} />
      ) : !changes ? (
        <p className="text-[11px] text-muted-foreground">Reading the run&rsquo;s workspace comparison…</p>
      ) : changes.available === false ? (
        <p className="text-[11px] text-amber-700 dark:text-amber-400">
          The Gateway reports that it could not compare this run&rsquo;s workspace snapshots. The counts it sends with
          that answer are not a measurement, so nothing is claimed about the files this run touched.
        </p>
      ) : changes.summary === null ? (
        <p className="text-[11px] text-muted-foreground">
          The Gateway sent a comparison with no summary object, so its totals are unknown.
        </p>
      ) : (
        <>
          <div className="grid grid-cols-3 lg:grid-cols-6 gap-2">
            <Measured
              label="files created"
              value={measured(changes.summary.created)}
              title="files the Gateway's before/after comparison reported as created"
            />
            <Measured
              label="files modified"
              value={measured(changes.summary.modified)}
              title="files the Gateway's before/after comparison reported as modified"
            />
            <Measured
              label="files deleted"
              value={measured(changes.summary.deleted)}
              title="files the Gateway's before/after comparison reported as deleted"
            />
            <Measured
              label="symlinks created"
              value={measured(changes.summary.symlinkCreated)}
              title="symlinks the Gateway's before/after comparison reported as created"
            />
            <Measured
              label="lines added"
              value={measured(changes.summary.additions)}
              title="diff lines added across this run's changed files"
            />
            <Measured
              label="lines removed"
              value={measured(changes.summary.deletions)}
              title="diff lines removed across this run's changed files"
            />
          </div>
          {changes.summary.truncated === true && (
            <p className="text-[11px] text-amber-600 dark:text-amber-400">The Gateway truncated this summary.</p>
          )}
          {changes.files.length === 0 ? (
            <p className="text-[11px] text-muted-foreground">
              The Gateway compared this run&rsquo;s snapshots and recorded no file changes.
            </p>
          ) : (
            <ul className="space-y-1.5">
              {changes.files.map((file, i) => (
                <li key={`${file.path ?? "?"}-${i}`} className="rounded-lg bg-muted/30 px-2.5 py-1.5 space-y-1">
                  <div className="flex items-center gap-2 flex-wrap text-[10px]">
                    <Badge tone="gray">{file.status ?? "status not reported"}</Badge>
                    <span className="font-mono break-all">{file.path ?? "path not reported"}</span>
                    {file.root && <span className="font-mono text-muted-foreground">{file.root}</span>}
                    {file.sizeAfter !== null && (
                      <span className="font-mono text-muted-foreground ml-auto">{file.sizeAfter} bytes after</span>
                    )}
                  </div>
                  {file.diff !== null ? (
                    <pre className="rounded bg-muted/40 px-2 py-1 text-[10px] font-mono whitespace-pre-wrap break-words max-h-56 overflow-y-auto">
                      {file.diff.length > 3000 ? `${file.diff.slice(0, 3000)}\n… [+${file.diff.length - 3000} chars truncated]` : file.diff}
                    </pre>
                  ) : (
                    <p className="text-[10px] italic text-muted-foreground">
                      {file.diffUnavailableReason
                        ? `No diff: the Gateway recorded "${file.diffUnavailableReason}".`
                        : "The Gateway recorded no diff for this file."}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </Panel>
  );
}

export function DeliveryPanel(props: {
  receipt: DeliveryReceipt | null;
  manifest: ArtifactArchiveManifest | null;
  artifacts: RunArtifact[];
  timelineError: string | null;
  error: string | null;
}) {
  const receipt = props.receipt;
  return (
    <Panel title="Delivered artifacts" icon={<Package className="size-4 text-primary" />}>
      {props.timelineError ? (
        <ErrorBox message={`The run's delivery receipt lives on its event stream, which could not be read. (${props.timelineError})`} />
      ) : (
        <>
          {receipt === null ? (
            <p className="text-[11px] text-muted-foreground">
              The Gateway recorded no delivery event for this run. Whether files were delivered is not claimed here.
            </p>
          ) : (
            <div className="space-y-2">
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
                <Measured
                  label="files presented"
                  value={measured(receipt.presented)}
                  title="paths this run's own delivery receipt listed as presented"
                />
                <Measured label="delivery stage" value={receipt.stage} title="run.delivery content.stage, verbatim" />
                <Measured
                  label="delivery requirement satisfied"
                  value={receipt.satisfied === null ? null : receipt.satisfied ? "yes" : "no"}
                  title="whether the presented paths covered the produced paths — not whether the answer was right"
                />
                <Measured
                  label="how that check was made"
                  value={receipt.verificationSource}
                  title="run.delivery verification.source, verbatim — the method, not a verdict on the run"
                />
              </div>
              <p className="text-[11px] text-muted-foreground">
                This is a delivery receipt, not a verdict on the answer and not a verification of the run. The Gateway
                compared the presented files against {receipt.requirement ?? "an unreported requirement"}.
              </p>
              {receipt.paths.length > 0 ? (
                <ul className="space-y-1">
                  {receipt.paths.map((path) => (
                    <li key={path} className="rounded-lg bg-muted/30 px-2 py-1.5 text-[11px] font-mono break-all flex items-center gap-2">
                      <Package className="size-3 text-primary shrink-0" />
                      {path}
                      {receipt.byTool && Object.entries(receipt.byTool).some(([, paths]) => paths.includes(path)) && (
                        <span className="ml-auto text-[10px] text-muted-foreground">
                          via {Object.entries(receipt.byTool).find(([, paths]) => paths.includes(path))?.[0]}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-[11px] text-muted-foreground">The receipt lists no delivered path.</p>
              )}
            </div>
          )}

          <div className="space-y-1">
            <p className="text-[11px] font-semibold">Archive manifest</p>
            {props.error ? (
              <p className="text-[11px] text-destructive">The archive manifest could not be read: {props.error}</p>
            ) : props.manifest === null ? (
              <p className="text-[11px] text-muted-foreground">Reading the archive manifest…</p>
            ) : (
              <p className="text-[11px] text-muted-foreground">
                {props.manifest.fileCount === null
                  ? "The Gateway sent a manifest with no file count."
                  : `The archive endpoint will package ${props.manifest.fileCount} file${props.manifest.fileCount === 1 ? "" : "s"} for this run.`}
              </p>
            )}
          </div>

          <div className="space-y-1">
            <p className="text-[11px] font-semibold">Artifacts recorded on tool results</p>
            {props.artifacts.length === 0 ? (
              <p className="text-[11px] text-muted-foreground">No tool result in this run carried an artifact.</p>
            ) : (
              props.artifacts.map((artifact, i) => (
                <Json key={i} value={artifact.artifact} label={`artifact from ${artifact.toolName ?? "an unnamed tool"}`} maxChars={1200} />
              ))
            )}
          </div>
        </>
      )}
    </Panel>
  );
}

/* ── tokens ───────────────────────────────────────────────────────────────── */

export function TokenPanel(props: { record: RunRecord; usage: ThreadTokenUsage | null; error: string | null }) {
  const { record } = props;
  const usage = props.usage;
  // `formatTokenCount` abbreviates ("52.9k"), so every tile names the exact
  // figure in its tooltip: an abbreviated number with no unit beside it is the
  // one header stat an operator cannot check against a bill.
  const tokens = (value: number | null, what: string) =>
    value === null ? null : formatTokenCount(value);
  return (
    <Panel title="Tokens and model" icon={<Coins className="size-4 text-primary" />}>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
        <Measured
          label="total tokens this run"
          value={tokens(record.total_tokens, "total")}
          title={
            record.total_tokens === null
              ? "the Gateway reported no total for this run"
              : `${record.total_tokens} tokens, as reported on this run's record`
          }
        />
        <Measured
          label="input tokens"
          value={tokens(record.total_input_tokens, "input")}
          title={
            record.total_input_tokens === null
              ? "the Gateway reported no input count for this run"
              : `${record.total_input_tokens} tokens read across this run`
          }
        />
        <Measured
          label="output tokens"
          value={tokens(record.total_output_tokens, "output")}
          title={
            record.total_output_tokens === null
              ? "the Gateway reported no output count for this run"
              : `${record.total_output_tokens} tokens written across this run`
          }
        />
        <Measured
          label="model calls made"
          value={record.llm_call_count === null ? null : String(record.llm_call_count)}
          title="llm_call_count — how many model responses this run recorded, not a token figure"
        />
        <Measured
          label="lead agent tokens"
          value={tokens(record.lead_agent_tokens, "lead")}
          title={
            record.lead_agent_tokens === null
              ? "the Gateway attributed no tokens to the lead agent"
              : `${record.lead_agent_tokens} tokens attributed to the lead agent`
          }
        />
        <Measured
          label="subagent tokens"
          value={tokens(record.subagent_tokens, "subagent")}
          title={
            record.subagent_tokens === null
              ? "the Gateway attributed no tokens to subagents"
              : `${record.subagent_tokens} tokens attributed to delegated subagents — zero here does not mean the run delegated nothing`
          }
        />
        <Measured
          label="middleware tokens"
          value={tokens(record.middleware_tokens, "middleware")}
          title={
            record.middleware_tokens === null
              ? "the Gateway attributed no tokens to middleware"
              : `${record.middleware_tokens} tokens spent on middleware model calls`
          }
        />
        <Measured
          label="messages recorded"
          value={measured(record.message_count)}
          title="message_count on the run record — the Gateway's own count, not this client's"
        />
      </div>

      <div className="space-y-1">
        <p className="text-[11px] font-semibold">Per-model split</p>
        <p className="text-[10px] text-muted-foreground">
          These keys are the names the <span className="font-mono">provider</span> reported for each model call
          ({record.model ? `a run configured with ${record.model} may still have been served by these` : "not the configured alias"}),
          so this is where a run's spend is actually attributable.
        </p>
        {record.token_usage_by_model === null ? (
          <p className="text-[11px] text-muted-foreground">
            This run reported no per-model token split. That is not zero tokens.
          </p>
        ) : (
          <ul className="space-y-1">
            {record.token_usage_by_model.map((row) => (
              <li
                key={row.model}
                className="rounded-lg bg-muted/30 px-2 py-1.5 text-[11px] font-mono flex items-center gap-2 flex-wrap"
                title={`${row.model}: ${row.input ?? "not reported"} in, ${row.output ?? "not reported"} out, ${
                  row.total ?? "not reported"
                } total — the provider-reported model name and its token counts`}
              >
                <span className="font-semibold break-all">{row.model}</span>
                <span className="text-muted-foreground">
                  {/*
                    A bare `—` here claimed all three unknowns at once: the
                    Gateway sent no input count, sent no output count, or this
                    model bucket has no usage. The words are longer and say
                    which one it is.
                  */}
                  {row.input === null ? "input not reported" : `${formatTokenCount(row.input)} in`} /{" "}
                  {row.output === null ? "output not reported" : `${formatTokenCount(row.output)} out`}
                </span>
                {row.total !== null && <span className="text-muted-foreground">· {formatTokenCount(row.total)} total</span>}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="space-y-1">
        <p className="text-[11px] font-semibold">Thread context (whole conversation, not this run)</p>
        {props.error ? (
          <p className="text-[11px] text-destructive">The thread&rsquo;s token usage could not be read: {props.error}</p>
        ) : usage === null ? (
          <p className="text-[11px] text-muted-foreground">Reading the thread&rsquo;s token usage&hellip;</p>
        ) : (
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
            <Measured
              label="thread tokens (all runs)"
              value={tokens(usage.totalTokens, "thread")}
              title={
                usage.totalTokens === null
                  ? "the Gateway reported no thread total"
                  : `${usage.totalTokens} tokens across every run in this conversation, not this run alone`
              }
            />
            <Measured
              label="runs counted"
              value={measured(usage.totalRuns)}
              title="runs the Gateway included in the thread total above"
            />
            <Measured
              label="context tokens in use"
              value={usage.contextUsage === null ? null : formatTokenCount(usage.contextUsage.tokenCount)}
              title={
                usage.contextUsage?.tokenCount === null || usage.contextUsage === null
                  ? "the Gateway reported no context measurement"
                  : `${usage.contextUsage.tokenCount} tokens currently in the model's context`
              }
            />
            <Measured
              label="of the context window"
              value={
                usage.contextUsage === null || usage.contextUsage.percentage === null
                  ? null
                  : formatPercentage(usage.contextUsage.percentage)
              }
              title={
                usage.contextUsage === null || usage.contextUsage.percentage === null
                  ? "the Gateway reported no occupancy percentage"
                  : `${usage.contextUsage.percentage}% of the window, computed by the Gateway`
              }
            />
          </div>
        )}
        {usage?.contextUsage && usage.contextUsage.maxContextTokens !== null && (
          <p className="text-[10px] text-muted-foreground font-mono">
            context window {formatTokenCount(usage.contextUsage.maxContextTokens)} tokens
          </p>
        )}
      </div>
    </Panel>
  );
}

/* ── the run picker ───────────────────────────────────────────────────────── */

/**
 * The loaded runs, narrowed by the filter box.
 *
 * The filter is a view over the rows already in state — it issues no request —
 * so the header still describes the *list* (the newest page only, or a complete
 * walk) and never implies the filtered view is a new answer from the Gateway.
 */
function RunPicker(props: {
  runs: RunRecord[];
  complete: boolean;
  selectedRunId: string | null;
  onSelect: (runId: string) => void;
  query: string;
  onQueryChange: (query: string) => void;
  /** Older runs are reachable only where the read's cap left a cursor behind. */
  onLoadOlder?: () => void;
  loadingOlder?: boolean;
  loadOlderError?: string | null;
  canLoadOlder?: boolean;
}) {
  const filter = applyRunFilter(props.runs, props.query);
  return (
    <div className="space-y-1.5">
      <p className="text-[11px] font-semibold flex items-center gap-1.5">
        <History className="size-3.5 text-primary" /> Runs in this conversation
        <span className="font-normal text-muted-foreground">
          ({props.complete ? "the Gateway reported no further pages" : "the newest page only"})
        </span>
      </p>
      <div className="relative">
        <Search className="size-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none" />
        <input
          type="text"
          value={props.query}
          onChange={(event) => props.onQueryChange(event.target.value)}
          placeholder="Filter these runs"
          aria-label="Filter the loaded runs"
          title="Narrows the runs already loaded, with no new request. Matches a run's id, status, model or recorded error."
          className={`${inputCls} pl-8`}
        />
      </div>
      {filter.filtered ? (
        <p className="text-[10px] text-muted-foreground">
          {filter.runs.length} of {props.runs.length} loaded run{props.runs.length === 1 ? "" : "s"} match the filter.
        </p>
      ) : null}
      {filter.filtered && filter.runs.length === 0 ? (
        // The two empty states are different sentences. This one is about the
        // query; the "the Gateway reported no runs" line above the picker is
        // about the server, and the filter must never borrow its wording.
        <p className="text-[11px] text-muted-foreground">
          {props.runs.length === 0
            ? "The filter returned no run."
            : `No loaded run matches this filter. The Gateway reported ${props.runs.length} run${props.runs.length === 1 ? "" : "s"} for this conversation, so the conversation is not empty.`}
        </p>
      ) : (
        <ul className="space-y-1.5 max-h-[32rem] overflow-y-auto pr-1">
          {filter.runs.map((run) => {
            const selected = run.run_id !== null && run.run_id === props.selectedRunId;
            return (
              <li key={run.run_id ?? `${run.created_at ?? "?"}-${Math.random()}`}>
                <button
                  type="button"
                  onClick={() => run.run_id && props.onSelect(run.run_id)}
                  disabled={run.run_id === null}
                  className={`w-full text-left rounded-xl border px-2.5 py-2 transition-colors ${
                    selected ? "border-primary ring-1 ring-primary/30 bg-card" : "border-border/60 hover:border-primary/40 bg-card"
                  } ${run.run_id === null ? "opacity-50 cursor-not-allowed" : ""}`}
                >
                  <div className="flex items-center gap-2 flex-wrap">
                    <Badge tone={statusTone(run.status)}>{run.status ?? "status not reported"}</Badge>
                    <span className="text-[10px] font-mono text-muted-foreground break-all">
                      {run.run_id ?? "run id not reported"}
                    </span>
                    {isActive(run.status) && <Badge tone="blue">still active</Badge>}
                    <span className="ml-auto text-[10px] text-muted-foreground" title={stamp(run.created_at)}>
                      {shortTime(run.created_at)}
                    </span>
                  </div>
                  <div className="text-[10px] text-muted-foreground mt-1 font-mono truncate">
                    {run.model ?? "model not reported"} ·{" "}
                    {run.total_tokens === null ? "tokens not reported" : `${formatTokenCount(run.total_tokens)} tokens`}
                  </div>
                  {run.error && <div className="text-[10px] text-destructive mt-0.5 line-clamp-2">{run.error}</div>}
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {props.onLoadOlder ? (
        <div className="pt-1">
          <Btn variant="ghost" onClick={props.onLoadOlder} disabled={props.loadingOlder || !props.canLoadOlder}>
            <RefreshCw className={`size-3.5 ${props.loadingOlder ? "animate-spin" : ""}`} />
            {props.loadingOlder ? "Loading older runs…" : "Load older runs"}
          </Btn>
          <p className="text-[10px] text-muted-foreground mt-1">
            {props.complete
              ? "The Gateway reported no earlier run for this conversation."
              : props.canLoadOlder
                ? "This list stopped at the read's own cap. Loading more asks the Gateway for the next keyset page."
                : "The oldest loaded run has no cursor to page from, so an earlier page cannot be requested."}
          </p>
          {props.loadOlderError ? (
            <p className="text-[10px] text-destructive mt-1">Could not load older runs: {props.loadOlderError}</p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/* ── the permalink ─────────────────────────────────────────────────────────── */

/**
 * The control that copies a link to the selected run.
 *
 * The link is a *view* of one run's recorded state, not a claim about it: the
 * label and the title say "this run" and never "verified", because the Gateway
 * records no verification verdict for a chat run. The copy reports what
 * happened — a write that never landed is stated in words, and the button is
 * disabled for the duration so a double-click cannot start a second write.
 */
function RunLinkControl(props: {
  runId: string;
  copying: boolean;
  notice: { message: string; copied: boolean } | null;
  onCopy: () => void;
}) {
  return (
    <div className="space-y-1">
      <Btn
        variant="ghost"
        onClick={props.onCopy}
        disabled={props.copying}
        title={`Copies a link that reopens the run inspector on run ${props.runId} in this conversation, showing what the Gateway recorded for it.`}
      >
        <Link2 className="size-3.5" /> {props.copying ? "Copying…" : "Copy link to this run"}
      </Btn>
      {props.notice ? (
        <p
          className={`text-[11px] flex items-center gap-1.5 ${props.notice.copied ? "text-emerald-700 dark:text-emerald-400" : "text-destructive"}`}
        >
          {props.notice.copied ? <Check className="size-3" /> : <AlertTriangle className="size-3" />}
          {props.notice.message}
        </p>
      ) : null}
    </div>
  );
}

/* ── the view (pure) ──────────────────────────────────────────────────────── */

export interface RunInspectorState {
  threadId: string;
  runs: RunRecord[];
  runsComplete: boolean;
  runsError: string | null;
  selectedRunId: string | null;
  record: RunRecord | null;
  transcript: Transcript | null;
  timeline: Timeline | null;
  workspace: WorkspaceChanges | null;
  usage: ThreadTokenUsage | null;
  manifest: ArtifactArchiveManifest | null;
  /**
   * The thread's whole `delegations` ledger, or `null` when the thread state
   * carried no such channel. Filtered to this run by `delegationsForRun`.
   */
  delegations: RunDelegation[] | null;
  /** True while the delegation ledger read is in flight. */
  loadingDelegations: boolean;
  /** Per-read failures, so one failed panel never blanks the rest. */
  errors: Partial<
    Record<"record" | "transcript" | "timeline" | "workspace" | "usage" | "manifest" | "delegations", string>
  >;
  loadingRun: boolean;
  showPicker: boolean;
  /** The picker's filter box. Purely local, over the runs already loaded. */
  runFilter: string;
  /** True while a clipboard write is in flight; the control is disabled then. */
  copyingLink: boolean;
  /** The copy outcome in words. `copied: false` means nothing was copied. */
  linkNotice: { message: string; copied: boolean } | null;
  /** Why a permalink in the address bar was not applied, when it was not. */
  selectionNote: string | null;
  /** True while an older keyset page is in flight; the control is disabled then. */
  loadingOlderRuns: boolean;
  /** The older-page read's own reason, so a failure is never an empty list. */
  olderRunsError: string | null;
}

/**
 * The whole inspector, as a pure function of one read.
 *
 * Exported separately from the container so the honesty rules can be asserted
 * against real rendered markup with real client output, not against source.
 */
export function RunInspectorView(props: {
  state: RunInspectorState;
  onSelect: (runId: string) => void;
  onReload: () => void;
  onQueryChange: (query: string) => void;
  onCopyLink: () => void;
  onLoadOlder: () => void;
  /** False when the oldest loaded run carries no cursor to page from. */
  canLoadOlder: boolean;
}) {
  const { state } = props;
  const calls = useMemo(
    () => (state.transcript ? toolCallsFrom(state.transcript) : { calls: [], unattributedCount: 0 }),
    [state.transcript]
  );
  const delivery = useMemo(() => (state.timeline ? deliveryFrom(state.timeline) : null), [state.timeline]);
  const artifacts = useMemo(() => artifactsFrom(calls.calls), [calls.calls]);
  // The serving model is a property of the run's *responses*, not of the run
  // record, so it is derived here where the transcript is in hand.
  const serving = useMemo(
    () => servingModelsFrom(state.transcript, state.record?.model ?? null),
    [state.transcript, state.record?.model]
  );
  // The coded failure lives on the run's own `run.error` event.
  const failure = useMemo(() => runFailureFrom(state.timeline), [state.timeline]);
  // The delegation ledger is thread-wide; the `run_id` tag is what makes a row
  // this run's. Untagged rows are counted, never folded in.
  const { delegations, unattributed } = useMemo(
    () => delegationsForRun(state.delegations, state.selectedRunId),
    [state.delegations, state.selectedRunId]
  );
  // The subagent events this run's own stream carried, as a cross-check on the
  // ledger. `null` while the stream is unread, so "0" never stands for "not
  // looked at".
  const subagentStreamCount = useMemo(
    () => (state.timeline ? state.timeline.events.filter((event) => event.eventType.startsWith("subagent.")).length : null),
    [state.timeline]
  );
  // A permalink may name a run outside the page we loaded. That is disclosed
  // rather than hidden, and the panels still read that run directly.
  const outsideLoadedPage =
    state.selectedRunId !== null &&
    state.runs.length > 0 &&
    !state.runs.some((run) => run.run_id === state.selectedRunId);

  return (
    <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,20rem)_minmax(0,1fr)] gap-4">
      {props.state.showPicker && (
        <div className="rounded-2xl border border-border/60 bg-card p-3">
          {state.runsError ? (
            <ErrorBox
              message={`This conversation's run list could not be read, so no run can be selected. (${state.runsError})`}
              onRetry={props.onReload}
            />
          ) : state.runs.length === 0 ? (
            <p className="text-[11px] text-muted-foreground">
              The Gateway reported no runs for this conversation. That is its answer, not a failed read.
            </p>
          ) : (
            <RunPicker
              runs={state.runs}
              complete={state.runsComplete}
              selectedRunId={state.selectedRunId}
              onSelect={props.onSelect}
              query={state.runFilter}
              onQueryChange={props.onQueryChange}
              onLoadOlder={props.onLoadOlder}
              loadingOlder={state.loadingOlderRuns}
              loadOlderError={state.olderRunsError}
              canLoadOlder={props.canLoadOlder}
            />
          )}
        </div>
      )}

      <div className="space-y-4 min-w-0">
        {!state.selectedRunId ? (
          <EmptyState title="Select a run" hint="Pick a run to read its prompt, tool calls, events, files and tokens." />
        ) : state.loadingRun && state.record === null ? (
          <SkeletonList rows={4} />
        ) : state.record === null && state.errors.record ? (
          <ErrorBox message={`This run's record could not be read, so its status, error and model are unknown. (${state.errors.record})`} onRetry={props.onReload} />
        ) : state.record === null ? (
          <SkeletonList rows={4} />
        ) : (
          <>
            <div className="flex items-center gap-2 flex-wrap">
              <h3 className="text-sm font-semibold font-mono break-all">{state.selectedRunId}</h3>
              {state.loadingRun && (
                <span className="text-[10px] text-muted-foreground flex items-center gap-1">
                  <RefreshCw className="size-3 animate-spin" /> reloading
                </span>
              )}
            </div>
            <RunLinkControl
              runId={state.selectedRunId}
              copying={state.copyingLink}
              notice={state.linkNotice}
              onCopy={props.onCopyLink}
            />
            {outsideLoadedPage && (
              <p className="text-[11px] text-muted-foreground">
                This run is not in the page of runs loaded above, so the panels read it from the Gateway directly. Each
                one reports its own answer, including if that answer is a refusal.
              </p>
            )}
            {state.selectionNote ? (
              <p className="text-[11px] text-amber-700 dark:text-amber-400">{state.selectionNote}</p>
            ) : null}
            <RunStatusPanel
              record={state.record}
              error={state.errors.record ?? null}
              serving={serving}
              failure={failure}
            />
            <ConversationPanel record={state.record} transcript={state.transcript} error={state.errors.transcript ?? null} />
            <ToolCallsPanel calls={calls.calls} unattributedCount={calls.unattributedCount} error={state.errors.transcript ?? null} />
            <DelegationPanel
              delegations={delegations}
              unattributed={unattributed}
              ledger={state.delegations}
              streamEventCount={subagentStreamCount}
              error={state.errors.delegations ?? null}
              loading={state.loadingDelegations}
            />
            <RunInspectorTimeline timeline={state.timeline} error={state.errors.timeline ?? null} />
            <WorkspacePanel changes={state.workspace} error={state.errors.workspace ?? null} />
            <DeliveryPanel
              receipt={delivery}
              manifest={state.manifest}
              artifacts={artifacts}
              timelineError={state.errors.timeline ?? null}
              error={state.errors.manifest ?? null}
            />
            <TokenPanel record={state.record} usage={state.usage} error={state.errors.usage ?? null} />
          </>
        )}
      </div>
    </div>
  );
}

/* ── the container ────────────────────────────────────────────────────────── */

const EMPTY_STATE: RunInspectorState = {
  threadId: "",
  runs: [],
  runsComplete: false,
  runsError: null,
  selectedRunId: null,
  record: null,
  transcript: null,
  timeline: null,
  workspace: null,
  usage: null,
  manifest: null,
  delegations: null,
  loadingDelegations: false,
  errors: {},
  loadingRun: false,
  showPicker: true,
  runFilter: "",
  copyingLink: false,
  linkNotice: null,
  selectionNote: null,
  loadingOlderRuns: false,
  olderRunsError: null,
};

/** The run a permalink in the address bar names, or `null` if it names none. */
function linkedRun(): { threadId: string; runId: string } | null {
  if (typeof window === "undefined") return null;
  return parseRunInspectorHash(window.location.hash);
}

/**
 * Put the open run in the address bar, so the URL and the view agree.
 *
 * `replaceState`, never `pushState`: a selection is not a navigation, and it
 * must not put a back-button entry behind every run the operator looks at.
 */
function writeLinkedRun(threadId: string, runId: string): void {
  if (typeof window === "undefined") return;
  const hash = runInspectorHash(threadId, runId);
  if (window.location.hash === hash) return;
  window.history.replaceState(null, "", hash);
}

/**
 * One run's full story, read from the Gateway.
 *
 * The six run-scoped reads are issued independently and settled independently,
 * so a 429 on the event stream degrades exactly one panel with the server's
 * own reason instead of blanking the inspector or — worse — rendering the
 * missing panels as empty successes. Every control disables itself while its
 * request is in flight, so a double-click cannot start two reads of the same run.
 */
export function RunInspectorSection(props: { threadId: string | null; runId?: string | null; showPicker?: boolean }) {
  const showPicker = props.showPicker ?? true;
  const [state, setState] = useState<RunInspectorState>({ ...EMPTY_STATE, showPicker });
  const [reloadKey, setReloadKey] = useState(0);
  // A slow read for a run the user already left must not repaint the new one.
  const generation = useRef(0);
  // A synchronous lock for the copy: the control only disables on the next
  // render, and a second click before that render must not start a second write.
  const copying = useRef(false);
  /** Synchronous lock so a double-click cannot start a second page read. */
  const loadingOlder = useRef(false);

  useEffect(() => {
    setState((prev) => ({ ...EMPTY_STATE, showPicker, threadId: props.threadId ?? "" }));
    if (!props.threadId) return;
    const token = ++generation.current;
    let cancelled = false;
    void (async () => {
      try {
        const list = await fetchRecentRuns(props.threadId!);
        if (cancelled || generation.current !== token) return;
        // A permalink survives a reload, so the run it names is opened here
        // instead of the newest one. A link for a *different* conversation
        // names no run in this one, so it is not opened and not hidden either.
        const link = linkedRun();
        const note =
          link && link.threadId !== props.threadId
            ? `The link in the address bar names conversation ${link.threadId}, not the one open here, so its run was not opened.`
            : null;
        const host = props.runId ?? null;
        const fromLink = link && link.threadId === props.threadId ? link.runId : null;
        const hostRun = host !== null && list.runs.some((run) => run.run_id === host) ? host : null;
        // Honoured as written: a linked run outside the loaded page is still
        // selected, and the panels below read it from the Gateway directly.
        const first = hostRun ?? fromLink ?? list.runs[0]?.run_id ?? null;
        setState((prev) => ({
          ...prev,
          runs: list.runs,
          runsComplete: list.complete,
          selectedRunId: first,
          selectionNote: note,
        }));
      } catch (e) {
        if (cancelled || generation.current !== token) return;
        setState((prev) => ({ ...prev, runs: [], runsError: errMsg(e) }));
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.threadId, reloadKey]);

  const runId = state.selectedRunId;
  useEffect(() => {
    if (!props.threadId || !runId) return;
    const token = ++generation.current;
    let cancelled = false;
    setState((prev) => ({ ...prev, loadingRun: true, loadingDelegations: true, errors: {} }));
    const read = async <T,>(
      key: "record" | "transcript" | "timeline" | "workspace" | "usage" | "manifest" | "delegations",
      call: () => Promise<T>
    ): Promise<[typeof key, T | null, string | null]> => {
      try {
        return [key, await call(), null];
      } catch (e) {
        return [key, null, errMsg(e)];
      }
    };
    void (async () => {
      const results = await Promise.all([
        read("record", () => fetchRunRecord(props.threadId!, runId)),
        read("transcript", () => fetchRunTranscript(props.threadId!, runId)),
        read("timeline", () => fetchRunTimeline(props.threadId!, runId)),
        read("workspace", () => fetchWorkspaceChangesForRun(props.threadId!, runId)),
        read("usage", () => fetchThreadTokenUsage(props.threadId!)),
        read("manifest", () => fetchArtifactArchiveManifest(props.threadId!, runId)),
        // The ledger is thread-wide, so it is re-read with the rest of the run
        // rather than cached across selections: a later run in the same
        // conversation delegates into the same channel, and a stale ledger would
        // report "no delegations" for a run that made one.
        read("delegations", () => fetchThreadDelegations(props.threadId!)),
      ]);
      if (cancelled || generation.current !== token) return;
      const errors: RunInspectorState["errors"] = {};
      const patch: Partial<RunInspectorState> = { loadingRun: false, loadingDelegations: false };
      for (const [key, value, error] of results) {
        if (error !== null) {
          errors[key] = error;
          continue;
        }
        if (key === "record") patch.record = value as RunRecord | null;
        if (key === "transcript") patch.transcript = value as Transcript | null;
        if (key === "timeline") patch.timeline = value as Timeline | null;
        if (key === "workspace") patch.workspace = value as WorkspaceChanges | null;
        if (key === "usage") patch.usage = value as ThreadTokenUsage | null;
        if (key === "manifest") patch.manifest = value as ArtifactArchiveManifest | null;
        if (key === "delegations") patch.delegations = value as RunDelegation[] | null;
      }
      setState((prev) => ({ ...prev, ...patch, errors }));
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.threadId, runId, reloadKey]);

  const onSelect = useCallback(
    (next: string) => {
      if (props.threadId) writeLinkedRun(props.threadId, next);
      setState((prev) => ({
        ...prev,
        selectedRunId: next,
        record: null,
        transcript: null,
        timeline: null,
        workspace: null,
        manifest: null,
        errors: {},
        selectionNote: null,
        linkNotice: null,
      }));
    },
    [props.threadId]
  );

  /** The picker's filter box: local, over the runs already loaded, no request. */
  const onQueryChange = useCallback((runFilter: string) => {
    setState((prev) => ({ ...prev, runFilter }));
  }, []);

  /**
   * Copy a link that reopens the inspector on exactly this run.
   *
   * The clipboard write is the only thing that produces a "Link copied", and
   * it is only said once the write has actually resolved. A browser with no
   * clipboard, or one that refuses the write, is told in words that nothing
   * was copied — and the address bar is left holding the link, so the copy is
   * still reachable by hand.
   */
  const onCopyLink = useCallback(() => {
    const threadId = props.threadId;
    const runId = state.selectedRunId;
    if (!threadId || !runId || copying.current) return;
    copying.current = true;
    setState((prev) => ({ ...prev, copyingLink: true, linkNotice: null }));
    const giveUp = (message: string) => {
      copying.current = false;
      setState((prev) => ({ ...prev, copyingLink: false, linkNotice: { message, copied: false } }));
    };
    void (async () => {
      const base =
        typeof window === "undefined"
          ? ""
          : `${window.location.origin}${window.location.pathname}${window.location.search}`;
      const link = runPermalink(base, threadId, runId);
      writeLinkedRun(threadId, runId);
      const clipboard = typeof navigator === "undefined" ? undefined : navigator.clipboard;
      if (!clipboard || typeof clipboard.writeText !== "function") {
        giveUp("This browser gave the page no clipboard to write to, so nothing was copied. The link is in the address bar.");
        return;
      }
      try {
        await clipboard.writeText(link);
        copying.current = false;
        setState((prev) => ({
          ...prev,
          copyingLink: false,
          linkNotice: { message: "Link copied. Opening it reopens the run inspector on this run.", copied: true },
        }));
      } catch (e) {
        giveUp(`The browser refused the clipboard write, so nothing was copied. The link is in the address bar. (${errMsg(e)})`);
      }
    })();
  }, [props.threadId, state.selectedRunId]);

  const onReload = useCallback(() => setReloadKey((key) => key + 1), []);

  /**
   * The keyset cursor for the next, older page.
   *
   * It is the OLDEST row already loaded: `created_at` plus `run_id` is exactly
   * the pair the Gateway hands back as `next_before_*`, so the cursor is the
   * server's own and is not reconstructed from anything the server did not say.
   * Without both fields there is nowhere to page from, and the control says so
   * instead of re-reading the newest page.
   */
  const olderCursor = useMemo(() => {
    let oldest: RunRecord | null = null;
    for (const run of state.runs) {
      if (run.run_id === null || run.created_at === null) continue;
      if (oldest === null || (run.created_at < oldest.created_at!)) oldest = run;
    }
    return oldest ? { createdAt: oldest.created_at as string, runId: oldest.run_id as string } : null;
  }, [state.runs]);

  /**
   * Append the next older page of runs.
   *
   * A failed page appends nothing and shows the server's reason: the list the
   * user already has stays exactly as it was, and a failed read is never
   * rendered as "there are no older runs".
   */
  const onLoadOlder = useCallback(() => {
    const threadId = props.threadId;
    if (!threadId || !olderCursor || loadingOlder.current) return;
    loadingOlder.current = true;
    setState((prev) => ({ ...prev, loadingOlderRuns: true, olderRunsError: null }));
    void (async () => {
      try {
        const page = await fetchOlderRuns(threadId, olderCursor.createdAt, olderCursor.runId);
        loadingOlder.current = false;
        setState((prev) => ({
          ...prev,
          loadingOlderRuns: false,
          runs: [...prev.runs, ...page.runs],
          // The server's own `has_more` is the whole story about what follows.
          runsComplete: page.hasMore === false,
        }));
      } catch (e) {
        loadingOlder.current = false;
        setState((prev) => ({ ...prev, loadingOlderRuns: false, olderRunsError: errMsg(e) }));
      }
    })();
  }, [props.threadId, olderCursor]);

  if (!props.threadId) {
    return (
      <Section
        title="Run inspector"
        hint="One run, end to end: the prompt, every tool call, the event timeline, the files it changed, and the tokens it spent."
      >
        <EmptyState title="No conversation selected" hint="Start or pick a chat first — then its runs can be inspected here." />
      </Section>
    );
  }

  return (
    <Section
      title="Run inspector"
      hint="One run, end to end: the prompt, every tool call, the event timeline, the files it changed, and the tokens it spent. Everything shown is what the Gateway reported; a measurement it did not make reads as unknown."
      actions={
        <>
          {isActive(state.record?.status ?? null) && (
            <Badge tone="blue">
              <RefreshCw className="size-3 animate-spin" /> this run is still active
            </Badge>
          )}
          <Btn variant="ghost" onClick={onReload} disabled={state.loadingRun}>
            <RefreshCw className="size-3.5" /> {state.loadingRun ? "Reading…" : "Reload"}
          </Btn>
        </>
      }
    >
      <RunInspectorView
        state={{ ...state, showPicker, threadId: props.threadId }}
        onSelect={onSelect}
        onReload={onReload}
        onQueryChange={onQueryChange}
        onCopyLink={onCopyLink}
      onLoadOlder={onLoadOlder}
      canLoadOlder={olderCursor !== null}
    />
    </Section>
  );
}
