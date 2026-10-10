"use client";

import React from "react";
import { RefreshCw, Wifi, WifiOff } from "lucide-react";
import type { ThreadNetworkWaits } from "@/lib/network-wait";
import { fetchThreadNetworkWaits } from "@/lib/network-wait";
import { recheckConnectivity } from "@/lib/network";
import { DURATION_UNMEASURED, networkWaitTimeline } from "@/lib/network-wait-view";

/**
 * The in-chat network bubble.
 *
 * It answers one question a blank transcript cannot: *is the work still going,
 * or is it waiting for the internet?* A run that parks on a dead link is alive
 * and durable — `alpha.runtime.sessions` calls the state `waiting_network`, not
 * `failed` — and the durable-runtime contract is precisely that an internet
 * outage must not become a task failure. That guarantee is worthless if the
 * person watching cannot tell it apart from a hang, so the bubble states it in
 * words beside a clock that is still ticking.
 *
 * It renders one bubble per recorded park, newest first, with both timestamps:
 * when the link died, and when the wait ended (or that it has not).
 *
 * ## Why it polls rather than waiting for an event
 *
 * The park is recorded by the **backend**, on a run that already died with a
 * transport error, and the resume is performed by the recovery service on a
 * timer. There is no run-scoped stream to attach to by then — the run is
 * terminal. So the honest read is a bounded poll of the thread's own timeline,
 * which is also what makes the bubble survive a page reload: the row is
 * durable, so a chat reopened tomorrow still shows the outage it survived.
 *
 * `POLL_MS` is deliberately faster than the backend's recovery cadence
 * (`network_wait.poll_interval_seconds`, 30s by default) because the open-wait
 * counter ticks every second regardless and a stale bubble would look broken.
 *
 * **Polling does not stop when nothing is parked, and that is load-bearing.**
 * A tempting optimisation is to stop once every park is settled, on the argument
 * that the timeline "cannot change without a new run". It can: the next run in
 * this thread is exactly the thing that parks, and the bubble has to be on screen
 * while it is happening — a bubble that only materialises after a reload is not
 * the live wait notice this exists to be. So the interval runs for the life of the
 * mounted thread. It is one cheap, bounded, owner-scoped read of a per-thread row,
 * and the alternative — missing the wait entirely — is the failure mode that
 * matters here.
 */

const POLL_MS = 5_000;

export function NetworkWaitBubbles({ threadId }: { threadId: string | null }) {
  const [data, setData] = React.useState<ThreadNetworkWaits | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [rechecking, setRechecking] = React.useState(false);
  const [nowMs, setNowMs] = React.useState(() => Date.now());

  // A thread change invalidates the previous thread's timeline; keeping it would
  // show one conversation's outage inside another's, which is the same class of
  // bug as a stale thread's messages surviving navigation.
  React.useEffect(() => {
    setData(null);
    setError(null);
  }, [threadId]);

  const load = React.useCallback(async () => {
    if (!threadId) return;
    try {
      setData(await fetchThreadNetworkWaits(threadId));
      setError(null);
    } catch (err) {
      // Kept as an error rather than resolved to an empty timeline: "this chat
      // never lost its internet" and "we could not read whether it did" are
      // opposite answers, and the bubble exists to answer the first honestly.
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [threadId]);

  React.useEffect(() => {
    if (!threadId) return;
    void load();
    const id = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(id);
  }, [threadId, load]);

  // The open-wait counter is client-observed, so it needs a tick of its own.
  // Region discipline mirrors ActivityStatus: the clock sits outside
  // `role="status"` so a screen reader is not interrupted every second.
  //
  // Keyed on the BOOLEAN, not on `data.open_wait`. The poll returns a freshly
  // parsed object every time, so an object-typed dependency would tear down and
  // rebuild this interval on every poll and make the counter stutter.
  const hasOpenWait = data?.open_wait != null;
  React.useEffect(() => {
    if (!hasOpenWait) return;
    setNowMs(Date.now());
    const id = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [hasOpenWait]);

  const bubbles = networkWaitTimeline(data, error !== null, nowMs);
  if (bubbles.length === 0) return null;

  return (
    <div className="space-y-1.5 px-4 py-2 text-xs" data-network-waits={bubbles.length}>
      {bubbles.map((bubble, index) => (
        <div
          key={index}
          role="status"
          aria-live={bubble.kind === "waiting" ? "polite" : "off"}
          className={toneClass(bubble.tone)}
          data-network-wait={bubble.kind}
          title={bubble.title2}
        >
          <div className="flex items-start gap-2">
            <span className="mt-0.5 shrink-0">
              {bubble.kind === "waiting" || bubble.kind === "unavailable" ? (
                <WifiOff className="size-3.5 shrink-0" aria-hidden="true" />
              ) : bubble.kind === "gave_up" ? (
                <WifiOff className="size-3.5 shrink-0" aria-hidden="true" />
              ) : (
                <Wifi className="size-3.5 shrink-0" aria-hidden="true" />
              )}
            </span>

            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className="font-semibold text-foreground/90">{bubble.title}</span>

                {/* Both timestamps are shown whenever the server sent them: the
                    outage's shape is the pair, and printing only one would lose
                    the duration the user came to read. */}
                <span className="font-mono text-[11px] tabular-nums text-muted-foreground">
                  {bubble.lostAt ?? "time not reported"}
                  {bubble.backAt ? ` → ${bubble.backAt}` : " → waiting"}
                </span>

                {bubble.waited === null && bubble.kind === "waiting" ? (
                  <span className="text-[11px] text-muted-foreground">{DURATION_UNMEASURED}</span>
                ) : bubble.waited ? (
                  <span className="rounded-full bg-muted/60 px-1.5 py-0.5 font-mono text-[10px] tabular-nums text-muted-foreground">
                    {bubble.waited}
                  </span>
                ) : null}

                {bubble.kind === "waiting" && !bubble.unbounded ? (
                  <span className="rounded-full bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-medium text-amber-700 dark:text-amber-300">
                    bounded by this deployment
                  </span>
                ) : null}
              </div>

              <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">{bubble.detail}</p>
            </div>
          </div>
        </div>
      ))}

      {error && (
        <div className="flex items-center gap-2 pl-8 text-[11px] text-muted-foreground">
          <span className="min-w-0 truncate">
            The outage timeline could not be read: {error}
          </span>
          <button
            type="button"
            onClick={async () => {
              // The recheck measures the link, not the timeline, so the timeline
              // is re-read after it. Nothing is painted from the click.
              setRechecking(true);
              try {
                await recheckConnectivity();
                await load();
              } catch {
                /* the re-read below surfaces the real reason */
              } finally {
                setRechecking(false);
              }
            }}
            disabled={rechecking}
            className="inline-flex shrink-0 items-center gap-1 rounded-md border border-border/60 px-1.5 py-0.5 text-[10px] disabled:opacity-50"
            aria-label="Re-check the internet connection"
          >
            <RefreshCw className={`size-3 ${rechecking ? "animate-spin" : ""}`} aria-hidden="true" />
            {rechecking ? "Checking…" : "Retry"}
          </button>
        </div>
      )}
    </div>
  );
}

function toneClass(tone: "amber" | "green" | "red" | "gray"): string {
  switch (tone) {
    case "amber":
      return "rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2";
    case "green":
      return "rounded-lg border border-emerald-500/20 bg-emerald-500/5 px-3 py-2";
    case "red":
      return "rounded-lg border border-red-500/30 bg-red-500/5 px-3 py-2";
    default:
      return "rounded-lg border border-border/60 bg-muted/20 px-3 py-2";
  }
}
