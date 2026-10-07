"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  activityBadge,
  ASSIGNMENT_INTENTS,
  ASSIGNMENT_KINDS,
  assignmentProblems,
  assignmentReceipt,
  assignmentWarnings,
  ATTENTION_STATES,
  byUrgency,
  claimExpiryView,
  claimsBySubject,
  conflictIndex,
  createClaim,
  DETAIL_MAX_LENGTH,
  emptyAssignmentDraft,
  failureTitle,
  fetchGroupTree,
  fetchRoomCoordination,
  healthBadge,
  lastReadView,
  LIVE_POLL_MS,
  reclaimAffordance,
  reclaimClaim,
  reconcileRoom,
  releaseClaim,
  roomHeadline,
  TTL_MAX_SECONDS,
  type AssignmentDraft,
  type AgentActivity,
  type RoomCoordination,
  type SoftConflict,
  type WorkClaim,
} from "@/lib/group-coordination";
import { Badge, Btn, EmptyState, ErrorBox, inputCls, Notice, StatCard } from "@/components/ui";
import { errMsg } from "@/lib/http";
import { AlertTriangle, Hand, RefreshCw, Send, ShieldAlert, Users } from "lucide-react";

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
 * 5. **Watching work and starting work were not the same gap.** The panel
 *    could release and reclaim a claim, but the one verb an operator needs —
 *    *assign* — had no control, even though `POST /{name}/claims` and the
 *    client for it both existed. The form below closes that: it writes a real
 *    claim and then re-reads, so the board and the receipt describe the same
 *    server-confirmed row.
 * 6. **"Live" is a claim about the last successful read, not about the
 *    toggle.** The interval keeps firing while reads fail, so a green LIVE dot
 *    over a five-minute-old snapshot would be the exact lie `lastReadView`
 *    exists to catch. A failed poll keeps the board it already has and says
 *    which age it is showing, rather than blanking a room that was fine one
 *    interval ago.
 */
export function GroupCoordinationPanel(props: { room: string; onRoomChange?: (room: string) => void }) {
  const [rooms, setRooms] = useState<string[]>([]);
  const [snapshot, setSnapshot] = useState<RoomCoordination | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  // Live re-read state. `lastReadAt` only advances on a *successful* read, so
  // the freshness chip reports the age of the data and never the age of the
  // last attempt. `pollError` is kept apart from `error`: the first means "the
  // board you are looking at is the last good one", the second means "we have
  // nothing to show".
  const [live, setLive] = useState(true);
  const [lastReadAt, setLastReadAt] = useState<number | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);

  // A 1s clock so lease countdowns and the freshness chip move without waiting
  // for the next poll. Only ticks while there is a board to describe.
  const [now, setNow] = useState(() => Date.now());

  // The assignment draft. Separate from `receipt` so a refused write can never
  // leave a half-cleared form behind.
  const [draft, setDraft] = useState<AssignmentDraft>(emptyAssignmentDraft);
  const [assigning, setAssigning] = useState(false);
  const [assignError, setAssignError] = useState<string | null>(null);

  // Discover rooms so this is a picker rather than a free-text box. A failure
  // here is not fatal: the operator may know the room name and type it, so the
  // panel stays usable and the room list simply stays empty.
  useEffect(() => {
    let active = true;
    fetchGroupTree()
      .then((nodes) => {
        if (!active) return;
        setRooms(nodes.map((n) => n.name).sort());
      })
      .catch(() => {
        if (active) setRooms([]);
      });
    return () => {
      active = false;
    };
  }, []);

  // Land on a real room rather than an empty panel. The first visit used to
  // stop at "No room selected" while holding the very list it would have
  // picked from — a picker that declines to pick. This only ever *adds* a
  // default: when rooms cannot be discovered the free-text path is unchanged.
  useEffect(() => {
    if (props.room || rooms.length === 0 || !props.onRoomChange) return;
    props.onRoomChange(rooms[0]);
  }, [rooms, props.room, props.onRoomChange]);

  // The room the panel is *currently* watching, and whether a read is in
  // flight. Both are refs rather than state on purpose: the poll fires on an
  // interval, so it has to see a lock the moment it is taken, not after React
  // has re-rendered. `loadingRef` is what stops a 60-second read under a
  // five-second interval from stacking twelve requests — and, worse, from
  // settling out of order, which is how a stale failure can end up displayed
  // beside a fresh "read 6s ago".
  const roomRef = useRef(props.room);
  const loadingRef = useRef(false);

  /**
   * Read the room.
   *
   * The room comes from `roomRef`, not from the closure, so this function is
   * stable and there is exactly one of it. That is what lets the `finally`
   * below re-read after a room change: a closure bound to the old room would
   * simply fetch the room we just left.
   *
   * The loud form clears the snapshot on failure because that is what runs
   * when the *room* changes: keeping it would render the previous room's
   * agents under the new room's name. The quiet form is the poll — it keeps
   * the board it already has and reports the failure separately, because
   * blanking a room that was fine one interval ago is worse than disclosing
   * that the newest read is the oldest one.
   */
  const load = useCallback(async (opts: { quiet?: boolean } = {}) => {
    const room = roomRef.current;
    if (!room || loadingRef.current) return;
    loadingRef.current = true;
    setLoading(true);
    if (!opts.quiet) setError(null);
    setPollError(null);
    try {
      const next = await fetchRoomCoordination(room);
      // The operator moved to another room while this read was in flight.
      // Applying it would paint one room's agents and claims under another
      // room's name, which is a fabricated roster — drop it instead, and let
      // the `finally` below fetch wherever the panel now points.
      if (roomRef.current !== room) return;
      setSnapshot(next);
      setLastReadAt(Date.now());
      if (!opts.quiet) setError(null);
      else setPollError(null);
    } catch (err) {
      if (roomRef.current !== room) return;
      const why = `${failureTitle(err)} — ${errMsg(err)}`;
      if (opts.quiet) {
        setPollError(why);
      } else {
        setSnapshot(null);
        setError(why);
      }
    } finally {
      loadingRef.current = false;
      setLoading(false);
      // A room change that arrived mid-read would otherwise leave the new
      // room waiting on a lock nobody is going to release for it.
      if (roomRef.current !== room) void load();
    }
  }, []);

  // The room ref is updated *before* the read effect below runs — both are
  // declared in this order so React's in-declaration-order effect execution
  // guarantees it. Reaching the read before the ref would fetch the room the
  // panel just left.
  useEffect(() => {
    roomRef.current = props.room;
    // Changing rooms invalidates whatever is on screen. A snapshot left over
    // from the previous room would read as this room's roster, and
    // `lastReadAt` would describe a room we are no longer watching.
    setSnapshot(null);
    setLastReadAt(null);
    setPollError(null);
    setError(null);
  }, [props.room]);

  useEffect(() => {
    void load();
  }, [props.room, load]);

  // The live re-read.
  //
  // Deliberately NOT gated on `document.visibilityState`. This surface is a
  // war room: an operator who switches away for ten minutes and comes back
  // wants the room they are looking at to be the room that exists, not the
  // room that existed. Skipping hidden tabs buys a bounded amount of local
  // work and costs exactly that. The freshness chip is what keeps this honest
  // either way — if reads do start failing, it ages into `stale` and says so
  // while the toggle stays on.
  //
  // Coming back to a tab that was hidden for a long stretch gets an immediate
  // catch-up read rather than waiting out the remainder of an interval.
  useEffect(() => {
    if (!live || !props.room) return;
    const id = window.setInterval(() => void load({ quiet: true }), LIVE_POLL_MS);
    const onVisible = () => {
      if (document.visibilityState === "visible") void load({ quiet: true });
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [live, props.room, load]);

  const hasSnapshot = snapshot != null;
  useEffect(() => {
    if (!hasSnapshot) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [hasSnapshot]);

  const freshness = useMemo(() => lastReadView(lastReadAt, now), [lastReadAt, now]);
  const roster = useMemo(() => (snapshot ? snapshot.agents.map((a) => a.bot_name) : []), [snapshot]);
  const problems = useMemo(() => assignmentProblems(draft), [draft]);
  const warnings = useMemo(() => assignmentWarnings(draft, roster), [draft, roster]);

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

  /**
   * Assign work to an agent.
   *
   * Two rules this handler is built around. The receipt is written from the
   * *server's* response and only after the re-read that follows it, so the
   * sentence and the board underneath it describe the same confirmed row —
   * never the draft the operator typed. And the draft is only cleared on
   * success: a 422 leaves every field exactly where it was, because a form
   * that empties itself on failure loses the input the operator has to fix.
   */
  const onAssign = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (!props.room || problems.length > 0 || assigning) return;
      setAssigning(true);
      setAssignError(null);
      try {
        const created = await createClaim(props.room, {
          holder: draft.holder.trim(),
          kind: draft.kind as WorkClaim["kind"],
          subject: draft.subject.trim(),
          intent: draft.intent as WorkClaim["intent"],
          detail: draft.detail.trim(),
          ttl_seconds: draft.ttl_seconds,
        });
        // Keep the holder: assigning three subjects to one bot is the common
        // case, and re-typing it each time is friction with no honesty in it.
        setDraft({ ...emptyAssignmentDraft(), holder: draft.holder });
        setReceipt(assignmentReceipt(created, Date.now() / 1000));
        await load();
      } catch (err) {
        // The Gateway's own 422 detail lands here verbatim — it names the
        // field and the allowed values, which is more than "HTTP 422".
        setAssignError(errMsg(err));
      } finally {
        setAssigning(false);
      }
    },
    [props.room, problems.length, assigning, draft, load],
  );

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

          {/* Freshness is reported from the age of the last *successful* read,
              so this chip can never claim a recency the data does not have. A
              stale board says so even while auto-refresh is switched on. */}
          <Badge
            tone={freshness.tone}
            title={
              lastReadAt == null
                ? "No successful read has completed for this room yet"
                : `Last successful read ${new Date(lastReadAt).toLocaleTimeString()}`
            }
          >
            {live && !freshness.stale ? "● Live · " : ""}
            {freshness.label}
          </Badge>

          <label
            className="inline-flex items-center gap-1.5 rounded-lg border border-border/60 px-2 py-1.5 text-[11px] text-muted-foreground hover:text-foreground cursor-pointer select-none"
            title={`Re-read this room every ${LIVE_POLL_MS / 1000}s, in any tab visibility, plus one immediately when the tab becomes visible again. A read already in flight is never stacked on top of.`}
          >
            <input
              type="checkbox"
              className="size-3 accent-primary"
              checked={live}
              onChange={(e) => setLive(e.target.checked)}
            />
            Auto-refresh
          </label>

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
          {/* A failed *poll* is not a failed read: the board below is the last
              one the server confirmed, and this says so instead of blanking it. */}
          {pollError && (
            <Notice
              tone="warn"
              message={`Auto-refresh could not re-read this room — ${pollError}. The board still shows ${
                lastReadAt == null ? "no confirmed read" : `the read from ${freshness.label.replace(/^read /, "")}`
              }.`}
            />
          )}

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

              <AssignWorkForm
                room={props.room}
                roster={roster}
                draft={draft}
                onDraft={setDraft}
                problems={problems}
                warnings={warnings}
                assigning={assigning}
                assignError={assignError}
                onSubmit={(e) => void onAssign(e)}
              />

              <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
                <ActivityColumn agents={snapshot.agents} />
                <ClaimsColumn
                  room={props.room}
                  groups={groups}
                  conflicts={conflicts}
                  busy={busy}
                  now={now}
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
 * The write half of this panel: record that an agent is taking a subject.
 *
 * Deliberately always open rather than tucked behind an "Add" button. The
 * War Room could release and reclaim a claim but had no way to *create* one,
 * so the one verb an operator needs was the one with no control — and a
 * feature hidden behind a disclosure on a surface nobody had opened is the
 * same as not shipping it.
 *
 * Three properties this form keeps:
 *
 * - **The problems render live, beside the control that produced them.** They
 *   are a mirror of the Gateway's `ClaimRequest` bounds, so an operator is
 *   told which field and which limit before a round trip. The Gateway stays
 *   the authority: its 422 is rendered verbatim if the two ever disagree.
 * - **A roster miss is a warning, not a block.** The store accepts any holder
 *   string, so refusing here would invent a rule the server does not have.
 *   It is disclosed because the claim would land with no member to match it.
 * - **The TTL presets are bounds, not suggestions.** Each maps to a value the
 *   server's `Field(gt=0, le=3600)` accepts, so no preset can produce a 422.
 */
function AssignWorkForm(props: {
  room: string;
  roster: string[];
  draft: AssignmentDraft;
  onDraft: (draft: AssignmentDraft) => void;
  problems: { field: string; problem: string }[];
  warnings: string[];
  assigning: boolean;
  assignError: string | null;
  onSubmit: (e: React.FormEvent) => void;
}) {
  const { draft, onDraft, problems } = props;
  const set = (patch: Partial<AssignmentDraft>) => onDraft({ ...draft, ...patch });
  const problemFor = (field: string) => problems.find((p) => p.field === field)?.problem ?? null;
  const ttlPresets: { label: string; seconds: number }[] = [
    { label: "2m", seconds: 120 },
    { label: "10m", seconds: 600 },
    { label: "30m", seconds: 1800 },
    { label: "1h", seconds: 3600 },
  ];

  const row = "flex flex-col gap-1 min-w-0";

  return (
    <form
      onSubmit={props.onSubmit}
      className="rounded-xl border border-primary/25 bg-card/70 p-4 space-y-3"
      aria-label="Assign work"
    >
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h4 className="text-xs font-bold uppercase tracking-wider text-foreground flex items-center gap-1.5">
            <Send className="size-3.5 text-primary" aria-hidden="true" />
            Assign work in {props.room}
          </h4>
          <p className="text-[11px] text-muted-foreground mt-0.5 max-w-2xl">
            Records a real claim through <code className="font-mono">POST /api/groups/&#123;room&#125;/claims</code>. A claim
            records intent to touch a subject — it is advisory, does not start a run, and never refuses two agents from
            holding the same one. That overlap is reported as contested on the board below.
          </p>
        </div>
        <Badge tone="blue" title="This control performs a real write against the Gateway">
          real write
        </Badge>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        <div className={row}>
          <label htmlFor="assign-holder" className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
            Holder <span className="text-destructive">*</span>
          </label>
          <input
            id="assign-holder"
            name="holder"
            list="assign-holder-options"
            className={inputCls}
            value={draft.holder}
            placeholder="agent name"
            onChange={(e) => set({ holder: e.target.value })}
            aria-invalid={problemFor("holder") !== null}
          />
          <datalist id="assign-holder-options">
            {props.roster.map((name) => (
              <option key={name} value={name} />
            ))}
          </datalist>
          {problemFor("holder") && <p className="text-[10px] text-destructive">{problemFor("holder")}</p>}
        </div>

        <div className={row}>
          <label htmlFor="assign-kind" className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
            Kind
          </label>
          <select
            id="assign-kind"
            name="kind"
            className={inputCls}
            value={draft.kind}
            onChange={(e) => set({ kind: e.target.value })}
          >
            {ASSIGNMENT_KINDS.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
          {problemFor("kind") && <p className="text-[10px] text-destructive">{problemFor("kind")}</p>}
        </div>

        <div className={row}>
          <label htmlFor="assign-intent" className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
            Intent
          </label>
          <select
            id="assign-intent"
            name="intent"
            className={inputCls}
            value={draft.intent}
            onChange={(e) => set({ intent: e.target.value })}
          >
            {ASSIGNMENT_INTENTS.map((i) => (
              <option key={i} value={i}>
                {i}
              </option>
            ))}
          </select>
          {problemFor("intent") && <p className="text-[10px] text-destructive">{problemFor("intent")}</p>}
        </div>

        <div className={row}>
          <label htmlFor="assign-ttl" className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
            Lease
          </label>
          <input
            id="assign-ttl"
            name="ttl_seconds"
            type="number"
            min={1}
            max={TTL_MAX_SECONDS}
            className={inputCls}
            value={String(draft.ttl_seconds)}
            onChange={(e) => set({ ttl_seconds: Number(e.target.value) })}
          />
          <div className="flex flex-wrap gap-1">
            {ttlPresets.map((p) => (
              <button
                key={p.seconds}
                type="button"
                onClick={() => set({ ttl_seconds: p.seconds })}
                className={`text-[10px] px-1.5 py-0.5 rounded border transition-colors ${
                  draft.ttl_seconds === p.seconds
                    ? "border-primary bg-primary/15 text-primary"
                    : "border-border/60 text-muted-foreground hover:text-foreground"
                }`}
                title={`${p.seconds} seconds — within the ${1}s..${TTL_MAX_SECONDS}s the server accepts`}
              >
                {p.label}
              </button>
            ))}
          </div>
          {problemFor("ttl_seconds") && <p className="text-[10px] text-destructive">{problemFor("ttl_seconds")}</p>}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3">
        <div className={row}>
          <label htmlFor="assign-subject" className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
            Subject <span className="text-destructive">*</span>
            <span className="ml-1.5 font-normal normal-case text-muted-foreground/70">
              {draft.subject.length}/{2000}
            </span>
          </label>
          <input
            id="assign-subject"
            name="subject"
            className={inputCls}
            value={draft.subject}
            placeholder="what is being worked on — a file path, task id, or requirement"
            onChange={(e) => set({ subject: e.target.value })}
            aria-invalid={problemFor("subject") !== null}
          />
          {problemFor("subject") && <p className="text-[10px] text-destructive">{problemFor("subject")}</p>}
        </div>

        <div className={row}>
          <label htmlFor="assign-detail" className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
            Detail
            <span className="ml-1.5 font-normal normal-case text-muted-foreground/70">
              {draft.detail.length}/{DETAIL_MAX_LENGTH}
            </span>
          </label>
          <input
            id="assign-detail"
            name="detail"
            className={inputCls}
            value={draft.detail}
            placeholder="optional — shown on the claim row"
            maxLength={DETAIL_MAX_LENGTH + 1}
            onChange={(e) => set({ detail: e.target.value })}
            aria-invalid={problemFor("detail") !== null}
          />
          {problemFor("detail") && <p className="text-[10px] text-destructive">{problemFor("detail")}</p>}
        </div>
      </div>

      {props.warnings.map((w) => (
        <div key={w} className="rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-[11px] text-amber-700 dark:text-amber-300">
          <AlertTriangle className="size-3 inline mr-1.5 -mt-0.5" aria-hidden="true" />
          {w}
        </div>
      ))}

      {props.assignError && <ErrorBox message={`Assignment refused — ${props.assignError}`} />}

      <div className="flex items-center justify-between gap-3 flex-wrap pt-1 border-t border-border/50">
        <p className="text-[11px] text-muted-foreground">
          {problems.length > 0
            ? `${problems.length} field${problems.length === 1 ? "" : "s"} must be fixed before this can be sent.`
            : "Sent as-is; the Gateway's own validation is the final word."}
        </p>
        <Btn
          variant="primary"
          type="submit"
          disabled={props.assigning || !props.room || problems.length > 0}
          title={
            problems.length > 0
              ? "Fix the fields listed above"
              : `POST /api/groups/${props.room}/claims`
          }
        >
          {props.assigning ? (
            <>
              <RefreshCw className="size-3.5 animate-spin" aria-hidden="true" /> Assigning…
            </>
          ) : (
            <>
              <Send className="size-3.5" aria-hidden="true" /> Assign work
            </>
          )}
        </Btn>
      </div>
    </form>
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
  /** Epoch ms. Drives the lease countdown so an assigned claim visibly ages. */
  now: number;
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
                    // Lease age, ticked by the panel's 1s clock. An assigned
                    // claim visibly counts down, and an expired one says how
                    // long ago rather than showing a stale "active".
                    const expiry = claimExpiryView(claim, props.now / 1000);
                    const expiryTitle =
                      typeof claim.expires_at === "number" && Number.isFinite(claim.expires_at)
                        ? `Lease ends ${new Date(claim.expires_at * 1000).toLocaleString()}`
                        : "The server sent no expiry for this claim";
                    return (
                      <li key={claim.claim_id} className="flex items-center gap-2 flex-wrap text-[11px]">
                        <span className="font-semibold">{claim.holder}</span>
                        <Badge tone="gray" title={`Claim kind: ${claim.kind}`}>
                          {claim.intent}
                        </Badge>
                        <Badge tone={expiry.tone} title={expiryTitle}>
                          {expiry.label}
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