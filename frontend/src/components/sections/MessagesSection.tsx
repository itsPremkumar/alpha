"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  listRooms,
  getRoom,
  createRoom,
  postToRoom,
  deleteRoom,
  startRoomRun,
  listRoomRuns,
  cancelRoomRun,
  listDmThreads,
  ensureRosterAgent,
  unreadCount,
  markSeen,
  senderColor,
  kindTone,
  MESSAGE_KINDS,
  DM_MESSAGE_KINDS,
  REACTION_EMOJI,
  OPERATOR,
  ChatMsg,
  DmThread,
  MemberPresence,
  PresenceState,
  listRoomMembers,
  editRoomMessage,
  deleteRoomMessage,
  reactToRoomMessage,
  forwardRoomMessage,
  groupTree,
  roomRoster,
  roomBreadcrumbs,
  createSubgroup,
  listChildren,
  mergeGroups,
  setMemberExcluded,
  roomActivity,
  type RoomActivity,
  type Breadcrumb,
  type RoomRoster,
  type TreeNode,
} from "@/lib/comm";
import {
  breadcrumbText,
  membershipHeadline,
  rosterBuckets,
  acceptsSubgroups,
  buildTree,
  stateLabel,
  stateTone,
  visibleTreeNodes,
} from "@/lib/groups-tree";
import {
  GroupTreeSidebar,
  GroupBreadcrumbs,
  NewSubgroupForm,
} from "@/components/sections/GroupTreeSidebar";
import { GroupActivityPanel } from "@/components/sections/GroupActivityPanel";
import { GroupProfilePanel } from "@/components/sections/GroupProfilePanel";
import { Composer } from "@/components/Composer";
import { fetchRoster } from "@/lib/inbox";
import { sendAgentMessage } from "@/lib/inbox";
import {
  type MentionAgent,
  type MentionRow,
  findMentionAtCaret,
  filterMentionRows,
  countMentionTokens,
  parseMentions,
  rosterHandles,
  departmentRoleIndex,
  applyMention,
  buildMentionRows,
} from "@/lib/agent-mentions";
import { previewMentions, type MentionPreviewResponse } from "@/lib/groups-profile";
import { runCouncil, CouncilStrategy } from "@/lib/deliberation";
import {
  clockTime,
  dayLabel as sharedDayLabel,
  absoluteStamp,
} from "@/lib/time";
import {
  sectionConversations,
  hiddenSummary,
  unreadLabel,
  groupMessageRuns,
  isInboundToUser,
} from "@/lib/messages-view";
import { orgEvents } from "@/lib/teamops";
import {
  Section,
  EmptyState,
  ErrorBox,
  Btn,
  Badge,
  Field,
  SkeletonList,
  inputCls,
} from "@/components/ui";
import { errMsg } from "@/lib/http";
import {
  Search,
  Users,
  User,
  UserPlus,
  Plus,
  Send,
  ArrowLeft,
  Info,
  X,
  Check,
  CheckCheck,
  Play,
  Ban,
  Trash2,
  Scale,
  RefreshCw,
  Pause,
  Reply,
  Pencil,
  Forward,
  Smile,
  Copy,
  CornerUpLeft,
  FolderPlus,
  Layers,
  CornerUpRight,
  AtSign,
  ArrowLeftRight,
  Loader2,
  AlertTriangle,
  Pin,
} from "lucide-react";

type Filter = "all" | "unread" | "groups" | "direct" | "a2u" | "decisions" | "blockers";

/**
 * The chip labels, one place, because the key is not always the word.
 *
 * `direct` is labelled **A2A** and `a2u` **A→U**: the pair names the two
 * directions of the same mailbox. **A2A** is every direct thread — agent to
 * agent and agent to you alike, which is what the workspace's own vocabulary
 * calls that list. **A→U** narrows it to the threads carrying mail an agent
 * addressed *to you by name*. The keys stay `direct`/`a2u` — they are the
 * wire/state vocabulary, not copy.
 */
const FILTER_LABELS: Record<Filter, string> = {
  all: "All",
  unread: "Unread",
  groups: "Groups",
  direct: "A2A",
  a2u: "A→U",
  decisions: "Decisions",
  blockers: "Blockers",
};


/**
 * Which lens of the GROUPS section is on screen.
 *
 * `all` — the flat list of every group with its live bots — is the default
 * because "who is here, and what are they doing" is the question the list
 * answers; the nested `tree` and the live `working` lens are one click away.
 * Exactly one renders at a time, so a room is still listed once.
 */
type GroupsTab = "all" | "tree" | "working";

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
  const busy = entries.filter(
    (e) => e.state === "busy" || e.state === "online",
  ).length;
  if (busy === 0) {
    const unknown = entries.filter(
      (e) => !e.state || e.state === "unknown",
    ).length;
    if (unknown === entries.length) return "no member state reported";
    return "none working now";
  }
  return `${busy} of ${entries.length} working now`;
}

/** One icon-only per-message control in the floating hover toolbar. */
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
      className={`p-1.5 rounded-full transition-colors dur-fast hover:bg-muted ${
        props.danger
          ? "text-destructive"
          : "text-muted-foreground hover:text-foreground"
      }`}
    >
      {props.children}
    </button>
  );
}

/** The two-character mark a name collapses to: "backend-dev" -> "BD". */
function initials(name: string): string {
  const parts = name
    .replace(/^#\s*/, "")
    .trim()
    .split(/[\s_\-.]+/)
    .filter(Boolean);
  if (parts.length === 0) return "••";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return `${parts[0][0]}${parts[1][0]}`.toUpperCase();
}

/**
 * The one avatar mark this surface renders.
 *
 * The tint is `senderColor` — the same hash the sender names already use — so a
 * bot is the same colour in the list, the header and the roster. A room has no
 * sender name to hash, so it keeps one fixed tint and a people glyph: invented
 * initials like "PA" for `project-atlas` would read as an agent's name, which
 * is a claim about the roster that no read made.
 */
function Avatar(props: {
  name: string;
  group?: boolean;
  size?: "sm" | "md" | "lg";
  className?: string;
  children?: React.ReactNode;
}) {
  const sizing = {
    sm: "size-6 text-[9px]",
    md: "size-8 text-[10px]",
    lg: "size-10 text-[12px]",
  }[props.size ?? "md"];
  return (
    <span className={`relative inline-flex shrink-0 ${props.className ?? ""}`}>
      <span
        className={`${sizing} rounded-full inline-flex items-center justify-center font-bold text-white ring-1 ring-black/10 dark:ring-white/10`}
        style={{
          backgroundColor: props.group ? "#5566ff" : senderColor(props.name),
        }}
        aria-hidden="true"
      >
        {props.group ? <Users className="size-4" /> : initials(props.name)}
      </span>
      {props.children}
    </span>
  );
}

/**
 * The presence dot, from the server's resolved state.
 *
 * Matched whole against a fixed set — the earlier version chained regexes
 * over the raw status word, where `idle` appeared in the *first* branch
 * (drawing a green "working" dot for an idle agent) and `inactive` matched
 * `active` (drawing a green dot for a suspended one). The server now resolves
 * a closed vocabulary, so there is nothing left to pattern-match: an absent
 * state is a neutral dot and never an online one. The list rows and the
 * roster panel share this one mapping so a bot is the same colour in both.
 */
function presenceDotClass(state: PresenceState | null): string {
  if (state === "online") return "bg-emerald-500";
  if (state === "busy") return "bg-amber-500";
  if (state === "idle") return "bg-muted-foreground";
  if (state === "offline") return "bg-destructive";
  return "bg-muted-foreground/40";
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

export function MessagesSection(props: {
  threadId: string | null;
  botNames: string[];
  /**
   * One-shot request from another surface (a project card's Room shortcut, the
   * project hub's room panel) to open ONE room here.
   *
   * A project's room is named by the crew service, so the caller passes the
   * name the server gave rather than composing one — and the transcript it
   * opens is this view's own, with the roster, activity and composer that go
   * with a room.
   */
  openRoomRequest?: { name: string; nonce: number } | null;
  /** Acknowledge the request above, so it cannot re-fire on the next mount. */
  onOpenRoomRequestHandled?: () => void;
  /**
   * Open a project's full read from the opened room.
   *
   * A project's room names the project it is bound to; this is how that name
   * reaches the project's own hub, so the hub's *Room* shortcut is a step in a
   * task rather than a dead end.
   */
  onOpenProjectDetails?: (projectId: string) => void;
}) {
  const [filter, setFilter] = useState<Filter>("all");
  const [search, setSearch] = useState("");
  const [rooms, setRooms] = useState<
    Array<{ name: string; members: string[]; status: string }>
  >([]);
  const [dms, setDms] = useState<DmThread[]>([]);
  const [roomMsgs, setRoomMsgs] = useState<Record<string, ChatMsg[]>>({});
  const [roster, setRoster] = useState<
    Array<{
      name: string;
      role: string;
      status: string;
      display_name?: string | null;
      department?: string | null;
      avatar?: string | null;
      model?: string | null;
      capabilities?: string[];
    }>
  >([]);
  /**
   * The open room's measured roster, keyed by room name.
   *
   * This is per-room because it reads the room's own membership: the
   * thread-scoped agent roster above is empty for any room the operator never
   * opened as a thread, which is why every participant used to render as
   * `unknown`. A failed read leaves the key absent and `membersError` set, so
   * the panel reports "could not read this roster" instead of "nobody is here".
   */
  const [membersByRoom, setMembersByRoom] = useState<
    Record<string, MemberPresence[]>
  >({});
  const [membersError, setMembersError] = useState<Record<string, string>>({});
  /**
   * Live activity per room, kept apart from `membersByRoom` on purpose.
   * Presence says "who is enrolled"; activity says "what are they doing, and
   * did they die?". Merging them is what made a crashed agent indistinguishable
   * from a finished one, because both resolved to `idle`.
   *
   * A key that is absent means "not read yet" — which the panel renders as
   * *reading…*, never as an empty room. A failed read records its reason.
   */
  const [activityByRoom, setActivityByRoom] = useState<
    Record<string, RoomActivity>
  >({});
  const [activityError, setActivityError] = useState<Record<string, string>>(
    {},
  );
  const [activityBusy, setActivityBusy] = useState(false);
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
  const [roomRead, setRoomRead] = useState<{
    name: string;
    state: "ok" | "failed" | "loading";
  } | null>(null);
  const [roomReadError, setRoomReadError] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [kind, setKind] = useState<string>("discussion");
  const [sending, setSending] = useState(false);
  /** Caret offset for @ tag palette — tracked explicitly since @ opens mid-sentence. */
  const [caret, setCaret] = useState<number>(0);
  /** Which palette row the arrow keys have highlighted. */
  const [mentionIndex, setMentionIndex] = useState<number>(0);
  /** Dismissal state for the @ palette. */
  const [mentionDismissed, setMentionDismissed] = useState<boolean>(false);
  /* ── Message features ── */
  /** The message being replied to; renders as a quote strip above the composer. */
  const [replyTo, setReplyTo] = useState<ChatMsg | null>(null);
  /** The message open in the inline editor; only one at a time. */
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(
    null,
  );
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
  /* Below `lg` the details pane is a full-coverage overlay (see `DetailsPane`),
     so landing on the section with it open would hide the list and transcript
     behind a page the operator never asked for. Start closed at those widths
     only; the `true` default still gives `lg`+ its column on first paint. This
     runs after hydration, so server and client markup stay identical. */
  useEffect(() => {
    if (window.innerWidth < 1024) setShowDetails(false);
  }, []);
  const [showNewGroup, setShowNewGroup] = useState(false);
  const [newGroup, setNewGroup] = useState({ name: "", members: "" });
  const [newDm, setNewDm] = useState("");
  const [live, setLive] = useState(false);
  /** "Post as group decision" is in flight, so a double-click cannot double-post. */
  const [postingVerdict, setPostingVerdict] = useState(false);
  const [council, setCouncil] = useState<{
    topic: string;
    strategy: CouncilStrategy;
    busy: boolean;
    result: string | null;
  }>({ topic: "", strategy: "debate", busy: false, result: null });
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
  /**
   * Which view of the GROUPS section is on screen — the list by default.
   *
   * The flat list answers "who is here and what are they doing" for every
   * group at a glance; the nested tree and the live working lens are one
   * click away. Exactly one lens renders at a time, so the section never
   * lists a room twice.
   */
  const [groupsTab, setGroupsTab] = useState<GroupsTab>("all");
  const bottomRef = useRef<HTMLDivElement>(null);
  const timer = useRef<number | null>(null);
  /**
   * The conversation scroller, for arrow-key roving focus.
   *
   * Every row is already a real `<button>`, so the list is one long tab stop
   * chain: reaching the transcript means tabbing past every conversation in the
   * sidebar first. The rows are located by DOM order at keypress time instead of
   * held in a ref array, so the keyboard path can never drift from the filter
   * that actually decided what is on screen.
   */
  const convListRef = useRef<HTMLDivElement>(null);

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

  /**
   * The composer's type list is surface-specific.
   *
   * Group rooms validate against the group intent vocabulary and direct
   * threads against the A2A mailbox vocabulary — the two overlap but are not
   * equal (`blocker` is a group-only intent, `incident` a DM-only kind).
   * Carrying a selection across the surfaces would render a `<select>` with
   * no matching option and be refused with a 422 on send, so an invalid
   * carry-over resets to the surface's own default.
   */
  useEffect(() => {
    const vocab = sel?.kind === "dm" ? DM_MESSAGE_KINDS : MESSAGE_KINDS;
    if (!(vocab as readonly string[]).includes(kind)) {
      setKind(sel?.kind === "dm" ? "message" : "discussion");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sel?.kind]);

  useEffect(() => {
    if (live) timer.current = window.setInterval(() => load(true), 10000);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live, props.threadId]);

  // Activity rides the existing 10s poll rather than starting its own timer:
  // one cadence, one place, and no second interval to leak on unmount. A crash
  // notice is therefore at most one poll late, which is the same latency the
  // presence dots already have.
  const activityRoom = sel?.kind === "group" ? sel.name : null;
  useEffect(() => {
    if (!activityRoom || !live) return;
    void loadActivity(activityRoom);
    const handle = window.setInterval(
      () => void loadActivity(activityRoom),
      10000,
    );
    return () => window.clearInterval(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activityRoom, live]);

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

  /**
   * Open the room another surface asked for, then acknowledge it.
   *
   * The nonce is what makes this an event rather than a mirror of state: a
   * project hub asking for the room already on screen is still a deliberate
   * re-read, and the acknowledgement is what stops the request from re-firing
   * on every later mount of this view — which would silently yank the operator
   * out of whatever room they had since opened.
   */
  useEffect(() => {
    const request = props.openRoomRequest;
    if (!request) return;
    void openRoom(request.name);
    props.onOpenRoomRequestHandled?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.openRoomRequest]);

  /**
   * Re-read one room's live activity. Deliberately a **third** state alongside
   * the roster and the presence dots, because it settles separately: an older
   * Gateway has no `/activity` route at all, and that must read as "this build
   * does not have it" rather than as "nobody is working".
   */
  const loadActivity = async (name: string) => {
    try {
      const snapshot = await roomActivity(name);
      setActivityByRoom((prev) => ({ ...prev, [name]: snapshot }));
      setActivityError((prev) => {
        const next = { ...prev };
        delete next[name];
        return next;
      });
    } catch (e) {
      setActivityError((prev) => ({ ...prev, [name]: errMsg(e) }));
    }
  };

  /**
   * Read one room's member presence alone.
   *
   * The list tab states who is in each group and what they are doing, and a
   * claim needs a read: `loadMembers` also pulls the split roster and the
   * breadcrumbs, which only the open room needs, so the bulk list read stays
   * on this lighter path. Failures land in `membersError`, per room — one
   * unread room must never dress up as "nobody is working".
   */
  const loadPresence = async (name: string) => {
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
  };

  /** Re-read one room's roster. Success replaces the entry; failure records the reason. */
  const loadMembers = async (name: string) => {
    // Presence and the split roster are two different reads and settle
    // separately: a roster that names who is inherited can still fail while the
    // presence dots succeed.
    await loadPresence(name);
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

  /**
   * Presence for EVERY group row, not just the open one.
   *
   * The list tab claims "which bots are here, and is any of them working" for
   * every room, so every room's roster is read — `load()` produces a fresh
   * `rooms` array on each poll, which is what makes the dots follow real time.
   * Rooms settle independently through `loadPresence`'s per-room writes, so a
   * slow or failed room never blocks or blanks the rest of the list.
   */
  useEffect(() => {
    for (const r of rooms) void loadPresence(r.name);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rooms]);

  const onCreateSubgroup = async (
    parentName: string,
    body: { name: string; topic: string; summary: string; inherit: boolean },
  ) => {
    await createSubgroup(parentName, body);
    setSubgroupFor(null);
    await load(true);
  };

  const onMergeSubgroups = async (roomName: string) => {
    // The server reports which children merged and which did not; a partial
    // merge must not be painted as a clean one.
    if (
      !window.confirm(
        `Merge every subgroup of "${roomName}" into it? Messages and members move; the subgroups are removed.`,
      )
    ) {
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
        setError(
          `Merged ${merged} of ${total} subgroups into "${roomName}"${failed ? `, ${failed} failed` : ""}.`,
        );
      }
      await load(true);
      if (sel?.kind === "group" && sel.name === roomName)
        await openRoom(roomName);
    } catch (e) {
      setError(errMsg(e));
    }
  };

  const openDm = (peer: string, messages: ChatMsg[]) => {
    setSel({ kind: "dm", peer });
    markSeen(`dm:${peer}`, messages);
  };

  /**
   * Keep the transcript at the newest message — but only when that is what the
   * operator wants.
   *
   * This used to depend on the `roomMsgs` and `dms` OBJECT identities. With
   * Live polling on, `load(true)` reassigns `dms` to a fresh array every ten
   * seconds whether or not anything arrived, so the scroller was dragged back
   * to the bottom mid-sentence: scroll up to check a link, wait ten seconds,
   * and you were at the end again with nothing on screen saying it had moved.
   *
   * Two guards, both about not taking a decision away from the operator:
   *
   * - `atBottomRef` is false while they are parked away from the end, so a new
   *   message never yanks them out of the history they were reading. It is
   *   restored by their own scroll back down, not by a timeout.
   * - The trigger is the row COUNT, so a poll that brought nothing new cannot
   *   move the viewport at all. A poll that *did* bring something new and was
   *   read while scrolled up leaves the position alone; the row is there when
   *   they come back down.
   */
  const transcriptRef = useRef<HTMLDivElement>(null);
  const atBottomRef = useRef(true);

  /** Track whether the operator is parked at the newest message. */
  const onTranscriptScroll = () => {
    const el = transcriptRef.current;
    if (!el) return;
    // A tolerance rather than `=== 0`: sub-pixel layout and the smooth-scroll's
    // own easing otherwise leave the last frame reading as "scrolled away" and
    // the next arrival would no longer follow the transcript.
    atBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
  };

  /**
   * Every conversation, newest first — BEFORE the filter and the search.
   *
   * Split from the visible list on purpose: a chip that counts the rows left on
   * screen reports the same number for every chip (whatever the current one
   * spared), so its count would be a restatement of the selection rather than a
   * claim about the inbox. `allConvs` is what each chip is counting.
   */
  const allConvs: Conv[] = useMemo(() => {
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
        subtitle:
          roster.find((x) => x.name === d.peer)?.status || "direct thread",
        lastText: last ? last.content.slice(0, 80) : "No messages yet",
        lastAt: last?.at ?? null,
        unread: unreadCount(d.id, d.messages),
        members: [OPERATOR, d.peer],
      });
    }
    return list.sort((a, b) => (b.lastAt || "").localeCompare(a.lastAt || ""));
  }, [rooms, dms, roomMsgs, roster]);

  /**
   * Does one conversation pass one filter (plus the shared search text)?
   *
   * The single rule the chips count with and the list renders with — two copies
   * would let a chip claim `4` over a list of three. `f` is the filter being
   * *asked about*, not necessarily the active one, because each chip counts
   * what it would show if it were pressed.
   */
  const matchesFilter = (c: Conv, f: Filter, q: string): boolean => {
    if (f === "groups" && c.kind !== "group") return false;
    if (f === "direct" && c.kind !== "dm") return false;
    if (f === "unread" && c.unread === 0) return false;
    if (f === "a2u") {
      // The A→U direction only. A mailbox holds what its agent sent to you
      // *and* what it sent to other agents, so this asks the loaded rows who
      // each one was addressed to — never the sender alone, which would count
      // an `architect → docs-writer` handoff as mail to the operator.
      if (c.kind !== "dm") return false;
      const msgs = dms.find((d) => d.id === c.id)?.messages;
      if (!msgs || !isInboundToUser(msgs, OPERATOR)) return false;
    }
    if (f === "decisions" || f === "blockers") {
      // Only rooms whose history has actually been READ can be searched
      // for a decision or a blocker. Filtering on `roomMsgs[...] || []`
      // silently dropped every unopened room, so the filter answered "no
      // decisions" for a workspace full of them. The unsearchable rooms are
      // named instead of being treated as clean.
      const msgs =
        c.kind === "group"
          ? roomMsgs[c.title.slice(2)]
          : dms.find((d) => d.id === c.id)?.messages;
      if (!msgs) return false;
      const want =
        f === "decisions" ? ["decision"] : ["blocker", "warning", "escalation"];
      if (!msgs.some((m) => want.includes(m.kind))) return false;
    }
    if (q && !`${c.title} ${c.lastText}`.toLowerCase().includes(q))
      return false;
    return true;
  };

  const query = search.trim().toLowerCase();
  /** The rows actually on screen: the active filter plus the search text. */
  const convs = allConvs.filter((c) => matchesFilter(c, filter, query));
  /**
   * What the GROUPS section shows — derived once, used by both renderers.
   *
   * The section's own count is `section.rows.length` (the flat list's visible
   * rooms), so the tree is narrowed to exactly those rooms or the header would
   * disagree with what sits under it. A room the flat read never reported
   * stays (two reads disagreeing must not delete a branch), and a matching
   * child is promoted to root when the filter hid its parent.
   */
  const groupVisibleTree = visibleTreeNodes(
    tree,
    new Set(convs.filter((c) => c.kind === "group").map((c) => c.title.slice(2))),
    new Set(allConvs.filter((c) => c.kind === "group").map((c) => c.title.slice(2))),
  );
  /** room name -> measured unread, for the tree rows; an unopened room carries no badge. */
  const unreadByRoom: Record<string, number> = {};
  for (const c of allConvs) {
    if (c.kind === "group") unreadByRoom[c.title.slice(2)] = c.unread;
  }
  /** room name -> measured busy count (working now), or absent when presence was never read. */
  const busyByRoom: Record<string, number> = {};
  for (const [name, list] of Object.entries(membersByRoom)) {
    busyByRoom[name] = list.filter(
      (m) => m.state === "busy" || m.state === "online",
    ).length;
  }
  /**
   * What each chip would show under the current search — measured, not guessed.
   * `all` counts every row the search leaves, so it also reads as the denominator.
   */
  const chipCounts = useMemo(() => {
    const counts = {} as Record<Filter, number>;
    for (const f of [
      "all",
      "unread",
      "groups",
      "direct",
      "a2u",
      "decisions",
      "blockers",
    ] as Filter[]) {
      counts[f] = allConvs.filter((c) => matchesFilter(c, f, query)).length;
    }
    return counts;
    // `matchesFilter` closes over roomMsgs/dms, which are allConvs' own deps.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allConvs, query, roomMsgs, dms]);

  const activeMsgs: ChatMsg[] = sel
    ? sel.kind === "group"
      ? roomMsgs[sel.name] || []
      : dms.find((d) => d.peer === sel.peer)?.messages || []
    : [];

  // Declared here rather than beside its refs because it reads `activeMsgs`,
  // which is the only place the open room's and the open thread's message
  // lists have been resolved to one array.
  useEffect(() => {
    if (!atBottomRef.current) return;
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    // `activeMsgs.length` is the trigger on purpose — see the note above. `sel`
    // is what makes switching conversation jump to that room's newest message,
    // which is right even if the operator was parked up in the previous one's
    // history.
  }, [sel, activeMsgs.length]);

  const activeTitle = sel
    ? sel.kind === "group"
      ? `# ${sel.name}`
      : sel.peer
    : "";
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
      ? (membersByRoom[sel.name] ?? [])
      : [
          {
            name: OPERATOR,
            state: "online",
            source: "operator",
            activityAt: null,
            detail: "you",
            role: null,
            displayName: null,
          },
          {
            name: sel.peer,
            state:
              roster.find((r) => r.name === sel.peer)?.status === "busy"
                ? "busy"
                : "unknown",
            source: "agent_roster",
            activityAt: null,
            detail: roster.find((r) => r.name === sel.peer)?.status ?? "",
            role: roster.find((r) => r.name === sel.peer)?.role ?? null,
            displayName: null,
          },
        ]
    : [];
  const activeMembersError =
    sel?.kind === "group" ? (membersError[sel.name] ?? null) : null;
  /** The split roster for the open group: direct / inherited / rule-matched. */
  const activeRoster =
    sel?.kind === "group" ? (rosters[sel.name] ?? null) : null;
  const activeRosterError =
    sel?.kind === "group" ? (rosterErrors[sel.name] ?? null) : null;
  const activeBuckets = rosterBuckets(
    activeRoster,
    (id) => tree.find((n) => n.room_id === id)?.name ?? id,
  );

  /**
   * Room-specific mention agents for the @ tag palette.
   *
   * In a group room, the palette shows the room's members (with departments
   * for @role:). In a DM, it shows the peer + the global roster (so you can
   * @-tag other agents even in a direct thread).
   *
   * Note: Room members from `listRoomMembers` don't include department/avatar/model
   * fields, so we use sensible defaults. The global roster (from thread-scoped
   * `/agent-messages/roster`) does include these fields.
   */
  const mentionAgents = useMemo<MentionAgent[]>(() => {
    if (!sel) return [];
    if (sel.kind === "group") {
      // Room members with departments for @role: resolution
      // Note: MemberPresence doesn't include department/avatar, so we use defaults
      const members = membersByRoom[sel.name] ?? [];
      return members.map((m) => ({
        handle: m.name,
        displayName: m.displayName ?? m.name,
        role: m.role ?? "Agent",
        department: "general", // Room members don't have department; use default
        status: m.state ?? null,
        avatar: "", // Room members don't have avatar
        model: null,
        capabilities: [],
      }));
    }
    // DM: the peer + global roster (so you can @-tag others)
    const peer = roster.find((r) => r.name === sel.peer);
    const peerAgent: MentionAgent = {
      handle: sel.peer,
      displayName: peer?.display_name ?? sel.peer,
      role: peer?.role ?? "Agent",
      department: peer?.department ?? "general",
      status: peer?.status ?? null,
      avatar: peer?.avatar ?? "",
      model: typeof peer?.model === "string" ? peer.model : null,
      capabilities: Array.isArray(peer?.capabilities) ? peer.capabilities : [],
    };
    // Also include other bots from global roster (excluding the peer)
    const otherAgents = roster
      .filter((b) => b.name !== sel.peer)
      .map((b) => ({
        handle: b.name,
        displayName: b.display_name ?? b.name,
        role: b.role,
        department: b.department ?? "general",
        status: b.status,
        avatar: b.avatar ?? "",
        model: typeof b.model === "string" ? b.model : null,
        capabilities: Array.isArray(b.capabilities) ? b.capabilities : [],
      }));
    return [peerAgent, ...otherAgents];
  }, [sel, membersByRoom, roster, membersError]);

  /**
   * The unread badge beside the "Messages" heading.
   *
   * Summed over `allConvs` rather than the filtered rows: pressing "Groups"
   * must not make unread DMs vanish from the workspace total. The sum is the
   * real one and only the *display* is capped by `unreadLabel`, because
   * capping each row at 99 before adding undercounted every room that was
   * genuinely noisy — 3 rooms × 120 unread read as "297" instead of "360".
   */
  const totalUnread = allConvs.reduce((n, c) => n + Math.max(c.unread, 0), 0);

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
    return membersByRoom[room].filter(
      (m) => m.state === "busy" || m.state === "online",
    ).length;
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
    await Promise.all([openRoom(name), loadMembers(name), loadActivity(name)]);
  };

  // ── @ tag palette state (mirrors Composer's logic) ──

  /** Convert agents to rows (includes @role:, @everyone, @bot: qualifiers). */
  const rosterRows = useMemo(
    () =>
      buildMentionRows({
        agents: mentionAgents,
        activeHandle: null, // No "switch" mode in MessagesSection
        allowSwitch: false,
      }),
    [mentionAgents],
  );

  /** The @ token the caret is in, or null. */
  const mentionTrigger = useMemo(() => {
    if (mentionDismissed) return null;
    return findMentionAtCaret(draft, caret);
  }, [draft, caret, mentionDismissed]);

  const mentionFilter = useMemo(() => {
    if (!mentionTrigger) return null;
    return filterMentionRows(rosterRows, mentionTrigger);
  }, [rosterRows, mentionTrigger]);

  const mentionRows: MentionRow[] = mentionFilter ? mentionFilter.visible : [];

  /** Rows grouped under their section heading, for the palette body. */
  const mentionSections = useMemo(() => {
    if (mentionRows.length === 0) return [];
    const groups: Array<{ id: string; label: string; icon: React.ReactNode; rows: Array<{ row: MentionRow; index: number }> }> = [
      { id: "tag", label: "Tag an agent", icon: <AtSign className="size-3.5 text-primary" />, rows: [] },
      { id: "mode", label: "Bot mode — switch this chat's agent", icon: <ArrowLeftRight className="size-3.5 text-primary" />, rows: [] },
      { id: "broad", label: "Broad tags", icon: <Users className="size-3.5 text-primary" />, rows: [] },
    ];
    mentionRows.forEach((row, index) => {
      const bucket = row.mode === "switch" ? 1 : row.mode === "mention" ? 0 : 2;
      groups[bucket].rows.push({ row, index });
    });
    return groups.filter((g) => g.rows.length > 0);
  }, [mentionRows]);

  /**
   * The persistent strip under the input, and why it is not optional.
   *
   * A tag that addresses nobody is silent by construction: the server resolves
   * an unknown handle to nothing rather than to a near match, so the message
   * goes out and nobody is called. Previewing that before send is the only point
   * at which the operator can still fix it.
   */
  const tagStatus = useMemo(() => {
    if (countMentionTokens(draft) === 0) return null;
    return parseMentions(draft, rosterHandles(mentionAgents), departmentRoleIndex(mentionAgents));
  }, [draft, mentionAgents]);

  /**
   * Server-side mention preview for group rooms.
   *
   * In a group chat (`sel.kind === "group"`), the server owns the authoritative
   * resolution — it has the room's roster, the member departments for `@role:`,
   * and the fan-out ceiling. The client mirror is used only for DMs.
   */
  const [serverTagStatus, setServerTagStatus] = useState<MentionPreviewResponse | null>(null);
  const [serverPreviewError, setServerPreviewError] = useState<string | null>(null);

  useEffect(() => {
    if (!sel || sel.kind !== "group") return;
    if (countMentionTokens(draft) === 0) {
      setServerTagStatus(null);
      setServerPreviewError(null);
      return;
    }
    // The previous draft's resolution must never stand in for this one, and a
    // superseded read must never land: clear first, then let the cleanup
    // cancel whatever this run replaces. An in-flight flag is deliberately NOT
    // a dependency of this effect — a setState in its own dependency array
    // re-ran the effect, fired the cleanup, and discarded the response the run
    // had just requested, so the strip said "checking with the room…" forever
    // and a failure could never surface.
    let cancelled = false;
    setServerTagStatus(null);
    setServerPreviewError(null);
    previewMentions(sel.name, draft, OPERATOR)
      .then((res) => {
        if (!cancelled) setServerTagStatus(res);
      })
      .catch((err) => {
        if (!cancelled) setServerPreviewError(errMsg(err));
      });
    return () => {
      cancelled = true;
    };
  }, [sel, draft]);

  /** The authoritative status: server in a room, client mirror in a DM. */
  const authoritativeStatus = sel?.kind === "group" ? serverTagStatus : tagStatus;

  // Narrowing the query — or moving the caret into a new token — starts the
  // highlight over, because the row order changes under the cursor on every
  // keystroke.
  useEffect(() => {
    setMentionIndex(0);
  }, [mentionTrigger]);

  /**
   * What the tag strip renders, and which authority it came from.
   *
   * For a room the server's preview is the authority. When that read fails the
   * strip falls back to the client mirror *and says so* — a tag the room would
   * have resolved (or the reverse) is the one lie this strip exists to
   * prevent.
   */
  const tagStrip = authoritativeStatus ?? (serverPreviewError ? tagStatus : null);
  const tagStripFromRoom = Boolean(authoritativeStatus) && sel?.kind === "group";
  const tagStripFromMirror =
    !authoritativeStatus && Boolean(serverPreviewError) && Boolean(tagStatus);

  /** The palette row the arrow keys act on, clamped to what it is showing. */
  const activeMention =
    mentionRows.length === 0
      ? -1
      : Math.min(mentionIndex, mentionRows.length - 1);

  /**
   * Apply one palette row: the token written is `row.token` verbatim.
   *
   * Every insertion is the canonical `@bot:` / `@role:` / `@everyone` spelling
   * the server resolves. The palette never writes the raw characters the
   * operator typed, because an abbreviated handle the server cannot match is
   * how a tag silently addresses nobody.
   */
  const chooseMention = (row: MentionRow) => {
    if (!mentionTrigger || row.refusal) return;
    const applied = applyMention(draft, mentionTrigger, row);
    setDraft(applied.text);
    setCaret(applied.caret);
    setMentionIndex(0);
  };

  /**
   * The composer's keys, with the palette owning the ones it is showing.
   *
   * While the palette is open, ↑/↓ walk it, Enter/Tab apply the highlighted
   * row and Escape dismisses it — each handled before Enter's send, so one
   * keypress can never both pick a tag and post a half-finished message.
   */
  const onComposerKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (mentionRows.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setMentionIndex((i) => (i + 1) % mentionRows.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setMentionIndex((i) => (i - 1 + mentionRows.length) % mentionRows.length);
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        if (activeMention >= 0) chooseMention(mentionRows[activeMention]);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setMentionDismissed(true);
        return;
      }
    }
    if (e.key === "Enter") send();
  };

  /** Open (or reuse) a direct thread with a named agent. */
  const startDm = () => {
    const name = newDm.trim();
    if (!name || !props.threadId) return;
    ensureRosterAgent(props.threadId, name)
      .then(() => {
        setNewDm("");
        load();
      })
      .catch((er) => setError(errMsg(er)));
  };

  const send = async () => {
    if (!draft.trim() || sending) return;
    if (sel?.kind === "group") {
      setSending(true);
      try {
        await postToRoom(
          sel.name,
          OPERATOR,
          draft.trim(),
          kind,
          replyTo?.id ?? null,
        );
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
        await sendAgentMessage(
          props.threadId,
          OPERATOR,
          sel.peer,
          draft.trim(),
          kind,
        );
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
    if (
      !window.confirm("Delete this message? Replies to it keep pointing here.")
    )
      return;
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
      window.setTimeout(
        () => setCopied((cur) => (cur === m.id ? null : cur)),
        1500,
      );
    } catch (e) {
      setError(errMsg(e));
    }
  };

  const onCouncil = async () => {
    if (council.topic.trim().length < 2 || council.busy) return;
    setCouncil((c) => ({ ...c, busy: true, result: null }));
    try {
      const verdict = await runCouncil(
        council.topic.trim(),
        council.strategy,
        3,
      );
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
      await postToRoom(
        sel.name,
        OPERATOR,
        `Council verdict (${council.strategy}):\n${council.result.slice(0, 1500)}`,
        "decision",
      );
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

  /**
   * Arrow-key roving focus across the conversation rows.
   *
   * Home/End jump to the ends and ↑/↓ step one row, which is the usual listbox
   * behaviour. It moves focus only — it never changes the selection — so the
   * row that opens is still chosen by an explicit Enter or click, and a reader
   * can walk the list to hear what is in the inbox without opening twelve rooms.
   * When nothing inside the list holds focus (the pointer is elsewhere, or the
   * scroller itself was focused) the first row is taken, so the key is useful
   * instead of dead.
   */
  const onConvKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (
      e.key !== "ArrowDown" &&
      e.key !== "ArrowUp" &&
      e.key !== "Home" &&
      e.key !== "End"
    )
      return;
    const rows = convListRef.current?.querySelectorAll<HTMLButtonElement>(
      "[data-conv-row]",
    );
    if (!rows || rows.length === 0) return;
    e.preventDefault();
    const here = Array.from(rows).indexOf(
      document.activeElement as HTMLButtonElement,
    );
    const target =
      here === -1
        ? e.key === "End"
          ? rows[rows.length - 1]
          : rows[0]
        : e.key === "ArrowDown"
          ? rows[Math.min(here + 1, rows.length - 1)]
          : e.key === "ArrowUp"
            ? rows[Math.max(here - 1, 0)]
            : e.key === "Home"
              ? rows[0]
              : rows[rows.length - 1];
    target?.focus();
  };

  /**
   * Group creation, rendered as the list rows it sits among.
   *
   * Both controls wear the same row shape as the rooms below — creation is a
   * listing action, not a header toolbar — and the first one wears the group
   * icon every group row wears. The two call sites are the Groups section
   * itself and the "No chats yet" empty state (reachable exactly when no
   * conversation, and so no Groups section, exists), so the controls are
   * never offered in two places at once.
   */
  const renderGroupControls = () => (
    <div>
      <button
        type="button"
        onClick={() => setShowNewGroup((v) => !v)}
        aria-expanded={showNewGroup}
        className="w-full flex items-center gap-3 px-3 py-2.5 text-left border-b border-border/40 transition-colors dur-fast hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary/60"
      >
        {/* The same group glyph every group row wears — creation is a row of
            this list, not a toolbar button parked above it. */}
        <Avatar name="__new_group__" group size="lg">
          <span
            className="absolute -right-0.5 -bottom-0.5 size-3.5 rounded-full bg-primary text-primary-foreground ring-2 ring-background inline-flex items-center justify-center"
            aria-hidden="true"
          >
            <Plus className="size-2.5" />
          </span>
        </Avatar>
        <span className="flex-1 text-xs font-semibold">New group</span>
      </button>
      {/* Nested groups: nest one inside the open group, at any time. */}
      <button
        type="button"
        onClick={() => setSubgroupFor(sel?.kind === "group" ? sel.name : null)}
        disabled={!sel || sel.kind !== "group"}
        title={
          sel?.kind === "group"
            ? `Add a group inside ${sel.name}`
            : "Open a group first"
        }
        className="w-full flex items-center gap-3 px-3 py-2.5 text-left border-b border-border/40 transition-colors dur-fast hover:bg-muted/40 disabled:opacity-50 disabled:hover:bg-transparent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary/60"
      >
        <span className="size-10 shrink-0 rounded-full bg-muted text-muted-foreground inline-flex items-center justify-center">
          <FolderPlus className="size-4" aria-hidden="true" />
        </span>
        <span className="flex-1 text-xs font-semibold">
          Add group inside
          {sel?.kind === "group" && (
            <span className="ml-1.5 text-[10px] font-normal text-muted-foreground">
              — {sel.name}
            </span>
          )}
        </span>
      </button>
      {subgroupFor && (
        <div className="px-3 py-2">
          <NewSubgroupForm
            parentName={subgroupFor}
            onCreate={(body) => onCreateSubgroup(subgroupFor, body)}
            onCancel={() => setSubgroupFor(null)}
          />
        </div>
      )}
      {showNewGroup && (
        <div className="px-3 py-2">
          <div className="rounded-xl border border-border/60 p-2.5 space-y-2 bg-card">
            <Field label="Group name">
              <input
                value={newGroup.name}
                onChange={(e) =>
                  setNewGroup({ ...newGroup, name: e.target.value })
                }
                placeholder="project-atlas"
                className={`${inputCls} font-mono`}
              />
            </Field>
            <Field label="Members (comma-separated bot names)">
              <input
                value={newGroup.members}
                onChange={(e) =>
                  setNewGroup({ ...newGroup, members: e.target.value })
                }
                placeholder="architect, backend, qa"
                className={inputCls}
              />
            </Field>
            <Btn
              onClick={() => {
                if (!newGroup.name.trim()) return;
                createRoom(
                  newGroup.name.trim(),
                  newGroup.members
                    .split(",")
                    .map((m) => m.trim())
                    .filter(Boolean),
                )
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
        </div>
      )}
    </div>
  );

  /** The lenses over the GROUPS section; exactly one renders at a time. */
  const groupTabs = [
    { key: "all", label: "All" },
    { key: "tree", label: "Tree" },
    { key: "working", label: "Working now" },
  ] as const;

  const renderGroupTabs = () => (
    <div
      className="flex gap-1 px-3 pt-2 pb-1"
      role="tablist"
      aria-label="Group views"
    >
      {groupTabs.map((t) => (
        <button
          key={t.key}
          type="button"
          role="tab"
          aria-selected={groupsTab === t.key}
          onClick={() => setGroupsTab(t.key)}
          className={`text-[10px] font-bold uppercase tracking-wider px-2 py-1 rounded-md transition-colors dur-fast ${
            groupsTab === t.key
              ? "bg-primary text-primary-foreground"
              : "text-muted-foreground hover:text-foreground hover:bg-muted/60"
          }`}
        >
          {t.label}
        </button>
      ))}
    </div>
  );

  /**
   * The "Working now" lens: rooms whose measured roster has a working member.
   *
   * A room whose presence was never read is NOT quietly "not working" — that
   * would dress an unread roster up as a quiet one. It stays out of the list
   * and is named in the notice above it, or becomes the empty state itself
   * when every room is unread.
   */
  const renderWorkingTab = (rows: Conv[]) => {
    const working = rows.filter((c) => groupBusyCount(c.id) > 0);
    const unread = rows.filter(
      (c) =>
        c.kind === "group" &&
        (membersByRoom[c.title.slice(2)] === undefined ||
          !!membersError[c.title.slice(2)]),
    );
    if (working.length === 0) {
      return (
        <div className="p-3">
          <EmptyState
            title={
              unread.length === 0
                ? "No bots are working right now"
                : "Nothing shown as working"
            }
            hint={
              unread.length === 0
                ? "Every group roster was read, and none report a working member. This is live presence, not an error."
                : `Presence was not read for ${unread.length} group${unread.length === 1 ? "" : "s"} — a member could be working in one, but nothing unmeasured is shown as working.`
            }
          />
        </div>
      );
    }
    return (
      <>
        {unread.length > 0 && (
          <p className="px-3 py-1.5 text-[11px] text-amber-600 dark:text-amber-400">
            {unread.length} group{unread.length === 1 ? "" : "s"} not shown —
            presence was not read for {unread.length === 1 ? "it" : "them"}.
          </p>
        )}
        {working.map(renderConvRow)}
      </>
    );
  };

  /**
   * One flat conversation row.
   *
   * Direct threads always render through this. Group rooms render through it
   * as the GROUPS section's default list — each row carrying its members'
   * live presence — through the "Working now" lens, and as the fallback while
   * the tree read is unavailable; never beside the tree, so a room is still
   * listed only once at a time.
   */
  const renderConvRow = (c: Conv) => {
    const isActive =
      sel &&
      ((sel.kind === "group" && c.id === `group:${sel.name}`) ||
        (sel.kind === "dm" && c.id === `dm:${sel.peer}`));
    return (
      <button
        key={c.id}
        type="button"
        data-conv-row
        onClick={() =>
          c.kind === "group"
            ? openRoom(c.title.slice(2))
            : openDm(
                c.title,
                dms.find((d) => d.id === c.id)?.messages || [],
              )
        }
        aria-current={isActive ? "true" : undefined}
        className={`relative w-full flex items-center gap-3 px-3 py-2.5 text-left border-b border-border/40 transition-colors dur-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary/60 ${isActive ? "bg-primary/[0.07]" : "hover:bg-muted/40"}`}
      >
        {isActive && (
          <span
            className="absolute left-0 top-1.5 bottom-1.5 w-0.5 rounded-full bg-primary"
            aria-hidden="true"
          />
        )}
        <Avatar
          name={c.title}
          group={c.kind === "group"}
          size="lg"
        >
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
        </Avatar>
        <span className="flex-1 min-w-0">
          <span className="flex items-center gap-1.5">
            <span
              className={`text-xs truncate flex-1 ${c.unread > 0 ? "font-bold" : "font-semibold"}`}
            >
              {c.title}
            </span>
            <span
              className={`text-[10px] shrink-0 tabular-nums ${c.unread > 0 ? "text-primary font-semibold" : "text-muted-foreground"}`}
            >
              {fmtTime(c.lastAt)}
            </span>
          </span>
          <span className="flex items-center gap-1.5 mt-0.5">
            <span
              className={`text-[11px] truncate flex-1 ${c.unread > 0 ? "text-foreground/90" : "text-muted-foreground"}`}
            >
              {c.subtitle} — {c.lastText || "…"}
            </span>
            {c.unread > 0 && (
              <span
                className="text-[10px] font-bold bg-primary text-primary-foreground rounded-full min-w-5 h-5 inline-flex items-center justify-center px-1 shrink-0 tabular-nums"
                aria-label={`${c.unread} unread message${c.unread === 1 ? "" : "s"} in ${c.title}`}
              >
                {unreadLabel(c.unread)}
              </span>
            )}
          </span>
          {/* The bots themselves, with their measured presence — the row is a
              claim about who is in the room, so the roster behind it is read
              for every group (see the presence effect) and a dot only appears
              once that read answered. An unread roster shows neutral dots and
              says so on hover, never a green one nobody measured. */}
          {c.kind === "group" && c.members.length > 0 && (
            <span className="flex items-center gap-1 mt-1">
              {c.members.slice(0, 4).map((m) => {
                const list = membersByRoom[c.title.slice(2)];
                const read =
                  list !== undefined && !membersError[c.title.slice(2)];
                const entry = list?.find((x) => x.name === m);
                return (
                  <span
                    key={m}
                    className="relative"
                    title={
                      read
                        ? `${entry?.displayName || m} — ${entry?.state ?? "state not reported"}`
                        : `${m} — presence not read`
                    }
                  >
                    <Avatar name={m} size="sm" />
                    <span
                      className={`absolute -right-0.5 -bottom-0.5 size-2 rounded-full ring-1 ring-background ${
                        read
                          ? presenceDotClass(entry?.state ?? null)
                          : "bg-muted-foreground/30"
                      }`}
                      aria-hidden="true"
                    />
                  </span>
                );
              })}
              {c.members.length > 4 && (
                <span className="text-[9px] text-muted-foreground pl-0.5">
                  +{c.members.length - 4}
                </span>
              )}
            </span>
          )}
        </span>
      </button>
    );
  };

  return (
    <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
      <div className="max-w-none w-full flex-1 flex min-h-0 relative">
        {/* ── Conversation list ── */}
        <aside
          className={`w-full md:w-80 shrink-0 border-r border-border bg-card/40 flex-col min-h-0 ${sel ? "hidden md:flex" : "flex"}`}
          aria-label="Conversations"
        >
          <div className="p-3 space-y-2 border-b border-border/60">
            <div className="flex items-center gap-2">
              <h2 className="text-base font-semibold tracking-tight flex-1">
                Messages{" "}
                {totalUnread > 0 && (
                  <span className="ml-1 text-[10px] px-1.5 py-0.5 rounded-full bg-primary text-primary-foreground font-bold">
                    {totalUnread}
                  </span>
                )}
              </h2>
              <Btn
                variant="ghost"
                onClick={() => setLive((v) => !v)}
                title="Auto-refresh every 10 seconds"
              >
                {live ? (
                  <Pause className="size-3.5" aria-hidden="true" />
                ) : (
                  <Play className="size-3.5" aria-hidden="true" />
                )}{" "}
                {live ? "Live" : "Poll"}
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
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                onKeyDown={(e) => {
                  // Escape clears rather than blurring: a search that matched
                  // nothing is the state an operator most wants to back out of,
                  // and reaching for the mouse to undo it is the slowest path.
                  if (e.key === "Escape" && search) {
                    e.preventDefault();
                    setSearch("");
                  }
                }}
                placeholder="Search chats…"
                aria-label="Search chats"
                className={`${inputCls} pl-8 ${search ? "pr-8" : ""}`}
              />
              {search && (
                <button
                  type="button"
                  onClick={() => setSearch("")}
                  className="absolute right-2 top-1/2 -translate-y-1/2 p-0.5 rounded hover:bg-muted text-muted-foreground"
                  title="Clear search"
                  aria-label="Clear search"
                >
                  <X className="size-3.5" aria-hidden="true" />
                </button>
              )}
            </div>
            {/* Each chip counts what IT would show under the current search —
                measured from the same predicate the list renders with. A chip
                with no number invites the reader to press it to find out, and a
                chip that reads `0` is the useful answer: nothing to see. */}
            <div className="flex gap-1 overflow-x-auto flex-nowrap -mx-0.5 px-0.5 pb-0.5">
              {(
                [
                  "all",
                  "unread",
                  "groups",
                  "direct",
                  "a2u",
                  "decisions",
                  "blockers",
                ] as Filter[]
              ).map((f) => {
                const active = filter === f;
                const count = chipCounts[f];
                const label = FILTER_LABELS[f];
                return (
                  <button
                    key={f}
                    type="button"
                    onClick={() => setFilter(f)}
                    aria-pressed={active}
                    className={`shrink-0 text-[11px] px-2.5 py-1 rounded-full font-semibold inline-flex items-center gap-1 transition-colors dur-fast ${
                      active
                        ? "bg-primary text-primary-foreground elev-1"
                        : count === 0
                          ? // An empty chip is dimmed so it reads as "none", not as
                            // a filter waiting to be tried — but never hidden: a
                            // chip that disappears would take the zero with it.
                            "bg-muted/40 text-muted-foreground/70 hover:text-foreground hover:bg-muted/60"
                          : "bg-muted/60 text-muted-foreground hover:text-foreground hover:bg-muted"
                    }`}
                  >
                    {label}
                    <span
                      className={`text-[10px] font-bold tabular-nums ${active ? "text-primary-foreground/80" : "text-muted-foreground/80"}`}
                    >
                      {count}
                    </span>
                  </button>
                );
              })}
            </div>
            {/* What the filter and search hid. `null` when nothing was hidden,
                so the happy path carries no sentence at all. */}
            {(() => {
              const hidden = hiddenSummary(convs.length, allConvs.length);
              return hidden ? (
                <p
                  className="text-[11px] text-muted-foreground"
                  data-messages-hidden-summary
                >
                  {hidden}
                </p>
              ) : null;
            })()}
            {/* A decisions/blockers filter can only search history that was
                read. Those rooms are excluded above — excluding them silently
                would read as "no decisions in this workspace", so the count of
                rooms that were never searched is stated beside the filter. */}
            {(filter === "decisions" || filter === "blockers") &&
              (() => {
                const unsearched = allConvs.filter((c) => {
                  const msgs =
                    c.kind === "group"
                      ? roomMsgs[c.title.slice(2)]
                      : dms.find((d) => d.id === c.id)?.messages;
                  return !msgs;
                }).length;
                return unsearched > 0 ? (
                  <p
                    className="text-[11px] text-amber-600 dark:text-amber-400"
                    data-messages-unsearched
                  >
                    {unsearched} room{unsearched === 1 ? "" : "s"} excluded —{" "}
                    {unsearched === 1 ? "its" : "their"} history has not been
                    read yet, so {unsearched === 1 ? "it was" : "they were"} not
                    searched. Not a clean result.
                  </p>
                ) : null;
              })()}
            {/* An empty A→U list has two possible causes, and they are not the
                same claim: no agent has written to you, or the rows that did
                never reported a recipient (the mailbox mapper stores a missing
                `receiver_name` as ""). Say so when the list is empty, because
                "no inbound mail" would be the wrong of the two. */}
            {filter === "a2u" &&
              convs.length === 0 &&
              dms.length > 0 && (
                <p
                  className="text-[11px] text-muted-foreground"
                  data-messages-a2u-note
                >
                  A→U counts only mail an agent addressed to you by name. None
                  of the {dms.length} direct thread{dms.length === 1 ? "" : "s"}{" "}
                  read here carries one — a message with no reported recipient is
                  not counted.
                </p>
              )}
            {/* Group creation controls render with the GROUPS section — see
                renderGroupControls — not here in the fixed header. This header
                keeps only the filters and the per-list read failures. */}
            <div className="flex gap-2">
              <div className="relative flex-1 min-w-0">
                <AtSign
                  className="size-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none"
                  aria-hidden="true"
                />
                <input
                  value={newDm}
                  onChange={(e) => setNewDm(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") startDm();
                  }}
                  placeholder="Message an agent directly…"
                  aria-label="Start direct chat"
                  className={`${inputCls} pl-8`}
                />
              </div>
              <button
                type="button"
                onClick={startDm}
                disabled={!newDm.trim() || !props.threadId}
                className="size-9 shrink-0 rounded-xl bg-muted/60 border border-border/70 inline-flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-muted disabled:opacity-40 transition-colors dur-fast"
                title={
                  props.threadId
                    ? "Open a direct chat with this agent"
                    : "Open a chat first — direct threads live inside one"
                }
                aria-label="Open direct chat"
              >
                <UserPlus className="size-4" aria-hidden="true" />
              </button>
            </div>
            {!props.threadId && (
              <p className="text-[11px] text-muted-foreground">
                A2A chats live inside a conversation — open or start a chat
                to unlock them. Groups work anytime.
              </p>
            )}
          </div>

          {/* Per-list failures: shown as unavailable, never as an empty list. */}
          {(roomsError || dmsError || rosterError) && (
            <div className="px-3 py-2 space-y-2 border-b border-border/60">
              {roomsError && (
                <ErrorBox
                  message={`Group list unavailable — failed to load, not empty. (${roomsError})`}
                  onRetry={() => load()}
                />
              )}
              {dmsError && (
                <ErrorBox
                  message={`A2A threads unavailable — failed to load, not empty. (${dmsError})`}
                  onRetry={() => load()}
                />
              )}
              {rosterError && (
                <ErrorBox
                  message={`Agent roster unavailable — statuses may show as unknown. (${rosterError})`}
                  onRetry={() => load()}
                />
              )}
            </div>
          )}

          <div
            ref={convListRef}
            onKeyDown={onConvKeyDown}
            className="flex-1 overflow-y-auto"
          >
            {loading ? (
              <div className="p-3">
                <SkeletonList rows={5} />
              </div>
            ) : error && convs.length === 0 ? (
              <div className="p-3">
                <ErrorBox message={error} onRetry={() => load()} />
              </div>
            ) : convs.length === 0 && (roomsError || dmsError) ? (
              <div className="p-3">
                <ErrorBox
                  message="Conversations couldn't be loaded — this is a fetch failure, not an empty inbox."
                  onRetry={() => load()}
                />
              </div>
            ) : convs.length === 0 && allConvs.length > 0 ? (
              /* A filter that hides everything is NOT an empty inbox. The
                 `hiddenSummary` line above already says "Showing 0 of 12 — 12
                 hidden by this filter or search"; rendering "No chats yet"
                 under it contradicted that sentence and then prescribed a
                 remedy — create a group — for a problem the operator does not
                 have. The two readings ("you have none" and "you are looking at
                 none") are opposite, so this is a separate state. */
              <div className="p-3">
                <EmptyState
                  title="Nothing matches this filter"
                  hint={`${allConvs.length} conversation${allConvs.length === 1 ? " is" : "s are"} hidden — this is the filter and search, not an empty inbox.`}
                  action={
                    <Btn
                      variant="ghost"
                      onClick={() => {
                        setFilter("all");
                        setSearch("");
                      }}
                    >
                      Clear filter and search
                    </Btn>
                  }
                />
              </div>
            ) : convs.length === 0 ? (
              /* No conversations at all — so no Groups section exists to hold
                 the creation controls. This empty state is the one other
                 place they render; the two sites are mutually exclusive. */
              <div className="p-3 space-y-3">
                <EmptyState
                  title="No chats yet"
                  hint="Create a group to get started, or message an agent directly once a chat is open."
                />
                {renderGroupControls()}
              </div>
            ) : (
              /* Two sections, not one flat run: group rooms and direct threads
                 answer different questions ("where is the work?" vs "who pinged
                 me?"). The Groups half renders the structured tree — the one
                 place group rooms are listed — while the Direct half keeps its
                 flat rows. An empty half is omitted by `sectionConversations`,
                 so a workspace with no DMs never shows a "Direct (0)" header
                 over nothing. */
              sectionConversations(
                convs,
                // The header repeats the chip that produced these rows, so the
                // A→U lens is never rendered under an "A2A" header.
                filter === "a2u" ? FILTER_LABELS.a2u : FILTER_LABELS.direct,
              ).map((section) => (
                <div key={section.key}>
                  <div className="sticky top-0 z-10 flex items-baseline gap-2 px-3 py-1.5 bg-card/95 backdrop-blur border-b border-border/60">
                    <h3 className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                      {section.label}
                    </h3>
                    <span className="text-[10px] text-muted-foreground/80 tabular-nums">
                      {section.rows.length}
                    </span>
                    {section.key === "direct" && !props.threadId && (
                      <span className="text-[10px] text-muted-foreground/80">
                        · open a chat to post
                      </span>
                    )}
                  </div>
                  {/* The GROUPS section is a tabbed surface: the default tab
                      lists every group with its live bots, the Tree tab shows
                      the nested structure, and Working now narrows to the
                      rooms whose measured roster has someone working. Exactly
                      one lens renders, so every room is still listed once.
                      Group creation renders here too — controls live with the
                      listing they act on, not in the fixed header. */}
                  {section.key === "groups" ? (
                    <>
                      {renderGroupControls()}
                      {renderGroupTabs()}
                      {groupsTab === "working" ? (
                        renderWorkingTab(section.rows)
                      ) : groupsTab === "tree" ? (
                        treeError ? (
                          <>
                            <div className="p-3">
                              <ErrorBox
                                message={`Group structure unavailable — subgroups could not be read, not empty. (${treeError})`}
                                onRetry={() => load()}
                              />
                            </div>
                            {section.rows.map(renderConvRow)}
                          </>
                        ) : tree.length > 0 ? (
                          <div
                            className="px-1 py-1.5"
                            aria-label="Group structure"
                          >
                            <GroupTreeSidebar
                              embedded
                              nodes={groupVisibleTree}
                              busyByRoom={busyByRoom}
                              errorsByRoom={membersError}
                              activeRoom={
                                sel?.kind === "group" ? sel.name : null
                              }
                              collapsed={collapsed}
                              onToggle={(id) =>
                                setCollapsed((prev) => {
                                  const next = new Set(prev);
                                  if (next.has(id)) next.delete(id);
                                  else next.add(id);
                                  return next;
                                })
                              }
                              onOpen={(name) => openRoom(name)}
                              onNewSubgroup={(name) => setSubgroupFor(name)}
                              onMerge={onMergeSubgroups}
                              unreadByRoom={unreadByRoom}
                            />
                          </div>
                        ) : (
                          section.rows.map(renderConvRow)
                        )
                      ) : (
                        section.rows.map(renderConvRow)
                      )}
                    </>
                  ) : (
                    section.rows.map(renderConvRow)
                  )}
                </div>
              ))
            )}
          </div>
        </aside>

        {/* ── Chat window ── */}
        <section
          className={`flex-1 flex-col min-w-0 min-h-0 bg-background ${sel ? "flex" : "hidden md:flex"}`}
          aria-label="Messages"
        >
          {!sel ? (
            <div className="flex-1 flex items-center justify-center p-6">
              <EmptyState
                title="Pick a conversation"
                hint="Choose a group or a direct thread on the left — like WhatsApp, everything lands here."
              />
            </div>
          ) : (
            <>
              <header className="shrink-0 px-4 py-2.5 border-b border-border/60 bg-card/60 backdrop-blur flex items-center gap-3">
                <button
                  type="button"
                  onClick={() => setSel(null)}
                  className="md:hidden p-1.5 rounded-lg hover:bg-muted"
                  aria-label="Back to chats"
                >
                  <ArrowLeft className="size-4" aria-hidden="true" />
                </button>
                <Avatar
                  name={activeTitle}
                  group={sel.kind === "group"}
                  size="lg"
                />
                <div className="flex-1 min-w-0">
                  {sel.kind === "group" && crumbs.length > 0 && (
                    <GroupBreadcrumbs
                      chain={crumbs}
                      current={sel.name}
                      onOpen={(name) => openRoom(name)}
                    />
                  )}
                  <p className="text-sm font-semibold truncate leading-tight">
                    {activeTitle}
                  </p>
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
                  className="inline-flex items-center gap-1 text-[11px] font-semibold px-2.5 py-1 rounded-full bg-secondary/60 border border-border/50 hover:bg-secondary text-muted-foreground shrink-0 transition-colors dur-fast"
                  title="Show who is in this group"
                  aria-label={`Show the ${activeMembers.length} members of this group`}
                >
                  <Users className="size-3" aria-hidden="true" />
                  {activeMembers.length}
                </button>
                <button
                  type="button"
                  onClick={() => setShowDetails((v) => !v)}
                  className="p-2 rounded-lg hover:bg-muted text-muted-foreground transition-colors dur-fast"
                  title="Details"
                  aria-label="Toggle details"
                  aria-expanded={showDetails}
                >
                  <Info className="size-4" aria-hidden="true" />
                </button>
              </header>

              <div
                ref={transcriptRef}
                onScroll={onTranscriptScroll}
                className="flex-1 overflow-y-auto px-4 sm:px-8 py-4 space-y-1.5"
              >
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
                      action={
                        <Btn
                          onClick={() =>
                            sel.kind === "group" && openRoom(sel.name)
                          }
                        >
                          Try again
                        </Btn>
                      }
                    />
                  </div>
                ) : sel.kind === "group" && roomRead?.state === "loading" ? (
                  <div className="h-full flex items-center justify-center">
                    <p className="text-[11px] text-muted-foreground">
                      Reading {sel.name}…
                    </p>
                  </div>
                ) : activeMsgs.length === 0 ? (
                  <div className="h-full flex items-center justify-center">
                    <EmptyState
                      title="No messages yet"
                      hint="Say hello below — agents reply here and the run history stays attached."
                    />
                  </div>
                ) : (
                  /* Runs first, messages second: consecutive rows from one
                     author on one day are ONE utterance. `groupMessageRuns`
                     breaks on author, on day, and on a deleted row (a
                     tombstone renders as its own italic line, so sharing a
                     header with the message above it would imply the two were
                     written together). */
                  groupMessageRuns(activeMsgs, (m) => dayLabel(m.at)).map(
                    (run, ri) => {
                      const showDay = Boolean(run.day) && run.day !== lastDay;
                      if (showDay) lastDay = run.day;
                      return (
                        <React.Fragment
                          key={`run-${ri}-${run.items[0]?.id ?? ri}`}
                        >
                          {showDay && (
                            <div className="flex justify-center py-2.5">
                              <span className="text-[10px] font-semibold px-3 py-1 rounded-full bg-muted/70 border border-border/50 text-muted-foreground">
                                {run.day}
                              </span>
                            </div>
                          )}
                          {/* Gap-0.5 inside a run against the scroller's
                            space-y-1.5 between them: the cluster is visibly one
                            block without drawing a box around it. The avatar in
                            the gutter repeats the sender the bubble already
                            names — a mark, never a second identity claim. */}
                          <div className="flex items-start gap-2">
                            {run.items[0] && run.items[0].sender !== OPERATOR ? (
                              <Avatar
                                name={run.items[0].sender}
                                size="sm"
                                className="mt-1"
                              />
                            ) : null}
                            <div className="flex flex-col gap-0.5 flex-1 min-w-0">
                            {run.items.map((m, i) => {
                              const mine = m.sender === OPERATOR;
                              /* The sender is named on the run's first bubble
                               only — an agent answering six times in a row
                               used to print its name six times, which reads as
                               six speakers rather than one continuing one. */
                              const showSender = i === 0;
                              return (
                                <React.Fragment key={`${m.id}-${i}`}>
                                  {(() => {
                                    const dmMatch = m.content?.match(
                                      /^\[DM from ([^\]]+)\]\s*([\s\S]*)$/,
                                    );
                                    const isA2A = Boolean(dmMatch);
                                    const a2aSender = dmMatch
                                      ? dmMatch[1]
                                      : null;
                                    const cleanContent = dmMatch
                                      ? dmMatch[2]
                                      : m.content;
                                    const isEditing = editing?.id === m.id;
                                    const busy = !!busyMsg[m.id];
                                    const replyQuote = quoted(m);
                                    const reactions = m.reactions ?? {};
                                    const reactionEntries = Object.entries(
                                      reactions,
                                    ).filter(([, who]) => who.length > 0);

                                    return (
                                      <div
                                        className={`flex ${mine ? "justify-end" : "justify-start"} group/bubble`}
                                      >
                                        <div
                                          className={`max-w-[80%] sm:max-w-[70%] rounded-2xl px-3.5 py-2 elev-1 relative ${
                                            mine
                                              ? "bg-emerald-600 text-white rounded-br-md"
                                              : isA2A
                                                ? "bg-card border border-blue-500/40 rounded-bl-md"
                                                : "bg-card border border-border/60 rounded-bl-md"
                                          }`}
                                        >
                                          {!mine && (showSender || isA2A) && (
                                            <div className="flex items-center gap-1.5 flex-wrap mb-0.5">
                                              {showSender && (
                                                <p
                                                  className="text-[11px] font-bold"
                                                  style={{
                                                    color: senderColor(
                                                      m.sender,
                                                    ),
                                                  }}
                                                >
                                                  {m.sender}
                                                </p>
                                              )}
                                              {/* Per-message provenance, so it stays on
                                                every row of the run even after the
                                                name above stopped repeating. */}
                                              {isA2A && (
                                                <span className="text-[9px] font-mono px-1 py-0.5 rounded bg-blue-500/15 text-blue-400">
                                                  ⚡ from @{a2aSender}
                                                </span>
                                              )}
                                            </div>
                                          )}

                                          {/* Forward provenance: stated, because a message
                                            that crossed rooms is a different claim
                                            from one authored here. */}
                                          {m.forwardedFrom && (
                                            <p
                                              className={`text-[10px] italic mb-1 ${mine ? "text-white/75" : "text-muted-foreground"}`}
                                            >
                                              ↪ forwarded from #
                                              {m.forwardedFrom.room}
                                              {m.forwardedFrom.sender
                                                ? ` by ${m.forwardedFrom.sender}`
                                                : ""}
                                            </p>
                                          )}

                                          {/* A reply quotes its target. A target that is
                                            gone says so instead of rendering an empty
                                            quote box. */}
                                          {m.replyTo && (
                                            <div
                                              className={`text-[10px] mb-1 pl-2 border-l-2 rounded-r ${
                                                mine
                                                  ? "border-white/50 text-white/80"
                                                  : "border-primary/50 text-muted-foreground"
                                              }`}
                                            >
                                              {replyQuote ? (
                                                <>
                                                  <span className="font-bold">
                                                    {replyQuote.sender}
                                                  </span>
                                                  {": "}
                                                  {replyQuote.deleted ? (
                                                    <em>message deleted</em>
                                                  ) : (
                                                    replyQuote.content.slice(
                                                      0,
                                                      120,
                                                    )
                                                  )}
                                                </>
                                              ) : (
                                                <em>
                                                  original message not available
                                                </em>
                                              )}
                                            </div>
                                          )}

                                          {m.kind !== "discussion" && (
                                            <span
                                              className={`inline-block text-[10px] font-bold px-1.5 py-0.5 rounded-md mt-0.5 mb-1 ${kindTone(m.kind) === "green" ? "bg-emerald-500/15 text-emerald-600" : kindTone(m.kind) === "amber" ? "bg-amber-500/15 text-amber-600" : kindTone(m.kind) === "blue" ? "bg-primary/15 text-primary" : "bg-muted text-muted-foreground"}`}
                                            >
                                              {m.kind
                                                .replace(/_/g, " ")
                                                .toUpperCase()}
                                            </span>
                                          )}

                                          {m.deleted ? (
                                            <p
                                              className={`text-[13px] italic ${mine ? "text-white/70" : "text-muted-foreground"}`}
                                            >
                                              This message was deleted.
                                            </p>
                                          ) : isEditing ? (
                                            <div className="space-y-1.5">
                                              <textarea
                                                value={editing.text}
                                                onChange={(e) =>
                                                  setEditing({
                                                    id: m.id,
                                                    text: e.target.value,
                                                  })
                                                }
                                                onKeyDown={(e) => {
                                                  if (
                                                    e.key === "Enter" &&
                                                    (e.metaKey || e.ctrlKey)
                                                  )
                                                    onSaveEdit(m.id);
                                                  if (e.key === "Escape")
                                                    setEditing(null);
                                                }}
                                                rows={3}
                                                aria-label="Edit message"
                                                className="w-full text-[13px] rounded-lg border border-border bg-background p-2 focus:outline-none focus:ring-1 focus:ring-primary/40"
                                              />
                                              <div className="flex items-center gap-2 justify-end">
                                                <Btn
                                                  variant="ghost"
                                                  onClick={() =>
                                                    setEditing(null)
                                                  }
                                                >
                                                  Cancel
                                                </Btn>
                                                <Btn
                                                  onClick={() =>
                                                    onSaveEdit(m.id)
                                                  }
                                                  disabled={
                                                    savingEdit ||
                                                    !editing.text.trim()
                                                  }
                                                >
                                                  {savingEdit
                                                    ? "Saving…"
                                                    : "Save"}
                                                </Btn>
                                              </div>
                                            </div>
                                          ) : (
                                            <p className="text-[13px] leading-relaxed whitespace-pre-wrap break-words">
                                              {cleanContent}
                                            </p>
                                          )}

                                          {/* Reactions render only what the server
                                            returned. An absent set renders nothing at
                                            all rather than an empty strip. */}
                                          {reactionEntries.length > 0 && (
                                            <div className="flex flex-wrap gap-1 mt-1.5">
                                              {reactionEntries.map(
                                                ([emoji, who]) => (
                                                  <button
                                                    key={emoji}
                                                    type="button"
                                                    onClick={() =>
                                                      onReact(m, emoji)
                                                    }
                                                    disabled={busy}
                                                    title={who.join(", ")}
                                                    aria-label={`${emoji} reacted by ${who.join(", ")}`}
                                                    className={`text-[11px] px-2 py-0.5 rounded-full border transition-colors dur-fast disabled:opacity-50 ${
                                                      who.includes(OPERATOR)
                                                        ? "border-primary/60 bg-primary/15"
                                                        : "border-border/60 bg-muted/40 hover:bg-muted"
                                                    }`}
                                                  >
                                                    {emoji} {who.length}
                                                  </button>
                                                ),
                                              )}
                                            </div>
                                          )}

                                          {reactingTo === m.id &&
                                            !m.deleted && (
                                              <div className="flex flex-wrap gap-1 mt-1.5 p-1.5 rounded-lg bg-muted/60 border border-border/60">
                                                {REACTION_EMOJI.map((emoji) => (
                                                  <button
                                                    key={emoji}
                                                    type="button"
                                                    onClick={() =>
                                                      onReact(m, emoji)
                                                    }
                                                    disabled={busy}
                                                    aria-label={`React with ${emoji}`}
                                                    className="text-sm px-1 rounded hover:bg-muted disabled:opacity-50"
                                                  >
                                                    {emoji}
                                                  </button>
                                                ))}
                                              </div>
                                            )}

                                          <p
                                            className={`text-[10px] mt-1 flex items-center gap-1 justify-end ${mine ? "text-white/70" : "text-muted-foreground"}`}
                                          >
                                            {/* Pin state comes from the server's
                                              `pinned` boolean — a pinned message
                                              must say so here, or the room's pin
                                              is invisible in the transcript. */}
                                            {m.pinned && (
                                              <span
                                                className="inline-flex items-center gap-0.5 text-amber-500/90 font-medium"
                                                title="Pinned to this room"
                                              >
                                                <Pin
                                                  className="size-3"
                                                  aria-hidden="true"
                                                />
                                                pinned
                                              </span>
                                            )}
                                            {m.editedAt && !m.deleted && (
                                              <span
                                                title={`Edited ${absoluteStamp(m.editedAt) ?? ""}`}
                                              >
                                                edited
                                              </span>
                                            )}
                                            <span
                                              title={
                                                absoluteStamp(m.at) ??
                                                "no recorded time"
                                              }
                                            >
                                              {fmtTime(m.at)}
                                            </span>
                                            {mine &&
                                              !m.deleted &&
                                              (m.read ? (
                                                <CheckCheck className="size-3" />
                                              ) : (
                                                <Check className="size-3" />
                                              ))}
                                          </p>

                                          {/* Per-message actions. Hover-revealed on
                                            pointer devices, always present for keyboard
                                            and touch so the control is reachable. */}
                                          {!m.deleted && (
                                            <div className="flex items-center gap-0.5 absolute -top-3 right-2 rounded-full border border-border/70 bg-popover/95 backdrop-blur px-1 py-0.5 elev-3 opacity-0 focus-within:opacity-100 group-hover/bubble:opacity-100 transition-opacity">
                                              <BubbleAction
                                                title="Reply"
                                                onClick={() => setReplyTo(m)}
                                              >
                                                <CornerUpLeft
                                                  className="size-3"
                                                  aria-hidden="true"
                                                />
                                                <span className="sr-only">
                                                  Reply to {m.sender}
                                                </span>
                                              </BubbleAction>
                                              <BubbleAction
                                                title="React"
                                                onClick={() =>
                                                  setReactingTo((cur) =>
                                                    cur === m.id ? null : m.id,
                                                  )
                                                }
                                              >
                                                <Smile
                                                  className="size-3"
                                                  aria-hidden="true"
                                                />
                                                <span className="sr-only">
                                                  React to message from{" "}
                                                  {m.sender}
                                                </span>
                                              </BubbleAction>
                                              {sel.kind === "group" && (
                                                <>
                                                  <BubbleAction
                                                    title="Edit"
                                                    onClick={() =>
                                                      setEditing({
                                                        id: m.id,
                                                        text: m.content,
                                                      })
                                                    }
                                                  >
                                                    <Pencil
                                                      className="size-3"
                                                      aria-hidden="true"
                                                    />
                                                    <span className="sr-only">
                                                      Edit message from{" "}
                                                      {m.sender}
                                                    </span>
                                                  </BubbleAction>
                                                  <BubbleAction
                                                    title="Forward"
                                                    onClick={() => {
                                                      setForwarding(m);
                                                      setForwardRoom("");
                                                    }}
                                                  >
                                                    <Forward
                                                      className="size-3"
                                                      aria-hidden="true"
                                                    />
                                                    <span className="sr-only">
                                                      Forward message from{" "}
                                                      {m.sender}
                                                    </span>
                                                  </BubbleAction>
                                                  <BubbleAction
                                                    title="Delete"
                                                    danger
                                                    onClick={() =>
                                                      onDeleteMessage(m)
                                                    }
                                                  >
                                                    <Trash2
                                                      className="size-3"
                                                      aria-hidden="true"
                                                    />
                                                    <span className="sr-only">
                                                      Delete message from{" "}
                                                      {m.sender}
                                                    </span>
                                                  </BubbleAction>
                                                </>
                                              )}
                                              <BubbleAction
                                                title={
                                                  copied === m.id
                                                    ? "Copied"
                                                    : "Copy"
                                                }
                                                onClick={() => onCopy(m)}
                                              >
                                                <Copy
                                                  className="size-3"
                                                  aria-hidden="true"
                                                />
                                                <span className="sr-only">
                                                  Copy message from {m.sender}
                                                </span>
                                              </BubbleAction>
                                            </div>
                                          )}
                                        </div>
                                      </div>
                                    );
                                  })()}
                                </React.Fragment>
                              );
                              })}
                            </div>
                          </div>
                        </React.Fragment>
                      );
                    },
                  )
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
                        {forwarding.deleted
                          ? "This message was deleted."
                          : forwarding.content}
                      </p>
                      <div className="flex items-center gap-2">
                        <input
                          value={forwardRoom}
                          onChange={(e) => setForwardRoom(e.target.value)}
                          placeholder="target-group"
                          aria-label="Target group name"
                          className={inputCls}
                        />
                        <Btn
                          onClick={onForward}
                          disabled={!forwardRoom.trim() || forwardingBusy}
                        >
                          {forwardingBusy ? "Forwarding…" : "Forward"}
                        </Btn>
                        <Btn
                          variant="ghost"
                          onClick={() => setForwarding(null)}
                        >
                          Cancel
                        </Btn>
                      </div>
                      {rooms.length > 0 && (
                        <p className="text-[10px] text-muted-foreground">
                          Existing groups:{" "}
                          {rooms
                            .filter(
                              (r) =>
                                r.name !==
                                (sel?.kind === "group" ? sel.name : null),
                            )
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
                      <Reply
                        className="size-3.5 text-primary mt-0.5 shrink-0"
                        aria-hidden="true"
                      />
                      <div className="flex-1 min-w-0">
                        <p className="text-[11px] font-semibold text-primary">
                          Replying to {replyTo.sender}
                        </p>
                        <p className="text-[11px] text-muted-foreground line-clamp-2">
                          {replyTo.content}
                        </p>
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

                  {/* The tag strip. A tag that addresses nobody is silent by
                      construction — the server resolves an unknown handle to
                      nothing rather than a near match — so the resolution is
                      previewed here, before the only moment it can still be
                      fixed. Chips carry the exact reason, never a generic
                      "invalid". */}
                  {countMentionTokens(draft) > 0 && (
                    <div className="flex flex-wrap items-center gap-1 rounded-xl border border-border/60 bg-muted/30 px-2.5 py-1.5">
                      <span
                        className="text-[10px] font-bold uppercase tracking-wide text-muted-foreground mr-0.5"
                        title={
                          sel.kind === "group"
                            ? "Resolved by this room's server preview"
                            : "Resolved by the local roster mirror — the server decides at send"
                        }
                      >
                        Tags address
                      </span>
                      {(tagStrip?.resolvedHandles ?? []).map((handle) => (
                        <span
                          key={handle}
                          className="inline-flex items-center gap-0.5 text-[10px] font-semibold rounded-full bg-primary/10 text-primary px-2 py-0.5"
                        >
                          <AtSign className="size-2.5" aria-hidden="true" />
                          {handle}
                        </span>
                      ))}
                      {(tagStrip?.unresolved ?? []).map((u) => (
                        <span
                          key={u.raw}
                          title={u.reason}
                          className="inline-flex items-center gap-0.5 text-[10px] font-semibold rounded-full bg-destructive/10 text-destructive px-2 py-0.5"
                        >
                          {u.raw} — addresses nobody: {u.reason}
                        </span>
                      ))}
                      {sel.kind === "group" && !tagStrip && !serverPreviewError && (
                        <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground">
                          <Loader2 className="size-3 animate-spin" aria-hidden="true" />
                          checking with the room…
                        </span>
                      )}
                      {sel.kind === "group" && serverPreviewError && (
                        <span className="text-[10px] text-destructive">
                          Room preview failed ({serverPreviewError})
                        </span>
                      )}
                      {tagStripFromRoom && (
                        <span className="ml-auto text-[10px] text-muted-foreground/80">
                          room roster
                        </span>
                      )}
                      {tagStripFromMirror && (
                        <span
                          className="ml-auto text-[10px] text-muted-foreground/80"
                          title="The room's preview could not be read, so this is the client's mirror of the roster grammar."
                        >
                          local roster mirror
                        </span>
                      )}
                    </div>
                  )}

                  {/* The composer reads as ONE raised field: the kind picker,
                      the input and send inside a single focus-ring card, so
                      the keyboard focus state is the whole control rather
                      than three adjacent boxes. The @ palette opens above it,
                      anchored to the card. */}
                  <div className="relative rounded-2xl border border-border/70 bg-card elev-1 transition-colors dur-fast focus-within:border-primary/40 focus-within:ring-2 focus-within:ring-primary/20">
                    {mentionRows.length > 0 && (
                      <div
                        id="messages-mention-palette"
                        role="listbox"
                        aria-label="Tag an agent or a role"
                        className="absolute bottom-full left-0 right-0 mb-2 rounded-2xl border border-border/70 bg-popover elev-3 overflow-hidden z-20"
                      >
                        <div className="flex items-center justify-between gap-2 px-3 py-2 border-b border-border/60 bg-muted/30">
                          <p className="text-[10px] font-bold uppercase tracking-wide text-muted-foreground">
                            Tag an agent or a role
                          </p>
                          {mentionFilter && mentionFilter.hidden > 0 && (
                            <p className="text-[10px] text-muted-foreground">
                              {mentionFilter.hidden} more — keep typing
                            </p>
                          )}
                        </div>
                        <div className="max-h-72 overflow-y-auto py-1">
                          {mentionSections.map((section) => (
                            <div key={section.id}>
                              <p className="flex items-center gap-1.5 px-3 pt-2 pb-1 text-[10px] font-bold uppercase tracking-wide text-muted-foreground/80">
                                {section.icon}
                                {section.label}
                              </p>
                              {section.rows.map(({ row, index }) => (
                                <button
                                  key={row.id}
                                  id={`messages-mention-option-${index}`}
                                  type="button"
                                  role="option"
                                  aria-selected={index === activeMention}
                                  disabled={!!row.refusal}
                                  title={row.refusal ?? row.token}
                                  onMouseDown={(e) => {
                                    e.preventDefault();
                                    chooseMention(row);
                                  }}
                                  onMouseEnter={() => setMentionIndex(index)}
                                  className={`w-full flex items-center gap-2.5 px-3 py-1.5 text-left transition-colors dur-fast ${
                                    index === activeMention
                                      ? "bg-primary/10"
                                      : "hover:bg-muted/60"
                                  } ${row.refusal ? "opacity-50 cursor-not-allowed" : ""}`}
                                >
                                  <span
                                    className="size-7 rounded-full inline-flex items-center justify-center text-[10px] font-bold text-white shrink-0"
                                    style={{
                                      backgroundColor: senderColor(row.title),
                                    }}
                                    aria-hidden="true"
                                  >
                                    {row.mode === "everyone" ? (
                                      <Users className="size-3.5" />
                                    ) : row.mode === "role" ? (
                                      <AtSign className="size-3.5" />
                                    ) : (
                                      initials(row.title)
                                    )}
                                  </span>
                                  <span className="min-w-0 flex-1">
                                    <span className="flex items-baseline gap-1.5 min-w-0">
                                      <span className="text-[11px] font-semibold truncate">
                                        {row.title}
                                      </span>
                                      {row.subtitle && (
                                        <span className="text-[10px] text-muted-foreground truncate">
                                          {row.subtitle}
                                        </span>
                                      )}
                                    </span>
                                    <span className="block text-[10px] font-mono text-muted-foreground/90 truncate">
                                      {row.token}
                                    </span>
                                  </span>
                                  <span className="shrink-0 text-[10px] text-muted-foreground rounded-full bg-muted/70 px-1.5 py-0.5">
                                    {row.refusal ? "unavailable" : row.badge}
                                  </span>
                                </button>
                              ))}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                    <div className="flex items-end gap-1 p-1.5">
                      <select
                        value={kind}
                        onChange={(e) => setKind(e.target.value)}
                        className="text-[11px] bg-transparent border-0 rounded-xl px-2 py-2.5 font-semibold text-muted-foreground hover:text-foreground hover:bg-muted/60 cursor-pointer focus:outline-none shrink-0 transition-colors dur-fast"
                        title="Message type"
                        aria-label="Message type"
                      >
                        {(sel?.kind === "dm" ? DM_MESSAGE_KINDS : MESSAGE_KINDS).map((k) => (
                          <option key={k} value={k}>
                            {k.replace(/_/g, " ")}
                          </option>
                        ))}
                      </select>
                      <input
                        value={draft}
                        onChange={(e) => {
                          setDraft(e.target.value);
                          setCaret(e.target.selectionStart ?? 0);
                          // Fresh input re-opens the palette; only Escape
                          // keeps it closed for the current token.
                          setMentionDismissed(false);
                        }}
                        onSelect={(e) =>
                          setCaret(
                            (e.target as HTMLInputElement).selectionStart ?? 0,
                          )
                        }
                        onClick={(e) =>
                          setCaret(
                            (e.target as HTMLInputElement).selectionStart ?? 0,
                          )
                        }
                        onKeyDown={onComposerKeyDown}
                        placeholder={`Message ${sel.kind === "group" ? "the group" : activeTitle}…`}
                        aria-label="Write a message"
                        role="combobox"
                        aria-autocomplete="list"
                        aria-expanded={mentionRows.length > 0}
                        aria-controls="messages-mention-palette"
                        aria-activedescendant={
                          mentionRows.length > 0 && activeMention >= 0
                            ? `messages-mention-option-${activeMention}`
                            : undefined
                        }
                        className="flex-1 min-w-0 bg-transparent border-0 text-[13px] px-2 py-2.5 focus:outline-none placeholder:text-muted-foreground/70"
                      />
                      <button
                        type="button"
                        onClick={send}
                        disabled={!draft.trim() || sending}
                        className="size-9 rounded-xl bg-emerald-600 text-white flex items-center justify-center shrink-0 disabled:opacity-40 hover:bg-emerald-500 transition-colors dur-fast"
                        title="Send"
                        aria-label="Send message"
                      >
                        <Send className="size-4" />
                      </button>
                    </div>
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
            onOpenProjectDetails={props.onOpenProjectDetails}
            members={activeMembers}
            presence={activePresence}
            presenceError={activeMembersError}
            activity={
              sel?.kind === "group" ? (activityByRoom[sel.name] ?? null) : null
            }
            activityError={
              sel?.kind === "group" ? (activityError[sel.name] ?? null) : null
            }
            activityBusy={activityBusy}
            onRefreshActivity={async () => {
              if (sel?.kind !== "group") return;
              setActivityBusy(true);
              try {
                await loadActivity(sel.name);
              } finally {
                setActivityBusy(false);
              }
            }}
            roster={activeRoster}
            rosterError={activeRosterError}
            buckets={activeBuckets}
            headline={membershipHeadline(activeRoster, activeRosterError)}
            canNest={acceptsSubgroups(
              buildTree(tree, {}).find(
                (e) =>
                  e.node.name === (sel?.kind === "group" ? sel.name : null),
              ) ?? null,
            )}
            crumbs={crumbs}
            onOpenRoom={(name) => openRoom(name)}
            onAddSubgroup={(name) => setSubgroupFor(name)}
            tree={tree}
            onRefreshRoster={() =>
              sel?.kind === "group" ? loadMembers(sel.name) : Promise.resolve()
            }
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
            onRefresh={() =>
              sel.kind === "group" ? openRoom(sel.name) : load(true)
            }
            onDeleteRoom={async () => {
              if (sel.kind !== "group") return;
              if (
                !window.confirm(`Delete group "${sel.name}" and its history?`)
              )
                return;
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

/** One member row in the roster: avatar, dot, name, optional role, and its state. */
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
      className={`flex items-center gap-2.5 text-[11px] rounded-lg px-1.5 py-1.5 hover:bg-muted/40 transition-colors dur-fast ${props.muted ? "opacity-80" : ""}`}
      title={props.title}
    >
      <Avatar name={props.name} size="sm">
        <span
          className={`absolute -right-0.5 -bottom-0.5 size-2.5 rounded-full ring-2 ring-background ${props.dotClass}`}
          aria-hidden="true"
        />
      </Avatar>
      <span className="min-w-0 flex-1">
        <span
          className="font-semibold truncate block"
          style={{ color: mine ? undefined : senderColor(props.name) }}
        >
          {mine ? "You (operator)" : props.displayName || props.name}
        </span>
        {props.role && (
          <span className="text-[10px] text-muted-foreground truncate block">
            {props.role}
          </span>
        )}
      </span>
      <span className="text-[10px] text-muted-foreground shrink-0 text-right">
        {props.detail}
      </span>
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
  const children = props.tree.filter((n) =>
    (n.scope?.parents ?? []).includes(room?.room_id ?? ""),
  );
  if (!room || children.length === 0) {
    return (
      <p className="text-[11px] text-muted-foreground">
        No groups inside this one yet. Add one above — it starts with this
        group&rsquo;s members and inherits future additions automatically.
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
          <CornerUpRight
            className="size-3 shrink-0 text-muted-foreground"
            aria-hidden="true"
          />
          <span className="font-semibold truncate flex-1">{child.name}</span>
          {child.scope?.state && child.scope.state !== "active" && (
            <span className="text-[9px] text-muted-foreground">
              {stateLabel(child.scope.state)}
            </span>
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
  /**
   * Live activity, or null when it has not been read yet. `null` renders as
   * *reading…*, never as an empty room — an unread read and a room with nobody
   * working are opposite claims.
   */
  activity: RoomActivity | null;
  /** Why the activity read failed, or null. Never conflated with empty. */
  activityError: string | null;
  /** Whether an activity refresh is in flight, so the control can disable itself. */
  activityBusy: boolean;
  onRefreshActivity: () => Promise<void>;
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
  /**
   * Open the project this room is bound to, from the group's own profile.
   *
   * A project's room is where the hub's *Room* shortcut lands, so this is the
   * way back; without it the room names its project and can even unlink it, but
   * never open it.
   */
  onOpenProjectDetails?: (projectId: string) => void;
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
  council: {
    topic: string;
    strategy: CouncilStrategy;
    busy: boolean;
    result: string | null;
  };
  setCouncil: React.Dispatch<
    React.SetStateAction<{
      topic: string;
      strategy: CouncilStrategy;
      busy: boolean;
      result: string | null;
    }>
  >;
  onCouncil: () => void;
  onPostVerdict: () => void;
  /** True while the verdict is being written to the room, so it cannot double-post. */
  postingVerdict: boolean;
}) {
  const [objective, setObjective] = useState("");
  const [runs, setRuns] = useState<Array<Record<string, unknown>>>([]);
  const [runsError, setRunsError] = useState<string | null>(null);
  /**
   * The pane's action forms are view-only on arrival: starting a supervised
   * run and opening the council each take one explicit control — the same rule
   * the group profile follows. A details pane is read far more often than it is
   * written, so nothing in it is live-editable until asked.
   */
  const [supervising, setSupervising] = useState(false);
  const [openCouncil, setOpenCouncil] = useState(false);
  // A new selection arrives with its forms closed — never the previous room's.
  const paneSelKey = props.sel.kind === "group" ? props.sel.name : props.sel.peer;
  useEffect(() => {
    setSupervising(false);
    setOpenCouncil(false);
  }, [paneSelKey]);
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

  const decisions = props.messages.filter(
    (m) => m.kind === "decision" && !m.deleted,
  );
  const blockers = props.messages.filter(
    (m) => ["blocker", "warning", "escalation"].includes(m.kind) && !m.deleted,
  );

  /**
   * The presence dot — the shared vocabulary, so the list rows and this
   * panel never disagree about a colour. Rationale on `presenceDotClass`.
   */
  const dot = presenceDotClass;

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
       It is now an overlay below `lg` (so the toggle always does something)
       and a normal column at `lg` and up (so the layout is unchanged where it
       worked).

       Below `lg` it covers the whole conversation area with an OPAQUE surface:
       the earlier translucent `w-72` strip floated at `bg-card/40` on top of
       the transcript, so the chat behind it bled through the panel's gaps and
       the two rendered as one garbled overlapping page. Width below `lg` must
       be `auto`: an over-constrained absolute drops `right`, so a fixed
       `w-72`/`w-full` would ignore the left edge — `md:max-lg:left-80` plus
       `w-auto` is what keeps the conversation list beside the pane at widths
       where one fits while the pane still ends at the viewport's right edge
       (a `w-full` pane would overflow it and push the close button
       off-screen). */
    <aside
      className="w-72 shrink-0 border-l border-border bg-card/40 flex flex-col min-h-0 max-lg:absolute max-lg:inset-0 max-lg:w-auto max-lg:border-l-0 max-lg:bg-card max-lg:z-30 max-lg:elev-3 md:max-lg:left-80"
      aria-label="Conversation details"
    >
      <div className="px-3 py-2.5 border-b border-border/60 flex items-center gap-2 bg-card/60">
        <Info className="size-3.5 text-muted-foreground" aria-hidden="true" />
        <p className="text-xs font-bold flex-1">Details</p>
        <button
          type="button"
          onClick={props.onClose}
          className="p-1.5 rounded-lg hover:bg-muted text-muted-foreground"
          aria-label="Close details"
        >
          <X className="size-4" aria-hidden="true" />
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-3 space-y-4">
        {/* The group's own charter — identity, links, goals, project binding and
            cloning. It reads on its own four routes so a failure here cannot
            blank the roster below, and vice versa: two independent reads of two
            independent subsystems, each with its own honest failure. */}
        {isGroup && (
          <GroupProfilePanel
            roomName={props.sel.kind === "group" ? props.sel.name : ""}
            onError={props.onError}
            onChanged={() => props.onRefresh()}
            onCloned={(name) => props.onOpenRoom(name)}
            onOpenProjectDetails={props.onOpenProjectDetails}
          />
        )}

        {/* This roster is the reason the view exists: every bot in the group,
            with the state the server actually measured. Direct / inherited /
            rule-matched are kept visually distinct because each has a
            different owner and lifetime — flattening them is how a nested
            roster stops making sense. */}
        <section className="rounded-xl border border-border/60 bg-card/50 p-3 space-y-2">
          <p className="text-[11px] font-bold flex items-center gap-1.5">
            <Users className="size-3" aria-hidden="true" />
            Members in this group
            <span className="font-normal text-muted-foreground">
              · {props.headline}
            </span>
          </p>
          <GroupActivityPanel
            snapshot={props.activity}
            error={props.activityError}
            busy={props.activityBusy}
            onRefresh={() => void props.onRefreshActivity()}
          />
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
              <p className="text-[10px] font-bold text-muted-foreground">
                INHERITED ({props.buckets.inherited.length})
              </p>
              {props.buckets.inherited.map((b) => (
                <MemberRow
                  key={`inh-${b.name}`}
                  name={b.name}
                  state={
                    props.presence.find((p) => p.name === b.name)?.state ?? null
                  }
                  dotClass={dot(
                    props.presence.find((p) => p.name === b.name)?.state ??
                      null,
                  )}
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
              <p className="text-[10px] font-bold text-muted-foreground">
                RULE MATCHED ({props.buckets.ruleMatched.length})
              </p>
              {props.buckets.ruleMatched.map((b) => {
                const rule = props.roster?.rules?.find((r) => r.id === b.rule);
                return (
                  <MemberRow
                    key={`rule-${b.name}`}
                    name={b.name}
                    state={
                      props.presence.find((p) => p.name === b.name)?.state ??
                      null
                    }
                    dotClass={dot(
                      props.presence.find((p) => p.name === b.name)?.state ??
                        null,
                    )}
                    detail={
                      rule
                        ? rule.label || `${rule.field} ${rule.op} ${rule.value}`
                        : "matched a rule"
                    }
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
              <p className="text-[10px] font-bold text-muted-foreground">
                EXCLUDED ({props.buckets.excluded.length})
              </p>
              {props.buckets.excluded.map((name) => (
                <div
                  key={`exc-${name}`}
                  className="flex items-center gap-2 text-[11px] px-1.5 py-1 opacity-70"
                >
                  <span
                    className="size-2 rounded-full shrink-0 bg-muted-foreground/30"
                    aria-hidden="true"
                  />
                  <span
                    className="flex-1 truncate font-semibold"
                    style={{ color: senderColor(name) }}
                  >
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
              <p className="text-[10px] font-bold text-muted-foreground">
                EXPIRED ({props.buckets.expired.length})
              </p>
              {props.buckets.expired.map((name) => (
                <p
                  key={`exp-${name}`}
                  className="text-[10px] text-muted-foreground px-1.5 py-1"
                >
                  {name} — borrowed here and past its expiry, so no longer
                  participating.
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
          <section className="rounded-xl border border-border/60 bg-card/50 p-3 space-y-2">
            <p className="text-[11px] font-bold flex items-center gap-1.5">
              <Check className="size-3.5 text-emerald-500" aria-hidden="true" />
              Decisions made ({decisions.length})
            </p>
            <div className="space-y-1.5">
              {decisions.slice(-5).map((d, i) => (
                <div
                  key={i}
                  className="rounded-lg border-l-2 border-emerald-500 bg-muted/40 px-2.5 py-1.5"
                >
                  <p
                    className="text-[11px] font-semibold"
                    style={{ color: senderColor(d.sender) }}
                  >
                    {d.sender}
                  </p>
                  <p className="text-[11px] line-clamp-3">{d.content}</p>
                </div>
              ))}
            </div>
          </section>
        )}

        {blockers.length > 0 && (
          <section className="rounded-xl border border-border/60 bg-card/50 p-3 space-y-2">
            <p className="text-[11px] font-bold flex items-center gap-1.5">
              <AlertTriangle
                className="size-3.5 text-amber-500"
                aria-hidden="true"
              />
              Needs attention ({blockers.length})
            </p>
            <div className="space-y-1.5">
              {blockers.slice(-4).map((b, i) => (
                <div
                  key={i}
                  className="rounded-lg border-l-2 border-amber-500 bg-muted/40 px-2.5 py-1.5"
                >
                  <p className="text-[10px] font-bold text-amber-600">
                    {b.kind.toUpperCase()} • {b.sender}
                  </p>
                  <p className="text-[11px] line-clamp-3">{b.content}</p>
                </div>
              ))}
            </div>
          </section>
        )}

        {isGroup && (
          <>
            {/* Groups nested inside this one — the WhatsApp-community shape. */}
            <section className="rounded-xl border border-border/60 bg-card/50 p-2.5 space-y-2">
              <p className="text-[11px] font-bold flex items-center gap-1.5">
                <Layers className="size-3.5 text-primary" aria-hidden="true" />{" "}
                Groups inside this one
              </p>
              {props.canNest && (
                <Btn
                  variant="ghost"
                  onClick={() =>
                    props.onAddSubgroup(
                      props.sel.kind === "group" ? props.sel.name : "",
                    )
                  }
                >
                  <FolderPlus className="size-3.5" aria-hidden="true" /> Add a
                  group inside
                </Btn>
              )}
              <SubgroupList
                roomName={props.sel.kind === "group" ? props.sel.name : ""}
                tree={props.tree}
                onOpen={props.onOpenRoom}
              />
            </section>

            <section className="rounded-xl border border-border/60 bg-card/50 p-2.5 space-y-2">
              <p className="text-[11px] font-bold">Supervise the team</p>
              {!supervising ? (
                <Btn
                  variant="ghost"
                  onClick={() => setSupervising(true)}
                  title="Stage an autonomous run goal for this room"
                >
                  <Play className="size-3.5" aria-hidden="true" /> Start a run…
                </Btn>
              ) : (
                <div className="flex gap-2">
                  <input
                    value={objective}
                    onChange={(e) => setObjective(e.target.value)}
                    placeholder="Goal for an autonomous run…"
                    aria-label="Autonomous run goal"
                    className={inputCls}
                  />
                  <Btn
                    variant="ghost"
                    onClick={() => startRun()}
                    disabled={!objective.trim() || !!startingRun}
                    title={
                      startingRun
                        ? "A run is still starting"
                        : "Start one autonomous run for this room"
                    }
                    aria-label="Start an autonomous run for this room"
                  >
                    <Play className="size-3.5" aria-hidden="true" />
                  </Btn>
                  <Btn variant="ghost" onClick={() => setSupervising(false)} disabled={!!startingRun}>
                    Cancel
                  </Btn>
                </div>
              )}
              {runsError ? (
                <ErrorBox
                  message={`Room runs unavailable — failed to load, not empty. (${runsError})`}
                  onRetry={loadRuns}
                />
              ) : (
                runs.length > 0 && (
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
                        <div
                          key={rid}
                          className="flex items-center gap-2 text-[11px] rounded-lg bg-muted/40 px-2 py-1.5"
                        >
                          <Badge tone={live ? "blue" : "gray"}>
                            {st || "status not reported"}
                          </Badge>
                          <span
                            className="font-mono flex-1 truncate"
                            title={rid}
                          >
                            {rid.slice(0, 16)}
                          </span>
                          {live && (
                            <button
                              type="button"
                              onClick={() => cancelRun(rid)}
                              disabled={!!cancellingRun}
                              className="p-1 rounded hover:bg-muted disabled:opacity-30"
                              title={
                                cancellingRun
                                  ? "A cancel is still in flight"
                                  : `Stop run ${rid}`
                              }
                              aria-label={`Stop run ${rid}`}
                            >
                              <Ban className="size-3.5" aria-hidden="true" />
                            </button>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )
              )}
              <Btn variant="danger" onClick={props.onDeleteRoom}>
                <Trash2 className="size-3.5" aria-hidden="true" /> Delete group
              </Btn>
            </section>

            <section className="rounded-xl border border-border/60 bg-card/50 p-2.5 space-y-2">
              <p className="text-[11px] font-bold inline-flex items-center gap-1.5">
                <Scale className="size-3.5 text-primary" /> Settle a
                disagreement
              </p>
              {!openCouncil ? (
                <Btn
                  variant="ghost"
                  onClick={() => setOpenCouncil(true)}
                  title="Stage a disputed question for the team council"
                >
                  <Scale className="size-3.5" aria-hidden="true" /> Ask the council…
                </Btn>
              ) : (
                <>
                  <Field label="What's disputed?">
                    <input
                      value={props.council.topic}
                      onChange={(e) =>
                        props.setCouncil((c) => ({ ...c, topic: e.target.value }))
                      }
                      placeholder="REST vs GraphQL for the API…"
                      className={inputCls}
                    />
                  </Field>
                  <div className="flex gap-2">
                    <select
                      value={props.council.strategy}
                      onChange={(e) =>
                        props.setCouncil((c) => ({
                          ...c,
                          strategy: e.target.value as CouncilStrategy,
                        }))
                      }
                      className={`${inputCls} !w-auto`}
                      aria-label="Council strategy"
                    >
                      <option value="debate">Debate</option>
                      <option value="council">Council vote</option>
                      <option value="ensemble">Ensemble</option>
                      <option value="single">Single judge</option>
                    </select>
                    <Btn
                      onClick={props.onCouncil}
                      disabled={
                        props.council.busy || props.council.topic.trim().length < 2
                      }
                    >
                      {props.council.busy ? "Deliberating…" : "Ask council"}
                    </Btn>
                    <Btn
                      variant="ghost"
                      onClick={() => setOpenCouncil(false)}
                      disabled={props.council.busy}
                    >
                      Cancel
                    </Btn>
                  </div>
                </>
              )}
              {props.council.result && (
                <div className="space-y-1.5">
                  <pre className="text-[11px] whitespace-pre-wrap rounded-lg bg-muted/40 p-2 max-h-48 overflow-y-auto">
                    {props.council.result.slice(0, 2000)}
                  </pre>
                  <Btn
                    onClick={props.onPostVerdict}
                    disabled={props.postingVerdict}
                  >
                    {props.postingVerdict
                      ? "Posting…"
                      : "Post as group decision"}
                  </Btn>
                </div>
              )}
            </section>
          </>
        )}

        {props.eventsError ? (
          <section className="rounded-xl border border-border/60 bg-card/50 p-3 space-y-2">
            <p className="text-[11px] font-bold">Recent team events</p>
            <p className="text-[10px] text-destructive">
              Team events unavailable — failed to load, not empty. (
              {props.eventsError})
            </p>
          </section>
        ) : (
          props.events.length > 0 && (
            <section className="rounded-xl border border-border/60 bg-card/50 p-3 space-y-2">
              <p className="text-[11px] font-bold">Recent team events</p>
              <div className="space-y-1">
                {props.events.slice(0, 6).map((e, i) => (
                  <p
                    key={i}
                    className="text-[10px] text-muted-foreground rounded-lg bg-muted/40 px-2 py-1.5"
                  >
                    {String(e.event_type ?? e.type ?? "event")} —{" "}
                    {String(e.summary ?? e.description ?? "").slice(0, 100)}
                  </p>
                ))}
              </div>
            </section>
          )
        )}
      </div>
    </aside>
  );
}
