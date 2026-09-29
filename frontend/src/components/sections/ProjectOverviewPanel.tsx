"use client";

import React, { useEffect, useState } from "react";
import {
  fetchProjectDetail,
  type DetailSection,
  type ProjectConstitution,
  type ProjectDecision,
  type ProjectDetail,
  type ProjectEvent,
  type ProjectPendingWork,
  type ProjectStateDigest,
} from "@/lib/project-detail";
import { Badge, Btn, ErrorBox, SkeletonList } from "@/components/ui";
import { Activity, Gavel, RefreshCw, ShieldCheck } from "lucide-react";

/**
 * Everything the Gateway already knows about one project, in one place.
 *
 * The Projects list used to answer "which projects exist" and nothing else:
 * `instructions` (in the payload, never rendered), `created_at` /
 * `updated_at` (same), the project id, and every one of the ~22 read-only
 * `GET /projects/{id}/*` routes were invisible without a trip to another view
 * that keeps its own project dropdown — one that defaults to the *first*
 * project, not the one you were looking at.
 *
 * This reads the compact ones and shows them inline, and links to the live
 * control surface rather than reimplementing it.
 *
 * Honesty rules, which are the whole point:
 *  - Each section is independent. One route that fails states its own reason
 *    and the rest still render; the panel says up front when it is partial.
 *  - A count the server did not send is "unknown", never `0`.
 *  - `last_verified: null` is "never verified", never a green tick.
 *  - An empty list is the server saying there is nothing, and reads as such.
 */

/** A measured number, or the honest "not reported" when the field was absent. */
const Counter = (props: { label: string; value: number | null }) => (
  <div className="rounded-lg border border-border/60 bg-card px-2.5 py-1.5">
    <p className="text-[10px] text-muted-foreground">{props.label}</p>
    <p
      className={`text-sm font-semibold tabular-nums ${props.value === null ? "text-muted-foreground" : ""}`}
      title={props.value === null ? "The server did not report this value." : undefined}
    >
      {props.value === null ? "—" : props.value}
    </p>
  </div>
);

/**
 * A timestamp, or an explicit unknown.
 *
 * `new Date("")` is the epoch, and rendering that would claim the event happened
 * in 1970 — so an absent timestamp is never passed to `Date`.
 */
const When = (props: { value: string; prefix?: string }) => {
  if (!props.value) {
    return <span className="text-muted-foreground">{props.prefix ? `${props.prefix} ` : ""}time not reported</span>;
  }
  const parsed = new Date(props.value);
  const label = Number.isNaN(parsed.getTime()) ? props.value : parsed.toLocaleString();
  return (
    <time dateTime={props.value} title={props.value}>
      {props.prefix ? `${props.prefix} ` : ""}
      {label}
    </time>
  );
};

const Subhead = (props: { icon: React.ReactNode; title: string; right?: React.ReactNode }) => (
  <div className="flex items-center gap-1.5 flex-wrap">
    <p className="text-[11px] font-semibold inline-flex items-center gap-1.5">
      {props.icon}
      {props.title}
    </p>
    {props.right ? <div className="ml-auto flex items-center gap-1.5">{props.right}</div> : null}
  </div>
);

/** A section that failed states the server's reason rather than disappearing. */
function Failed(props: { label: string; section: DetailSection<unknown> }) {
  if (props.section.status !== "error") return null;
  return (
    <div className="rounded-lg border border-destructive/40 bg-destructive/5 px-2.5 py-2">
      <p className="text-[11px] font-semibold text-destructive">{props.label} could not be read</p>
      <p className="text-[11px] text-destructive/90 mt-0.5 break-words">{props.section.error}</p>
    </div>
  );
}

function StateBlock(props: { section: DetailSection<ProjectStateDigest> }) {
  const { section } = props;
  if (section.status === "error") return <Failed label="Project state" section={section} />;
  const s = section.data;
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-1.5">
        <Counter label="active tasks" value={s.active_tasks} />
        <Counter label="blocked" value={s.blocked_tasks} />
        <Counter label="completed" value={s.completed_tasks} />
        <Counter label="failed" value={s.failed_tasks} />
        <Counter label="active agents" value={s.active_agents} />
        <Counter label="open conflicts" value={s.open_conflicts} />
        <div className="rounded-lg border border-border/60 bg-card px-2.5 py-1.5">
          <p className="text-[10px] text-muted-foreground">phase</p>
          <p className="text-sm font-semibold truncate" title={s.phase || "The server reported no phase."}>
            {s.phase || <span className="text-muted-foreground font-normal">not reported</span>}
          </p>
        </div>
        <div className="rounded-lg border border-border/60 bg-card px-2.5 py-1.5">
          <p className="text-[10px] text-muted-foreground">arch version</p>
          <p className="text-sm font-semibold font-mono truncate">
            {s.arch_version || <span className="text-muted-foreground font-normal">not reported</span>}
          </p>
        </div>
      </div>
      <div className="text-[11px] space-y-1">
        <p>
          <span className="text-muted-foreground">Goal: </span>
          {s.goal ? <span className="whitespace-pre-wrap">{s.goal}</span> : <span className="text-muted-foreground">none recorded</span>}
        </p>
        <p>
          <span className="text-muted-foreground">Last verified: </span>
          {s.last_verified ? (
            // The server sent a timestamp, so the project has been verified. It
            // is NOT "verified" merely because this panel rendered.
            <span className="text-emerald-600 dark:text-emerald-400">
              <When value={s.last_verified} />
            </span>
          ) : (
            <span className="text-muted-foreground">never verified</span>
          )}
        </p>
        <p>
          <span className="text-muted-foreground">Latest decision: </span>
          {s.latest_decision || <span className="text-muted-foreground">none recorded</span>}
        </p>
        {s.updated_at ? (
          <p className="text-muted-foreground">
            <When value={s.updated_at} prefix="State last changed" />
          </p>
        ) : null}
      </div>
      {s.open_risks.length > 0 && (
        <div className="rounded-lg border border-amber-500/40 bg-amber-500/5 px-2.5 py-2">
          <p className="text-[11px] font-semibold text-amber-700 dark:text-amber-400">
            Open risks ({s.open_risks.length})
          </p>
          <ul className="mt-1 space-y-0.5">
            {s.open_risks.map((risk, i) => (
              <li key={i} className="text-[11px] text-amber-700/90 dark:text-amber-400/90">
                {risk || <span className="italic text-muted-foreground">the server sent a risk with no text</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function ConstitutionBlock(props: { section: DetailSection<ProjectConstitution> }) {
  const { section } = props;
  if (section.status === "error") return <Failed label="Constitution" section={section} />;
  const c = section.data;
  return (
    <div className="text-[11px] space-y-1">
      {c.present ? (
        <p className="text-emerald-600 dark:text-emerald-400">A constitution is in place for this project.</p>
      ) : (
        <p className="text-muted-foreground">
          No constitution yet.{" "}
          {c.template ? "The Gateway has a template it can seed one from." : ""}
        </p>
      )}
      {c.template && (
        <details className="rounded-lg border border-border/60 bg-card px-2.5 py-1.5">
          <summary className="cursor-pointer text-[11px] font-medium">Show the template</summary>
          <pre className="mt-1.5 max-h-48 overflow-auto whitespace-pre-wrap break-words text-[10px] text-muted-foreground">
            {c.template}
          </pre>
        </details>
      )}
    </div>
  );
}

function PendingBlock(props: {
  section: DetailSection<ProjectPendingWork>;
  /** Counted from the decisions read, so there is one source and one request. */
  decisions: DetailSection<ProjectDecision[]>;
}) {
  const { section } = props;
  if (section.status === "error") return <Failed label="Pending work" section={section} />;
  const p = section.data;
  const rows: Array<[string, number]> = [
    ["Open approvals", p.approvals],
    ["Active locks", p.locks],
    ["Lock requests", p.lock_requests],
    ["Checkpoints", p.checkpoints],
    ["Handoffs", p.handoffs],
    // Unknown when the decisions read failed, rather than a second, possibly
    // disagreeing read of the same route.
    ["Decisions", props.decisions.status === "ok" ? props.decisions.data.length : 0],
  ];
  const waiting = p.approvals + p.lock_requests;
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap gap-1.5">
        {rows.map(([label, value]) => (
          <span
            key={label}
            className={`rounded-full px-2 py-0.5 text-[10px] font-medium border ${
              value > 0 ? "border-border/70 bg-card text-foreground" : "border-border/40 bg-muted/40 text-muted-foreground"
            }`}
            title={label === "Decisions" && props.decisions.status !== "ok" ? "The decisions read failed, so this count is unknown." : undefined}
          >
            {label} {label === "Decisions" && props.decisions.status !== "ok" ? "—" : value}
          </span>
        ))}
      </div>
      {waiting === 0 ? (
        <p className="text-[11px] text-muted-foreground">Nothing is waiting on you in this project.</p>
      ) : (
        <p className="text-[11px]">
          <span className="text-muted-foreground">Waiting on you: </span>
          {waiting} item{waiting === 1 ? "" : "s"}. Approvals and lock requests are actioned in the project’s live
          control view.
        </p>
      )}
    </div>
  );
}

function DecisionsBlock(props: { section: DetailSection<ProjectDecision[]> }) {
  const { section } = props;
  if (section.status === "error") return <Failed label="Decisions" section={section} />;
  if (section.data.length === 0) {
    return <p className="text-[11px] text-muted-foreground">No decisions recorded yet.</p>;
  }
  return (
    <ul className="space-y-1.5">
      {section.data.map((d) => (
        <li key={d.id} className="rounded-lg border border-border/60 bg-card px-2.5 py-1.5">
          <div className="flex items-center gap-1.5 flex-wrap">
            <p className="text-[11px] font-semibold">{d.title || <span className="text-muted-foreground">untitled decision</span>}</p>
            {d.approved_by ? (
              <Badge tone="green">approved by {d.approved_by}</Badge>
            ) : (
              <Badge tone="gray">not approved</Badge>
            )}
          </div>
          {d.body ? <p className="text-[11px] text-muted-foreground mt-0.5 whitespace-pre-wrap">{d.body}</p> : null}
          {d.reason ? <p className="text-[11px] text-muted-foreground mt-0.5">Why: {d.reason}</p> : null}
          <p className="text-[10px] text-muted-foreground mt-0.5">
            {d.made_by ? `by ${d.made_by} · ` : ""}
            <When value={d.created_at} />
          </p>
        </li>
      ))}
    </ul>
  );
}

function EventsBlock(props: { section: DetailSection<ProjectEvent[]> }) {
  const { section } = props;
  if (section.status === "error") return <Failed label="Activity" section={section} />;
  if (section.data.length === 0) {
    return <p className="text-[11px] text-muted-foreground">No activity recorded yet.</p>;
  }
  return (
    <ol className="space-y-1">
      {section.data.map((e) => (
        <li key={e.id} className="flex items-baseline gap-2 text-[11px]">
          <span className="text-muted-foreground shrink-0 font-mono text-[10px]">
            <When value={e.created_at} />
          </span>
          <span className="font-medium shrink-0">{e.type || <span className="text-muted-foreground italic">untyped</span>}</span>
          {e.actor ? <span className="text-muted-foreground shrink-0">by {e.actor}</span> : null}
          {e.payload ? (
            <span className="text-muted-foreground truncate min-w-0 font-mono text-[10px]" title={JSON.stringify(e.payload, null, 2)}>
              {JSON.stringify(e.payload)}
            </span>
          ) : null}
        </li>
      ))}
    </ol>
  );
}

export function ProjectOverviewPanel(props: {
  projectId: string;
  /** Opens the live per-project control surface with THIS project selected. */
  onOpenLive?: (projectId: string) => void;
}) {
  const [detail, setDetail] = useState<ProjectDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      // fetchProjectDetail resolves even when sections failed, so `error` here is
      // only for a failure of the read as a whole, which cannot currently happen
      // but would still be surfaced rather than swallowed.
      setDetail(await fetchProjectDetail(props.projectId));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setDetail(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // Re-read when the project changes; an explicit Refresh re-runs it too.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.projectId]);

  // Named explicitly rather than by walking Object.keys: `partial` is a boolean,
  // and a filter that reaches for `.status` on it is one refactor away from
  // quietly reporting every section as failed.
  const failedSections = detail
    ? ([
        ["state", detail.state],
        ["pending work", detail.pending],
        ["constitution", detail.constitution],
        ["decisions", detail.decisions],
        ["activity", detail.events],
      ] as Array<[string, DetailSection<unknown>]>)
        .filter(([, section]) => section.status === "error")
        .map(([label]) => label)
    : [];

  return (
    <div className="space-y-2.5">
      <Subhead
        icon={<ShieldCheck className="size-3.5 text-primary" />}
        title="Project detail"
        right={
          <>
            {props.onOpenLive && (
              <Btn variant="ghost" onClick={() => props.onOpenLive?.(props.projectId)} title="Open the live per-project control surface with this project selected">
                <Activity className="size-3.5" /> Live controls
              </Btn>
            )}
            <Btn variant="ghost" onClick={() => void load()} disabled={loading}>
              <RefreshCw className="size-3.5" /> {loading ? "Refreshing…" : "Refresh"}
            </Btn>
          </>
        }
      />

      {error && <ErrorBox message={error} onRetry={() => void load()} />}

      {loading && !detail ? (
        <SkeletonList rows={3} />
      ) : !detail ? null : (
        <>
          {detail.partial && (
            <p className="rounded-lg border border-amber-500/40 bg-amber-500/5 px-2.5 py-1.5 text-[11px] text-amber-700 dark:text-amber-400">
              Partly unreadable: {failedSections.length} section{failedSections.length === 1 ? "" : "s"} did not answer (
              {failedSections.join(", ")}). Everything else below is the server’s own data.
            </p>
          )}

          <StateBlock section={detail.state} />
          <PendingBlock section={detail.pending} decisions={detail.decisions} />
          <ConstitutionBlock section={detail.constitution} />

          <div className="space-y-1.5 border-t border-border/50 pt-2.5">
            <Subhead icon={<Gavel className="size-3.5 text-primary" />} title="Decisions" />
            <DecisionsBlock section={detail.decisions} />
          </div>

          <div className="space-y-1.5 border-t border-border/50 pt-2.5">
            <Subhead icon={<Activity className="size-3.5 text-primary" />} title="Activity" />
            <EventsBlock section={detail.events} />
          </div>
        </>
      )}
    </div>
  );
}
