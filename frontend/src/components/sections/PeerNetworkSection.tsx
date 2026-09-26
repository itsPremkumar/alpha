"use client";

// A separate Alpha Network workspace session. It intentionally does not reuse
// the local bot/group-chat surfaces: those describe agents inside one Gateway,
// while this view describes independently installed Alpha peers and delivery
// receipts across the free local-first network.

import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  Check,
  CircleDot,
  Copy,
  KeyRound,
  Link2,
  MessageSquare,
  Network,
  Plus,
  RefreshCw,
  Search,
  Send,
  ShieldCheck,
  Users,
  X,
} from "lucide-react";
import { Badge, Btn, EmptyState, ErrorBox, Field, Notice, Section, SkeletonList, inputCls } from "@/components/ui";
import {
  createPeerConversation,
  discoverPeers,
  fetchPeerNetworkStatus,
  listPeerConversations,
  listPeerMessages,
  listPeers,
  markPeerMessageRead,
  pairPeer,
  publishPeerCardToGitHub,
  rotatePairingCode,
  sendPeerMessage,
  setPeerTrust,
  type Peer,
  type PeerConversation,
  type PeerConversationMode,
  type PeerMessage,
  type PeerNetworkStatus,
} from "@/lib/peer-network";
import { errMsg } from "@/lib/http";

const MODES: Array<{ value: PeerConversationMode; label: string; hint: string }> = [
  { value: "direct", label: "One-to-one", hint: "Two participants" },
  { value: "one_to_many", label: "One-to-many", hint: "One sender, many recipients" },
  { value: "many_to_one", label: "Many-to-one", hint: "Many senders, one target" },
  { value: "many_to_many", label: "Many-to-many", hint: "Shared group conversation" },
  { value: "broadcast", label: "Broadcast", hint: "Fan out to selected peers" },
];

function peerLabel(peer: Peer): string {
  return peer.name || peer.agent_id;
}

function trustTone(trust: string): "green" | "amber" | "red" | "gray" {
  if (trust === "paired") return "green";
  if (trust === "blocked") return "red";
  if (trust === "discovered") return "amber";
  return "gray";
}

function discoveryTone(value: unknown): "green" | "amber" | "red" | "gray" {
  if (value === true) return "green";
  if (value === false) return "amber";
  return "gray";
}

function fieldValue(record: unknown, key: string): unknown {
  return record && typeof record === "object" ? (record as Record<string, unknown>)[key] : undefined;
}

export function PeerNetworkSection() {
  const [status, setStatus] = useState<PeerNetworkStatus | null>(null);
  const [peers, setPeers] = useState<Peer[]>([]);
  const [conversations, setConversations] = useState<PeerConversation[]>([]);
  const [messages, setMessages] = useState<PeerMessage[]>([]);
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [showPairingCode, setShowPairingCode] = useState(false);
  const [search, setSearch] = useState("");
  const [pairForm, setPairForm] = useState({ endpoint: "", pairing_code: "", expected_agent_id: "" });
  const [conversationForm, setConversationForm] = useState({
    title: "Alpha peer room",
    mode: "many_to_many" as PeerConversationMode,
    participants: [] as string[],
  });
  const [messageText, setMessageText] = useState("");

  const refresh = useCallback(async (quiet = false) => {
    if (!quiet) setRefreshing(true);
    try {
      const [nextStatus, nextPeers, nextConversations] = await Promise.all([
        fetchPeerNetworkStatus(),
        listPeers(),
        listPeerConversations(),
      ]);
      setStatus(nextStatus);
      setPeers(nextPeers);
      setConversations(nextConversations);
      setError(null);
    } catch (cause) {
      setError(errMsg(cause));
    } finally {
      setLoading(false);
      if (!quiet) setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(true), 7000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const loadMessages = useCallback(async (conversationId: string) => {
    try {
      setMessages(await listPeerMessages(conversationId));
      setError(null);
    } catch (cause) {
      setError(errMsg(cause));
    }
  }, []);

  useEffect(() => {
    if (selectedConversationId) void loadMessages(selectedConversationId);
    else setMessages([]);
  }, [loadMessages, selectedConversationId]);

  const selectedConversation = useMemo(
    () => conversations.find((conversation) => conversation.conversation_id === selectedConversationId) ?? null,
    [conversations, selectedConversationId],
  );

  const filteredPeers = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return peers;
    return peers.filter((peer) =>
      [peer.agent_id, peer.name, peer.description, ...peer.capabilities].some((value) =>
        String(value).toLowerCase().includes(needle),
      ),
    );
  }, [peers, search]);

  const runDiscover = async () => {
    setBusy(true);
    try {
      const discovered = await discoverPeers();
      setPeers(discovered);
      setNotice(`Discovery pass completed. The Gateway reported ${discovered.length} peer${discovered.length === 1 ? "" : "s"}.`);
      setError(null);
    } catch (cause) {
      setError(errMsg(cause));
    } finally {
      setBusy(false);
    }
  };

  const runPublishGitHub = async () => {
    if (!window.confirm("Publish this public Agent Card to the configured GitHub rendezvous repository? Do not use a repository for pairing codes or messages.")) return;
    setBusy(true);
    try {
      await publishPeerCardToGitHub();
      setNotice("Agent Card published to the configured free GitHub rendezvous repository.");
      setError(null);
    } catch (cause) {
      setError(errMsg(cause));
    } finally {
      setBusy(false);
    }
  };

  const runPair = async () => {
    if (!pairForm.endpoint.trim() || !pairForm.pairing_code.trim()) return;
    setBusy(true);
    try {
      const peer = await pairPeer({
        endpoint: pairForm.endpoint.trim(),
        pairing_code: pairForm.pairing_code.trim(),
        expected_agent_id: pairForm.expected_agent_id.trim() || null,
      });
      setNotice(`Paired with ${peerLabel(peer)}. The credential stays out of the Agent Card.`);
      setPairForm({ endpoint: "", pairing_code: "", expected_agent_id: "" });
      await refresh(true);
    } catch (cause) {
      setError(errMsg(cause));
    } finally {
      setBusy(false);
    }
  };

  const runTrust = async (peer: Peer, trust: "blocked" | "discovered") => {
    setBusy(true);
    try {
      await setPeerTrust(peer.agent_id, trust);
      setNotice(`${peerLabel(peer)} is now marked ${trust}.`);
      await refresh(true);
    } catch (cause) {
      setError(errMsg(cause));
    } finally {
      setBusy(false);
    }
  };

  const runRotateCode = async () => {
    if (!window.confirm("Rotate the pairing code? Existing peers must be paired again with the new code.")) return;
    setBusy(true);
    try {
      await rotatePairingCode();
      setNotice("Pairing code rotated. Pair each peer again before sending messages.");
      await refresh(true);
    } catch (cause) {
      setError(errMsg(cause));
    } finally {
      setBusy(false);
    }
  };

  const toggleParticipant = (agentId: string) => {
    setConversationForm((form) => ({
      ...form,
      participants: form.participants.includes(agentId)
        ? form.participants.filter((item) => item !== agentId)
        : [...form.participants, agentId],
    }));
  };

  const runCreateConversation = async () => {
    if (conversationForm.participants.length === 0) return;
    setBusy(true);
    try {
      const conversation = await createPeerConversation({
        title: conversationForm.title,
        mode: conversationForm.mode,
        participants: conversationForm.participants,
      });
      setConversations((current) => [conversation, ...current.filter((item) => item.conversation_id !== conversation.conversation_id)]);
      setSelectedConversationId(conversation.conversation_id);
      setNotice(`Conversation created: ${conversation.title}.`);
      setError(null);
    } catch (cause) {
      setError(errMsg(cause));
    } finally {
      setBusy(false);
    }
  };

  const runSend = async () => {
    if (!selectedConversationId || !messageText.trim()) return;
    setBusy(true);
    try {
      const sent = await sendPeerMessage({
        conversation_id: selectedConversationId,
        text: messageText.trim(),
        kind: "chat",
      });
      setMessages((current) => [...current.filter((item) => item.message_id !== sent.message_id), sent]);
      setMessageText("");
      setNotice(`Message ${sent.status}. Delivery receipts are shown per recipient.`);
      setError(null);
    } catch (cause) {
      setError(errMsg(cause));
    } finally {
      setBusy(false);
    }
  };

  const runRead = async (message: PeerMessage) => {
    if (message.direction !== "inbound" || message.read_at) return;
    try {
      const updated = await markPeerMessageRead(message.message_id);
      setMessages((current) => current.map((item) => (item.message_id === updated.message_id ? updated : item)));
    } catch (cause) {
      setError(errMsg(cause));
    }
  };

  const copyPairingCode = async () => {
    if (!status?.pairing_code) return;
    try {
      await navigator.clipboard.writeText(status.pairing_code);
      setNotice("Pairing code copied. Share it only through a channel you trust.");
    } catch {
      setError("The browser did not allow clipboard access; reveal and copy the code manually.");
    }
  };

  if (loading && !status) {
    return (
      <Section title="Alpha Network" hint="Discovering independently installed Alpha peers over free local-first transports.">
        <SkeletonList rows={5} />
      </Section>
    );
  }

  return (
    <Section
      title="Alpha Network"
      hint="A separate peer-to-peer session for discovering, pairing, and messaging other Alpha installations. Discovery does not grant access; pairing is explicit."
      actions={
        <>
          <Btn variant="ghost" onClick={() => void refresh()} disabled={refreshing}>
            <RefreshCw className={`size-3.5 ${refreshing ? "animate-spin" : ""}`} /> Refresh
          </Btn>
          <Btn onClick={() => void runDiscover()} disabled={busy}>
            <RadioIcon /> Discover LAN peers
          </Btn>
          {status && fieldValue(status.discovery, "github") && (fieldValue(status.discovery.github as Record<string, unknown>, "writable") === true) && (
            <Btn variant="ghost" onClick={() => void runPublishGitHub()} disabled={busy}>
              <Link2 className="size-3.5" /> Publish card
            </Btn>
          )}
        </>
      }
    >
      {notice && <Notice message={notice} />}
      {error && <ErrorBox message={`Alpha Network: ${error}`} onRetry={() => void refresh()} />}

      {status && (
        <div className="grid gap-2 md:grid-cols-4">
          <div className="rounded-xl border border-border/60 bg-card p-3 text-[11px] md:col-span-2">
            <div className="flex flex-wrap items-center gap-2">
              <Network className="size-4 text-primary" />
              <span className="font-semibold">{String(status.identity.name || "Alpha")}</span>
              <Badge tone={status.enabled ? "green" : "gray"}>{status.enabled ? "network enabled" : "network disabled"}</Badge>
              <span className="font-mono text-muted-foreground">{String(status.identity.agent_id || "unknown id")}</span>
            </div>
            <p className="mt-1 text-muted-foreground">Local persistence: {String(fieldValue(status.persistence, "backend") || "unknown")} · no broker required.</p>
          </div>
          <div className="rounded-xl border border-border/60 bg-card p-3 text-[11px]">
            <div className="font-semibold">Discovery providers</div>
            <div className="mt-1 flex flex-wrap gap-1">
              {Object.entries(status.discovery).map(([name, value]) => (
                <Badge key={name} tone={discoveryTone(fieldValue(value, "running"))}>
                  {name}: {fieldValue(value, "running") === true ? "running" : fieldValue(value, "available") === true ? "available" : "off"}
                </Badge>
              ))}
            </div>
          </div>
          <div className="rounded-xl border border-border/60 bg-card p-3 text-[11px]">
            <div className="flex items-center gap-1 font-semibold"><KeyRound className="size-3.5" /> Pairing code</div>
            {showPairingCode && status.pairing_code ? (
              <div className="mt-1 flex items-center gap-1">
                <code className="min-w-0 truncate rounded bg-muted px-1.5 py-1">{status.pairing_code}</code>
                <button type="button" className="rounded p-1 hover:bg-muted" onClick={() => void copyPairingCode()} aria-label="Copy pairing code"><Copy className="size-3.5" /></button>
              </div>
            ) : (
              <button type="button" className="mt-1 text-primary hover:underline" onClick={() => setShowPairingCode(true)}>Reveal pairing code</button>
            )}
            <button type="button" className="mt-1 block text-muted-foreground hover:text-foreground hover:underline" onClick={() => void runRotateCode()} disabled={busy}>Rotate code</button>
          </div>
        </div>
      )}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.1fr)_minmax(0,1.4fr)]">
        <div className="space-y-3">
          <div className="rounded-2xl border border-border/60 bg-card/40 p-3 space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h3 className="text-sm font-semibold">Discovered peers</h3>
                <p className="text-[11px] text-muted-foreground">Untrusted cards are visible; pair before sending.</p>
              </div>
              <Badge tone="blue">{peers.length} total</Badge>
            </div>
            <div className="relative">
              <Search className="pointer-events-none absolute left-2.5 top-2.5 size-3.5 text-muted-foreground" />
              <input className={`${inputCls} pl-8`} value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search id, name, skill…" aria-label="Search peers" />
            </div>
            {filteredPeers.length === 0 ? (
              <EmptyState title="No peers reported" hint="Run LAN discovery or pair a manually addressed peer. An empty result is not proof that the Internet is unreachable." />
            ) : (
              <div className="space-y-2">
                {filteredPeers.map((peer) => (
                  <div key={peer.agent_id} className="rounded-xl border border-border/60 p-2.5 text-[11px]">
                    <div className="flex flex-wrap items-center gap-2">
                      <CircleDot className="size-3.5 text-primary" />
                      <span className="font-semibold">{peerLabel(peer)}</span>
                      <Badge tone={trustTone(peer.trust)}>{peer.trust}</Badge>
                      <span className="font-mono text-muted-foreground">{peer.agent_id}</span>
                    </div>
                    <p className="mt-1 text-muted-foreground">{peer.description || "No description supplied."}</p>
                    <p className="mt-1 text-muted-foreground">Skills: {peer.capabilities.length ? peer.capabilities.join(", ") : "none declared"}</p>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {peer.trust !== "blocked" && <Btn variant="ghost" disabled={busy} onClick={() => void runTrust(peer, "blocked")}><X className="size-3.5" /> Block</Btn>}
                      {peer.trust === "blocked" && <Btn variant="ghost" disabled={busy} onClick={() => void runTrust(peer, "discovered")}><Check className="size-3.5" /> Allow discovery</Btn>}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="rounded-2xl border border-border/60 bg-card/40 p-3 space-y-3">
            <div className="flex items-center gap-2"><Link2 className="size-4 text-primary" /><h3 className="text-sm font-semibold">Pair a known endpoint</h3></div>
            <p className="text-[11px] text-muted-foreground">Use a direct Gateway URL, share the remote Alpha pairing code out of band, then pair. The code is never placed in discovery or the Agent Card.</p>
            <Field label="Gateway endpoint" hint="For example http://192.168.1.20:8001">
              <input className={inputCls} value={pairForm.endpoint} onChange={(event) => setPairForm({ ...pairForm, endpoint: event.target.value })} placeholder="http://host:8001" />
            </Field>
            <Field label="Remote pairing code">
              <input className={inputCls} type="password" value={pairForm.pairing_code} onChange={(event) => setPairForm({ ...pairForm, pairing_code: event.target.value })} />
            </Field>
            <Field label="Expected agent id (optional)">
              <input className={inputCls} value={pairForm.expected_agent_id} onChange={(event) => setPairForm({ ...pairForm, expected_agent_id: event.target.value })} placeholder="alpha-…" />
            </Field>
            <Btn disabled={busy || !pairForm.endpoint.trim() || !pairForm.pairing_code.trim()} onClick={() => void runPair()}><ShieldCheck className="size-3.5" /> Pair peer</Btn>
          </div>
        </div>

        <div className="space-y-3">
          <div className="rounded-2xl border border-border/60 bg-card/40 p-3 space-y-3">
            <div className="flex items-center gap-2"><Users className="size-4 text-primary" /><h3 className="text-sm font-semibold">Create a communication topology</h3></div>
            <div className="grid gap-2 md:grid-cols-2">
              <Field label="Conversation name"><input className={inputCls} value={conversationForm.title} onChange={(event) => setConversationForm({ ...conversationForm, title: event.target.value })} /></Field>
              <Field label="Topology" hint="The Gateway validates participant counts.">
                <select className={inputCls} value={conversationForm.mode} onChange={(event) => setConversationForm({ ...conversationForm, mode: event.target.value as PeerConversationMode })}>
                  {MODES.map((mode) => <option key={mode.value} value={mode.value}>{mode.label} — {mode.hint}</option>)}
                </select>
              </Field>
            </div>
            <div>
              <div className="mb-1 text-[11px] font-semibold">Participants</div>
              {peers.length === 0 ? <p className="text-[11px] text-muted-foreground">Discover or pair peers first.</p> : (
                <div className="flex flex-wrap gap-1.5">
                  {peers.map((peer) => {
                    const selected = conversationForm.participants.includes(peer.agent_id);
                    return <button key={peer.agent_id} type="button" onClick={() => toggleParticipant(peer.agent_id)} className={`rounded-lg border px-2 py-1 text-[11px] ${selected ? "border-primary bg-primary/10 text-primary" : "border-border text-muted-foreground hover:bg-muted"}`}>{peerLabel(peer)}</button>;
                  })}
                </div>
              )}
              <p className="mt-2 text-[10px] text-muted-foreground">This Alpha installation is included automatically as the local participant.</p>
            </div>
            <Btn disabled={busy || conversationForm.participants.length === 0} onClick={() => void runCreateConversation()}><Plus className="size-3.5" /> Create conversation</Btn>
          </div>

          <div className="rounded-2xl border border-border/60 bg-card/40 p-3">
            <div className="flex items-center justify-between gap-2"><div className="flex items-center gap-2"><MessageSquare className="size-4 text-primary" /><h3 className="text-sm font-semibold">Peer sessions</h3></div><Badge tone="blue">{conversations.length}</Badge></div>
            {conversations.length === 0 ? <EmptyState title="No peer conversations" hint="Create a direct, one-to-many, many-to-one, many-to-many, or broadcast session above." /> : (
              <div className="mt-3 grid gap-2 md:grid-cols-2">
                {conversations.map((conversation) => <button key={conversation.conversation_id} type="button" onClick={() => setSelectedConversationId(conversation.conversation_id)} className={`rounded-xl border p-2.5 text-left text-[11px] ${selectedConversationId === conversation.conversation_id ? "border-primary bg-primary/5" : "border-border/60 hover:bg-muted"}`}><div className="flex items-center gap-2"><span className="font-semibold">{conversation.title}</span><Badge tone="blue">{conversation.mode}</Badge></div><p className="mt-1 text-muted-foreground">{conversation.participants.length} participants · {conversation.status}</p></button>)}
              </div>
            )}
          </div>

          {selectedConversation && (
            <div className="rounded-2xl border border-border/60 bg-card/40 p-3">
              <div className="flex items-center gap-2"><MessageSquare className="size-4 text-primary" /><h3 className="text-sm font-semibold">{selectedConversation.title}</h3><Badge tone="blue">{selectedConversation.mode}</Badge></div>
              <p className="mt-1 text-[11px] text-muted-foreground">Participants: {selectedConversation.participants.join(", ")}</p>
              <div className="mt-3 max-h-80 space-y-2 overflow-y-auto">
                {messages.length === 0 ? <EmptyState title="No messages yet" hint="Send a message to create the first server-confirmed delivery record." /> : messages.map((message) => <button key={message.message_id} type="button" onClick={() => void runRead(message)} className="w-full rounded-xl border border-border/60 p-2 text-left text-[11px] hover:bg-muted"><div className="flex flex-wrap items-center gap-2"><span className="font-semibold">{message.sender_id}</span><Badge tone={message.direction === "inbound" ? "cyan" : "blue"}>{message.direction}</Badge><span className="text-muted-foreground">{message.status}</span></div><p className="mt-1 whitespace-pre-wrap break-words">{message.text || "(structured payload)"}</p>{message.deliveries.length > 0 && <div className="mt-1 flex flex-wrap gap-1">{message.deliveries.map((delivery) => <Badge key={delivery.recipient_id} tone={delivery.status === "delivered" || delivery.status === "read" ? "green" : delivery.status === "failed" ? "red" : "amber"}>{delivery.recipient_id}: {delivery.status}</Badge>)}</div>}</button>)}
              </div>
              <div className="mt-3 flex gap-2"><input className={inputCls} value={messageText} onChange={(event) => setMessageText(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void runSend(); } }} placeholder="Message this peer session…" /><Btn disabled={busy || !messageText.trim()} onClick={() => void runSend()}><Send className="size-3.5" /> Send</Btn></div>
            </div>
          )}
        </div>
      </div>
    </Section>
  );
}

function RadioIcon() {
  return <Activity className="size-3.5" />;
}
