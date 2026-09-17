import { get, send, asList, pick } from "./http";

const base = (threadId: string) => `/threads/${encodeURIComponent(threadId)}/agent-messages`;

export interface RosterAgent {
  name: string;
  role: string;
  status: string;
}

export async function fetchRoster(threadId: string): Promise<RosterAgent[]> {
  try {
    const d = await get<unknown>(`${base(threadId)}/roster`);
    return asList(d, ["agents", "roster", "data"]).map((a) => ({
      name: String(pick(a, ["name"], "")),
      role: String(pick(a, ["role"], "worker")),
      status: String(pick(a, ["status"], "idle")),
    }));
  } catch {
    return [];
  }
}

export async function registerRosterAgent(threadId: string, name: string, role = "worker"): Promise<void> {
  await send(`${base(threadId)}/register`, "POST", { name, role, status: "idle" });
}

export async function sendAgentMessage(
  threadId: string,
  sender: string,
  receiver: string,
  content: string
): Promise<void> {
  await send(`${base(threadId)}/messages`, "POST", {
    sender_name: sender,
    receiver_name: receiver,
    content,
  });
}

export interface InboxMessage {
  id: string;
  from: string;
  to: string;
  content: string;
  read: boolean;
}

export async function fetchInbox(threadId: string, agent: string): Promise<InboxMessage[]> {
  try {
    const d = await get<unknown>(`${base(threadId)}/inbox?agent_name=${encodeURIComponent(agent)}`);
    return asList(d, ["messages", "inbox", "data"]).map((m, i) => ({
      id: String(pick(m, ["id", "message_id"], `msg-${i}`)),
      from: String(pick(m, ["sender_name", "from", "sender"], "")),
      to: String(pick(m, ["receiver_name", "to", "receiver"], "")),
      content: String(pick(m, ["content", "text"], "")),
      read: Boolean(pick(m, ["read"], true)),
    }));
  } catch {
    return [];
  }
}

export async function setRosterStatus(threadId: string, agent: string, status: string): Promise<void> {
  await send(`${base(threadId)}/${encodeURIComponent(agent)}/status`, "PATCH", { status });
}
