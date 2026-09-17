import { get, send, pick } from "./http";

export interface McpServer {
  name: string;
  enabled: boolean;
  command: string;
  description: string;
}

export async function fetchMcpConfig(): Promise<McpServer[]> {
  const d = await get<Record<string, unknown>>("/mcp/config");
  const servers = d.mcp_servers;
  if (!servers || typeof servers !== "object") return [];
  return Object.entries(servers as Record<string, unknown>).map(([name, s]) => {
    const rec = (s && typeof s === "object" ? s : {}) as Record<string, unknown>;
    return {
      name,
      enabled: Boolean(pick(rec, ["enabled", "is_enabled"], true)),
      command: String(pick(rec, ["command", "url", "transport"], "")),
      description: String(pick(rec, ["description"], "")),
    };
  });
}

export async function setMcpServerEnabled(name: string, enabled: boolean): Promise<void> {
  try {
    await send("/mcp/config", "PATCH", { server_name: name, enabled });
  } catch {
    await send("/mcp/config", "PATCH", { name, enabled });
  }
}

export async function addMcpServer(name: string, command: string, args: string[]): Promise<void> {
  await send("/mcp/config/servers", "POST", {
    mcp_servers: { [name]: { command, args } },
  });
}

export async function deleteMcpServer(name: string): Promise<void> {
  await send(`/mcp/config/servers/${encodeURIComponent(name)}`, "DELETE");
}

export async function resetMcpCache(): Promise<string> {
  const d = await send<Record<string, unknown>>("/mcp/cache/reset", "POST", {});
  return String(pick(d, ["message"], "Cache reset."));
}
