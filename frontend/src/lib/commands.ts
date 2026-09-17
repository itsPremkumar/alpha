import { get, send, asList, pick } from "./http";

export interface SlashCommand {
  name: string;
  description: string;
  category: string;
  usage?: string;
}

function toCmd(c: Record<string, unknown>): SlashCommand {
  return {
    name: String(pick(c, ["name", "command"], "")),
    description: String(pick(c, ["description", "summary"], "")),
    category: String(pick(c, ["category"], "general")),
    usage: typeof c.usage === "string" ? c.usage : undefined,
  };
}

export async function listCommands(): Promise<SlashCommand[]> {
  const d = await get<unknown>("/commands");
  return asList(d, ["commands", "data"]).map((c) => toCmd(c));
}

export async function searchCommands(q: string): Promise<SlashCommand[]> {
  const d = await get<unknown>(`/commands/search?q=${encodeURIComponent(q)}`);
  return asList(d, ["commands", "results", "data"]).map((c) => toCmd(c));
}

export async function commandCategories(): Promise<Array<{ name: string; count: number }>> {
  try {
    const d = await get<unknown>("/commands/categories");
    const list = asList(d, ["categories", "data"]);
    if (list.length > 0) {
      return list.map((c) => ({
        name: String(pick(c, ["name", "category"], "")),
        count: Number(pick(c, ["count", "total"], 0)),
      }));
    }
    const rec = (d as Record<string, unknown>).categories;
    if (rec && typeof rec === "object") {
      return Object.entries(rec as Record<string, unknown>).map(([name, count]) => ({
        name,
        count: typeof count === "number" ? count : 0,
      }));
    }
  } catch {
    /* ignore */
  }
  return [];
}

/** Run a slash command (e.g. "/goal status"). Returns a human-readable summary. */
export async function executeCommand(command: string, context?: Record<string, unknown>): Promise<string> {
  const d = await send<Record<string, unknown>>("/commands/execute", "POST", {
    command,
    ...(context ? { context } : {}),
  });
  const msg = pick<string | null>(d, ["message", "result", "output", "summary"], null);
  if (msg) return String(msg).slice(0, 4000);
  return JSON.stringify(d, null, 2).slice(0, 4000);
}
