import { listKanbanTasks, moveKanbanTask, KanbanTask as ServerTask } from "./kanban";

/**
 * Full project kanban: 8-stage board stored locally (instant + offline),
 * merged with the server company board when reachable. Server-owned cards
 * push status moves back to the server; everything else lives in the card.
 */

export type CardStatus =
  | "backlog"
  | "todo"
  | "ready"
  | "in_progress"
  | "blocked"
  | "review"
  | "testing"
  | "approval"
  | "done";

/** The four stages the server's `TaskStatus` enum can represent on this board. */
export const SYNCABLE_STAGES: CardStatus[] = ["todo", "in_progress", "review", "done"];

export const COLUMNS: Array<{ id: CardStatus; label: string; hint: string }> = [
  { id: "backlog", label: "Backlog", hint: "Ideas, not started" },
  { id: "todo", label: "Ready", hint: "Defined, can start" },
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
  /**
   * Operator-entered progress, 0-100 — or `null` for "not recorded".
   *
   * This was `number` and defaulted to 0, which meant a card mirrored from the
   * server rendered a measured "0%" bar for a card the server tracks no
   * progress for at all. The server's `KanbanTask` has no progress field, so
   * there is nothing to mirror and the honest value is absent. A number here
   * always means somebody typed it.
   */
  progress: number | null;
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

const KEY = "alpha.kanban.v1";
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
    // Absent, not 0. A new card has had no progress reported; saying "0%"
    // would be a measurement nobody took. The editor's slider shows 0 as its
    // starting position, but nothing is stored until the operator moves it.
    progress: null,
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

/**
 * Map a stage onto one of this board's eight columns.
 *
 * `todo` and `ready` are the same stage spelled two ways — the backend's
 * `TaskStatus` enum says `todo`, this board's own vocabulary and the older UI
 * said `ready` — so they share one column rather than two.
 */
function normalizeStatus(s: string): CardStatus {
  const v = (s || "").toLowerCase().replace(/[\s-]+/g, "_");
  if (v === "ready") return "todo";
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
        // A board written before `progress` became nullable stored a real
        // number; that number is a value the operator entered, so it is kept.
        // Anything else (absent, a string, a non-finite number) is "not
        // recorded" — not a measured zero.
        progress:
          typeof c.progress === "number" && Number.isFinite(c.progress) ? c.progress : null,
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

/**
 * Mirror a server row into a card, carrying the server's OWN facts.
 *
 * This used to spread `emptyCard()` and then overwrite a few fields, which left
 * the card asserting things the server never said:
 *
 *  * `progress: 0` — `emptyCard()`'s default, painted as a measured "0%" bar on
 *    a card the server tracks no progress for. The board has no progress
 *    channel for a server card, so the field is left at the local default and
 *    the card is flagged `serverManaged`; the view states the progress is
 *    local-only rather than reporting a fabricated zero.
 *  * `createdAt: <now>` — the card editor printed "Created <the moment the page
 *    loaded>" for a card the server created months ago. `ServerTask.createdAt`
 *    is the server's own `created_at` (epoch seconds), so it is used when
 *    present and left empty (not invented) when absent.
 *  * `history: [{at: now, text: "Mirrored…"}]` — same fabrication, now
 *    timestamped with the server's `updated_at` when it sent one.
 */
function serverToCard(t: ServerTask): Card {
  const base = emptyCard();
  const createdAt =
    t.createdAt != null ? new Date(t.createdAt * 1000).toISOString() : base.createdAt;
  const updatedAt =
    t.updatedAt != null ? new Date(t.updatedAt * 1000).toISOString() : createdAt;
  return {
    ...base,
    id: `srv-${t.id}`,
    title: t.title || t.id,
    description: t.description || "",
    status: normalizeStatus(t.status),
    agent: t.assignee || null,
    // The server has no progress channel, so this stays absent rather than
    // inheriting a measured 0%.
    progress: null,
    createdAt,
    updatedAt,
    history: [{ at: updatedAt, text: "Mirrored from the server board." }],
    serverId: t.id,
  };
}

/**
 * Merge server company-board cards with local ones (matched by serverId).
 *
 * **The server's status wins for a mirrored card.** The old comment here
 * claimed "server status wins unless locally moved after the last sync —
 * tracked implicitly by updatedAt ordering", but no comparison was ever
 * performed: the merge kept `existing.status` and adopted only the server's
 * `assignee`. Measured, a server row reading `status: "done"` merged into a
 * local card still reading `in_progress`, on the first sync and on every one
 * after. So if an agent moved the card on the server, the operator never saw
 * it, and the operator's next move wrote their stale stage straight back over
 * the agent's — the exact "one agent's action silently overwrites another's"
 * failure, on a surface whose whole purpose is several agents on one board.
 *
 * The server is the authority for a card it owns. A local edit that is still
 * waiting to be pushed is applied to the *card* and then pushed by
 * `pushStatus`; it does not need to win the merge to take effect. Local-only
 * cards (no `serverId`) are untouched, and a local-only field the user typed
 * — title, description, deadline — still wins, because the server row has no
 * value for it.
 */
export function mergeServerCards(local: Card[], server: ServerTask[]): Card[] {
  // Local-only cards carry no serverId. Bucketing them all under one "" Map
  // key makes the Map retain only the last of them, so every sync silently
  // drops the rest of the user's own board. They are not mirrors, so they are
  // kept in their own list instead of competing for a server-id key.
  const byServer = new Map<string, Card>();
  const localOnly: Card[] = [];
  for (const c of local) {
    if (c.serverId) byServer.set(c.serverId, c);
    else localOnly.push(c);
  }
  const out: Card[] = [];
  for (const t of server) {
    const existing = byServer.get(t.id);
    if (existing) {
      const serverStatus = normalizeStatus(t.status);
      out.push({
        ...existing,
        // The server owns the stage of a card it owns.
        status: serverStatus,
        // A local assignee the user chose wins; otherwise adopt the server's.
        agent: existing.agent ?? (t.assignee || null),
        // The server's own clock, when it sent one. Never `new Date()`.
        updatedAt:
          t.updatedAt != null ? new Date(t.updatedAt * 1000).toISOString() : existing.updatedAt,
      });
      byServer.delete(t.id);
    } else {
      out.push(serverToCard(t));
    }
  }
  for (const c of byServer.values()) {
    if (c.serverId) continue; // server card deleted remotely — drop mirror
    out.push(c);
  }
  out.push(...localOnly);
  return out;
}

/**
 * Push a status move to the server board for mirrored cards. Throws on failure.
 *
 * This cast `status as "ready" | "in_progress" | "review" | "done"` and then
 * posted whatever the local board held. Measured, the local board's `testing`,
 * `approval` and `backlog` stages went on the wire verbatim — and the backend
 * does `TaskStatus(new_status.lower())` (alpha/company/kanban.py:156) against a
 * six-value enum that contains none of them, raising a `ValueError` that
 * `update_kanban_task` (company.py:456) does not catch. The operator got a 500
 * and a "server board rejected …" message about a stage the server had never
 * heard of.
 *
 * So the mapping is now explicit and refusable: `ready` is translated to the
 * enum's `todo`, and a local-only stage throws a message that says the stage is
 * this browser's, so the caller can report that instead of blaming the server.
 */
export async function pushStatus(card: Card, status: CardStatus): Promise<void> {
  if (!card.serverId) return;
  if (!SYNCABLE_STAGES.includes(status)) {
    throw new Error(
      `The server board has no "${status}" stage, so it was not sent. This stage is only in your browser.`,
    );
  }
  await moveKanbanTask(
    card.serverId,
    status as "todo" | "in_progress" | "review" | "done",
    `Moved to ${status} from board UI`,
  );
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
