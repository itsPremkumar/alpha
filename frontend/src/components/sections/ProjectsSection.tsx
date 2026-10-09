"use client";

import React, { useEffect, useMemo, useState } from "react";
import { DEFAULT_AGENT_NAME } from "@/lib/default-agent";
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
  updateCollaboration,
  getProjectTemplate,
  PROJECT_TEMPLATES,
  Project,
  ProjectAgentInput,
  ProjectMember,
  ProjectThread,
} from "@/lib/projects";
import { moveThread } from "@/lib/threads-ext";
import { Thread } from "@/types/chat";
import { Section, EmptyState, ErrorBox, Notice, Btn, Badge, Field, SkeletonList, inputCls } from "@/components/ui";
import { errMsg } from "@/lib/http";
import { Plus, Archive, ArchiveRestore, Trash2, RefreshCw, Pencil, Users, UserPlus, X, Sparkles, MessagesSquare, Hash, ChevronRight } from "lucide-react";
import { ProjectCrewPanel } from "@/components/sections/ProjectCrewPanel";
import { ProjectOverviewPanel } from "@/components/sections/ProjectOverviewPanel";
import { ProjectInspectorSection } from "@/components/sections/ProjectInspectorSection";

export interface ProjectBot {
  name: string;
  display_name: string;
}

/**
 * Which kind of project this is, from what the server reported.
 *
 * A project is not "a single project" or a "group project" by its name or its
 * instructions — the crew layer decides it. The membership roster
 * (`GET /projects/{id}/presence`) is that authority, and the crew service
 * provisions a shared group room at the *second* member, so:
 *
 *  - `crew`    — two or more agents attached: a shared room exists.
 *  - `solo`    — exactly one agent: real membership, no shared room yet.
 *  - `empty`   — nobody attached.
 *  - `unknown` — the roster read failed or has not answered, which is NOT the
 *                same claim as "this project has no crew".
 *
 * "unknown" is the state that matters: rendering a failed read as `solo` would
 * make an unreadable project look like a deliberately single-agent one.
 */
type ProjectShape = "crew" | "solo" | "empty" | "unknown";

const SHAPE_META: Record<ProjectShape, { label: string; tone: "purple" | "cyan" | "gray" | "amber"; title: string }> = {
  crew: { label: "team crew", tone: "purple", title: "Two or more agents attached — this project has a shared group room." },
  solo: { label: "single agent", tone: "cyan", title: "Exactly one agent attached — a shared room opens when a second joins." },
  empty: { label: "no agents", tone: "gray", title: "No agents are attached to this project." },
  unknown: { label: "crew unknown", tone: "amber", title: "The project roster could not be read, so this is unknown — not empty." },
};

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
  /**
   * Open the live per-project control surface for this project.
   *
   * Without this, the deep per-project state (war-room, RSI, perpetual, canary,
   * blueprints) is only reachable from the Workforce view, which keeps its own
   * project dropdown and defaults it to the FIRST project in the list — so
   * opening a project here and then hunting for its state lands you on someone
   * else's project. The view owner must select this project, not just switch.
   */
  onOpenLiveProject?: (projectId: string) => void;
}) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [threads, setThreads] = useState<Record<string, ProjectThread[]>>({});
  const [editing, setEditing] = useState<{ id: string; instructions: string } | null>(null);
  const [botFilter, setBotFilter] = useState<string>("all");
  const [membersByProject, setMembersByProject] = useState<Record<string, ProjectMember[]>>({});
  const [teamLoading, setTeamLoading] = useState<Record<string, boolean>>({});
  const [teamErrors, setTeamErrors] = useState<Record<string, string | null>>({});
  const [selectedBots, setSelectedBots] = useState<Record<string, string[]>>({});
  const [memberRole, setMemberRole] = useState("worker");
  const [memberBusy, setMemberBusy] = useState<string | null>(null);
  const [creatingProject, setCreatingProject] = useState(false);
  // Quick-start: a template fills the instructions and proposes a set of agent
  // *roles*. Bots are runtime roster data, so the role→bot mapping is shown for
  // confirmation and stays editable before anything is created.
  const [templateId, setTemplateId] = useState("blank");
  const [draftInstructions, setDraftInstructions] = useState("");
  const [draftBots, setDraftBots] = useState<string[]>([]);
  const [touchedDraft, setTouchedDraft] = useState(false);
  /**
   * The project whose full end-to-end read is open, or null for the list.
   *
   * This is the "View more" target: one project at a time, read live by
   * `ProjectInspectorSection` rather than assembled from what this list already
   * happens to hold — otherwise "every detail" would mean "every detail the card
   * had fetched", which was the gap that made the deep state unreachable.
   */
  const [inspectedProjectId, setInspectedProjectId] = useState<string | null>(null);
  /** "all" | "crew" | "solo" — a view filter, never a claim about the data. */
  const [shapeFilter, setShapeFilter] = useState<"all" | "crew" | "solo">("all");

  const template = useMemo(() => getProjectTemplate(templateId), [templateId]);

  /**
   * The bots a template would staff, matched to its roles.
   *
   * A role is a request, not an identity: no bot is assumed to exist, and a
   * role nothing matches is simply left unstaffed rather than filled with a
   * fallback agent. The user can change every pick before creating.
   */
  const suggestBots = (roles: string[]): string[] => {
    const available = props.bots.map((b) => b.name);
    const taken = new Set<string>();
    const picked: string[] = [];
    for (const role of roles) {
      const match = available.find(
        (name) => !taken.has(name) && name.toLowerCase().includes(role.toLowerCase().split("-")[0]),
      );
      if (match) {
        taken.add(match);
        picked.push(match);
      }
    }
    return picked;
  };

  const applyTemplate = (id: string) => {
    setTemplateId(id);
    const next = getProjectTemplate(id);
    // Do not stomp on text the user already typed.
    if (!touchedDraft) setDraftInstructions(next.instructions);
    setDraftBots(suggestBots(next.roles));
  };

  const botLabel = (botName: string | null): string => {
    if (!botName) return DEFAULT_AGENT_NAME;
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

  /**
   * Conversations the server has on this project that the grouped list above
   * does not carry.
   *
   * The two lists are different reads: `projectThreads()` is the project's own
   * record, `props.threads` is the workspace list. They can disagree while a
   * page is in flight, for an archived conversation, or after a partial page —
   * and when they do, rendering only `props.threads` hides a conversation the
   * project really holds. The difference is shown rather than discarded.
   */
  const serverOnlyThreads = (projectId: string) => {
    const onServer = threads[projectId] || [];
    if (onServer.length === 0) return [];
    const inGrouped = new Set(groupsFor(projectId).flatMap((group) => group.items.map((t) => t.thread_id)));
    return onServer.filter((t) => !inGrouped.has(t.thread_id));
  };

  /**
   * Classify a project from the roster read alone.
   *
   * While the read is in flight the answer is `unknown`, not `empty` — a list
   * that has not answered must not be painted as a project with no agents. The
   * same applies to a failed read: `teamErrors` says why, and the shape badge
   * says unknown rather than quietly reporting zero.
   */
  const shapeOf = (projectId: string): ProjectShape => {
    if (teamLoading[projectId] || teamErrors[projectId]) return "unknown";
    const members = membersByProject[projectId];
    if (!members) return "unknown";
    if (members.length === 0) return "empty";
    return members.length === 1 ? "solo" : "crew";
  };

  const visibleProjects = projects.filter((project) => {
    // The shape filter narrows on what the roster reported. A project whose
    // roster is unreadable matches "all" but neither shape, so a failed read can
    // never be silently hidden by a filter the operator did not intend.
    if (shapeFilter !== "all") {
      const shape = shapeOf(project.id);
      if (shape === "unknown") return false;
      if (shapeFilter === "crew" && shape !== "crew") return false;
      if (shapeFilter === "solo" && (shape === "crew" || shape === "empty")) return false;
    }
    if (botFilter === "all" || teamLoading[project.id]) return true;
    const isMember = (membersByProject[project.id] || []).some((member) => member.bot_name === botFilter);
    return isMember || groupsFor(project.id).some((group) => group.key === botFilter);
  });

  const shapeCounts = useMemo(() => {
    const counts = { crew: 0, solo: 0, empty: 0, unknown: 0 };
    for (const project of projects) counts[shapeOf(project.id)] += 1;
    return counts;
  }, [projects, membersByProject, teamLoading, teamErrors]);

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
      // Agents are sent with the project so the crew is provisioned in one
      // transaction; adding them afterwards would create a project with no
      // crew first and then patch it.
      const agents: ProjectAgentInput[] = draftBots.map((botName) => ({
        name: botName,
        role: template.roles[0] || "worker",
      }));
      const created = await createProject(projectName, draftInstructions, agents);

      // The project and its crew are now real. A template's coordination
      // settings are a second, separate write, so apply them here and report
      // honestly if that half failed — the project itself still exists.
      if (Object.keys(template.collaboration).length > 0) {
        try {
          await updateCollaboration(created.id, template.collaboration);
        } catch (err) {
          flash(
            `Project created, but its coordination settings were not applied. ${errMsg(err)}`,
          );
          await load();
          return;
        }
      }

      setName("");
      setDraftInstructions("");
      setDraftBots([]);
      setTouchedDraft(false);
      setTemplateId("blank");
      flash(
        agents.length > 0
          ? `Project created with ${agents.length} agent${agents.length === 1 ? "" : "s"}.`
          : "Project created.",
      );
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

  /**
   * The full end-to-end read of one project replaces the list while it is open.
   *
   * This section's own create/manage surface owns the list; the inspector is a
   * separate read-only surface mounted in its place, so "View more" is a real
   * drill-down rather than another collapsed accordion row.
   */
  if (inspectedProjectId) {
    return (
      <ProjectInspectorSection
        projectId={inspectedProjectId}
        onOpenThread={props.onOpenThread}
        onClose={() => setInspectedProjectId(null)}
        onOpenLiveProject={props.onOpenLiveProject}
      />
    );
  }

  return (
    <Section
      title="Projects"
      hint="Every project on this installation — single-agent and team crews alike. Open one to read its full state end to end."
      actions={
        <Btn variant="ghost" onClick={load}>
          <RefreshCw className="size-3.5" /> Refresh
        </Btn>
      }
    >
      {error && <ErrorBox message={error} onRetry={load} />}
      {notice && <Notice message={notice} />}

      <div className="rounded-2xl border border-border/60 bg-card p-4 space-y-3">
        <Field
          label="Start from a template"
          hint="A template fills the instructions and suggests which roles to staff. Nothing is created until you press Create."
        >
          <div className="flex gap-2">
            <select
              value={templateId}
              onChange={(e) => applyTemplate(e.target.value)}
              className={`${inputCls} !w-auto font-medium`}
              aria-label="Project template"
            >
              {PROJECT_TEMPLATES.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.name} — {option.summary}
                </option>
              ))}
            </select>
            <Btn variant="ghost" onClick={() => applyTemplate(templateId)} title="Re-apply this template's suggestions">
              <Sparkles className="size-3.5" /> Apply
            </Btn>
          </div>
        </Field>

        {template.roles.length > 0 && (
          <p className="text-[10px] text-muted-foreground">
            Looks for: {template.roles.join(", ")}.
            {suggestBots(template.roles).length < template.roles.length && (
              <>
                {" "}
                No bot matched{" "}
                {template.roles
                  .filter((role) => !suggestBots(template.roles).some((bot) => bot.toLowerCase().includes(role.toLowerCase().split("-")[0])))
                  .join(", ")}{" "}
                — create or rename a bot with that role, or pick one by hand below.
              </>
            )}
          </p>
        )}

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

        <Field
          label="Project instructions"
          hint="Every conversation inside this project follows these."
        >
          <textarea
            value={draftInstructions}
            onChange={(e) => {
              setDraftInstructions(e.target.value);
              setTouchedDraft(true);
            }}
            rows={2}
            placeholder="Optional — what should the agent do in this project?"
            className={inputCls}
            aria-label="Project instructions"
          />
        </Field>

        {props.bots.length > 0 && (
          <Field
            label={`Staff this project${draftBots.length > 0 ? ` (${draftBots.length} selected)` : ""}`}
            hint="Two or more agents get a shared group room. One agent works solo."
          >
            <div className="grid sm:grid-cols-2 gap-1.5">
              {props.bots.map((bot) => {
                const checked = draftBots.includes(bot.name);
                return (
                  <label
                    key={bot.name}
                    className="flex items-center gap-2 rounded-lg border border-border/60 bg-card px-2.5 py-2 cursor-pointer hover:border-primary/40"
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={(event) =>
                        setDraftBots((previous) =>
                          event.target.checked
                            ? [...previous, bot.name]
                            : previous.filter((botName) => botName !== bot.name),
                        )
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
          </Field>
        )}
      </div>

      {loading ? (
        <SkeletonList rows={4} />
      ) : projects.length === 0 ? (
        <EmptyState title="No projects yet" hint="Create one above, then move conversations into it from the chat sidebar." />
      ) : (
        <>
          {/*
            Two filters, both honest about what they hide. The shape counts are
            counted from real roster reads; a project whose roster could not be
            read is counted as unknown rather than folded into "single agent".
          */}
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-[11px] font-semibold">Crew type:</span>
            {(["all", "crew", "solo"] as const).map((option) => (
              <button
                key={option}
                type="button"
                onClick={() => setShapeFilter(option)}
                aria-pressed={shapeFilter === option}
                className={`rounded-full px-2.5 py-0.5 text-[11px] font-medium border transition-colors ${
                  shapeFilter === option
                    ? "bg-primary text-primary-foreground border-primary"
                    : "border-border/60 bg-card text-muted-foreground hover:text-foreground"
                }`}
              >
                {option === "all"
                  ? `All (${projects.length})`
                  : option === "crew"
                    ? `Team crews (${shapeCounts.crew})`
                    : `Single agent (${shapeCounts.solo})`}
              </button>
            ))}
            {shapeCounts.unknown > 0 && (
              <span className="text-[11px] text-amber-700 dark:text-amber-400">
                {shapeCounts.unknown} project{shapeCounts.unknown === 1 ? "" : "s"}: crew not reported
              </span>
            )}
          </div>

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
                const shapeMeta = SHAPE_META[shapeOf(p.id)];
                return (
                  <div key={p.id} className="rounded-xl border border-border/60 bg-card">
                    <div className="flex items-start gap-2 px-4 py-3 cursor-pointer" onClick={() => toggle(p)} role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && toggle(p)}>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <p className="text-sm font-semibold truncate">{p.name}</p>
                          {/* The id is what every other API call needs, and it was
                              previously unreachable from the UI. */}
                          <span className="text-[10px] font-mono text-muted-foreground inline-flex items-center gap-0.5" title={`Project id: ${p.id}`}>
                            <Hash className="size-2.5" />
                            {p.id.slice(0, 8)}
                          </span>
                          {/*
                            Single-agent vs team crew, from the roster the server
                            reported. This is the distinction a project list cannot
                            otherwise make: two projects with identical names and
                            instructions can be a one-bot job and a five-bot crew.
                          */}
                          <Badge tone={shapeMeta.tone} title={shapeMeta.title}>
                            {shapeMeta.label}
                          </Badge>
                        </div>
                        {/* Instructions were in the payload the whole time and were
                            never rendered outside the expanded editor, so a project
                            said nothing about itself until you opened and edited it. */}
                        {p.instructions ? (
                          <p className="text-[11px] text-muted-foreground mt-0.5 line-clamp-2">{p.instructions}</p>
                        ) : (
                          <p className="text-[11px] text-muted-foreground/80 mt-0.5 italic">no instructions set</p>
                        )}
                        <div className="flex gap-1 mt-1.5 flex-wrap items-center">
                          <span
                            className="text-[10px] px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground font-medium inline-flex items-center gap-0.5"
                            title={`${total} conversation${total === 1 ? "" : "s"} in this project`}
                          >
                            <MessagesSquare className="size-2.5" /> {total} chat{total === 1 ? "" : "s"}
                          </span>
                          <span
                            className="text-[10px] px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground font-medium inline-flex items-center gap-0.5"
                            title={`${members.length} bot${members.length === 1 ? "" : "s"} attached`}
                          >
                            <Users className="size-2.5" /> {members.length} bot{members.length === 1 ? "" : "s"}
                          </span>
                          {p.updated_at && (
                            <span className="text-[10px] text-muted-foreground" title={p.updated_at}>
                              updated {new Date(p.updated_at).toLocaleDateString()}
                            </span>
                          )}
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
                      <div className="flex flex-col items-end gap-1.5 shrink-0">
                        <Badge tone={p.status === "archived" ? "gray" : "green"}>{p.status}</Badge>
                        {/*
                          "View more" is a real button rather than the row's own
                          expand: it swaps this whole surface for the project's
                          full read, so it must not be nested inside the clickable
                          card header that toggles the inline detail.
                        */}
                        <button
                          type="button"
                          onClick={() => setInspectedProjectId(p.id)}
                          className="inline-flex items-center gap-1 px-2 py-1 rounded-lg border border-border text-[11px] font-semibold hover:bg-muted"
                          title={`Read every detail the Gateway knows about ${p.name}`}
                        >
                          View more <ChevronRight className="size-3" />
                        </button>
                        <span className="text-[11px] text-muted-foreground">{open === p.id ? "Hide" : "Show"}</span>
                      </div>
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

                  <div className="rounded-xl border border-border/60 bg-muted/20 p-3 space-y-3">
                    <ProjectOverviewPanel
                      projectId={p.id}
                      onOpenLive={props.onOpenLiveProject}
                    />
                  </div>

                  <div className="rounded-xl border border-border/60 bg-muted/20 p-3 space-y-3">
                    <ProjectCrewPanel
                      projectId={p.id}
                      projectName={p.name}
                    />
                  </div>

                  <div>
                    <p className="text-[11px] font-semibold mb-1.5">
                      Conversations by bot ({groupsFor(p.id).reduce((n, g) => n + g.items.length, 0)})
                    </p>
                    {groupsFor(p.id).length === 0 ? (
                      <p className="text-[11px] text-muted-foreground">
                        {(threads[p.id] || []).length === 0
                          ? "Empty — move a chat here from the sidebar menu."
                          : "None of the workspace's loaded conversations are in this project, but the project's own record lists some below."}
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
                                  <button type="button" onClick={() => props.onOpenThread(t.thread_id)} className="text-[11px] font-medium flex-1 text-left truncate hover:text-primary" title={t.updated_at ? new Date(t.updated_at).toLocaleString() : "no last-activity time reported"}>
                                    {t.title}
                                  </button>
                                  {t.updated_at ? (
                                    <span className="text-[10px] text-muted-foreground shrink-0">{new Date(t.updated_at).toLocaleDateString()}</span>
                                  ) : (
                                    <span className="text-[10px] text-muted-foreground/80 shrink-0" title="The server reported no last-activity time for this conversation.">no date</span>
                                  )}
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

                    {/*
                      The grouped list above is built from the workspace's own thread
                      list, so a conversation the server has on the project but this
                      list has not loaded (a page still in flight, an archived thread,
                      a partial page) is invisible here — while the count on the card
                      would silently under-report. `projectThreads()` is the
                      authoritative read and is already fetched per project, so any row
                      it reports that the grouped list does not carry is listed here
                      rather than dropped.
                    */}
                    {serverOnlyThreads(p.id).length > 0 && (
                      <div className="mt-2 rounded-lg border border-amber-500/40 bg-amber-500/5 p-2">
                        <p className="text-[11px] font-semibold text-amber-700 dark:text-amber-400">
                          On the server but not in this list ({serverOnlyThreads(p.id).length})
                        </p>
                        <p className="text-[10px] text-amber-700/80 dark:text-amber-400/80 mb-1.5">
                          The project holds these conversations, but the workspace list has not loaded them. They are shown
                          from the project’s own record.
                        </p>
                        <div className="space-y-1">
                          {serverOnlyThreads(p.id).map((t) => (
                            <div key={t.thread_id} className="flex items-center gap-2 rounded-lg bg-card border border-border/40 px-2.5 py-1.5">
                              <button
                                type="button"
                                onClick={() => props.onOpenThread(t.thread_id)}
                                className="text-[11px] font-medium flex-1 text-left truncate hover:text-primary"
                              >
                                {t.display_name}
                              </button>
                              <span className="text-[10px] text-muted-foreground shrink-0 font-mono" title={t.thread_id}>
                                {t.thread_id.slice(0, 8)}
                              </span>
                              {t.updated_at ? (
                                <span className="text-[10px] text-muted-foreground shrink-0">{new Date(t.updated_at).toLocaleDateString()}</span>
                              ) : null}
                            </div>
                          ))}
                        </div>
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
