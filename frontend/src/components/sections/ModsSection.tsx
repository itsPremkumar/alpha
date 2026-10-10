"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  AUDIT_LIMIT_DEFAULT,
  HOLD_DECISIONS,
  HOLD_REASON_MAX_LENGTH,
  HOLDS_LIMIT_DEFAULT,
  AuditEntry,
  AuditPage,
  HoldList,
  HoldRecord,
  ImpactPreview,
  ModCommand,
  ModCommandList,
  ModCommandResult,
  ModDescription,
  ModFleet,
  decideHold,
  failureText,
  fetchModAudit,
  fetchModCommands,
  fetchModFleet,
  fetchModHolds,
  holdAge,
  holdExpiryView,
  manifestList,
  previewImpact,
  runModCommand,
} from "@/lib/mods";
import { Badge, Btn, EmptyState, ErrorBox, Notice, Section, SkeletonList, StatCard, inputCls } from "@/components/ui";
import { absoluteStamp, relTime } from "@/lib/time";
import { Play, RefreshCw, Search } from "lucide-react";

/**
 * The operator surface over Alpha's ordered mod chain — the `mods` workspace
 * view, reading `GET|POST /api/mods/*`.
 *
 * It answers four questions a policy chain has to be auditable for: **what runs,
 * in what order, with what authority** · **what did each of them decide** ·
 * **what is parked waiting for a human** · **what would this tool call touch**.
 *
 * The rules this surface is built around, each with a tempting wrong reading:
 *
 * | Server says | Panel shows |
 * | --- | --- |
 * | `chain: null` | "The Gateway sent no chain list" — not "no mods" |
 * | `chain: []` | "No mods are registered" |
 * | a describe with `error` | that error, and no invented blank fields |
 * | `discrepancies: null` | *discrepancies not reported* — not *none* |
 * | `discrepancies: []` | *no discrepancies reported* |
 * | `measurable: false` | "the preview could not be computed" — never "nothing at stake" |
 * | `measurable` absent | *measurable not reported* — a third state |
 * | `entries: null` / `holds: null` | "The Gateway sent no … list" — not an empty one |
 * | `entries: []` | "No records match this filter" |
 * | either admin read 403s | the server's own sentence, verbatim |
 * | an audit read 503s | "no audit ledger is installed", verbatim — not `[]` |
 * | a command declaring `requires_approval` | its badge, plus the route's `409` verbatim when refused |
 * | `expires_at` elapsed on an `approved` hold | *expiry time passed* beside the approval |
 * | an unknown outcome / decision string | rendered verbatim in a grey badge |
 *
 * Six rules that must keep:
 *
 * 1. **Four reads, four failures.** Fleet, commands, audit and holds go
 *    through `Promise.allSettled`; a broken audit ledger must not blank a
 *    healthy chain, because "there are no records" and "the records could not
 *    be read" lead to opposite actions. Each failure names its own read.
 * 2. **Authorisation is server-owned.** There is no client-side role check —
 *    the audit and holds blocks render for every caller, and a member's 403 is
 *    rendered through `failureText`, because `errMsg` would replace the
 *    refusal with a generic sentence and the refusal *is* the answer.
 * 3. **Nothing is painted from a click.** A hold decision posts, then the list
 *    is re-read; a command run shows the response the handler returned; the
 *    preview shows what the server computed. A refused write fires nothing.
 * 4. **The panel never executes a tool.** The preview is a bounded parse plus a
 *    read-only walk, and the panel says so beside the control rather than
 *    leaving it to a doc.
 * 5. **Expiry travels beside the decision.** The store keeps
 *    `decision: "approved"` on a hold whose TTL has elapsed, so an approval is
 *    never rendered as a release: `holdExpiryView` reads `expires_at` too, and
 *    the block carries the store's own rule in words. It never gates a button —
 *    the store is the only authority on whether a hold still stands.
 * 6. **Durability is disclosed, not implied.** The journal, mod state, timers
 *    and this ledger are process-local, and the hold store is a durable file
 *    for one Gateway process; neither is cross-worker exactly-once.
 *
 * Coverage: `src/lib/mods.test.mjs` (routes, verbs, the null-preserving
 * mappers, the clamps, `failureText`, `holdExpiryView`) and
 * `src/lib/mods-view.test.mjs` (the four-place wiring and every pin above,
 * against this file).
 */

/** A count, with its absence named rather than zeroed. */
function countText(count: number | null): string {
  return count === null ? "not reported" : String(count);
}

/** An epoch-seconds stamp as an age, with its absence named. */
function timeText(seconds: number | null, label: string): string {
  if (seconds === null) return `${label} not reported`;
  const rel = relTime(seconds * 1000);
  return rel === null ? `${label} unreadable` : rel;
}

/**
 * Badge tone for a kernel outcome.
 *
 * Only `EventOutcome`'s eight known values get a colour, and every other
 * string stays grey: snapping a verdict from a newer Gateway onto a colour this
 * build associates with a different one would paint an unknown as a known.
 */
function outcomeTone(outcome: string): "green" | "blue" | "purple" | "red" | "amber" | "gray" {
  switch (outcome) {
    case "continue":
    case "observe":
      return "green";
    case "answer":
      return "blue";
    case "rewrite":
      return "purple";
    case "deny":
    case "escalate":
      return "red";
    case "defer":
    case "retry":
      return "amber";
    default:
      return "gray";
  }
}

/** Badge tone for a hold's decision — the four values `HoldDecision` declares. */
function decisionTone(decision: string): "green" | "amber" | "red" | "cyan" | "gray" {
  switch (decision) {
    case "approved":
      return "green";
    case "pending":
      return "cyan";
    case "rejected":
      return "red";
    case "expired":
      return "amber";
    default:
      return "gray";
  }
}

/**
 * The risk string stays grey and verbatim, deliberately.
 *
 * Three unrelated `RiskLevel` vocabularies exist in the harness (blast-radius
 * `R1`–`R5`, the shell analyzer's `BLOCKED/HIGH/MEDIUM`, and the reasoning
 * model's own), and a hold carries whichever its holding mod wrote. Colouring
 * one against a scale nobody declared would be a claim the payload does not
 * make, so the badge carries the holding mod's own word and nothing else.
 */
function riskTitle(): string {
  return "The risk string the holding mod recorded — vocabulary is the mod's own, so it is shown verbatim";
}

/** One sub-heading of the panel, so each block's failure can name itself. */
function Block(props: { title: string; hint?: string; testId: string; children: React.ReactNode }) {
  return (
    <div className="space-y-3" data-mods-block={props.testId}>
      <div>
        <h3 className="text-[13px] font-semibold">{props.title}</h3>
        {props.hint && <p className="text-[11px] text-muted-foreground mt-0.5">{props.hint}</p>}
      </div>
      {props.children}
    </div>
  );
}

/** One row of the ordered chain, with its declaration beside what was observed. */
function ChainRowView(props: { order: number | null; name: string; version: string | null; priority: number | null; firstParty: boolean | null; events: string[] | null; described: ModDescription | null }) {
  const described = props.described;
  const declaredHooks = manifestList(described?.declared ?? null, "hooks");
  const observedHooks = manifestList(described?.observed ?? null, "hooks");

  return (
    <div className="rounded-xl border border-border/60 px-3 py-2.5 space-y-2" data-mods-chain-row={props.name}>
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <span className="flex items-center gap-1.5 flex-wrap">
          <Badge tone="gray" title="Position in the dispatch chain, sorted by priority — not registration time">
            #{props.order === null ? "?" : props.order}
          </Badge>
          <span className="text-[11px] font-medium font-mono">{props.name || "name not reported"}</span>
          <Badge tone="gray" title="Priority the kernel dispatches on (lower runs first)">
            {props.priority === null ? "priority not reported" : `priority ${props.priority}`}
          </Badge>
          {props.firstParty === true && (
            <Badge tone="blue" title="Registered through Alpha's own trusted path">
              first-party
            </Badge>
          )}
          {props.firstParty === false && (
            <Badge tone="purple" title="Externally supplied code: capability grants and the priority ceiling apply to it">
              external
            </Badge>
          )}
          {props.firstParty === null && <Badge title="The Gateway sent no first_party claim">origin not reported</Badge>}
        </span>
        <span className="text-[10px] text-muted-foreground">{props.version === null ? "version not reported" : `v${props.version}`}</span>
      </div>

      <p className="text-[10px] text-muted-foreground">
        subscribes to {props.events === null ? "subscriptions not reported" : props.events.length === 0 ? "no events" : props.events.join(", ")}
      </p>

      {described === null ? (
        <p className="text-[11px] text-muted-foreground">No description was returned for this mod by <code>describe</code>.</p>
      ) : described.error !== null ? (
        <Notice tone="warn" message={`describe failed for ${described.name}: ${described.error}. Nothing else about this mod could be read, so no field below it is claimed.`} />
      ) : (
        <div className="space-y-1.5 text-[11px]">
          <p className="text-muted-foreground">{described.description === null ? "description not reported" : described.description}</p>
          <p>
            <span className="text-muted-foreground">granted capabilities:</span>{" "}
            {described.granted_capabilities === null ? (
              "not reported"
            ) : described.granted_capabilities.length === 0 ? (
              "none granted"
            ) : (
              described.granted_capabilities.join(", ")
            )}
            {" · "}
            <span className="text-muted-foreground">declared requirements:</span>{" "}
            {described.required_capabilities === null ? (
              "not reported"
            ) : described.required_capabilities.length === 0 ? (
              "none declared"
            ) : (
              described.required_capabilities.join(", ")
            )}
          </p>
          <p className="text-muted-foreground">
            A declared requirement is documentation, never a grant: the registration path decides what this mod may actually do.
          </p>
          <p>
            <span className="text-muted-foreground">hooks declared:</span>{" "}
            {declaredHooks === null ? "not reported" : declaredHooks.length === 0 ? "none declared" : declaredHooks.join(", ")}
            {" · "}
            <span className="text-muted-foreground">hooks observed:</span>{" "}
            {observedHooks === null ? "not reported" : observedHooks.length === 0 ? "none observed" : observedHooks.join(", ")}
          </p>
          <p>
            <span className="text-muted-foreground">manifest discrepancies:</span>{" "}
            {described.discrepancies === null ? (
              <span>discrepancies not reported — the Gateway sent no manifest comparison for this mod</span>
            ) : described.discrepancies.length === 0 ? (
              <span>no discrepancies reported — declared and observed agree</span>
            ) : (
              <span className="text-amber-700 dark:text-amber-300">{described.discrepancies.join(" · ")}</span>
            )}
          </p>
        </div>
      )}
    </div>
  );
}

export function ModsSection() {
  // Four independent reads. Each one can fail on its own: the fleet read is
  // member-readable, while audit and holds are admin-only and will 403 for a
  // caller who is not one — which is an answer, not a broken panel.
  const [fleet, setFleet] = useState<ModFleet | null>(null);
  const [fleetError, setFleetError] = useState<string | null>(null);
  const [commands, setCommands] = useState<ModCommandList | null>(null);
  const [commandsError, setCommandsError] = useState<string | null>(null);
  const [audit, setAudit] = useState<AuditPage | null>(null);
  const [auditError, setAuditError] = useState<string | null>(null);
  const [holds, setHolds] = useState<HoldList | null>(null);
  const [holdsError, setHoldsError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [auditLimit, setAuditLimit] = useState<number>(AUDIT_LIMIT_DEFAULT);
  const [auditEvent, setAuditEvent] = useState<string>("");
  const [auditMod, setAuditMod] = useState<string>("");
  const [auditOutcome, setAuditOutcome] = useState<string>("");
  // The actionable set by default — the reason this block exists is "what is
  // waiting on me". The filter is displayed above the list, so its empty state
  // is always qualified by the decision it filtered on.
  const [holdDecision, setHoldDecision] = useState<string>("pending");
  const [holdLimit, setHoldLimit] = useState<number>(HOLDS_LIMIT_DEFAULT);

  const [commandArgs, setCommandArgs] = useState<string>("");
  const [running, setRunning] = useState<string | null>(null);
  const [runResult, setRunResult] = useState<{ name: string; result: ModCommandResult } | null>(null);
  const [runError, setRunError] = useState<string | null>(null);

  const [toolName, setToolName] = useState<string>("");
  const [toolArgs, setToolArgs] = useState<string>("");
  const [previewing, setPreviewing] = useState(false);
  const [preview, setPreview] = useState<ImpactPreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewInputError, setPreviewInputError] = useState<string | null>(null);

  const [deciding, setDeciding] = useState<string | null>(null);
  const [reason, setReason] = useState<string>("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [recorded, setRecorded] = useState<HoldRecord | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    // Four reads, four outcomes: a ledger that 503s must not blank a chain
    // that answered, and an admin 403 must not look like an empty workspace.
    const [fleetResult, commandsResult, auditResult, holdsResult] = await Promise.allSettled([
      fetchModFleet(),
      fetchModCommands(),
      fetchModAudit({ limit: auditLimit, event_name: auditEvent, mod_name: auditMod, outcome: auditOutcome }),
      fetchModHolds({ decision: holdDecision, limit: holdLimit }),
    ]);
    if (fleetResult.status === "fulfilled") {
      setFleet(fleetResult.value);
      setFleetError(null);
    } else {
      setFleetError(failureText(fleetResult.reason));
    }
    if (commandsResult.status === "fulfilled") {
      setCommands(commandsResult.value);
      setCommandsError(null);
    } else {
      setCommandsError(failureText(commandsResult.reason));
    }
    if (auditResult.status === "fulfilled") {
      setAudit(auditResult.value);
      setAuditError(null);
    } else {
      setAuditError(failureText(auditResult.reason));
    }
    if (holdsResult.status === "fulfilled") {
      setHolds(holdsResult.value);
      setHoldsError(null);
    } else {
      setHoldsError(failureText(holdsResult.reason));
    }
    setLoading(false);
  }, [auditLimit, auditEvent, auditMod, auditOutcome, holdDecision, holdLimit]);

  useEffect(() => {
    void load();
  }, [load]);

  const runCommand = useCallback(async (command: ModCommand) => {
    setRunning(command.name);
    setRunError(null);
    setRunResult(null);
    try {
      // The same payload shape the catalog projection sends for a bound row:
      // `args` as a string. The mods router hands it through verbatim, so a
      // handler reads the surface it was called from.
      const result = await runModCommand(command.name, { args: commandArgs });
      setRunResult({ name: command.name, result });
    } catch (err) {
      // A 404 (no such mod command), a 403 or the route's own 409 for a
      // command declaring `requires_approval` — each keeps its own words.
      setRunError(failureText(err));
    } finally {
      setRunning(null);
    }
  }, [commandArgs]);

  const runPreview = useCallback(async () => {
    setPreview(null);
    setPreviewError(null);
    setPreviewInputError(null);
    let args: Record<string, unknown> = {};
    if (toolArgs.trim() !== "") {
      try {
        const parsed: unknown = JSON.parse(toolArgs);
        if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
          setPreviewInputError("tool_args must be a JSON object of the arguments the call would carry.");
          return;
        }
        args = parsed as Record<string, unknown>;
      } catch (err) {
        setPreviewInputError(`tool_args is not valid JSON: ${err instanceof Error ? err.message : String(err)}`);
        return;
      }
    }
    setPreviewing(true);
    try {
      setPreview(await previewImpact(toolName, args));
    } catch (err) {
      setPreviewError(failureText(err));
    } finally {
      setPreviewing(false);
    }
  }, [toolName, toolArgs]);

  const submitDecision = useCallback(
    async (holdId: string, verb: "approve" | "reject") => {
      setSubmitting(true);
      setSubmitError(null);
      setRecorded(null);
      try {
        // The response carries the store's own row (decision, operator,
        // timestamps) — but the list is re-read too, because one row's claim
        // is not the state of the queue.
        const decision = await decideHold(holdId, verb, reason);
        setRecorded(decision.hold);
        setReason("");
        setAcknowledged(false);
        setDeciding(null);
        void load();
      } catch (err) {
        // A member's 403, an unknown hold's 404, or a store that could not be
        // written — the server's sentence is the answer and is shown as-is.
        setSubmitError(failureText(err));
      } finally {
        setSubmitting(false);
      }
    },
    [reason, load],
  );

  const chain = fleet?.chain ?? null;
  const described = fleet?.mods ?? null;
  const describeByName = useMemo(() => {
    const map = new Map<string, ModDescription>();
    if (described !== null) {
      for (const entry of described) if (entry.name !== "") map.set(entry.name, entry);
    }
    return map;
  }, [described]);

  // A description whose mod has no chain row is a real inconsistency: the two
  // lists come from one response, so a mismatch is worth naming rather than
  // dropping the extra row on the floor.
  const chainNames = useMemo(() => new Set((chain ?? []).map((row) => row.mod)), [chain]);
  const unchained = useMemo(() => (described ?? []).filter((entry) => entry.name !== "" && !chainNames.has(entry.name)), [described, chainNames]);
  const failedDescriptions = useMemo(() => (described ?? []).filter((entry) => entry.error !== null).length, [described]);

  const entries = audit?.entries ?? null;
  const entryRows = holds?.holds ?? null;
  const runStatus = runResult === null ? null : runResult.result.status;
  const reasonTooLong = reason.length > HOLD_REASON_MAX_LENGTH;
  const decisionDisabled = submitting || !acknowledged || reasonTooLong;

  return (
    <Section
      title="Mod kernel"
      hint="The ordered policy chain that runs on every lead-agent tool and model event: what is installed, in what order it dispatches, what each one decided, what it is holding for a human, and what a tool call would touch. Nothing here installs, reorders or removes a mod — registration and its guards own that."
      actions={
        <Btn onClick={() => void load()} disabled={loading} title="Re-read the chain, commands, ledger and holds">
          <RefreshCw className="size-3.5" />
          {loading ? "Refreshing…" : "Refresh"}
        </Btn>
      }
    >
      <div className="space-y-6" data-mods-section="kernel">
        {fleetError !== null && (
          <Notice tone="warn" message={`The chain read failed: ${fleetError}. The commands, ledger and holds below are unaffected.`} />
        )}
        {commandsError !== null && (
          <Notice tone="warn" message={`The command list read failed: ${commandsError}. The chain above and the ledger below are unaffected.`} />
        )}
        {auditError !== null && (
          <Notice tone="warn" message={`The audit ledger read failed: ${auditError}. This block is admin-only, and an absent ledger answers 503 rather than an empty list — neither is "no records". The chain, commands and holds above and below are unaffected.`} />
        )}
        {holdsError !== null && (
          <Notice tone="warn" message={`The hold store read failed: ${holdsError}. This block is admin-only, so a member's read answers 403 — that refusal is the answer, not an empty queue. The other blocks are unaffected.`} />
        )}

        {/* ── 1. The chain ─────────────────────────────────────────── */}
        <Block
          testId="chain"
          title="Control chain"
          hint="Dispatch order, sorted by the same priority key the kernel dispatches on — never by registration time. Position #0 is outermost."
        >
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            <StatCard label="Mods registered" value={countText(fleet?.total ?? null)} sub="as the Gateway reported it in this response" />
            <StatCard
              label="Chain rows returned"
              value={chain === null ? "not reported" : String(chain.length)}
              sub={chain === null ? "the Gateway sent no chain list" : "each row is one mod, in dispatch order"}
            />
            <StatCard
              label="Descriptions that failed"
              value={described === null ? "not reported" : String(failedDescriptions)}
              sub="a describe that raised reports an error, never a blank mod"
            />
          </div>

          {loading && chain === null && <SkeletonList rows={3} />}

          {!loading && chain === null && (
            <EmptyState
              title="The Gateway sent no chain list"
              hint="This is not a claim that no mods are registered — the read either failed above or returned a response without a chain."
            />
          )}

          {chain !== null && chain.length === 0 && (
            <EmptyState title="No mods are registered" hint="The Gateway returned an empty chain: nothing subscribes to the tool or model lifecycle on this installation." />
          )}

          {unchained.length > 0 && (
            <Notice tone="warn" message={`${unchained.length} described mod(s) have no row in the dispatch chain: ${unchained.map((entry) => entry.name).join(", ")}. The two lists came from one response, so this is reported rather than dropped.`} />
          )}

          <div className="space-y-2">
            {(chain ?? []).map((row) => (
              <ChainRowView
                key={`${row.order ?? "?"}-${row.mod}`}
                order={row.order}
                name={row.mod}
                version={row.version}
                priority={row.priority}
                firstParty={row.first_party}
                events={row.subscribed_events}
                described={describeByName.get(row.mod) ?? null}
              />
            ))}
          </div>
        </Block>

        {/* ── 2. Commands ──────────────────────────────────────────── */}
        <Block
          testId="commands"
          title="Mod commands"
          hint="Commands a mod contributed: they run immediately, with no model turn and no tokens. The payload is passed through verbatim, so a handler reads the surface it was called from."
        >
          {commandsError === null && loading && commands === null && <SkeletonList rows={2} />}

          {commandsError === null && commands !== null && commands.commands === null && (
            <EmptyState title="The Gateway sent no command list" hint="An absent list is not an empty one: no claim is made about whether commands exist." />
          )}

          {commandsError === null && commands?.commands?.length === 0 && (
            <EmptyState title="No mod commands are registered" hint="No mod has contributed a command to this Gateway." />
          )}

          <label className="block space-y-1">
            <span className="text-[11px] font-semibold">Arguments string passed as `args`</span>
            <input
              className={inputCls}
              value={commandArgs}
              onChange={(e) => setCommandArgs(e.target.value)}
              placeholder="optional — sent as { args: … }"
            />
          </label>

          <div className="space-y-2">
            {(commands?.commands ?? []).map((command) => (
              <div key={command.name} className="rounded-xl border border-border/60 px-3 py-2.5 space-y-2" data-mods-command={command.name}>
                <div className="flex items-center justify-between gap-2 flex-wrap">
                  <span className="flex items-center gap-1.5 flex-wrap">
                    <span className="text-[11px] font-medium font-mono">/{command.name}</span>
                    <Badge tone="gray" title="The mod that registered this command">
                      {command.mod_name === "" ? "mod not reported" : command.mod_name}
                    </Badge>
                    {command.requires_approval && (
                      <Badge tone="amber" title="Over POST /api/mods/commands/{name} this route refuses the command with 409 — it has no approval flow. Use the hold store below.">
                        requires approval
                      </Badge>
                    )}
                  </span>
                  <Btn onClick={() => void runCommand(command)} disabled={running !== null} title="Run this command now — no model turn">
                    <Play className="size-3.5" />
                    {running === command.name ? "Running…" : "Run"}
                  </Btn>
                </div>
                <p className="text-[11px] text-muted-foreground">{command.description === "" ? "description not reported" : command.description}</p>
                <p className="text-[10px] text-muted-foreground">{timeText(command.registered_at, "registered")}</p>
              </div>
            ))}
          </div>

          {runError !== null && <ErrorBox message={runError} />}
          {runResult !== null && (
            <div className="rounded-xl border border-border/60 px-3 py-2.5 space-y-1.5" data-mods-command-result={runResult.name}>
              <p className="text-[11px] font-semibold">
                /{runResult.name} answered{" "}
                <Badge tone={runStatus === "success" ? "green" : runStatus === null || runStatus === "" ? "gray" : "amber"} title="The handler's own status string">
                  {runStatus === null || runStatus === "" ? "status not reported" : runStatus}
                </Badge>
              </p>
              <p className="text-[11px] text-muted-foreground">
                {runResult.result.mod_name === null ? "owning mod not reported" : `owned by ${runResult.result.mod_name}`}
                {runResult.result.requires_approval === null
                  ? " · approval requirement not reported"
                  : runResult.result.requires_approval
                    ? " · declares requires_approval"
                    : " · no approval required"}
              </p>
              <pre className="text-[11px] font-mono whitespace-pre-wrap break-all bg-muted/40 rounded-lg p-2">
                {runResult.result.output === null ? "output not reported" : runResult.result.output}
              </pre>
            </div>
          )}
        </Block>

        {/* ── 3. Impact preview ────────────────────────────────────── */}
        <Block
          testId="preview"
          title="Impact preview"
          hint="A bounded estimate of what a tool call would touch. It never runs the tool: bounded command parsing plus a read-only filesystem walk."
        >
          <Notice tone="neutral" message="This preview executes nothing. `measurable: false` means the preview could not be computed — it is never a claim that nothing is at stake." />

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 items-end">
            <label className="block space-y-1">
              <span className="text-[11px] font-semibold">Tool name</span>
              <input className={inputCls} value={toolName} onChange={(e) => setToolName(e.target.value)} placeholder="run_command" />
            </label>
            <label className="block space-y-1 sm:col-span-2">
              <span className="text-[11px] font-semibold">Arguments as JSON</span>
              <input className={inputCls} value={toolArgs} onChange={(e) => setToolArgs(e.target.value)} placeholder='{ "cmd": "ls" }' />
            </label>
          </div>
          <Btn onClick={() => void runPreview()} disabled={previewing || toolName.trim() === ""} title="Compute the estimate without running the tool">
            <Search className="size-3.5" />
            {previewing ? "Computing…" : "Preview impact"}
          </Btn>

          {previewInputError !== null && <ErrorBox message={previewInputError} />}
          {previewError !== null && <ErrorBox message={previewError} />}

          {preview !== null && (
            <div className="rounded-xl border border-border/60 px-3 py-2.5 space-y-2" data-mods-preview="result">
              <div className="flex items-center gap-1.5 flex-wrap">
                <Badge tone="gray" title="What kind of action the preview classified this as">
                  {preview.kind === "" ? "kind not reported" : preview.kind}
                </Badge>
                {preview.measurable === true && (
                  <Badge tone="green" title="The preview bounded this action and reported its figures">
                    measurable
                  </Badge>
                )}
                {preview.measurable === false && (
                  <Badge tone="amber" title="The preview could not be computed — this says nothing about the action's blast radius">
                    could not compute
                  </Badge>
                )}
                {preview.measurable === null && (
                  <Badge title="The Gateway sent no measurable claim for this preview — a third state, neither measured nor unmeasurable">
                    measurable not reported
                  </Badge>
                )}
                {preview.truncated === true && (
                  <Badge tone="amber" title="The preview hit its bound: the list below is not the complete set">
                    listing truncated
                  </Badge>
                )}
                <span className="text-[10px] text-muted-foreground">
                  size {preview.estimated_bytes === null ? "not reported" : `${preview.estimated_bytes} bytes`}
                </span>
              </div>

              <p className="text-[11px]">{preview.summary === "" ? "summary not reported" : preview.summary}</p>
              {preview.reason !== null && preview.reason !== "" && <p className="text-[11px] text-muted-foreground">{preview.reason}</p>}

              <p className="text-[11px]">
                <span className="text-muted-foreground">affected paths:</span>{" "}
                {preview.affected_paths === null ? (
                  "paths not reported — the preview sent no list"
                ) : preview.affected_paths.length === 0 ? (
                  "no paths reported by this preview"
                ) : (
                  <span className="font-mono break-all">{preview.affected_paths.slice(0, 20).join(" · ")}</span>
                )}
              </p>
              {preview.affected_paths !== null && preview.affected_paths.length > 20 && (
                <p className="text-[10px] text-muted-foreground">
                  showing the first 20 of {preview.affected_paths.length} paths — the rest are hidden by this panel's own bound, which is disclosed
                  rather than counted as the whole list.
                </p>
              )}
              {preview.evidence !== null && (
                <details className="text-[10px] text-muted-foreground">
                  <summary className="cursor-pointer">evidence the preview recorded</summary>
                  <pre className="whitespace-pre-wrap break-all font-mono mt-1">{JSON.stringify(preview.evidence, null, 2)}</pre>
                </details>
              )}
            </div>
          )}
        </Block>

        {/* ── 4. Audit ledger (admin) ──────────────────────────────── */}
        <Block
          testId="audit"
          title="Audit ledger"
          hint="Admin-only: what the chain decided, event by event, with the reason and the mods that ran. Payloads are redacted server-side and only digests cross this API."
        >
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-2 items-end">
            <label className="block space-y-1">
              <span className="text-[11px] font-semibold">Event</span>
              <input className={inputCls} value={auditEvent} onChange={(e) => setAuditEvent(e.target.value)} placeholder="tool.requested" />
            </label>
            <label className="block space-y-1">
              <span className="text-[11px] font-semibold">Mod</span>
              <input className={inputCls} value={auditMod} onChange={(e) => setAuditMod(e.target.value)} placeholder="sec-default" />
            </label>
            <label className="block space-y-1">
              <span className="text-[11px] font-semibold">Outcome</span>
              <input className={inputCls} value={auditOutcome} onChange={(e) => setAuditOutcome(e.target.value)} placeholder="deny" />
            </label>
            <label className="block space-y-1">
              <span className="text-[11px] font-semibold">Rows</span>
              <select className={inputCls} value={auditLimit} onChange={(e) => setAuditLimit(Number(e.target.value))}>
                {[25, 50, 100, 250, 500, 1000].map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </label>
            <p className="text-[10px] text-muted-foreground">
              Filters are exact matches. The window is 1–1000 rows, newest last.
            </p>
          </div>

          {auditError === null && loading && audit === null && <SkeletonList rows={3} />}

          {auditError === null && audit !== null && audit.stats !== null && (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <StatCard label="Retained records" value={countText(audit.stats.retained)} sub={`of ${countText(audit.stats.capacity)} capacity`} />
              <StatCard label="Distinct events" value={countText(audit.stats.events)} sub="within the retained window only" />
              <StatCard label="By outcome" value={audit.stats.by_outcome === null ? "not reported" : String(Object.keys(audit.stats.by_outcome).length)} sub="outcome keys the ledger counted" />
              <StatCard label="Mods counted" value={audit.stats.by_mod === null ? "not reported" : String(Object.keys(audit.stats.by_mod).length)} sub="mods with at least one record" />
            </div>
          )}

          <p className="text-[10px] text-muted-foreground">
            The ledger is process-local and bounded to its retention window: it counts what this process has seen since it started, and it is not a
            durable audit trail across restarts or workers.
          </p>

          {auditError === null && loading && audit === null ? null : auditError === null && entries === null ? (
            <EmptyState title="The Gateway sent no entry list" hint="An absent list is not an empty one — this reads as 'no records were sent', not 'nothing was recorded'." />
          ) : auditError === null && entries?.length === 0 ? (
            <EmptyState title="No records match this filter" hint="The ledger answered with an empty list for these filters. Clear a filter to widen the window." />
          ) : null}

          <div className="space-y-2">
            {(entries ?? []).map((entry, index) => (
              <AuditRow key={`${entry.event_id ?? index}-${entry.timestamp ?? index}`} entry={entry} />
            ))}
          </div>
        </Block>

        {/* ── 5. Holds (admin) ─────────────────────────────────────── */}
        <Block
          testId="holds"
          title="Held actions"
          hint="Admin-only: actions a mod parked with DEFER, waiting for an operator. Deciding records you as the authenticated caller — the body never supplies the operator's name."
        >
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 items-end">
            <label className="block space-y-1">
              <span className="text-[11px] font-semibold">Decision</span>
              <select className={inputCls} value={holdDecision} onChange={(e) => setHoldDecision(e.target.value)}>
                <option value="">all decisions</option>
                {HOLD_DECISIONS.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </label>
            <label className="block space-y-1">
              <span className="text-[11px] font-semibold">Rows</span>
              <select className={inputCls} value={holdLimit} onChange={(e) => setHoldLimit(Number(e.target.value))}>
                {[25, 50, 100, 250, 500].map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </label>
            <p className="text-[10px] text-muted-foreground sm:col-span-2">
              Store:{" "}
              <code className="break-all">{holds?.store_file ?? "path not reported"}</code>
              {holds?.store_file === null || holds === null ? "" : " — a durable file for one Gateway process, not a shared multi-worker approval store."}
            </p>
          </div>

          {holdsError === null && loading && holds === null && <SkeletonList rows={2} />}

          {holdsError === null && holds !== null && entryRows === null && (
            <EmptyState title="The Gateway sent no hold list" hint="An absent list is not an empty one — no claim is made about what is held." />
          )}

          {holdsError === null && entryRows?.length === 0 && (
            <EmptyState
              title={holdDecision === "" ? "No holds match this filter" : `No holds are recorded as "${holdDecision}"`}
              hint="The store answered with an empty list for this filter. Widen the decision to see the rest of the queue."
            />
          )}

          <Notice tone="neutral" message="Expiry is time-based: an expired hold voids even an approved decision, and the prior verdict is kept for audit. The badge below reads the timestamp against this browser's clock and is disclosure only — the store decides whether a hold stands, never this panel." />

          {submitError !== null && <Notice tone="warn" message={submitError} />}
          {recorded !== null && (
            <Notice
              tone="success"
              message={`Recorded ${recorded.decision === "" ? "a decision" : recorded.decision} on ${recorded.hold_id || "the hold"}${
                recorded.decided_by === null || recorded.decided_by === "" ? "" : ` — operator ${recorded.decided_by}`
              }. ${timeText(recorded.decided_at, "decided")} — the list below has been re-read.`}
            />
          )}

          <div className="space-y-2">
            {(entryRows ?? []).map((hold) => {
              const expiry = holdExpiryView(hold);
              const open = deciding === hold.hold_id;
              return (
                <div key={hold.hold_id} className="rounded-xl border border-border/60 px-3 py-2.5 space-y-2" data-mods-hold={hold.hold_id}>
                  <div className="flex items-center justify-between gap-2 flex-wrap">
                    <span className="flex items-center gap-1.5 flex-wrap">
                      <span className="text-[11px] font-medium font-mono">{hold.hold_id === "" ? "id not reported" : hold.hold_id}</span>
                      <Badge tone="gray" title="The tool that was parked">{hold.tool_name === "" ? "tool not reported" : hold.tool_name}</Badge>
                      <Badge tone={decisionTone(hold.decision)} title="The store's own decision string — an expired hold keeps the verdict it had">
                        {hold.decision === "" ? "decision not reported" : hold.decision}
                      </Badge>
                      <Badge title={riskTitle()}>{hold.risk_level === "" ? "risk not reported" : hold.risk_level}</Badge>
                      <Badge tone={expiry.tone} title="Read from expires_at against this browser's clock; the store is the authority">
                        {expiry.label}
                      </Badge>
                    </span>
                    <Btn
                      variant={open ? "primary" : "ghost"}
                      onClick={() => {
                        setDeciding(open ? null : hold.hold_id);
                        setSubmitError(null);
                        setRecorded(null);
                        setReason("");
                        setAcknowledged(false);
                      }}
                      disabled={submitting}
                    >
                      {open ? "Close" : "Decide"}
                    </Btn>
                  </div>

                  <p className="text-[11px] text-muted-foreground">
                    {hold.reason === "" ? "no reason recorded on this hold" : hold.reason}
                  </p>

                  <div className="text-[10px] text-muted-foreground flex items-center gap-2 flex-wrap">
                    <span>{holdAge(hold) === null ? "created not reported" : `created ${holdAge(hold)}`}</span>
                    <span>
                      {hold.decided_at === null
                        ? "no decision timestamp recorded"
                        : `decided ${absoluteStamp(hold.decided_at * 1000) ?? "at an unreadable time"}`}
                    </span>
                    <span>
                      {hold.decided_by === null || hold.decided_by === "" ? "no operator recorded" : `operator ${hold.decided_by}`}
                    </span>
                    <span>run {hold.run_id === "" ? "not reported" : hold.run_id}</span>
                    <span className="font-mono break-all">key {hold.idempotency_key === "" ? "not reported" : hold.idempotency_key}</span>
                  </div>

                  {hold.decision_reason !== null && hold.decision_reason !== "" && (
                    <p className="text-[11px]">Decision reason: {hold.decision_reason}</p>
                  )}

                  {expiry.past && (
                    <p className="text-[10px] text-amber-700 dark:text-amber-300">
                      The expiry stamp on this hold has passed by this browser's clock{hold.decision === "approved" ? ", and the hold reads approved" : ""}.
                      Expiry voids the decision rather than rewriting it, so a decision here is a record — not a release. The store refuses or
                      accepts on its own rule.
                    </p>
                  )}

                  {open && (
                    <div className="space-y-2 border-t border-border/50 pt-3">
                      <p className="text-[11px] text-muted-foreground">
                        Record why this action should run or not. This writes a decision to the durable hold store for one Gateway process; it never
                        starts, cancels or resumes a run. The server decides whether you may — a refusal is shown as it was written.
                      </p>
                      <label className="block space-y-1">
                        <span className="text-[11px] font-semibold">Decision reason — what was checked to make it</span>
                        <textarea
                          className={`${inputCls} min-h-20`}
                          value={reason}
                          onChange={(e) => setReason(e.target.value)}
                          rows={3}
                        />
                        <span className={`block text-[11px] font-normal ${reasonTooLong ? "text-amber-700 dark:text-amber-300" : "text-muted-foreground"}`}>
                          {reason.length}/{HOLD_REASON_MAX_LENGTH} characters — the server accepts 0–{HOLD_REASON_MAX_LENGTH} and stores this as the
                          decision's reason.
                        </span>
                      </label>
                      <label className="flex items-start gap-2 text-[11px]">
                        <input
                          type="checkbox"
                          className="mt-0.5"
                          checked={acknowledged}
                          onChange={(e) => setAcknowledged(e.target.checked)}
                        />
                        <span>
                          I have read what this hold was parked for. Approving releases the action the holding mod refused to run on its own; rejecting
                          keeps it refused. Either way the decision is recorded with my account as the operator.
                        </span>
                      </label>
                      <div className="flex items-center gap-2 flex-wrap">
                        <Btn onClick={() => void submitDecision(hold.hold_id, "approve")} disabled={decisionDisabled} title="Record an approval">
                          {submitting ? "Recording…" : "Approve"}
                        </Btn>
                        <Btn variant="danger" onClick={() => void submitDecision(hold.hold_id, "reject")} disabled={decisionDisabled} title="Record a rejection">
                          {submitting ? "Recording…" : "Reject"}
                        </Btn>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </Block>

        <p className="text-[10px] text-muted-foreground border-t border-border/50 pt-3">
          What is durable here: the hold store (an atomic file for one Gateway process). What is not: the event journal behind the audit ledger, the
          mod-scoped capability store, the timers and the UI-card store — all process-local, all lost on restart, and none of them cross-worker
          exactly-once. This panel reports those bounds rather than implying they are absent.
        </p>
      </div>
    </Section>
  );
}

/** One audit record. Payloads stay on the server; only the digest crosses. */
function AuditRow(props: { entry: AuditEntry }) {
  const entry = props.entry;
  return (
    <div className="rounded-xl border border-border/60 px-3 py-2.5 space-y-1.5" data-mods-audit={entry.event_id ?? entry.event}>
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <span className="flex items-center gap-1.5 flex-wrap">
          <span className="text-[11px] font-medium font-mono">{entry.event === "" ? "event not reported" : entry.event}</span>
          <Badge tone={outcomeTone(entry.outcome)} title="The kernel's own outcome string">
            {entry.outcome === "" ? "outcome not reported" : entry.outcome}
          </Badge>
          {(entry.chain ?? []).map((mod) => (
            <Badge key={mod} tone="gray" title="A mod that ran on this event">
              {mod}
            </Badge>
          ))}
        </span>
        <span className="text-[10px] text-muted-foreground">
          {timeText(entry.timestamp, "recorded")} · {entry.duration_ms === null ? "duration not reported" : `${entry.duration_ms} ms`}
        </span>
      </div>

      {entry.reason !== null && entry.reason !== "" && <p className="text-[11px] text-muted-foreground">{entry.reason}</p>}

      {entry.rewrites !== null && entry.rewrites.length > 0 && (
        <p className="text-[11px]">
          <span className="text-muted-foreground">rewritten by:</span>{" "}
          {entry.rewrites.map((rewrite) => `${rewrite.mod ?? "mod not reported"} (${rewrite.reason ?? "no reason recorded"})`).join(" · ")}
        </p>
      )}

      <p className="text-[10px] text-muted-foreground font-mono break-all">
        payload {entry.payload_digest ?? "digest not reported"}
        {entry.source === null ? "" : ` · source ${entry.source}`}
        {entry.event_id === null ? "" : ` · ${entry.event_id}`} — the payload itself is redacted server-side and never crosses this API.
      </p>
    </div>
  );
}
