/**
 * Bot detail reader (`/bots/[name]`).
 *
 * This page has one job: answer *everything the Gateway knows about one bot*
 * from the operator's seat — profile, SOUL, capabilities, tools, model config,
 * project membership, group rooms, company employment, working state,
 * kanban tasks, chat history and organisation events — and, wherever the full
 * record lives on another surface, point at it instead of duplicating it.
 *
 * Why a separate module and not an extension of `lib/bots.ts`: `lib/bots.ts`
 * owns the fleet roster read (`GET /api/bots`) and stays cheap. This one fans
 * out to a dozen per-bot routes, and a page should be allowed to fail
 * per-section without the roster client dragging it down.
 *
 * The honesty contract (from `frontend/AGENTS.md`) is enforced here rather than
 * in JSX, so it cannot be bypassed by a panel that forgets:
 *
 *   - **absent is `null`**, never `0` / `""` / `[]` / `false`. A list the
 *     Gateway did not send is `null` — distinct from a list it sent empty.
 *   - **a failed read rejects with the server's own reason**, so an empty
 *     section always says *why* it is empty.
 *   - **sections are independent** — `Promise.allSettled`, never `Promise.all`.
 *     One corrupt group store must not blank a healthy project list.
 *   - **unknown enum strings survive verbatim**.
 */

import { apiFetch } from "./api-client";
import { normalizeBot } from "./bots";
import {
  fetchPauseState,
  normalizeHealthOverview,
  normalizeWorkReport,
  workRunsFor,
  type FleetHealthOverview,
  type BotWorkReport,
  type BotWorkRun,
  type PauseState,
} from "./bot-working-status";
import { listKanbanTasks, type KanbanTask } from "./kanban";
import { normalizeCapabilityScope, type CapabilityScope } from "./bot-capabilities";
import type { BotProfile } from "@/types/bots";

export type DetailState<T> =
  | { state: "ok"; value: T }
  /** The read was refused or failed; `reason` is the server's own words. */
  | { state: "error"; reason: string };

/** A per-section read the page can render independently of the others. */
export interface BotDetailBundle {
  profile: DetailState<BotProfile>;
  performance: DetailState<BotPerformance>;
  inbox: DetailState<BotInbox>;
  chat: DetailState<BotChatHistory>;
  fleetHealth: DetailState<FleetHealthOverview>;
  organization: DetailState<BotOrgPosition>;
  projects: DetailState<BotProjects>;
  groups: DetailState<BotGroups>;
  companies: DetailState<BotCompanies>;
  working: DetailState<BotWorkReport>;
  events: DetailState<BotOrgEvents>;
  capabilities: DetailState<CapabilityScope>;
  pause: DetailState<PauseState>;
}

export interface BotInboxMessage {
  id?: string;
  sender?: string;
  body?: string;
  created_at?: number | string;
  read?: boolean;
  [key: string]: unknown;
}

export interface BotInbox {
  messages: BotInboxMessage[];
  /**
   * `null` when the server sent no counter. An absent `unread_count` is not a
   * measured zero, so it must not render as "0 unread".
   */
  unread_count: number | null;
}

/** One membership row from `GET /api/bots/{name}/projects`. */
export interface BotProjectRow {
  id: string;
  name: string;
  status: string;
  /** The bot's `role_in_project`, verbatim — `""` when the server sent none. */
  role: string;
  /** The membership's own status (not the project's), verbatim. */
  member_status: string;
  updated_at: string | null;
}

/** A project (or room, or company) whose read failed, with the server's reason. */
export interface BotUnreadableRow {
  id: string;
  name: string;
  reason: string;
}

export interface BotProjects {
  bot: string;
  /** `null` when the Gateway sent no project list at all. */
  projects: BotProjectRow[] | null;
  unreadable: BotUnreadableRow[] | null;
  /**
   * Where cognitive memory is scoped. The Gateway answers `project` — there is
   * no per-bot memory store — so the page says that rather than showing an
   * empty per-bot memory block that reads as "this bot has no memory".
   */
  memory_scoped: string | null;
  memory_note: string | null;
}

/** One group-room membership row from `GET /api/bots/{name}/groups`. */
export interface BotGroupRoom {
  name: string;
  room_id: string;
  project_id: string | null;
  /** Room mode, verbatim. */
  mode: string;
  /** Room lifecycle, verbatim. */
  lifecycle: string;
  /**
   * Every roster bucket the bot appears in (`direct` / `rule_matched` /
   * `inherited` / `excluded` / `expired`). Membership is a live projection, so
   * an inherited member is reported as inherited — never as somebody who was
   * added to this room.
   */
  origins: string[];
  /** Whether the bot can actually take part (excluded/expired still list). */
  effective: boolean;
  /** Direct members only — travels beside `effective_count`, never instead. */
  direct_count: number | null;
  effective_count: number | null;
  parent_count: number | null;
  /** Root-first visibility-parent chain with the room appended. */
  path: string[];
}

export interface BotGroups {
  bot: string;
  /** `null` when the Gateway sent no room list at all. */
  rooms: BotGroupRoom[] | null;
  unreadable: BotUnreadableRow[] | null;
  /** How many rooms were examined, `null` when the server omitted it. */
  rooms_checked: number | null;
  note: string | null;
}

/** One employment row from `GET /api/bots/{name}/companies`. */
export interface BotCompanyRow {
  company_id: string;
  name: string;
  archetype: string;
  /** Company state, verbatim. */
  state: string;
  created_at: string | null;
  /** The full employment record, mapped verbatim. */
  employment: Record<string, unknown>;
  /** `null` when the employment's `role_id` dangles — never a stub. */
  role: Record<string, unknown> | null;
  /** `null` when the employment's `unit_id` dangles — never a stub. */
  unit: Record<string, unknown> | null;
}

export interface BotCompanies {
  bot: string;
  /** `null` when the Gateway sent no employment list at all. */
  companies: BotCompanyRow[] | null;
  companies_checked: number | null;
  note: string | null;
}

/** The canonical DM thread plus its inbox summary, from `GET .../chat`. */
export interface BotChatHistory {
  bot_name: string | null;
  /** `null` when the server omitted the canonical id. */
  canonical_thread_id: string | null;
  unread_count: number | null;
  /** `null` when the Gateway sent no message list at all. */
  recent: BotInboxMessage[] | null;
}

/** One organisational event from `GET /api/bots/events`. */
export interface BotOrgEvent {
  event_type?: string;
  actor?: string;
  target?: string;
  created_at?: string | number;
  [key: string]: unknown;
}

export interface BotOrgEvents {
  /** `null` when the Gateway sent no event list at all. */
  events: BotOrgEvent[] | null;
  /** The server's own count of matching rows, `null` when it omitted it. */
  count: number | null;
  /** `true` when `count === events.length` — the two are separate facts. */
  complete: boolean | null;
}

/**
 * `GET /api/bots/{name}/performance`, mapped on the route's real keys.
 *
 * The key names here are load-bearing. `alpha.bots.performance` returns
 * `completed_runs` / `failed_runs` / `avg_duration_seconds` — *not*
 * `completed` / `failed` / `avg_duration_sec` — so a mapper using the plausible
 * names would render four permanently "not reported" rows over a payload that
 * carried every value. Wrong-key reads fail closed into a confident claim the
 * server sent nothing, which is the opposite of this page's job.
 *
 * `basis` is rendered beside the numbers on purpose: the route reports
 * `total_runs: 0` and `avg_duration_seconds: 0.0` for a bot that has never
 * run, with `basis: "no recorded runs - unverified"`. The zero is real, and
 * only the server's own sentence keeps it from reading as a measured idle run
 * history.
 */
export interface BotPerformance {
  bot_name: string | null;
  display_name: string | null;
  role: string | null;
  department: string | null;
  /** `null` while unmeasured — never coerced to `0` or to a perfect `1.0`. */
  reputation_score: number | null;
  /** The server's own tier word, verbatim; `null` when there is no score. */
  reputation_tier: string | null;
  success_rate_percent: number | null;
  basis: string | null;
  total_runs: number | null;
  completed_runs: number | null;
  failed_runs: number | null;
  avg_duration_seconds: number | null;
}

/** One flat graph node from `GET /api/bots/organization-chart`. */
export interface BotOrgNode {
  id: string;
  label: string;
  role: string;
  department: string;
  status: string;
}

/**
 * This bot's place in `GET /api/bots/organization-chart`.
 *
 * The chart's top level is `{departments, tree, graph, total_bots}` — there is
 * no top-level `department` / `reports_to` key, so reading those would render
 * two permanently-absent rows over a chart that holds the answer nested in a
 * node. The one fact here that appears nowhere else is **who reports to this
 * bot**: `reports_to` is on the profile, but its subordinates are not.
 */
export interface BotOrgPosition {
  /** `null` when the chart carries no node for this bot (archived bots are excluded). */
  node: BotOrgNode | null;
  /** Whether the bot sits at the top of `tree`; `null` when the tree held no entry for it. */
  is_root: boolean | null;
  /** Names reporting directly to this bot; `null` when the tree did not say. */
  direct_reports: string[] | null;
  /** Bots on the installation the chart counted; `null` when omitted. */
  total_bots: number | null;
}

const asString = (v: unknown): string | null => (typeof v === "string" ? v : null);
const asNumber = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const asBool = (v: unknown): boolean | null => (typeof v === "boolean" ? v : null);

/**
 * Read a list honestly: `null` when the key is absent or not an array, so
 * "the Gateway sent no list" stays distinguishable from "the list is empty".
 */
const asList = <T>(v: unknown): T[] | null => (Array.isArray(v) ? (v as T[]) : null);

/** Coerce one raw row into an object, or `null` when it is not one. */
const asRow = (v: unknown): Record<string, unknown> | null =>
  v !== null && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;

function reasonOf(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return String(error);
}

/**
 * Load one bot's full detail, per-section.
 *
 * Every section is settled independently with `Promise.allSettled`: a failing
 * inbox read must not blank the performance panel. Each rejection is converted
 * into an `error` state carrying the server's reason so the page can show *why*
 * a section is empty instead of implying the bot has nothing there.
 *
 * The two fleet-wide reads (`/bots/working`, `/bots/kill-switch`) are issued
 * alongside the per-bot ones rather than folded into them: they answer a
 * different question and 503 on a `database.backend: memory` deployment, where
 * the per-bot reads still work.
 */
export async function fetchBotDetail(name: string): Promise<BotDetailBundle> {
  const key = encodeURIComponent(name);

  // `apiFetch` resolves a `Response`, so each body is parsed here. A non-2xx
  // already rejected inside the client with the server's reason, so `.json()`
  // here only runs on a real success envelope.
  const json = (path: string) => apiFetch(path).then((res) => res.json() as Promise<Record<string, unknown>>);

  const [profile, performance, inbox, chat, fleetHealth, organization, projects, groups, companies, working, events, capabilities, pause] =
    await Promise.allSettled([
      json(`/bots/${key}`).then((raw) => normalizeBot(raw)),
      json(`/bots/${key}/performance`).then(normalizePerformance),
      json(`/bots/${key}/inbox`).then(normalizeInbox),
      json(`/bots/${key}/chat`).then(normalizeChat),
      json("/bots/health/overview").then(normalizeHealthOverview),
      json("/bots/organization-chart").then((raw) => normalizeOrgPosition(raw, name)),
      json(`/bots/${key}/projects`).then(normalizeProjects),
      json(`/bots/${key}/groups`).then(normalizeGroups),
      json(`/bots/${key}/companies`).then(normalizeCompanies),
      json("/bots/working").then(normalizeWorkReport),
      json(`/bots/events?actor=${key}&limit=50`).then(normalizeEvents),
      json(`/bots/${key}/capabilities`).then(normalizeCapabilityScope),
      fetchPauseState().then((result) => {
        // `ReadResult` and `DetailState` carry the same two states under
        // different names; converting here keeps one vocabulary on this page
        // and lets `fetchPauseState`'s *resolve-with-a-refusal* contract
        // (`ok: false`) reach the same error branch a rejection would.
        if (!result.ok) throw new Error(result.error);
        return result.value;
      }),
    ]);

  const settle = <T,>(result: PromiseSettledResult<T>): DetailState<T> =>
    result.status === "fulfilled"
      ? { state: "ok", value: result.value }
      : { state: "error", reason: reasonOf(result.reason) };

  return {
    profile: settle(profile),
    performance: settle(performance),
    inbox: settle(inbox),
    chat: settle(chat),
    fleetHealth: settle(fleetHealth),
    organization: settle(organization),
    projects: settle(projects),
    groups: settle(groups),
    companies: settle(companies),
    working: settle(working),
    events: settle(events),
    capabilities: settle(capabilities),
    pause: settle(pause),
  };
}

/** Map `GET /api/bots/{name}/inbox` onto a typed shape (exported for tests). */
export function normalizeInbox(raw: Record<string, unknown>): BotInbox {
  const messages = Array.isArray(raw.messages) ? (raw.messages as BotInboxMessage[]) : [];
  return {
    messages,
    unread_count: asNumber(raw.unread_count),
  };
}

/** Map `GET /api/bots/{name}/chat` onto a typed shape (exported for tests). */
export function normalizeChat(raw: Record<string, unknown>): BotChatHistory {
  return {
    bot_name: asString(raw.bot_name),
    canonical_thread_id: asString(raw.canonical_thread_id),
    unread_count: asNumber(raw.unread_count),
    recent: asList<BotInboxMessage>(raw.recent),
  };
}

function normalizeUnreadable(raw: unknown): BotUnreadableRow[] | null {
  const list = asList<unknown>(raw);
  if (list === null) return null;
  return list.map((row) => {
    const obj = asRow(row) ?? {};
    return {
      id: asString(obj.id) ?? "",
      name: asString(obj.name) ?? "",
      reason: asString(obj.reason) ?? "",
    };
  });
}

/** Map `GET /api/bots/{name}/projects` (exported for tests). */
export function normalizeProjects(raw: Record<string, unknown>): BotProjects {
  const list = asList<unknown>(raw.projects);
  return {
    bot: asString(raw.bot) ?? "",
    projects:
      list === null
        ? null
        : list.map((row) => {
            const obj = asRow(row) ?? {};
            return {
              id: asString(obj.id) ?? "",
              name: asString(obj.name) ?? "",
              status: asString(obj.status) ?? "",
              role: asString(obj.role) ?? "",
              member_status: asString(obj.member_status) ?? "",
              updated_at: asString(obj.updated_at),
            };
          }),
    unreadable: normalizeUnreadable(raw.unreadable),
    memory_scoped: asString(raw.memory_scoped),
    memory_note: asString(raw.memory_note),
  };
}

/** Map `GET /api/bots/{name}/groups` (exported for tests). */
export function normalizeGroups(raw: Record<string, unknown>): BotGroups {
  const list = asList<unknown>(raw.rooms);
  return {
    bot: asString(raw.bot) ?? "",
    rooms:
      list === null
        ? null
        : list.map((row) => {
            const obj = asRow(row) ?? {};
            return {
              name: asString(obj.name) ?? "",
              room_id: asString(obj.room_id) ?? "",
              project_id: asString(obj.project_id),
              mode: asString(obj.mode) ?? "",
              lifecycle: asString(obj.lifecycle) ?? "",
              origins: asList<string>(obj.origins) ?? [],
              effective: asBool(obj.effective) ?? false,
              direct_count: asNumber(obj.direct_count),
              effective_count: asNumber(obj.effective_count),
              parent_count: asNumber(obj.parent_count),
              path: asList<string>(obj.path) ?? [],
            };
          }),
    unreadable: normalizeUnreadable(raw.unreadable),
    rooms_checked: asNumber(raw.rooms_checked),
    note: asString(raw.note),
  };
}

/** Map `GET /api/bots/{name}/companies` (exported for tests). */
export function normalizeCompanies(raw: Record<string, unknown>): BotCompanies {
  const list = asList<unknown>(raw.companies);
  return {
    bot: asString(raw.bot) ?? "",
    companies:
      list === null
        ? null
        : list.map((row) => {
            const obj = asRow(row) ?? {};
            return {
              company_id: asString(obj.company_id) ?? "",
              name: asString(obj.name) ?? "",
              archetype: asString(obj.archetype) ?? "",
              state: asString(obj.state) ?? "",
              created_at: asString(obj.created_at),
              employment: asRow(obj.employment) ?? {},
              role: asRow(obj.role),
              unit: asRow(obj.unit),
            };
          }),
    companies_checked: asNumber(raw.companies_checked),
    note: asString(raw.note),
  };
}

/** Map `GET /api/bots/events` (exported for tests). */
export function normalizeEvents(raw: Record<string, unknown>): BotOrgEvents {
  const list = asList<unknown>(raw.events);
  const count = asNumber(raw.count);
  return {
    events: list === null ? null : (list as BotOrgEvent[]),
    count,
    // The server's `count` is its own `len(events)`, so a mismatch is real
    // information (a newer Gateway may count what it filtered) rather than
    // something to paper over.
    complete: list === null || count === null ? null : count === list.length,
  };
}

/** Map `GET /api/bots/{name}/performance` (exported for tests). */
export function normalizePerformance(raw: Record<string, unknown>): BotPerformance {
  return {
    bot_name: asString(raw.bot_name),
    display_name: asString(raw.display_name),
    role: asString(raw.role),
    department: asString(raw.department),
    reputation_score: asNumber(raw.reputation_score),
    reputation_tier: asString(raw.reputation_tier),
    success_rate_percent: asNumber(raw.success_rate_percent),
    basis: asString(raw.basis),
    total_runs: asNumber(raw.total_runs),
    completed_runs: asNumber(raw.completed_runs),
    failed_runs: asNumber(raw.failed_runs),
    avg_duration_seconds: asNumber(raw.avg_duration_seconds),
  };
}

/** Depth-first search of the chart's `tree` for one bot, returning its node. */
function findTreeEntry(rows: unknown, key: string): Record<string, unknown> | null {
  if (!Array.isArray(rows)) return null;
  for (const row of rows) {
    const node = asRow(row);
    if (node === null) continue;
    if (asString(node.name)?.trim().toLowerCase() === key) return node;
    const nested = findTreeEntry(node.subordinates, key);
    if (nested !== null) return nested;
  }
  return null;
}

/** Map `GET /api/bots/organization-chart` onto this bot's position (exported for tests). */
export function normalizeOrgPosition(raw: Record<string, unknown>, name: string): BotOrgPosition {
  const key = name.trim().toLowerCase();

  const graph = asRow(raw.graph);
  const nodes = graph === null ? null : asList<unknown>(graph.nodes);
  const graphNode =
    nodes === null
      ? null
      : nodes.map(asRow).find((n) => n !== null && asString(n?.id)?.trim().toLowerCase() === key) ?? null;

  // `tree` is root-first, so reaching the top-level array at all is what makes
  // the bot a root; finding it only after descending says the opposite.
  const top = asList<unknown>(raw.tree);
  const topNode =
    top === null ? null : top.map(asRow).find((n) => n !== null && asString(n?.name)?.trim().toLowerCase() === key) ?? null;
  const treeEntry = topNode ?? findTreeEntry(raw.tree, key);

  const subs = treeEntry === null ? null : asList<unknown>(treeEntry.subordinates);
  const directReports =
    subs === null
      ? null
      : subs
          .map((row) => asString(asRow(row)?.name))
          .filter((n): n is string => n !== null);

  return {
    node:
      graphNode === null
        ? null
        : {
            id: asString(graphNode.id) ?? "",
            label: asString(graphNode.label) ?? "",
            role: asString(graphNode.role) ?? "",
            department: asString(graphNode.department) ?? "",
            status: asString(graphNode.status) ?? "",
          },
    // A bot the chart omits entirely is neither a root nor a subordinate; both
    // readings would be guesses about a chart that never mentioned it.
    is_root: treeEntry === null ? null : topNode !== null,
    direct_reports: directReports,
    total_bots: asNumber(raw.total_bots),
  };
}

/**
 * The kanban tasks assigned to this bot, filtered client-side.
 *
 * The board route is fleet-wide, so this is a *filter*, not a count: an
 * assignee string is compared exactly (case-insensitively) and nothing is
 * inferred from a missing `assignee`. A task the server left unassigned is
 * not this bot's task.
 */
export function kanbanTasksFor(tasks: KanbanTask[], botName: string): KanbanTask[] {
  const key = botName.trim().toLowerCase();
  if (!key) return [];
  return tasks.filter((task) => String(task.assignee ?? "").trim().toLowerCase() === key);
}

/**
 * The runs currently attributed to this bot, or `null` when the run-store read
 * could not be taken at all.
 *
 * `reported: false` is the Gateway's own refusal (`database.backend: memory`
 * has no run history), which is a different fact from "no runs", so it is
 * preserved rather than folded into an empty list.
 */
export function workRunsForBot(report: BotWorkReport | null, botName: string): BotWorkRun[] {
  if (report === null || !report.reported) return [];
  return workRunsFor(report, botName);
}

/** Load the fleet board once so the page can filter it per bot. */
export async function fetchKanbanTasks(): Promise<KanbanTask[]> {
  return listKanbanTasks();
}

/**
 * Render an unknown enum verbatim.
 *
 * A `status` this client has never heard of is still a real answer from the
 * server, so it is passed through untouched rather than coerced to a known
 * value — `frontend/AGENTS.md` requires unknown enum values to be displayed
 * rather than silently snapped.
 */
export function enumOrUnknown(value: unknown, known: readonly string[]): { value: string; known: boolean } {
  const text = typeof value === "string" ? value : null;
  if (text === null) return { value: "not reported", known: false };
  return { value: text, known: known.includes(text) };
}
