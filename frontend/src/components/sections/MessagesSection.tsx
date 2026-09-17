"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  listRooms, getRoom, createRoom, postToRoom, deleteRoom, startRoomRun, listRoomRuns, cancelRoomRun,
  listDmThreads, ensureRosterAgent, unreadCount, markSeen, senderColor, kindTone,
  MESSAGE_KINDS, rollCall, OPERATOR, ChatMsg, DmThread,
} from "@/lib/comm";
import { fetchRoster } from "@/lib/inbox";
import { sendAgentMessage } from "@/lib/inbox";
import { runCouncil, CouncilStrategy } from "@/lib/deliberation";
import { orgEvents } from "@/lib/teamops";
import { Section, EmptyState, ErrorBox, Btn, Badge, Field, SkeletonList, inputCls } from "@/components/ui";
import { errMsg } from "@/lib/http";
import {
  Search, Users, User, Plus, Send, ArrowLeft, Info, X, Check, CheckCheck,
  Play, Ban, Trash2, Scale, RefreshCw, Pause,
} from "lucide-react";

type Filter = "all" | "unread" | "groups" | "direct" | "decisions" | "blockers";
type Selection = { kind: "group"; name: string } | { kind: "dm"; peer: string };

interface Conv {
  id: string;
  kind: "group" | "dm";
  title: string;
  subtitle: string;
  lastText: string;
  lastAt: string;
  unread: number;
  members: string[];
}

export function MessagesSection(props: { threadId: string | null; botNames: string[] }) {
  const [filter, setFilter] = useState<Filter>("all");
  const [search, setSearch] = useState("");
  const [rooms, setRooms] = useState<Array<{ name: string; members: string[]; status: string }>>([]);
  const [dms, setDms] = useState<DmThread[]>([]);
  const [roomMsgs, setRoomMsgs] = useState<Record<string, ChatMsg[]>>({});
  const [roster, setRoster] = useState<Array<{ name: string; role: string; status: string }>>([]);
  const [presence, setPresence] = useState<Array<{ name: string; status: string; detail: string }>>([]);
  const [events, setEvents] = useState<Array<Record<string, unknown>>>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sel, setSel] = useState<Selection | null>(null);
  const [draft, setDraft] = useState("");
  const [kind, setKind] = useState<string>("discussion");
  const [sending, setSending] = useState(false);
  const [showDetails, setShowDetails] = useState(true);
  const [showNewGroup, setShowNewGroup] = useState(false);
  const [newGroup, setNewGroup] = useState({ name: "", members: "" });
  const [newDm, setNewDm] = useState("");
  const [live, setLive] = useState(false);
  const [council, setCouncil] = useState<{ topic: string; strategy: CouncilStrategy; busy: boolean; result: string | null }>({ topic: "", strategy: "debate", busy: false, result: null });
  const bottomRef = useRef<HTMLDivElement>(null);
  const timer = useRef<number | null>(null);

  const load = async (quiet = false) => {
    if (!quiet) setLoading(true);
    setError(null);
    try {
      const [r, p, e] = await Promise.all([
        listRooms(),
        rollCall(),
        orgEvents(15).catch(() => []),
      ]);
      setRooms(r);
      setPresence(p);
      setEvents(e);
      if (props.threadId) {
        const [threads, rost] = await Promise.all([
          listDmThreads(props.threadId).catch(() => [] as DmThread[]),
          fetchRoster(props.threadId).catch(() => []),
        ]);
        setDms(threads);
        setRoster(rost);
      } else {
        setDms([]);
        setRoster([]);
      }
    } catch (e) {
      if (!quiet) setError(errMsg(e));
    } finally {
      if (!quiet) setLoading(false);
    }
  };

  useEffect(() => {
    setSel(null);
    setRoomMsgs({});
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.threadId]);

  useEffect(() => {
    if (live) timer.current = window.setInterval(() => load(true), 10000);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live, props.threadId]);

  const openRoom = async (name: string) => {
    setSel({ kind: "group", name });
    try {
      const room = await getRoom(name);
      setRoomMsgs((prev) => ({ ...prev, [name]: room.messages }));
      markSeen(`group:${name}`, room.messages);
    } catch (e) {
      setError(errMsg(e));
    }
  };

  const openDm = (peer: string, messages: ChatMsg[]) => {
    setSel({ kind: "dm", peer });
    markSeen(`dm:${peer}`, messages);
  };

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [sel, roomMsgs, dms]);

  const convs: Conv[] = useMemo(() => {
    const list: Conv[] = rooms.map((r) => {
      const msgs = roomMsgs[r.name] || [];
      const last = msgs[msgs.length - 1];
      return {
        id: `group:${r.name}`,
        kind: "group" as const,
        title: `# ${r.name}`,
        subtitle: `${r.members.length} members${r.status ? ` • ${r.status}` : ""}`,
        lastText: last ? `${last.sender}: ${last.content.slice(0, 80)}` : "No messages yet",
        lastAt: last?.at || "",
        unread: unreadCount(`group:${r.name}`, msgs),
        members: r.members,
      };
    });
    for (const d of dms) {
      const last = d.messages[d.messages.length - 1];
      list.push({
        id: d.id,
        kind: "dm" as const,
        title: d.peer,
        subtitle: roster.find((x) => x.name === d.peer)?.status || "direct thread",
        lastText: last ? last.content.slice(0, 80) : "No messages yet",
        lastAt: last?.at || "",
        unread: unreadCount(d.id, d.messages),
        members: [OPERATOR, d.peer],
      });
    }
    const q = search.trim().toLowerCase();
    return list
      .filter((c) => {
        if (filter === "groups" && c.kind !== "group") return false;
        if (filter === "direct" && c.kind !== "dm") return false;
        if (filter === "unread" && c.unread === 0) return false;
        if (filter === "decisions" || filter === "blockers") {
          const msgs = c.kind === "group" ? roomMsgs[c.title.slice(2)] || [] : dms.find((d) => d.id === c.id)?.messages || [];
          const want = filter === "decisions" ? ["decision"] : ["blocker", "warning", "escalation"];
          if (!msgs.some((m) => want.includes(m.kind))) return false;
        }
        if (q && !`${c.title} ${c.lastText}`.toLowerCase().includes(q)) return false;
        return true;
      })
      .sort((a, b) => (b.lastAt || "").localeCompare(a.lastAt || ""));
  }, [rooms, dms, roomMsgs, roster, filter, search]);

  const activeMsgs: ChatMsg[] = sel
    ? sel.kind === "group"
      ? roomMsgs[sel.name] || []
      : dms.find((d) => d.peer === sel.peer)?.messages || []
    : [];

  const activeTitle = sel ? (sel.kind === "group" ? `# ${sel.name}` : sel.peer) : "";
  const activeMembers = sel
    ? sel.kind === "group"
      ? rooms.find((r) => r.name === sel.name)?.members || []
      : [OPERATOR, sel.peer]
    : [];

  const totalUnread = convs.reduce((n, c) => n + Math.min(c.unread, 99), 0);

  const send = async () => {
    if (!draft.trim() || sending) return;
    if (sel?.kind === "group") {
      setSending(true);
      try {
        await postToRoom(sel.name, OPERATOR, draft.trim(), kind);
        setDraft("");
        await openRoom(sel.name);
      } catch (e) {
        setError(errMsg(e));
      } finally {
        setSending(false);
      }
    } else if (sel?.kind === "dm" && props.threadId) {
      setSending(true);
      try {
        await sendAgentMessage(props.threadId, OPERATOR, sel.peer, draft.trim());
        setDraft("");
        await load(true);
      } catch (e) {
        setError(errMsg(e));
      } finally {
        setSending(false);
      }
    }
  };

  const onCouncil = async () => {
    if (council.topic.trim().length < 2 || council.busy) return;
    setCouncil((c) => ({ ...c, busy: true, result: null }));
    try {
      const verdict = await runCouncil(council.topic.trim(), council.strategy, 3);
      setCouncil((c) => ({ ...c, busy: false, result: verdict }));
    } catch (e) {
      setError(errMsg(e));
      setCouncil((c) => ({ ...c, busy: false }));
    }
  };

  const postVerdict = async () => {
    if (sel?.kind !== "group" || !council.result) return;
    try {
      await postToRoom(sel.name, OPERATOR, `Council verdict (${council.strategy}):\n${council.result.slice(0, 1500)}`, "decision");
      setCouncil({ topic: "", strategy: "debate", busy: false, result: null });
      await openRoom(sel.name);
    } catch (e) {
      setError(errMsg(e));
    }
  };

  const fmtTime = (at: string) => {
    if (!at) return "";
    const d = new Date(at);
    if (isNaN(d.getTime())) return "";
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  };

  const dayLabel = (at: string) => {
    if (!at) return "";
    const d = new Date(at);
    if (isNaN(d.getTime())) return "";
    const today = new Date();
    const y = new Date();
    y.setDate(y.getDate() - 1);
    if (d.toDateString() === today.toDateString()) return "Today";
    if (d.toDateString() === y.toDateString()) return "Yesterday";
    return d.toLocaleDateString();
  };

  let lastDay = "";

  return (
    <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
      <div className="max-w-none w-full flex-1 flex min-h-0">
        {/* ── Conversation list ── */}
        <aside className={`w-full md:w-80 shrink-0 border-r border-border bg-card/40 flex-col min-h-0 ${sel ? "hidden md:flex" : "flex"}`} aria-label="Conversations">
          <div className="p-3 space-y-2 border-b border-border/60">
            <div className="flex items-center gap-2">
              <h2 className="text-base font-semibold tracking-tight flex-1">
                Messages {totalUnread > 0 && <span className="ml-1 text-[10px] px-1.5 py-0.5 rounded-full bg-primary text-primary-foreground font-bold">{totalUnread}</span>}
              </h2>
              <Btn variant="ghost" onClick={() => setLive((v) => !v)} title="Auto-refresh every 10 seconds">
                {live ? <Pause className="size-3.5" /> : <Play className="size-3.5" />} {live ? "Live" : "Poll"}
              </Btn>
              <button type="button" onClick={() => load()} className="p-2 rounded-lg hover:bg-muted text-muted-foreground" title="Refresh">
                <RefreshCw className="size-4" />
              </button>
            </div>
            <div className="relative">
              <Search className="size-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground" />
              <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search chats…" aria-label="Search chats" className={`${inputCls} pl-8`} />
            </div>
            <div className="flex gap-1 flex-wrap">
              {(["all", "unread", "groups", "direct", "decisions", "blockers"] as Filter[]).map((f) => (
                <button
                  key={f}
                  type="button"
                  onClick={() => setFilter(f)}
                  className={`text-[11px] px-2.5 py-1 rounded-full font-semibold ${filter === f ? "bg-primary text-primary-foreground" : "bg-muted/60 text-muted-foreground hover:text-foreground"}`}
                >
                  {f[0].toUpperCase() + f.slice(1)}
                </button>
              ))}
            </div>
            <div className="flex gap-2">
              <Btn variant="ghost" onClick={() => setShowNewGroup((v) => !v)}>
                <Plus className="size-3.5" /> New group
              </Btn>
            </div>
            {showNewGroup && (
              <div className="rounded-xl border border-border/60 p-2.5 space-y-2 bg-card">
                <Field label="Group name">
                  <input value={newGroup.name} onChange={(e) => setNewGroup({ ...newGroup, name: e.target.value })} placeholder="project-atlas" className={`${inputCls} font-mono`} />
                </Field>
                <Field label="Members (comma-separated bot names)">
                  <input value={newGroup.members} onChange={(e) => setNewGroup({ ...newGroup, members: e.target.value })} placeholder="architect, backend, qa" className={inputCls} />
                </Field>
                <Btn
                  onClick={() => {
                    if (!newGroup.name.trim()) return;
                    createRoom(newGroup.name.trim(), newGroup.members.split(",").map((m) => m.trim()).filter(Boolean))
                      .then(() => {
                        setNewGroup({ name: "", members: "" });
                        setShowNewGroup(false);
                        load();
                      })
                      .catch((e) => setError(errMsg(e)));
                  }}
                  disabled={!newGroup.name.trim()}
                >
                  Create group
                </Btn>
              </div>
            )}
            <div className="flex gap-2">
              <input value={newDm} onChange={(e) => setNewDm(e.target.value)} onKeyDown={(e) => e.key === "Enter" && newDm.trim() && props.threadId && ensureRosterAgent(props.threadId, newDm.trim()).then(() => { setNewDm(""); load(); }).catch((er) => setError(errMsg(er)))} placeholder="Message an agent directly…" aria-label="Start direct chat" className={inputCls} />
            </div>
            {!props.threadId && (
              <p className="text-[11px] text-muted-foreground">Direct chats live inside a conversation — open or start a chat to unlock them. Groups work anytime.</p>
            )}
          </div>

          <div className="flex-1 overflow-y-auto">
            {loading ? (
              <div className="p-3"><SkeletonList rows={5} /></div>
            ) : error && convs.length === 0 ? (
              <div className="p-3"><ErrorBox message={error} onRetry={() => load()} /></div>
            ) : convs.length === 0 ? (
              <div className="p-3">
                <EmptyState title="No chats yet" hint="Create a group above, or message an agent directly once a chat is open." />
              </div>
            ) : (
              convs.map((c) => {
                const isActive = sel && ((sel.kind === "group" && c.id === `group:${sel.name}`) || (sel.kind === "dm" && c.id === `dm:${sel.peer}`));
                return (
                  <button
                    key={c.id}
                    type="button"
                    onClick={() => (c.kind === "group" ? openRoom(c.title.slice(2)) : openDm(c.title, dms.find((d) => d.id === c.id)?.messages || []))}
                    className={`w-full flex items-center gap-3 px-3 py-2.5 text-left border-b border-border/40 hover:bg-muted/40 ${isActive ? "bg-primary/10" : ""}`}
                  >
                    <span className="size-10 rounded-full flex items-center justify-center text-sm font-bold shrink-0 text-white" style={{ backgroundColor: c.kind === "group" ? "#5566ff" : senderColor(c.title) }}>
                      {c.kind === "group" ? <Users className="size-4" /> : c.title.slice(0, 2).toUpperCase()}
                    </span>
                    <span className="flex-1 min-w-0">
                      <span className="flex items-center gap-1.5">
                        <span className="text-xs font-semibold truncate flex-1">{c.title}</span>
                        <span className="text-[10px] text-muted-foreground shrink-0">{fmtTime(c.lastAt)}</span>
                      </span>
                      <span className="flex items-center gap-1.5">
                        <span className="text-[11px] text-muted-foreground truncate flex-1">{c.subtitle} — {c.lastText || "…"}</span>
                        {c.unread > 0 && (
                          <span className="text-[10px] font-bold bg-emerald-500 text-white rounded-full min-w-5 h-5 inline-flex items-center justify-center px-1 shrink-0">
                            {c.unread > 99 ? "99+" : c.unread}
                          </span>
                        )}
                      </span>
                    </span>
                  </button>
                );
              })
            )}
          </div>
        </aside>

        {/* ── Chat window ── */}
        <section className={`flex-1 flex-col min-w-0 min-h-0 bg-background ${sel ? "flex" : "hidden md:flex"}`} aria-label="Messages">
          {!sel ? (
            <div className="flex-1 flex items-center justify-center p-6">
              <EmptyState title="Pick a conversation" hint="Choose a group or a direct thread on the left — like WhatsApp, everything lands here." />
            </div>
          ) : (
            <>
              <header className="shrink-0 px-4 py-2.5 border-b border-border/60 bg-card/40 flex items-center gap-2.5">
                <button type="button" onClick={() => setSel(null)} className="md:hidden p-1.5 rounded-lg hover:bg-muted" aria-label="Back to chats">
                  <ArrowLeft className="size-4" />
                </button>
                <span className="size-9 rounded-full flex items-center justify-center text-sm font-bold text-white shrink-0" style={{ backgroundColor: sel.kind === "group" ? "#5566ff" : senderColor(activeTitle) }}>
                  {sel.kind === "group" ? <Users className="size-4" /> : activeTitle.slice(0, 2).toUpperCase()}
                </span>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-semibold truncate">{activeTitle}</p>
                  <p className="text-[11px] text-muted-foreground truncate">
                    {sel.kind === "group" ? `${activeMembers.length} participants • tap ⓘ for presence & decisions` : "direct thread — private between you two"}
                  </p>
                </div>
                <button type="button" onClick={() => setShowDetails((v) => !v)} className="p-2 rounded-lg hover:bg-muted text-muted-foreground" title="Details" aria-label="Toggle details">
                  <Info className="size-4" />
                </button>
              </header>

              <div className="flex-1 overflow-y-auto px-4 sm:px-8 py-4 space-y-1.5">
                {activeMsgs.length === 0 ? (
                  <div className="h-full flex items-center justify-center">
                    <EmptyState title="No messages yet" hint="Say hello below — agents reply here and the run history stays attached." />
                  </div>
                ) : (
                  activeMsgs.map((m, i) => {
                    const mine = m.sender === OPERATOR;
                    const day = dayLabel(m.at);
                    const showDay = day && day !== lastDay;
                    if (showDay) lastDay = day;
                    return (
                      <React.Fragment key={`${m.id}-${i}`}>
                        {showDay && (
                          <div className="flex justify-center py-2">
                            <span className="text-[10px] font-semibold px-2.5 py-1 rounded-lg bg-muted text-muted-foreground">{day}</span>
                          </div>
                        )}
                        {(() => {
                          const dmMatch = m.content?.match(/^\[DM from ([^\]]+)\]\s*([\s\S]*)$/);
                          const isA2A = Boolean(dmMatch);
                          const a2aSender = dmMatch ? dmMatch[1] : null;
                          const cleanContent = dmMatch ? dmMatch[2] : m.content;

                          return (
                            <div className={`flex ${mine ? "justify-end" : "justify-start"}`}>
                              <div className={`max-w-[80%] sm:max-w-[70%] rounded-2xl px-3 py-2 shadow-sm ${
                                mine
                                  ? "bg-emerald-600/90 text-white rounded-br-md"
                                  : isA2A
                                    ? "bg-card border border-blue-500/40 rounded-bl-md shadow-blue-500/5"
                                    : "bg-card border border-border/60 rounded-bl-md"
                              }`}>
                                {!mine && (
                                  <div className="flex items-center gap-1.5 flex-wrap">
                                    <p className="text-[11px] font-bold" style={{ color: senderColor(m.sender) }}>
                                      {m.sender}
                                    </p>
                                    {isA2A && (
                                      <span className="text-[9px] font-mono px-1 py-0.2 rounded bg-blue-500/15 text-blue-400">
                                        ⚡ from @{a2aSender}
                                      </span>
                                    )}
                                  </div>
                                )}
                                {m.kind !== "discussion" && (
                                  <span className={`inline-block text-[10px] font-bold px-1.5 py-0.5 rounded-md mt-0.5 mb-1 ${kindTone(m.kind) === "green" ? "bg-emerald-500/15 text-emerald-600" : kindTone(m.kind) === "amber" ? "bg-amber-500/15 text-amber-600" : kindTone(m.kind) === "blue" ? "bg-primary/15 text-primary" : "bg-muted text-muted-foreground"}`}>
                                    {m.kind.replace(/_/g, " ").toUpperCase()}
                                  </span>
                                )}
                                <p className="text-[13px] leading-relaxed whitespace-pre-wrap break-words">{cleanContent}</p>
                                <p className={`text-[10px] mt-1 flex items-center gap-1 justify-end ${mine ? "text-white/70" : "text-muted-foreground"}`}>
                                  {fmtTime(m.at)}
                                  {mine && (m.read ? <CheckCheck className="size-3" /> : <Check className="size-3" />)}
                                </p>
                              </div>
                            </div>
                          );
                        })()}
                      </React.Fragment>
                    );
                  })
                )}
                <div ref={bottomRef} />
              </div>

              <footer className="shrink-0 p-3 border-t border-border/60 bg-card/40">
                <div className="flex items-center gap-2 max-w-3xl mx-auto">
                  <select value={kind} onChange={(e) => setKind(e.target.value)} className="text-[11px] bg-muted/60 border border-border/70 rounded-xl px-2 py-2.5 font-semibold cursor-pointer focus:outline-none focus:ring-1 focus:ring-primary/40 shrink-0" title="Message type" aria-label="Message type">
                    {MESSAGE_KINDS.map((k) => (
                      <option key={k} value={k}>{k.replace(/_/g, " ")}</option>
                    ))}
                  </select>
                  <input
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && send()}
                    placeholder={`Message ${sel.kind === "group" ? "the group" : activeTitle}…`}
                    aria-label="Write a message"
                    className={`${inputCls} !rounded-full !py-2.5`}
                  />
                  <button
                    type="button"
                    onClick={send}
                    disabled={!draft.trim() || sending}
                    className="size-10 rounded-full bg-emerald-600 text-white flex items-center justify-center shrink-0 disabled:opacity-40 hover:opacity-90"
                    title="Send"
                    aria-label="Send message"
                  >
                    <Send className="size-4" />
                  </button>
                </div>
              </footer>
            </>
          )}
        </section>

        {/* ── Details pane ── */}
        {sel && showDetails && (
          <DetailsPane
            sel={sel}
            members={activeMembers}
            messages={activeMsgs}
            roster={roster}
            presence={presence}
            events={events}
            botNames={props.botNames}
            onClose={() => setShowDetails(false)}
            onError={setError}
            onRefresh={() => (sel.kind === "group" ? openRoom(sel.name) : load(true))}
            onDeleteRoom={async () => {
              if (sel.kind !== "group") return;
              if (!window.confirm(`Delete group "${sel.name}" and its history?`)) return;
              try {
                await deleteRoom(sel.name);
                setSel(null);
                load();
              } catch (e) {
                setError(errMsg(e));
              }
            }}
            onAutoRun={async (objective: string) => {
              if (sel.kind !== "group") return;
              await startRoomRun(sel.name, objective);
              await openRoom(sel.name);
            }}
            onCancelRun={async (runId: string) => {
              if (sel.kind !== "group") return;
              await cancelRoomRun(sel.name, runId);
              await openRoom(sel.name);
            }}
            council={council}
            setCouncil={setCouncil}
            onCouncil={onCouncil}
            onPostVerdict={postVerdict}
          />
        )}
      </div>
      {error && (
        <div className="shrink-0 p-2">
          <ErrorBox message={error} onRetry={() => load()} />
        </div>
      )}
    </div>
  );
}

function DetailsPane(props: {
  sel: { kind: "group"; name: string } | { kind: "dm"; peer: string };
  members: string[];
  messages: ChatMsg[];
  roster: Array<{ name: string; role: string; status: string }>;
  presence: Array<{ name: string; status: string; detail: string }>;
  events: Array<Record<string, unknown>>;
  botNames: string[];
  onClose: () => void;
  onError: (m: string) => void;
  onRefresh: () => void;
  onDeleteRoom: () => void;
  onAutoRun: (objective: string) => Promise<void>;
  onCancelRun: (runId: string) => Promise<void>;
  council: { topic: string; strategy: CouncilStrategy; busy: boolean; result: string | null };
  setCouncil: React.Dispatch<React.SetStateAction<{ topic: string; strategy: CouncilStrategy; busy: boolean; result: string | null }>>;
  onCouncil: () => void;
  onPostVerdict: () => void;
}) {
  const [objective, setObjective] = useState("");
  const [runs, setRuns] = useState<Array<Record<string, unknown>>>([]);
  const isGroup = props.sel.kind === "group";

  useEffect(() => {
    if (props.sel.kind === "group") {
      listRoomRuns(props.sel.name).then(setRuns).catch(() => setRuns([]));
    } else {
      setRuns([]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.sel]);

  const decisions = props.messages.filter((m) => m.kind === "decision");
  const blockers = props.messages.filter((m) => ["blocker", "warning", "escalation"].includes(m.kind));
  const statusOf = (name: string) =>
    props.roster.find((r) => r.name === name)?.status ||
    props.presence.find((p) => p.name === name)?.status ||
    "unknown";
  const dot = (s: string) =>
    /active|working|online|idle/i.test(s) ? "bg-emerald-500" : /busy|testing|running/i.test(s) ? "bg-amber-500" : /off|unknown|idle/i.test(s) ? "bg-muted-foreground" : "bg-primary";

  return (
    <aside className="hidden lg:flex w-72 shrink-0 border-l border-border bg-card/40 flex-col min-h-0" aria-label="Conversation details">
      <div className="p-3 border-b border-border/60 flex items-center gap-2">
        <p className="text-xs font-bold flex-1">Details</p>
        <button type="button" onClick={props.onClose} className="p-1.5 rounded-lg hover:bg-muted text-muted-foreground" aria-label="Close details">
          <X className="size-4" />
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-3 space-y-4">
        <section>
          <p className="text-[11px] font-bold mb-1.5">Participants ({props.members.length})</p>
          {props.members.length === 0 ? (
            <p className="text-[11px] text-muted-foreground">Nobody listed.</p>
          ) : (
            <div className="space-y-1">
              {props.members.map((m) => (
                <div key={m} className="flex items-center gap-2 text-[11px]">
                  <span className={`size-2 rounded-full shrink-0 ${dot(statusOf(m))}`} />
                  <span className="font-semibold flex-1 truncate" style={{ color: m === OPERATOR ? undefined : senderColor(m) }}>{m === OPERATOR ? "You (operator)" : m}</span>
                  <span className="text-muted-foreground">{statusOf(m)}</span>
                </div>
              ))}
            </div>
          )}
        </section>

        {decisions.length > 0 && (
          <section>
            <p className="text-[11px] font-bold mb-1.5">Decisions made ({decisions.length})</p>
            <div className="space-y-1.5">
              {decisions.slice(-5).map((d, i) => (
                <div key={i} className="rounded-lg border-l-2 border-emerald-500 bg-muted/40 px-2.5 py-1.5">
                  <p className="text-[11px] font-semibold" style={{ color: senderColor(d.sender) }}>{d.sender}</p>
                  <p className="text-[11px] line-clamp-3">{d.content}</p>
                </div>
              ))}
            </div>
          </section>
        )}

        {blockers.length > 0 && (
          <section>
            <p className="text-[11px] font-bold mb-1.5">Needs attention ({blockers.length})</p>
            <div className="space-y-1.5">
              {blockers.slice(-4).map((b, i) => (
                <div key={i} className="rounded-lg border-l-2 border-amber-500 bg-muted/40 px-2.5 py-1.5">
                  <p className="text-[10px] font-bold text-amber-600">{b.kind.toUpperCase()} • {b.sender}</p>
                  <p className="text-[11px] line-clamp-3">{b.content}</p>
                </div>
              ))}
            </div>
          </section>
        )}

        {isGroup && (
          <>
            <section className="rounded-xl border border-border/60 p-2.5 space-y-2">
              <p className="text-[11px] font-bold">Supervise the team</p>
              <div className="flex gap-2">
                <input value={objective} onChange={(e) => setObjective(e.target.value)} placeholder="Goal for an autonomous run…" aria-label="Autonomous run goal" className={inputCls} />
                <Btn
                  variant="ghost"
                  onClick={() => objective.trim() && props.onAutoRun(objective.trim()).then(() => setObjective("")).catch((e) => props.onError(errMsg(e)))}
                  disabled={!objective.trim()}
                >
                  <Play className="size-3.5" />
                </Btn>
              </div>
              {runs.length > 0 && (
                <div className="space-y-1">
                  {runs.slice(0, 5).map((r, i) => {
                    const rid = String(r.run_id ?? r.id ?? i);
                    const st = String(r.status ?? r.state ?? "unknown");
                    const live = st === "running" || st === "pending";
                    return (
                      <div key={rid} className="flex items-center gap-2 text-[11px] rounded-lg bg-muted/40 px-2 py-1.5">
                        <Badge tone={live ? "blue" : "gray"}>{st}</Badge>
                        <span className="font-mono flex-1 truncate">{rid.slice(0, 16)}</span>
                        {live && (
                          <button type="button" onClick={() => props.onCancelRun(rid).catch((e) => props.onError(errMsg(e)))} className="p-1 rounded hover:bg-muted" title="Stop run">
                            <Ban className="size-3.5" />
                          </button>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
              <Btn variant="danger" onClick={props.onDeleteRoom}>
                <Trash2 className="size-3.5" /> Delete group
              </Btn>
            </section>

            <section className="rounded-xl border border-border/60 p-2.5 space-y-2">
              <p className="text-[11px] font-bold inline-flex items-center gap-1.5">
                <Scale className="size-3.5 text-primary" /> Settle a disagreement
              </p>
              <Field label="What's disputed?">
                <input value={props.council.topic} onChange={(e) => props.setCouncil((c) => ({ ...c, topic: e.target.value }))} placeholder="REST vs GraphQL for the API…" className={inputCls} />
              </Field>
              <div className="flex gap-2">
                <select value={props.council.strategy} onChange={(e) => props.setCouncil((c) => ({ ...c, strategy: e.target.value as CouncilStrategy }))} className={`${inputCls} !w-auto`} aria-label="Council strategy">
                  <option value="debate">Debate</option>
                  <option value="council">Council vote</option>
                  <option value="ensemble">Ensemble</option>
                  <option value="single">Single judge</option>
                </select>
                <Btn onClick={props.onCouncil} disabled={props.council.busy || props.council.topic.trim().length < 2}>
                  {props.council.busy ? "Deliberating…" : "Ask council"}
                </Btn>
              </div>
              {props.council.result && (
                <div className="space-y-1.5">
                  <pre className="text-[11px] whitespace-pre-wrap rounded-lg bg-muted/40 p-2 max-h-48 overflow-y-auto">{props.council.result.slice(0, 2000)}</pre>
                  <Btn onClick={props.onPostVerdict}>Post as group decision</Btn>
                </div>
              )}
            </section>
          </>
        )}

        {props.events.length > 0 && (
          <section>
            <p className="text-[11px] font-bold mb-1.5">Recent team events</p>
            <div className="space-y-1">
              {props.events.slice(0, 6).map((e, i) => (
                <p key={i} className="text-[10px] text-muted-foreground rounded-lg bg-muted/40 px-2 py-1.5">
                  {String(e.event_type ?? e.type ?? "event")} — {String(e.summary ?? e.description ?? "").slice(0, 100)}
                </p>
              ))}
            </div>
          </section>
        )}
      </div>
    </aside>
  );
}
