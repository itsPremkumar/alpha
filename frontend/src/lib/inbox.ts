import { get, send, asList, pick } from "./http";

const base = (threadId: string) => `/threads/${encodeURIComponent(threadId)}/agent-messages`;

export interface RosterAgent {
  name: string;
  role: string;
  status: string;
  avatar?: string | null;
}

export async function fetchRoster(threadId: string): Promise<RosterAgent[]> {
  // Failure propagates: an empty roster must not masquerade as "no agents".
  const d = await get<unknown>(`${base(threadId)}/roster`);
  return asList(d, ["agents", "roster", "data"]).map((a) => ({
    name: String(pick(a, ["name"], "")),
    role: String(pick(a, ["role"], "worker")),
    status: String(pick(a, ["status"], "idle")),
    avatar: String(pick(a, ["avatar"], "")) || null,
  }));
}

export async function registerRosterAgent(threadId: string, name: string, role = "worker"): Promise<void> {
  await send(`${base(threadId)}/register`, "POST", { name, role, status: "idle" });
}

export async function sendAgentMessage(
  threadId: string,
  sender: string,
  receiver: string,
  content: string,
  // The server's own default (`SendMessageRequest.kind`); sent explicitly so a
  // chosen envelope type is never silently dropped to `message` on the wire.
  kind = "message"
): Promise<void> {
  await send(`${base(threadId)}/messages`, "POST", {
    sender_name: sender,
    receiver_name: receiver,
    content,
    kind,
  });
}

export interface InboxMessage {
  id: string;
  from: string;
  to: string;
  content: string;
  read: boolean;
  /** The typed envelope the sender chose, verbatim from the wire. */
  kind: string;
  /** Server-stamped creation time, or `null` when the row carries none. */
  createdAt: string | null;
}

export async function fetchInbox(threadId: string, agent: string): Promise<InboxMessage[]> {
  try {
    // `mark_as_read=false`: the Direct list re-reads on every poll, and the
    // route's default marks rows read *and* returns only unread — so a plain
    // read would consume the other agent's mailbox and empty the thread after
    // one cycle. Viewing never consumes (same rule as `protocols.getAgentInbox`).
    const d = await get<unknown>(`${base(threadId)}/inbox?agent_name=${encodeURIComponent(agent)}&mark_as_read=false`);
    return asList(d, ["messages", "inbox", "data"]).map((m, i) => {
      const at = pick<unknown>(m, ["created_at", "timestamp", "at", "time"], null);
      return {
        id: String(pick(m, ["id", "message_id"], `msg-${i}`)),
        from: String(pick(m, ["sender_name", "from", "sender"], "")),
        to: String(pick(m, ["receiver_name", "to", "receiver"], "")),
        content: String(pick(m, ["content", "text"], "")),
        read: Boolean(pick(m, ["read"], true)),
        // The mailbox row carries `kind` (`InterAgentMessage.kind`); the
        // fallback is the dataclass's own default, not a client invention.
        kind: String(pick(m, ["kind"], "message")),
        // Absent stays absent: `""` would read as a real (empty) stamp and
        // `String(null)` would render the word "null" next to the message.
        createdAt: at === null || at === undefined || at === "" ? null : String(at),
      };
    });
  } catch {
    return [];
  }
}

export async function setRosterStatus(threadId: string, agent: string, status: string): Promise<void> {
  await send(`${base(threadId)}/${encodeURIComponent(agent)}/status`, "PATCH", { status });
}
