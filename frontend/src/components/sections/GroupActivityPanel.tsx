"use client";

/**
 * Live activity for a group room: who is working, on what, and who stopped.
 *
 * The reason this is a separate surface from the presence list is that the two
 * answer different questions. Presence answers "who is enrolled and what
 * lifecycle word their registry row holds"; this answers "what is each agent
 * doing right now, and did it die?". Collapsing them is what made a crashed
 * agent read as `idle` — which is also what a cleanly finished agent reads as.
 *
 * The rules this component must keep, from `lib/group-activity.ts`:
 *
 * - `crashed` and `unresponsive` are visually distinct. Both being red would
 *   re-create the original lie one layer up, because `unresponsive` is often an
 *   agent inside a long tool call, which is fine.
 * - A crashed agent keeps its row. Hiding it would make its work vanish, which
 *   is the failure this whole feature exists to stop.
 * - A failed read names its reason. It never renders as "0 working" or "everyone
 *   idle", because a broken read and an empty room are opposite claims.
 * - An unfamiliar state word renders verbatim.
 */

import { AlertTriangle, FileCode2, Hand, RefreshCw } from "lucide-react";

import {
  activityHeadline,
  activityTone,
  availableSubjects,
  conflictRows,
  isKnownActivity,
  membershipHeadline,
  sortByUrgency,
  type AgentActivity,
  type RoomActivity,
  type SoftConflict,
} from "@/lib/group-activity";

/**
 * One dot per state. `warn` is deliberately shared by `blocked` and
 * `unresponsive` — both mean "not working, and something is off" — while
 * `bad` is reserved for a confirmed crash.
 */
const DOT: Record<string, string> = {
  busy: "bg-emerald-500",
  ok: "bg-border",
  warn: "bg-amber-500",
  bad: "bg-red-500",
  off: "bg-muted-foreground/40",
  unknown: "bg-muted-foreground/30",
};

const TEXT: Record<string, string> = {
  busy: "text-foreground",
  ok: "text-muted-foreground",
  warn: "text-amber-600 dark:text-amber-400",
  bad: "text-red-600 dark:text-red-400",
  off: "text-muted-foreground",
  unknown: "text-muted-foreground",
};

function dot(tone: string) {
  return `size-1.5 shrink-0 rounded-full ${DOT[tone] ?? DOT.unknown}`;
}

/** What one row says about itself. Verbatim for a word we do not know. */
function stateText(activity: string): string {
  return isKnownActivity(activity) ? activity : `${activity} (not a state this build knows)`;
}

function elapsedLabel(since: number | null, now: number): string | null {
  if (since === null) return null;
  const seconds = Math.max(0, Math.round((now - since) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  return `${Math.round(minutes / 60)}h`;
}

function ConflictRow({ conflict }: { conflict: SoftConflict }) {
  return (
    <li className="flex items-start gap-1.5 text-[11px]">
      <AlertTriangle
        className={`mt-0.5 size-3 shrink-0 ${conflict.reclaimable ? "text-amber-500" : "text-orange-500"}`}
        aria-hidden="true"
      />
      <span className="min-w-0">
        <span className="font-mono">{conflict.subject}</span>
        <span className="text-muted-foreground"> — {conflict.holders.map((h) => `@${h}`).join(", ")}</span>
        {conflict.reclaimable ? (
          <span className="text-amber-600 dark:text-amber-400">
            {" "}
            · @{conflict.dead_holder} is gone, so this is available to take
          </span>
        ) : null}
      </span>
    </li>
  );
}

export function GroupActivityPanel({
  snapshot,
  error,
  onRefresh,
  busy,
}: {
  snapshot: RoomActivity | null;
  error: string | null;
  onRefresh: () => void;
  busy: boolean;
}) {
  // A read that has not landed yet, and a read that failed, are different
  // states and both are different from an empty room.
  const pending = snapshot === null && !error;

  return (
    <section className="mt-3">
      <p className="text-[11px] font-bold mb-1.5 flex items-center gap-1.5">
        <FileCode2 className="size-3" aria-hidden="true" />
        Live activity
        <span className="font-normal text-muted-foreground">
          · {snapshot ? membershipHeadline(snapshot) : error ? "membership not reported" : "reading…"}
        </span>
        <button
          type="button"
          onClick={onRefresh}
          disabled={busy}
          className="ml-auto inline-flex items-center gap-1 rounded-md px-1 py-0.5 text-[10px] text-muted-foreground hover:bg-accent disabled:opacity-50"
          aria-label="Refresh live activity"
        >
          <RefreshCw className={`size-3 ${busy ? "animate-spin" : ""}`} aria-hidden="true" />
          {busy ? "Refreshing…" : "Refresh"}
        </button>
      </p>

      {error ? (
        <p className="text-[11px] text-red-600 dark:text-red-400">
          Activity could not be read — who is working is unknown, not empty. ({error})
        </p>
      ) : pending ? (
        <p className="text-[11px] text-muted-foreground">Reading who is working…</p>
      ) : (
        <>
          <p className="text-[11px] text-muted-foreground mb-1">{activityHeadline(snapshot)}</p>
          {snapshot && snapshot.agents.length > 0 ? (
            <ul className="space-y-1">
              {sortByUrgency(snapshot.agents).map((agent) => (
                <ActivityRow key={agent.bot_name} agent={agent} />
              ))}
            </ul>
          ) : null}
          {snapshot && snapshot.conflicts.length > 0 ? (
            <>
              <p className="text-[11px] font-bold mt-2.5 mb-1 flex items-center gap-1.5">
                <AlertTriangle className="size-3 text-orange-500" aria-hidden="true" />
                Overlapping claims
              </p>
              <ul className="space-y-1">
                {conflictRows(snapshot).map((conflict) => (
                  <ConflictRow key={`${conflict.subject}:${conflict.holders.join(",")}`} conflict={conflict} />
                ))}
              </ul>
            </>
          ) : null}
          {snapshot && availableSubjects(snapshot).length > 0 ? (
            <p className="text-[11px] text-amber-600 dark:text-amber-400 mt-1.5 flex items-start gap-1.5">
              <Hand className="mt-0.5 size-3 shrink-0" aria-hidden="true" />
              <span>
                Unclaimed after a stop: <span className="font-mono">{availableSubjects(snapshot).join(", ")}</span>. Check the
                unfinished work before assuming it is intact.
              </span>
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}

function ActivityRow({ agent }: { agent: AgentActivity }) {
  const tone = activityTone(agent.activity);
  const held = agent.held_paths.length > 0 ? agent.held_paths.join(", ") : null;
  const crashed = agent.activity === "crashed";
  return (
    <li className="flex items-start gap-1.5 text-[11px]">
      <span className={`${dot(tone)} mt-1`} aria-hidden="true" />
      <span className="min-w-0 flex-1">
        <span className="font-medium">{agent.bot_name}</span>
        <span className={`ml-1.5 ${TEXT[tone] ?? TEXT.unknown}`}>{stateText(agent.activity)}</span>
        {agent.health ? <span className="text-muted-foreground"> · health {agent.health}</span> : null}
        {held ? (
          <>
            <span className="text-muted-foreground"> · </span>
            <span className="font-mono break-all">{held}</span>
          </>
        ) : null}
        {agent.detail ? <span className="block text-muted-foreground">{agent.detail}</span> : null}
        {crashed && agent.evidence?.run?.stop_reason ? (
          <span className="block text-red-600 dark:text-red-400">
            stopped: <span className="font-mono">{agent.evidence.run.stop_reason}</span>
            {agent.evidence.run.error ? ` — ${agent.evidence.run.error}` : ""}
          </span>
        ) : null}
      </span>
    </li>
  );
}

export default GroupActivityPanel;
