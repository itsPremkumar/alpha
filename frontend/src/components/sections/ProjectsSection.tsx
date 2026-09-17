"use client";

import React, { useEffect, useState } from "react";
import { listProjects, createProject, updateProject, archiveProject, restoreProject, deleteProject, projectThreads, Project } from "@/lib/projects";
import { moveThread } from "@/lib/threads-ext";
import { Thread } from "@/types/chat";
import { Section, EmptyState, ErrorBox, Notice, Btn, Badge, Field, SkeletonList, inputCls } from "@/components/ui";
import { errMsg } from "@/lib/http";
import { Plus, Archive, ArchiveRestore, Trash2, RefreshCw, Pencil } from "lucide-react";

export interface ProjectBot {
  name: string;
  display_name: string;
}

export function ProjectsSection(props: {
  onOpenThread: (id: string) => void;
  /** All known conversations (server list, carries bot + project links). */
  threads: Thread[];
  bots: ProjectBot[];
}) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [threads, setThreads] = useState<Record<string, Array<{ thread_id: string; display_name: string }>>>({});
  const [editing, setEditing] = useState<{ id: string; instructions: string } | null>(null);
  const [botFilter, setBotFilter] = useState<string>("all");

  const botLabel = (botName: string | null): string => {
    if (!botName) return "Lead Agent";
    return props.bots.find((b) => b.name === botName)?.display_name || botName;
  };

  /** This project's conversations, grouped per bot (multi-bot project = several groups). */
  const groupsFor = (projectId: string): Array<{ key: string; label: string; items: Thread[] }> => {
    const mine = props.threads.filter((t) => t.projectId === projectId);
    const map = new Map<string, Thread[]>();
    for (const t of mine) {
      const key = t.botName || "";
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(t);
    }
    return Array.from(map.entries())
      .map(([key, items]) => ({
        key,
        label: botLabel(key || null),
        items: items.sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || "")),
      }))
      .sort((a, b) => b.items.length - a.items.length);
  };

  const visibleProjects = projects.filter((p) => {
    if (botFilter === "all") return true;
    return groupsFor(p.id).some((g) => g.key === botFilter);
  });

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      setProjects(await listProjects());
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

  const toggle = async (p: Project) => {
    const isOpen = open === p.id;
    setOpen(isOpen ? null : p.id);
    if (!isOpen && !threads[p.id]) {
      try {
        const list = await projectThreads(p.id);
        setThreads((prev) => ({ ...prev, [p.id]: list }));
      } catch (e) {
        setError(errMsg(e));
      }
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
      title="Projects"
      hint="Group related conversations — one project per client, topic or goal. Give a project instructions and every chat inside follows them."
      actions={
        <Btn variant="ghost" onClick={load}>
          <RefreshCw className="size-3.5" /> Refresh
        </Btn>
      }
    >
      {error && <ErrorBox message={error} onRetry={load} />}
      {notice && <Notice message={notice} />}

      <div className="rounded-2xl border border-border/60 bg-card p-4">
        <Field label="New project" hint="Example: Website redesign, Q4 planning, Customer support.">
          <div className="flex gap-2">
            <input value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && name.trim() && act(() => createProject(name.trim()).then(() => undefined), "Project created.")} placeholder="Project name…" className={inputCls} aria-label="New project name" />
            <Btn onClick={() => name.trim() && act(() => createProject(name.trim()).then(() => { setName(""); }), "Project created.")} disabled={!name.trim()}>
              <Plus className="size-3.5" /> Create
            </Btn>
          </div>
        </Field>
      </div>

      {loading ? (
        <SkeletonList rows={4} />
      ) : projects.length === 0 ? (
        <EmptyState title="No projects yet" hint="Create one above, then move conversations into it from the chat sidebar." />
      ) : (
        <>
          <div className="flex items-center gap-2 flex-wrap">
            <label className="text-[11px] font-semibold" htmlFor="project-bot-filter">
              Show projects for:
            </label>
            <select
              id="project-bot-filter"
              value={botFilter}
              onChange={(e) => setBotFilter(e.target.value)}
              className={`${inputCls} !w-auto font-medium`}
            >
              <option value="all">All bots</option>
              {props.bots.map((b) => (
                <option key={b.name} value={b.name}>
                  {b.display_name}
                </option>
              ))}
            </select>
            {botFilter !== "all" && (
              <span className="text-[11px] text-muted-foreground">
                {visibleProjects.length} project{visibleProjects.length === 1 ? "" : "s"} involve {botLabel(botFilter)}
              </span>
            )}
          </div>
          {visibleProjects.length === 0 ? (
            <EmptyState title="No projects for this bot" hint="Move one of its chats into a project from the sidebar, or pick another bot." />
          ) : (
            <div className="space-y-2">
              {visibleProjects.map((p) => {
                const groups = groupsFor(p.id);
                const total = groups.reduce((n, g) => n + g.items.length, 0);
                return (
                  <div key={p.id} className="rounded-xl border border-border/60 bg-card">
                    <div className="flex items-center gap-2 px-4 py-3 cursor-pointer" onClick={() => toggle(p)} role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && toggle(p)}>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-semibold truncate">{p.name}</p>
                        <div className="flex gap-1 mt-1 flex-wrap">
                          {groups.length === 0 ? (
                            <span className="text-[10px] text-muted-foreground">no chats tracked yet</span>
                          ) : (
                            groups.slice(0, 4).map((g) => (
                              <span key={g.key} className="text-[10px] px-1.5 py-0.5 rounded-full bg-primary/10 text-primary font-medium">
                                {g.label} • {g.items.length}
                              </span>
                            ))
                          )}
                          {groups.length > 4 && (
                            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground">+{groups.length - 4}</span>
                          )}
                        </div>
                      </div>
                      <Badge tone={p.status === "archived" ? "gray" : "green"}>{p.status}</Badge>
                      <span className="text-[11px] text-muted-foreground shrink-0">{open === p.id ? "Hide" : `Show${total ? ` (${total})` : ""}`}</span>
                    </div>
              {open === p.id && (
                <div className="px-4 pb-4 space-y-3 border-t border-border/50 pt-3">
                  {editing?.id === p.id ? (
                    <div className="space-y-2">
                      <Field label="Project instructions" hint="The agent follows these in every conversation inside this project.">
                        <textarea value={editing.instructions} onChange={(e) => setEditing({ id: p.id, instructions: e.target.value })} rows={3} className={inputCls} />
                      </Field>
                      <div className="flex gap-2">
                        <Btn onClick={() => act(() => updateProject(p.id, { instructions: editing.instructions }).then(() => setEditing(null)), "Instructions saved.")}>Save</Btn>
                        <Btn variant="ghost" onClick={() => setEditing(null)}>Cancel</Btn>
                      </div>
                    </div>
                  ) : (
                    <div className="flex items-start gap-2">
                      <p className="text-xs text-muted-foreground flex-1 whitespace-pre-wrap">{p.instructions || "No instructions yet."}</p>
                      <Btn variant="ghost" onClick={() => setEditing({ id: p.id, instructions: p.instructions })}>
                        <Pencil className="size-3.5" /> Edit
                      </Btn>
                    </div>
                  )}
                  <div>
                    <p className="text-[11px] font-semibold mb-1.5">
                      Conversations by bot ({groupsFor(p.id).reduce((n, g) => n + g.items.length, 0)})
                    </p>
                    {groupsFor(p.id).length === 0 ? (
                      <p className="text-[11px] text-muted-foreground">
                        {(threads[p.id] || []).length === 0
                          ? "Empty — move a chat here from the sidebar menu."
                          : "Tracked on the server — open a chat to link its bot."}
                      </p>
                    ) : (
                      <div className="space-y-2.5">
                        {groupsFor(p.id).map((g) => (
                          <div key={g.key} className="rounded-xl bg-muted/30 border border-border/40 p-2">
                            <p className="text-[11px] font-bold px-1 pb-1.5">
                              {g.label} <span className="font-normal text-muted-foreground">({g.items.length})</span>
                            </p>
                            <div className="space-y-1">
                              {g.items.map((t) => (
                                <div key={t.thread_id} className="flex items-center gap-2 rounded-lg bg-card border border-border/40 px-2.5 py-1.5">
                                  <button type="button" onClick={() => props.onOpenThread(t.thread_id)} className="text-[11px] font-medium flex-1 text-left truncate hover:text-primary" title={new Date(t.updated_at).toLocaleString()}>
                                    {t.title}
                                  </button>
                                  <button type="button" onClick={() => act(() => moveThread(t.thread_id, null), "Moved out of project.").then(() => setThreads((prev) => ({ ...prev, [p.id]: (prev[p.id] || []).filter((x) => x.thread_id !== t.thread_id) })))} className="text-[11px] text-muted-foreground hover:text-destructive" title="Remove from project">
                                    Remove
                                  </button>
                                </div>
                              ))}
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                  <div className="flex gap-2 flex-wrap">
                    {p.status === "archived" ? (
                      <Btn variant="ghost" onClick={() => act(() => restoreProject(p.id), "Restored.")}>
                        <ArchiveRestore className="size-3.5" /> Restore
                      </Btn>
                    ) : (
                      <Btn variant="ghost" onClick={() => act(() => archiveProject(p.id), "Archived.")}>
                        <Archive className="size-3.5" /> Archive
                      </Btn>
                    )}
                    <Btn variant="danger" onClick={() => window.confirm(`Delete project "${p.name}"? Chats inside are kept.`) && act(() => deleteProject(p.id), "Deleted.")}>
                      <Trash2 className="size-3.5" /> Delete
                    </Btn>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    )}
  </>
)}
    </Section>
  );
}
