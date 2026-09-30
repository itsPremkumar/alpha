/**
 * Chat-shell derivations: what may be *claimed* about a bot, a project and a
 * conversation, and what must be rendered as unknown instead.
 *
 * Every helper here is pure. The components under `components/chat-shell/` call
 * these and render the result, so the rule "a number the server did not send is
 * never a number" is enforced in one testable file rather than in each JSX
 * branch where it would be forgotten.
 *
 * The three-state vocabulary used throughout:
 *
 *   - a **measured** value is one the Gateway returned. It is rendered as
 *     itself, including a genuine `0`, because `0` is a measurement;
 *   - **not reported** means the field was absent. It is rendered as words,
 *     never as `0`, `""` or an empty list;
 *   - **unreadable** means a read failed, or answered in a shape this build
 *     cannot interpret. It is rendered as a failure with the server's reason.
 *
 * Those are three different claims and collapsing any two of them is the defect
 * class this module exists to prevent.
 */

import { get } from "./http";
import { absoluteStamp, isRecent, PRESENCE_WINDOW_SECONDS, relTime } from "./time";
import { listProjectAgents, projectThreads, type Project, type ProjectThread } from "./projects";
import { threadTitle } from "./threads-ext";
import type { BotProfile } from "@/types/bots";
import type { Thread } from "@/types/chat";

/* ── counts ─────────────────────────────────────────────────────────────── */

/**
 * The marker for a value the Gateway did not report.
 *
 * A distinct symbol rather than `-1` or `0`, so no arithmetic downstream can
 * turn "unknown" into a number by accident.
 */
export const NOT_REPORTED = Symbol.for("alpha.chat-shell.notReported");
export type NotReported = typeof NOT_REPORTED;

/** A measured count, or the explicit "not reported" marker. Never a fake 0. */
export type Count = number | NotReported;

/**
 * Read a count out of a server payload.
 *
 * `null`, `undefined`, a non-number and a non-finite number are all
 * "not reported". A genuine `0` is returned as `0` — the live Gateway sends
 * `{"completed":0,"failed":0,"total_runs":0}` for a bot that has never run, and
 * turning that into "not reported" would be a different lie.
 */
export function measuredCount(value: unknown): Count {
  return typeof value === "number" && Number.isFinite(value) ? value : NOT_REPORTED;
}

/** A measured number, or `null`. The shape the `number | null` props use. */
export function countOrNull(value: unknown): number | null {
  const n = measuredCount(value);
  return n === NOT_REPORTED ? null : n;
}

/** `true` only for a real measurement. */
export function isMeasured(value: Count | number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/**
 * The text a count renders as.
 *
 * The measured and unreported cases are deliberately *not* interchangeable
 * strings, and a measured `0` renders as "0" rather than as the empty string
 * or an em dash that a caller might mistake for the unknown case.
 */
export function countText(value: Count | number | null | undefined): string {
  if (isMeasured(value)) return String(value);
  return "not reported";
}

/** The tooltip for a count cell: states where the number came from. */
export function countTitle(value: Count | number | null | undefined, source: string): string {
  return isMeasured(value)
    ? `${source}: the Gateway reported ${value}.`
    : `${source}: the Gateway did not report a value. This is not a measurement of zero.`;
}

/* ── bot presence ───────────────────────────────────────────────────────── */

/**
 * The three presence readings, which are three different claims.
 *
 *   - `unrecorded`  the Gateway has no `last_active` for this bot at all.
 *                  No dot is drawn, because any dot is a claim.
 *   - `recent`      `last_active` is inside the presence window. This is a
 *                  *display* reading, not a liveness verdict:
 *                  `alpha.bots.health` owns healthy/stale/stalled/dead.
 *   - `idle`        `last_active` was reported but falls outside the window,
 *                  so we know the instant and know it is not recent.
 */
export type PresenceState = "recent" | "idle" | "unrecorded";

export interface PresenceView {
  state: PresenceState;
  /** Sentence naming the reading and, where one exists, the measured instant. */
  label: string;
  /** Raw timestamp as the Gateway sent it, or `null`. Never a synthesized one. */
  raw: string | number | null;
}

export function botPresence(
  bot: Pick<BotProfile, "last_active">,
  now: number = Date.now(),
): PresenceView {
  const raw = bot.last_active ?? null;
  if (raw === null || raw === undefined || raw === "") {
    return {
      state: "unrecorded",
      label: "presence not reported",
      raw: null,
    };
  }
  // `relTime` returns null for a value it cannot read. A present-but-unreadable
  // timestamp is neither "recent" nor a known idle instant, so it is reported
  // as unrecorded rather than silently drawn as an idle dot.
  const since = relTime(raw, now);
  if (since === null) {
    return { state: "unrecorded", label: "presence not reported", raw: null };
  }
  if (isRecent(raw, PRESENCE_WINDOW_SECONDS, now)) {
    return { state: "recent", label: "seen working just now", raw };
  }
  return { state: "idle", label: `last seen ${since}`, raw };
}

/**
 * The dot class for a presence reading.
 *
 * `unrecorded` is a hollow ring, not a grey fill: a grey filled dot next to
 * "recent" would be read as "offline", which is a claim the Gateway never made.
 */
export function presenceDotClass(state: PresenceState): string {
  if (state === "recent") return "bg-emerald-500";
  if (state === "idle") return "bg-muted-foreground/50";
  return "bg-transparent ring-1 ring-border";
}

/** The tooltip behind a presence dot; always names what it is based on. */
export function presenceTitle(view: PresenceView, windowSeconds: number = PRESENCE_WINDOW_SECONDS): string {
  if (view.state === "recent") {
    return `Working within the last ${windowSeconds} seconds, read from the Gateway's last_active. A display reading, not a health verdict.`;
  }
  if (view.state === "idle") {
    return view.raw
      ? `Not seen in the last ${windowSeconds} seconds. Last active: ${absoluteStamp(view.raw) ?? String(view.raw)}.`
      : "Not seen recently. The Gateway reported no readable last_active.";
  }
  return "The Gateway reported no last_active for this bot, so no presence is drawn. This is not an offline reading.";
}

/**
 * The server's own status enum, read as an enum.
 *
 * `normalizeBot` in `lib/bots.ts` already substitutes the string "active" for a
 * row that carried no `status` at all. That substitution is invisible from
 * here, so a bare enum is never rendered as a green/amber/red verdict on this
 * surface: the rail shows presence (which is derived) and, separately, the
 * literal status string in words, so a substituted default is visible as the
 * word "active" rather than as a badge colour.
 */
export function botStatusText(status: string | null | undefined): string {
  const text = typeof status === "string" ? status.trim() : "";
  return text || "status not reported";
}

/* ── the whole per-project rail row ─────────────────────────────────────── */

/**
 * Everything the rail claims about one project, each field with its own failure.
 *
 * The two reads are kept apart on purpose. A project whose presence read fails
 * still has a measured conversation count, and a project whose conversation
 * read fails still has a measured crew; folding them into one status would lose
 * whichever read answered.
 */
export interface ProjectRailRow {
  projectId: string;
  conversationCount: number | null;
  conversationError: string | null;
  /** Member bot names from `GET /projects/{id}/presence`, or `null` if it failed. */
  members: string[] | null;
  crewError: string | null;
  /** `true`/`false` when the roster answered; `null` when it did not. */
  leadsSelectedBot: boolean | null;
}

/**
 * Read the rail's per-project facts.
 *
 * `GET /projects` returns `ProjectResponse` with no counts and no membership
 * (`projects.py:42-49`), so both facts need their own request per project. That
 * is N+1 by necessity, not by choice, and every one of them is bounded by
 * `deadlineMs` and individually recoverable: a project that fails is rendered
 * as a failure on that row while the rest of the rail renders normally.
 *
 * `leadsSelectedBot` is `null` for a null bot name — with no bot selected the
 * question is not asked, and answering "no" would claim the project belongs to
 * nobody.
 */
export async function readProjectRailRows(
  projects: ReadonlyArray<Pick<Project, "id">>,
  botName: string | null,
  deadlineMs = 8000,
): Promise<ProjectRailRow[]> {
  return Promise.all(
    projects.map(async (project): Promise<ProjectRailRow> => {
      const [conversations, crew] = await Promise.all([
        // Two independent reads, so a failing presence read costs the crew and
        // not the conversation count, and the other way round.
        withDeadline(projectThreads(project.id), deadlineMs).then(
          (threads) => ({ count: threads.length as number | null, error: null as string | null }),
          (error: unknown) => ({
            count: null as number | null,
            error: error instanceof Error ? error.message : String(error),
          }),
        ),
        withDeadline(listProjectAgents(project.id), deadlineMs).then(
          (members) => ({
            members: members.map((member) => member.bot_name) as string[] | null,
            error: null as string | null,
          }),
          (error: unknown) => ({
            members: null as string[] | null,
            error: error instanceof Error ? error.message : String(error),
          }),
        ),
      ]);
      return {
        projectId: project.id,
        conversationCount: conversations.count,
        conversationError: conversations.error,
        members: crew.members,
        crewError: crew.error,
        leadsSelectedBot:
          botName === null ? null : crew.members === null ? null : crew.members.includes(botName),
      };
    }),
  );
}

function withDeadline<T>(work: Promise<T>, ms: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("Timed out reading this project's conversations.")), ms);
    work.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error) => {
        clearTimeout(timer);
        reject(error);
      },
    );
  });
}

/**
 * Look one project's rail row up.
 *
 * A project id that is not in `rows` means the per-project reads have not
 * produced a row for it YET - which is the normal state on the rail's first
 * paint, before any `GET /projects/{id}/threads` has answered. It does not
 * mean a read failed.
 *
 * So the fallback carries `null` in both error fields, never a sentence. The
 * earlier version answered `"This project's facts were not read."` and
 * `"This project's team was not read."`, which asserts a failure that was never
 * attempted and painted a red alert on the rail before the operator had done
 * anything. `null` is the shape this file already uses for "the server did not
 * say", and the renderer distinguishes it from a real error.
 */
export function railRowFor(
  rows: ReadonlyArray<ProjectRailRow>,
  projectId: string,
): ProjectRailRow {
  return (
    rows.find((row) => row.projectId === projectId) ?? {
      projectId,
      conversationCount: null,
      conversationError: null,
      members: null,
      crewError: null,
      leadsSelectedBot: null,
    }
  );
}

/* ── conversation grouping ──────────────────────────────────────────────── */

export interface ConversationBucket {
  /** Project id, or `""` for the standalone group. */
  projectId: string;
  /** The project's name when the server listed it, else `null` — never the id. */
  name: string | null;
  /** `true` only when the id is in the loaded project list. */
  known: boolean;
  items: Thread[];
}

export interface ConversationGroups {
  standalone: ConversationBucket;
  projects: ConversationBucket[];
}

/**
 * Split a bot's conversations into the standalone group and one bucket per
 * project.
 *
 * Two rules carried over from the existing sidebar grouping, because dropping a
 * conversation is a worse failure than labelling it thinly:
 *
 *   - a conversation with no project is a real state, not an error, so it gets
 *     its own group rather than being folded into an arbitrary project;
 *   - a conversation whose project id is not in the loaded list is still
 *     shown, under its raw id, flagged `known: false` — an unreadable project
 *     name must not cost the user their conversation.
 *
 * The returned `projects` list is ordered by the caller's `projects` array so
 * the rail's order is the server's order, and unknown ids are appended after.
 */
export function groupConversations(threads: ReadonlyArray<Thread>, projects: ReadonlyArray<Project>): ConversationGroups {
  const nameById = new Map(projects.map((project) => [project.id, project.name] as const));
  const standalone: Thread[] = [];
  const byProject = new Map<string, Thread[]>();
  const order: string[] = [];

  for (const thread of threads) {
    const projectId = typeof thread.projectId === "string" ? thread.projectId : "";
    if (!projectId) {
      standalone.push(thread);
      continue;
    }
    if (!byProject.has(projectId)) {
      byProject.set(projectId, []);
      order.push(projectId);
    }
    byProject.get(projectId)!.push(thread);
  }

  // Server order first, then any project id the server did not list.
  const ids = [
    ...projects.map((project) => project.id).filter((id) => byProject.has(id)),
    ...order.filter((id) => !projects.some((project) => project.id === id)),
  ];

  return {
    standalone: { projectId: "", name: null, known: true, items: standalone },
    projects: ids.map((projectId) => ({
      projectId,
      name: nameById.get(projectId) ?? null,
      known: nameById.has(projectId),
      items: byProject.get(projectId) ?? [],
    })),
  };
}

/**
 * Conversations for one bot, plus what was excluded and why.
 *
 * A thread with no owner at all belongs to the Lead Agent's space, so it is
 * included in the unscoped "all bots" view and excluded from every named bot's
 * space. Dropping it from a bot's list would be right; silently lending it to
 * that bot would be a claim nobody made. `unowned` is reported so the caller
 * can say how many conversations it is not showing rather than implying the
 * number shown is the whole story.
 */
export function scopeConversations(
  threads: ReadonlyArray<Thread>,
  ownerOf: (thread: Thread) => string | null,
  botName: string | null,
): { threads: Thread[]; unowned: number } {
  let unowned = 0;
  const scoped: Thread[] = [];
  for (const thread of threads) {
    const owner = ownerOf(thread);
    if (owner === null) {
      unowned += 1;
      if (botName === null) scoped.push(thread);
      continue;
    }
    if (botName === null || owner === botName) scoped.push(thread);
  }
  return { threads: scoped, unowned };
}

/** Sort newest first by the server's own `updated_at`; undated rows sort last. */
export function byRecency<T extends { updated_at?: string }>(rows: ReadonlyArray<T>): T[] {
  return [...rows].sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""));
}

/* ── project conversation rows ──────────────────────────────────────────── */

export interface ProjectConversationRow {
  threadId: string;
  /** The server's display name, or `null` when it sent none — never the id. */
  displayName: string | null;
  updatedAt: string;
  /** `"2h ago"`, or `null` when no timestamp was reported. */
  relative: string | null;
  /** Absolute stamp for the tooltip, or `null`. */
  absolute: string | null;
  /** Human sentence, always a sentence — never a silent gap. */
  whenText: string;
}

/**
 * Project conversations as rows with honest relative times.
 *
 * `ProjectThreadResponse.created_at`/`updated_at` default to `""` on the server
 * (`projects.py:91-95`), so an undated row is common rather than exceptional.
 * Such a row says "time not reported"; it is never given `new Date("")`, which
 * is the epoch and would claim the conversation happened in 1970.
 */
export function projectConversationRows(
  threads: ReadonlyArray<ProjectThread>,
  now: number = Date.now(),
): ProjectConversationRow[] {
  return byRecency(threads).map((thread) => {
    const relative = relTime(thread.updated_at || thread.created_at, now);
    const absolute = absoluteStamp(thread.updated_at || thread.created_at);
    return {
      threadId: thread.thread_id,
      displayName: thread.display_name && thread.display_name.trim() ? thread.display_name : null,
      updatedAt: thread.updated_at || thread.created_at || "",
      relative,
      absolute,
      whenText: relative ?? "time not reported",
    };
  });
}

/* ── the one-line "where am I" sentence ─────────────────────────────────── */

export interface ContextSentence {
  text: string;
  /** Which pieces were actually known; drives what the sentence can claim. */
  botKnown: boolean;
  projectKnown: boolean;
  conversationKnown: boolean;
}

/**
 * One line naming the bot, the project and the conversation, assembled only
 * from parts that are actually known.
 *
 * Every branch is written out rather than joined from optional fragments,
 * because a fragment-dropping join produces exactly the false claim this
 * module exists to prevent: dropping "in project X" reads as "there is no
 * project", and dropping the bot name reads as "you are talking to nobody".
 */
export function contextSentence(input: {
  botName: string | null;
  projectName: string | null;
  conversationTitle: string | null;
}): ContextSentence {
  const bot = input.botName && input.botName.trim() ? input.botName : null;
  const project = input.projectName && input.projectName.trim() ? input.projectName : null;
  const conversation = input.conversationTitle && input.conversationTitle.trim() ? input.conversationTitle : null;

  const who = bot
    ? `You are talking to ${bot}.`
    : "You are talking to the Lead Agent, which auto-routes.";
  const where = project ? ` This conversation is in project ${project}.` : " This conversation is not in any project.";
  const what = conversation
    ? ` You are in ${conversation}.`
    : " This is a new conversation with nothing sent yet.";

  return {
    text: `${who}${where}${what}`,
    botKnown: bot !== null,
    projectKnown: project !== null,
    conversationKnown: conversation !== null,
  };
}

/** The title of a thread, or `null` when the Gateway never named it. */
export function conversationTitle(thread: Thread | null | undefined): string | null {
  if (!thread) return null;
  const title = threadTitle(thread as unknown as Record<string, unknown>);
  if (!title || title === "Untitled") return null;
  return title;
}

/* ── sources the Gateway does not expose ────────────────────────────────── */

/**
 * A panel whose source route does not exist.
 *
 * The reference design showed `Files 5` and `Tasks 2` on a project. Neither
 * claim has a source:
 *
 *   - there is no `GET /projects/{id}/files`. The only file listing in the
 *     Gateway is `GET /threads/{thread_id}/uploads/list`
 *     (`routers/uploads.py:491`) and the artifact route is
 *     `GET /threads/{thread_id}/artifacts/{path}`
 *     (`routers/artifacts.py:371`) — both are **conversation**-scoped. A
 *     project-scoped file count would have to be summed across conversations,
 *     and a sum over partially-failed reads is exactly the "looks complete but
 *     is not" number `lib/bots.ts` already refuses to produce.
 *   - there is no `GET /projects/{id}/tasks` either. `GET /projects/{id}/state`
 *     does report `active_tasks` / `blocked_tasks` / `completed_tasks` /
 *     `failed_tasks`, so the *counts* are real and are rendered; what does not
 *     exist is a task **list** to enumerate.
 *
 * So the panel is built and says so, rather than rendering an empty list that
 * would read as "this project has no files". The strings live here so a test can
 * assert the disclosure is present in the rendered markup.
 */
export interface UnavailableSource {
  /** What the panel would have been called. */
  what: string;
  /** Why the panel cannot be filled, in the user's terms. */
  reason: string;
  /** The route that would have had to exist for the claim to be honest. */
  missingRoute: string;
  /** The real, differently-scoped route that does exist, when one does. */
  actualRoute?: string;
}

export const UNAVAILABLE_SOURCES = {
  projectFiles: {
    what: "Project files",
    reason:
      "The Gateway lists files per conversation, not per project, so there is no honest project-level file count. Nothing is shown here rather than a number that would be a sum over reads that may have failed.",
    missingRoute: "GET /api/projects/{id}/files",
    actualRoute: "GET /api/threads/{thread_id}/uploads/list",
  } satisfies UnavailableSource,
  projectTaskList: {
    what: "The project task list",
    reason:
      "There is no project task-list route. The counts below come from the project state digest, which is a different measurement from a list of tasks.",
    missingRoute: "GET /api/projects/{id}/tasks",
  } satisfies UnavailableSource,
} as const;

/* ── starter actions ────────────────────────────────────────────────────── */

export interface StarterAction {
  id: string;
  label: string;
  /** HTTP method this action issues. */
  method: "GET" | "POST";
  /** Path as the client sends it, relative to the Gateway base (`/api`). */
  path: string;
  /** The exact method + path as a human sees it in the API. Shown in the tooltip. */
  route: string;
  /**
   * The literal request body. A starter action is a real request, so this is
   * the request the button sends, not a label pretending to be one.
   */
  body: Record<string, unknown>;
  /** Why this action is offered, in one clause. */
  hint: string;
}

/**
 * The starter actions offered on the empty state.
 *
 * Every one is a real route with a real body:
 *
 *   - `GET  /api/agents`                    read the Gateway's agent roster
 *   - `GET  /api/skills`                    read the installed skills
 *   - `GET  /api/models`                    read the model catalog
 *   - `POST /api/projects/{id}/phase`       record a phase on this project
 *
 * There is no "start a run" action here: the run boundary takes a real prompt,
 * and a button that invented one would be a request nobody made. The phase
 * action is the only mutation, and it is only offered when a project is
 * actually selected — it is meaningless without a project id.
 */
export function starterActions(projectId: string | null): StarterAction[] {
  const actions: StarterAction[] = [
    {
      id: "read-agents",
      label: "Read the agent roster",
      method: "GET",
      path: "/agents",
      route: "GET /api/agents",
      body: {},
      hint: "Lists the agents this Gateway can reach. No state is changed.",
    },
    {
      id: "read-skills",
      label: "List installed skills",
      method: "GET",
      path: "/skills",
      route: "GET /api/skills",
      body: {},
      hint: "Lists the skills installed on this installation. No state is changed.",
    },
    {
      id: "read-models",
      label: "Show the model catalog",
      method: "GET",
      path: "/models",
      route: "GET /api/models",
      body: {},
      hint: "Lists the models the Gateway can currently build. No state is changed.",
    },
  ];
  if (projectId) {
    actions.push({
      id: "set-phase",
      label: "Record a project phase",
      method: "POST",
      path: `/projects/${encodeURIComponent(projectId)}/phase`,
      route: `POST /api/projects/${encodeURIComponent(projectId)}/phase`,
      body: { phase: "planning" },
      hint: "Writes phase=planning on this project through the real route. Nothing else changes.",
    });
  }
  return actions;
}

/* ── section availability ───────────────────────────────────────────────── */

export type SectionAvailability = "loading" | "ok" | "unavailable" | "error";

export interface SectionState<T> {
  availability: SectionAvailability;
  data: T | null;
  /** The server's own reason, when `availability` is `error`. */
  error: string | null;
}

/**
 * Fold a read into a section state.
 *
 * `unavailable` is distinct from `ok` on purpose: a section whose source the
 * Gateway does not expose is not an empty section, and drawing it as one tells
 * the user "this project has no files", which is a false claim about a project.
 */
export function sectionState<T>(
  state: "loading" | "ok" | "unavailable" | "error",
  data: T | null = null,
  error: string | null = null,
): SectionState<T> {
  return { availability: state, data, error };
}

/* ── quick actions ──────────────────────────────────────────────────────── */

/**
 * The quick actions for a project, each naming the real route it opens.
 *
 * `POST /projects/{id}/phase` is included only when a phase was actually
 * chosen: sending `phase: ""` is a 422, so an action that always fires it
 * would be a control that cannot succeed.
 */
export interface QuickAction {
  id: string;
  label: string;
  route: string;
  method: "GET" | "POST";
  enabled: boolean;
  /** Why it is disabled, in words. */
  reason: string | null;
}

export function projectQuickActions(projectId: string | null, phase: string | null): QuickAction[] {
  const noProject: string = "Select a project first — this action is scoped to one.";
  const hasProject = Boolean(projectId);
  const hasPhase = Boolean(phase && phase.trim());
  return [
    {
      id: "conversations",
      label: "Read this project's conversations",
      route: hasProject ? `GET /api/projects/${encodeURIComponent(projectId!)}/threads` : "GET /api/projects/{id}/threads",
      method: "GET",
      enabled: hasProject,
      reason: hasProject ? null : noProject,
    },
    {
      id: "crew",
      label: "Read the project crew",
      route: hasProject ? `GET /api/projects/${encodeURIComponent(projectId!)}/crew` : "GET /api/projects/{id}/crew",
      method: "GET",
      enabled: hasProject,
      reason: hasProject ? null : noProject,
    },
    {
      id: "approvals",
      label: "Read open approvals",
      route: hasProject ? `GET /api/projects/${encodeURIComponent(projectId!)}/approvals` : "GET /api/projects/{id}/approvals",
      method: "GET",
      enabled: hasProject,
      reason: hasProject ? null : noProject,
    },
    {
      id: "set-phase",
      label: "Record the current phase",
      route: hasProject ? `POST /api/projects/${encodeURIComponent(projectId!)}/phase` : "POST /api/projects/{id}/phase",
      method: "POST",
      enabled: hasProject && hasPhase,
      reason: !hasProject ? noProject : !hasPhase ? "The project has no phase recorded, and the route rejects an empty one." : null,
    },
  ];
}

/* ── project status ─────────────────────────────────────────────────────── */

/**
 * A project's own `status` string, read as a string.
 *
 * `toProject` in `lib/projects.ts` substitutes the server's own default
 * (`"active"`) when the field is absent, so this cannot distinguish "the server
 * said active" from "the server said nothing and the client filled in active".
 * That is why the UI shows the literal word and does not colour it: a colour
 * would assert a liveness verdict the read did not support.
 */
export function projectStatusText(status: string | null | undefined): string {
  const text = typeof status === "string" ? status.trim() : "";
  return text || "status not reported";
}

/**
 * Collapse a bot roster to one row per bot.
 *
 * The roster can legitimately arrive with the same bot more than once: it is
 * assembled from more than one source (the bot registry and a project/crew
 * membership read), and a bot that is both a registry entry and a project
 * member appears twice. Rendered as-is that is a visible defect - the same
 * specialist listed twice, with two rows both claiming the selection, and
 * React logging a duplicate-key warning because the rows key on `name`.
 *
 * Identity is `name`, because that is what the selection compares against and
 * what the row keys on; two entries with the same name are the same bot as far
 * as this UI is concerned.
 *
 * FIRST WINS, deliberately. A later duplicate is not a fresher read of the same
 * bot in any way this UI can verify, and preferring the last would let a
 * membership projection silently overwrite the registry's own record. Keeping
 * the first also keeps the render order stable, so a re-read that reorders the
 * array cannot make rows jump.
 *
 * A row with no usable name is DROPPED rather than rendered: a nameless entry
 * cannot be selected, cannot be keyed, and would render as a blank row that
 * looks like a loading artefact. Dropping is a smaller lie than showing an
 * unselectable empty row.
 */
export function dedupeBots<T extends { name?: string | null }>(bots: readonly T[]): T[] {
  const seen = new Set<string>();
  const out: T[] = [];
  for (const bot of bots) {
    const name = typeof bot?.name === "string" ? bot.name.trim() : "";
    if (!name || seen.has(name)) continue;
    seen.add(name);
    out.push(bot);
  }
  return out;
}
