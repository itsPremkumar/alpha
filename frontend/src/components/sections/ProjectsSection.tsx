"use client";

import React, { useEffect, useState } from "react";
import {
  listProjects,
  createProject,
  updateProject,
  archiveProject,
  restoreProject,
  deleteProject,
  projectThreads,
  listProjectAgents,
  confirmProjectAgents,
  attachProjectAgents,
  detachProjectAgent,
  Project,
  ProjectMember,
} from "@/lib/projects";
import { moveThread } from "@/lib/threads-ext";
import { Thread } from "@/types/chat";
import { Section, EmptyState, ErrorBox, Notice, Btn, Badge, Field, SkeletonList, inputCls } from "@/components/ui";
import { errMsg } from "@/lib/http";
import { Plus, Archive, ArchiveRestore, Trash2, RefreshCw, Pencil, Users, UserPlus, X } from "lucide-react";

export interface ProjectBot {
  name: string;
  display_name: string;
}

export function ProjectsSection(props: {
  onOpenThread: (id: string) => void;
  /** All known conversations (server list, carries bot + project links). */
  threads: Thread[];
  bots: ProjectBot[];
  /**
   * Re-read the parent's thread list. Moving a conversation between projects
   * changes the header project picker and the chat sidebar grouping, so the
   * owning view must refresh instead of keeping a stale scope.
   */
  onThreadsChanged?: () => void;
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
  const [membersByProject, setMembersByProject] = useState<Record<string, ProjectMember[]>>({});
  const [teamLoading, setTeamLoading] = useState<Record<string, boolean>>({});
  const [teamErrors, setTeamErrors] = useState<Record<string, string | null>>({});
  const [selectedBots, setSelectedBots] = useState<Record<string, string[]>>({});
  const [memberRole, setMemberRole] = useState("worker");
  const [memberBusy, setMemberBusy] = useState<string | null>(null);
  const [creatingProject, setCreatingProject] = useState(false);

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

  const visibleProjects = projects.filter((project) => {
    if (botFilter === "all" || teamLoading[project.id]) return true;
    const isMember = (membersByProject[project.id] || []).some((member) => member.bot_name === botFilter);
    return isMember || groupsFor(project.id).some((group) => group.key === botFilter);
  });

  const refreshTeam = async (projectId: string) => {
    setTeamLoading((previous) => ({ ...previous, [projectId]: true }));
    setTeamErrors((previous) => ({ ...previous, [projectId]: null }));
    try {
      const members = await listProjectAgents(projectId);
      setMembersByProject((previous) => ({ ...previous, [projectId]: members }));
    } catch (err) {
      setTeamErrors((previous) => ({ ...previous, [projectId]: errMsg(err) }));
    } finally {
      setTeamLoading((previous) => ({ ...previous, [projectId]: false }));
    }
  };

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const loaded = await listProjects();
      setProjects(loaded);
      await Promise.allSettled(loaded.map((project) => refreshTeam(project.id)));
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // Initial project/team load only; explicit Refresh re-runs it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const flash = (m: string) => {
    setNotice(m);
    window.setTimeout(() => setNotice(null), 4000);
  };

  const toggle = async (project: Project) => {
    const isOpen = open === project.id;
    setOpen(isOpen ? null : project.id);
    if (isOpen) return;
    const tasks: Promise<unknown>[] = [refreshTeam(project.id)];
    if (!threads[project.id]) {
      tasks.push(
        projectThreads(project.id).then((list) => {
          setThreads((previous) => ({ ...previous, [project.id]: list }));
        }),
      );
    }
    const results = await Promise.allSettled(tasks);
    const failure = results.find((result) => result.status === "rejected");
    if (failure?.status === "rejected") setError(errMsg(failure.reason));
  };

  const act = async (fn: () => Promise<void>, ok: string): Promise<boolean> => {
    try {
      await fn();
      flash(ok);
      await load();
      return true;
    } catch (e) {
      setError(errMsg(e));
      return false;
    }
  };

  const createGlobalProject = async () => {
    const projectName = name.trim();
    if (!projectName || creatingProject) return;
    setCreatingProject(true);
    try {
      await createProject(projectName);
      setName("");
      flash("Project created.");
      await load();
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setCreatingProject(false);
    }
  };

  const attachSelectedBots = async (projectId: string) => {
    const names = selectedBots[projectId] || [];
    if (names.length === 0 || memberBusy) return;
    setMemberBusy(projectId);
    setTeamErrors((previous) => ({ ...previous, [projectId]: null }));
    try {
      const role = memberRole.trim().slice(0, 64) || "worker";
      await attachProjectAgents(
        projectId,
        names.map((name) => ({ name, role })),
        role,
      );
      // Only claim success once the server's own roster confirms the change.
      // attachProjectAgents returning 2xx is not proof the bots joined.
      const members = await confirmProjectAgents(projectId, names, true);
      setMembersByProject((previous) => ({ ...previous, [projectId]: members }));
      setSelectedBots((previous) => ({ ...previous, [projectId]: [] }));
      flash(`${names.length} bot${names.length === 1 ? "" : "s"} added to the project.`);
    } catch (err) {
      setTeamErrors((previous) => ({ ...previous, [projectId]: errMsg(err) }));
      // Re-read the roster so the UI never keeps an unconfirmed membership.
      await refreshTeam(projectId);
    } finally {
      setMemberBusy(null);
    }
  };

  const removeBot = async (projectId: string, botName: string) => {
    if (memberBusy) return;
    setMemberBusy(projectId);
    setTeamErrors((previous) => ({ ...previous, [projectId]: null }));
    try {
      await detachProjectAgent(projectId, botName);
      const members = await confirmProjectAgents(projectId, [botName], false);
      setMembersByProject((previous) => ({ ...previous, [projectId]: members }));
      flash(`${botLabel(botName)} removed from the project.`);
    } catch (err) {
      setTeamErrors((previous) => ({ ...previous, [projectId]: errMsg(err) }));
      await refreshTeam(projectId);
    } finally {
      setMemberBusy(null);
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
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void createGlobalProject();
              }}
              placeholder="Project name…"
              className={inputCls}
              aria-label="New project name"
            />
            <Btn onClick={() => void createGlobalProject()} disabled={!name.trim() || creatingProject}>
              <Plus className="size-3.5" /> {creatingProject ? "Creating…" : "Create"}
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
                const members = membersByProject[p.id] || [];
                const selectedCount = (selectedBots[p.id] || []).length;
                return (
                  <div key={p.id} className="rounded-xl border border-border/60 bg-card">
                    <div className="flex items-center gap-2 px-4 py-3 cursor-pointer" onClick={() => toggle(p)} role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && toggle(p)}>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-semibold truncate">{p.name}</p>
                        <div className="flex gap-1 mt-1 flex-wrap">
                          {members.slice(0, 4).map((member) => (
                            <span key={member.bot_name} className="text-[10px] px-1.5 py-0.5 rounded-full bg-cyan-500/10 text-cyan-700 dark:text-cyan-300 font-medium">
                              <Users className="size-2.5 mr-0.5 inline" /> {botLabel(member.bot_name)}
                            </span>
                          ))}
                          {members.length > 4 && (
                            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground">+{members.length - 4} bots</span>
                          )}
                          {teamLoading[p.id] ? (
                            <span className="text-[10px] text-muted-foreground">loading project team…</span>
                          ) : teamErrors[p.id] ? (
                            <span className="text-[10px] text-destructive">project team unavailable</span>
                          ) : members.length === 0 && groups.length === 0 ? (
                            <span className="text-[10px] text-muted-foreground">no bots or chats assigned yet</span>
                          ) : null}
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

                  <div className="rounded-xl border border-border/60 bg-muted/20 p-3 space-y-3">
                    <div className="flex items-center justify-between gap-2 flex-wrap">
                      <div>
                        <p className="text-[11px] font-semibold inline-flex items-center gap-1.5">
                          <Users className="size-3.5 text-primary" /> Project team {teamLoading[p.id] ? "(loading…)" : `(${members.length})`}
                        </p>
                        <p className="text-[10px] text-muted-foreground mt-0.5">
                          Add several bot profiles to collaborate in this project.
                        </p>
                      </div>
                      <Btn variant="ghost" onClick={() => void refreshTeam(p.id)} disabled={teamLoading[p.id]}>
                        <RefreshCw className="size-3.5" /> {teamLoading[p.id] ? "Loading…" : "Refresh team"}
                      </Btn>
                    </div>

                    {teamErrors[p.id] && <ErrorBox message={teamErrors[p.id]!} onRetry={() => void refreshTeam(p.id)} />}

                    {teamLoading[p.id] ? (
                      <p className="text-[11px] text-muted-foreground">Loading project team…</p>
                    ) : members.length === 0 ? (
                      <p className="text-[11px] text-muted-foreground">No bots are attached yet.</p>
                    ) : (
                      <div className="flex flex-wrap gap-1.5">
                        {members.map((member) => (
                          <span key={member.bot_name} className="inline-flex items-center gap-1 rounded-lg border border-border/70 bg-card px-2 py-1 text-[11px]">
                            <strong>{botLabel(member.bot_name)}</strong>
                            <span className="text-muted-foreground">{member.role_in_project}</span>
                            <Badge tone={member.status === "active" ? "green" : "gray"}>{member.status}</Badge>
                            <button
                              type="button"
                              onClick={() => void removeBot(p.id, member.bot_name)}
                              disabled={Boolean(memberBusy)}
                              className="text-muted-foreground hover:text-destructive disabled:opacity-40"
                              title={`Remove ${botLabel(member.bot_name)} from this project`}
                              aria-label={`Remove ${botLabel(member.bot_name)} from this project`}
                            >
                              <X className="size-3" />
                            </button>
                          </span>
                        ))}
                      </div>
                    )}

                    {props.bots.length > 0 && (
                      <div className="space-y-2 border-t border-border/50 pt-3">
                        <div className="flex items-center gap-2 flex-wrap">
                          <label className="text-[10px] font-semibold text-muted-foreground" htmlFor={`role-${p.id}`}>
                            New bot role
                          </label>
                          <input
                            id={`role-${p.id}`}
                            value={memberRole}
                            onChange={(event) => setMemberRole(event.target.value)}
                            maxLength={64}
                            className="bg-card border border-border/70 rounded-lg px-2 py-1 text-[11px] min-w-32"
                            placeholder="worker"
                          />
                        </div>
                        <div className="grid sm:grid-cols-2 gap-1.5">
                          {props.bots
                            .filter((bot) => !members.some((member) => member.bot_name === bot.name))
                            .map((bot) => {
                              const checked = (selectedBots[p.id] || []).includes(bot.name);
                              return (
                                <label key={bot.name} className="flex items-center gap-2 rounded-lg border border-border/60 bg-card px-2.5 py-2 cursor-pointer hover:border-primary/40">
                                  <input
                                    type="checkbox"
                                    checked={checked}
                                    onChange={(event) =>
                                      setSelectedBots((previous) => {
                                        const current = previous[p.id] || [];
                                        return {
                                          ...previous,
                                          [p.id]: event.target.checked
                                            ? [...current, bot.name]
                                            : current.filter((name) => name !== bot.name),
                                        };
                                      })
                                    }
                                    className="accent-primary"
                                  />
                                  <span className="min-w-0">
                                    <span className="block text-[11px] font-medium truncate">{bot.display_name || bot.name}</span>
                                    <span className="block text-[10px] text-muted-foreground font-mono truncate">@{bot.name}</span>
                                  </span>
                                </label>
                              );
                            })}
                        </div>
                        <Btn
                          onClick={() => void attachSelectedBots(p.id)}
                          disabled={memberBusy === p.id || selectedCount === 0}
                        >
                          <UserPlus className="size-3.5" /> {selectedCount > 0 ? `Add ${selectedCount} selected bot${selectedCount === 1 ? "" : "s"}` : "Select bots to add"}
                        </Btn>
                      </div>
                    )}
                  </div>

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
                                  <button
                                    type="button"
                                    onClick={() => {
                                      void act(() => moveThread(t.thread_id, null), "Moved out of project.").then((confirmed) => {
                                        if (!confirmed) return;
                                        setThreads((previous) => ({
                                          ...previous,
                                          [p.id]: (previous[p.id] || []).filter((thread) => thread.thread_id !== t.thread_id),
                                        }));
                                        // The parent view groups by project and owns the
                                        // header picker, so it must re-read real state.
                                        props.onThreadsChanged?.();
                                      });
                                    }}
                                    className="text-[11px] text-muted-foreground hover:text-destructive"
                                    title="Remove from project"
                                  >
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
