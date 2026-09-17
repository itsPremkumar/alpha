"use client";

import React, { useEffect, useState } from "react";
import { listScheduledTasks, createScheduledTask, pauseTask, resumeTask, triggerTask, deleteTask, previewCron, describeSchedule, ScheduledTask } from "@/lib/scheduled";
import { Section, EmptyState, ErrorBox, Notice, Btn, Badge, Field, SkeletonList, inputCls } from "@/components/ui";
import { errMsg } from "@/lib/http";
import { Plus, Pause, Play, Zap, Trash2, RefreshCw } from "lucide-react";

interface Draft {
  title: string;
  prompt: string;
  schedule_type: "cron" | "interval";
  cron: string;
  interval_seconds: number;
  timezone: string;
  assistant_id: string;
}

const EMPTY_DRAFT: Draft = { title: "", prompt: "", schedule_type: "cron", cron: "0 9 * * *", interval_seconds: 3600, timezone: "UTC", assistant_id: "" };

export function ScheduledSection(props: { bots: Array<{ name: string; display_name: string }> }) {
  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [draft, setDraft] = useState(EMPTY_DRAFT);
  const [preview, setPreview] = useState<string[]>([]);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      setTasks(await listScheduledTasks());
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

  const tz = () => {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    } catch {
      return "UTC";
    }
  };

  const onPreview = async () => {
    try {
      setPreview(await previewCron(draft.cron || "0 9 * * *", draft.timezone || tz()));
    } catch (e) {
      setError(errMsg(e));
    }
  };

  const onCreate = async () => {
    if (!draft.title.trim() || !draft.prompt.trim()) {
      setError("Give the task a title and tell the agent what to do.");
      return;
    }
    try {
      await createScheduledTask({
        title: draft.title.trim(),
        prompt: draft.prompt.trim(),
        schedule_type: draft.schedule_type,
        cron: draft.cron,
        interval_seconds: draft.interval_seconds,
        timezone: draft.timezone || tz(),
        assistant_id: draft.assistant_id || undefined,
      });
      setDraft(EMPTY_DRAFT);
      setShowForm(false);
      flash("Scheduled — the agent will run this automatically.");
      await load();
    } catch (e) {
      setError(errMsg(e));
    }
  };

  const act = async (fn: () => Promise<void>, ok: string) => {
    try {
      await fn();
      flash(ok);
      await load();
    } catch (e) {
      setError(errMsg(e));
    }
  };

  return (
    <Section
      title="Scheduled tasks"
      hint="Put the agent on autopilot: a morning briefing, a weekly report, a check every hour. Each run happens on its own — trigger one manually any time."
      actions={
        <>
          <Btn variant="ghost" onClick={load}>
            <RefreshCw className="size-3.5" /> Refresh
          </Btn>
          <Btn onClick={() => { setShowForm((v) => !v); setDraft((d) => ({ ...d, timezone: d.timezone || tz() })); }}>
            <Plus className="size-3.5" /> New schedule
          </Btn>
        </>
      }
    >
      {error && <ErrorBox message={error} onRetry={load} />}
      {notice && <Notice message={notice} />}

      {showForm && (
        <div className="rounded-2xl border border-primary/30 bg-card p-4 space-y-3">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Field label="Title" hint="Shown in the list, e.g. Morning briefing.">
              <input value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} placeholder="Morning briefing" className={inputCls} />
            </Field>
            <Field label="Who should do it" hint="Leave as Lead Agent to auto-route.">
              <select value={draft.assistant_id} onChange={(e) => setDraft({ ...draft, assistant_id: e.target.value })} className={inputCls}>
                <option value="">Lead Agent (auto-route)</option>
                {props.bots.map((b) => (
                  <option key={b.name} value={b.name}>{b.display_name}</option>
                ))}
              </select>
            </Field>
          </div>
          <Field label="What should the agent do?" hint="Write the instruction exactly as if you were asking in chat.">
            <textarea value={draft.prompt} onChange={(e) => setDraft({ ...draft, prompt: e.target.value })} rows={3} placeholder="Summarize yesterday's progress and list today's priorities…" className={inputCls} />
          </Field>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <Field label="Repeat">
              <select value={draft.schedule_type} onChange={(e) => setDraft({ ...draft, schedule_type: e.target.value as "cron" | "interval" })} className={inputCls}>
                <option value="cron">On a schedule (cron)</option>
                <option value="interval">Every X seconds</option>
              </select>
            </Field>
            {draft.schedule_type === "cron" ? (
              <Field label="Schedule" hint='Cron format. "0 9 * * *" = 9:00 daily.'>
                <input value={draft.cron} onChange={(e) => setDraft({ ...draft, cron: e.target.value })} placeholder="0 9 * * *" className={`${inputCls} font-mono`} />
              </Field>
            ) : (
              <Field label="Every (seconds)" hint="3600 = hourly.">
                <input type="number" min={60} value={draft.interval_seconds} onChange={(e) => setDraft({ ...draft, interval_seconds: Number(e.target.value) })} className={inputCls} />
              </Field>
            )}
            <Field label="Timezone">
              <input value={draft.timezone} onChange={(e) => setDraft({ ...draft, timezone: e.target.value })} className={`${inputCls} font-mono`} />
            </Field>
          </div>
          {draft.schedule_type === "cron" && (
            <div className="flex items-center gap-2 flex-wrap">
              <Btn variant="ghost" onClick={onPreview}>Preview next runs</Btn>
              {preview.length > 0 && <span className="text-[11px] text-muted-foreground">{preview.join("  •  ")}</span>}
            </div>
          )}
          <div className="flex gap-2">
            <Btn onClick={onCreate}>Save schedule</Btn>
            <Btn variant="ghost" onClick={() => setShowForm(false)}>Cancel</Btn>
          </div>
        </div>
      )}

      {loading ? (
        <SkeletonList rows={4} />
      ) : tasks.length === 0 ? (
        <EmptyState title="Nothing scheduled" hint="Create your first schedule above — e.g. a daily 9:00 briefing." action={<Btn onClick={() => setShowForm(true)}><Plus className="size-3.5" /> New schedule</Btn>} />
      ) : (
        <div className="space-y-2">
          {tasks.map((t) => (
            <div key={t.id} className="rounded-xl border border-border/60 bg-card p-4">
              <div className="flex items-center gap-2 flex-wrap">
                <p className="text-sm font-semibold flex-1 min-w-40">{t.title}</p>
                <Badge tone={t.status === "active" || t.status === "running" ? "green" : t.status === "paused" ? "amber" : "gray"}>{t.status}</Badge>
              </div>
              <p className="text-[11px] text-muted-foreground mt-1 line-clamp-2">{t.prompt}</p>
              <p className="text-[11px] font-mono text-muted-foreground mt-1">
                {describeSchedule(t)}{t.timezone ? ` • ${t.timezone}` : ""}{t.next_run ? ` • next: ${new Date(t.next_run).toLocaleString()}` : ""}
              </p>
              <div className="flex gap-2 mt-2.5 flex-wrap">
                <Btn variant="ghost" onClick={() => act(() => triggerTask(t.id), "Triggered — running now.")}>
                  <Zap className="size-3.5" /> Run now
                </Btn>
                {t.status === "paused" ? (
                  <Btn variant="ghost" onClick={() => act(() => resumeTask(t.id), "Resumed.")}>
                    <Play className="size-3.5" /> Resume
                  </Btn>
                ) : (
                  <Btn variant="ghost" onClick={() => act(() => pauseTask(t.id), "Paused.")}>
                    <Pause className="size-3.5" /> Pause
                  </Btn>
                )}
                <Btn
                  variant="danger"
                  onClick={() => window.confirm(`Delete "${t.title}"?`) && act(() => deleteTask(t.id), "Deleted.")}
                >
                  <Trash2 className="size-3.5" /> Delete
                </Btn>
              </div>
            </div>
          ))}
        </div>
      )}
    </Section>
  );
}
