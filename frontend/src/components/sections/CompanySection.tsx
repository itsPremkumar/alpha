"use client";

/**
 * CompanySection — the operator's control plane for autonomous organizations.
 *
 * Structure mirrors the domain rather than the API: a portfolio of companies,
 * then per-company charter, org chart, workforce, work, budget, loop and the
 * approval queue. Every panel renders server-reported state and says which of
 * three things it knows: measured, unreadable, or not yet configured.
 *
 * The honesty rules this section enforces:
 *   - loading, empty and unavailable are three distinct states;
 *   - a subsystem that could not be read renders an ErrorBox naming the reason,
 *     never as an empty list;
 *   - health, spend and tick cost render as "not measured" when the server sent
 *     no measurement, never as 0;
 *   - a disabled loop renders as disabled with the reason, not as idle.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Building2,
  Briefcase,
  CheckCircle2,
  ChevronRight,
  CircleDot,
  Coins,
  GitBranch,
  Loader2,
  Megaphone,
  Play,
  Plus,
  RefreshCw,
  Target,
  Users,
  XCircle,
} from "lucide-react";

import { Section, Btn, Badge, EmptyState, ErrorBox, Field, Notice, inputCls } from "@/components/ui";
import * as api from "@/lib/company";
import type { CompanySummary, Overview, TickRecord } from "@/lib/company";

type Panel =
  | "overview"
  | "workforce"
  | "work"
  | "projects"
  | "groups"
  | "budget"
  | "loop"
  | "approvals";

const PANELS: Array<{ id: Panel; label: string }> = [
  { id: "overview", label: "Charter" },
  { id: "workforce", label: "Workforce" },
  { id: "work", label: "Work" },
  { id: "projects", label: "Projects" },
  { id: "groups", label: "Groups" },
  { id: "budget", label: "Budget" },
  { id: "loop", label: "Loop" },
  { id: "approvals", label: "Approvals" },
];

function reasonOf(err: unknown): string {
  return err instanceof Error && err.message.trim() ? err.message : "The Gateway gave no reason.";
}

export function CompanySection() {
  const [companies, setCompanies] = useState<CompanySummary[]>([]);
  const [listError, setListError] = useState<string | null>(null);
  const [loadingList, setLoadingList] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [panel, setPanel] = useState<Panel>("overview");

  const loadList = useCallback(async () => {
    setLoadingList(true);
    setListError(null);
    try {
      const rows = await api.listCompanies();
      setCompanies(rows);
      setSelectedId((prev) => prev ?? rows[0]?.company_id ?? null);
    } catch (e) {
      setListError(reasonOf(e));
    } finally {
      setLoadingList(false);
    }
  }, []);

  useEffect(() => {
    void loadList();
  }, [loadList]);

  if (loadingList) {
    return (
      <Section title="Companies" hint="Loading your portfolio…">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="size-3.5 animate-spin" /> Reading the company registry…
        </div>
      </Section>
    );
  }

  return (
    <Section
      title="Companies"
      hint="Autonomous organizations with their own charter, workforce, projects, board and perpetual loop."
      actions={
        <>
          <Btn variant="ghost" onClick={loadList}>
            <RefreshCw className="size-3.5" /> Refresh
          </Btn>
          <Btn onClick={() => setPanel("overview")}>
            <Plus className="size-3.5" /> New company
          </Btn>
        </>
      }
    >
      {listError && (
        <ErrorBox
          message={`The company list could not be read — this is a load failure, not an empty portfolio. (${listError})`}
          onRetry={loadList}
        />
      )}

      {!listError && companies.length === 0 ? (
        <EmptyState
          title="No companies yet"
          hint="Create one and Alpha will propose an org chart, hire real agents, and open a board you can run on."
        />
      ) : (
        companies.length > 0 && (
          <>
            <div className="flex flex-wrap gap-2 mb-4">
              {companies.map((c) => (
                <button
                  key={c.company_id}
                  onClick={() => setSelectedId(c.company_id)}
                  className={`px-3 py-2 rounded-xl border text-left text-xs transition-colors ${
                    c.company_id === selectedId
                      ? "border-primary/60 bg-primary/10"
                      : "border-border/60 bg-card/40 hover:bg-card/70"
                  }`}
                >
                  <div className="font-semibold flex items-center gap-1.5">
                    <Building2 className="size-3.5" /> {c.name}
                  </div>
                  <div className="text-[11px] text-muted-foreground mt-0.5">
                    {c.archetype} · {c.state}
                  </div>
                </button>
              ))}
            </div>

            {selectedId && <CompanyDetail companyId={selectedId} panel={panel} setPanel={setPanel} />}
          </>
        )
      )}

      {selectedId === null && companies.length === 0 && !listError && <NewCompanyForm onCreated={loadList} />}
    </Section>
  );
}

function CompanyDetail({
  companyId,
  panel,
  setPanel,
}: {
  companyId: string;
  panel: Panel;
  setPanel: (p: Panel) => void;
}) {
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    setError(null);
    try {
      setData(await api.getCompany(companyId));
    } catch (e) {
      setError(reasonOf(e));
    }
  }, [companyId]);

  useEffect(() => {
    setData(null);
    void reload();
  }, [reload]);

  if (error) {
    return (
      <ErrorBox
        message={`This company could not be read: ${error}`}
        onRetry={reload}
      />
    );
  }
  if (!data) {
    return (
      <div className="flex items-center gap-2 text-xs text-muted-foreground py-6">
        <Loader2 className="size-3.5 animate-spin" /> Reading company…
      </div>
    );
  }

  const m = data.metrics;
  const activeApprovals = data.approvals.filter((a) => a.status === "pending");

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <Badge tone={data.company.state === "active" ? "green" : data.company.state === "paused" ? "amber" : "gray"}>
          {data.company.state}
        </Badge>
        <Badge tone="blue">{data.company.archetype}</Badge>
        <Badge tone={data.loop.enabled ? "green" : "gray"} title={data.loop.enabled ? "The perpetual loop is enabled." : "The perpetual loop is disabled for this company."}>
          {data.loop.enabled ? "loop on" : "loop off"}
        </Badge>
        <Badge tone={data.health.health_percent === null ? "gray" : api.healthTone(data.health)}>
          health {api.healthLabel(data.health)}
        </Badge>
        {m.pending_approvals > 0 && <Badge tone="amber">{m.pending_approvals} awaiting approval</Badge>}
        {activeApprovals.length > 0 && <Badge tone="amber">{activeApprovals.length} pending</Badge>}
      </div>

      <div className="flex flex-wrap gap-1.5 border-b border-border/60 pb-2">
        {PANELS.map((p) => (
          <button
            key={p.id}
            onClick={() => setPanel(p.id)}
            className={`px-2.5 py-1 rounded-lg text-[11px] border transition-colors ${
              panel === p.id
                ? "border-primary/60 bg-primary/10 text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {p.label}
            {p.id === "approvals" && activeApprovals.length > 0 ? ` (${activeApprovals.length})` : ""}
          </button>
        ))}
      </div>

      {panel === "overview" && <OverviewPanel data={data} onReload={reload} busy={busy} setBusy={setBusy} />}
      {panel === "workforce" && <WorkforcePanel data={data} onReload={reload} />}
      {panel === "work" && <WorkPanel data={data} onReload={reload} />}
      {panel === "projects" && <ProjectsPanel data={data} />}
      {panel === "groups" && <GroupsPanel data={data} onReload={reload} />}
      {panel === "budget" && <BudgetPanel data={data} />}
      {panel === "loop" && <LoopPanel data={data} onReload={reload} />}
      {panel === "approvals" && <ApprovalsPanel data={data} onReload={reload} />}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Panels
// --------------------------------------------------------------------------- //

function OverviewPanel({
  data,
  onReload,
  busy,
  setBusy,
}: {
  data: Overview;
  onReload: () => Promise<void>;
  busy: boolean;
  setBusy: (b: boolean) => void;
}) {
  const c = data.company;
  const [tier, setTier] = useState(c.charter.autonomy_tier);
  const [daily, setDaily] = useState(
    c.charter.budget_daily_usd === null ? "" : String(c.charter.budget_daily_usd),
  );

  const save = async () => {
    setBusy(true);
    try {
      const patch: Record<string, unknown> = { autonomy_tier: tier };
      if (daily.trim() !== "") patch.budget_daily_usd = Number(daily);
      await api.setLoopPolicy(c.company_id, {});
      await fetch(`/api/companies/${encodeURIComponent(c.company_id)}/charter`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify(patch),
      }).then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
      });
      await onReload();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      {!data.invariants_ok && (
        <ErrorBox
          message={`This company has structural problems that need repair: ${data.invariant_problems.join("; ")}`}
        />
      )}

      <div className="rounded-xl border border-border/60 bg-card/40 p-3 space-y-2">
        <div className="text-[11px] uppercase tracking-wide text-muted-foreground flex items-center gap-1.5">
          <Target className="size-3" /> Mission
        </div>
        <p className="text-xs">{c.charter.mission}</p>
        {c.charter.vision && (
          <>
            <div className="text-[11px] uppercase tracking-wide text-muted-foreground pt-1">Vision</div>
            <p className="text-xs text-muted-foreground">{c.charter.vision}</p>
          </>
        )}
        {c.charter.values.length > 0 && (
          <div className="flex flex-wrap gap-1.5 pt-1">
            {c.charter.values.map((v) => (
              <Badge key={v} tone="purple">
                {v}
              </Badge>
            ))}
          </div>
        )}
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <Tile label="Employees" value={String(m_count(data, "headcount"))} sub={`${m_active(data)} active`} />
        <Tile label="Open work" value={String(data.work.open_items ?? 0)} sub={`${data.work.blocked_items ?? 0} blocked`} />
        <Tile label="Projects" value={String(data.metrics.project_count)} />
        <Tile label="Rooms" value={String(data.metrics.room_count)} />
      </div>

      <div className="rounded-xl border border-border/60 bg-card/40 p-3 grid gap-3 sm:grid-cols-2">
        <Field label="Autonomy tier" hint="Bounds what the loop may do without asking. T0 reads only; T4 restructures the org.">
          <select value={tier} onChange={(e) => setTier(e.target.value)} className={inputCls}>
            {["T0_observe", "T1_advise", "T2_execute_routine", "T3_self_direct", "T4_autonomous"].map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Daily spend ceiling (USD)" hint="The loop refuses to spend when this is unset — it never invents an allowance.">
          <input
            value={daily}
            onChange={(e) => setDaily(e.target.value)}
            inputMode="decimal"
            placeholder="e.g. 5.00"
            className={inputCls}
          />
        </Field>
        <div className="sm:col-span-2">
          <Btn onClick={save} disabled={busy}>
            {busy ? <Loader2 className="size-3.5 animate-spin" /> : <CheckCircle2 className="size-3.5" />} Save charter
          </Btn>
        </div>
      </div>

      {c.objectives.length > 0 && (
        <div className="space-y-2">
          <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Objectives</div>
          {c.objectives.map((o) => (
            <div key={o.objective_id} className="rounded-xl border border-border/60 bg-card/40 p-3">
              <div className="flex items-center gap-2">
                <span className="text-xs font-semibold">{o.title}</span>
                <Badge tone={o.status === "at_risk" ? "amber" : o.status === "achieved" ? "green" : "blue"}>
                  {o.status}
                </Badge>
              </div>
              {o.key_results.map((kr) => (
                <div key={kr.key_result_id} className="text-[11px] text-muted-foreground mt-1 flex items-center gap-2">
                  <CircleDot className="size-2.5" />
                  <span>{kr.title}</span>
                  <span>
                    {kr.current === null
                      ? `target ${kr.target ?? "?"}${kr.unit}`
                      : `${kr.current}${kr.unit} of ${kr.target ?? "?"}${kr.unit} (${kr.current_basis})`}
                  </span>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function m_count(data: Overview, key: "headcount"): number {
  return data.metrics[key] ?? 0;
}
function m_active(data: Overview): number {
  return data.metrics.active_headcount ?? 0;
}

function WorkforcePanel({ data, onReload }: { data: Overview; onReload: () => Promise<void> }) {
  const [handle, setHandle] = useState("");
  const [role, setRole] = useState("");
  const [unit, setUnit] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const roster = data.workforce;

  const hire = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api.hireEmployee(data.company.company_id, {
        handle,
        role_title: role,
        unit_name: unit,
      });
      setHandle("");
      setRole("");
      setUnit("");
      await onReload();
    } catch (e) {
      setErr(reasonOf(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      {roster.missing_profile_count > 0 && (
        <Notice
          message={`${roster.missing_profile_count} employee record(s) have no bot profile in the roster: ${roster.missing_profiles.join(", ")}. They exist on the org chart but cannot be dispatched.`}
        />
      )}
      {err && <ErrorBox message={err} />}

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <Tile label="Employees" value={String(roster.employment_count)} sub={`${roster.active_employment_count} active`} />
        <Tile label="Missing profiles" value={String(roster.missing_profile_count)} />
        <Tile label="Unassigned bots" value={String(roster.unassigned_bot_count)} sub="in the roster, not in this company" />
        <Tile label="Units" value={String(data.company.units.length)} />
      </div>

      <div className="rounded-xl border border-border/60 bg-card/40 p-3 space-y-2">
        <div className="text-[11px] uppercase tracking-wide text-muted-foreground flex items-center gap-1.5">
          <Users className="size-3" /> Hire
        </div>
        <div className="grid gap-2 sm:grid-cols-3">
          <Field label="Handle">
            <input value={handle} onChange={(e) => setHandle(e.target.value)} placeholder="e.g. data-lead" className={inputCls} />
          </Field>
          <Field label="Role">
            <input value={role} onChange={(e) => setRole(e.target.value)} placeholder="Senior Data Engineer" className={inputCls} />
          </Field>
          <Field label="Unit">
            <input value={unit} onChange={(e) => setUnit(e.target.value)} placeholder="Engineering" className={inputCls} />
          </Field>
        </div>
        <Btn onClick={hire} disabled={busy || !handle || !role || !unit}>
          {busy ? <Loader2 className="size-3.5 animate-spin" /> : <Plus className="size-3.5" />} Hire
        </Btn>
      </div>

      <div className="space-y-1.5">
        {data.company.employments.length === 0 ? (
          <EmptyState title="No employees yet" hint="Hire above, or create the company with hiring enabled." />
        ) : (
          data.company.employments.map((e) => (
            <div key={e.employment_id} className="rounded-xl border border-border/60 bg-card/40 p-3 flex items-center gap-3">
              <div className="min-w-0 flex-1">
                <div className="text-xs font-semibold flex items-center gap-1.5">
                  {e.agent_handle}
                  <Badge tone={e.status === "active" ? "green" : e.status === "terminated" ? "gray" : "amber"}>
                    {e.employment_type}
                  </Badge>
                </div>
                <div className="text-[11px] text-muted-foreground">
                  reports to {e.reports_to_handle ?? "nobody"} · backup {e.backup_agent_handle ?? "none"}
                </div>
              </div>
              <div className="text-[11px] text-muted-foreground">
                score{" "}
                {e.performance_score === null ? (
                  <Badge tone="gray" title="No performance measurement has been recorded.">
                    not measured
                  </Badge>
                ) : (
                  String(e.performance_score)
                )}
              </div>
              <div className="flex gap-1.5">
                <Btn
                  variant="ghost"
                  onClick={async () => {
                    await api.cloneEmployee(data.company.company_id, { source: e.agent_handle, handle: `${e.agent_handle}-2` });
                    await onReload();
                  }}
                >
                  Clone
                </Btn>
                <Btn
                  variant="ghost"
                  onClick={async () => {
                    await api.promoteEmployee(data.company.company_id, e.agent_handle);
                    await onReload();
                  }}
                >
                  Promote
                </Btn>
                <Btn
                  variant="danger"
                  onClick={async () => {
                    await api.terminateEmployee(data.company.company_id, e.agent_handle);
                    await onReload();
                  }}
                >
                  Terminate
                </Btn>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function WorkPanel({ data, onReload }: { data: Overview; onReload: () => Promise<void> }) {
  const [title, setTitle] = useState("");
  const [dod, setDod] = useState("");
  const [err, setErr] = useState<string | null>(null);

  if (!data.work.reachable) {
    return (
      <ErrorBox
        message={`The work board could not be read, so no card counts are shown: ${data.work.error ?? "unknown reason"}. This is a load failure, not an empty board.`}
      />
    );
  }

  const add = async () => {
    setErr(null);
    try {
      await api.createWorkItem(data.company.company_id, {
        title,
        definition_of_done: dod
          .split("\n")
          .map((s) => s.trim())
          .filter(Boolean),
      });
      setTitle("");
      setDod("");
      await onReload();
    } catch (e) {
      setErr(reasonOf(e));
    }
  };

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-3 sm:grid-cols-6 gap-2">
        <Tile label="Total" value={String(data.work.total_items ?? 0)} />
        <Tile label="Ready" value={String(data.work.ready_items ?? 0)} />
        <Tile label="In review" value={String(data.work.in_review_items ?? 0)} />
        <Tile label="Blocked" value={String(data.work.blocked_items ?? 0)} />
        <Tile label="Done" value={String(data.work.done_items ?? 0)} />
        <Tile label="Missing DoD" value={String(data.work.items_missing_dod ?? 0)} sub="cannot be verified" />
      </div>

      {(data.work.items_missing_dod ?? 0) > 0 && (
        <Notice message="Cards without a Definition of Done cannot be verified, so the loop flags them instead of dispatching them." />
      )}

      {err && <ErrorBox message={err} />}

      <div className="rounded-xl border border-border/60 bg-card/40 p-3 space-y-2">
        <div className="text-[11px] uppercase tracking-wide text-muted-foreground flex items-center gap-1.5">
          <Briefcase className="size-3" /> New card
        </div>
        <Field label="Title">
          <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="What needs doing?" className={inputCls} />
        </Field>
        <Field label="Definition of Done" hint="One per line. Without at least one, the loop will not dispatch this card.">
          <textarea value={dod} onChange={(e) => setDod(e.target.value)} rows={3} className={inputCls} />
        </Field>
        <Btn onClick={add} disabled={!title}>
          <Plus className="size-3.5" /> Create card
        </Btn>
      </div>

      <div className="space-y-1.5">
        {data.company.work_items.length === 0 ? (
          <EmptyState title="Board is empty" hint="Create the first card above. The loop picks up ready work automatically." />
        ) : (
          data.company.work_items.map((w) => (
            <div key={`${w.board_id}:${w.task_id}`} className="rounded-xl border border-border/60 bg-card/40 p-3">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-xs font-semibold">{w.title}</span>
                <Badge tone={w.column === "blocked" ? "red" : w.column === "done" ? "green" : w.column === "in_review" ? "amber" : "blue"}>
                  {w.column}
                </Badge>
                <Badge tone="gray">{w.priority}</Badge>
                {w.assignee_agent_handle && <Badge tone="purple">@{w.assignee_agent_handle}</Badge>}
                {w.definition_of_done.length === 0 && <Badge tone="amber">no DoD</Badge>}
              </div>
              {w.definition_of_done.length > 0 && (
                <ul className="text-[11px] text-muted-foreground mt-1 space-y-0.5">
                  {w.definition_of_done.map((d, i) => (
                    <li key={i}>• {d}</li>
                  ))}
                </ul>
              )}
              <div className="flex gap-1.5 mt-2">
                {["todo", "in_progress", "in_review", "done", "blocked"].map((col) => (
                  <Btn key={col} variant="ghost" onClick={async () => {
                    await api.moveWorkItem(data.company.company_id, w.task_id, col);
                    await onReload();
                  }}>
                    {col.replace("_", " ")}
                  </Btn>
                ))}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function ProjectsPanel({ data }: { data: Overview }) {
  const [pid, setPid] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  return (
    <div className="space-y-3">
      <Notice message="A company links to projects you already own. It never creates a second copy of one." />
      <div className="rounded-xl border border-border/60 bg-card/40 p-3 grid gap-2 sm:grid-cols-[1fr_auto] sm:items-end">
        <Field label="Existing project id">
          <input value={pid} onChange={(e) => setPid(e.target.value)} placeholder="project id from the Projects view" className={inputCls} />
        </Field>
        <Btn
          onClick={async () => {
            setErr(null);
            setOk(null);
            try {
              await api.attachProject(data.company.company_id, { project_id: pid });
              setOk(`Linked ${pid}.`);
              setPid("");
            } catch (e) {
              setErr(reasonOf(e));
            }
          }}
          disabled={!pid}
        >
          <GitBranch className="size-3.5" /> Link
        </Btn>
      </div>
      {err && <ErrorBox message={err} />}
      {ok && <Notice message={ok} />}
      {data.company.projects.length === 0 ? (
        <EmptyState title="No projects linked" hint="Link an existing project above to see it on the company portfolio." />
      ) : (
        <div className="space-y-1.5">
          {data.company.projects.map((p) => (
            <div key={p.project_id} className="rounded-xl border border-border/60 bg-card/40 p-3 flex items-center gap-2 flex-wrap">
              <span className="text-xs font-semibold">{p.title || p.project_id}</span>
              <Badge tone="blue">{p.project_id}</Badge>
              <Badge tone={p.status === "active" ? "green" : "gray"}>{p.status}</Badge>
              {p.status_basis === "unmeasured" && (
                <Badge tone="gray" title="The project store has not reported this project's status yet.">
                  not measured
                </Badge>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function GroupsPanel({ data, onReload }: { data: Overview; onReload: () => Promise<void> }) {
  const [name, setName] = useState("");
  const [topic, setTopic] = useState("");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [sent, setSent] = useState<string | null>(null);

  return (
    <div className="space-y-3">
      <Notice message="Rooms are real group rooms shared with the Messages view. The company posts announcements; liveness stays a silent pulse so nobody trains themselves to ignore the channel." />
      <div className="rounded-xl border border-border/60 bg-card/40 p-3 grid gap-2 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
        <Field label="Room name">
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="engineering" className={inputCls} />
        </Field>
        <Field label="Topic">
          <input value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="What this room is for" className={inputCls} />
        </Field>
        <Btn
          onClick={async () => {
            setErr(null);
            try {
              await api.createGroup(data.company.company_id, { name, topic });
              setName("");
              setTopic("");
              await onReload();
            } catch (e) {
              setErr(reasonOf(e));
            }
          }}
          disabled={!name}
        >
          <Plus className="size-3.5" /> Create
        </Btn>
      </div>
      {err && <ErrorBox message={err} />}
      {data.company.groups.length === 0 ? (
        <EmptyState title="No rooms yet" hint="Create one above; it appears in the Messages view too." />
      ) : (
        <div className="space-y-1.5">
          {data.company.groups.map((g) => (
            <div key={g.room_id} className="rounded-xl border border-border/60 bg-card/40 p-3">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-xs font-semibold">{g.name || g.room_id}</span>
                <Badge tone="blue">{g.room_id}</Badge>
                {g.member_agent_handles.length > 0 && (
                  <span className="text-[11px] text-muted-foreground">{g.member_agent_handles.length} member(s)</span>
                )}
              </div>
              <div className="flex gap-1.5 mt-2">
                <input
                  value={msg}
                  onChange={(e) => setMsg(e.target.value)}
                  placeholder="Announce something…"
                  className={inputCls}
                />
                <Btn
                  variant="ghost"
                  onClick={async () => {
                    setErr(null);
                    setSent(null);
                    try {
                      await api.announce(data.company.company_id, { room_id: g.room_id, content: msg });
                      setSent(`Posted to ${g.name || g.room_id}.`);
                      setMsg("");
                    } catch (e) {
                      setErr(reasonOf(e));
                    }
                  }}
                  disabled={!msg}
                >
                  <Megaphone className="size-3.5" />
                </Btn>
              </div>
            </div>
          ))}
          {sent && <Notice message={sent} />}
        </div>
      )}
    </div>
  );
}

function BudgetPanel({ data }: { data: Overview }) {
  const cost = data.cost;
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <Tile label="Spend today" value={api.usd(cost.spent_today_usd)} sub={cost.basis} />
        <Tile label="Daily ceiling" value={cost.budget_daily_usd === null ? "not set" : api.usd(cost.budget_daily_usd)} />
        <Tile label="Total budget" value={cost.budget_total_usd === null ? "not set" : api.usd(cost.budget_total_usd)} />
        <Tile label="Utilisation" value={cost.utilization_percent === null ? "not measured" : `${cost.utilization_percent}%`} />
      </div>
      <Notice
        message="Spend is only advanced by a real reading from a run's token usage. It is never estimated from task counts, because an estimate fed into a ceiling is a ceiling that trips on fiction."
      />
      <div className="text-[11px] text-muted-foreground">
        Measured from {cost.measured_run_count} run(s)
        {cost.last_measured_at ? ` · last at ${new Date(cost.last_measured_at * 1000).toISOString()}` : " · never measured"}
      </div>
    </div>
  );
}

function LoopPanel({ data, onReload }: { data: Overview; onReload: () => Promise<void> }) {
  const [ticks, setTicks] = useState<TickRecord[] | null>(null);
  const [tickErr, setTickErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const loadTicks = useCallback(async () => {
    setTickErr(null);
    try {
      setTicks(await api.listTicks(data.company.company_id, 25));
    } catch (e) {
      setTickErr(reasonOf(e));
    }
  }, [data.company.company_id]);

  useEffect(() => {
    void loadTicks();
  }, [loadTicks]);

  const toggle = async () => {
    setBusy(true);
    try {
      await api.setLoopPolicy(data.company.company_id, { enabled: !data.loop.enabled });
      await onReload();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-border/60 bg-card/40 p-3 flex items-center gap-3 flex-wrap">
        <div className="flex-1 min-w-0">
          <div className="text-xs font-semibold flex items-center gap-1.5">
            Perpetual loop
            <Badge tone={data.loop.enabled ? "green" : "gray"}>{data.loop.enabled ? "enabled" : "disabled"}</Badge>
          </div>
          <div className="text-[11px] text-muted-foreground">
            {data.loop.enabled
              ? `Runs every ${String(data.company.loop_policy.interval_seconds ?? "?")}s, at most ${String(data.company.loop_policy.max_actions_per_tick ?? "?")} action(s) per tick.`
              : "Disabled — the loop will not run until you enable it here or in config."}
          </div>
          {data.loop.consecutive_no_progress_ticks > 0 && (
            <div className="text-[11px] text-amber-600 dark:text-amber-400 mt-1">
              {data.loop.consecutive_no_progress_ticks} consecutive tick(s) with no verified outcome.
            </div>
          )}
        </div>
        <Btn onClick={toggle} disabled={busy}>
          {data.loop.enabled ? <XCircle className="size-3.5" /> : <Play className="size-3.5" />}
          {data.loop.enabled ? "Disable" : "Enable"}
        </Btn>
        <Btn
          variant="ghost"
          onClick={async () => {
            setBusy(true);
            try {
              await api.runTick(data.company.company_id);
              await loadTicks();
              await onReload();
            } finally {
              setBusy(false);
            }
          }}
          disabled={busy}
        >
          <Play className="size-3.5" /> Run one tick
        </Btn>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <Tile label="Rituals due" value={data.rituals.due.length === 0 ? "none" : data.rituals.due.join(", ")} />
        <Tile label="Recent outcomes" value={String(data.loop.recent_outcomes.length)} sub="in the storm window" />
        <Tile label="Standup cadence" value={String(data.rituals.cadence.standup ?? "—")} />
        <Tile label="Review cadence" value={String(data.rituals.cadence.review ?? "—")} />
      </div>

      {tickErr && <ErrorBox message={`The tick ledger could not be read: ${tickErr}`} onRetry={loadTicks} />}

      {ticks === null && !tickErr ? (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="size-3.5 animate-spin" /> Reading tick ledger…
        </div>
      ) : ticks && ticks.length === 0 ? (
        <EmptyState title="The loop has never run" hint="Enable it and run one tick, or let the supervisor sweep pick it up." />
      ) : (
        <div className="space-y-1.5">
          {ticks?.map((t) => (
            <div key={t.tick_id} className="rounded-xl border border-border/60 bg-card/40 p-3">
              <div className="flex items-center gap-2 flex-wrap">
                <Badge tone={api.tickTone(t.outcome)}>{t.outcome.replace("_", " ")}</Badge>
                <span className="text-xs">{t.reason}</span>
                <span className="text-[11px] text-muted-foreground ml-auto">
                  {t.duration_ms === null ? "duration not measured" : `${t.duration_ms.toFixed(1)}ms`} · cost{" "}
                  {api.usd(t.cost_usd)} ({t.cost_basis})
                </span>
              </div>
              {t.dispatched.length > 0 && (
                <ul className="text-[11px] text-muted-foreground mt-1">
                  {t.dispatched.map((d, i) => (
                    <li key={i}>→ {String(d.kind)}: {String(d.reason)}</li>
                  ))}
                </ul>
              )}
              {t.failures.length > 0 && (
                <ul className="text-[11px] text-red-600 dark:text-red-400 mt-1">
                  {t.failures.map((f, i) => (
                    <li key={i}>✗ {String(f.kind)}: {String(f.error ?? f.reason ?? "")}</li>
                  ))}
                </ul>
              )}
              {t.deferred.length > 0 && (
                <div className="text-[11px] text-muted-foreground mt-1">{t.deferred.length} deferred by policy</div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ApprovalsPanel({ data, onReload }: { data: Overview; onReload: () => Promise<void> }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const pending = data.approvals.filter((a) => a.status === "pending");
  const decided = data.approvals.filter((a) => a.status !== "pending");

  const decide = async (id: string, decision: "approve" | "reject") => {
    setBusy(id);
    setErr(null);
    try {
      await api.decideApproval(data.company.company_id, id, decision);
      await onReload();
    } catch (e) {
      setErr(reasonOf(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="space-y-3">
      <Notice message="A proposal is never an action. Nothing here changes anything until you approve it, and a rejection is recorded rather than deleted." />
      {err && <ErrorBox message={err} />}
      {pending.length === 0 ? (
        <EmptyState title="Nothing awaiting approval" hint="The loop proposes here when its autonomy tier allows something it may not do itself." />
      ) : (
        <div className="space-y-1.5">
          {pending.map((a) => (
            <div key={a.approval_id} className="rounded-xl border border-border/60 bg-card/40 p-3">
              <div className="flex items-center gap-2 flex-wrap">
                <Badge tone="amber">{a.kind}</Badge>
                <span className="text-xs font-semibold">{a.title}</span>
              </div>
              {a.rationale && <div className="text-[11px] text-muted-foreground mt-1">{a.rationale}</div>}
              <div className="flex gap-1.5 mt-2">
                <Btn onClick={() => decide(a.approval_id, "approve")} disabled={busy === a.approval_id}>
                  <CheckCircle2 className="size-3.5" /> Approve
                </Btn>
                <Btn variant="danger" onClick={() => decide(a.approval_id, "reject")} disabled={busy === a.approval_id}>
                  <XCircle className="size-3.5" /> Reject
                </Btn>
              </div>
            </div>
          ))}
        </div>
      )}
      {decided.length > 0 && (
        <details className="rounded-xl border border-border/60 bg-card/40 p-3">
          <summary className="text-xs cursor-pointer">{decided.length} decided</summary>
          <div className="space-y-1.5 mt-2">
            {decided.map((a) => (
              <div key={a.approval_id} className="text-[11px] text-muted-foreground flex items-center gap-2">
                <ChevronRight className="size-3" />
                <Badge tone={a.status === "approved" ? "green" : "gray"}>{a.status}</Badge>
                {a.title}
                {a.decision_note ? ` — ${a.decision_note}` : ""}
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

function NewCompanyForm({ onCreated }: { onCreated: () => Promise<void> }) {
  const [prompt, setPrompt] = useState("");
  const [name, setName] = useState("");
  const [archetypes, setArchetypes] = useState<api.CompanyArchetype[]>([]);
  const [archetype, setArchetype] = useState<string | null>(null);
  const [budget, setBudget] = useState("");
  const [hire, setHire] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [preview, setPreview] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    api
      .listArchetypes()
      .then(setArchetypes)
      .catch((e) => setErr(reasonOf(e)));
  }, []);

  const doPreview = useCallback(async () => {
    setErr(null);
    try {
      setPreview(
        await api.previewBlueprint({
          prompt,
          name: name || undefined,
          archetype,
          budget_daily_usd: budget.trim() === "" ? null : Number(budget),
        }),
      );
    } catch (e) {
      setErr(reasonOf(e));
    }
  }, [prompt, name, archetype, budget]);

  const create = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api.createCompany({
        prompt,
        name: name || "Autonomous Company",
        archetype,
        budget_daily_usd: budget.trim() === "" ? null : Number(budget),
        hire,
      });
      await onCreated();
    } catch (e) {
      setErr(reasonOf(e));
    } finally {
      setBusy(false);
    }
  };

  const headcount = useMemo(() => {
    const bp = (preview?.blueprint ?? null) as { headcount?: number } | null;
    return bp?.headcount ?? null;
  }, [preview]);

  return (
    <div className="rounded-xl border border-border/60 bg-card/40 p-4 space-y-3">
      <div className="text-xs font-semibold flex items-center gap-1.5">
        <Building2 className="size-3.5" /> New company
      </div>
      {err && <ErrorBox message={err} />}
      <Field label="Mission" hint="One plain sentence. Everything else — objectives, departments, duties — is proposed from this.">
        <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={3} className={inputCls} />
      </Field>
      <div className="grid gap-2 sm:grid-cols-3">
        <Field label="Name">
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Optional" className={inputCls} />
        </Field>
        <Field label="Archetype" hint="Leave on auto to detect it from the mission.">
          <select value={archetype ?? "auto"} onChange={(e) => setArchetype(e.target.value === "auto" ? null : e.target.value)} className={inputCls}>
            <option value="auto">Detect automatically</option>
            {archetypes.map((a) => (
              <option key={a.archetype} value={a.archetype}>
                {a.display_name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Daily ceiling (USD)" hint="Required before the loop will spend anything.">
          <input value={budget} onChange={(e) => setBudget(e.target.value)} inputMode="decimal" placeholder="5.00" className={inputCls} />
        </Field>
      </div>
      <label className="flex items-center gap-2 text-[11px] text-muted-foreground">
        <input type="checkbox" checked={hire} onChange={(e) => setHire(e.target.checked)} />
        Hire the proposed workforce immediately (each hire is transactional — a failure rolls the whole batch back)
      </label>
      <div className="flex gap-2">
        <Btn variant="ghost" onClick={doPreview} disabled={prompt.length < 5}>
          Preview org chart
        </Btn>
        <Btn onClick={create} disabled={busy || prompt.length < 5}>
          {busy ? <Loader2 className="size-3.5 animate-spin" /> : <Plus className="size-3.5" />} Create company
        </Btn>
      </div>
      {preview && (
        <div className="rounded-lg border border-border/60 p-3 text-[11px] space-y-1">
          <div className="font-semibold text-xs">Proposed org chart</div>
          <div>Headcount: {headcount ?? "not reported"}</div>
          {Array.isArray(preview.employees) && (
            <ul className="text-muted-foreground space-y-0.5">
              {(preview.employees as Array<Record<string, unknown>>).map((e, i) => (
                <li key={i}>
                  • {String(e.handle)} — {String(e.role)} ({String(e.unit)})
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

function Tile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-xl border border-border/60 bg-card/40 p-3">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className="text-sm font-semibold mt-0.5 truncate">{value}</div>
      {sub && <div className="text-[11px] text-muted-foreground">{sub}</div>}
    </div>
  );
}

export default CompanySection;