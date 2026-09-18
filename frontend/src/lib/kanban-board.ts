import { listKanbanTasks, moveKanbanTask, KanbanTask as ServerTask } from "./kanban";

/**
 * Full project kanban: 8-stage board stored locally (instant + offline),
 * merged with the server company board when reachable. Server-owned cards
 * push status moves back to the server; everything else lives in the card.
 */

export type CardStatus =
  | "backlog"
  | "ready"
  | "in_progress"
  | "blocked"
  | "review"
  | "testing"
  | "approval"
  | "done";

export const COLUMNS: Array<{ id: CardStatus; label: string; hint: string }> = [
  { id: "backlog", label: "Backlog", hint: "Ideas, not started" },
  { id: "ready", label: "Ready", hint: "Defined, can start" },
  { id: "in_progress", label: "Doing", hint: "Someone is on it" },
  { id: "blocked", label: "Blocked", hint: "Needs unblocking" },
  { id: "review", label: "Review", hint: "Needs a check" },
  { id: "testing", label: "Testing", hint: "Being verified" },
  { id: "approval", label: "Approval", hint: "Needs sign-off" },
  { id: "done", label: "Done", hint: "Finished + evidenced" },
];

export type Priority = "low" | "medium" | "high" | "urgent";

export interface HistoryEntry {
  at: string;
  text: string;
}

export interface Card {
  id: string;
  title: string;
  description: string;
  status: CardStatus;
  priority: Priority;
  /** Owning bot name, or null = unassigned / Lead. */
  agent: string | null;
  /** Project id (server) or null = no project. */
  projectId: string | null;
  projectName: string;
  dependencies: string[];
  files: string[];
  progress: number;
  deadline: string;
  blockedReason: string;
  evidence: string;
  tests: string;
  history: HistoryEntry[];
  createdAt: string;
  updatedAt: string;
  /** Server company-board card mirror (status moves sync back). */
  serverId: string | null;
}

const KEY = "agent_workspace.kanban.v1";
const MAX_CARDS = 300;

function uid(): string {
  return `card-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
}

export function emptyCard(): Card {
  const now = new Date().toISOString();
  return {
    id: uid(),
    title: "",
    description: "",
    status: "backlog",
    priority: "medium",
    agent: null,
    projectId: null,
    projectName: "",
    dependencies: [],
    files: [],
    progress: 0,
    deadline: "",
    blockedReason: "",
    evidence: "",
    tests: "",
    history: [{ at: now, text: "Card created." }],
    createdAt: now,
    updatedAt: now,
    serverId: null,
  };
}

function normalizeStatus(s: string): CardStatus {
  const v = (s || "").toLowerCase().replace(/[\s-]+/g, "_");
  const ids = COLUMNS.map((c) => c.id);
  if ((ids as string[]).includes(v)) return v as CardStatus;
  if (v.includes("progress") || v === "doing") return "in_progress";
  if (v.includes("test")) return "testing";
  if (v.includes("approv")) return "approval";
  if (v.includes("block")) return "blocked";
  if (v.includes("done") || v.includes("complete")) return "done";
  return "backlog";
}

export function loadCards(): Card[] {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [];
    const list = JSON.parse(raw) as Card[];
    if (!Array.isArray(list)) return [];
    return list
      .filter((c) => c && typeof c.id === "string")
      .map((c) => ({
        ...emptyCard(),
        ...c,
        id: String(c.id),
        status: normalizeStatus(String(c.status || "backlog")),
        history: Array.isArray(c.history) ? c.history : [],
        dependencies: Array.isArray(c.dependencies) ? c.dependencies : [],
        files: Array.isArray(c.files) ? c.files : [],
      }))
      .slice(0, MAX_CARDS);
  } catch {
    return [];
  }
}

function write(cards: Card[]): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(cards.slice(0, MAX_CARDS)));
  } catch {
    try {
      localStorage.setItem(KEY, JSON.stringify(cards.slice(0, 80)));
    } catch {
      /* storage unavailable */
    }
  }
}

export function saveCard(card: Card, note?: string): Card[] {
  const cards = loadCards();
  const now = new Date().toISOString();
  const next: Card = {
    ...card,
    updatedAt: now,
    history: note ? [...card.history, { at: now, text: note }].slice(-50) : card.history,
  };
  const i = cards.findIndex((c) => c.id === card.id);
  if (i >= 0) cards[i] = next;
  else cards.unshift(next);
  write(cards);
  return cards;
}

export function deleteCard(id: string): Card[] {
  const cards = loadCards().filter((c) => c.id !== id);
  write(cards);
  return cards;
}

export function clearBoard(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
}

function serverToCard(t: ServerTask): Card {
  const now = new Date().toISOString();
  return {
    ...emptyCard(),
    id: `srv-${t.id}`,
    title: t.title || t.id,
    description: t.description || "",
    status: normalizeStatus(t.status),
    agent: t.assignee || null,
    updatedAt: now,
    history: [{ at: now, text: "Mirrored from the server board." }],
    serverId: t.id,
  };
}

/**
 * Merge server company-board cards with local ones (matched by serverId).
 * Local edits (title, fields) win; server status wins unless locally moved
 * after the last sync — tracked implicitly by updatedAt ordering.
 */
export function mergeServerCards(local: Card[], server: ServerTask[]): Card[] {
  const byServer = new Map(local.map((c) => [c.serverId || "", c]));
  const out: Card[] = [];
  for (const t of server) {
    const existing = byServer.get(t.id);
    if (existing) {
      out.push({ ...existing, agent: existing.agent ?? (t.assignee || null) });
      byServer.delete(t.id);
    } else {
      out.push(serverToCard(t));
    }
  }
  for (const c of byServer.values()) {
    if (c.serverId) continue; // server card deleted remotely — drop mirror
    out.push(c);
  }
  return out;
}

/** Push a status move to the server board for mirrored cards.Throws on failure. */
export async function pushStatus(card: Card, status: CardStatus): Promise<void> {
  if (!card.serverId) return;
  await moveKanbanTask(card.serverId, status as "ready" | "in_progress" | "review" | "done", `Moved to ${status} from board UI`);
}

export function boardStats(cards: Card[]): { total: number; done: number; blocked: number; overdue: number } {
  const now = Date.now();
  return {
    total: cards.length,
    done: cards.filter((c) => c.status === "done").length,
    blocked: cards.filter((c) => c.status === "blocked").length,
    overdue: cards.filter((c) => c.status !== "done" && c.deadline && new Date(c.deadline).getTime() < now).length,
  };
}
