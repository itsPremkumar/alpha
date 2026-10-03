"use client";

// External Alpha — read every cross-installation conversation.
//
// This is the transcript/forensics companion to the "Alpha Network" tab. Alpha
// Network owns pairing and delivery control; this tab owns *reading* what the
// cross-installation plane actually exchanged, including the local Agent turns
// those remote messages produced.
//
// The honesty rules this file implements, in order of importance:
//
//  1. Two sides, never merged. A `peer_message` entry and a `local_reply` entry
//     render differently and are labelled differently, because a merged stream
//     would let a reader believe a remote peer said something the local Agent
//     said.
//  2. Untrusted text is data. Peer text is rendered as escaped text by React's
//     normal rendering path. It is never injected as raw HTML, never executed,
//     and never promoted into a prompt. It came from another machine.
//  3. "Disabled" is not "no traffic". The plane defaults OFF; an off plane must
//     say so rather than showing an empty list.
//  4. Bounded is not complete. `truncated` and `events_truncated` are rendered
//     as warnings, not swallowed.
//  5. A failed request is never an empty list.
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowDownToLine,
  CircleDot,
  Clock,
  Coins,
  Network,
  RefreshCw,
  Search,
  Wrench,
} from "lucide-react";
import { Badge, Btn, EmptyState, ErrorBox, Notice, Section, SkeletonList, inputCls } from "@/components/ui";
import {
  exportTranscript,
  getTranscript,
  getTurnForensics,
  isTranscriptEvent,
  listTranscripts,
  subscribePeerEvents,
  transcriptRoleLabel,
  type PeerStreamEvent,
  type Transcript,
  type TranscriptEntry,
  type TranscriptIndexEntry,
  type TranscriptTurnDetail,
} from "@/lib/external-alpha";
import { errMsg } from "@/lib/http";
import { clockTime, hasTime } from "@/lib/time";

type SubTab = "conversations" | "timeline" | "forensics";

const SUB_TABS: Array<{ id: SubTab; label: string }> = [
  { id: "conversations", label: "Conversations" },
  { id: "timeline", label: "Live timeline" },
  { id: "forensics", label: "Forensics" },
];

function tone(value: string | null): "green" | "amber" | "red" | "gray" | "blue" {
  if (value === "delivered" || value === "read" || value === "success" || value === "paired") return "green";
  if (value === "failed" || value === "error" || value === "blocked" || value === "interrupted") return "red";
  if (value === "queued" || value === "pending" || value === "running") return "amber";
  if (value === "delivering" || value === "peer_message") return "blue";
  return "gray";
}

function peerName(entry: TranscriptIndexEntry | Transcript): string {
  const peer = entry.peer;
  if (peer.name) return peer.name;
  if (peer.agent_id) return peer.agent_id;
  // No fabricated identity: a removed peer reads as unknown, not as blank space.
  return "Peer not reported";
}

/**
 * The conversation's own title, coerced.
 *
 * `transcript.conversation` is a raw record, so `title` is `unknown` — reading it
 * directly would render an object as a React child. Falls back to the id rather
 * than to an empty heading.
 */
function conversationTitle(transcript: Transcript, conversationId: string): string {
  const title = transcript.conversation.title;
  return typeof title === "string" && title.trim() ? title : conversationId;
}

function DownloadTranscriptButton({ conversationId }: { conversationId: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const payload = await exportTranscript(conversationId);
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `alpha-peer-transcript-${conversationId}.json`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (cause) {
      setError(errMsg(cause));
    } finally {
      setBusy(false);
    }
  }, [conversationId]);

  return (
    <>
      <Btn variant="ghost" onClick={() => void run()} disabled={busy} title="Download the full transcript as JSON">
        <ArrowDownToLine className="size-3.5" /> Export
      </Btn>
      {error && <ErrorBox message={`Export failed: ${error}`} onRetry={() => void run()} />}
    </>
  );
}

/** One transcript row. Remote text is rendered as escaped text, never as HTML. */
function TranscriptRow({ entry }: { entry: TranscriptEntry }) {
  const isPeer = entry.role === "peer_message";
  const isReply = entry.role === "local_reply";
  return (
    <div
      className={`rounded-xl border px-3 py-2 ${isPeer ? "border-border/60 bg-muted/30" : "border-primary/30 bg-card/40"}`}
      data-testid="transcript-entry"
      data-role={entry.role}
      data-side={entry.side}
    >
      <div className="flex items-center gap-2 flex-wrap mb-1">
        <Badge tone={isPeer ? "blue" : "green"} title={isPeer ? "Authored at the remote Alpha installation" : "Produced by this installation's Agent"}>
          {transcriptRoleLabel(entry.role)}
        </Badge>
        <Badge tone="gray">{entry.kind}</Badge>
        {entry.status && <Badge tone={tone(entry.status)}>{entry.status}</Badge>}
        {entry.sender_id && <span className="text-[10px] text-muted-foreground font-mono">{entry.sender_id}</span>}
        {hasTime(entry.created_at) && (
          <span className="text-[10px] text-muted-foreground ml-auto">
            <Clock className="size-2.5 inline" /> {clockTime(entry.created_at)}
          </span>
        )}
      </div>

      {entry.text ? (
        <div className="text-[12px] leading-relaxed whitespace-pre-wrap break-words">{entry.text}</div>
      ) : (
        <div className="text-[11px] text-muted-foreground italic">No text on this entry.</div>
      )}

      {/* Tool receipts are shown as activity, never as the Agent's own words. */}
      {entry.tool_calls && entry.tool_calls.length > 0 && (
        <div className="mt-1.5 space-y-1">
          {entry.tool_calls.map((call) => (
            <div key={call.id ?? call.name ?? "tool"} className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
              <Wrench className="size-2.5" />
              <span className="font-mono">{call.name ?? "tool"}</span>
              {call.status && <Badge tone={tone(call.status)}>{call.status}</Badge>}
            </div>
          ))}
        </div>
      )}

      {entry.latency_ms != null && (
        <div className="mt-1 text-[10px] text-muted-foreground">{entry.latency_ms} ms</div>
      )}

      {/* One row per recipient: a fan-out is not a single delivery claim. */}
      {entry.deliveries.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {entry.deliveries.map((delivery) => (
            <Badge key={delivery.recipient_id ?? "recipient"} tone={tone(delivery.status)} title={delivery.error ?? undefined}>
              {delivery.recipient_id ?? "recipient"}: {delivery.status ?? "not reported"}
            </Badge>
          ))}
        </div>
      )}

      {entry.delivery_error && (
        <div className="mt-1 text-[10px] text-destructive flex items-center gap-1">
          <AlertTriangle className="size-2.5" /> {entry.delivery_error}
        </div>
      )}

      {isReply && entry.run_id && (
        <div className="mt-1 text-[10px] text-muted-foreground font-mono">
          run {entry.run_id}
          {entry.thread_id ? ` · thread ${entry.thread_id}` : ""}
        </div>
      )}
    </div>
  );
}

function TurnForensics({ conversationId, runId }: { conversationId: string; runId: string }) {
  const [turn, setTurn] = useState<TranscriptTurnDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setTurn(await getTurnForensics(conversationId, runId));
      setError(null);
    } catch (cause) {
      setError(errMsg(cause));
    } finally {
      setLoading(false);
    }
  }, [conversationId, runId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading) return <SkeletonList rows={3} />;
  if (error) return <ErrorBox message={`Turn detail: ${error}`} onRetry={() => void load()} />;
  if (!turn) return null;

  return (
    <div className="space-y-2">
      <div className="grid gap-2 md:grid-cols-4">
        <div className="rounded-xl border border-border/60 bg-card/40 px-3 py-2">
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground">Status</div>
          <Badge tone={tone(turn.status)}>{turn.status ?? "not reported"}</Badge>
        </div>
        <div className="rounded-xl border border-border/60 bg-card/40 px-3 py-2">
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground">Model</div>
          <div className="text-[11px] font-mono">{turn.model_name ?? "not reported"}</div>
        </div>
        <div className="rounded-xl border border-border/60 bg-card/40 px-3 py-2">
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground flex items-center gap-1">
            <Coins className="size-2.5" /> Tokens
          </div>
          <div className="text-[11px]">
            {turn.total_tokens} total · {turn.total_input_tokens} in / {turn.total_output_tokens} out
          </div>
          <div className="text-[10px] text-muted-foreground">{turn.llm_call_count} model calls</div>
        </div>
        <div className="rounded-xl border border-border/60 bg-card/40 px-3 py-2">
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground">Messages</div>
          <div className="text-[11px]">{turn.message_count}</div>
        </div>
      </div>

      {Object.keys(turn.token_usage_by_model).length > 0 && (
        <div className="rounded-xl border border-border/60 bg-card/40 px-3 py-2">
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">Tokens by model</div>
          {Object.entries(turn.token_usage_by_model).map(([model, counts]) => (
            <div key={model} className="text-[11px] font-mono flex justify-between gap-2">
              <span>{model}</span>
              <span>
                {counts.total_tokens} ({counts.input_tokens} in / {counts.output_tokens} out)
              </span>
            </div>
          ))}
        </div>
      )}

      {turn.error && <ErrorBox message={`Run reported an error: ${turn.error}`} />}

      {/* Truncation is stated, never implied. */}
      {turn.events_truncated && (
        <Notice
          message={`Showing ${turn.events_shown} of ${turn.events_total} stored events. This turn is only partially rendered; the full stream is on the run inspector.`}
        />
      )}

      <div className="space-y-1.5">
        {turn.events.length === 0 ? (
          <EmptyState title="No local events stored" hint="The run may predate event persistence, or its history may have been pruned. This is not proof the turn produced no output." />
        ) : (
          turn.events.map((event) => <TranscriptRow key={event.entry_id} entry={event} />)
        )}
      </div>
    </div>
  );
}

export function ExternalAlphaSection() {
  const [tab, setTab] = useState<SubTab>("conversations");
  const [index, setIndex] = useState<TranscriptIndexEntry[]>([]);
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [transcript, setTranscript] = useState<Transcript | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [live, setLive] = useState(false);
  const [events, setEvents] = useState<PeerStreamEvent[]>([]);
  const [gaps, setGaps] = useState<string[]>([]);
  const cleanupRef = useRef<(() => void) | null>(null);

  const refresh = useCallback(async (quiet = false) => {
    if (!quiet) setRefreshing(true);
    try {
      const next = await listTranscripts();
      setIndex(next.transcripts);
      setEnabled(next.enabled);
      setError(null);
    } catch (cause) {
      // A failed request must surface, never degrade into an empty list.
      setError(errMsg(cause));
    } finally {
      setLoading(false);
      if (!quiet) setRefreshing(false);
    }
  }, []);

  const loadTranscript = useCallback(async (conversationId: string) => {
    try {
      setTranscript(await getTranscript(conversationId));
      setError(null);
    } catch (cause) {
      setError(errMsg(cause));
    }
  }, []);

  useEffect(() => {
    void refresh();
    // Polling is the fallback; the SSE reader below is the live path. The poll is
    // paused while the document is hidden so a backgrounded tab is not polling.
    const timer = window.setInterval(() => {
      if (!document.hidden) void refresh(true);
    }, 10000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    if (selectedId) void loadTranscript(selectedId);
    else setTranscript(null);
  }, [loadTranscript, selectedId]);

  // The live stream is opt-in and always cleaned up: a leaked reader holds a
  // Gateway connection open and the server-side queue then drops events.
  useEffect(() => {
    if (!live) return;
    const unsubscribe = subscribePeerEvents(
      (event) => {
        if (isTranscriptEvent(event.type)) {
          setEvents((previous) => [...previous.slice(-199), event]);
          void refresh(true);
          if (selectedId) void loadTranscript(selectedId);
        }
      },
      (event) => {
        setGaps((previous) => [
          ...previous.slice(-9),
          event.type === "stream.overflow"
            ? "Some live events were dropped because this client fell behind. The transcript below was re-fetched from the server."
            : "Live event history was reset. The transcript below was re-fetched from the server.",
        ]);
        void refresh(true);
      },
    );
    cleanupRef.current = unsubscribe;
    return () => {
      unsubscribe();
      cleanupRef.current = null;
    };
  }, [live, refresh, loadTranscript, selectedId]);

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return index;
    return index.filter((entry) =>
      [entry.title, entry.conversation_id, peerName(entry), ...entry.participants].join(" ").toLowerCase().includes(needle),
    );
  }, [index, search]);

  const replyRuns = useMemo(
    () => (transcript ? transcript.entries.filter((entry) => entry.role === "local_reply" && entry.run_id).map((entry) => entry.run_id as string) : []),
    [transcript],
  );

  if (loading) {
    return (
      <Section title="External Alpha" hint="Reading cross-installation conversations between this Alpha and other Alpha installations.">
        <SkeletonList rows={5} />
      </Section>
    );
  }

  return (
    <Section
      title="External Alpha"
      hint="Every conversation this Alpha exchanged with another Alpha installation, with the local reply to each remote message."
      actions={
        <>
          <Btn variant="ghost" onClick={() => void refresh()} disabled={refreshing} title="Re-read from the Gateway">
            <RefreshCw className="size-3.5" /> Refresh
          </Btn>
          {/* Guarded: exporting with no conversation selected would request an
              empty id and surface a meaningless 404. */}
          {selectedId && <DownloadTranscriptButton conversationId={selectedId} />}
        </>
      }
    >
      {notice && <Notice message={notice} />}
      {error && <ErrorBox message={`External Alpha: ${error}`} onRetry={() => void refresh()} />}

      {/* "Off" is not "empty". The plane defaults disabled. */}
      {enabled === false && (
        <Notice message="The Alpha peer network is disabled on this installation (ALPHA_PEER_NETWORK_ENABLED is not set). An empty transcript below means the plane is off, not that no peers exist." />
      )}

      <div className="flex gap-1 rounded-xl bg-muted/60 p-1 w-fit">
        {SUB_TABS.map((entry) => (
          <button
            key={entry.id}
            type="button"
            onClick={() => setTab(entry.id)}
            className={`px-3 py-1.5 rounded-lg text-[11px] font-semibold ${
              tab === entry.id ? "bg-card shadow" : "text-muted-foreground hover:text-foreground"
            }`}
          >
            {entry.label}
          </button>
        ))}
      </div>

      {tab === "conversations" && (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)]">
          <div className="space-y-2">
            <div className="flex items-center justify-between gap-2">
              <span className="text-[11px] font-semibold">Conversations ({filtered.length})</span>
              <div className="relative">
                <Search className="size-3.5 absolute left-2.5 top-2.5 text-muted-foreground" />
                <input
                  className={`${inputCls} pl-8`}
                  placeholder="Search conversations"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  aria-label="Search cross-installation conversations"
                />
              </div>
            </div>
            {filtered.length === 0 ? (
              <EmptyState
                title={index.length === 0 ? "No cross-installation conversations" : "No conversations match that search"}
                hint="This is not proof that no peer is reachable. Pair an installation in the Alpha Network tab, then send a message."
              />
            ) : (
              filtered.map((entry) => (
                <button
                  key={entry.conversation_id}
                  type="button"
                  onClick={() => setSelectedId(entry.conversation_id)}
                  className={`w-full text-left rounded-xl border px-3 py-2 ${
                    selectedId === entry.conversation_id ? "border-primary/50 bg-card/60" : "border-border/60 bg-card/30"
                  }`}
                >
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-[12px] font-semibold">{entry.title}</span>
                    <Badge tone="gray">{entry.mode}</Badge>
                    <Badge tone={tone(entry.status)}>{entry.status}</Badge>
                  </div>
                  <div className="text-[10px] text-muted-foreground mt-1 flex items-center gap-2 flex-wrap">
                    <span className="font-mono">{peerName(entry)}</span>
                    <span>· {entry.counts.messages} messages</span>
                    {entry.peer.trust && <Badge tone={tone(entry.peer.trust)}>{entry.peer.trust}</Badge>}
                  </div>
                </button>
              ))
            )}
          </div>

          <div className="space-y-2">
            {!selectedId ? (
              <EmptyState title="Select a conversation" hint="Its full cross-installation transcript appears here, newest last." />
            ) : !transcript ? (
              <SkeletonList rows={4} />
            ) : (
              <>
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-[12px] font-semibold">{conversationTitle(transcript, selectedId)}</span>
                  <Badge tone="blue">{peerName(transcript)}</Badge>
                  <Badge tone="gray">
                    {transcript.counts.messages} messages · {transcript.counts.turns} local turns
                  </Badge>
                </div>
                {/* A bounded response must say it is bounded. */}
                {transcript.truncated && (
                  <Notice message="This transcript is longer than the read limit, so it is only partially shown. Export it for the complete record." />
                )}
                <div className="space-y-1.5 max-h-[32rem] overflow-y-auto pr-1">
                  {transcript.entries.length === 0 ? (
                    <EmptyState
                      title="No transcript entries"
                      hint="An empty transcript means nothing was recorded for this conversation. It is not proof that no message was exchanged."
                    />
                  ) : (
                    transcript.entries.map((entry) => <TranscriptRow key={entry.entry_id} entry={entry} />)
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {tab === "timeline" && (
        <div className="space-y-2">
          <div className="flex items-center gap-2 flex-wrap">
            <Btn variant={live ? "primary" : "ghost"} onClick={() => setLive((previous) => !previous)}>
              <CircleDot className="size-3.5" /> {live ? "Live — stop" : "Go live"}
            </Btn>
            <Badge tone={live ? "green" : "gray"}>{live ? "Streaming" : "Polling every 10s"}</Badge>
            <Badge tone="gray">{events.length} events</Badge>
          </div>
          {gaps.map((gap, position) => (
            <Notice key={`${gap}-${position}`} message={gap} />
          ))}
          {events.length === 0 ? (
            <EmptyState
              title="No live events yet"
              hint="Go live to stream peer-network events from this Gateway. Events are buffered per process, so a reconnect may need a re-fetch."
            />
          ) : (
            <div className="space-y-1 max-h-[28rem] overflow-y-auto pr-1">
              {events
                .slice()
                .reverse()
                .map((event, position) => (
                  <div key={`${event.seq ?? position}-${position}`} className="rounded-xl border border-border/60 bg-card/30 px-3 py-1.5">
                    <div className="flex items-center gap-2 flex-wrap text-[10px]">
                      <Badge tone="blue">{event.type}</Badge>
                      {event.seq != null && <span className="font-mono text-muted-foreground">#{event.seq}</span>}
                      {hasTime(event.at) && <span className="text-muted-foreground ml-auto">{clockTime(event.at)}</span>}
                    </div>
                    <div className="text-[11px] font-mono mt-0.5 break-words text-muted-foreground">
                      {Object.keys(event.data).length === 0 ? "no payload" : JSON.stringify(event.data)}
                    </div>
                  </div>
                ))}
            </div>
          )}
        </div>
      )}

      {tab === "forensics" && (
        <div className="space-y-2">
          {!selectedId ? (
            <EmptyState title="Select a conversation first" hint="Forensics shows one local Agent turn in full: events, tool calls, tokens and errors." />
          ) : replyRuns.length === 0 ? (
            <EmptyState
              title="No local Agent turns in this conversation"
              hint="Nothing here is proof the remote message was ignored. A peer only starts a local turn when the operator granted that peer auto-reply."
            />
          ) : (
            <>
              <div className="flex items-center gap-2 flex-wrap">
                <Network className="size-3.5 text-muted-foreground" />
                <span className="text-[11px] font-semibold">Local turns ({replyRuns.length})</span>
              </div>
              {replyRuns.map((runId) => (
                <div key={runId} className="space-y-1">
                  <div className="text-[10px] font-mono text-muted-foreground">{runId}</div>
                  <TurnForensics conversationId={selectedId} runId={runId} />
                </div>
              ))}
            </>
          )}
        </div>
      )}
    </Section>
  );
}

export default ExternalAlphaSection;