"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  activityBadge,
  ATTENTION_STATES,
  byUrgency,
  claimsBySubject,
  conflictIndex,
  failureTitle,
  fetchGroupTree,
  fetchRoomCoordination,
  healthBadge,
  reclaimAffordance,
  reclaimClaim,
  reconcileRoom,
  releaseClaim,
  roomHeadline,
  type AgentActivity,
  type RoomCoordination,
  type SoftConflict,
  type WorkClaim,
} from "@/lib/group-coordination";
import { Badge, Btn, EmptyState, ErrorBox, inputCls, Notice, StatCard } from "@/components/ui";
import { errMsg } from "@/lib/http";
import { AlertTriangle, Hand, RefreshCw, ShieldAlert, Users } from "lucide-react";

/**
 * Live coordination for one room: who is doing what, and what is stuck.
 *
 * This panel exists because the War Room's other tabs all describe work that
 * already happened. Nothing in the enterprise views, the run list, or the
 * analytics says what the room is doing *right now* — and "nothing has happened
 * yet" and "nothing is happening" are indistinguishable without this read.
 *
 * The layout follows the four rules the model holds:
 *
 * 1. **Two columns, two axes.** The left column is one row per member carrying
 *    both `activity` and `health` as separate badges. The right column is the
 *    claims board, grouped by subject so a contested file is one row rather
 *    than two rows that look unrelated.
 * 2. **A crash is evidence; a hand-off is a decision.** `Reconcile` records
 *    that crashed holders' work is free. `Reclaim` is a peer taking it. They
 *    are two buttons and the UI never collapses them into one "fix" action,
 *    because recording the evidence is always safe and reclaiming is not.
 * 3. **`crashed` looks different from `unresponsive`.** Red versus amber, and
 *    only `crashed` unlocks the reclaim affordance. A slow agent may be
 *    mid-tool-call; reclaiming its work hands live work to a second agent.
 * 4. **A failed read is not an empty room.** Every panel here renders an
 *    `ErrorBox` naming the failure. It never degrades to "no agents, no
 *    claims, all clear".
 */
export function GroupCoordinationPanel(props: { room: string; onRoomChange?: (room: string) => void }) {
  const [rooms, setRooms] = useState<string[]>([]);
  const [snapshot, setSnapshot] = useState<RoomCoordination | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  // Discover rooms so this is a picker rather than a free-text box. A failure
  // here is not fatal: the operator may know the room name and type it, so the
  // panel stays usable and the room list simply stays empty.
  useEffect(() => {
    let live = true;
    fetchGroupTree()
      .then((nodes) => {
        if (!live) return;
        setRooms(nodes.map((n) => n.name).sort());
      })
      .catch(() => {
        if (live) setRooms([]);
      });
    return () => {
      live = false;
    };
  }, []);

  const load = useCallback(async () => {
    if (!props.room) return;
    setLoading(true);
    setError(null);
    try {
      setSnapshot(await fetchRoomCoordination(props.room));
    } catch (err) {
      setSnapshot(null);
      setError(`${failureTitle(err)} — ${errMsg(err)}`);
    } finally {
      setLoading(false);
    }
  }, [props.room]);

  useEffect(() => {
    void load();
  }, [load]);

  const headline = useMemo(() => (snapshot ? roomHeadline(snapshot) : null), [snapshot]);
  const conflicts = useMemo(() => (snapshot ? conflictIndex(snapshot) : new Map<string, SoftConflict>()), [snapshot]);
  const needsAttention = useMemo(
    () => (snapshot ? snapshot.agents.filter((a) => ATTENTION_STATES.includes(a.activity)).length : 0),
    [snapshot],
  );

  const onReconcile = useCallback(async () => {
    if (!props.room) return;
    setBusy("reconcile");
    setReceipt(null);
    setError(null);
    try {
      const result = await reconcileRoom(props.room);
      // Report the announcement count separately from the move count. A crash
      // that was seen but not announced is a real, incomplete outcome, and
      // rounding it to "reconciled" would hide it.
      const moved = result.orphaned.length;
      const crashed = result.crashed.length;
      if (!crashed) {
        setReceipt("No agent in this room reads as crashed, so nothing was moved.");
      } else if (!moved) {
        setReceipt(`${crashed} crashed, but ${crashed === 1 ? "it was" : "they were"} holding no live claim.`);
      } else {
        const announce = moved === result.announced ? "" : ` ${result.announced} of ${moved} announced in the room.`;
        setReceipt(
          `${moved} claim${moved === 1 ? "" : "s"} from ${crashed} crashed ${crashed === 1 ? "agent" : "agents"} ${moved === 1 ? "is" : "are"} now available.${announce}`,
        );
      }
      await load();
    } catch (err) {
      setError(`Reconcile failed — ${errMsg(err)}`);
    } finally {
      setBusy(null);
    }
  }, [props.room, load]);

  const onReclaim = useCallback(
    async (claim: WorkClaim) => {
      const holder = window.prompt(`Which bot is taking over "${claim.subject}"?`);
      if (!holder) return;
      setBusy(claim.claim_id);
      setError(null);
      try {
        // The dedicated reclaim route, not a forced release. The backend
        // refuses this unless the claim is orphaned *and* the holder reads
        // crashed, so a 409 here is the system protecting live work — and it is
        // reported as such rather than as a generic failure.
        const taken = await reclaimClaim(props.room, claim.claim_id, { holder: holder.trim() });
        await load();
        setReceipt(`"${taken.subject}" is now held by ${holder.trim()}.`);
      } catch (err) {
        setError(`Hand-off refused — ${errMsg(err)}`);
      } finally {
        setBusy(null);
      }
    },
    [props.room, load],
  );

  const onRelease = useCallback(
    async (claim: WorkClaim) => {
      setBusy(claim.claim_id);
      setError(null);
      try {
        const result = await releaseClaim(props.room, claim.claim_id, { holder: claim.holder });
        setReceipt(
          result.by === claim.holder
            ? `${claim.holder} released "${claim.subject}".`
            : `${result.by} released "${claim.subject}", which ${result.owner_was} owned.`,
        );
        await load();
      } catch (err) {
        setError(`Release failed — ${errMsg(err)}`);
      } finally {
        setBusy(null);
      }
    },
    [props.room, load],
  );

  const groups = useMemo(() => (snapshot ? claimsBySubject(snapshot.claims) : []), [snapshot]);
  const orphanedCount = snapshot?.orphaned.length ?? 0;

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h3 className="text-sm font-semibold tracking-tight">Live coordination</h3>
          <p className="text-xs text-muted-foreground mt-0.5 max-w-2xl">
            What this room is doing right now, read from the run store and the activity ledger. Activity and health
            are separate questions: an agent can be blocked and still answering.
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {rooms.length > 0 && props.onRoomChange && (
            <select
              aria-label="Room"
              className={`${inputCls} w-auto`}
              value={props.room}
              onChange={(e) => props.onRoomChange?.(e.target.value)}
            >
              {rooms.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          )}
          <Btn variant="ghost" onClick={() => void load()} disabled={loading} title="Re-read the room">
            <RefreshCw className={`size-3 ${loading ? "animate-spin" : ""}`} aria-hidden="true" />
            Refresh
          </Btn>
          <Btn
            variant="ghost"
            onClick={() => void onReconcile()}
            disabled={busy === "reconcile" || !snapshot}
            title="Record that crashed agents' work is free"
          >
            <ShieldAlert className="size-3" aria-hidden="true" />
            Reconcile crashes
          </Btn>
        </div>
      </div>

      {!props.room ? (
        <EmptyState
          title="No room selected"
          hint={
            rooms.length === 0
              ? "No rooms were readable, so this panel cannot watch one. The Group Tree view creates them."
              : "Pick a room to see its live activity and work claims."
          }
        />
      ) : (
        <div className="space-y-4">
          {error && <ErrorBox message={error} onRetry={() => void load()} />}
          {receipt && <Notice message={receipt} />}

          {loading && !snapshot ? (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="h-16 rounded-xl border border-border/60 bg-card/60 animate-pulse" />
              ))}
            </div>
          ) : null}

          {snapshot && headline ? (
            <>
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <StatCard label="In this room" value={headline.primary} sub={headline.caption} />
                <StatCard
                  label="Live claims"
                  value={String(snapshot.live_claim_count)}
                  sub={snapshot.live_claim_count === 1 ? "one subject held" : "subjects held right now"}
                />
                <StatCard
                  label="Contested"
                  value={String(snapshot.conflicts.length)}
                  sub={
                    snapshot.conflicts.some((c) => c.reclaimable)
                      ? "at least one holder crashed"
                      : "no dead holder, so none are free"
                  }
                />
                <StatCard
                  label="Needs a look"
                  value={String(needsAttention)}
                  sub={needsAttention === 0 ? "every member is working or idle" : "crashed, blocked or unresponsive"}
                />
              </div>

              {orphanedCount > 0 && (
                <div className="rounded-xl border border-amber-500/30 bg-amber-500/5 px-3 py-2.5 text-xs flex items-start gap-2">
                  <AlertTriangle className="size-3.5 mt-0.5 shrink-0 text-amber-600" aria-hidden="true" />
                  <span>
                    {orphanedCount} claim{orphanedCount === 1 ? "" : "s"} {orphanedCount === 1 ? "is" : "are"} orphaned
                    {orphanedCount === 1 ? "" : ""} — the holder crashed and the work is recorded as free. Handing it to a peer
                    is still a separate, deliberate step.
                  </span>
                </div>
              )}

              <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
                <ActivityColumn agents={snapshot.agents} />
                <ClaimsColumn
                  room={props.room}
                  groups={groups}
                  conflicts={conflicts}
                  busy={busy}
                  onReclaim={(c) => void onReclaim(c)}
                  onRelease={(c) => void onRelease(c)}
                />
              </div>
            </>
          ) : null}
        </div>
      )}
    </div>
  );
}

/**
 * One row per member, most urgent first.
 *
 * `crashed` leads because it is the only state an operator can act on; `idle`
 * trails because a bot with nothing to do is the least news in the room.
 */
function ActivityColumn(props: { agents: AgentActivity[] }) {
  const ordered = useMemo(() => byUrgency(props.agents), [props.agents]);
  return (
    <div className="space-y-2">
      <h3 className="text-xs font-semibold flex items-center gap-1.5">
        <Users className="size-3.5" aria-hidden="true" />
        Agents
        <span className="text-muted-foreground font-normal">({props.agents.length})</span>
      </h3>
      {props.agents.length === 0 ? (
        <EmptyState title="No members resolved" hint="This room has nobody in its effective roster — not even inherited or rule-matched members." />
      ) : (
        <ul className="space-y-1.5">
          {ordered.map((agent) => (
            <AgentRow key={agent.bot_name} agent={agent} />
          ))}
        </ul>
      )}
    </div>
  );
}

function AgentRow(props: { agent: AgentActivity }) {
  const activity = activityBadge(props.agent);
  const health = healthBadge(props.agent);
  return (
    <li
      className="rounded-xl border border-border/60 bg-card px-3 py-2.5"
      title={`${props.agent.evidence.detail}\n\nSource: ${props.agent.evidence.source} · reason: ${props.agent.evidence.reason}`}
    >
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs font-semibold truncate">{props.agent.bot_name}</span>
        <Badge tone={activity.tone}>{activity.label}</Badge>
        {/* The second axis. Rendered even when it is "unknown", because a
            missing health verdict must not read as a healthy one. */}
        <Badge tone={health.tone} title={props.agent.detail}>
          {health.label}
        </Badge>
        {props.agent.claim_ids.length > 0 && (
          <span className="text-[10px] text-muted-foreground">
            {props.agent.claim_ids.length} claim{props.agent.claim_ids.length === 1 ? "" : "s"}
          </span>
        )}
      </div>
      <p className="text-[11px] text-muted-foreground mt-1 break-words">{props.agent.detail}</p>
      {props.agent.held_paths.length > 0 && (
        <p className="text-[10px] text-muted-foreground mt-1 font-mono break-all">
          {props.agent.held_paths.slice(0, 3).join(" · ")}
          {props.agent.held_paths.length > 3 ? ` +${props.agent.held_paths.length - 3} more` : ""}
        </p>
      )}
    </li>
  );
}

/**
 * Claims grouped by subject.
 *
 * Grouping is the point: two agents claiming one file must read as one row
 * that is contested, not two rows that each look fine. A conflict the backend
 * reported but that has no visible claim row is surfaced explicitly, because
 * "the server says these overlap and I cannot show you why" is a bug in this
 * view, not something to quietly drop.
 */
function ClaimsColumn(props: {
  room: string;
  groups: { subject: string; claims: WorkClaim[] }[];
  conflicts: Map<string, SoftConflict>;
  busy: string | null;
  onReclaim: (claim: WorkClaim) => void;
  onRelease: (claim: WorkClaim) => void;
}) {
  const unresolved = useMemo(
    () => [...props.conflicts.values()].filter((c) => !c.claim_ids.some((id) => props.groups.some((g) => g.claims.some((claim) => claim.claim_id === id)))),
    [props.conflicts, props.groups],
  );

  return (
    <div className="space-y-2">
      <h3 className="text-xs font-semibold flex items-center gap-1.5">
        <Hand className="size-3.5" aria-hidden="true" />
        Work claims
      </h3>
      {props.groups.length === 0 ? (
        <EmptyState
          title="No claims recorded"
          hint="Claims are advisory. Two agents can still claim the same subject — that is reported as a contested row rather than refused."
        />
      ) : (
        <ul className="space-y-1.5">
          {props.groups.map((group) => {
            const conflict = group.claims.map((c) => props.conflicts.get(c.claim_id)).find(Boolean) ?? null;
            return (
              <li
                key={group.subject}
                className={`rounded-xl border px-3 py-2.5 ${
                  conflict ? "border-amber-500/40 bg-amber-500/5" : "border-border/60 bg-card"
                }`}
                title={conflict?.detail ?? undefined}
              >
                <div className="flex items-start gap-2 flex-wrap justify-between">
                  <span className="text-xs font-mono font-semibold break-all min-w-0">{group.subject}</span>
                  {conflict && (
                    <Badge tone={conflict.reclaimable ? "red" : "amber"} title={conflict.detail}>
                      {conflict.reclaimable ? "Free to take" : "Contested"}
                    </Badge>
                  )}
                </div>
                <ul className="mt-1.5 space-y-1">
                  {group.claims.map((claim) => {
                    const affordance = reclaimAffordance(claim, props.conflicts.get(claim.claim_id) ?? conflict);
                    const working = props.busy === claim.claim_id;
                    return (
                      <li key={claim.claim_id} className="flex items-center gap-2 flex-wrap text-[11px]">
                        <span className="font-semibold">{claim.holder}</span>
                        <Badge tone="gray" title={`Claim kind: ${claim.kind}`}>
                          {claim.intent}
                        </Badge>
                        {claim.state !== "active" && (
                          <Badge tone={claim.state === "orphaned" ? "red" : "gray"} title={JSON.stringify(claim.orphan_evidence ?? {})}>
                            {claim.state}
                          </Badge>
                        )}
                        {claim.detail && <span className="text-muted-foreground break-words">{claim.detail}</span>}
                        <span className="ml-auto flex items-center gap-1.5">
                          {claim.live && (
                            <Btn variant="ghost" className="px-2 py-1" disabled={working} onClick={() => props.onRelease(claim)} title="Give this claim up">
                              Release
                            </Btn>
                          )}
                          {affordance.allowed && (
                            <Btn variant="primary" className="px-2 py-1" disabled={working} onClick={() => props.onReclaim(claim)} title={affordance.reason}>
                              {affordance.label}
                            </Btn>
                          )}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              </li>
            );
          })}
        </ul>
      )}

      {unresolved.length > 0 && (
        <div className="rounded-xl border border-destructive/40 bg-destructive/5 px-3 py-2.5 text-xs">
          <p className="font-semibold">{unresolved.length} reported overlap could not be shown</p>
          <ul className="mt-1 space-y-0.5 text-muted-foreground">
            {unresolved.map((c) => (
              <li key={c.claim_ids.join("|")} className="break-words">
                {c.detail}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export default GroupCoordinationPanel;