"use client";

import { useEffect, useState } from "react";
import { Cpu, Radio, ShieldCheck } from "lucide-react";

import { Badge, Btn, ErrorBox, Notice, Section, SkeletonList } from "@/components/ui";
import {
  APEX_PROFILES,
  fetchApexPolicy,
  fetchApexStatus,
  type ApexBlock,
  type ApexContract,
  type ApexProfile,
  type ApexStatus,
  runApexCycle,
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
 * **This panel starts nothing.** "Run one cycle" records a decision; the
 * Gateway's supervisor loop and a host adapter perform work. The button is
 * labelled accordingly.
 */

/**
 * A block the backend could not read.
 *
 * `Notice` takes a `message` string rather than children, so the emphasis is
 * carried in the text. Rendering it as a zeroed card instead would present an
 * unmeasured subsystem as a working one — the exact failure this surface's
 * mappers exist to prevent.
 */
function Unavailable({ block, label }: { block: ApexBlock<unknown>; label: string }) {
  if (block.available) return null;
  return <Notice tone="warn" message={`${label} unavailable — ${block.reason}`} />;
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
          <Badge tone={contract.enabled ? "green" : "gray"}>{contract.enabled ? contract.profile : "off"}</Badge>
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
          <dd>{contract.budget.max_tool_calls.toLocaleString()}</dd>
        </div>
        <div>
          <dt className="text-neutral-500">Max agents</dt>
          <dd>{contract.budget.max_active_agents}</dd>
        </div>
        <div>
          <dt className="text-neutral-500">Delegation depth</dt>
          <dd>{contract.budget.max_delegation_depth}</dd>
        </div>
        <div>
          <dt className="text-neutral-500">Runtime ceiling</dt>
          <dd>{contract.budget.max_runtime_minutes} min</dd>
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
        <Badge tone={contract.policy_sites_missing.length === 0 ? "green" : "amber"}>
          {contract.policy_sites_live.length}/{contract.policy_sites.length} live
        </Badge>
      </div>
      <p className="text-xs text-neutral-500">
        APEX composes these; it is not a second policy kernel. A missing one is an unenforced
        boundary.
      </p>
      <ul className="space-y-1 text-xs">
        {contract.policy_sites.map((site) => {
          const live = contract.policy_sites_live.includes(site.name);
          return (
            <li key={site.name} className="flex items-start gap-2">
              <span className={live ? "text-emerald-600" : "text-amber-600"}>{live ? "●" : "○"}</span>
              <span className="min-w-0">
                <span className="font-medium">{site.name}</span>
                <code className="block truncate text-neutral-500">{site.module}</code>
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function InvariantsCard({ report }: { report: NonNullable<ApexStatus["invariants"]> }) {
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
            <span className={invariant.live ? "text-emerald-600" : "text-amber-600"}>
              {invariant.live ? "●" : "○"}
            </span>
            <span className="min-w-0">
              <span className="font-mono">{invariant.id}</span> {invariant.statement}
              <code className="block truncate text-neutral-500">
                {invariant.module}:{invariant.symbol}
                {!invariant.live && invariant.reason ? ` — ${invariant.reason}` : ""}
              </code>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function ApexSection() {
  const [status, setStatus] = useState<ApexStatus | null>(null);
  const [policy, setPolicy] = useState<ApexContract | null>(null);
  const [profile, setProfile] = useState<ApexProfile>("autonomous");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [next, nextPolicy] = await Promise.all([fetchApexStatus(), fetchApexPolicy(profile)]);
        if (!cancelled) {
          setStatus(next);
          setPolicy(nextPolicy);
        }
      } catch (exc) {
        if (!cancelled) setError(exc instanceof Error ? exc.message : String(exc));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [profile]);

  const refresh = async () => {
    setError(null);
    try {
      setStatus(await fetchApexStatus());
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  };

  const runCycle = async () => {
    const sessionId = status?.session?.available ? status.session.session_id : null;
    if (!sessionId) return;
    setBusy(true);
    setError(null);
    try {
      // Records a decision only. No tool runs and no run is created here.
      await runApexCycle(sessionId);
      setStatus(await fetchApexStatus({ sessionId }));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
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
          <select
            aria-label="APEX profile"
            className="rounded-md border border-neutral-300 bg-transparent px-2 py-1 text-sm dark:border-neutral-700"
            value={profile}
            onChange={(event) => setProfile(event.target.value as ApexProfile)}
          >
            {APEX_PROFILES.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
          <Btn onClick={runCycle} disabled={busy}>
            {busy ? "Running…" : "Run one cycle"}
          </Btn>
          <Btn onClick={refresh}>Refresh</Btn>
        </>
      }
    >
      <div className="space-y-3">
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
              Sessions: <strong>{status.sessions.total ?? 0}</strong>
              {status.sessions.active !== null && ` · ${status.sessions.active} active`}
            </span>
            {Object.entries(status.sessions.by_state).length === 0 && (
              <span className="text-neutral-500">none recorded</span>
            )}
          </div>
        ) : (
          <Unavailable block={status.sessions} label="Session store" />
        )}

        <PolicySitesCard contract={policy} />

        {status.invariants ? <InvariantsCard report={status.invariants} /> : null}

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
          message="APEX records decisions. It does not run tools, start runs, or complete a mission — an acceptance report in which every criterion was evaluated and held is the only path to COMPLETED."
        />

        {error && <ErrorBox message={error} />}
      </div>
    </Section>
  );
}