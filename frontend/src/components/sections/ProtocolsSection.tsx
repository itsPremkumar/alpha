"use client";

// ProtocolsSection — the inter-agent + delivery + MoA planes, all previously invisible.
//
// Panels (every value from the real gateway, no mocks):
//   A2A           — /api/protocols/a2a (federation cards, delegation dispatch, registration)
//   Agent messages— /api/threads/{id}/agent-messages (roster, direct/broadcast send, inbox)
//   Deliveries    — /api/scheduled-tasks/* (exactly-once delivery ledger, blueprints, incidents)
//   Input polish  — /api/input-polish (one real pre-send rewrite)
//   MoA           — /api/models/moa + /api/models/moa/run (engine status + one real round,
//                   with the server's evidence_kind rendered verbatim)
//
// Honesty rules on this page:
//   * A2A cards, the roster, the delivery ledger and incident state are PROCESS-LOCAL
//     registries — the UI says so; nothing here is presented as durable storage.
//   * The MoA result shows the server's evidence_kind (real / failed / simulated) and its
//     note as sent; a simulated round is never rendered as model output.
//   * Inbox reads default to mark_as_read=false so viewing never consumes messages.
//   * Failures surface the gateway's detail (the shared API client carries it); an
//     unavailable subsystem renders its real error, never an empty success.
import React, { useCallback, useEffect, useState } from "react";
import {
  listA2ACards,
  registerA2ACard,
  delegateA2ATask,
  listAgentRoster,
  registerRosterAgent,
  sendAgentMessage,
  getAgentInbox,
  listTaskDeliveries,
  claimTaskDelivery,
  markTaskDelivery,
  listScheduledBlueprints,
  launchScheduledBlueprint,
  listTaskIncidents,
  resolveTaskIncident,
  polishInput,
} from "@/lib/protocols";
import type {
  A2ACapabilityCard,
  A2ADelegation,
  DeliveryRecord,
  RosterAgent,
} from "@/lib/protocols";
import { getMoaStatus, runMoaRound } from "@/lib/moa";
import type { MoaRunResult, MoaStatus } from "@/lib/moa";
import { Section, EmptyState, ErrorBox, Notice, Btn, Badge, Field, SkeletonList, inputCls } from "@/components/ui";
import { errMsg } from "@/lib/http";
import { RefreshCw, Send, Radio, Wand2, Network, Hammer, ScrollText } from "lucide-react";

type Tone = "green" | "amber" | "gray" | "blue" | "red" | "purple" | "cyan" | "indigo";

function toneFor(value: string): Tone {
  const v = value.toLowerCase();
  if (["available", "completed", "delivered", "succeeded", "ok", "registered", "real"].includes(v)) return "green";
  if (["busy", "pending", "accepted", "simulated", "working"].includes(v)) return "amber";
  if (["failed", "rejected", "offline", "error", "denied"].includes(v)) return "red";
  return "gray";
}

/** Render any record field honestly: strings/numbers/booleans as text, else "". */
function field(record: Record<string, unknown> | undefined, key: string): string {
  const v = record?.[key];
  if (v === null || v === undefined) return "";
  if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") return String(v);
  return JSON.stringify(v);
}

function parseJsonOrThrow(text: string, what: string): Record<string, unknown> {
  if (!text.trim()) return {};
  try {
    const parsed: unknown = JSON.parse(text);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error(`${what} must be a JSON object`);
    }
    return parsed as Record<string, unknown>;
  } catch (e) {
    throw new Error(`${what} must be valid JSON — ${e instanceof Error ? e.message : String(e)}`);
  }
}

function SubTabs(props: { value: string; onChange: (v: string) => void; tabs: Array<{ id: string; label: string }> }) {
  return (
    <div className="flex gap-1 rounded-xl bg-muted/60 p-1 w-fit flex-wrap">
      {props.tabs.map((t) => (
        <button
          key={t.id}
          type="button"
          onClick={() => props.onChange(t.id)}
          className={`px-3 py-1.5 rounded-lg text-[11px] font-semibold ${props.value === t.id ? "bg-card shadow" : "text-muted-foreground hover:text-foreground"}`}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

type PanelId = "a2a" | "messages" | "deliveries" | "polish" | "moa";

export function ProtocolsSection() {
  const [panel, setPanel] = useState<PanelId>("a2a");
  const [refreshKey, setRefreshKey] = useState(0);

  return (
    <Section
      title="Protocols"
      hint="A2A federation, agent-to-agent messaging, the scheduled delivery ledger, input polish and Mixture-of-Agents — measured by the gateway, never simulated."
      actions={
        <Btn variant="ghost" onClick={() => setRefreshKey((k) => k + 1)}>
          <RefreshCw className="size-3.5" /> Refresh
        </Btn>
      }
    >
      <SubTabs
        value={panel}
        onChange={(v) => setPanel(v as PanelId)}
        tabs={[
          { id: "a2a", label: "A2A" },
          { id: "messages", label: "Agent messages" },
          { id: "deliveries", label: "Deliveries" },
          { id: "polish", label: "Input polish" },
          { id: "moa", label: "MoA" },
        ]}
      />
      {panel === "a2a" && <A2APanel refreshKey={refreshKey} />}
      {panel === "messages" && <MessagesPanel refreshKey={refreshKey} />}
      {panel === "deliveries" && <DeliveriesPanel refreshKey={refreshKey} />}
      {panel === "polish" && <PolishPanel />}
      {panel === "moa" && <MoaPanel refreshKey={refreshKey} />}
    </Section>
  );
}

/* ══ A2A ═══════════════════════════════════════════════════════════ */

function A2APanel(props: { refreshKey: number }) {
  const [cards, setCards] = useState<A2ACapabilityCard[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [skillFilter, setSkillFilter] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [showRegister, setShowRegister] = useState(false);
  const [cardForm, setCardForm] = useState({ agent_id: "", name: "", description: "", skills: "", availability: "available", endpoint_url: "" });
  const [delegateForm, setDelegateForm] = useState({ target_agent_id: "", task_objective: "", context_data: "", deadline_seconds: "120" });
  const [delegation, setDelegation] = useState<A2ADelegation | null>(null);
  const [delegateError, setDelegateError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setCards(await listA2ACards(skillFilter.trim() || undefined));
      setError(null);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setLoading(false);
    }
  }, [skillFilter]);

  useEffect(() => {
    void load();
  }, [load, props.refreshKey]);

  const registerCard = async () => {
    setBusy(true);
    try {
      const res = await registerA2ACard({
        agent_id: cardForm.agent_id.trim(),
        name: cardForm.name.trim(),
        description: cardForm.description.trim(),
        skills: cardForm.skills.split(",").map((s) => s.trim()).filter(Boolean),
        availability: cardForm.availability,
        endpoint_url: cardForm.endpoint_url.trim() || null,
      });
      setNotice(`Card registered (${res.status}): ${res.card.agent_id}`);
      setShowRegister(false);
      await load();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  const delegate = async () => {
    setBusy(true);
    setDelegateError(null);
    try {
      const result = await delegateA2ATask({
        target_agent_id: delegateForm.target_agent_id.trim(),
        task_objective: delegateForm.task_objective.trim(),
        context_data: parseJsonOrThrow(delegateForm.context_data, "Context data"),
        deadline_seconds: Number(delegateForm.deadline_seconds) || 120,
      });
      setDelegation(result);
    } catch (e) {
      setDelegateError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      {notice && <Notice message={notice} />}
      {error && <ErrorBox message={`A2A registry: ${error}`} onRetry={load} />}
      {loading && cards.length === 0 ? (
        <SkeletonList rows={3} />
      ) : (
        <>
          <div className="flex flex-wrap items-end gap-2">
            <Field label="Skill filter" hint="Exact skill string the registry filters on.">
              <input
                className={inputCls}
                value={skillFilter}
                onChange={(e) => setSkillFilter(e.target.value)}
                placeholder="research"
              />
            </Field>
            <Btn variant="ghost" onClick={() => void load()}>
              <RefreshCw className="size-3.5" /> Reload
            </Btn>
            <Btn variant="ghost" onClick={() => setShowRegister((v) => !v)}>
              <Network className="size-3.5" /> Register card
            </Btn>
          </div>

          {showRegister && (
            <div className="space-y-2 rounded-xl border border-border/60 p-3">
              <div className="grid gap-2 md:grid-cols-2">
                <Field label="Agent id" hint="Unique in the federation registry.">
                  <input className={inputCls} value={cardForm.agent_id} onChange={(e) => setCardForm({ ...cardForm, agent_id: e.target.value })} />
                </Field>
                <Field label="Name">
                  <input className={inputCls} value={cardForm.name} onChange={(e) => setCardForm({ ...cardForm, name: e.target.value })} />
                </Field>
                <Field label="Description">
                  <input className={inputCls} value={cardForm.description} onChange={(e) => setCardForm({ ...cardForm, description: e.target.value })} />
                </Field>
                <Field label="Skills" hint="Comma separated.">
                  <input className={inputCls} value={cardForm.skills} onChange={(e) => setCardForm({ ...cardForm, skills: e.target.value })} />
                </Field>
                <Field label="Availability" hint="available | busy | offline — the server stores what you send.">
                  <input className={inputCls} value={cardForm.availability} onChange={(e) => setCardForm({ ...cardForm, availability: e.target.value })} />
                </Field>
                <Field label="Endpoint URL (optional)">
                  <input className={inputCls} value={cardForm.endpoint_url} onChange={(e) => setCardForm({ ...cardForm, endpoint_url: e.target.value })} />
                </Field>
              </div>
              <Btn disabled={busy || !cardForm.agent_id || !cardForm.name} onClick={registerCard}>
                Register
              </Btn>
            </div>
          )}

          {cards.length === 0 ? (
            <EmptyState title="No capability cards" hint="The in-process A2A registry returned nothing for this filter." />
          ) : (
            <div className="grid gap-2 md:grid-cols-2">
              {cards.map((card) => (
                <div key={card.agent_id} className="rounded-xl border border-border/60 p-2.5 text-[11px]">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold">{card.name || card.agent_id}</span>
                    <Badge tone={toneFor(card.availability)}>{card.availability}</Badge>
                    <span className="text-muted-foreground">{card.agent_id} · v{card.version}</span>
                  </div>
                  <p className="mt-1">{card.description}</p>
                  <p className="text-muted-foreground">skills: {card.skills.length ? card.skills.join(", ") : "none declared"}</p>
                  <p className="text-muted-foreground">protocols: {card.supported_protocols.join(", ")} · auth {card.auth_mode}</p>
                  {card.endpoint_url && <p className="text-muted-foreground">endpoint: {card.endpoint_url}</p>}
                </div>
              ))}
            </div>
          )}

          <div className="space-y-2 rounded-xl border border-border/60 p-3">
            <div className="text-[11px] font-semibold text-muted-foreground">Delegate a task (real dispatch)</div>
            <div className="grid gap-2 md:grid-cols-2">
              <Field label="Target agent id" hint="Must match a registered card; unknown targets answer 400/404 with the registry's reason.">
                <input className={inputCls} value={delegateForm.target_agent_id} onChange={(e) => setDelegateForm({ ...delegateForm, target_agent_id: e.target.value })} />
              </Field>
              <Field label="Deadline (seconds)">
                <input className={inputCls} value={delegateForm.deadline_seconds} onChange={(e) => setDelegateForm({ ...delegateForm, deadline_seconds: e.target.value })} />
              </Field>
            </div>
            <Field label="Task objective">
              <input className={inputCls} value={delegateForm.task_objective} onChange={(e) => setDelegateForm({ ...delegateForm, task_objective: e.target.value })} />
            </Field>
            <Field label="Context data (JSON object, optional)">
              <textarea className={inputCls} rows={2} value={delegateForm.context_data} onChange={(e) => setDelegateForm({ ...delegateForm, context_data: e.target.value })} />
            </Field>
            <Btn disabled={busy || !delegateForm.target_agent_id || !delegateForm.task_objective} onClick={delegate}>
              <Send className="size-3.5" /> Dispatch
            </Btn>
            {delegateError && <ErrorBox message={`Delegation failed: ${delegateError}`} onRetry={() => setDelegateError(null)} />}
            {delegation && (
              <div className="space-y-1 rounded-lg border border-border/60 p-2 text-[11px]">
                <div className="flex items-center gap-2">
                  <Badge tone={toneFor(delegation.status)}>{delegation.status}</Badge>
                  <span className="text-muted-foreground">{delegation.request_id} · {delegation.execution_seconds}s</span>
                </div>
                {delegation.error && <p className="text-red-600 dark:text-red-400">{delegation.error}</p>}
                {delegation.deliverable !== null && (
                  <pre className="whitespace-pre-wrap break-words">{JSON.stringify(delegation.deliverable, null, 2)}</pre>
                )}
                {delegation.evidence.length > 0 && (
                  <ul className="list-disc pl-4 text-muted-foreground">
                    {delegation.evidence.map((e, i) => (
                      <li key={i}>{e}</li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

/* ══ Agent messages ════════════════════════════════════════════════ */

function MessagesPanel(props: { refreshKey: number }) {
  const [threadId, setThreadId] = useState("");
  const [roster, setRoster] = useState<{ agents: RosterAgent[]; count: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [registerForm, setRegisterForm] = useState({ name: "", role: "worker", status: "idle" });
  const [sendForm, setSendForm] = useState({ sender_name: "", receiver_name: "", content: "", mode: "auto", kind: "message" });
  const [inboxAgent, setInboxAgent] = useState("");
  const [markRead, setMarkRead] = useState(false);
  const [inbox, setInbox] = useState<{ messages: Array<Record<string, unknown>>; count: number } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const loadRoster = useCallback(async () => {
    if (!threadId.trim()) {
      setRoster(null);
      return;
    }
    try {
      setRoster(await listAgentRoster(threadId.trim()));
      setError(null);
    } catch (e) {
      setError(errMsg(e));
    }
  }, [threadId]);

  useEffect(() => {
    void loadRoster();
  }, [loadRoster, props.refreshKey]);

  const register = async () => {
    setBusy(true);
    try {
      const agent = await registerRosterAgent(threadId.trim(), {
        name: registerForm.name.trim(),
        role: registerForm.role.trim() || "worker",
        status: registerForm.status.trim() || "idle",
      });
      setNotice(`Registered ${agent.name} (${agent.role}, ${agent.status})`);
      await loadRoster();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  const send = async () => {
    setBusy(true);
    try {
      const res = await sendAgentMessage(threadId.trim(), {
        sender_name: sendForm.sender_name.trim(),
        receiver_name: sendForm.receiver_name.trim(),
        content: sendForm.content,
        mode: sendForm.mode,
        kind: sendForm.kind,
      });
      setNotice(`Message ${res.status}`);
      await loadRoster();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  const openInbox = async () => {
    setBusy(true);
    try {
      setInbox(await getAgentInbox(threadId.trim(), inboxAgent.trim(), markRead));
      setError(null);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      <p className="text-[11px] text-muted-foreground">
        The roster and mailboxes are process-local in-memory state (single worker by design) — restarting the
        gateway clears them. Access is owner-checked per thread.
      </p>
      {notice && <Notice message={notice} />}
      {error && <ErrorBox message={`Agent messages: ${error}`} onRetry={() => void loadRoster()} />}
      <div className="grid gap-2 md:grid-cols-2">
        <Field label="Thread id" hint="Required — messaging is thread-scoped and owner-checked.">
          <input className={inputCls} value={threadId} onChange={(e) => setThreadId(e.target.value)} placeholder="thread-1" />
        </Field>
        <div className="flex items-end gap-2">
          <Btn variant="ghost" onClick={() => void loadRoster()}>
            <RefreshCw className="size-3.5" /> Load roster
          </Btn>
        </div>
      </div>

      {roster && (
        <div className="space-y-1.5">
          <div className="text-[11px] font-semibold text-muted-foreground">Roster ({roster.count})</div>
          {roster.agents.length === 0 ? (
            <EmptyState title="No agents in this thread's roster" hint="Register one below, or let a send auto-register its sender." />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-[11px] text-left">
                <thead className="text-muted-foreground">
                  <tr>
                    <th className="py-1 pr-3">Name</th>
                    <th className="py-1 pr-3">Role</th>
                    <th className="py-1 pr-3">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {roster.agents.map((a) => (
                    <tr key={a.name} className="border-t border-border/60">
                      <td className="py-1.5 pr-3">{a.name}</td>
                      <td className="py-1.5 pr-3">{a.role}</td>
                      <td className="py-1.5 pr-3">
                        <Badge tone={toneFor(a.status)}>{a.status}</Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      <div className="grid gap-3 md:grid-cols-2">
        <div className="space-y-2 rounded-xl border border-border/60 p-3">
          <div className="text-[11px] font-semibold text-muted-foreground">Register an agent</div>
          <Field label="Name" hint="1-64 chars: letters, digits, _ and - (server rule).">
            <input className={inputCls} value={registerForm.name} onChange={(e) => setRegisterForm({ ...registerForm, name: e.target.value })} />
          </Field>
          <Field label="Role">
            <input className={inputCls} value={registerForm.role} onChange={(e) => setRegisterForm({ ...registerForm, role: e.target.value })} />
          </Field>
          <Field label="Status" hint="idle | busy | completed — the server rejects anything else with 422.">
            <input className={inputCls} value={registerForm.status} onChange={(e) => setRegisterForm({ ...registerForm, status: e.target.value })} />
          </Field>
          <Btn disabled={busy || !threadId.trim() || !registerForm.name.trim()} onClick={register}>
            Register
          </Btn>
        </div>

        <div className="space-y-2 rounded-xl border border-border/60 p-3">
          <div className="text-[11px] font-semibold text-muted-foreground">Send a message</div>
          <div className="grid gap-2 md:grid-cols-2">
            <Field label="Sender name">
              <input className={inputCls} value={sendForm.sender_name} onChange={(e) => setSendForm({ ...sendForm, sender_name: e.target.value })} />
            </Field>
            <Field label="Receiver name" hint='Use "all" to broadcast.'>
              <input className={inputCls} value={sendForm.receiver_name} onChange={(e) => setSendForm({ ...sendForm, receiver_name: e.target.value })} />
            </Field>
            <Field label="Mode" hint="auto | steer | follow_up (server enum).">
              <input className={inputCls} value={sendForm.mode} onChange={(e) => setSendForm({ ...sendForm, mode: e.target.value })} />
            </Field>
            <Field label="Kind" hint="Server-side MESSAGE_KINDS; unknown kinds answer 422.">
              <input className={inputCls} value={sendForm.kind} onChange={(e) => setSendForm({ ...sendForm, kind: e.target.value })} />
            </Field>
          </div>
          <Field label="Content">
            <textarea className={inputCls} rows={2} value={sendForm.content} onChange={(e) => setSendForm({ ...sendForm, content: e.target.value })} />
          </Field>
          <Btn disabled={busy || !threadId.trim() || !sendForm.sender_name.trim() || !sendForm.receiver_name.trim() || !sendForm.content} onClick={send}>
            <Send className="size-3.5" /> Send
          </Btn>
        </div>
      </div>

      <div className="space-y-2 rounded-xl border border-border/60 p-3">
        <div className="text-[11px] font-semibold text-muted-foreground">Inbox</div>
        <div className="flex flex-wrap items-end gap-2">
          <Field label="Agent name">
            <input className={inputCls} value={inboxAgent} onChange={(e) => setInboxAgent(e.target.value)} />
          </Field>
          <label className="flex items-center gap-1.5 pb-2 text-[11px]">
            <input type="checkbox" checked={markRead} onChange={(e) => setMarkRead(e.target.checked)} className="size-3.5" />
            <span>mark as read (off by default — viewing never consumes messages)</span>
          </label>
          <Btn variant="ghost" disabled={busy || !threadId.trim() || !inboxAgent.trim()} onClick={openInbox}>
            <Radio className="size-3.5" /> Fetch inbox
          </Btn>
        </div>
        {inbox && (
          inbox.messages.length === 0 ? (
            <EmptyState title="Inbox empty" hint="No messages were returned for this agent — a measurement, not a delivery guarantee." />
          ) : (
            <ul className="space-y-1.5 text-[11px]">
              {inbox.messages.map((m, i) => (
                <li key={field(m, "id") || i} className="rounded-lg border border-border/60 px-2 py-1.5">
                  <div className="flex flex-wrap gap-2 text-muted-foreground">
                    <span>{field(m, "sender_name") || field(m, "sender")} → {field(m, "receiver_name") || field(m, "receiver")}</span>
                    <Badge tone={toneFor(field(m, "kind") || "message")}>{field(m, "kind") || "message"}</Badge>
                    <span>{field(m, "created_at")}</span>
                  </div>
                  <p>{field(m, "content")}</p>
                </li>
              ))}
            </ul>
          )
        )}
      </div>
    </div>
  );
}

/* ══ Deliveries / blueprints / incidents ═══════════════════════════ */

function DeliveriesPanel(props: { refreshKey: number }) {
  const [taskId, setTaskId] = useState("");
  const [deliveries, setDeliveries] = useState<{ deliveries: DeliveryRecord[]; count: number } | null>(null);
  const [incidents, setIncidents] = useState<Array<Record<string, unknown>>>([]);
  const [blueprints, setBlueprints] = useState<Array<Record<string, unknown>>>([]);
  const [unresolvedOnly, setUnresolvedOnly] = useState(true);
  const [claimOccurrence, setClaimOccurrence] = useState("");
  const [markForm, setMarkForm] = useState({ occurrence_id: "", status: "delivered", channel: "", artifact_ref: "", error: "" });
  const [blueprintValues, setBlueprintValues] = useState("{}");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setBlueprints(await listScheduledBlueprints());
      if (taskId.trim()) {
        setDeliveries(await listTaskDeliveries(taskId.trim()));
        setIncidents((await listTaskIncidents(taskId.trim(), unresolvedOnly)).incidents);
      }
      setError(null);
    } catch (e) {
      setError(errMsg(e));
    }
  }, [taskId, unresolvedOnly]);

  useEffect(() => {
    void load();
  }, [load, props.refreshKey]);

  const claim = async () => {
    setBusy(true);
    try {
      const res = await claimTaskDelivery(taskId.trim(), claimOccurrence.trim());
      setNotice(res.is_new ? "Delivery claimed (new record created)." : "Delivery already existed — claim is idempotent.");
      await load();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  const mark = async () => {
    setBusy(true);
    try {
      const rec = await markTaskDelivery(taskId.trim(), markForm.occurrence_id.trim(), {
        status: markForm.status,
        channel: markForm.channel.trim() || null,
        artifact_ref: markForm.artifact_ref.trim() || null,
        error: markForm.error.trim() || null,
      });
      setNotice(`Delivery marked ${field(rec, "status") || markForm.status}.`);
      await load();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  const launch = async (id: string) => {
    setBusy(true);
    try {
      const job = await launchScheduledBlueprint(id, parseJsonOrThrow(blueprintValues, "Blueprint values"));
      setNotice(`Blueprint ${id} launched as job ${field(job, "id") || "(id not reported)"} (${field(job, "cron_expression") || "cron not reported"}).`);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  const resolve = async (id: string) => {
    setBusy(true);
    try {
      await resolveTaskIncident(id);
      setNotice(`Incident ${id} resolved.`);
      await load();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      <p className="text-[11px] text-muted-foreground">
        The delivery ledger and incident tracker are in-process state recorded by this gateway process — the
        exactly-once claim/mark protocol is real, the storage is not durable.
      </p>
      {notice && <Notice message={notice} />}
      {error && <ErrorBox message={`Deliveries: ${error}`} onRetry={() => void load()} />}
      <div className="flex flex-wrap items-end gap-2">
        <Field label="Task id" hint="Scheduled task whose deliveries/incidents you want to audit.">
          <input className={inputCls} value={taskId} onChange={(e) => setTaskId(e.target.value)} placeholder="task-1" />
        </Field>
        <label className="flex items-center gap-1.5 pb-2 text-[11px]">
          <input type="checkbox" checked={unresolvedOnly} onChange={(e) => setUnresolvedOnly(e.target.checked)} className="size-3.5" />
          <span>unresolved incidents only</span>
        </label>
        <Btn variant="ghost" onClick={() => void load()}>
          <RefreshCw className="size-3.5" /> Load
        </Btn>
      </div>

      <div className="space-y-2 rounded-xl border border-border/60 p-3">
        <div className="text-[11px] font-semibold text-muted-foreground">Delivery ledger ({deliveries?.count ?? 0})</div>
        {deliveries && deliveries.deliveries.length === 0 && (
          <EmptyState title="No delivery records for this task" hint="Claim an occurrence below to create the first record." />
        )}
        {deliveries && deliveries.deliveries.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-[11px] text-left">
              <thead className="text-muted-foreground">
                <tr>
                  <th className="py-1 pr-3">Occurrence</th>
                  <th className="py-1 pr-3">Status</th>
                  <th className="py-1 pr-3">Channel</th>
                  <th className="py-1 pr-3">Artifact</th>
                  <th className="py-1 pr-3">Error</th>
                </tr>
              </thead>
              <tbody>
                {deliveries.deliveries.map((d, i) => (
                  <tr key={field(d, "occurrence_id") || i} className="border-t border-border/60 align-top">
                    <td className="py-1.5 pr-3">{field(d, "occurrence_id") || "—"}</td>
                    <td className="py-1.5 pr-3">
                      <Badge tone={toneFor(field(d, "status") || "pending")}>{field(d, "status") || "pending"}</Badge>
                    </td>
                    <td className="py-1.5 pr-3">{field(d, "channel") || "—"}</td>
                    <td className="py-1.5 pr-3">{field(d, "artifact_ref") || "—"}</td>
                    <td className="py-1.5 pr-3 text-red-600 dark:text-red-400">{field(d, "error") || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="grid gap-2 md:grid-cols-2">
          <Field label="Claim occurrence id" hint="POST claim creates the record once; repeating it is idempotent.">
            <input className={inputCls} value={claimOccurrence} onChange={(e) => setClaimOccurrence(e.target.value)} />
          </Field>
          <div className="flex items-end">
            <Btn disabled={busy || !taskId.trim() || !claimOccurrence.trim()} onClick={claim}>
              Claim
            </Btn>
          </div>
        </div>
        <div className="grid gap-2 md:grid-cols-2">
          <Field label="Mark occurrence id">
            <input className={inputCls} value={markForm.occurrence_id} onChange={(e) => setMarkForm({ ...markForm, occurrence_id: e.target.value })} />
          </Field>
          <Field label="Status" hint="delivered | failed | skipped | pending (server enum; anything else is 422).">
            <input className={inputCls} value={markForm.status} onChange={(e) => setMarkForm({ ...markForm, status: e.target.value })} />
          </Field>
          <Field label="Channel (optional)">
            <input className={inputCls} value={markForm.channel} onChange={(e) => setMarkForm({ ...markForm, channel: e.target.value })} />
          </Field>
          <Field label="Artifact ref (optional)">
            <input className={inputCls} value={markForm.artifact_ref} onChange={(e) => setMarkForm({ ...markForm, artifact_ref: e.target.value })} />
          </Field>
          <Field label="Error (optional, recorded verbatim)">
            <input className={inputCls} value={markForm.error} onChange={(e) => setMarkForm({ ...markForm, error: e.target.value })} />
          </Field>
          <div className="flex items-end">
            <Btn disabled={busy || !taskId.trim() || !markForm.occurrence_id.trim()} onClick={mark}>
              Mark delivery
            </Btn>
          </div>
        </div>
      </div>

      <div className="space-y-2 rounded-xl border border-border/60 p-3">
        <div className="flex items-center gap-2 text-[11px] font-semibold text-muted-foreground">
          <ScrollText className="size-3.5" /> Blueprints ({blueprints.length})
        </div>
        <Field label="Launch values (JSON object)" hint="Rendered into the cron job the blueprint defines.">
          <textarea className={inputCls} rows={2} value={blueprintValues} onChange={(e) => setBlueprintValues(e.target.value)} />
        </Field>
        {blueprints.length === 0 ? (
          <EmptyState title="No blueprints registered" hint="The blueprint store is empty in this process." />
        ) : (
          <ul className="space-y-1.5 text-[11px]">
            {blueprints.map((b, i) => (
              <li key={field(b, "id") || i} className="flex flex-wrap items-center gap-2 rounded-lg border border-border/60 px-2 py-1.5">
                <span className="font-semibold">{field(b, "id") || "(no id)"}</span>
                <span className="text-muted-foreground">{field(b, "name") || field(b, "description")}</span>
                <Btn variant="ghost" disabled={busy} onClick={() => void launch(field(b, "id"))}>
                  <Hammer className="size-3.5" /> Launch
                </Btn>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="space-y-2 rounded-xl border border-border/60 p-3">
        <div className="text-[11px] font-semibold text-muted-foreground">Incidents ({incidents.length})</div>
        {incidents.length === 0 ? (
          <EmptyState title="No incidents recorded" hint={unresolvedOnly ? "No unresolved incidents for this task in this process." : "No incidents at all for this task in this process."} />
        ) : (
          <ul className="space-y-1.5 text-[11px]">
            {incidents.map((inc, i) => (
              <li key={field(inc, "incident_id") || i} className="flex flex-wrap items-center gap-2 rounded-lg border border-border/60 px-2 py-1.5">
                <span className="font-semibold">{field(inc, "incident_id") || "(no id)"}</span>
                <Badge tone={field(inc, "resolved") === "true" ? "green" : "red"}>
                  {field(inc, "resolved") === "true" ? "resolved" : "unresolved"}
                </Badge>
                <span className="text-muted-foreground">{field(inc, "kind") || field(inc, "error")}</span>
                <Btn variant="ghost" disabled={busy} onClick={() => void resolve(field(inc, "incident_id"))}>
                  Resolve
                </Btn>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

/* ══ Input polish ══════════════════════════════════════════════════ */

function PolishPanel() {
  const [text, setText] = useState("");
  const [locale, setLocale] = useState("");
  const [result, setResult] = useState<{ rewritten_text: string; changed: boolean } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      setResult(await polishInput(text, { locale: locale.trim() || null }));
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      <p className="text-[11px] text-muted-foreground">
        A real model call rewrites the draft before sending (intent, entities, paths and any leading slash
        command are preserved). The result is the model's output or an error — never a canned suggestion.
      </p>
      {error && <ErrorBox message={`Input polish failed: ${error}`} onRetry={() => setError(null)} />}
      <Field label="Draft text">
        <textarea className={inputCls} rows={4} value={text} onChange={(e) => setText(e.target.value)} />
      </Field>
      <Field label="Locale hint (optional)">
        <input className={inputCls} value={locale} onChange={(e) => setLocale(e.target.value)} placeholder="en-US" />
      </Field>
      <Btn disabled={busy || !text.trim()} onClick={run}>
        <Wand2 className="size-3.5" /> {busy ? "Polishing…" : "Polish draft"}
      </Btn>
      {result && (
        <div className="space-y-1.5 rounded-xl border border-border/60 p-3 text-[11px]">
          <div className="flex items-center gap-2">
            <Badge tone={result.changed ? "green" : "gray"}>{result.changed ? "model changed the draft" : "model left it as-is"}</Badge>
          </div>
          <pre className="whitespace-pre-wrap break-words">{result.rewritten_text || "(the model returned an empty rewrite)"}</pre>
        </div>
      )}
    </div>
  );
}

/* ══ MoA ═══════════════════════════════════════════════════════════ */

function evidenceTone(kind: string): Tone {
  if (kind === "real") return "green";
  if (kind === "simulated") return "amber";
  if (kind === "failed") return "red";
  return "gray";
}

function MoaPanel(props: { refreshKey: number }) {
  const [status, setStatus] = useState<MoaStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [prompt, setPrompt] = useState("");
  const [modelsInput, setModelsInput] = useState("");
  const [run, setRun] = useState<MoaRunResult | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setStatus(await getMoaStatus());
      setStatusError(null);
    } catch (e) {
      setStatusError(errMsg(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, props.refreshKey]);

  const candidateModels = modelsInput
    .split(",")
    .map((m) => m.trim())
    .filter(Boolean);

  const runRound = async () => {
    setBusy(true);
    setRunError(null);
    try {
      setRun(await runMoaRound({ prompt, candidate_models: candidateModels }));
    } catch (e) {
      setRunError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      {statusError && <ErrorBox message={`MoA status unavailable: ${statusError}`} onRetry={load} />}
      {loading && !status && <SkeletonList rows={3} />}
      {status && (
        <div className="space-y-1.5 rounded-xl border border-border/60 p-3 text-[11px]">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-semibold">Mixture-of-Agents</span>
            {status.engine_error ? (
              <Badge tone="red">capability registry: {status.engine_error}</Badge>
            ) : (
              <Badge tone={status.engine ? "green" : "amber"}>
                {status.engine ? "engine registered" : "engine entry unavailable"}
              </Badge>
            )}
            {status.tool.registered === true ? (
              <Badge tone="green">tool {field(status.tool, "name")} registered</Badge>
            ) : (
              <Badge tone="amber">
                tool {field(status.tool, "name") || "moa_multi_model_reasoning"} not registered
                {status.tool.error ? ` (${field(status.tool, "error")})` : ""}
              </Badge>
            )}
          </div>
          {status.command ? (
            <p className="text-muted-foreground">
              command {field(status.command, "command")} · handler{" "}
              {status.command.handler_available === true ? "available" : "unavailable"}
              {status.command.handler_error ? ` (${field(status.command, "handler_error")})` : ""} ·{" "}
              {field(status.command, "usage")}
            </p>
          ) : (
            <p className="text-muted-foreground">command entry unavailable: {status.command_error ?? "no reason given"}</p>
          )}
          {status.limits_error ? (
            <p className="text-muted-foreground">limits unavailable: {status.limits_error}</p>
          ) : (
            <p className="text-muted-foreground">
              limits: max_advisors {field(status.limits, "max_advisors") || "?"} · max_reference_chars{" "}
              {field(status.limits, "max_reference_chars") || "?"} · orchestrator workers{" "}
              {field(status.orchestrator, "max_workers") || field(status.orchestrator, "error") || "?"}
            </p>
          )}
          {"error" in status.redaction ? (
            <p className="text-muted-foreground">redaction probe unavailable: {field(status.redaction, "error")}</p>
          ) : (
            <p className="text-muted-foreground">
              redaction probes: email {status.redaction.email_masked === true ? "masked" : "NOT masked"} · phone{" "}
              {status.redaction.phone_masked === true ? "masked" : "NOT masked"} · secret{" "}
              {status.redaction.secret_masked === true ? "masked" : "NOT masked"}
            </p>
          )}
        </div>
      )}

      <div className="space-y-2 rounded-xl border border-border/60 p-3">
        <div className="text-[11px] font-semibold text-muted-foreground">Run one real MoA round</div>
        <Field label="Question / prompt" hint="The engine redacts PII and secrets before any advisor sees it; the response shows the redacted prompt actually used.">
          <textarea className={inputCls} rows={3} value={prompt} onChange={(e) => setPrompt(e.target.value)} />
        </Field>
        <Field label="Candidate models" hint="Comma separated model names. The server caps the panel at the engine's max_advisors and re-checks model:use authorization for every one.">
          <input className={inputCls} value={modelsInput} onChange={(e) => setModelsInput(e.target.value)} placeholder="model-a, model-b" />
        </Field>
        <Btn disabled={busy || !prompt.trim() || candidateModels.length === 0} onClick={runRound}>
          <Network className="size-3.5" /> {busy ? "Running round…" : "Run round"}
        </Btn>
        {runError && <ErrorBox message={`MoA round failed: ${runError}`} onRetry={() => setRunError(null)} />}
        {run && (
          <div className="space-y-2 text-[11px]">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={evidenceTone(run.evidence_kind)}>evidence: {run.evidence_kind}</Badge>
              <span className="text-muted-foreground">{run.total_duration_ms} ms total</span>
            </div>
            <p className="text-muted-foreground">{run.evidence_note}</p>
            <p className="text-muted-foreground">prompt actually used (redacted): {run.prompt}</p>
            <div className="overflow-x-auto">
              <table className="w-full text-left">
                <thead className="text-muted-foreground">
                  <tr>
                    <th className="py-1 pr-3">Model</th>
                    <th className="py-1 pr-3">Outcome</th>
                    <th className="py-1 pr-3">Duration</th>
                    <th className="py-1 pr-3">Response / error</th>
                  </tr>
                </thead>
                <tbody>
                  {run.candidates.map((c) => (
                    <tr key={c.model_name} className="border-t border-border/60 align-top">
                      <td className="py-1.5 pr-3">{c.model_name}</td>
                      <td className="py-1.5 pr-3">
                        <Badge tone={c.success ? "green" : "red"}>{c.success ? "ok" : "failed"}</Badge>
                      </td>
                      <td className="py-1.5 pr-3 tabular-nums">{c.duration_ms} ms</td>
                      <td className="py-1.5 pr-3">
                        {c.success ? c.response : <span className="text-red-600 dark:text-red-400">{c.error ?? "no error text"}</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="rounded-lg border border-border/60 p-2">
              <div className="font-semibold">Consensus</div>
              <pre className="whitespace-pre-wrap break-words">{run.consensus_response || "(no consensus text)"}</pre>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
