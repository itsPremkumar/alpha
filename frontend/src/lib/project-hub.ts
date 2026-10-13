/**
 * The project hub's own reads: the rooms a project owns, the company it sits
 * under, the skills its crew carries and the board rows that belong to it.
 *
 * Every one of these is a **join over reads that already exist**. The Gateway
 * has no "project hub" endpoint and this module deliberately does not invent
 * one: a hub assembled from a second projection would drift from the very
 * surfaces it claims to summarise, so each panel names its own source read.
 *
 * Three shapes are load-bearing across all of them:
 *
 * - **A failed read is never an empty answer.** Every loader that can fail
 *   returns the reason (or names the rows it could not read) beside the data
 *   it did get, because "no rooms" and "the room service did not answer" look
 *   identical once you drop the failure.
 * - **Counts travel with their basis.** `count` is `null` when it was not
 *   reported, never `0`.
 * - **A join claims only what it matched.** A board card with no `projectId`
 *   is not this project's work, and a skill is attributed to the agent that
 *   actually carries it.
 */
import { fetchBot } from "./bots";
import { getCompany, listCompanies, ProjectLink } from "./company";
import { errMsg, get } from "./http";
import { listKanbanTasks } from "./kanban";
import { Card, COLUMNS, loadCards, mergeServerCards } from "./kanban-board";

// ---------------------------------------------------------------- rooms

/** One room bound to a project: the crew channel, or a subgroup of it. */
export interface ProjectRoomRef {
  name: string;
  /** The server's own lifecycle string; `unknown` when it reported none. */
  status: string;
  memberCount: number;
}

export interface ProjectRoomsView {
  /**
   * Every room the group service has bound to this project.
   *
   * `null` only where the Gateway did not report a count at all — it is never
   * derived from `rooms.length`, which would make an unreadable list claim
   * "zero rooms" instead of "not reported".
   */
  count: number | null;
  rooms: ProjectRoomRef[];
}

/**
 * Read every message room bound to one project.
 *
 * `GET /groups/by-project/{project_id}` is the server's own answer to "which
 * rooms does this project own" — including a `project-{id}` crew channel that
 * the crew service creates at the second agent, and any subgroup created with
 * this project id. Nothing here synthesises the room name: a project whose
 * room was renamed or parked still resolves.
 */
export async function projectRooms(projectId: string): Promise<ProjectRoomsView> {
  const body = await get<Record<string, unknown>>(
    `/groups/by-project/${encodeURIComponent(projectId)}`,
  );
  const rows = Array.isArray(body?.rooms) ? (body.rooms as unknown[]) : null;
  if (!rows) throw new Error("The server returned an unreadable room list for this project.");
  return {
    rooms: rows.map((row) => {
      const rec = (row ?? {}) as Record<string, unknown>;
      const members = Array.isArray(rec.members) ? rec.members : [];
      return {
        name: String(rec.name ?? ""),
        status: String(rec.status ?? "unknown"),
        memberCount: members.length,
      };
    }),
    // A count the Gateway skipped is `null`, not a zero read off the array.
    count: typeof body.count === "number" && Number.isFinite(body.count) ? body.count : null,
  };
}

// ---------------------------------------------------------------- company

/** The company a project sits under, from the company's own portfolio row. */
export interface ProjectCompanyRef {
  company_id: string;
  name: string;
}

/** Companies scanned per attribution read. A scan is a fan-out of one read per company. */
const MAX_COMPANIES_SCANNED = 25;

/**
 * Index company links by project id.
 *
 * The link is stored on the company (`Company.projects[]`) and the project row
 * carries no `company_id`, so attribution is this reverse lookup and nothing
 * else. Companies the Gateway would not let us read are named rather than
 * dropped: an unreadable company is exactly the case where "this project is in
 * no company" would be a lie.
 */
export async function projectCompanyIndex(): Promise<{
  byProject: Map<string, ProjectCompanyRef[]>;
  /** Company ids whose own read failed — the attribution is partial, not clean. */
  unreadable: string[];
}> {
  const companies = await listCompanies();
  const scanned = companies.slice(0, MAX_COMPANIES_SCANNED);
  const settled = await Promise.allSettled(scanned.map((c) => getCompany(c.company_id)));
  const byProject = new Map<string, ProjectCompanyRef[]>();
  const unreadable: string[] = [];
  settled.forEach((result, index) => {
    const company = scanned[index];
    if (result.status === "rejected") {
      unreadable.push(company.company_id);
      return;
    }
    const links: ProjectLink[] = Array.isArray(result.value.company?.projects)
      ? result.value.company.projects
      : [];
    for (const link of links) {
      if (!link?.project_id) continue;
      const list = byProject.get(link.project_id) ?? [];
      list.push({ company_id: company.company_id, name: company.name });
      byProject.set(link.project_id, list);
    }
  });
  return { byProject, unreadable };
}

// ---------------------------------------------------------------- skills

/** One skill and the agents that actually carry it. */
export interface ProjectSkill {
  skill: string;
  agents: string[];
}

/**
 * Union the crew's skills, keeping who carries each.
 *
 * There is no project-scoped skill registry on the Gateway — a skill belongs to
 * an agent — so a project's skills are exactly the skills of the agents on it,
 * and the union keeps provenance so the panel can say *which* agent brings it
 * rather than implying the project owns it.
 */
export function unionProjectSkills(
  profiles: Array<{ name: string; skills: string[] }>,
): ProjectSkill[] {
  const bySkill = new Map<string, string[]>();
  for (const profile of profiles) {
    for (const raw of profile.skills) {
      const skill = typeof raw === "string" ? raw.trim() : "";
      if (!skill) continue;
      const agents = bySkill.get(skill) ?? [];
      if (!agents.includes(profile.name)) agents.push(profile.name);
      bySkill.set(skill, agents);
    }
  }
  return [...bySkill.entries()]
    .map(([skill, agents]) => ({ skill, agents }))
    .sort((a, b) => a.skill.localeCompare(b.skill));
}

/**
 * The skills held by this project's crew, read from each agent's own profile.
 *
 * One agent's profile failing must not silently shrink the union — that is how
 * "every skill is gone" gets painted from a single bad read — so the failures
 * travel back by name beside the skills that did resolve.
 */
export async function projectSkillUnion(botNames: string[]): Promise<{
  skills: ProjectSkill[];
  /** Agents whose profile could not be read; their skills are NOT counted as absent. */
  unreadable: string[];
}> {
  const settled = await Promise.allSettled(botNames.map((name) => fetchBot(name)));
  const readable: Array<{ name: string; skills: string[] }> = [];
  const unreadable: string[] = [];
  settled.forEach((result, index) => {
    // `fetchBot` resolves to `null` for a name the registry does not know, and
    // a rejection is a failed read — both are "this agent's skills are not
    // known", and neither may be rendered as "this agent has none".
    const bot = result.status === "fulfilled" ? result.value : null;
    if (!bot) {
      unreadable.push(botNames[index]);
      return;
    }
    readable.push({ name: bot.name, skills: Array.isArray(bot.skills) ? bot.skills : [] });
  });
  return { skills: unionProjectSkills(readable), unreadable };
}

// ---------------------------------------------------------------- board

/** The board rows that belong to ONE project. Pure: no read, no claim beyond the id. */
export function projectBoardCards(cards: Card[], projectId: string): Card[] {
  return cards.filter((card) => (card.projectId || "") === projectId);
}

/** Columns of a card list, in board order, with the empty ones kept visible. */
export function boardColumns(cards: Card[]): Array<{ id: string; label: string; hint: string; cards: Card[] }> {
  return COLUMNS.map((column) => ({
    ...column,
    cards: cards.filter((card) => card.status === column.id),
  }));
}

/**
 * One project's board: the local board merged with the server company board,
 * then narrowed to this project.
 *
 * A failed server read is reported, never absorbed — the Kanban view already
 * refuses to degrade to local-only cards without saying so, and a project hub
 * that quietly dropped the server half would make "this project has no cards"
 * indistinguishable from "the company board did not answer".
 */
export async function loadProjectBoard(projectId: string): Promise<{
  cards: Card[];
  /** The server board's own failure message, or `null` when it answered. */
  serverBoardError: string | null;
}> {
  const local = loadCards();
  try {
    const merged = mergeServerCards(local, await listKanbanTasks());
    return { cards: projectBoardCards(merged, projectId), serverBoardError: null };
  } catch (err) {
    return {
      cards: projectBoardCards(local, projectId),
      serverBoardError: err instanceof Error ? err.message : errMsg(err),
    };
  }
}
