"use client";

import React, { useEffect, useState } from "react";
import { orgChart, fleetHealth, killSwitchState, setKillSwitch, pauseBot, resumeBot, handoffTask, matchBots, orgEvents } from "@/lib/teamops";
import { BotProfile } from "@/types/bots";
import { Section, EmptyState, ErrorBox, Notice, Btn, Badge, Field, SkeletonList, inputCls } from "@/components/ui";
import { errMsg } from "@/lib/http";
import { OctagonAlert, Play, Pause, ArrowRightLeft, Search, RefreshCw } from "lucide-react";

export function BotOpsSection(props: { bots: BotProfile[]; onRefreshBots: () => void }) {
  const [chart, setChart] = useState<Record<string, unknown> | null>(null);
  const [health, setHealth] = useState<Record<string, unknown> | null>(null);
  const [kill, setKill] = useState<{ active: boolean; detail: string }>({ active: false, detail: "" });
  const [events, setEvents] = useState<Array<Record<string, unknown>>>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [handoff, setHandoff] = useState({ task_id: "", from_bot: "", to_bot: "", objective: "" });
  const [matchQuery, setMatchQuery] = useState("");
  const [matches, setMatches] = useState<Array<Record<string, unknown>>>([]);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const [c, h, k, e] = await Promise.all([orgChart(), fleetHealth(), killSwitchState(), orgEvents(20)]);
      setChart(c);
      setHealth(h);
      setKill(k);
      setEvents(e);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const flash = (m: string) => {
    setNotice(m);
    window.setTimeout(() => setNotice(null), 4000);
  };

  const act = async (fn: () => Promise<void>, ok: string) => {
    try {
      await fn();
      flash(ok);
      props.onRefreshBots();
      await load();
    } catch (e) {
      setError(errMsg(e));
    }
  };

  const onMatch = async () => {
    if (!matchQuery.trim()) return;
    try {
      setMatches(await matchBots(matchQuery.trim()));
    } catch (e) {
      setError(errMsg(e));
    }
  };

  const str = (v: unknown) => (typeof v === "string" ? v : "");

  return (
    <div className="flex-1 overflow-y-auto px-4 sm:px-6 py-5 w-full">
      <div className="max-w-6xl mx-auto space-y-4">
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div>
            <h2 className="text-base font-semibold tracking-tight">Team operations</h2>
            <p className="text-xs text-muted-foreground mt-0.5 max-w-2xl">
              Run the bot team like a manager: pause someone, move work between bots, find the best bot for a job, and review the audit trail.
            </p>
          </div>
          <Btn variant="ghost" onClick={load}>
            <RefreshCw className="size-3.5" /> Refresh
          </Btn>
        </div>

        {error && <ErrorBox message={error} onRetry={load} />}
        {notice && <Notice message={notice} />}

        {/* Safety controls */}
        <div className={`rounded-2xl border p-4 ${kill.active ? "border-destructive bg-destructive/5" : "border-border/60 bg-card"}`}>
          <div className="flex items-center gap-2 flex-wrap">
            <OctagonAlert className={`size-4 ${kill.active ? "text-destructive" : "text-muted-foreground"}`} />
            <p className="text-xs font-semibold flex-1 min-w-40">
              Emergency stop {kill.active ? "is ENGAGED — all bots halted" : "is off — team working normally"}
            </p>
            <Badge tone={kill.active ? undefined : "green"}>{kill.active ? "stopped" : "running"}</Badge>
            {kill.active ? (
              <Btn onClick={() => act(() => setKillSwitch(false, "Released from UI"), "Team resumed.")}>
                <Play className="size-3.5" /> Release stop
              </Btn>
            ) : (
              <Btn
                variant="danger"
                onClick={() => window.confirm("HALT the whole bot team right now?") && act(() => setKillSwitch(true, "Emergency stop from UI"), "Team halted.")}
              >
                Halt everything
              </Btn>
            )}
          </div>
        </div>

        {loading ? (
          <SkeletonList rows={4} />
        ) : (
          <>
            {/* Per-bot pause / resume */}
            <div className="rounded-2xl border border-border/60 bg-card p-4">
              <p className="text-xs font-semibold mb-2">Pause / resume ({props.bots.length})</p>
              {props.bots.length === 0 ? (
                <EmptyState title="No bots" hint="Bot profiles load from the Bots tab." />
              ) : (
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
                  {props.bots.map((b) => (
                    <div key={b.name} className="flex items-center gap-2 rounded-xl bg-muted/40 px-2.5 py-2">
                      <span className="text-base">{b.avatar || "🤖"}</span>
                      <span className="text-[11px] font-semibold flex-1 truncate">{b.display_name || b.name}</span>
                      <Badge tone={b.status === "active" ? "green" : b.status === "paused" ? "amber" : "gray"}>{b.status}</Badge>
                      {b.status === "paused" ? (
                        <button type="button" onClick={() => act(() => resumeBot(b.name), `${b.name} resumed.`)} className="p-1.5 rounded-lg hover:bg-muted" title={`Resume ${b.name}`}>
                          <Play className="size-3.5" />
                        </button>
                      ) : (
                        <button type="button" onClick={() => window.confirm(`Pause ${b.name}?`) && act(() => pauseBot(b.name, "Paused from UI"), `${b.name} paused.`)} className="p-1.5 rounded-lg hover:bg-muted" title={`Pause ${b.name}`}>
                          <Pause className="size-3.5" />
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
              {/* Handoff */}
              <div className="rounded-2xl border border-border/60 bg-card p-4 space-y-2.5">
                <p className="text-xs font-semibold inline-flex items-center gap-1.5">
                  <ArrowRightLeft className="size-3.5 text-primary" /> Move work between bots
                </p>
                <Field label="Task ID">
                  <input value={handoff.task_id} onChange={(e) => setHandoff({ ...handoff, task_id: e.target.value })} placeholder="task-123" className={`${inputCls} font-mono`} />
                </Field>
                <div className="grid grid-cols-2 gap-2">
                  <Field label="From">
                    <select value={handoff.from_bot} onChange={(e) => setHandoff({ ...handoff, from_bot: e.target.value })} className={inputCls}>
                      <option value="">Pick…</option>
                      {props.bots.map((b) => (
                        <option key={b.name} value={b.name}>{b.display_name || b.name}</option>
                      ))}
                    </select>
                  </Field>
                  <Field label="To">
                    <select value={handoff.to_bot} onChange={(e) => setHandoff({ ...handoff, to_bot: e.target.value })} className={inputCls}>
                      <option value="">Pick…</option>
                      {props.bots.map((b) => (
                        <option key={b.name} value={b.name}>{b.display_name || b.name}</option>
                      ))}
                    </select>
                  </Field>
                </div>
                <Field label="What needs doing?">
                  <input value={handoff.objective} onChange={(e) => setHandoff({ ...handoff, objective: e.target.value })} placeholder="Finish the API tests…" className={inputCls} />
                </Field>
                <Btn
                  onClick={() => act(() => handoffTask({ task_id: handoff.task_id.trim(), from_bot: handoff.from_bot, to_bot: handoff.to_bot, objective: handoff.objective.trim() }), "Work handed over.")}
                  disabled={!handoff.task_id.trim() || !handoff.from_bot || !handoff.to_bot || !handoff.objective.trim()}
                >
                  Hand over
                </Btn>
              </div>

              {/* Best-bot finder */}
              <div className="rounded-2xl border border-border/60 bg-card p-4 space-y-2.5">
                <p className="text-xs font-semibold inline-flex items-center gap-1.5">
                  <Search className="size-3.5 text-primary" /> Who's best for a job?
                </p>
                <Field label="Describe the job" hint="The server ranks bots by capability match.">
                  <div className="flex gap-2">
                    <input value={matchQuery} onChange={(e) => setMatchQuery(e.target.value)} onKeyDown={(e) => e.key === "Enter" && onMatch()} placeholder="Review a pull request for security issues…" className={inputCls} />
                    <Btn onClick={onMatch} disabled={!matchQuery.trim()}>Find</Btn>
                  </div>
                </Field>
                {matches.length > 0 && (
                  <div className="space-y-1.5">
                    {matches.slice(0, 5).map((m, i) => (
                      <div key={i} className="rounded-xl bg-muted/40 px-3 py-2 text-[11px]">
                        <span className="font-semibold">#{i + 1} {str(m.bot) || str(m.name) || "candidate"}</span>
                        {typeof m.score === "number" && <span className="font-mono text-muted-foreground"> — score {m.score.toFixed(2)}</span>}
                        {str(m.reason) && <p className="text-muted-foreground mt-0.5">{str(m.reason)}</p>}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>

            {/* Org chart + health (server-rendered data, shown readably) */}
            {(chart || health) && (
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                {health && (
                  <div className="rounded-2xl border border-border/60 bg-card p-4">
                    <p className="text-xs font-semibold mb-2">Fleet health (server)</p>
                    <pre className="text-[11px] font-mono whitespace-pre-wrap max-h-56 overflow-y-auto rounded-xl bg-muted/40 p-3">{JSON.stringify(health, null, 2).slice(0, 4000)}</pre>
                  </div>
                )}
                {chart && (
                  <div className="rounded-2xl border border-border/60 bg-card p-4">
                    <p className="text-xs font-semibold mb-2">Organization chart (server)</p>
                    <pre className="text-[11px] font-mono whitespace-pre-wrap max-h-56 overflow-y-auto rounded-xl bg-muted/40 p-3">{JSON.stringify(chart, null, 2).slice(0, 4000)}</pre>
                  </div>
                )}
              </div>
            )}

            {/* Audit trail */}
            <div className="rounded-2xl border border-border/60 bg-card p-4">
              <p className="text-xs font-semibold mb-2">Recent team events ({events.length})</p>
              {events.length === 0 ? (
                <p className="text-[11px] text-muted-foreground">No events recorded yet.</p>
              ) : (
                <div className="space-y-1.5 max-h-72 overflow-y-auto">
                  {events.map((e, i) => (
                    <div key={i} className="text-[11px] rounded-lg bg-muted/40 px-2.5 py-1.5 flex gap-2 flex-wrap">
                      <Badge tone="blue">{str(e.event_type) || str(e.type) || "event"}</Badge>
                      <span className="flex-1 min-w-40">{str(e.summary) || str(e.description) || JSON.stringify(e).slice(0, 160)}</span>
                      {str(e.created_at) && <span className="text-muted-foreground">{new Date(str(e.created_at)).toLocaleString()}</span>}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
