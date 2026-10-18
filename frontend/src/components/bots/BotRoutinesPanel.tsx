"use client";

import { useState } from "react";
import { Loader2, Pause, Play, Plus, Trash2 } from "lucide-react";

import { Btn, inputCls } from "@/components/ui";
import {
  browserTimezone,
  createBotRoutine,
  deleteBotRoutine,
  deletionNote,
  routineScheduleLine,
  type BotRoutines,
} from "@/lib/bot-routines";
import { errMsg } from "@/lib/http";
import { pauseTask, resumeTask } from "@/lib/scheduled";
import { Notice, VerbatimBadge } from "./detail-primitives";

type ScheduleType = "cron" | "interval";

/**
 * The routines table on the bot detail page.
 *
 * A routine is a scheduled task bound to this bot's canonical chat thread, so
 * each row is really two records and the table shows both: the routine's own
 * line (name, stored schedule, action) and the LIVE task's line (status, next
 * run, last run/error). They are rendered from separate slots on purpose —
 * a routine whose task record is missing says exactly that instead of reading
 * as "scheduled but never due".
 *
 * Writes go through the surfaces that own them: pause/resume are the
 * scheduled-task routes, create/delete are the bot-routine routes, and after
 * every write the page re-reads its bundle so the table shows the server's
 * answer rather than the draft that was typed.
 */
export function BotRoutinesPanel({
  name,
  routines,
  onChanged,
}: {
  name: string;
  routines: BotRoutines;
  /** Re-read the page's bundle so the table shows the server's answer. */
  onChanged: () => void | Promise<void>;
}) {
  const [showForm, setShowForm] = useState(false);
  const [draft, setDraft] = useState(() => ({
    name: "",
    action: "",
    schedule_type: "cron" as ScheduleType,
    cron: "0 9 * * *",
    interval_seconds: "3600",
    timezone: browserTimezone(),
  }));
  const [busy, setBusy] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const rows = routines.routines;

  const act = async (key: string, fn: () => Promise<string>) => {
    if (busy !== null) return;
    setBusy(key);
    setError(null);
    setNotice(null);
    try {
      setNotice(await fn());
      await onChanged();
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setBusy(null);
      setConfirming(null);
    }
  };

  const remove = (routineName: string) =>
    act(`delete:${routineName}`, async () => deletionNote(await deleteBotRoutine(name, routineName)));

  const create = async () => {
    if (busy !== null) return;
    const routineName = draft.name.trim();
    if (!routineName || !draft.action.trim()) {
      setError("Give the routine a name and tell the bot what to do.");
      return;
    }
    const seconds = Number(draft.interval_seconds);
    if (draft.schedule_type === "interval" && (!Number.isFinite(seconds) || seconds <= 0)) {
      setError("The interval must be a positive number of seconds.");
      return;
    }
    await act("create", async () => {
      await createBotRoutine(name, {
        name: routineName,
        action: draft.action.trim(),
        schedule_type: draft.schedule_type,
        cron: draft.schedule_type === "cron" ? draft.cron.trim() : undefined,
        interval_seconds: draft.schedule_type === "interval" ? seconds : undefined,
        timezone: draft.timezone.trim() || browserTimezone(),
      });
      setDraft((d) => ({ ...d, name: "", action: "" }));
      setShowForm(false);
      return `Created ${routineName} — it will run in the bot's own chat.`;
    });
  };

  return (
    <div className="space-y-3" data-panel="bot-routines">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">
          Each routine is a scheduled task bound to this bot: the run lands in the bot&apos;s own chat thread.
        </p>
        <button
          type="button"
          onClick={() => setShowForm((v) => !v)}
          disabled={busy !== null}
          className="flex items-center gap-1 rounded-md border border-border/60 px-2 py-1 text-[11px] hover:bg-muted/40 disabled:opacity-50"
        >
          <Plus className="size-3" /> {showForm ? "Close form" : "New routine"}
        </button>
      </div>

      {error && (
        <p className="rounded-lg border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-destructive" role="alert">
          {error}
        </p>
      )}
      {notice && <Notice tone="good">{notice}</Notice>}

      {showForm && (
        <div className="rounded-lg border border-border/60 bg-muted/20 p-3 space-y-2.5">
          <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
            <label className="block">
              <span className="mb-0.5 block text-[11px] text-muted-foreground">Name</span>
              <input
                value={draft.name}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                placeholder="daily-digest"
                maxLength={64}
                disabled={busy !== null}
                className={inputCls}
              />
            </label>
            <label className="block">
              <span className="mb-0.5 block text-[11px] text-muted-foreground">Repeat</span>
              <select
                value={draft.schedule_type}
                onChange={(e) => setDraft({ ...draft, schedule_type: e.target.value as ScheduleType })}
                disabled={busy !== null}
                className={inputCls}
              >
                <option value="cron">On a schedule (cron)</option>
                <option value="interval">Every X seconds</option>
              </select>
            </label>
          </div>
          <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
            {draft.schedule_type === "cron" ? (
              <label className="block">
                <span className="mb-0.5 block text-[11px] text-muted-foreground">Cron expression</span>
                <input
                  value={draft.cron}
                  onChange={(e) => setDraft({ ...draft, cron: e.target.value })}
                  placeholder="0 9 * * *"
                  disabled={busy !== null}
                  className={`${inputCls} font-mono`}
                />
              </label>
            ) : (
              <label className="block">
                <span className="mb-0.5 block text-[11px] text-muted-foreground">Every (seconds)</span>
                <input
                  value={draft.interval_seconds}
                  onChange={(e) => setDraft({ ...draft, interval_seconds: e.target.value })}
                  inputMode="numeric"
                  disabled={busy !== null}
                  className={`${inputCls} font-mono`}
                />
              </label>
            )}
            <label className="block">
              <span className="mb-0.5 block text-[11px] text-muted-foreground">Timezone</span>
              <input
                value={draft.timezone}
                onChange={(e) => setDraft({ ...draft, timezone: e.target.value })}
                placeholder="UTC"
                disabled={busy !== null}
                className={`${inputCls} font-mono`}
              />
            </label>
          </div>
          <label className="block">
            <span className="mb-0.5 block text-[11px] text-muted-foreground">What should the bot do?</span>
            <textarea
              value={draft.action}
              onChange={(e) => setDraft({ ...draft, action: e.target.value })}
              rows={2}
              placeholder="Summarize yesterday's activity and post the digest in this chat."
              disabled={busy !== null}
              className={inputCls}
            />
          </label>
          <div className="flex items-center gap-2">
            <Btn onClick={() => void create()} disabled={busy !== null}>
              {busy === "create" ? <Loader2 className="size-3.5 animate-spin" /> : <Plus className="size-3.5" />}
              {busy === "create" ? "Creating" : "Create routine"}
            </Btn>
            <span className="text-[10px] text-muted-foreground">
              A duplicate name is refused — delete the old routine first so one name never points at two schedules.
            </span>
          </div>
        </div>
      )}

      {rows === null ? (
        <p className="text-sm text-muted-foreground italic">
          The Gateway sent no routine list — which is not an empty one.
        </p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">No routines recorded for this bot.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-border/40 text-[10px] uppercase tracking-wide text-muted-foreground">
                <th className="py-1.5 pr-3 font-medium">Routine</th>
                <th className="py-1.5 pr-3 font-medium">Schedule</th>
                <th className="py-1.5 pr-3 font-medium">Next run</th>
                <th className="py-1.5 pr-3 font-medium">Last run</th>
                <th className="py-1.5 pr-3 font-medium">State</th>
                <th className="py-1.5 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((routine) => {
                const task = routine.task;
                const taskId = task?.id ?? null;
                const paused = task?.status === "paused";
                const rowKey = routine.name;
                const line = routineScheduleLine(routine);
                return (
                  <tr key={rowKey || `unnamed-${rows.indexOf(routine)}`} className="border-b border-border/30 last:border-0">
                    <td className="py-2 pr-3 align-top">
                      <p className="font-mono">{routine.name || "(unnamed)"}</p>
                      {routine.action && (
                        <p className="mt-0.5 max-w-[16rem] text-[10px] text-muted-foreground">{routine.action}</p>
                      )}
                    </td>
                    <td className="py-2 pr-3 align-top font-mono">
                      {line ?? <span className="italic text-muted-foreground">not reported</span>}
                      {routine.timezone && <p className="text-[10px] text-muted-foreground">{routine.timezone}</p>}
                    </td>
                    <td className="py-2 pr-3 align-top font-mono">
                      {task === null ? (
                        <span className="italic text-muted-foreground">no task recorded</span>
                      ) : (
                        (task.next_run_at ?? <span className="italic text-muted-foreground">not reported</span>)
                      )}
                    </td>
                    <td className="py-2 pr-3 align-top">
                      {routine.task_error ? (
                        <span className="text-destructive">task read failed: {routine.task_error}</span>
                      ) : task === null ? (
                        <span className="italic text-muted-foreground">no task recorded</span>
                      ) : task.last_error ? (
                        <span className="text-destructive">last run failed: {task.last_error}</span>
                      ) : task.last_run_at ? (
                        <span className="font-mono">{task.last_run_at}</span>
                      ) : (
                        <span className="italic text-muted-foreground">no run recorded</span>
                      )}
                    </td>
                    <td className="py-2 pr-3 align-top">
                      <VerbatimBadge
                        value={task?.status ?? null}
                        empty={task === null ? "no task recorded" : "status not reported"}
                      />
                      {task?.run_count != null && (
                        <span className="ml-1 text-[10px] text-muted-foreground">
                          ×{task.run_count} run{task.run_count === 1 ? "" : "s"}
                        </span>
                      )}
                    </td>
                    <td className="py-2 align-top">
                      <div className="flex flex-wrap items-center gap-1.5">
                        {taskId !== null &&
                          (paused ? (
                            <button
                              type="button"
                              disabled={busy !== null}
                              onClick={() =>
                                void act(`resume:${rowKey}`, async () => {
                                  await resumeTask(taskId);
                                  return `Resumed ${rowKey}.`;
                                })
                              }
                              className="flex items-center gap-1 rounded-md border border-border/60 px-1.5 py-0.5 text-[11px] hover:bg-muted/40 disabled:opacity-50"
                            >
                              {busy === `resume:${rowKey}` ? (
                                <Loader2 className="size-3 animate-spin" />
                              ) : (
                                <Play className="size-3" />
                              )}
                              Resume
                            </button>
                          ) : (
                            <button
                              type="button"
                              disabled={busy !== null}
                              onClick={() =>
                                void act(`pause:${rowKey}`, async () => {
                                  await pauseTask(taskId);
                                  return `Paused ${rowKey}.`;
                                })
                              }
                              className="flex items-center gap-1 rounded-md border border-border/60 px-1.5 py-0.5 text-[11px] hover:bg-muted/40 disabled:opacity-50"
                            >
                              {busy === `pause:${rowKey}` ? (
                                <Loader2 className="size-3 animate-spin" />
                              ) : (
                                <Pause className="size-3" />
                              )}
                              Pause
                            </button>
                          ))}
                        {confirming === rowKey ? (
                          <>
                            <button
                              type="button"
                              disabled={busy !== null}
                              onClick={() => void remove(rowKey)}
                              className="flex items-center gap-1 rounded-md border border-destructive/40 px-1.5 py-0.5 text-[11px] text-destructive hover:bg-destructive/10 disabled:opacity-50"
                            >
                              {busy === `delete:${rowKey}` ? (
                                <Loader2 className="size-3 animate-spin" />
                              ) : (
                                <Trash2 className="size-3" />
                              )}
                              Confirm
                            </button>
                            <button
                              type="button"
                              onClick={() => setConfirming(null)}
                              className="rounded-md border border-border/60 px-1.5 py-0.5 text-[11px] hover:bg-muted/40"
                            >
                              Cancel
                            </button>
                          </>
                        ) : (
                          <button
                            type="button"
                            disabled={busy !== null}
                            onClick={() => setConfirming(rowKey)}
                            title="Remove the routine and retire its scheduled task"
                            className="flex items-center gap-1 rounded-md border border-border/60 px-1.5 py-0.5 text-[11px] hover:bg-muted/40 disabled:opacity-50"
                          >
                            <Trash2 className="size-3" /> Delete
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <p className="text-[10px] text-muted-foreground">
        Pause and resume act on the scheduled task; delete retires the task first and the routine record second, so a
        running occurrence is never half-removed — it refuses and the routine stays whole.
      </p>
    </div>
  );
}
