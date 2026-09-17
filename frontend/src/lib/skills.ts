import { get, send, asList, pick } from "./http";

export interface Skill {
  name: string;
  description: string;
  enabled: boolean;
  version?: string;
  source?: string;
}

function toSkill(s: Record<string, unknown>): Skill {
  return {
    name: String(pick(s, ["name", "id"], "")),
    description: String(pick(s, ["description", "summary"], "")),
    enabled: Boolean(pick(s, ["enabled", "is_enabled"], true)),
    version: typeof s.version === "string" ? s.version : undefined,
    source: typeof s.source === "string" ? s.source : undefined,
  };
}

export async function listSkills(): Promise<Skill[]> {
  const d = await get<unknown>("/skills");
  return asList(d, ["skills", "data"]).map((s) => toSkill(s));
}

export async function setSkillEnabled(name: string, enabled: boolean): Promise<Skill> {
  const d = await send<Record<string, unknown>>(
    `/skills/${encodeURIComponent(name)}`,
    "PUT",
    { enabled }
  );
  return toSkill(d);
}

export async function reloadSkills(): Promise<string> {
  const d = await send<Record<string, unknown>>("/skills/reload", "POST", {});
  return String(pick(d, ["message", "status"], "Skills reloaded."));
}

export async function installSkill(name: string): Promise<string> {
  const d = await send<Record<string, unknown>>("/skills/install", "POST", { name });
  return String(pick(d, ["message", "status"], "Install requested."));
}

export interface SkillProposal {
  id: string;
  name: string;
  description: string;
  status: string;
  reason?: string;
}

export async function listProposals(): Promise<SkillProposal[]> {
  try {
    const d = await get<unknown>("/skills/proposals");
    return asList(d, ["proposals", "data"]).map((p, i) => ({
      id: String(pick(p, ["id", "proposal_id"], `proposal-${i}`)),
      name: String(pick(p, ["name", "skill_name"], "")),
      description: String(pick(p, ["description", "reason"], "")),
      status: String(pick(p, ["status"], "pending")),
      reason: typeof p.reason === "string" ? p.reason : undefined,
    }));
  } catch {
    return [];
  }
}

export async function approveProposal(id: string): Promise<void> {
  await send(`/skills/proposals/${encodeURIComponent(id)}/approve`, "POST", {});
}

export async function rejectProposal(id: string): Promise<void> {
  await send(`/skills/proposals/${encodeURIComponent(id)}/reject`, "POST", {});
}
