import { get, send, asList, pick } from "./http";

export interface CustomAgent {
  name: string;
  display_name: string;
  description: string;
  model: string;
  soul: string;
}

function toAgent(a: Record<string, unknown>): CustomAgent {
  return {
    name: String(pick(a, ["name"], "")),
    display_name: String(pick(a, ["display_name", "displayName"], "")) || String(pick(a, ["name"], "")),
    description: String(pick(a, ["description"], "")),
    model: String(pick(a, ["model"], "")),
    soul: String(pick(a, ["soul"], "")),
  };
}

export async function listAgents(): Promise<CustomAgent[]> {
  const d = await get<unknown>("/agents");
  return asList(d, ["agents", "data"]).map((a) => toAgent(a));
}

export async function createAgent(draft: { name: string; description: string; model?: string; soul?: string }): Promise<CustomAgent> {
  const d = await send<Record<string, unknown>>("/agents", "POST", {
    name: draft.name,
    description: draft.description,
    ...(draft.model ? { model: draft.model } : {}),
    ...(draft.soul ? { soul: draft.soul } : {}),
  });
  return toAgent(d);
}

export async function updateAgent(name: string, patch: { description?: string; model?: string; soul?: string; display_name?: string }): Promise<CustomAgent> {
  const d = await send<Record<string, unknown>>(`/agents/${encodeURIComponent(name)}`, "PUT", patch);
  return toAgent(d);
}

export async function deleteAgent(name: string): Promise<void> {
  await send(`/agents/${encodeURIComponent(name)}`, "DELETE");
}

export async function fetchUserProfile(): Promise<string | null> {
  try {
    const d = await get<Record<string, unknown>>("/user-profile");
    const c = pick<string | null>(d, ["content", "text"], null);
    return c || null;
  } catch {
    return null;
  }
}

export async function saveUserProfile(content: string): Promise<void> {
  await send("/user-profile", "PUT", { content });
}
