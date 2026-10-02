"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  listRooms, getRoom, createRoom, postToRoom, deleteRoom, startRoomRun, listRoomRuns, cancelRoomRun,
  listDmThreads, ensureRosterAgent, unreadCount, markSeen, senderColor, kindTone,
  MESSAGE_KINDS, REACTION_EMOJI, OPERATOR, ChatMsg, DmThread, MemberPresence, PresenceState,
  listRoomMembers, editRoomMessage, deleteRoomMessage, reactToRoomMessage, forwardRoomMessage,
  groupTree, roomRoster, roomBreadcrumbs, createSubgroup, listChildren, mergeGroups, setMemberExcluded,
  type Breadcrumb, type RoomRoster, type TreeNode,
} from "@/lib/comm";
import {
  breadcrumbText, membershipHeadline, rosterBuckets, acceptsSubgroups, buildTree, stateLabel, stateTone,
} from "@/lib/groups-tree";
import { GroupTreeSidebar, GroupBreadcrumbs, NewSubgroupForm } from "@/components/sections/GroupTreeSidebar";
import { fetchRoster } from "@/lib/inbox";
import { sendAgentMessage } from "@/lib/inbox";
import { runCouncil, CouncilStrategy } from "@/lib/deliberation";
import { clockTime, dayLabel as sharedDayLabel, absoluteStamp } from "@/lib/time";
import { orgEvents } from "@/lib/teamops";
import { Section, EmptyState, ErrorBox, Btn, Badge, Field, SkeletonList, inputCls } from "@/components/ui";
import { errMsg } from "@/lib/http";
import {
  Search, Users, User, Plus, Send, ArrowLeft, Info, X, Check, CheckCheck,
  Play, Ban, Trash2, Scale, RefreshCw, Pause, Reply, Pencil, Forward,
  Smile, Copy, CornerUpLeft, FolderPlus, Layers, CornerUpRight,
} from "lucide-react";

type Filter = "all" | "unread" | "groups" | "direct" | "decisions" | "blockers";
type Selection = { kind: "group"; name: string } | { kind: "dm"; peer: string };

/**
 * The roster headline for a room header.
 *
 * Derived from the measured roster and stated in those terms: an unread roster
 * says the states are not read yet, never "nobody is working", and an all-idle
 * room says so.
 */
function busySummary(entries: MemberPresence[]): string {
  if (entries.length === 0) return "presence not read yet";
  const busy = entries.filter((e) => e.state === "busy" || e.state === "online").length;
  if (busy === 0) {
    const unknown = entries.filter((e) => !e.state || e.state === "unknown").length;
    if (unknown === entries.length) return "no member state reported";
    return "none working now";
  }
  return `${busy} of ${entries.length} working now`;
}

/** One icon-only per-message control. */
function BubbleAction(props: {
  title: string;
  onClick: () => void;
  danger?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={props.onClick}
      title={props.title}
      className={`p-1 rounded-full border border-border/70 bg-card elev-1 hover:bg-muted ${
        props.danger ? "text-destructive" : "text-muted-foreground"
      }`}
    >
      {props.children}
    </button>
  );
}

interface Conv {
  id: string;
  kind: "group" | "dm";
  title: string;
  subtitle: string;
  lastText: string;
  lastAt: string | null;
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
  /**
   * The open room's measured roster, keyed by room name.
   *
   * This is per-room because it reads the room's own membership: the
   * thread-scoped agent roster above is empty for any room the operator never
   * opened as a thread, which is why every participant used to render as
   * `unknown`. A failed read leaves the key absent and `membersError` set, so
   * the panel reports "could not read this roster" instead of "nobody is here".
   */
  const [membersByRoom, setMembersByRoom] = useState<Record<string, MemberPresence[]>>({});
  const [membersError, setMembersError] = useState<Record<string, string>>({});
  const [events, setEvents] = useState<Array<Record<string, unknown>>>([]);
  // Per-list fetch-failure flags: every list surfaces its own failure instead
  // of collapsing into an empty list that reads as "you have nothing here".
  const [roomsError, setRoomsError] = useState<string | null>(null);
  const [dmsError, setDmsError] = useState<string | null>(null);
  const [rosterError, setRosterError] = useState<string | null>(null);
  const [eventsError, setEventsError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sel, setSel] = useState<Selection | null>(null);
  /**
   * The open room's read state, and the reason a read failed.
   *
   * `roomMsgs[name]` is only written on a SUCCESSFUL read, so a failed
   * `getRoom` left the key absent and `activeMsgs` fell back to `[]` — the
   * transcript then rendered `EmptyState` "No messages yet — Say hello below".
   * A 404, a 500 and a genuinely quiet room were the same screen, and the
   * error box sat at the very bottom of the page, below the composer. This is
   * the same three-state model `TeamOpsSection.openMessages` already uses.
   */
  const [roomRead, setRoomRead] = useState<{ name: string; state: "ok" | "failed" | "loading" } | null>(null);
  const [roomReadError, setRoomReadError] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [kind, setKind] = useState<string>("discussion");
  const [sending, setSending] = useState(false);
  /* ── Message features ── */
  /** The message being replied to; renders as a quote strip above the composer. */
  const [replyTo, setReplyTo] = useState<ChatMsg | null>(null);
  /** The message open in the inline editor; only one at a time. */
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(null);
  const [savingEdit, setSavingEdit] = useState(false);
  /** Per-message in-flight state, so one row's spinner cannot disable another's. */
  const [busyMsg, setBusyMsg] = useState<Record<string, boolean>>({});
  /** The message whose reaction picker is open. */
  const [reactingTo, setReactingTo] = useState<string | null>(null);
  const [forwarding, setForwarding] = useState<ChatMsg | null>(null);
  const [forwardRoom, setForwardRoom] = useState("");
  const [forwardingBusy, setForwardingBusy] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  const [showDetails, setShowDetails] = useState(true);
  const [showNewGroup, setShowNewGroup] = useState(false);
  const [newGroup, setNewGroup] = useState({ name: "", members: "" });
  const [newDm, setNewDm] = useState("");
  const [live, setLive] = useState(false);
  /** "Post as group decision" is in flight, so a double-click cannot double-post. */
  const [postingVerdict, setPostingVerdict] = useState(false);
  const [council, setCouncil] = useState<{ topic: string; strategy: CouncilStrategy; busy: boolean; result: string | null }>({ topic: "", strategy: "debate", busy: false, result: null });
  /* ── Nested groups ── */
  /** The whole forest. A failed read leaves it empty and sets `treeError`. */
  const [tree, setTree] = useState<TreeNode[]>([]);
  const [treeError, setTreeError] = useState<string | null>(null);
  /** Per-room roster, kept apart from the flat room list. */
  const [rosters, setRosters] = useState<Record<string, RoomRoster>>({});
  const [rosterErrors, setRosterErrors] = useState<Record<string, string>>({});
  /** Breadcrumb chain for the open group. */
  const [crumbs, setCrumbs] = useState<Breadcrumb[]>([]);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  /** Parent for the open "add a group inside this one" form. */
  const [subgroupFor, setSubgroupFor] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const timer = useRef<number | null>(null);

  const load = async (quiet = false) => {
    if (!quiet) setLoading(true);
    setError(null);
    try {
      // Each list settles independently: one failed fetch flags only that
      // list as unavailable — never a silent empty array.
      const [roomsRes, eventsRes, treeRes] = await Promise.allSettled([
        listRooms(),
        orgEvents(15),
        groupTree(),
      ]);
      if (treeRes.status === "fulfilled") {
        setTree(treeRes.value);
        setTreeError(null);
      } else {
        // The forest is what makes subgroups visible at all, so a failed read
        // is surfaced rather than leaving the sidebar looking like every group
        // is a leaf.
        setTreeError(errMsg(treeRes.reason));
      }
      if (roomsRes.status === "fulfilled") {
        setRooms(roomsRes.value);
        setRoomsError(null);
      } else {
        setRoomsError(errMsg(roomsRes.reason));
      }
      if (eventsRes.status === "fulfilled") {
        setEvents(eventsRes.value);
        setEventsError(null);
      } else {
        setEventsError(errMsg(eventsRes.reason));
      }
      if (props.threadId) {
        const [dmsRes, rosterRes] = await Promise.allSettled([
          listDmThreads(props.threadId),
          fetchRoster(props.threadId),
        ]);
        if (dmsRes.status === "fulfilled") {
          setDms(dmsRes.value);
          setDmsError(null);
        } else {
          setDmsError(errMsg(dmsRes.reason));
        }
        if (rosterRes.status === "fulfilled") {
          setRoster(rosterRes.value);
          setRosterError(null);
        } else {
          setRosterError(errMsg(rosterRes.reason));
        }
      } else {
        setDms([]);
        setRoster([]);
        setDmsError(null);
        setRosterError(null);
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
    setRoomRead(null);
    setRoomReadError(null);
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
    setRoomRead({ name, state: "loading" });
    setRoomReadError(null);
    // The roster is fetched alongside the transcript, and settles on its own:
    // a failed member read must not blank a room whose messages arrived fine.
    void loadMembers(name);
    try {
      const room = await getRoom(name);
      setRoomMsgs((prev) => ({ ...prev, [name]: room.messages }));
      setRoomRead({ name, state: "ok" });
      markSeen(`group:${name}`, room.messages);
    } catch (e) {
      // A failed read is NOT an empty room, and it is not a quiet one either.
      setRoomRead({ name, state: "failed" });
      setRoomReadError(errMsg(e));
      setError(errMsg(e));
    }
  };

  /** Re-read one room's roster. Success replaces the entry; failure records the reason. */
  const loadMembers = async (name: string) => {
    // Presence and the split roster are two different reads and settle
    // separately: a roster that names who is inherited can still fail while the
    // presence dots succeed.
    try {
      const members = await listRoomMembers(name);
      setMembersByRoom((prev) => ({ ...prev, [name]: members }));
      setMembersError((prev) => {
        const next = { ...prev };
        delete next[name];
        return next;
      });
    } catch (e) {
      setMembersError((prev) => ({ ...prev, [name]: errMsg(e) }));
    }
    try {
      const roster = await roomRoster(name);
      setRosters((prev) => ({ ...prev, [name]: roster }));
      setRosterErrors((prev) => {
        const next = { ...prev };
        delete next[name];
        return next;
      });
    } catch (e) {
      setRosterErrors((prev) => ({ ...prev, [name]: errMsg(e) }));
    }
    try {
      const chain = await roomBreadcrumbs(name);
      setCrumbs(chain);
    } catch (e) {
      setCrumbs([]);
      setError(errMsg(e));
    }
  };

  const onCreateSubgroup = async (parentName: string, body: { name: string; topic: string; summary: string; inherit: boolean }) => {
    await createSubgroup(parentName, body);
    setSubgroupFor(null);
    await load(true);
  };

  const onMergeSubgroups = async (roomName: string) => {
    // The server reports which children merged and which did not; a partial
    // merge must not be painted as a clean one.
    if (!window.confirm(`Merge every subgroup of "${roomName}" into it? Messages and members move; the subgroups are removed.`)) {
      return;
    }
    setSubgroupFor(null);
    try {
      const result = await mergeGroups(roomName);
      const merged = Number(result.merged_count ?? 0);
      const total = Number(result.children_total ?? 0);
      const failed = Array.isArray(result.failed) ? result.failed.length : 0;
      if (merged === 0 && total === 0) {
        setError(`"${roomName}" has no subgroups to merge.`);
      } else if (merged < total || failed > 0) {
        setError(`Merged ${merged} of ${total} subgroups into "${roomName}"${failed ? `, ${failed} failed` : ""}.`);
      }
      await load(true);
      if (sel?.kind === "group" && sel.name === roomName) await openRoom(roomName);
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
      // `roomMsgs` is only filled for rooms the operator has OPENED, so an
      // unopened room's history has never been read. The row used to say "No
      // messages yet" for it — a claim about the room — and carry no time, so
      // the whole list was blank and unsortable. It now says the history has
      // not been read yet, which is what is actually true.
      const read = Object.prototype.hasOwnProperty.call(roomMsgs, r.name);
      const msgs = read ? roomMsgs[r.name] : null;
      const last = msgs && msgs.length > 0 ? msgs[msgs.length - 1] : null;
      return {
        id: `group:${r.name}`,
        kind: "group" as const,
        title: `# ${r.name}`,
        subtitle: `${r.members.length} members${r.status ? ` • ${r.status}` : ""}`,
        lastText: !read
          ? "History not read yet — open the room"
          : last
            ? `${last.sender}: ${last.content.slice(0, 80)}`
            : "No messages yet",
        lastAt: last?.at ?? null,
        unread: read ? unreadCount(`group:${r.name}`, msgs!) : 0,
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
        lastAt: last?.at ?? null,
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
          // Only rooms whose history has actually been READ can be searched
          // for a decision or a blocker. Filtering on `roomMsgs[...] || []`
          // silently dropped every unopened room, so the filter answered "no
          // decisions" for a workspace full of them. The unsearchable rooms are
          // named instead of being treated as clean.
          const msgs = c.kind === "group"
            ? roomMsgs[c.title.slice(2)]
            : dms.find((d) => d.id === c.id)?.messages;
          if (!msgs) return false;
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

  /**
   * The measured roster for the open room.
   *
   * For a group this is the server's own read. For a direct thread there is no
   * room, so the two participants are the whole roster — stated, not inferred
   * from a failed read.
   */
  const activePresence: MemberPresence[] = sel
    ? sel.kind === "group"
      ? membersByRoom[sel.name] ?? []
      : [
          { name: OPERATOR, state: "online", source: "operator", activityAt: null, detail: "you", role: null, displayName: null },
          {
            name: sel.peer,
            state: roster.find((r) => r.name === sel.peer)?.status === "busy" ? "busy" : "unknown",
            source: "agent_roster",
            activityAt: null,
            detail: roster.find((r) => r.name === sel.peer)?.status ?? "",
            role: roster.find((r) => r.name === sel.peer)?.role ?? null,
            displayName: null,
          },
        ]
    : [];
  const activeMembersError = sel?.kind === "group" ? membersError[sel.name] ?? null : null;
  /** The split roster for the open group: direct / inherited / rule-matched. */
  const activeRoster = sel?.kind === "group" ? rosters[sel.name] ?? null : null;
  const activeRosterError = sel?.kind === "group" ? rosterErrors[sel.name] ?? null : null;
  const activeBuckets = rosterBuckets(activeRoster, (id) => tree.find((n) => n.room_id === id)?.name ?? id);

  const totalUnread = convs.reduce((n, c) => n + Math.min(c.unread, 99), 0);

  /**
   * How many members of a group row are working right now.
   *
   * Read from the measured roster only. An unread or failed roster returns 0,
   * which renders as no dot — never a green one over a list nobody measured.
   */
  const groupBusyCount = (convId: string) => {
    if (!convId.startsWith("group:")) return 0;
    const room = convId.slice("group:".length);
    if (membersByRoom[room] === undefined || membersError[room]) return 0;
    return membersByRoom[room].filter((m) => m.state === "busy" || m.state === "online").length;
  };

  const markBusy = (id: string, on: boolean) =>
    setBusyMsg((prev) => {
      const next = { ...prev };
      if (on) next[id] = true;
      else delete next[id];
      return next;
    });

  /** Re-read the open room after a mutation, keeping the message list truthful. */
  const refreshRoom = async (name: string) => {
    await Promise.all([openRoom(name), loadMembers(name)]);
  };

  const send = async () => {
    if (!draft.trim() || sending) return;
    if (sel?.kind === "group") {
      setSending(true);
      try {
        await postToRoom(sel.name, OPERATOR, draft.trim(), kind, replyTo?.id ?? null);
        setDraft("");
        // A consumed reply is cleared in the same commit as the send it
        // belonged to: leaving it would silently re-attach the next message
        // to a reply that already went out.
        setReplyTo(null);
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

  const onSaveEdit = async (messageId: string) => {
    if (!editing || savingEdit) return;
    const text = editing.text.trim();
    if (!text || sel?.kind !== "group") {
      setError("An edited message cannot be empty.");
      return;
    }
    setSavingEdit(true);
    markBusy(messageId, true);
    try {
      await editRoomMessage(sel.name, messageId, text);
      setEditing(null);
      await refreshRoom(sel.name);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setSavingEdit(false);
      markBusy(messageId, false);
    }
  };

  const onDeleteMessage = async (m: ChatMsg) => {
    if (sel?.kind !== "group" || busyMsg[m.id]) return;
    if (!window.confirm("Delete this message? Replies to it keep pointing here.")) return;
    markBusy(m.id, true);
    try {
      await deleteRoomMessage(sel.name, m.id);
      if (editing?.id === m.id) setEditing(null);
      await refreshRoom(sel.name);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      markBusy(m.id, false);
    }
  };

  const onReact = async (m: ChatMsg, emoji: string) => {
    if (sel?.kind !== "group" || busyMsg[m.id]) return;
    markBusy(m.id, true);
    try {
      // The server's map is the result; the local row is never painted as
      // reacted before the gateway confirmed it.
      await reactToRoomMessage(sel.name, m.id, OPERATOR, emoji);
      setReactingTo((cur) => (cur === m.id ? null : cur));
      await openRoom(sel.name);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      markBusy(m.id, false);
    }
  };

  const onForward = async () => {
    if (sel?.kind !== "group" || !forwarding || forwardingBusy) return;
    const target = forwardRoom.trim();
    if (!target) return;
    setForwardingBusy(true);
    markBusy(forwarding.id, true);
    try {
      await forwardRoomMessage(sel.name, forwarding.id, target, OPERATOR);
      setForwarding(null);
      setForwardRoom("");
      await load();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setForwardingBusy(false);
      markBusy(forwarding.id, false);
    }
  };

  const onCopy = async (m: ChatMsg) => {
    try {
      await navigator.clipboard.writeText(m.content);
      setCopied(m.id);
      window.setTimeout(() => setCopied((cur) => (cur === m.id ? null : cur)), 1500);
    } catch (e) {
      setError(errMsg(e));
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
    if (postingVerdict) return;
    setPostingVerdict(true);
    try {
      await postToRoom(sel.name, OPERATOR, `Council verdict (${council.strategy}):\n${council.result.slice(0, 1500)}`, "decision");
      setCouncil({ topic: "", strategy: "debate", busy: false, result: null });
      await openRoom(sel.name);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setPostingVerdict(false);
    }
  };

  // Delegates to lib/time.ts so this pane, the roster and the transcript all
  // read one clock vocabulary. `""` renders no time, never a fabricated one.
  const fmtTime = (at: string | null) => clockTime(at) ?? "";

  const dayLabel = (at: string | null) => sharedDayLabel(at) ?? "";

  let lastDay = "";

  /** The quoted row a reply points at, or null when it was deleted/unknown. */
  const quoted = (m: ChatMsg): ChatMsg | null => {
    if (!m.replyTo) return null;
    return activeMsgs.find((x) => x.id === m.replyTo) ?? null;
  };

  return (
    <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
      <div className="max-w-none w-full flex-1 flex min-h-0 relative">
        {/* ── Conversation list ── */}
        <aside className={`w-full md:w-80 shrink-0 border-r border-border bg-card/40 flex-col min-h-0 ${sel ? "hidden md:flex" : "flex"}`} aria-label="Conversations">
          <div className="p-3 space-y-2 border-b border-border/60">
            <div className="flex items-center gap-2">
              <h2 className="text-base font-semibold tracking-tight flex-1">
                Messages {totalUnread > 0 && <span className="ml-1 text-[10px] px-1.5 py-0.5 rounded-full bg-primary text-primary-foreground font-bold">{totalUnread}</span>}
              </h2>
              <Btn variant="ghost" onClick={() => setLive((v) => !v)} title="Auto-refresh every 10 seconds">
                {live ? <Pause className="size-3.5" aria-hidden="true" /> : <Play className="size-3.5" aria-hidden="true" />} {live ? "Live" : "Poll"}
              </Btn>
              <button
                type="button"
                onClick={() => load()}
                className="p-2 rounded-lg hover:bg-muted text-muted-foreground"
                title="Refresh"
                aria-label="Refresh conversations"
              >
                <RefreshCw className="size-4" aria-hidden="true" />
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

            {/* Nested groups: add a group inside any existing one, at any time. */}
            <div className="flex gap-2">
              <Btn variant="ghost" onClick={() => setSubgroupFor(sel?.kind === "group" ? sel.name : null)} disabled={!sel || sel.kind !== "group"} title={sel?.kind === "group" ? `Add a group inside ${sel.name}` : "Open a group first"}>
                <FolderPlus className="size-3.5" /> Add group inside
              </Btn>
            </div>
            {subgroupFor && (
              <NewSubgroupForm
                parentName={subgroupFor}
                onCreate={(body) => onCreateSubgroup(subgroupFor, body)}
                onCancel={() => setSubgroupFor(null)}
              />
            )}

            {/* The forest. Every group, with the groups nested inside it. */}
            {treeError ? (
              <ErrorBox message={`Group structure unavailable — subgroups could not be read, not empty. (${treeError})`} onRetry={() => load()} />
            ) : tree.length > 0 ? (
              <GroupTreeSidebar
                nodes={tree}
                busyByRoom={Object.fromEntries(
                  Object.entries(membersByRoom).map(([name, list]) => [
                    name,
                    list.filter((m) => m.state === "busy" || m.state === "online").length,
                  ]),
                )}
                errorsByRoom={membersError}
                activeRoom={sel?.kind === "group" ? sel.name : null}
                collapsed={collapsed}
                onToggle={(id) => setCollapsed((prev) => {
                  const next = new Set(prev);
                  if (next.has(id)) next.delete(id);
                  else next.add(id);
                  return next;
                })}
                onOpen={(name) => openRoom(name)}
                onNewSubgroup={(name) => setSubgroupFor(name)}
                onMerge={onMergeSubgroups}
              />
            ) : null}
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

          {/* Per-list failures: shown as unavailable, never as an empty list. */}
          {(roomsError || dmsError || rosterError) && (
            <div className="px-3 py-2 space-y-2 border-b border-border/60">
              {roomsError && (
                <ErrorBox message={`Group list unavailable — failed to load, not empty. (${roomsError})`} onRetry={() => load()} />
              )}
              {dmsError && (
                <ErrorBox message={`Direct threads unavailable — failed to load, not empty. (${dmsError})`} onRetry={() => load()} />
              )}
              {rosterError && (
                <ErrorBox message={`Agent roster unavailable — statuses may show as unknown. (${rosterError})`} onRetry={() => load()} />
              )}
            </div>
          )}

          <div className="flex-1 overflow-y-auto">
            {loading ? (
              <div className="p-3"><SkeletonList rows={5} /></div>
            ) : error && convs.length === 0 ? (
              <div className="p-3"><ErrorBox message={error} onRetry={() => load()} /></div>
            ) : convs.length === 0 && (roomsError || dmsError) ? (
              <div className="p-3">
                <ErrorBox
                  message="Conversations couldn't be loaded — this is a fetch failure, not an empty inbox."
                  onRetry={() => load()}
                />
              </div>
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
                    <span className="relative shrink-0">
                      <span className="size-10 rounded-full flex items-center justify-center text-sm font-bold text-white" style={{ backgroundColor: c.kind === "group" ? "#5566ff" : senderColor(c.title) }}>
                        {c.kind === "group" ? <Users className="size-4" /> : c.title.slice(0, 2).toUpperCase()}
                      </span>
                      {/* A group row shows how many of its members are actually
                          working. Counted from the measured roster, and only
                          once the roster was read — an unread roster shows no
                          dot rather than a green one. */}
                      {c.kind === "group" && groupBusyCount(c.id) > 0 && (
                        <span
                          className="absolute -right-0.5 -bottom-0.5 size-2.5 rounded-full bg-emerald-500 ring-2 ring-background"
                          title={`${groupBusyCount(c.id)} member${groupBusyCount(c.id) === 1 ? "" : "s"} working now`}
                          aria-label={`${groupBusyCount(c.id)} members working now`}
                        />
                      )}
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
                  <ArrowLeft className="size-4" aria-hidden="true" />
                </button>
                <span className="size-9 rounded-full flex items-center justify-center text-sm font-bold text-white shrink-0" style={{ backgroundColor: sel.kind === "group" ? "#5566ff" : senderColor(activeTitle) }}>
                  {sel.kind === "group" ? <Users className="size-4" /> : activeTitle.slice(0, 2).toUpperCase()}
                </span>
                <div className="flex-1 min-w-0">
                  {sel.kind === "group" && crumbs.length > 0 && (
                    <GroupBreadcrumbs chain={crumbs} current={sel.name} onOpen={(name) => openRoom(name)} />
                  )}
                  <p className="text-sm font-semibold truncate">{activeTitle}</p>
                  <p className="text-[11px] text-muted-foreground truncate">
                    {sel.kind === "group"
                      ? `${membershipHeadline(activeRoster, activeRosterError)} — ${busySummary(activePresence)}`
                      : "direct thread — private between you two"}
                  </p>
                </div>
                {/* The roster is the point of this view, so the participant count
                    in the header opens it directly rather than hiding it behind
                    the details toggle. */}
                <button
                  type="button"
                  onClick={() => setShowDetails(true)}
                  className="inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-1 rounded-full bg-muted/60 hover:bg-muted text-muted-foreground shrink-0"
                  title="Show who is in this group"
                  aria-label={`Show the ${activeMembers.length} members of this group`}
                >
                  <Users className="size-3" aria-hidden="true" />
                  {activeMembers.length}
                </button>
                <button
                  type="button"
                  onClick={() => setShowDetails((v) => !v)}
                  className="p-2 rounded-lg hover:bg-muted text-muted-foreground"
                  title="Details"
                  aria-label="Toggle details"
                  aria-expanded={showDetails}
                >
                  <Info className="size-4" aria-hidden="true" />
                </button>
              </header>

              <div className="flex-1 overflow-y-auto px-4 sm:px-8 py-4 space-y-1.5">
                {/* Three states. `activeMsgs` is `[]` for a FAILED read and
                    for a genuinely quiet room, so the empty state is gated on
                    the read having succeeded — otherwise a down Gateway or a
                    renamed route showed "No messages yet" for a room full of
                    history the client simply never fetched. */}
                {sel.kind === "group" && roomRead?.state === "failed" ? (
                  <div className="h-full flex items-center justify-center">
                    <EmptyState
                      title="Could not read this room"
                      hint={`This is a fetch failure, not an empty room. The server said: ${roomReadError ?? "no reason given"}`}
                      action={<Btn onClick={() => sel.kind === "group" && openRoom(sel.name)}>Try again</Btn>}
                    />
                  </div>
                ) : sel.kind === "group" && roomRead?.state === "loading" ? (
                  <div className="h-full flex items-center justify-center">
                    <p className="text-[11px] text-muted-foreground">Reading {sel.name}…</p>
                  </div>
                ) : activeMsgs.length === 0 ? (
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
                          const isEditing = editing?.id === m.id;
                          const busy = !!busyMsg[m.id];
                          const replyQuote = quoted(m);
                          const reactions = m.reactions ?? {};
                          const reactionEntries = Object.entries(reactions).filter(([, who]) => who.length > 0);

                          return (
                            <div className={`flex ${mine ? "justify-end" : "justify-start"} group/bubble`}>
                              <div className={`max-w-[80%] sm:max-w-[70%] rounded-2xl px-3 py-2 elev-1 relative ${
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

                                {/* Forward provenance: stated, because a message
                                    that crossed rooms is a different claim
                                    from one authored here. */}
                                {m.forwardedFrom && (
                                  <p className={`text-[10px] italic mb-1 ${mine ? "text-white/75" : "text-muted-foreground"}`}>
                                    ↪ forwarded from #{m.forwardedFrom.room}
                                    {m.forwardedFrom.sender ? ` by ${m.forwardedFrom.sender}` : ""}
                                  </p>
                                )}

                                {/* A reply quotes its target. A target that is
                                    gone says so instead of rendering an empty
                                    quote box. */}
                                {m.replyTo && (
                                  <div className={`text-[10px] mb-1 pl-2 border-l-2 rounded-r ${
                                    mine ? "border-white/50 text-white/80" : "border-primary/50 text-muted-foreground"
                                  }`}>
                                    {replyQuote ? (
                                      <>
                                        <span className="font-bold">{replyQuote.sender}</span>
                                        {": "}
                                        {replyQuote.deleted ? <em>message deleted</em> : replyQuote.content.slice(0, 120)}
                                      </>
                                    ) : (
                                      <em>original message not available</em>
                                    )}
                                  </div>
                                )}

                                {m.kind !== "discussion" && (
                                  <span className={`inline-block text-[10px] font-bold px-1.5 py-0.5 rounded-md mt-0.5 mb-1 ${kindTone(m.kind) === "green" ? "bg-emerald-500/15 text-emerald-600" : kindTone(m.kind) === "amber" ? "bg-amber-500/15 text-amber-600" : kindTone(m.kind) === "blue" ? "bg-primary/15 text-primary" : "bg-muted text-muted-foreground"}`}>
                                    {m.kind.replace(/_/g, " ").toUpperCase()}
                                  </span>
                                )}

                                {m.deleted ? (
                                  <p className={`text-[13px] italic ${mine ? "text-white/70" : "text-muted-foreground"}`}>
                                    This message was deleted.
                                  </p>
                                ) : isEditing ? (
                                  <div className="space-y-1.5">
                                    <textarea
                                      value={editing.text}
                                      onChange={(e) => setEditing({ id: m.id, text: e.target.value })}
                                      onKeyDown={(e) => {
                                        if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) onSaveEdit(m.id);
                                        if (e.key === "Escape") setEditing(null);
                                      }}
                                      rows={3}
                                      aria-label="Edit message"
                                      className="w-full text-[13px] rounded-lg border border-border bg-background p-2 focus:outline-none focus:ring-1 focus:ring-primary/40"
                                    />
                                    <div className="flex items-center gap-2 justify-end">
                                      <Btn variant="ghost" onClick={() => setEditing(null)}>Cancel</Btn>
                                      <Btn onClick={() => onSaveEdit(m.id)} disabled={savingEdit || !editing.text.trim()}>
                                        {savingEdit ? "Saving…" : "Save"}
                                      </Btn>
                                    </div>
                                  </div>
                                ) : (
                                  <p className="text-[13px] leading-relaxed whitespace-pre-wrap break-words">{cleanContent}</p>
                                )}

                                {/* Reactions render only what the server
                                    returned. An absent set renders nothing at
                                    all rather than an empty strip. */}
                                {reactionEntries.length > 0 && (
                                  <div className="flex flex-wrap gap-1 mt-1.5">
                                    {reactionEntries.map(([emoji, who]) => (
                                      <button
                                        key={emoji}
                                        type="button"
                                        onClick={() => onReact(m, emoji)}
                                        disabled={busy}
                                        title={who.join(", ")}
                                        aria-label={`${emoji} reacted by ${who.join(", ")}`}
                                        className={`text-[10px] px-1.5 py-0.5 rounded-full border disabled:opacity-50 ${
                                          who.includes(OPERATOR)
                                            ? "border-primary/60 bg-primary/10"
                                            : "border-border/60 bg-muted/40"
                                        }`}
                                      >
                                        {emoji} {who.length}
                                      </button>
                                    ))}
                                  </div>
                                )}

                                {reactingTo === m.id && !m.deleted && (
                                  <div className="flex flex-wrap gap-1 mt-1.5 p-1.5 rounded-lg bg-muted/60 border border-border/60">
                                    {REACTION_EMOJI.map((emoji) => (
                                      <button
                                        key={emoji}
                                        type="button"
                                        onClick={() => onReact(m, emoji)}
                                        disabled={busy}
                                        aria-label={`React with ${emoji}`}
                                        className="text-sm px-1 rounded hover:bg-muted disabled:opacity-50"
                                      >
                                        {emoji}
                                      </button>
                                    ))}
                                  </div>
                                )}

                                <p className={`text-[10px] mt-1 flex items-center gap-1 justify-end ${mine ? "text-white/70" : "text-muted-foreground"}`}>
                                  {m.editedAt && !m.deleted && (
                                    <span title={`Edited ${absoluteStamp(m.editedAt) ?? ""}`}>edited</span>
                                  )}
                                  <span title={absoluteStamp(m.at) ?? "no recorded time"}>{fmtTime(m.at)}</span>
                                  {mine && !m.deleted && (m.read ? <CheckCheck className="size-3" /> : <Check className="size-3" />)}
                                </p>

                                {/* Per-message actions. Hover-revealed on
                                    pointer devices, always present for keyboard
                                    and touch so the control is reachable. */}
                                {!m.deleted && (
                                  <div className="flex items-center gap-0.5 absolute -top-2 right-2 opacity-0 focus-within:opacity-100 group-hover/bubble:opacity-100 transition-opacity">
                                    <BubbleAction title="Reply" onClick={() => setReplyTo(m)}>
                                      <CornerUpLeft className="size-3" aria-hidden="true" />
                                      <span className="sr-only">Reply to {m.sender}</span>
                                    </BubbleAction>
                                    <BubbleAction title="React" onClick={() => setReactingTo((cur) => (cur === m.id ? null : m.id))}>
                                      <Smile className="size-3" aria-hidden="true" />
                                      <span className="sr-only">React to message from {m.sender}</span>
                                    </BubbleAction>
                                    {sel.kind === "group" && (
                                      <>
                                        <BubbleAction title="Edit" onClick={() => setEditing({ id: m.id, text: m.content })}>
                                          <Pencil className="size-3" aria-hidden="true" />
                                          <span className="sr-only">Edit message from {m.sender}</span>
                                        </BubbleAction>
                                        <BubbleAction title="Forward" onClick={() => { setForwarding(m); setForwardRoom(""); }}>
                                          <Forward className="size-3" aria-hidden="true" />
                                          <span className="sr-only">Forward message from {m.sender}</span>
                                        </BubbleAction>
                                        <BubbleAction title="Delete" danger onClick={() => onDeleteMessage(m)}>
                                          <Trash2 className="size-3" aria-hidden="true" />
                                          <span className="sr-only">Delete message from {m.sender}</span>
                                        </BubbleAction>
                                      </>
                                    )}
                                    <BubbleAction title={copied === m.id ? "Copied" : "Copy"} onClick={() => onCopy(m)}>
                                      <Copy className="size-3" aria-hidden="true" />
                                      <span className="sr-only">Copy message from {m.sender}</span>
                                    </BubbleAction>
                                  </div>
                                )}
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
                <div className="max-w-3xl mx-auto space-y-2">
                {/* Forwarding target picker. Named before the post so it is
                    obvious what "forward" means — the destination room is
                    typed, never guessed from the first room in the list. */}
                {forwarding && (
                  <div className="rounded-xl border border-border/60 bg-card p-2.5 space-y-2">
                    <p className="text-[11px] font-semibold">
                      Forward to another group — from {forwarding.sender}
                    </p>
                    <p className="text-[11px] text-muted-foreground line-clamp-2">
                      {forwarding.deleted ? "This message was deleted." : forwarding.content}
                    </p>
                    <div className="flex items-center gap-2">
                      <input
                        value={forwardRoom}
                        onChange={(e) => setForwardRoom(e.target.value)}
                        placeholder="target-group"
                        aria-label="Target group name"
                        className={inputCls}
                      />
                      <Btn onClick={onForward} disabled={!forwardRoom.trim() || forwardingBusy}>
                        {forwardingBusy ? "Forwarding…" : "Forward"}
                      </Btn>
                      <Btn variant="ghost" onClick={() => setForwarding(null)}>Cancel</Btn>
                    </div>
                    {rooms.length > 0 && (
                      <p className="text-[10px] text-muted-foreground">
                        Existing groups:{" "}
                        {rooms
                          .filter((r) => r.name !== (sel?.kind === "group" ? sel.name : null))
                          .map((r) => r.name)
                          .slice(0, 8)
                          .join(", ") || "none yet"}
                      </p>
                    )}
                  </div>
                )}

                {/* The reply strip. Dismissing it drops the pending reply so
                    the next send is not silently attached to it. */}
                {replyTo && (
                  <div className="flex items-start gap-2 rounded-xl border border-primary/40 bg-primary/5 px-2.5 py-2">
                    <Reply className="size-3.5 text-primary mt-0.5 shrink-0" aria-hidden="true" />
                    <div className="flex-1 min-w-0">
                      <p className="text-[11px] font-semibold text-primary">
                        Replying to {replyTo.sender}
                      </p>
                      <p className="text-[11px] text-muted-foreground line-clamp-2">{replyTo.content}</p>
                    </div>
                    <button
                      type="button"
                      onClick={() => setReplyTo(null)}
                      className="p-1 rounded hover:bg-muted text-muted-foreground"
                      aria-label="Cancel reply"
                    >
                      <X className="size-3.5" aria-hidden="true" />
                    </button>
                  </div>
                )}

                <div className="flex items-center gap-2">
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
            presence={activePresence}
            presenceError={activeMembersError}
            roster={activeRoster}
            rosterError={activeRosterError}
            buckets={activeBuckets}
            headline={membershipHeadline(activeRoster, activeRosterError)}
            canNest={acceptsSubgroups(
              buildTree(tree, {}).find((e) => e.node.name === (sel?.kind === "group" ? sel.name : null)) ?? null,
            )}
            crumbs={crumbs}
            onOpenRoom={(name) => openRoom(name)}
            onAddSubgroup={(name) => setSubgroupFor(name)}
            tree={tree}
            onRefreshRoster={() => (sel?.kind === "group" ? loadMembers(sel.name) : Promise.resolve())}
            onExclude={async (bot, excluded) => {
              if (sel?.kind !== "group") return;
              try {
                await setMemberExcluded(sel.name, bot, excluded);
                await loadMembers(sel.name);
              } catch (e) {
                setError(errMsg(e));
              }
            }}
            onRefreshTree={() => load(true)}
            messages={activeMsgs}
            events={events}
            eventsError={eventsError}
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
            postingVerdict={postingVerdict}
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

/** One member row in the roster: dot, name, optional role, and its state. */
function MemberRow(props: {
  name: string;
  displayName?: string | null;
  role?: string | null;
  state: PresenceState | null;
  dotClass: string;
  detail: string;
  title: string;
  muted?: boolean;
}) {
  const mine = props.name === OPERATOR;
  return (
    <div
      className={`flex items-center gap-2 text-[11px] rounded-lg px-1.5 py-1 hover:bg-muted/40 ${props.muted ? "opacity-80" : ""}`}
      title={props.title}
    >
      <span className={`size-2 rounded-full shrink-0 ${props.dotClass}`} aria-hidden="true" />
      <span className="min-w-0 flex-1">
        <span className="font-semibold truncate block" style={{ color: mine ? undefined : senderColor(props.name) }}>
          {mine ? "You (operator)" : props.displayName || props.name}
        </span>
        {props.role && <span className="text-[10px] text-muted-foreground truncate block">{props.role}</span>}
      </span>
      <span className="text-[10px] text-muted-foreground shrink-0 text-right">{props.detail}</span>
    </div>
  );
}

/** The groups nested directly under one room, with their own honest counts. */
function SubgroupList(props: {
  roomName: string;
  tree: TreeNode[];
  onOpen: (name: string) => void;
}) {
  const room = props.tree.find((n) => n.name === props.roomName);
  // `parents` is the authoritative edge, so a child names this room directly.
  const children = props.tree.filter((n) => (n.scope?.parents ?? []).includes(room?.room_id ?? ""));
  if (!room || children.length === 0) {
    return (
      <p className="text-[11px] text-muted-foreground">
        No groups inside this one yet. Add one above — it starts with this group&rsquo;s members and inherits future
        additions automatically.
      </p>
    );
  }
  return (
    <div className="space-y-1">
      {children.map((child) => (
        <button
          key={child.room_id}
          type="button"
          onClick={() => props.onOpen(child.name)}
          className="w-full flex items-center gap-2 text-[11px] rounded-lg bg-muted/40 px-2 py-1.5 hover:bg-muted text-left"
        >
          <CornerUpRight className="size-3 shrink-0 text-muted-foreground" aria-hidden="true" />
          <span className="font-semibold truncate flex-1">{child.name}</span>
          {child.scope?.state && child.scope.state !== "active" && (
            <span className="text-[9px] text-muted-foreground">{stateLabel(child.scope.state)}</span>
          )}
          {/* Both counts together, or nothing. */}
          {child.direct_count !== null && child.effective_count !== null && (
            <span className="text-[9px] text-muted-foreground tabular-nums">
              {child.direct_count === child.effective_count
                ? `${child.effective_count}`
                : `${child.direct_count}/${child.effective_count}`}
            </span>
          )}
        </button>
      ))}
    </div>
  );
}

function DetailsPane(props: {
  sel: { kind: "group"; name: string } | { kind: "dm"; peer: string };
  members: string[];
  /** Measured per-member presence; empty when the roster has not been read. */
  presence: MemberPresence[];
  /** Why the roster could not be read, or null. Never conflated with empty. */
  presenceError: string | null;
  /** The split roster (direct / inherited / rule-matched), or null. */
  roster: RoomRoster | null;
  rosterError: string | null;
  buckets: ReturnType<typeof rosterBuckets>;
  /** Both membership counts as one honest headline. */
  headline: string;
  /** Whether this group can take subgroups at all. */
  canNest: boolean;
  crumbs: Breadcrumb[];
  onOpenRoom: (name: string) => void;
  onAddSubgroup: (name: string) => void;
  onRefreshRoster: () => Promise<void>;
  onExclude: (bot: string, excluded: boolean) => Promise<void>;
  onRefreshTree: () => void;
  /** The whole forest, so this pane can list the groups nested under itself. */
  tree: TreeNode[];
  messages: ChatMsg[];
  events: Array<Record<string, unknown>>;
  eventsError: string | null;
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
  /** True while the verdict is being written to the room, so it cannot double-post. */
  postingVerdict: boolean;
}) {
  const [objective, setObjective] = useState("");
  const [runs, setRuns] = useState<Array<Record<string, unknown>>>([]);
  const [runsError, setRunsError] = useState<string | null>(null);
  /**
   * Run lifecycle locks.
   *
   * `POST /api/groups/{name}/runs` mints a NEW run id per request and fans the
   * objective out to one subagent per member plus a moderator pass
   * (backend/app/gateway/routers/groups.py:197). The Play control was
   * `disabled={!objective.trim()}` only, and the objective was cleared in the
   * success path — so a double-click inside the request window started a SECOND
   * autonomous run: duplicate token spend, and two competing runs writing into
   * the same room log. `TeamOpsSection.autoRun` already guards this exact route
   * with `runningGroup`; this control is its twin and had no such lock.
   */
  const [startingRun, setStartingRun] = useState(false);
  const [cancellingRun, setCancellingRun] = useState(false);
  const isGroup = props.sel.kind === "group";

  const startRun = async () => {
    const text = objective.trim();
    if (!text || startingRun) return;
    setStartingRun(true);
    try {
      await props.onAutoRun(text);
      // Only clear the draft once the server has accepted the run.
      setObjective("");
    } catch (e) {
      props.onError(errMsg(e));
    } finally {
      setStartingRun(false);
    }
  };

  const cancelRun = async (runId: string) => {
    if (cancellingRun) return;
    setCancellingRun(true);
    try {
      await props.onCancelRun(runId);
    } catch (e) {
      props.onError(errMsg(e));
    } finally {
      setCancellingRun(false);
    }
  };

  const loadRuns = () => {
    if (props.sel.kind !== "group") {
      setRuns([]);
      setRunsError(null);
      return;
    }
    setRunsError(null);
    listRoomRuns(props.sel.name)
      .then(setRuns)
      .catch((e) => setRunsError(errMsg(e)));
  };

  useEffect(() => {
    loadRuns();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.sel]);

  const decisions = props.messages.filter((m) => m.kind === "decision" && !m.deleted);
  const blockers = props.messages.filter((m) => ["blocker", "warning", "escalation"].includes(m.kind) && !m.deleted);

  /**
   * The presence dot, from the server's resolved state.
   *
   * Matched whole against a fixed set — the earlier version chained regexes
   * over the raw status word, where `idle` appeared in the *first* branch
   * (drawing a green "working" dot for an idle agent) and `inactive` matched
   * `active` (drawing a green dot for a suspended one). The server now resolves
   * a closed vocabulary, so there is nothing left to pattern-match: an absent
   * state is a neutral dot and never an online one.
   */
  const dot = (state: PresenceState | null) => {
    if (state === "online") return "bg-emerald-500";
    if (state === "busy") return "bg-amber-500";
    if (state === "idle") return "bg-muted-foreground";
    if (state === "offline") return "bg-destructive";
    return "bg-muted-foreground/40";
  };

  /** Human label for a member row; always says something the server supports. */
  const stateLabel = (entry: MemberPresence) => {
    if (!entry.state) return "state not reported";
    return entry.detail || entry.state;
  };

  return (
    /* The pane was `hidden lg:flex`, so on a viewport narrower than 1024px the
       header's ⓘ button toggled `showDetails` and NOTHING changed on screen —
       the control was inert at exactly the widths a phone or a split window
       uses, while the header still said "tap ⓘ for presence & decisions".
       It is now an overlay drawer below `lg` (so the toggle always does
       something) and a normal column at `lg` and up (so the layout is
       unchanged where it worked). */
    <aside
      className="w-72 shrink-0 border-l border-border bg-card/40 flex-col min-h-0 max-lg:absolute max-lg:inset-y-0 max-lg:right-0 max-lg:z-30 max-lg:elev-3"
      aria-label="Conversation details"
    >
      <div className="p-3 border-b border-border/60 flex items-center gap-2">
        <p className="text-xs font-bold flex-1">Details</p>
        <button type="button" onClick={props.onClose} className="p-1.5 rounded-lg hover:bg-muted text-muted-foreground" aria-label="Close details">
          <X className="size-4" aria-hidden="true" />
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-3 space-y-4">
        {/* This roster is the reason the view exists: every bot in the group,
            with the state the server actually measured. Direct / inherited /
            rule-matched are kept visually distinct because each has a
            different owner and lifetime — flattening them is how a nested
            roster stops making sense. */}
        <section>
          <p className="text-[11px] font-bold mb-1.5 flex items-center gap-1.5">
            <Users className="size-3" aria-hidden="true" />
            Members in this group
            <span className="font-normal text-muted-foreground">· {props.headline}</span>
          </p>
          {props.rosterError && (
            <ErrorBox
              message={`Membership could not be read — who is here is unknown, not empty. (${props.rosterError})`}
              onRetry={() => void props.onRefreshRoster()}
            />
          )}
          {props.presenceError ? (
            <ErrorBox
              message={`Group roster unavailable — failed to load, not empty. (${props.presenceError})`}
              onRetry={props.onRefresh}
            />
          ) : props.presence.length === 0 ? (
            <p className="text-[11px] text-muted-foreground">
              {props.members.length === 0
                ? "This room has no members."
                : "Reading who is in this group…"}
            </p>
          ) : (
            <div className="space-y-1">
              {props.presence.map((entry) => (
                <MemberRow
                  key={entry.name}
                  name={entry.name}
                  displayName={entry.displayName}
                  role={entry.role}
                  state={entry.state}
                  dotClass={dot(entry.state)}
                  detail={stateLabel(entry)}
                  title={
                    entry.source === "unresolved"
                      ? `${entry.name}: no registry or attendance record`
                      : `${entry.name} — ${stateLabel(entry)} (reported by ${entry.source.replace(/\+/g, " + ")})`
                  }
                  muted={false}
                />
              ))}
            </div>
          )}

          {/* Inherited members: present here, owned by a parent. Never rendered
              in the direct list, because they were not added to this group. */}
          {props.buckets.inherited.length > 0 && (
            <div className="space-y-1 mt-2">
              <p className="text-[10px] font-bold text-muted-foreground">INHERITED ({props.buckets.inherited.length})</p>
              {props.buckets.inherited.map((b) => (
                <MemberRow
                  key={`inh-${b.name}`}
                  name={b.name}
                  state={props.presence.find((p) => p.name === b.name)?.state ?? null}
                  dotClass={dot(props.presence.find((p) => p.name === b.name)?.state ?? null)}
                  detail={`via ${b.via}`}
                  title={`${b.name} is in this group because ${b.via} includes them — not added here.`}
                  muted
                />
              ))}
            </div>
          )}

          {/* Rule-matched members: the output of a declared rule, resolved live.
              The rule that granted them is named so a room can be read. */}
          {props.buckets.ruleMatched.length > 0 && (
            <div className="space-y-1 mt-2">
              <p className="text-[10px] font-bold text-muted-foreground">RULE MATCHED ({props.buckets.ruleMatched.length})</p>
              {props.buckets.ruleMatched.map((b) => {
                const rule = props.roster?.rules?.find((r) => r.id === b.rule);
                return (
                  <MemberRow
                    key={`rule-${b.name}`}
                    name={b.name}
                    state={props.presence.find((p) => p.name === b.name)?.state ?? null}
                    dotClass={dot(props.presence.find((p) => p.name === b.name)?.state ?? null)}
                    detail={rule ? (rule.label || `${rule.field} ${rule.op} ${rule.value}`) : "matched a rule"}
                    title={
                      rule
                        ? `${b.name} matches ${rule.field} ${rule.op} "${rule.value}" — remove the rule to change this.`
                        : `${b.name} is present by a membership rule.`
                    }
                    muted
                  />
                );
              })}
            </div>
          )}

          {/* Excluded members are named rather than silently absent: "in this
              group but pulled out here" is a real state an operator made. */}
          {props.buckets.excluded.length > 0 && (
            <div className="space-y-1 mt-2">
              <p className="text-[10px] font-bold text-muted-foreground">EXCLUDED ({props.buckets.excluded.length})</p>
              {props.buckets.excluded.map((name) => (
                <div key={`exc-${name}`} className="flex items-center gap-2 text-[11px] px-1.5 py-1 opacity-70">
                  <span className="size-2 rounded-full shrink-0 bg-muted-foreground/30" aria-hidden="true" />
                  <span className="flex-1 truncate font-semibold" style={{ color: senderColor(name) }}>
                    {name}
                  </span>
                  <button
                    type="button"
                    onClick={() => void props.onExclude(name, false)}
                    className="text-[10px] text-primary hover:underline shrink-0"
                  >
                    Include again
                  </button>
                </div>
              ))}
            </div>
          )}

          {props.buckets.expired.length > 0 && (
            <div className="space-y-1 mt-2">
              <p className="text-[10px] font-bold text-muted-foreground">EXPIRED ({props.buckets.expired.length})</p>
              {props.buckets.expired.map((name) => (
                <p key={`exp-${name}`} className="text-[10px] text-muted-foreground px-1.5 py-1">
                  {name} — borrowed here and past its expiry, so no longer participating.
                </p>
              ))}
            </div>
          )}
          {/* A room can list a member the roster cannot resolve. Naming that
              gap is the difference between "this bot is down" and "we have no
              record of this bot". */}
          {props.presence.length > 0 &&
            props.members
              .filter((m) => !props.presence.some((p) => p.name === m))
              .map((m) => (
                <p key={m} className="text-[10px] text-muted-foreground mt-1">
                  {m} is listed as a member but was not in the roster read.
                </p>
              ))}
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
            {/* Groups nested inside this one — the WhatsApp-community shape. */}
            <section className="rounded-xl border border-border/60 p-2.5 space-y-2">
              <p className="text-[11px] font-bold flex items-center gap-1.5">
                <Layers className="size-3.5 text-primary" aria-hidden="true" /> Groups inside this one
              </p>
              {props.canNest && (
                <Btn variant="ghost" onClick={() => props.onAddSubgroup(props.sel.kind === "group" ? props.sel.name : "")}>
                  <FolderPlus className="size-3.5" aria-hidden="true" /> Add a group inside
                </Btn>
              )}
              <SubgroupList
                roomName={props.sel.kind === "group" ? props.sel.name : ""}
                tree={props.tree}
                onOpen={props.onOpenRoom}
              />
            </section>

            <section className="rounded-xl border border-border/60 p-2.5 space-y-2">
              <p className="text-[11px] font-bold">Supervise the team</p>
              <div className="flex gap-2">
                <input value={objective} onChange={(e) => setObjective(e.target.value)} placeholder="Goal for an autonomous run…" aria-label="Autonomous run goal" className={inputCls} />
                <Btn
                  variant="ghost"
                  onClick={() => startRun()}
                  disabled={!objective.trim() || !!startingRun}
                  title={startingRun ? "A run is still starting" : "Start one autonomous run for this room"}
                  aria-label="Start an autonomous run for this room"
                >
                  <Play className="size-3.5" aria-hidden="true" />
                </Btn>
              </div>
              {runsError ? (
                <ErrorBox
                  message={`Room runs unavailable — failed to load, not empty. (${runsError})`}
                  onRetry={loadRuns}
                />
              ) : runs.length > 0 && (
                <div className="space-y-1">
                  <p className="text-[10px] text-muted-foreground">
                    {runs.length} run{runs.length === 1 ? "" : "s"} recorded
                    {runs.length > 5 ? ` — showing the 5 most recent` : ""}
                  </p>
                  {runs.slice(0, 5).map((r, i) => {
                    const rid = String(r.run_id ?? r.id ?? `run-${i}`);
                    const st = String(r.status ?? r.state ?? "");
                    const live = st === "running" || st === "pending";
                    return (
                      <div key={rid} className="flex items-center gap-2 text-[11px] rounded-lg bg-muted/40 px-2 py-1.5">
                        <Badge tone={live ? "blue" : "gray"}>{st || "status not reported"}</Badge>
                        <span className="font-mono flex-1 truncate" title={rid}>{rid.slice(0, 16)}</span>
                        {live && (
                          <button
                            type="button"
                            onClick={() => cancelRun(rid)}
                            disabled={!!cancellingRun}
                            className="p-1 rounded hover:bg-muted disabled:opacity-30"
                            title={cancellingRun ? "A cancel is still in flight" : `Stop run ${rid}`}
                            aria-label={`Stop run ${rid}`}
                          >
                            <Ban className="size-3.5" aria-hidden="true" />
                          </button>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
              <Btn variant="danger" onClick={props.onDeleteRoom}>
                <Trash2 className="size-3.5" aria-hidden="true" /> Delete group
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
                  <Btn onClick={props.onPostVerdict} disabled={props.postingVerdict}>
                    {props.postingVerdict ? "Posting…" : "Post as group decision"}
                  </Btn>
                </div>
              )}
            </section>
          </>
        )}

        {props.eventsError ? (
          <section>
            <p className="text-[11px] font-bold mb-1.5">Recent team events</p>
            <p className="text-[10px] text-destructive">Team events unavailable — failed to load, not empty. ({props.eventsError})</p>
          </section>
        ) : props.events.length > 0 && (
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
