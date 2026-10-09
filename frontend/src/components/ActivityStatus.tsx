"use client";

import React from "react";
import { Activity, Wifi, WifiOff } from "lucide-react";
import type { ActivityState } from "@/lib/activity";
import { formatElapsed, phaseLabel } from "@/lib/activity";
import type { StreamReconnectStatus } from "@/lib/chat-stream";

interface ActivityStatusProps {
  state: ActivityState;
  /** Wall-clock ms since this client saw the run start. `0` renders no timer. */
  elapsedMs: number;
  /**
   * `"No update received for 32s"` once the byte stream has been silent past
   * two heartbeat intervals, otherwise `null`. Client-observed: it describes
   * this connection, never the run.
   */
  silence?: string | null;
  /** Resume attempt reported by the stream transport, not inferred from silence. */
  reconnect?: StreamReconnectStatus | null;
  /** Display name of the bot doing the work, for context below the transcript. */
  actor: string;
}

/**
 * The pinned "something is happening" line shown while a run is in flight.
 *
 * It exists to answer the one question a blank transcript cannot: *is this
 * working or stuck?* So it always animates, always names the phase it
 * observed, and always shows how long it has been — an idle run is never
 * silent, because silence is indistinguishable from a hang.
 *
 * Region discipline matters here: `role="status"` wraps only the first line,
 * so the phase is announced when it changes, while the per-second clock and the
 * per-second silence counter sit outside it and never become screen-reader
 * announcements that fire every tick.
 */
export function ActivityStatus({
  state,
  elapsedMs,
  silence,
  reconnect,
  actor,
}: ActivityStatusProps) {
  const elapsed = formatElapsed(elapsedMs);
  const [online, setOnline] = React.useState<boolean | null>(null);

  React.useEffect(() => {
    const update = () => setOnline(navigator.onLine);
    update();
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    return () => {
      window.removeEventListener("online", update);
      window.removeEventListener("offline", update);
    };
  }, []);

  return (
    <div className="text-xs">
      <div className="flex items-center gap-2 px-4 py-2" role="status">
        <Activity
          className="size-4 shrink-0 animate-spin text-primary"
          aria-hidden="true"
        />

        <span className="min-w-0 truncate">
          <span className="font-semibold text-foreground/90">{actor}</span>
          <span className="text-muted-foreground"> · </span>
          <span aria-live="polite" className="text-muted-foreground">
            {phaseLabel(state)}
          </span>
        </span>

        {state.toolTotal > 0 && (
          <span className="shrink-0 rounded-full bg-muted/60 px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
            {state.toolTotal} tool call{state.toolTotal === 1 ? "" : "s"}
          </span>
        )}

        {elapsed && (
          <span className="ml-auto shrink-0 font-mono text-[11px] tabular-nums text-muted-foreground">
            {elapsed}
          </span>
        )}
      </div>

      {/* Past two missed heartbeats the connection, not the phase, is what
          changed — so this says only the measured number. It never claims the
          run stalled: a long tool call and a dropped stream are not
          distinguishable from one client-side reading. */}
      {silence && (
        <div
          className="flex items-center gap-2 px-4 pb-2 pl-8 text-[11px] text-amber-600 dark:text-amber-400"
          title="Measured from the last byte this browser received on the run's stream, heartbeat comments included. The Gateway writes one every 15 seconds by default (stream_bridge.heartbeat_interval_seconds)."
        >
          <span
            className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-500"
            aria-hidden="true"
          />
          <span className="min-w-0 truncate">{silence}</span>
        </div>
      )}
      {reconnect && (
        <div
          className="flex items-center gap-2 px-4 pb-2 pl-8 text-[11px] text-amber-700 dark:text-amber-300"
          role="status"
          aria-live="polite"
          data-stream-reconnect={`${reconnect.phase}:${reconnect.attempt}`}
        >
          {online === false ? (
            <WifiOff className="size-3.5 shrink-0" aria-hidden="true" />
          ) : (
            <Wifi className="size-3.5 shrink-0" aria-hidden="true" />
          )}
          <span className="font-medium">
            Reconnecting {reconnect.attempt}/{reconnect.maxAttempts}
          </span>
          <span className="text-muted-foreground">
            {online === false
              ? "Waiting for network"
              : reconnect.phase === "waiting"
                ? "Waiting before retry"
                : "Trying to restore the stream"}
          </span>
        </div>
      )}
    </div>
  );
}
