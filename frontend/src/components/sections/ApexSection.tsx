"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { Cpu, PauseCircle, Power, Radio, ShieldCheck } from "lucide-react";

import {
  Badge,
  Btn,
  ErrorBox,
  Notice,
  Section,
  SkeletonList,
  inputCls,
} from "@/components/ui";
import {
  APEX_PROFILES,
  ENABLE_PROFILES,
  decideApexApproval,
  fetchApexApprovals,
  fetchApexMode,
  fetchApexPolicy,
  fetchApexStatus,
  createApexSession,
  dispatchApexSession,
  formatMeasuredCount,
  profileToAdopt,
  type ApexApprovals,
  type ApexBlock,
  type ApexContract,
  type ApexControlAction,
  type ApexMode,
  type ApexProfile,
  type ApexStatus,
  runApexCycle,
  setApexControl,
  setApexMode,
  steerApexSession,
} from "@/lib/apex";

/**
 * The APEX control panel.
 *
 * ## What this panel is careful about
 *
 * **Absent is not zero.** Every measured value goes through the `null`-preserving
 * mappers in `lib/apex.ts`, so an unreadable store renders as a disclosed
 * unavailability with its reason rather than as "0 sessions". A panel that
 * reported zeroes for something it could not read is worse than no panel,
 * because it looks like a working system.
 *
 * **`all_live` and `live/declared` travel together.** Rendering "12 invariants"
 * over 9 live sites would be a fabricated count, so the badge states both.
 *
 * **Dispatch is explicit.** Creating and dispatching an objective starts a
 * real run through the Gateway adapter. "Run one cycle" still records a
 * decision only; neither action can mark acceptance as verified.
 *
 * **Control actions re-read before they render.** The session card acts, then
 * re-reads mode and approvals from the server; no verb or verdict paints a
 * state it inferred from its own click.
 */

/**
 * A block the backend could not read.
 *
 * `Notice` takes a `message` string rather than children, so the emphasis is
 * carried in the text. Rendering it as a zeroed card instead would present an
 * unmeasured subsystem as a working one — the exact failure this surface's
 * mappers exist to prevent.
 */
function Unavailable({
  block,
  label,
}: {
  block: ApexBlock<unknown>;
  label: string;
}) {
  if (block.available) return null;
  return (
    <Notice tone="warn" message={`${label} unavailable — ${block.reason}`} />
  );
}

function quota(value: number | null | undefined): string {
  if (value === null) return "Unlimited";
  return value === undefined ? "Unknown" : value.toLocaleString();
}

function ContractCard({ contract }: { contract: ApexContract }) {
  const granted = contract.authority_granted.length;
  return (
    <div className="space-y-2 rounded-lg border border-neutral-200 p-3 dark:border-neutral-800">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium">Contract</span>
        <div className="flex items-center gap-2">
          {/* Gray, not green: a disabled profile must look off, per the
              client rule that defaults-off has to render as off. */}
          <Badge tone={contract.enabled ? "green" : "gray"}>
            {contract.enabled ? contract.profile : "off"}
          </Badge>
          <code className="text-xs text-neutral-500">{contract.digest}</code>
        </div>
      </div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm sm:grid-cols-3">
        <div>
          <dt className="text-neutral-500">Authority granted</dt>
          <dd>{granted} dimensions</dd>
        </div>
        <div>
          <dt className="text-neutral-500">Max tool calls</dt>
          <dd>{quota(contract.budget.max_tool_calls)}</dd>
        </div>
        <div>
          <dt className="text-neutral-500">Total token ceiling</dt>
          <dd>{quota(contract.budget.max_total_tokens)}</dd>
        </div>
        <div>
          <dt className="text-neutral-500">Max agents</dt>
          <dd>{quota(contract.budget.max_active_agents)}</dd>
        </div>
        <div>
          <dt className="text-neutral-500">Delegation depth</dt>
          <dd>{quota(contract.budget.max_delegation_depth)}</dd>
        </div>
        <div>
          <dt className="text-neutral-500">Replans</dt>
          <dd>{quota(contract.budget.max_replans)}</dd>
        </div>
        <div>
          <dt className="text-neutral-500">Retries per failure</dt>
          <dd>{quota(contract.budget.max_retries_per_failure_class)}</dd>
        </div>
        <div>
          <dt className="text-neutral-500">Runtime ceiling</dt>
          <dd>
            {contract.budget.max_runtime_minutes === null
              ? "Unlimited"
              : contract.budget.max_runtime_minutes === undefined
                ? "Unknown"
                : `${contract.budget.max_runtime_minutes} min`}
          </dd>
        </div>
        <div>
          <dt className="text-neutral-500">Emergency stop</dt>
          {/* Always on. There is no control that turns this off, which is the
              point of showing it as a fact rather than as a toggle. */}
          <dd className="text-emerald-600">always on</dd>
        </div>
      </dl>
      <p className="text-xs text-neutral-500">{contract.note}</p>
    </div>
  );
}

function PolicySitesCard({ contract }: { contract: ApexContract }) {
  return (
    <div className="space-y-2 rounded-lg border border-neutral-200 p-3 dark:border-neutral-800">
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-2 font-medium">
          <ShieldCheck className="size-4" />
          Delegated policy kernels
        </span>
        <Badge
          tone={contract.policy_sites_missing.length === 0 ? "green" : "amber"}
        >
          {contract.policy_sites_live.length}/{contract.policy_sites.length}{" "}
          live
        </Badge>
      </div>
      <p className="text-xs text-neutral-500">
        APEX composes these; it is not a second policy kernel. A missing one is
        an unenforced boundary.
      </p>
      <ul className="space-y-1 text-xs">
        {contract.policy_sites.map((site) => {
          const live = contract.policy_sites_live.includes(site.name);
          return (
            <li key={site.name} className="flex items-start gap-2">
              <span className={live ? "text-emerald-600" : "text-amber-600"}>
                {live ? "●" : "○"}
              </span>
              <span className="min-w-0">
                <span className="font-medium">{site.name}</span>
                <code className="block truncate text-neutral-500">
                  {site.module}
                </code>
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function InvariantsCard({
  report,
}: {
  report: NonNullable<ApexStatus["invariants"]>;
}) {
  return (
    <div className="space-y-2 rounded-lg border border-neutral-200 p-3 dark:border-neutral-800">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium">Invariants</span>
        {/* Declared and live are stated together: "12" over 9 live sites would
            be a fabricated count. */}
        <Badge tone={report.all_live ? "green" : "amber"}>
          {report.live}/{report.declared} live
        </Badge>
      </div>
      <ul className="space-y-1 text-xs">
        {report.invariants.map((invariant) => (
          <li key={invariant.id} className="flex items-start gap-2">
            <span
              className={invariant.live ? "text-emerald-600" : "text-amber-600"}
            >
              {invariant.live ? "●" : "○"}
            </span>
            <span className="min-w-0">
              <span className="font-mono">{invariant.id}</span>{" "}
              {invariant.statement}
              <code className="block truncate text-neutral-500">
                {invariant.module}:{invariant.symbol}
                {!invariant.live && invariant.reason
                  ? ` — ${invariant.reason}`
                  : ""}
              </code>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * The APEX ON/OFF switch.
 *
 * ## The one rule this component is built around
 *
 * **The switch is never painted before the server confirms it.** It renders
 * `mode` — the state a `GET /apex/mode` read returned — and nothing else. There
 * is no optimistic local flip, because a switch that reads ON against a server
 * that never enabled anything is the most dangerous single widget in this
 * package: it tells the operator the executive is running when it is not.
 *
 * That is why the write is followed by a re-read rather than by trusting the
 * POST's own body, and why a rejected write leaves the switch exactly where it
 * was with the server's reason beside it.
 *
 * Four disclosures the switch owes the operator, each reachable:
 *
 * - **A degraded store.** An unreadable mode store answers every scope as off.
 *   That is the correct fail-closed behaviour and a useless thing to show as a
 *   clean "OFF", so `load_error` is surfaced rather than swallowed.
 * - **A write that did not persist.** `durable: false` means the toggle changed
 *   an in-memory row that a restart will forget. The switch says so instead of
 *   implying a durable setting.
 * - **An unknown profile.** A record written by a newer build degrades to
 *   `assist`. `enabled: true` would still be true, so without `load_note` the
 *   panel would show the operator more authority than they will actually get.
 * - **`enabled` true while `contract_enabled` false.** The flag says someone
 *   switched this on; the contract is what actually gates work. When they
 *   disagree, the authority is what the switch reports as effective.
 */
function ApexToggle({
  onError,
  onChanged,
}: {
  onError: (message: string | null) => void;
  /** Fired after a write the server confirmed, so the panel's *other* reads re-read too. */
  onChanged?: () => void;
}) {
  const [mode, setMode] = useState<ApexMode | null>(null);
  const [pending, setPending] = useState<boolean | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [profile, setProfile] = useState<ApexProfile>("assist");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const next = await fetchApexMode();
        if (!cancelled) {
          setMode(next);
          // The picker follows the server's profile when it is one this build
          // can be *enabled* at, so the next enable does not silently reset an
          // operator who had deliberately chosen a different rung — and never
          // adopts `off`, which the picker's own option list excludes and which
          // the server refuses to enable at.
          const adoptable = profileToAdopt(next.profile);
          if (adoptable) {
            setProfile(adoptable);
          }
        }
      } catch (exc) {
        // A failed read is NOT "off". It is an unknown, and the switch says so
        // instead of rendering a state nobody measured.
        if (!cancelled)
          setLoadError(exc instanceof Error ? exc.message : String(exc));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const toggle = async (nextEnabled: boolean) => {
    if (pending !== null) return;
    setPending(nextEnabled);
    onError(null);
    try {
      const written = await setApexMode(nextEnabled, { profile });
      // Re-read rather than adopting the POST body: the toggle's contract is
      // that what it shows is what the server last confirmed.
      const confirmed = await fetchApexMode();
      setMode(confirmed);
      // The switch is only half the panel: the "Active profile" line below it
      // reads `/status`, and leaving that at its mount-time answer put "off
      // (no mission control)" under a switch the server had just turned on.
      // Two contradicting claims on one screen is the failure — so the other
      // read re-runs here, on the server's confirmation, never on the click.
      onChanged?.();
      if (written.durable === false) {
        onError(
          "The mode changed in memory but did not reach disk — it will not survive a restart.",
        );
      }
    } catch (exc) {
      // Leave `mode` untouched: a refused write must not move the switch.
      onError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setPending(null);
    }
  };

  if (loadError) {
    return (
      <div className="space-y-2 rounded-lg border border-amber-300 p-3 dark:border-amber-800">
        <div className="flex items-center gap-2 text-sm font-medium">
          <Power className="size-4" />
          APEX state unknown
        </div>
        <p className="text-xs text-neutral-600">
          The mode read failed, so whether APEX is on is{" "}
          <strong>not known</strong>. This is not the same as off.
        </p>
        <p className="font-mono text-xs text-amber-700">{loadError}</p>
        <Btn onClick={() => window.location.reload()}>Retry</Btn>
      </div>
    );
  }

  if (!mode) {
    return <SkeletonList rows={1} />;
  }

  const on = mode.enabled;
  const busy = pending !== null;

  return (
    <div className="space-y-3 rounded-xl border border-border/60 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Power
            className={`size-5 ${on ? "text-emerald-600" : "text-neutral-400"}`}
          />
          <span className="text-sm font-medium">APEX autopilot</span>
          {/* Defaults-off must look off, so this is grey rather than neutral
              green when APEX is not running. */}
          <Badge tone={on ? "green" : "gray"}>{on ? "ON" : "OFF"}</Badge>
        </div>

        <div className="flex items-center gap-2">
          <label className="flex items-center gap-2 text-xs text-neutral-500">
            Profile to enable
            <select
              aria-label="Profile to enable"
              className="rounded-md border border-border bg-transparent px-2 py-1 text-sm"
              value={profile}
              disabled={busy}
              onChange={(event) =>
                setProfile(event.target.value as ApexProfile)
              }
            >
              {ENABLE_PROFILES.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>

          <Btn
            onClick={() => toggle(!on)}
            disabled={busy}
            aria-pressed={on}
            className={on ? "" : "font-semibold"}
          >
            {busy
              ? pending
                ? "Turning on…"
                : "Turning off…"
              : on
                ? "Turn off"
                : "Turn on"}
          </Btn>
        </div>
      </div>

      <p className="text-xs text-neutral-600">
        {on ? (
          <>
            APEX chooses the strategy and composes existing capabilities. It
            does not run tools itself, and a mission completes only on measured
            acceptance evidence. Emergency stop stays outside this control.
          </>
        ) : (
          <>
            Turn APEX on to let it decide the approach to a high-level
            objective. With APEX off, Alpha executes requests normally.
          </>
        )}
      </p>

      {/* The disagreement between the recorded intent and the authority the
          contract actually grants. */}
      {on && !mode.contract_enabled && (
        <Notice
          tone="warn"
          message="APEX is recorded as on, but the active contract grants nothing. Work will not proceed until this is resolved."
        />
      )}

      {mode.load_note && (
        <Notice
          tone="warn"
          message={`Stored profile was not recognised — ${mode.load_note}`}
        />
      )}

      {mode.load_error && (
        <Notice
          tone="warn"
          message={`The mode store could not be read (${mode.load_error}). Every scope is being treated as OFF, which is the fail-closed choice.`}
        />
      )}

      {!on && mode.reason && (
        <p className="text-xs text-neutral-500">{mode.reason}</p>
      )}
    </div>
  );
}

/** State → tone for the session badge; a state this build does not know
 *  renders verbatim in grey rather than being snapped to a familiar one. */
const SESSION_TONE: Record<string, "green" | "amber" | "red" | "gray"> = {
  active: "green",
  paused: "amber",
  blocked: "red",
  completed: "gray",
  failed: "red",
  cancelled: "gray",
};

/**
 * Session control — pause / resume / stop, steering, and the approval gate.
 *
 * ## The two rules this card is built around
 *
 * **Nothing is painted before the server confirms it.** Every action re-reads
 * the mode (which carries `active_session`) and the approvals list after the
 * mutation; a refused action leaves the card exactly where it was and shows
 * the server's reason. The verbs are deliberately *not* re-implemented in the
 * client — no "resume disabled because paused" logic — because the server's
 * transition rules are the single authority, and a click that changes nothing
 * gets the server's own explanation instead of a silently dead button.
 *
 * **The two reads fail independently.** The session read and the approval read
 * go through `Promise.allSettled`: a broken approvals store must not blank a
 * healthy session view, and vice versa, because "no verdicts are waiting" and
 * "the verdicts could not be read" lead to opposite actions.
 */
function SessionControlCard({
  onError,
  reloadKey = 0,
}: {
  onError: (message: string | null) => void;
  /** Bumped by a completed cycle so this card re-reads rather than rendering the pre-cycle state. */
  reloadKey?: number;
}) {
  const [mode, setMode] = useState<ApexMode | null>(null);
  const [modeError, setModeError] = useState<string | null>(null);
  const [approvals, setApprovals] = useState<ApexApprovals | null>(null);
  const [approvalsError, setApprovalsError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [instruction, setInstruction] = useState("");
  const [verdictNote, setVerdictNote] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const mountedRef = useRef(true);

  const reload = useCallback(async () => {
    const [modeResult, approvalsResult] = await Promise.allSettled([
      fetchApexMode(),
      fetchApexApprovals(),
    ]);
    if (!mountedRef.current) return;
    if (modeResult.status === "fulfilled") {
      setMode(modeResult.value);
      setModeError(null);
    } else {
      setModeError(
        modeResult.reason instanceof Error
          ? modeResult.reason.message
          : String(modeResult.reason),
      );
    }
    if (approvalsResult.status === "fulfilled") {
      setApprovals(approvalsResult.value);
      setApprovalsError(null);
    } else {
      setApprovalsError(
        approvalsResult.reason instanceof Error
          ? approvalsResult.reason.message
          : String(approvalsResult.reason),
      );
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    void reload();
    const timer = window.setInterval(() => void reload(), 30_000);
    return () => {
      mountedRef.current = false;
      window.clearInterval(timer);
    };
  }, [reload, reloadKey]);

  const control = async (verb: ApexControlAction) => {
    if (busy) return;
    setBusy(verb);
    onError(null);
    setNotice(null);
    try {
      const outcome = await setApexControl(verb);
      await reload();
      // `applied: false` is the server saying it changed nothing (an
      // already-paused session) — surface that rather than letting a click
      // look like it did work. An applied stop carries its boundary note.
      setNotice(
        !outcome.applied
          ? `${verb} changed nothing — ${outcome.reason || "the session was already in that state"}.`
          : (outcome.note ?? null),
      );
    } catch (exc) {
      // Leave the card untouched: a refused write (404, 409, or the approval
      // gate naming itself) must not move the UI — it moves the error line.
      onError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(null);
    }
  };

  const steer = async () => {
    const session = mode?.active_session;
    const text = instruction.trim();
    if (!session || !text || busy) return;
    setBusy("steer");
    onError(null);
    setNotice(null);
    try {
      await steerApexSession(session.session_id, text);
      setInstruction("");
      setNotice("Steering recorded as a mission constraint on this session.");
      await reload();
    } catch (exc) {
      onError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(null);
    }
  };

  const decide = async (approvalId: string, verdict: "approve" | "reject") => {
    if (busy) return;
    setBusy(`${verdict}:${approvalId}`);
    onError(null);
    setNotice(null);
    try {
      const outcome = await decideApexApproval(approvalId, verdict, {
        note: verdictNote.trim(),
      });
      setVerdictNote("");
      await reload();
      // The verdicts are asymmetric and the response says which happened —
      // the card never infers it from which button was pressed.
      setNotice(
        verdict === "reject"
          ? "Rejected — the session stays parked with the blocker on record."
          : outcome.resumed
            ? "Approved — the session returned to ACTIVE."
            : "Approved — the verdict is recorded; the session was not resumed.",
      );
    } catch (exc) {
      onError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(null);
    }
  };

  const session = mode?.active_session ?? null;
  const pending = approvals?.available
    ? approvals.approvals.filter((row) => row.status === "pending")
    : [];
  const busyNow = busy !== null;

  return (
    <div className="space-y-3 rounded-xl border border-border/60 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex items-center gap-2 text-sm font-medium">
          <PauseCircle className="size-4" />
          Session control
        </span>
        {modeError ? (
          <Badge tone="gray">state unknown</Badge>
        ) : session ? (
          <Badge tone={SESSION_TONE[session.state] ?? "gray"}>
            {session.state}
          </Badge>
        ) : (
          <Badge tone="gray">no session</Badge>
        )}
      </div>

      {/* A failed session read is an unknown, not an absence — the badge above
          says "state unknown" and no control verbs render against it. The
          approval read below is independent and still renders. */}
      {modeError && (
        <Notice
          tone="warn"
          message={`The session read failed, so the control state is unknown — this is not the same as no session. ${modeError}`}
        />
      )}

      {!modeError &&
        (session ? (
          <div className="space-y-2">
            <p className="text-xs text-neutral-500">
              Session <code>{session.session_id}</code> ·{" "}
              {session.objective || "no objective recorded"}
              {session.blocked_reason
                ? ` · blocked: ${session.blocked_reason}`
                : ""}
            </p>
            {session.dispatch_state === "failed" && (
              <Notice
                tone="warn"
                message={`Dispatch failed${session.run_status ? `; linked run status is ${session.run_status}` : ""}. The session remains ${session.state}; inspect the run and recovery outcome before treating this objective as progressing.`}
              />
            )}
            <p className="text-xs text-neutral-500" aria-live="polite">
              Resource use:{" "}
              {session.usage.total_tokens === null
                ? "token usage not yet measured"
                : `${session.usage.total_tokens.toLocaleString()} / ${quota(session.token_limit)} tokens`}
              {` · ${session.usage.tool_calls?.toLocaleString() ?? "unmeasured"} tool calls · ${session.usage.llm_calls?.toLocaleString() ?? "unmeasured"} model calls`}
              {` · ${formatMeasuredCount(session.usage.replans)} / ${quota(session.replan_limit)} acceptance replans`}
            </p>
            <div className="flex flex-wrap gap-2">
              <Btn onClick={() => control("pause")} disabled={busyNow}>
                Pause
              </Btn>
              <Btn onClick={() => control("resume")} disabled={busyNow}>
                Resume
              </Btn>
              {/* A mission-scoped park, not the fleet ESTOP — the stop route's
                  own note says so after it applies, and it lands in `notice`. */}
              <Btn
                variant="danger"
                onClick={() => control("stop")}
                disabled={busyNow}
              >
                Stop
              </Btn>
            </div>
            <form
              className="flex flex-wrap items-center gap-2"
              onSubmit={(event) => {
                event.preventDefault();
                void steer();
              }}
            >
              <div className="min-w-[14rem] flex-1">
                <input
                  className={inputCls}
                  value={instruction}
                  onChange={(event) => setInstruction(event.target.value)}
                  disabled={busyNow}
                  aria-label="Steering instruction"
                  placeholder="Steering instruction — a mission constraint, not a prompt rewrite"
                />
              </div>
              <Btn type="submit" disabled={busyNow || !instruction.trim()}>
                Steer
              </Btn>
            </form>
          </div>
        ) : (
          <p className="text-xs text-neutral-500">
            No active APEX session in this scope. Pause, resume, stop, steer and
            approvals act on the session bound to this conversation.
          </p>
        ))}

      {approvalsError && (
        <Notice
          tone="warn"
          message={`The approval read failed, so pending verdicts are unknown — not zero. ${approvalsError}`}
        />
      )}

      {approvals && !approvals.available && (
        <Notice
          tone="warn"
          message={`Approvals unavailable — ${approvals.reason || "the store could not be read"}. Pending verdicts are unknown, not zero.`}
        />
      )}

      {approvals?.available && (
        <div className="space-y-2">
          <p className="text-xs text-neutral-500">
            {approvals.pending === null
              ? "Pending approvals were not reported."
              : approvals.pending === 0
                ? "No approvals are waiting."
                : `${approvals.pending} awaiting an operator verdict.`}
          </p>

          {approvals.truncated && (
            <Notice
              tone="warn"
              message={`Showing ${approvals.returned ?? "?"} of ${approvals.count ?? "?"} recorded approvals — the rest are not displayed. The count above covers the whole backlog, so fewer rows than the count are expected here.`}
            />
          )}

          {pending.length > 0 && (
            <>
              <label className="block space-y-1">
                <span className="text-[11px] font-semibold">
                  Verdict note (optional)
                </span>
                <input
                  className={inputCls}
                  value={verdictNote}
                  onChange={(event) => setVerdictNote(event.target.value)}
                  disabled={busyNow}
                  placeholder="Recorded with the verdict"
                />
              </label>
              <ul className="space-y-2">
                {pending.map((row) => (
                  <li
                    key={row.approval_id}
                    className="space-y-2 rounded-lg border border-neutral-200 p-3 dark:border-neutral-800"
                  >
                    <p className="text-xs">
                      {row.note ||
                        "The blocker was parked without a named reason."}
                    </p>
                    <p className="text-[11px] text-neutral-500">
                      requested by {row.requester || "unknown requester"} ·
                      session {row.session_id}
                    </p>
                    {row.action && (
                      <div className="space-y-1 rounded-md bg-neutral-50 p-2 text-[11px] text-neutral-600 dark:bg-neutral-900 dark:text-neutral-300">
                        <p>
                          Operation:{" "}
                          {row.action.tool_name || "unspecified tool"}
                          {row.action.action_class
                            ? ` · ${row.action.action_class}`
                            : ""}
                        </p>
                        {row.action.arguments_digest && (
                          <p className="break-all">
                            Exact arguments fingerprint:{" "}
                            {row.action.arguments_digest}
                          </p>
                        )}
                        {row.action.contract_digest && (
                          <p className="break-all">
                            Contract: {row.action.contract_digest}
                          </p>
                        )}
                        <p>
                          Tool arguments are hidden; the fingerprint binds
                          approval to the exact request.
                        </p>
                      </div>
                    )}
                    <div className="flex flex-wrap gap-2">
                      <Btn
                        onClick={() => decide(row.approval_id, "approve")}
                        disabled={busyNow}
                      >
                        Approve
                      </Btn>
                      <Btn
                        variant="danger"
                        onClick={() => decide(row.approval_id, "reject")}
                        disabled={busyNow}
                      >
                        Reject
                      </Btn>
                    </div>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}

      {notice && <Notice tone="neutral" message={notice} />}
    </div>
  );
}

/**
 * Which session this panel acts on.
 *
 * `/status` reports a `session` block only when it is *named*, and `/mode` is
 * the only route carrying `active_session` for this scope. So the subject has
 * to be resolved from `/mode` before a status read — otherwise the session
 * block, its policy-drift warning and the id "Run one cycle" posts to are all
 * silently absent, and `runCycle` reading its target back out of
 * `status.session` was a no-op that returned before `setBusy` ever ran.
 *
 * A rejected read comes back as a reason, never as `null` with no reason:
 * "the read failed" and "there is no session" lead to opposite actions.
 */
async function resolveSessionSubject(): Promise<{
  id: string | null;
  reason: string | null;
}> {
  try {
    const mode = await fetchApexMode();
    return { id: mode.active_session?.session_id ?? null, reason: null };
  } catch (exc) {
    return {
      id: null,
      reason: exc instanceof Error ? exc.message : String(exc),
    };
  }
}

export function ApexSection() {
  const [status, setStatus] = useState<ApexStatus | null>(null);
  const [policy, setPolicy] = useState<ApexContract | null>(null);
  const [previewProfile, setPreviewProfile] =
    useState<ApexProfile>("autonomous");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  /** `/mode` failed, so no session block is being shown — say which. */
  const [subjectError, setSubjectError] = useState<string | null>(null);
  /**
   * Bumped after a cycle. The session card reads its two routes once on
   * mount, so a cycle that parks the session and raises an approval would
   * otherwise leave `idle` and an empty verdict list rendered beside a live
   * approval — two claims, one of them stale.
   */
  const [reloadKey, setReloadKey] = useState(0);
  const [objective, setObjective] = useState("");
  const [criteriaText, setCriteriaText] = useState("");
  const [dispatchNotice, setDispatchNotice] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const subject = await resolveSessionSubject();
      if (cancelled) return;
      setSubjectError(subject.reason);
      try {
        const [next, nextPolicy] = await Promise.all([
          fetchApexStatus(subject.id ? { sessionId: subject.id } : undefined),
          fetchApexPolicy(previewProfile),
        ]);
        if (!cancelled) {
          setStatus(next);
          setPolicy(nextPolicy);
        }
      } catch (exc) {
        if (!cancelled)
          setError(exc instanceof Error ? exc.message : String(exc));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [previewProfile]);

  const refresh = async () => {
    setError(null);
    const subject = await resolveSessionSubject();
    setSubjectError(subject.reason);
    try {
      setStatus(
        await fetchApexStatus(
          subject.id ? { sessionId: subject.id } : undefined,
        ),
      );
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  };

  const runCycle = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      // Resolved at click time: a session created since the panel loaded must
      // be reachable, and reading the target back out of `status.session` is
      // what made this button a silent no-op.
      const subject = await resolveSessionSubject();
      if (subject.reason) {
        setError(
          `The active session could not be resolved, so no cycle was run: ${subject.reason}`,
        );
        return;
      }
      setSubjectError(null);
      if (!subject.id) {
        setError(
          "There is no active APEX session in this scope, so there is no cycle to run. Create a session first.",
        );
        return;
      }
      // Records a decision only. No tool runs and no run is created here.
      await runApexCycle(subject.id);
      // Re-read *named*, so the session block and any policy drift it carries
      // become part of the payload rather than staying absent.
      setStatus(await fetchApexStatus({ sessionId: subject.id }));
      setReloadKey((key) => key + 1);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const createAndDispatch = async () => {
    if (busy || !objective.trim()) return;
    setBusy(true);
    setError(null);
    setDispatchNotice(null);
    try {
      // Mode is authoritative: never silently turn APEX on or widen the
      // operator's selected profile in order to make dispatch succeed.
      const mode = await fetchApexMode();
      if (!mode.enabled || !mode.contract_enabled) {
        throw new Error(
          "Enable APEX and confirm its contract before dispatching an objective.",
        );
      }
      const previous = mode.active_session;
      const previousFailed =
        previous?.dispatch_state === "failed" &&
        [
          "error",
          "failed",
          "interrupted",
          "cancelled",
          "dispatch_error",
        ].includes(previous.run_status ?? "");
      if (previous && !previousFailed) {
        throw new Error(
          `This scope already has active session ${previous.session_id}. Finish or control it before creating another.`,
        );
      }
      if (!ENABLE_PROFILES.includes(mode.profile as ApexProfile)) {
        throw new Error(
          `The enabled profile '${mode.profile}' cannot be used for dispatch.`,
        );
      }
      const acceptance_criteria = criteriaText
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean);
      const created = await createApexSession({
        objective: objective.trim(),
        profile: mode.profile as ApexProfile,
        thread_id: mode.scope_key,
        ...(acceptance_criteria.length ? { acceptance_criteria } : {}),
      });
      const result = await dispatchApexSession(created.session_id);
      const dispatchError = result.errors.find(
        (item) => item.session_id === created.session_id,
      );
      if (dispatchError) {
        throw new Error(
          `Session ${created.session_id} was created, but dispatch was refused: ${dispatchError.error}`,
        );
      }
      setDispatchNotice(
        `${previousFailed ? `Previous session ${previous?.session_id ?? "unknown"} remains failed and unverified. ` : ""}Session ${created.session_id}: ${formatMeasuredCount(result.dispatched)} dispatched, ${formatMeasuredCount(result.running)} running, ${formatMeasuredCount(result.awaiting_verification)} awaiting verification, ${formatMeasuredCount(result.failed)} failed. A completed run is not verified until acceptance evidence is evaluated.`,
      );
      setObjective("");
      setCriteriaText("");
      setReloadKey((key) => key + 1);
      await refresh();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
      // Refresh even on partial success: creation may have committed before
      // dispatch returned an error, and the operator needs to see that state.
      setReloadKey((key) => key + 1);
      await refresh();
    } finally {
      setBusy(false);
    }
  };

  if (error && !status) {
    return (
      <Section title="APEX Autopilot" hint="The executive control plane">
        <ErrorBox message={error} />
        <Btn onClick={refresh}>Retry</Btn>
      </Section>
    );
  }

  if (!status || !policy) {
    return (
      <Section title="APEX Autopilot" hint="The executive control plane">
        <SkeletonList rows={5} />
      </Section>
    );
  }

  return (
    <Section
      title="APEX Autopilot"
      hint="One objective in; APEX decides the strategy, existing engines do the work, and verification decides whether it is done."
      actions={
        <>
          <label className="inline-flex items-center gap-2 text-xs text-neutral-500">
            Contract preview
            <select
              aria-label="Contract preview profile"
              title="Changes the policy preview only. Use the APEX autopilot control below to change the active profile."
              className="rounded-md border border-neutral-300 bg-transparent px-2 py-1 text-sm text-neutral-900 dark:border-neutral-700 dark:text-neutral-100"
              value={previewProfile}
              onChange={(event) =>
                setPreviewProfile(event.target.value as ApexProfile)
              }
            >
              {APEX_PROFILES.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <Btn onClick={runCycle} disabled={busy}>
            {busy ? "Running…" : "Run one cycle"}
          </Btn>
          <Btn onClick={refresh}>Refresh</Btn>
        </>
      }
    >
      <div className="space-y-3">
        {/* The switch owns the first row: it is the one control that decides
            whether anything below it is live at all. */}
        <ApexToggle onError={setError} onChanged={refresh} />

        <div className="space-y-3 rounded-xl border border-border/60 p-4">
          <div>
            <h3 className="text-sm font-medium">
              Create and dispatch an objective
            </h3>
            <p className="mt-1 text-xs text-neutral-500">
              Uses the currently enabled server profile and scope. Each line
              below is an acceptance criterion. Dispatch starts a real run; it
              does not verify completion. After a failed run this starts a fresh
              session and does not reuse its checkpoint.
            </p>
          </div>
          <label className="block space-y-1 text-sm">
            <span>Objective</span>
            <textarea
              aria-label="APEX objective"
              className={inputCls}
              rows={3}
              value={objective}
              onChange={(event) => setObjective(event.target.value)}
              placeholder="Describe the outcome Alpha should achieve"
            />
          </label>
          <label className="block space-y-1 text-sm">
            <span>Acceptance criteria (one per line, optional)</span>
            <textarea
              aria-label="APEX acceptance criteria"
              className={inputCls}
              rows={2}
              value={criteriaText}
              onChange={(event) => setCriteriaText(event.target.value)}
              placeholder="Evidence that would show the objective is satisfied"
            />
          </label>
          <Btn onClick={createAndDispatch} disabled={busy || !objective.trim()}>
            {busy ? "Dispatching…" : "Create and dispatch"}
          </Btn>
          {dispatchNotice && <Notice tone="neutral" message={dispatchNotice} />}
        </div>

        {/* Session-scoped verbs and the approval gate, reading their own two
            routes so a failure in one does not blank the other. It re-reads
            whenever a cycle completes: a cycle can park the session and raise
            an approval, and a card that read only on mount would render the
            pre-cycle state beside the panel's own fresh status. */}
        <SessionControlCard onError={setError} reloadKey={reloadKey} />

        <ContractCard contract={policy} />

        {status.contract.available ? (
          <p className="text-sm text-neutral-600">
            Active profile: <strong>{status.contract.profile}</strong>{" "}
            {status.contract.enabled ? "(enabled)" : "(no mission control)"}
          </p>
        ) : (
          <Unavailable block={status.contract} label="Contract" />
        )}

        {status.fleet.available ? (
          <div className="flex items-center gap-2 rounded-lg border border-neutral-200 p-3 text-sm dark:border-neutral-800">
            <Radio className="size-4" />
            <span>
              Fleet: <strong>{status.fleet.mode}</strong>
              {status.fleet.estop_sentinel && " · ESTOP sentinel engaged"}
            </span>
            <Badge tone={status.fleet.admits_work ? "green" : "amber"}>
              {status.fleet.admits_work ? "admits work" : "refuses work"}
            </Badge>
          </div>
        ) : (
          <Unavailable block={status.fleet} label="Fleet control" />
        )}

        {status.sessions.available ? (
          <div className="flex items-center gap-2 rounded-lg border border-neutral-200 p-3 text-sm dark:border-neutral-800">
            <Cpu className="size-4" />
            <span>
              Sessions:{" "}
              <strong>{formatMeasuredCount(status.sessions.total)}</strong>
              {status.sessions.active !== null
                ? ` · ${status.sessions.active} active`
                : " · active count unreported"}
            </span>
            {status.sessions.total === 0 && (
              <span className="text-neutral-500">none recorded</span>
            )}
            {status.sessions.total !== 0 &&
              Object.entries(status.sessions.by_state).length === 0 && (
                <span className="text-neutral-500">
                  state breakdown unreported
                </span>
              )}
          </div>
        ) : (
          <Unavailable block={status.sessions} label="Session store" />
        )}

        <PolicySitesCard contract={policy} />

        {status.invariants ? (
          <InvariantsCard report={status.invariants} />
        ) : null}

        {/* No session block was requested because `/mode` would not say which
            session this scope controls. Say so rather than leaving the
            absence to be read as "no session, no drift to check". */}
        {subjectError && (
          <Notice
            tone="warn"
            message={`The active session could not be resolved, so no session record or policy-drift check is shown: ${subjectError}`}
          />
        )}

        {status.session && !status.session.available && (
          <Unavailable block={status.session} label="Session" />
        )}

        {status.session?.available && status.session.contract_drift && (
          <Notice
            tone="warn"
            message={`Policy drift — this session was created under ${status.session.contract_digest}; the active contract differs. Revalidate before continuing.`}
          />
        )}

        {/* Stated in the panel itself, not only in a doc, because "run one cycle"
            is the button most likely to be read as "do the work". */}
        <Notice
          tone="neutral"
          message="Run one cycle records a decision only. Create and dispatch starts a run through the Gateway adapter. A mission reaches COMPLETED only through an acceptance report in which every criterion was evaluated and held."
        />

        {error && <ErrorBox message={error} />}
      </div>
    </Section>
  );
}
