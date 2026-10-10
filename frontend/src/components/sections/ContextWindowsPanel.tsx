/* Context windows panel (Settings → Models).
 *
 * `GET /api/ops/context-windows` is the answer to "how much context does each
 * model actually give me, and how much of it is really usable?" — which the
 * model picker cannot answer, because it reports the *advertised* figure and a
 * provider's advertised window is not the same as the usable one.
 *
 * The panel exists because of one specific confusion: a model can declare
 * 128k and still only offer ~122k of *input*, because the response and the
 * next turn are paid for out of the same window. Showing the declared figure
 * alone invites the operator to size a prompt against a number that will
 * overflow; showing the usable figure alone hides what was reserved. Both
 * travel together, and the reserve is named, so the arithmetic is checkable
 * rather than asserted.
 *
 * Honesty rules, each with a tempting wrong reading:
 *
 * | Server says | Panel shows |
 * | --- | --- |
 * | `reported: false` | the server's own `reason`, and *not reported* for every model |
 * | `models: null` | "the Gateway sent no model list" — not "no models declared" |
 * | `models: []` | "this deployment declares no models", distinct from the above |
 * | `declared_input_window: null` | *not declared*, never `0` — an unmeasured window is not an empty one |
 * | `usable_input_window: null` | *not derivable*, because the size of the room is unknown |
 * | `clamped: true` | amber, naming the floor the reserve was clamped to |
 * | `escalation_candidate: null` | *none larger declared*, distinct from escalation being switched off |
 * | `escalation_enabled: null` | *not reported* — no policy section at all, not "disabled" |
 * | an unrecognised `reason` string | rendered verbatim |
 */
import React, { useCallback, useEffect, useState } from "react";
import { Badge, Btn, EmptyState, ErrorBox, Notice } from "@/components/ui";
import { errMsg } from "@/lib/http";
import { fetchContextWindows, modelWindowView, windowSummaryText, type ContextWindows } from "@/lib/context-window";

export function ContextWindowsPanel() {
  const [windows, setWindows] = useState<ContextWindows | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setWindows(await fetchContextWindows());
    } catch (e) {
      setWindows(null);
      setError(errMsg(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-[11px] text-muted-foreground">
          {windows === null ? "Reading the context-window policy…" : windowSummaryText(windows)}
        </p>
        <Btn variant="ghost" onClick={() => void load()} disabled={loading}>
          {loading ? "Refreshing…" : "Refresh"}
        </Btn>
      </div>

      {error && <ErrorBox message={error} onRetry={() => void load()} />}

      {/* A refused or unread policy is its own state, not an empty catalog. */}
      {!error && windows !== null && !windows.reported && (
        <Notice tone="warn" message={`The Gateway reported no context-window policy (${windows.reason}), so no occupancy figure or exhaustion plan is computed for any thread. Declaring a context_window section in config.yaml enables it.`} />
      )}

      {!error && windows !== null && windows.reported && windows.models === null && (
        <Notice message="The Gateway reported no model list, so no window could be read. That is not the same as an empty catalog." />
      )}

      {!error && windows !== null && windows.models !== null && windows.models.length === 0 && (
        <EmptyState title="This deployment declares no models" hint="There is nothing to report a context window for." />
      )}

      {!error && windows !== null && windows.models !== null && windows.models.length > 0 && (
        <div className="space-y-1.5">
          {/*
            The escalation row is one sentence for the whole panel rather than a
            column per model: an operator deciding whether to declare a larger
            window needs to know whether the runtime is even willing to switch
            models, and that is one policy — never per model.
          */}
          <p className="text-[10px] text-muted-foreground">
            escalation to a larger window:{" "}
            {windows.escalationEnabled === null ? (
              <span className="font-mono">not reported</span>
            ) : windows.escalationEnabled ? (
              <span className="text-emerald-600 dark:text-emerald-400">enabled</span>
            ) : (
              <span className="text-amber-600 dark:text-amber-400">disabled by configuration</span>
            )}
          </p>
          {windows.models.map((r) => {
            const view = modelWindowView(r);
            return (
              <div key={r.name} className="rounded-xl border border-border/60 px-2.5 py-2 text-[11px] space-y-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-semibold break-all">{r.name || "(unnamed)"}</span>
                  {r.declaredInputWindow === null && <Badge tone="gray">window not declared</Badge>}
                  {r.declaredInputWindow !== null && !r.clamped && <Badge tone="green">{view.usableText} usable</Badge>}
                  {r.clamped && <Badge tone="amber">reserve clamped</Badge>}
                </div>
                <div className="text-[10px] text-muted-foreground font-mono">
                  declared {view.declaredText} · usable {view.usableText} · reserved {view.reservedText} · escalates to{" "}
                  {view.escalationText}
                </div>
                {/*
                  The clamp is the one case where the derivation is *worse* than
                  the declaration, so it gets a sentence rather than a colour:
                  the operator over-reserved and the runtime clamped to its
                  floor, which means the window in force is not the one they
                  wrote.
                */}
                {r.clamped && (
                  <p className="text-[10px] text-amber-700 dark:text-amber-300">
                    The response and next-turn reserves exceed this window, so the usable figure was clamped to the configured
                    floor <span className="font-mono">(context_window.minimum_usable_input_window)</span>. Raise the declared
                    window or lower the reserves.
                  </p>
                )}
                <p className="text-[10px] text-muted-foreground/80">
                  reason: <span className="font-mono">{view.reason}</span>
                </p>
              </div>
            );
          })}
        </div>
      )}

      {windows?.notes.map((note) => (
        <p key={note} className="text-[10px] text-muted-foreground">
          {note}
        </p>
      ))}
    </div>
  );
}
