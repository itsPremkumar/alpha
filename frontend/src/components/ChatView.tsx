"use client";

import React, { useState, useEffect, useMemo, useRef, useCallback, lazy, Suspense } from "react";
import { ThreadSidebar } from "@/components/ThreadSidebar";
import { MessageItem } from "@/components/MessageItem";
import { ActivityStatus } from "@/components/ActivityStatus";
import { SubagentList } from "@/components/SubagentList";
import type { SubagentTask } from "@/lib/sse-reducer";
import { currentTurn, deriveActivity, silenceNotice } from "@/lib/activity";
import { Composer } from "@/components/Composer";
import { NavTabs, WorkspaceView } from "@/components/NavTabs";
import { ChatMessage, Thread, AIModel } from "@/types/chat";
import { BotProfile } from "@/types/bots";
import { fetchThreadsResult, createThread, fetchThreadHistoryResult, fetchAvailableModels, fetchModelCatalog, autoTriggerCommand } from "@/lib/api";
import { apiFetch, ApiClientError } from "@/lib/api-client";
import {
  DEFAULT_EFFORT,
  FALLBACK_LABELS,
  FALLBACK_LADDER,
  reconcileEffortForModel,
  type EffortChoice,
} from "@/lib/reasoning-effort";
import { consumeChatStream } from "@/lib/chat-stream";
import type { StreamMessage } from "@/lib/sse-reducer";
import { chatRequestErrorMessage, ChatRequestFailure } from "@/lib/chat-request-error";
import { branding } from "@/lib/branding";
import { currentOperatorIdentity, subscribeOperatorName } from "@/lib/operator";
import { BrandLogo } from "@/components/BrandLogo";
import { LionPet, useLionPetActivity } from "@/components/lion-pet";
import { WorkspaceVitals } from "@/components/WorkspaceVitals";
import { fetchBots, touchBot } from "@/lib/bots";
import { fetchFeatures, fetchOpsStatus, FeatureFlags } from "@/lib/workspace";
import { listThreadRuns, cancelRun, prepareRegenerate, prepareEditRegenerate } from "@/lib/runs";
import { rateMessage } from "@/lib/feedback";
import { suggestionsEnabled, suggestFollowUps, polishDraft } from "@/lib/assist";
import { listCommands, executeCommand, SlashCommand } from "@/lib/commands";
import { readAutoplayEnabled, autoplaySpeak, speak } from "@/lib/voice";
import { SpeechSegmenter, cancelSpeech, enqueueSpeech, isSpeechCancellation, waitForSpeechIdle } from "@/lib/speech";
import {
  loadStore,
  upsertLocalThread,
  remapThreadId,
  appendLocalMessages,
  setLocalMessages,
  updateLocalMessage,
  removeLocalThread,
  setThreadMeta,
  searchLocalMessages,
  storageInfo,
  exportStoreJson,
  importStoreJson,
  type ThreadMeta,
} from "@/lib/history-store";
import { uploadFiles, listUploads } from "@/lib/files";
import { fetchGoal, setGoal, clearGoal, compactThread, fetchTokenUsage, TokenUsage, moveThread, deleteThread } from "@/lib/threads-ext";
import { listProjects, Project } from "@/lib/projects";
import { fetchFreeCatalog } from "@/lib/freeModels";
import { BotGallery } from "@/components/bots/BotGallery";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { BotDetailPanel } from "@/components/bots/BotDetailPanel";
import { ActiveBotPicker } from "@/components/bots/ActiveBotPicker";
import {
  ChatShell,
  ChatShellEmptyState,
  ProjectContextHeader,
  ProjectDetailPanel,
  WorkspaceTopBar,
  OmnisearchModal,
} from "@/components/chat-shell/ChatShell";
import { ErrorBox, SkeletonList } from "@/components/ui";
import { errMsg } from "@/lib/http";
import { Shrink, Target, ClipboardList, Settings } from "lucide-react";

/**
 * Health of the keyless free-model catalog, as the header should draw it.
 *
 * `unknown` is a real state, not a default to be optimised away: the server
 * reports `healthy: null` for a provider it has not probed, and a failed catalog
 * read leaves every provider unmeasured. Painting either of those green is an
 * optimistic success for something the server did not confirm.
 */
export type FreeCatalogTone = "good" | "partial" | "bad" | "unknown";

/** Dot colour per tone. Muted for `unknown` so it never reads as healthy. */
export const FREE_TONE_DOT: Record<FreeCatalogTone, string> = {
  good: "bg-emerald-500",
  partial: "bg-amber-500",
  bad: "bg-red-500",
  unknown: "bg-muted-foreground/40",
};

/**
 * Derive the header tone from the server's per-provider `healthy` flags.
 *
 * Exported and pure so the honesty test can drive it with the exact payload the
 * Gateway returns — including the "1/10 healthy" and "all null" cases that the
 * old hardcoded green dot could not represent.
 */
export function freeCatalogTone(
  providers: Array<{ healthy: boolean | null }>,
): FreeCatalogTone {
  const measured = providers.filter((p) => p.healthy !== null);
  // Nobody was measured: unknown, which must not wear a success colour.
  if (measured.length === 0) return "unknown";
  const healthy = measured.filter((p) => p.healthy === true).length;
  if (healthy === 0) return "bad";
  return healthy === measured.length ? "good" : "partial";
}

// Sections load on demand so the first paint stays light.
const BotOpsSection = lazy(() => import("@/components/sections/BotOpsSection").then((m) => ({ default: m.BotOpsSection })));
const MessagesSection = lazy(() => import("@/components/sections/MessagesSection").then((m) => ({ default: m.MessagesSection })));
const PeerNetworkSection = lazy(() => import("@/components/sections/PeerNetworkSection").then((m) => ({ default: m.PeerNetworkSection })));
const KanbanSection = lazy(() => import("@/components/sections/KanbanSection").then((m) => ({ default: m.KanbanSection })));
const RunsSection = lazy(() => import("@/components/sections/RunsSection").then((m) => ({ default: m.RunsSection })));
const RunInspectorSection = lazy(() => import("@/components/sections/RunInspectorSection").then((m) => ({ default: m.RunInspectorSection })));
const FilesSection = lazy(() => import("@/components/sections/FilesSection").then((m) => ({ default: m.FilesSection })));
const ScheduledSection = lazy(() => import("@/components/sections/ScheduledSection").then((m) => ({ default: m.ScheduledSection })));
const SubagentsSection = lazy(() => import("@/components/sections/SubagentsSection").then((m) => ({ default: m.SubagentsSection })));
const SkillsSection = lazy(() => import("@/components/sections/SkillsSection").then((m) => ({ default: m.SkillsSection })));
const MemorySection = lazy(() => import("@/components/sections/MemorySection").then((m) => ({ default: m.MemorySection })));
const ProjectsSection = lazy(() => import("@/components/sections/ProjectsSection").then((m) => ({ default: m.ProjectsSection })));
const DashboardSection = lazy(() => import("@/components/sections/DashboardSection").then((m) => ({ default: m.DashboardSection })));
const AgentsSection = lazy(() => import("@/components/sections/AgentsSection").then((m) => ({ default: m.AgentsSection })));
const TeamOpsSection = lazy(() => import("@/components/sections/TeamOpsSection").then((m) => ({ default: m.TeamOpsSection })));
const ChannelsSection = lazy(() => import("@/components/sections/ChannelsSection").then((m) => ({ default: m.ChannelsSection })));
const SystemSection = lazy(() => import("@/components/sections/SystemSection").then((m) => ({ default: m.SystemSection })));
const IntegrationSection = lazy(() => import("@/components/sections/IntegrationSection").then((m) => ({ default: m.IntegrationSection })));
const WorkforceSection = lazy(() => import("@/components/sections/WorkforceSection").then((m) => ({ default: m.WorkforceSection })));
const WarRoomSection = lazy(() => import("@/components/sections/WarRoomSection").then((m) => ({ default: m.WarRoomSection })));
const WarRoomRunsSection = lazy(() => import("@/components/sections/WarRoomRunsSection").then((m) => ({ default: m.WarRoomRunsSection })));
const SettingsSection = lazy(() => import("@/components/sections/SettingsSection").then((m) => ({ default: m.SettingsSection })));
const WorkflowsSection = lazy(() => import("@/components/sections/WorkflowsSection").then((m) => ({ default: m.WorkflowsSection })));
const ForgeSection = lazy(() => import("@/components/sections/ForgeSection").then((m) => ({ default: m.ForgeSection })));
const SupervisorSection = lazy(() => import("@/components/sections/SupervisorSection").then((m) => ({ default: m.SupervisorSection })));
const ProtocolsSection = lazy(() => import("@/components/sections/ProtocolsSection").then((m) => ({ default: m.ProtocolsSection })));

function SectionFallback() {
  return (
    <div className="flex-1 overflow-y-auto px-4 sm:px-6 py-5 w-full">
      <div className="max-w-6xl mx-auto">
        <SkeletonList rows={4} />
      </div>
    </div>
  );
}

function looksLikeUnsentDraft(thread: Thread): boolean {
  const title = thread.title.trim().toLowerCase();
  return title === "new conversation" || title.startsWith("chat with ");
}

/** Bounded background fan-out: never open one request per thread at once. */
const ARCHIVE_BATCH_SIZE = 3;

function threadLabel(thread: Thread): string {
  return thread.title.trim() || thread.thread_id;
}

/**
 * Hide only server-confirmed empty drafts from the visible list.
 *
 * A thread is hidden ONLY when all three checks succeed: the server returned a
 * complete (non-partial) empty message list AND the server confirmed an empty
 * upload list. A failed read, a partial page, or an unreadable upload list are
 * all treated as "not proven empty", so local history can never be hidden
 * behind a load failure.
 *
 * `knownLocalMessages` lets a caller that already loaded the archive reuse it
 * instead of reading every stored message a second time; when it cannot be
 * supplied the archive is read here.
 */
async function hideConfirmedEmptyDrafts(
  threads: Thread[],
  knownLocalMessages?: Record<string, ChatMessage[]>,
): Promise<{ visible: Thread[]; notes: string[] }> {
  const notes: string[] = [];
  let localMessages: Record<string, ChatMessage[]>;
  if (knownLocalMessages) {
    localMessages = knownLocalMessages;
  } else {
    try {
      localMessages = (await loadStore()).messages;
    } catch {
      // Inability to inspect the archive is not proof that a thread is empty.
      // Keep every server row visible rather than risking hidden local history.
      return { visible: threads, notes: [] };
    }
  }
  const candidates = threads.filter(
    (thread) => looksLikeUnsentDraft(thread) && (localMessages[thread.thread_id]?.length ?? 0) === 0,
  );
  const empty = new Set<string>();
  for (let offset = 0; offset < candidates.length; offset += ARCHIVE_BATCH_SIZE) {
    const checks = await Promise.all(
      candidates.slice(offset, offset + ARCHIVE_BATCH_SIZE).map(async (thread) => ({
        thread,
        history: await fetchThreadHistoryResult(thread.thread_id),
      })),
    );
    for (const { thread, history } of checks) {
      if (!history.ok) continue;
      if (history.incomplete) {
        // A partial page cannot prove the thread is empty.
        notes.push(`${threadLabel(thread)}: partial history — ${history.incomplete}`);
        continue;
      }
      if (history.value.length === 0) {
        try {
          const uploads = await listUploads(thread.thread_id);
          if (uploads.length === 0) empty.add(thread.thread_id);
        } catch (error) {
          // Failed upload inspection is not proof that the thread is empty.
          notes.push(`${threadLabel(thread)}: uploads unavailable — ${errMsg(error)}`);
        }
      } else {
        try {
          await setLocalMessages(thread.thread_id, history.value);
        } catch (error) {
          notes.push(`${threadLabel(thread)}: local write failed — ${errMsg(error)}`);
        }
      }
    }
  }
  return { visible: threads.filter((thread) => !empty.has(thread.thread_id)), notes };
}

/**
 * Copy every server conversation into the uncapped local archive.
 *
 * This runs in the background, in small batches, so the whole history is kept
 * on this computer without blocking the first paint or opening an unbounded
 * number of requests. Partial pages are archived too — they are real history —
 * and the truncation is reported instead of being presented as a complete
 * copy. Per-thread failures are collected and returned as one notice, which is
 * shown only when something needs attention: a clean archive is already
 * visible in the sidebar's storage counter.
 */
async function archiveServerHistory(threads: Thread[]): Promise<string[]> {
  const notes: string[] = [];
  let archived = 0;
  for (let offset = 0; offset < threads.length; offset += ARCHIVE_BATCH_SIZE) {
    const batch = threads.slice(offset, offset + ARCHIVE_BATCH_SIZE);
    const results = await Promise.all(
      batch.map(async (thread) => ({ thread, history: await fetchThreadHistoryResult(thread.thread_id) })),
    );
    for (const { thread, history } of results) {
      if (!history.ok) {
        // Keep the thread visible with whatever local copy exists.
        notes.push(`${threadLabel(thread)}: ${history.error}`);
        continue;
      }
      if (history.incomplete) {
        notes.push(`${threadLabel(thread)}: archived partially — ${history.incomplete}`);
      }
      if (history.value.length === 0) continue;
      try {
        await setLocalMessages(thread.thread_id, history.value);
        archived += 1;
      } catch (error) {
        notes.push(`${threadLabel(thread)}: local write failed — ${errMsg(error)}`);
      }
    }
  }
  if (notes.length === 0) return [];
  const shown = notes.slice(0, 2);
  const extra = notes.length - shown.length;
  return [
    `Saved ${archived} conversation${archived === 1 ? "" : "s"} on this computer. ${notes.length} could not be fully saved${
      extra > 0 ? ` (${extra} more)` : ""
    }: ${shown.join("; ")}`,
  ];
}

/**
 * Kick off the background archive unless one is already walking the same
 * history, and surface its notice only when something needed attention.
 */
function startHistoryArchive(
  threads: Thread[],
  archiveInFlightRef: { current: boolean },
  flash: (message: string) => void,
): void {
  if (archiveInFlightRef.current) return;
  archiveInFlightRef.current = true;
  void archiveServerHistory(threads)
    .then((notices) => {
      for (const notice of notices) flash(notice);
    })
    .catch((error) => console.error("Local history archive failed:", error))
    .finally(() => {
      archiveInFlightRef.current = false;
    });
}

export default function ChatView() {
  const [threads, setThreads] = useState<Thread[]>([]);
  const [threadsLoading, setThreadsLoading] = useState(true);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [models, setModels] = useState<AIModel[]>([]);
  const [selectedModel, setSelectedModel] = useState<string>("default");
  /**
   * Selected reasoning effort. `default` means "send nothing", so the model
   * entry's own default (or the provider's) applies. Reconciled against the
   * selected model on every change, so the trigger never shows a rung the
   * server would clamp.
   */
  const [reasoningEffort, setReasoningEffort] = useState<EffortChoice>(DEFAULT_EFFORT);
  /**
   * The canonical effort ladder and its labels, as declared by the server. The
   * fallbacks are the same seven rungs; a degraded read must still render a
   * working picker, so this is never left empty.
   */
  const [effortLadder, setEffortLadder] = useState<readonly string[]>(FALLBACK_LADDER);
  const [effortLabels, setEffortLabels] = useState<Readonly<Record<string, string>>>(FALLBACK_LABELS);
  const [input, setInput] = useState<string>("");
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [requestError, setRequestError] = useState<{ threadId: string; message: string; draft: string; partial: string; partialArchived: boolean } | null>(null);
  const [offlineDismissed, setOfflineDismissed] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const transcriptRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Live run status. The phase is derived from what the stream has actually
  // reported for THIS turn only, so a previous turn's tool calls are never
  // counted as current work. Absent (`null`) whenever no run is in flight.
  const activity = useMemo(
    () => (isLoading ? deriveActivity(messages.slice(currentTurn(messages))) : null),
    [messages, isLoading],
  );

  // Client-observed elapsed time for the running turn. The wire carries no
  // start timestamp, so this measures from when this client saw the run begin
  // and is cleared the moment the run ends — it never persists a stale clock.
  const [elapsedMs, setElapsedMs] = useState(0);
  const runStartedAtRef = useRef<number | null>(null);

  // Client-observed silence: when this browser last received ANY byte of the
  // current run's stream, heartbeat comments included. `null` means no reading
  // exists yet, which renders no notice at all rather than "0s quiet".
  const lastByteAtRef = useRef<number | null>(null);
  const [silence, setSilence] = useState<string | null>(null);

  // Subagent tasks folded from `task_*` custom events for the current turn.
  // Cleared when a NEW run starts rather than when one ends, so the completed
  // tasks stay on screen as the receipt for the answer that follows them.
  const [subagentTasks, setSubagentTasks] = useState<SubagentTask[]>([]);

  useEffect(() => {
    if (!isLoading) {
      runStartedAtRef.current = null;
      lastByteAtRef.current = null;
      setElapsedMs(0);
      setSilence(null);
      return;
    }
    if (runStartedAtRef.current === null) runStartedAtRef.current = Date.now();
    if (lastByteAtRef.current === null) lastByteAtRef.current = Date.now();
    const tick = () => {
      const now = Date.now();
      if (runStartedAtRef.current !== null) setElapsedMs(now - runStartedAtRef.current);
      // Same value every second until silence crosses the threshold, so this
      // costs no re-render in the normal case.
      setSilence(silenceNotice(lastByteAtRef.current, now));
    };
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [isLoading]);
  const activeRunRef = useRef(false);
  const [voiceConversationEnabled, setVoiceConversationEnabled] = useState(false);
  const voiceConversationEnabledRef = useRef(false);
  const [voiceTurnActive, setVoiceTurnActive] = useState(false);
  const [voiceResumeToken, setVoiceResumeToken] = useState(0);
  const voiceTurnRef = useRef(false);
  const voiceTurnGenerationRef = useRef(0);
  const voiceResumePendingRef = useRef(false);
  const voiceScopeRef = useRef<string | null>(null);

  // Multi-bot-profile state
  const [bots, setBots] = useState<BotProfile[]>([]);
  const [botsLoading, setBotsLoading] = useState<boolean>(true);
  const [activeBot, setActiveBot] = useState<BotProfile | null>(null);
  const [view, setView] = useState<WorkspaceView>("chat");
  /**
   * Who is operating this workspace.
   *
   * Resolved from storage rather than hardcoded, because the previous build
   * greeted every visitor as a developer's initials in five places. It is state
   * rather than a bare read so that naming yourself in Settings updates the top
   * bar and the landing greeting without a reload. `lib/operator.ts` is the only
   * place a name is resolved.
   */
  const [operator, setOperator] = useState(() => currentOperatorIdentity());
  /**
   * The project the Workforce view should show.
   *
   * Set when the operator opens a project from the Projects list, because the
   * deep per-project state (war-room, RSI, perpetual, canary, blueprints) lives
   * there behind its own dropdown — and that dropdown defaults to the first
   * project in the list, not the one being looked at. Without this, "open this
   * project's controls" lands on someone else's project.
   */
  const [focusedProjectId, setFocusedProjectId] = useState<string | null>(null);
  const voiceViewRef = useRef(view);
  const [settingsInitialTab, setSettingsInitialTab] = useState<"general" | "models" | "connectivity" | "appearance" | "diagnostics">("general");
  const [botsTab, setBotsTab] = useState<"profiles" | "ops">("profiles");
  const [inspectedBot, setInspectedBot] = useState<BotProfile | null>(null);

  // Platform state
  const [features, setFeatures] = useState<FeatureFlags>({ agentsApi: false, browserControl: false, mcpTasks: false, subagentBatches: false });

  // Conversation helpers
  const [goal, setGoalText] = useState<string | null>(null);
  const [goalEditing, setGoalEditing] = useState(false);
  const [goalDraft, setGoalDraft] = useState("");
  const [usage, setUsage] = useState<TokenUsage | null>(null);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [suggestionsOn, setSuggestionsOn] = useState(false);
  /**
   * The Gateway's follow-up-suggestions setting, or `null` when unknown.
   *
   * `suggestionsEnabled()` used to swallow a failed read as `false`, so a
   * gateway that simply did not answer rendered as "suggestions are off". The
   * tri-state lets the toggle below say *unknown* rather than assert a value
   * nobody reported.
   */
  const [suggestionsServerState, setSuggestionsServerState] = useState<boolean | null>(null);
  const [polishing, setPolishing] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [planMode, setPlanMode] = useState(false);
  const [slashCommands, setSlashCommands] = useState<SlashCommand[]>([]);
  const [gatewayOk, setGatewayOk] = useState<boolean | null>(null);
  // Project scope for the active chat (creation + permission/selection in-chat).
  const [projects, setProjects] = useState<Project[]>([]);
  const [pendingProjectId, setPendingProjectId] = useState<string | null>(null);
  const [omnisearchOpen, setOmnisearchOpen] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [activeContextTab, setActiveContextTab] = useState<"conversation" | "files" | "tasks" | "knowledge" | "agent">("conversation");

  const localThreadMetaRef = useRef<Record<string, ThreadMeta>>({});
  const historyLoadGenerationRef = useRef(0);
  // Bumped by every user-initiated navigation. An in-flight run compares its
  // own generation against this before touching React state, so a run that
  // finishes after the user switched conversations can never paint into the
  // newly opened thread. The local archive write is still performed.
  const runGenerationRef = useRef(0);
  // Set while the background archiver is walking the server history, so a
  // manual Refresh does not start a second concurrent copy of the same work.
  const archiveInFlightRef = useRef(false);
  // Synchronous attachment lock: two rapid drops must not create two drafts.
  const attachmentLockRef = useRef(false);
  // Server conversation-list failure reason. Null means "the list loaded".
  const [serverHistoryError, setServerHistoryError] = useState<string | null>(null);
  // Free-model catalog status (dynamic, auto-refreshed server-side TTL 300s).
  const [freeNote, setFreeNote] = useState<string | null>(null);
  const [freeRefreshing, setFreeRefreshing] = useState(false);
  /**
   * Health of the keyless catalog, derived from the server's own per-provider
   * `healthy` flags. `unknown` is a first-class state: the server has not
   * measured any provider, which is neither healthy nor sick. This exists
   * because the header dot used to be a hardcoded `bg-emerald-500` that stayed
   * green through `0/10 healthy` and through a failed read.
   */
  const [freeTone, setFreeTone] = useState<FreeCatalogTone>("unknown");
  const { state: lionState, message: lionMessage, update: updateLion } = useLionPetActivity({
    isLoading,
    hasApproval: messages.some((message) => Boolean(message.approvalRequest)),
  });

  // One timer, owned: two notices inside 4.5s used to schedule two independent
  // clears, and the *first* timer extinguished the second notice after up to a
  // fraction of its window. The handle is kept so a newer notice cancels the
  // pending clear of the older one.
  const noticeTimerRef = useRef<number | null>(null);
  const flash = (msg: string) => {
    setNotice(msg);
    if (noticeTimerRef.current !== null) window.clearTimeout(noticeTimerRef.current);
    noticeTimerRef.current = window.setTimeout(() => {
      setNotice(null);
      noticeTimerRef.current = null;
    }, 4500);
  };

  // Gateway connectivity is a changing fact, not a mount-time snapshot. It used
  // to be probed exactly once, inside the mount effect: the offline banner and
  // the header pill froze at whatever was true when the tab loaded, so
  // restarting the Gateway left "Backend not connected" up until a full reload,
  // and a Gateway that went down mid-session never produced the banner at all.
  // The probe re-runs on an interval, on the browser's own network
  // transitions, and when the tab returns to the foreground — each a moment
  // where the answer can genuinely have changed.
  const probeGateway = useCallback(() => {
    fetchOpsStatus()
      .then(() => {
        setGatewayOk(true);
        // A dismissal belongs to the outage that caused it. Once the Gateway
        // answers again, the next real outage has to warn again rather than
        // inherit a dismissed state from five minutes ago.
        setOfflineDismissed(false);
      })
      .catch(() => setGatewayOk(false));
  }, []);

  useEffect(() => {
    probeGateway();
    const id = window.setInterval(probeGateway, 30_000);
    const onVisibility = () => {
      if (document.visibilityState === "visible") probeGateway();
    };
    window.addEventListener("online", probeGateway);
    window.addEventListener("offline", probeGateway);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.clearInterval(id);
      window.removeEventListener("online", probeGateway);
      window.removeEventListener("offline", probeGateway);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [probeGateway]);

  const persistLocalHistory = async (
    operation: () => Promise<void>,
    failureMessage: string,
  ): Promise<boolean> => {
    try {
      await operation();
      return true;
    } catch (error) {
      flash(`${failureMessage} ${errMsg(error)}`);
      return false;
    }
  };

  useEffect(() => {
    voiceConversationEnabledRef.current = voiceConversationEnabled;
  }, [voiceConversationEnabled]);

  // Voice is scoped to this ChatView/thread. A local thread id can be remapped
  // to its server id during the first voice turn, so that transition is kept;
  // explicit navigation paths also stop the mode below.
  useEffect(() => {
    voiceViewRef.current = view;
    const nextScope = `${view}:${activeThreadId || "new"}`;
    const previousScope = voiceScopeRef.current;
    if (previousScope !== null && previousScope !== nextScope) {
      const previousView = previousScope.split(":", 1)[0];
      const nextView = nextScope.split(":", 1)[0];
      const localThreadRemap = voiceTurnRef.current && previousView === "chat" && nextView === "chat";
      if (!localThreadRemap) {
        if (voiceTurnRef.current) abortRef.current?.abort();
        voiceTurnGenerationRef.current += 1;
        voiceTurnRef.current = false;
        voiceResumePendingRef.current = false;
        voiceConversationEnabledRef.current = false;
        setVoiceConversationEnabled(false);
        cancelSpeech();
      }
    }
    voiceScopeRef.current = nextScope;
  }, [view, activeThreadId]);

  useEffect(() => {
    if (isLoading || !voiceResumePendingRef.current) return;
    voiceResumePendingRef.current = false;
    setVoiceResumeToken((token) => token + 1);
  }, [isLoading]);

  useEffect(() => () => {
    if (voiceTurnRef.current) abortRef.current?.abort();
    voiceTurnGenerationRef.current += 1;
    voiceTurnRef.current = false;
    voiceResumePendingRef.current = false;
    cancelSpeech();
  }, []);

  const stopVoiceForNavigation = () => {
    // Every user-initiated navigation goes through here, so this is the one
    // place that invalidates an in-flight run's React state writes.
    runGenerationRef.current += 1;
    // Subagent progress is per-turn UI state; a receipt from the thread the
    // user just left must not appear under the one they opened.
    setSubagentTasks([]);
    if (voiceTurnRef.current) abortRef.current?.abort();
    voiceTurnGenerationRef.current += 1;
    voiceTurnRef.current = false;
    voiceResumePendingRef.current = false;
    voiceConversationEnabledRef.current = false;
    setVoiceTurnActive(false);
    setVoiceConversationEnabled(false);
    cancelSpeech();
  };

  /** Force a live re-discovery of free providers (probe = real health check). */
  const refreshFreeCatalog = async () => {
    setFreeRefreshing(true);
    try {
      const [{ providers, updatedAt }, mList] = await Promise.all([
        fetchFreeCatalog({ refresh: true, probe: true }),
        fetchAvailableModels(),
      ]);
      setModels(mList);
      const healthy = providers.filter((p) => p.healthy === true).length;
      const eligible = providers.filter((p) => p.eligible).length;
      setFreeNote(
        providers.length === 0
          ? "Free catalog empty — the keyless router has no providers right now."
          : `Free models: ${healthy}/${providers.length} healthy, ${eligible} eligible${updatedAt ? ` (updated ${updatedAt})` : ""}.`
      );
      // Same rule as the mount read: the dot follows the server's count.
      setFreeTone(freeCatalogTone(providers));
      flash(providers.length === 0 ? "Free catalog refreshed: no providers." : `Free catalog refreshed: ${healthy}/${providers.length} healthy.`);
    } catch {
      setFreeNote("Free catalog refresh failed — keyless router may be down.");
      setFreeTone("unknown");
      flash("Free catalog refresh failed.");
    } finally {
      setFreeRefreshing(false);
    }
  };

  /** Project owning the active thread (server field first, pending pick fallback). */
  const activeProjectId: string | null =
    threads.find((t) => t.thread_id === activeThreadId)?.projectId || pendingProjectId;

  const activeProject = useMemo(
    () => projects.find((p) => p.id === activeProjectId) ?? null,
    [projects, activeProjectId],
  );
  const activeProjectThreadCount = useMemo(
    () => (activeProjectId ? threads.filter((t) => (t as unknown as Record<string, unknown>).projectId === activeProjectId || (t as unknown as Record<string, unknown>).project_id === activeProjectId).length : null),
    [threads, activeProjectId],
  );

  /** Scope an unsent draft now, or move the current persisted thread. */
  const handlePickProject = async (projectId: string | null) => {
    if (!activeThreadId || activeThreadId.startsWith("local-")) {
      setPendingProjectId(projectId);
      flash(projectId ? "This new conversation will open in the selected project." : "Project scope cleared.");
      return;
    }
    try {
      await moveThread(activeThreadId, projectId);
      setThreads((previous) =>
        previous.map((thread) => (thread.thread_id === activeThreadId ? { ...thread, projectId } : thread)),
      );
      flash(projectId ? "Chat moved into project." : "Chat removed from project.");
    } catch (error) {
      flash(`Couldn't move this chat. ${errMsg(error)}`);
    }
  };

  // Keep the resolved identity in step with Settings, and with other browser
  // tabs. `storage` never fires in the tab that wrote the value, so the module
  // also dispatches its own event; subscribing to both covers every case.
  useEffect(() => subscribeOperatorName(() => setOperator(currentOperatorIdentity())), []);

  // Initial load: complete local archive first (instant), then merge every
  // server page. Read failures keep the local archive visible and are surfaced
  // instead of being converted into a convincing empty history.
  useEffect(() => {
    async function init() {
      let localMessages: Record<string, ChatMessage[]> | undefined;
      try {
        const local = await loadStore();
        localThreadMetaRef.current = local.meta;
        localMessages = local.messages;
        if (local.warning) flash(local.warning);
        if (local.threads.length > 0) {
          const sorted = [...local.threads].sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""));
          setThreads(sorted);
          setActiveThreadId(sorted[0].thread_id);
        }
      } catch (error) {
        // Undefined here makes the empty-draft check read the archive itself,
        // and it degrades to "hide nothing" if that read fails too.
        localMessages = undefined;
        flash(`Local history could not be opened. ${errMsg(error)}`);
      }

      const [threadResult, effortCatalog, bList, feats, suggOn] = await Promise.all([
        fetchThreadsResult(),
        fetchModelCatalog(),
        // The roster is the one surface that renders last-message previews and
        // unread badges, so it is the caller that opts into the projection.
        fetchBots({ activity: true }),
        fetchFeatures(),
        suggestionsEnabled(),
      ]);
      const mList = effortCatalog.models;
      const serverThreads = threadResult.ok
        ? (await hideConfirmedEmptyDrafts(threadResult.value, localMessages)).visible
        : [];
      if (!threadResult.ok) {
        setServerHistoryError(threadResult.error);
        flash(`Server history is unavailable. ${threadResult.error}`);
      } else {
        setServerHistoryError(null);
        if (threadResult.incomplete) {
          flash(`Chat list is incomplete — ${threadResult.incomplete}. Showing what loaded.`);
        }
      }
      const merged = await mergeThreads(serverThreads);
      setThreads(merged);
      // Keep the whole history on this computer without blocking first paint.
      if (threadResult.ok) startHistoryArchive(serverThreads, archiveInFlightRef, flash);
      setModels(mList);
      if (effortCatalog.reasoningEffortLevels.length > 0) setEffortLadder(effortCatalog.reasoningEffortLevels);
      if (Object.keys(effortCatalog.reasoningEffortLabels).length > 0) setEffortLabels(effortCatalog.reasoningEffortLabels);
      let initialModel = "default";
      try {
        const savedModel = localStorage.getItem("alpha_selected_model");
        if (savedModel && (mList.length === 0 || mList.some((m) => m.id === savedModel))) {
          initialModel = savedModel;
        } else if (mList.length > 0) {
          initialModel = mList[0].id;
        }
      } catch {
        if (mList.length > 0) initialModel = mList[0].id;
      }
      setSelectedModel(initialModel);
      // A stored effort is only restored when the model that will serve it
      // actually declares that rung. Restoring it blindly would show a level in
      // the picker that the factory clamps server-side, which is exactly the
      // mismatch this control must not have.
      let initialEffort: EffortChoice = DEFAULT_EFFORT;
      try {
        const savedEffort = localStorage.getItem("alpha_reasoning_effort");
        if (savedEffort) {
          const target = mList.find((m) => m.id === initialModel) ?? null;
          initialEffort = reconcileEffortForModel(savedEffort, target);
          if (initialEffort === DEFAULT_EFFORT) localStorage.removeItem("alpha_reasoning_effort");
        }
      } catch {}
      setReasoningEffort(initialEffort);
      setActiveThreadId((prev) => prev || (merged.length > 0 ? merged[0].thread_id : null));
      setBots(bList);
      setBotsLoading(false);
      setFeatures(feats);
      // A `null` here means the Gateway did not report the setting, so the
      // session toggle starts off and the UI reports "unknown" rather than
      // claiming suggestions are disabled.
      setSuggestionsServerState(suggOn);
      setSuggestionsOn(suggOn === true);
      // Projects for the in-chat scope picker; an unavailable list is not an
      // authoritative empty project set.
      listProjects().then(setProjects).catch((error) => {
        setProjects([]);
        flash(`Projects are unavailable. ${errMsg(error)}`);
      });
      // Free-model catalog: dynamic server view, never fabricated client-side.
      //
      // The status dot is derived from the server's own health count, not
      // hardcoded. It used to be an unconditional `bg-emerald-500`, so a
      // catalog the server had just reported as `0/10 healthy` still wore a
      // solid green dot — an optimistic success for something nobody confirmed.
      // `null` health (the server did not measure a provider) is its own state
      // and gets the muted dot, because "unmeasured" is neither healthy nor sick.
      const renderFree = (providers: { name: string; healthy: boolean | null; eligible: boolean }[], updatedAt?: string | null) => {
        const healthy = providers.filter((p) => p.healthy === true).length;
        const eligible = providers.filter((p) => p.eligible).length;
        setFreeNote(
          providers.length === 0
            ? "Free catalog empty — the keyless router has no providers right now."
            : `Free models: ${healthy}/${providers.length} healthy, ${eligible} eligible${updatedAt ? ` (updated ${updatedAt})` : ""}.`
        );
        // No measured provider means the server has not answered for any of
        // them: that is unknown, so the dot stays neutral rather than green.
        setFreeTone(freeCatalogTone(providers));
      };
      fetchFreeCatalog()
        .then(({ providers, updatedAt }) => renderFree(providers, updatedAt))
        .catch(() => {
          setFreeNote("Free catalog unreachable — keyless router may be down.");
          // A failed read is not a healthy catalog. The old code left the
          // previous green dot in place through this catch.
          setFreeTone("unknown");
        });
      // Lightweight liveness probe for the header status pill — moved out of
      // this mount effect; see `probeGateway` below.
      // Shortcut commands for the "/" palette (quiet if unavailable).
      listCommands().then(setSlashCommands).catch(() => setSlashCommands([]));
      setThreadsLoading(false);
    }
    void init();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const refreshBots = async () => {
    setBotsLoading(true);
    // Keep the activity projection on refresh: a manual refresh that silently
    // dropped unread badges would make a read look like a write.
    //
    // This had no `try`/`catch`, so a failed roster read rejected out of an
    // async handler, skipped `setBotsLoading(false)`, and left the sidebar
    // stuck on its skeleton for the life of the page - plus an unhandled
    // rejection in the console. The failure is now surfaced with the server's
    // reason and the loading state always clears in `finally`.
    try {
      const bList = await fetchBots({ activity: true });
      setBots(bList);
      if (activeBot) {
        const fresh = bList.find((b) => b.name === activeBot.name);
        if (fresh) setActiveBot(fresh);
      }
    } catch (error) {
      // The existing roster stays on screen: an empty grid would be
      // indistinguishable from "the workspace has no agents", which is a claim
      // the failed read cannot make.
      flash(`Agent roster unavailable. ${errMsg(error)}`);
    } finally {
      setBotsLoading(false);
    }
  };

  /**
   * Union of server + locally stored threads.
   *
   * The merge is field-wise with the same two exceptions as
   * `history-store.mergeThreadFields`: an absent/empty field means the server
   * did not mention it (an older Gateway omits `bot_name` / `project_id`), so
   * the locally known value must survive. An explicit `null` is the server
   * saying "unassigned" and does clear it — otherwise a chat just moved out of
   * a project would keep that scope forever.
   */
  const mergeThreads = async (serverList: Thread[]): Promise<Thread[]> => {
    let localThreads: Thread[] = [];
    try {
      localThreads = (await loadStore()).threads;
    } catch (error) {
      console.error("Local chat metadata is unavailable:", error);
    }
    const byId = new Map<string, Thread>(localThreads.map((thread) => [thread.thread_id, thread]));
    for (const serverThread of serverList) {
      const local = byId.get(serverThread.thread_id);
      const merged: Thread = { ...(local ?? ({} as Thread)) };
      for (const [key, value] of Object.entries(serverThread)) {
        if (value === undefined || value === "") continue;
        (merged as unknown as Record<string, unknown>)[key] = value;
      }
      merged.thread_id = serverThread.thread_id;
      byId.set(serverThread.thread_id, merged);
      try {
        await upsertLocalThread(merged);
      } catch (error) {
        console.error(`Local metadata write failed for thread ${serverThread.thread_id}:`, error);
      }
    }
    return Array.from(byId.values()).sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""));
  };

  const reloadThreads = async (selectId?: string) => {
    setThreadsLoading(true);
    try {
      const result = await fetchThreadsResult();
      if (!result.ok) {
        // A failed refresh keeps the currently listed conversations visible.
        setServerHistoryError(result.error);
        throw new Error(result.error);
      }
      setServerHistoryError(null);
      const { visible, notes } = await hideConfirmedEmptyDrafts(result.value);
      if (result.incomplete) {
        flash(`Chat list is incomplete — ${result.incomplete}. Showing what loaded.`);
      } else if (notes.length > 0) {
        flash(notes[0]);
      }
      const merged = await mergeThreads(visible);
      setThreads(merged);
      if (selectId) setActiveThreadId(selectId);
      else if (activeThreadId && !merged.some((thread) => thread.thread_id === activeThreadId)) {
        setActiveThreadId(merged.length > 0 ? merged[0].thread_id : null);
      }
      if (!archiveInFlightRef.current) startHistoryArchive(visible, archiveInFlightRef, flash);
    } finally {
      setThreadsLoading(false);
    }
  };

  // Fetch the complete message feed + goal + usage when the thread changes.
  useEffect(() => {
    if (!activeThreadId) {
      historyLoadGenerationRef.current += 1;
      setMessages([]);
      setGoalText(null);
      setUsage(null);
      setSuggestions([]);
      return;
    }
    async function loadMessages() {
      const generation = historyLoadGenerationRef.current + 1;
      historyLoadGenerationRef.current = generation;
      const threadId = activeThreadId!;
      const history = await fetchThreadHistoryResult(threadId);
      if (historyLoadGenerationRef.current !== generation) return;

      if (history.ok) {
        let cached: ChatMessage[] = [];
        try {
          cached = (await loadStore()).messages[threadId] || [];
        } catch (error) {
          flash(`Server history loaded, but the local archive could not be read. ${errMsg(error)}`);
        }
        if (historyLoadGenerationRef.current !== generation) return;
        // A partial page is real history: show what arrived, but say so, and
        // still archive it so the local copy keeps the messages we do have.
        if (history.incomplete) {
          flash(`This chat's history is only partially loaded — ${history.incomplete}.`);
        }
        setMessages(history.value.length > 0 ? history.value : cached);
        if (history.value.length > 0) {
          await persistLocalHistory(
            () => setLocalMessages(threadId, history.value),
            "The server history loaded, but the complete local archive could not be updated.",
          );
        }
      } else {
        let cached: ChatMessage[] = [];
        try {
          cached = (await loadStore()).messages[threadId] || [];
        } catch (error) {
          if (historyLoadGenerationRef.current === generation) {
            setMessages([]);
            flash(`Neither server nor local history could be loaded. ${history.error}; ${errMsg(error)}`);
          }
          return;
        }
        if (historyLoadGenerationRef.current !== generation) return;
        setMessages(cached);
        flash(`Showing the saved local copy because server history failed. ${history.error}`);
      }

      try {
        const threadGoal = await fetchGoal(threadId);
        if (historyLoadGenerationRef.current !== generation) return;
        if (threadGoal.goal) {
          setGoalText(threadGoal.goal);
          localThreadMetaRef.current[threadId] = {
            ...(localThreadMetaRef.current[threadId] || { botName: null, goal: null }),
            goal: threadGoal.goal,
          };
          void persistLocalHistory(
            () => setThreadMeta(threadId, { goal: threadGoal.goal! }),
            "The goal loaded, but its local copy could not be updated.",
          );
        } else {
          setGoalText(localThreadMetaRef.current[threadId]?.goal || null);
        }
      } catch {
        if (historyLoadGenerationRef.current === generation) {
          setGoalText(localThreadMetaRef.current[threadId]?.goal || null);
        }
      }
      try {
        const tokenUsage = await fetchTokenUsage(threadId);
        if (historyLoadGenerationRef.current === generation) setUsage(tokenUsage);
      } catch {
        if (historyLoadGenerationRef.current === generation) setUsage(null);
      }
      if (historyLoadGenerationRef.current === generation) setSuggestions([]);
    }
    void loadMessages();
  }, [activeThreadId]);

  // Restore this conversation's specialist bot once bots are known.
  useEffect(() => {
    if (!activeThreadId || bots.length === 0) return;
    const name = localThreadMetaRef.current[activeThreadId]?.botName || null;
    setActiveBot(name ? bots.find((bot) => bot.name === name) || null : null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeThreadId, bots]);

  // Auto-scroll follows the answer only while the reader is already at the
  // bottom. Without that test, every SSE frame called
  // `scrollIntoView({behavior:"smooth"})`, so scrolling up to re-read the start
  // of a streaming answer fought the viewport back down for the whole run.
  // Opening another conversation also animated through the entire history
  // instead of simply showing the conversation selected, so a thread change
  // jumps instead of gliding. `prefers-reduced-motion` is checked here because
  // this scroll is issued from JavaScript, which the global
  // `prefers-reduced-motion` stylesheet rule cannot reach.
  const stickToBottomRef = useRef(true);
  const jumpScrollRef = useRef(true);
  useEffect(() => {
    stickToBottomRef.current = true;
    jumpScrollRef.current = true;
  }, [activeThreadId]);
  useEffect(() => {
    if (!stickToBottomRef.current) return;
    const reduce =
      typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const behavior: ScrollBehavior = reduce || jumpScrollRef.current ? "auto" : "smooth";
    messagesEndRef.current?.scrollIntoView({ behavior });
    jumpScrollRef.current = false;
  }, [messages, isLoading, view]);
  const handleTranscriptScroll = () => {
    const el = transcriptRef.current;
    if (!el) return;
    // 80px of slack: a trackpad's momentum or a scrollbar drag to the very end
    // must not by itself re-arm following.
    stickToBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };

  const handleNewChat = () => {
    stopVoiceForNavigation();
    // A blank composer is an unsent draft, not history. Do not create a local
    // or server thread until the first real message/file/command exists.
    setActiveThreadId(null);
    setMessages([]);
    setGoalText(null);
    setUsage(null);
    setSuggestions([]);
    setRequestError(null);
    setView("chat");
  };

  /** Owner of a thread: server metadata first, local meta fallback. */
  const threadOwner = (thread: Thread): string | null => {
    if (thread.botName) return thread.botName;
    return localThreadMetaRef.current[thread.thread_id]?.botName || null;
  };

  /** Pick a specialist and scope history + projects to its space. */
  const rememberBot = (bot: BotProfile | null) => {
    stopVoiceForNavigation();
    setActiveBot(bot);
    if (!bot) return;
    const mine = threads
      .filter((t) => threadOwner(t) === bot.name)
      .sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""));
    const pick = mine[0] || null;
    setActiveThreadId(pick ? pick.thread_id : null);
    if (!pick) setMessages([]);
    if (pick) {
      localThreadMetaRef.current[pick.thread_id] = {
        ...(localThreadMetaRef.current[pick.thread_id] || { botName: null, goal: null }),
        botName: bot.name,
      };
      void persistLocalHistory(
        () => setThreadMeta(pick.thread_id, { botName: bot.name }),
        "The bot link loaded, but its local copy could not be updated.",
      );
    }
  };

  const handleChatWithBot = (bot: BotProfile) => {
    stopVoiceForNavigation();
    setActiveBot(bot);
    setInspectedBot(null);
    setPendingProjectId(null);
    setActiveThreadId(null);
    setMessages([]);
    setGoalText(null);
    setUsage(null);
    setSuggestions([]);
    setRequestError(null);
    setView("chat");
  };

  /** Core send: streams one answer, attaches its run id, stores everything locally. */
  /**
   * Send a turn, optionally as a **server-prepared replay**.
   *
   * `replay` is what turns "Regenerate" and "Edit & resend" from a second,
   * appended turn into the thing the buttons promise. Both used to ignore
   * their target entirely: regenerate re-sent the last user text as a *new*
   * user message (so the question appeared twice and the old answer stayed
   * above the new one), and edit discarded its `messageId` and appended the
   * replacement after the unedited turn (so the transcript showed both the
   * original and the edit, and the model still read the original — the edit
   * never reached it). The Gateway's `runs/regenerate/prepare` and
   * `runs/edit-regenerate/prepare` endpoints own the replay: they return the
   * graph input at the checkpoint *before* the turn, the checkpoint to fork
   * from, and the metadata that marks this run as a replay so the paged
   * history hides the superseded attempt.
   *
   * `supersedeIds` are the local messages the replay replaces — dropped from
   * the transcript before the turn starts. `appendUserMessage` distinguishes
   * the two callers: a regenerate re-asks the question already on screen, an
   * edit puts the replacement in its place.
   */
  const sendMessage = async (
    text: string,
    options: {
      voiceTurn?: boolean;
      replay?: { prepared: Record<string, unknown>; supersedeIds: string[]; appendUserMessage: boolean };
    } = {}
  ): Promise<boolean> => {
    const content = text.trim();
    // Ref is deliberately synchronous: two voice callbacks can arrive before
    // React has committed isLoading=true, and a second run must still be blocked.
    const runLock = typeof activeRunRef !== "undefined" ? activeRunRef : { current: false };
    const stopQueuedSpeech = typeof cancelSpeech === "function" ? cancelSpeech : () => undefined;
    if (!content || isLoading || runLock.current) return false;
    runLock.current = true;
    // Claim a run generation. Any user navigation bumps it, after which this
    // run still archives its own transcript but no longer writes React state.
    const runGeneration = runGenerationRef.current + 1;
    runGenerationRef.current = runGeneration;
    const runIsCurrent = () => runGenerationRef.current === runGeneration;
    const voiceTurn = options.voiceTurn === true;
    stopQueuedSpeech();
    setInput("");
    setIsLoading(true);
    setRequestError(null);
    // A new run starts from zero subagent tasks; the previous turn's receipt
    // must not be read as live work for this one.
    setSubagentTasks([]);
    // Fresh silence baseline: a dead byte counter from the previous turn would
    // announce a stall before this run has had a chance to speak.
    lastByteAtRef.current = Date.now();
    updateLion("thinking", "Paw-sing the request...");

    let threadId = activeThreadId;
    if (!threadId) {
      // The first real prompt commits the draft to both the complete local
      // archive and the Gateway. Merely opening New Chat never creates a row.
      const localId = `local-${Date.now()}`;
      const draft: Thread = {
        thread_id: localId,
        title: content.slice(0, 30),
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        botName: activeBot?.name ?? null,
        projectId: pendingProjectId,
      };
      await persistLocalHistory(
        () => upsertLocalThread(draft),
        "The new chat could not be saved on this computer.",
      );
      setThreads((previous) => [draft, ...previous]);
      setActiveThreadId(localId);
      threadId = localId;
      let serverId: string | null = null;
      try {
        serverId = await createThread(content.slice(0, 30), {
          botName: activeBot?.name ?? null,
          projectId: pendingProjectId,
        });
      } catch {
        /* Keep the first prompt locally; the next reconnect can reconcile it. */
      }
      if (serverId) {
        const serverThread: Thread = { ...draft, thread_id: serverId };
        try {
          const savedMeta = await remapThreadId(localId, serverThread);
          if (savedMeta) localThreadMetaRef.current[serverId] = savedMeta;
        } catch (error) {
          flash(`The server chat was created, but its local id could not be remapped. ${errMsg(error)}`);
        }
        setThreads((previous) =>
          previous.map((thread) => (thread.thread_id === localId ? serverThread : thread)),
        );
        setActiveThreadId(serverId);
        setPendingProjectId(null);
        threadId = serverId;
      }
    }
    const tid = threadId;
    const replay = options.replay;
    // Captured before the drop so a failed replay can put them back — a failed
    // attempt supersedes nothing on the server either, and leaving the
    // transcript with the original answer removed would be a silent deletion.
    const supersededMessages =
      replay && replay.supersedeIds.length > 0 ? messages.filter((m) => replay.supersedeIds.includes(m.id)) : [];
    if (replay && supersededMessages.length > 0) {
      // The superseded tail leaves the transcript *before* the replacement
      // turn begins, so the transcript shows the turn that is actually
      // running. The server's own filter drops the same rows from the paged
      // history once the replay succeeds; a failed attempt restores them (see
      // `showRequestFailure`), so reloading agrees with what is on screen.
      const superseded = new Set(replay.supersedeIds);
      setMessages((prev) => prev.filter((message) => !superseded.has(message.id)));
    }

    // Automatically detect and trigger slash command lifecycle at the right time
    // — but never for a replay: the original turn already ran its command, and
    // re-detecting here would fire it a second time for a regenerated prompt.
    let detection = undefined;
    if (!replay) {
      try {
        const d = await autoTriggerCommand(content, undefined, true, { thread_id: tid });
        if (d && d.matched) {
          detection = d;
        }
      } catch (e) {
        console.warn("Autonomous trigger check:", e);
      }
    }

    const userMsg: ChatMessage = {
      id: `user-${Date.now()}`,
      role: "user",
      content,
      autonomousDetection: detection,
      createdAt: new Date().toISOString(),
    };
    if (!replay || replay.appendUserMessage) {
      setMessages((prev) => [...prev, userMsg]);
      await persistLocalHistory(
        () => appendLocalMessages(tid, [userMsg]),
        "Your message is visible, but its local archive write failed.",
      );
    }
    // Record bot activity on the server (last_active / version bump).
    if (activeBot) void touchBot(activeBot.name);
    setSuggestions([]);
    const controller = new AbortController();
    abortRef.current = controller;
    const assistantMsgId = `asst-${Date.now()}`;
    const assistantCreatedAt = new Date().toISOString();
    let assistantText = "";
    let responseStarted = false;
    let deliveredMessages: ChatMessage[] = [];
    let completed = false;
    const streamedIds = new Set<string>();
    const speechSegmenter = voiceTurn ? new SpeechSegmenter() : null;
    let speechFailureShown = false;
    const enqueueVoiceSegment = (segment: string) => {
      const value = segment.trim();
      if (!value) return;
      void enqueueSpeech(value, {
        player: (valueToSpeak, signal) => speak(valueToSpeak, { signal }),
      }).catch((error: unknown) => {
        if (!isSpeechCancellation(error) && !speechFailureShown) {
          speechFailureShown = true;
          flash(`Voice speech failed: ${error instanceof Error ? error.message : String(error)}`);
        }
      });
    };
    const showRequestFailure = async (failure: ChatRequestFailure) => {
      let partialArchived = false;
      if (responseStarted && assistantText.trim()) {
        // The partial transcript is archived even when the user has already
        // navigated away — the local copy must not depend on the UI state.
        partialArchived = await persistLocalHistory(
          () => appendLocalMessages(tid, deliveredMessages),
          "The incomplete response could not be added to the local archive.",
        );
      }
      // After a navigation the visible thread is a different conversation, so
      // the retry panel/draft would be painted into the wrong place.
      if (!runIsCurrent()) return;
      setMessages((prev) => {
        const kept = prev.filter((message) => !streamedIds.has(message.id));
        if (supersededMessages.length === 0) return kept;
        // A replay that did not complete supersedes nothing: put its original
        // turn back, and drop the replacement row this call appended (its text
        // is in the retry panel, which is where the user can act on it).
        const appendedId = replay?.appendUserMessage ? userMsg.id : null;
        return [...kept.filter((message) => message.id !== appendedId), ...supersededMessages];
      });
      setRequestError({
        threadId: tid,
        message: chatRequestErrorMessage({ ...failure, partialArchived }),
        draft: text,
        partial: assistantText,
        partialArchived,
      });
      setInput((current) => current || text);
      if (failure.kind === "stopped") {
        updateLion("idle", "Stopping safely. The workspace is ready whenever you are.");
      } else {
        updateLion("error", "That path needs another look. I kept the draft safe.");
      }
    };

    try {
      const res = await apiFetch(`/threads/${encodeURIComponent(threadId)}/runs/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: controller.signal,
        body: JSON.stringify({
          assistant_id: activeBot?.name || "lead_agent",
          // A transient browser/network drop must not cancel durable work;
          // the explicit Stop action remains the cancellation boundary.
          on_disconnect: "continue",
          // `custom` carries the root-namespace `task_*` subagent events. Without
          // it the transcript can show only a spinner while a delegation runs.
          stream_mode: ["messages-tuple", "values", "custom"],
          ...(replay
            ? {
                // The prepared payload is authoritative: `input` is the graph
                // input recorded at the base checkpoint (a regenerate re-sends
                // the original question without a second user row; an edit
                // carries the replacement already spliced in), `checkpoint` is
                // the fork point *before* the superseded turn, and `metadata`
                // marks the run as a replay so the paged history hides the
                // attempt it replaces.
                input: replay.prepared.input,
                checkpoint: replay.prepared.checkpoint,
                metadata: replay.prepared.metadata,
              }
            : { input: { messages: [{ role: "user", content }] } }),
          config: {
            configurable: {
              model_name: selectedModel,
              ...(planMode ? { is_plan_mode: true } : {}),
              // `default` is omitted rather than sent as a level: it means "no
              // explicit request", and sending the literal string would be
              // rejected by the run boundary.
              ...(reasoningEffort !== DEFAULT_EFFORT ? { reasoning_effort: reasoningEffort } : {}),
            },
          },
        }),
      });

      responseStarted = true;
      updateLion("working", "I'm on it. Roaring quietly.");
      const updateStream = (partial: StreamMessage[]) => {
        deliveredMessages = partial.map((message) => ({
          id: message.runId ? JSON.stringify([message.runId, message.id]) : assistantMsgId,
          role: "assistant",
          content: message.content,
          thinking: message.thinking,
          toolCalls: message.toolCalls,
          createdAt: assistantCreatedAt,
          runId: message.runId || undefined,
        }));
        assistantText = deliveredMessages.map((message) => message.content).join("\n\n");
        if (speechSegmenter) {
          for (const segment of speechSegmenter.pushSnapshot(assistantText)) enqueueVoiceSegment(segment);
        }
        for (const message of deliveredMessages) streamedIds.add(message.id);
        // A run that outlived its conversation must not repaint the thread the
        // user opened next; the transcript is still archived below.
        if (!runIsCurrent()) return;
        setMessages((prev) => [...prev.filter((message) => !streamedIds.has(message.id)), ...deliveredMessages]);
      };
      const result = await consumeChatStream(res, {
        threadId: tid,
        signal: controller.signal,
        onUpdate: updateStream,
        onTasks: (tasks) => {
          // A run that outlived its conversation must not repaint the thread
          // the user opened next.
          if (!runIsCurrent()) return;
          setSubagentTasks(tasks);
        },
        onActivity: () => {
          // Same guard: a stale run's bytes must not keep a newer run's
          // silence clock looking fresh.
          if (!runIsCurrent()) return;
          lastByteAtRef.current = Date.now();
        },
        onEvent: (event) => {
          if (event.type === "replay-gap") flash("Some streamed events could not be replayed. This response is incomplete.");
        },
      });
      updateStream(result.messages);
      if (controller.signal.aborted) {
        await showRequestFailure({ kind: "stopped" });
        return false;
      }
      if (!assistantText.trim()) {
        await showRequestFailure({ kind: "empty" });
        return false;
      }
      if (speechSegmenter) {
        for (const segment of speechSegmenter.flush()) enqueueVoiceSegment(segment);
      }
      // The answer is committed before optional speech playback. Stopping
      // playback must not erase a response that already streamed successfully.
      completed = true;
      if (runIsCurrent()) updateLion("success", "Task complete. Nice work, team.", 4200);
      try {
        const usage = await fetchTokenUsage(threadId);
        if (runIsCurrent()) setUsage(usage);
      } catch {}

      await persistLocalHistory(
        () => appendLocalMessages(tid, deliveredMessages),
        "The response completed, but its local archive write failed.",
      );

      if (speechSegmenter) {
        // Keep capture paused until every already-queued local TTS segment has
        // finished (or was cancelled by Stop). This prevents self-hearing.
        await waitForSpeechIdle();
      }
      // Full-response autoplay remains the manual-text behavior. Voice turns
      // already streamed their sentence segments above and must not overlap a
      // second full-response request.
      if (!voiceTurn && readAutoplayEnabled()) {
        void autoplaySpeak(assistantText, { onFailure: (message) => flash(`Autoplay failed: ${message}`) });
      }

      // Follow-up suggestions.
      if (suggestionsOn) {
        try {
          const convo = [...messages, userMsg, { ...userMsg, id: assistantMsgId, role: "assistant" as const, content: assistantText }]
            .slice(-6)
            .map((m) => ({ role: m.role, content: m.content.slice(0, 2000) }));
          const s = await suggestFollowUps(threadId, convo);
          if (runIsCurrent()) setSuggestions(s);
        } catch {
          /* suggestions are optional */
        }
      }
    } catch (error) {
      await showRequestFailure(controller.signal.aborted
        ? { kind: "stopped" }
        : !responseStarted && error instanceof ApiClientError && error.kind === "http"
          ? { kind: "http", status: error.status }
          : { kind: responseStarted ? "stream" : "network" });
    } finally {
      // isLoading is a workspace-wide "a run is in flight" indicator, not a
      // per-thread view. It must always clear or the composer stays wedged
      // after a run that outlived its conversation.
      setIsLoading(false);
      abortRef.current = null;
      runLock.current = false;
    }
    return completed;
  };

  const handleVoiceTranscript = async (text: string) => {
    const content = text.trim();
    if (!content || !voiceConversationEnabledRef.current || voiceViewRef.current !== "chat") return;
    if (activeRunRef.current) {
      // A final endpoint can race a manual run. Keep the session alive without
      // creating a second run; the normal run guard remains authoritative.
      voiceResumePendingRef.current = true;
      return;
    }
    if (voiceTurnRef.current) return;
    const generation = voiceTurnGenerationRef.current;
    voiceTurnRef.current = true;
    setVoiceTurnActive(true);
    try {
      await sendMessage(content, { voiceTurn: true });
    } catch (error) {
      flash(`Voice turn failed: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      voiceTurnRef.current = false;
      if (generation === voiceTurnGenerationRef.current) {
        setVoiceTurnActive(false);
        // A navigation/stop during the run owns the outcome; do not resume into
        // a different thread or resurrect a user-disabled conversation.
        if (voiceConversationEnabledRef.current && voiceViewRef.current === "chat") {
          setVoiceResumeToken((token) => token + 1);
        }
      }
    }
  };

  const handleSubmit = () => {
    const text = input.trim();
    // Slash shortcuts run directly instead of starting an agent run.
    if (text.startsWith("/")) {
      runSlash(text);
      return;
    }
    sendMessage(input);
  };

  /** Execute a "/command" and show its result right in the chat. */
  const runSlash = async (command: string) => {
    if (activeRunRef.current || isLoading) return;
    activeRunRef.current = true;
    cancelSpeech();
    let currentThreadId = activeThreadId;
    if (!currentThreadId) {
      const localId = `local-${Date.now()}`;
      const draft: Thread = {
        thread_id: localId,
        title: command.slice(0, 30),
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        botName: activeBot?.name ?? null,
        projectId: pendingProjectId,
      };
      await persistLocalHistory(
        () => upsertLocalThread(draft),
        "The shortcut chat could not be saved on this computer.",
      );
      setThreads((prev) => [draft, ...prev]);
      setActiveThreadId(localId);
      currentThreadId = localId;
      let serverId: string | null = null;
      try {
        serverId = await createThread(command.slice(0, 30), {
          botName: activeBot?.name ?? null,
          projectId: pendingProjectId,
        });
      } catch {
        /* Keep the command in the local archive while offline. */
      }
      if (serverId) {
        const serverThread: Thread = { ...draft, thread_id: serverId };
        try {
          const savedMeta = await remapThreadId(localId, serverThread);
          if (savedMeta) localThreadMetaRef.current[serverId] = savedMeta;
        } catch (error) {
          flash(`The shortcut chat exists on the server, but its local id could not be remapped. ${errMsg(error)}`);
        }
        setThreads((prev) => prev.map((t) => (t.thread_id === localId ? serverThread : t)));
        setActiveThreadId(serverId);
        setPendingProjectId(null);
        currentThreadId = serverId;
      }
    }
    const tid = currentThreadId;
    const userMsg: ChatMessage = {
      id: `user-${Date.now()}`,
      role: "user",
      content: command,
      createdAt: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, userMsg]);
    await persistLocalHistory(
      () => appendLocalMessages(tid, [userMsg]),
      "Your message is visible, but its local archive write failed.",
    );
    setInput("");
    setIsLoading(true);
    setSubagentTasks([]);
    updateLion("working", "Running that shortcut...");
    const reply = async (content: string) => {
      const assistantMsg: ChatMessage = {
        id: `asst-${Date.now()}`,
        role: "assistant",
        content,
        createdAt: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, assistantMsg]);
      await persistLocalHistory(
        () => appendLocalMessages(tid, [assistantMsg]),
        "The shortcut result is visible, but its local archive write failed.",
      );
    };
    try {
      const out = await executeCommand(command, { thread_id: tid });
      await reply(out);
      updateLion("success", "Shortcut complete. The desk is clear.", 3600);
    } catch (e) {
      await reply(`Couldn't run that shortcut: ${e instanceof Error ? e.message : "unknown error"}`);
      updateLion("error", "That shortcut hit a snag. Nothing was lost.");
    } finally {
      setIsLoading(false);
      activeRunRef.current = false;
    }
  };

  /** Stop button: halt the stream, then cancel the run server-side (best effort). */
  const handleStop = async () => {
    updateLion("waiting", "Stopping safely. Checking the last safe checkpoint...");
    abortRef.current?.abort();
    cancelSpeech();
    if (voiceTurnRef.current) {
      voiceTurnGenerationRef.current += 1;
      voiceTurnRef.current = false;
      voiceResumePendingRef.current = false;
      voiceConversationEnabledRef.current = false;
      setVoiceTurnActive(false);
      setVoiceConversationEnabled(false);
    }
    if (activeThreadId) {
      try {
        const runs = await listThreadRuns(activeThreadId);
        const live = runs.find((r) => r.status === "running" || r.status === "pending");
        if (live) await cancelRun(activeThreadId, live.run_id);
        flash(live ? "Cancellation requested — check Runs for status." : "No active run found — check Runs for status.");
      } catch {
        /* stream abort alone already halts the UI */
      }
    }
  };

  /**
   * Regenerate the newest answer through the Gateway's prepare route.
   *
   * The old implementation called `sendMessage(lastUser.content)`, which
   * appended a *second* copy of the question and streamed a second answer
   * under the first one — the button's promise ("generate again") is
   * replacement, not accumulation, and the duplicate prompt also changed what
   * the model saw.
   */
  const handleRegenerate = async () => {
    if (!activeThreadId) return;
    const userIndex = messages.map((m) => m.role).lastIndexOf("user");
    if (userIndex < 0) return;
    const assistantIndex = messages.findIndex((m, i) => i > userIndex && m.role === "assistant");
    if (assistantIndex < 0) {
      flash("There is no answer to regenerate yet.");
      return;
    }
    const supersedeIds = messages.slice(assistantIndex).map((m) => m.id);
    try {
      const prepared = await prepareRegenerate(activeThreadId, messages[assistantIndex].id);
      if (!prepared) {
        flash("This Gateway has no regenerate endpoint, so nothing was sent.");
        return;
      }
      await sendMessage(messages[userIndex].content, {
        replay: { prepared, supersedeIds, appendUserMessage: false },
      });
    } catch (error) {
      // Refusing is the honest outcome: silently falling back would append the
      // duplicate turn this change exists to remove.
      flash(`The answer was not regenerated: ${errMsg(error)}`);
    }
  };

  /**
   * Edit a message and re-run from it.
   *
   * `messageId` was accepted and dropped — the replacement was appended as a
   * brand-new turn after the message it was supposed to replace, so the
   * transcript showed both versions and the model read the original text. The
   * prepare route hands back the replay base with the replacement spliced in;
   * everything from the edited message on is superseded by it.
   */
  const handleEditResend = async (messageId: string, newContent: string) => {
    const replacement = newContent.trim();
    if (!replacement || !activeThreadId) return;
    const index = messages.findIndex((m) => m.id === messageId);
    if (index < 0) return;
    const supersedeIds = messages.slice(index).map((m) => m.id);
    try {
      const prepared = await prepareEditRegenerate(activeThreadId, messageId, replacement);
      if (!prepared) {
        flash("This Gateway has no edit-replay endpoint, so nothing was sent.");
        return;
      }
      await sendMessage(replacement, {
        replay: { prepared, supersedeIds, appendUserMessage: true },
      });
    } catch (error) {
      flash(`The edit was not applied: ${errMsg(error)}`);
    }
  };

  const handleRate = async (messageId: string, rating: 1 | -1) => {
    const msg = messages.find((m) => m.id === messageId);
    if (!msg?.runId || !activeThreadId) return;
    const next = msg.rating === rating ? undefined : rating;
    try {
      if (next) await rateMessage(activeThreadId, msg.runId, next);
      setMessages((prev) => prev.map((m) => (m.id === messageId ? { ...m, rating: next } : m)));
      await persistLocalHistory(
        () => updateLocalMessage(activeThreadId, messageId, { rating: next }),
        "The rating is saved on the server, but its local copy could not be updated.",
      );
      if (next) flash(next === 1 ? "Thanks — rated helpful." : "Noted — rated not helpful.");
    } catch {
      flash("Couldn't save your rating right now.");
    }
  };

  const handlePolish = async () => {
    if (!input.trim() || polishing) return;
    setPolishing(true);
    try {
      const { text, changed } = await polishDraft(input, activeThreadId ?? undefined);
      setInput(text);
      flash(changed ? "Draft improved — review and send." : "Draft already looks good.");
    } catch {
      flash("Couldn't polish right now — send as-is.");
    } finally {
      setPolishing(false);
    }
  };

  const handleAttach = async (files: FileList | File[]) => {
    if (files.length === 0) return;
    // Synchronous lock: two rapid drops (or a drop while a run is starting)
    // must not each create their own draft thread and server row.
    if (attachmentLockRef.current) {
      flash("An upload is already in progress — wait for it to finish.");
      return;
    }
    attachmentLockRef.current = true;
    try {
      await runAttachment(files);
    } finally {
      attachmentLockRef.current = false;
    }
  };

  const runAttachment = async (files: FileList | File[]) => {
    let threadId = activeThreadId;
    const draftProjectId = pendingProjectId;
    let draftLocalId: string | null = null;
    let draftServerId: string | null = null;
    if (!threadId) {
      const localId = `local-${Date.now()}`;
      draftLocalId = localId;
      const draft: Thread = {
        thread_id: localId,
        title: `Files: ${files[0]?.name || "uploads"}`,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        botName: activeBot?.name ?? null,
        projectId: draftProjectId,
      };
      await persistLocalHistory(
        () => upsertLocalThread(draft),
        "The upload chat could not be saved on this computer.",
      );
      setThreads((prev) => [draft, ...prev]);
      setActiveThreadId(localId);
      threadId = localId;
      let serverId: string | null = null;
      try {
        serverId = await createThread(draft.title, {
          botName: activeBot?.name ?? null,
          projectId: draftProjectId,
        });
      } catch {
        /* Keep the upload draft locally; upload it after reconnecting. */
      }
      if (serverId) {
        draftServerId = serverId;
        const serverThread: Thread = { ...draft, thread_id: serverId };
        try {
          const savedMeta = await remapThreadId(localId, serverThread);
          if (savedMeta) localThreadMetaRef.current[serverId] = savedMeta;
        } catch (error) {
          flash(`The upload chat exists on the server, but its local id could not be remapped. ${errMsg(error)}`);
        }
        setThreads((prev) => prev.map((t) => (t.thread_id === localId ? serverThread : t)));
        setActiveThreadId(serverId);
        setPendingProjectId(null);
        threadId = serverId;
      }
    }
    setUploading(true);
    try {
      const added = await uploadFiles(threadId, files);
      flash(`${added.length} file(s) attached — mention them in your message.`);
    } catch (e) {
      const uploadError = errMsg(e);
      if (draftLocalId || draftServerId) {
        let cleanupError: string | null = null;
        if (draftServerId) {
          try {
            await deleteThread(draftServerId);
          } catch (cleanupFailure) {
            cleanupError = errMsg(cleanupFailure);
          }
        }
        if (!cleanupError) {
          try {
            if (draftLocalId) await removeLocalThread(draftLocalId);
            if (draftServerId) await removeLocalThread(draftServerId);
            setThreads((previous) =>
              previous.filter(
                (thread) => thread.thread_id !== draftLocalId && thread.thread_id !== draftServerId,
              ),
            );
            setActiveThreadId((current) =>
              current === draftLocalId || current === draftServerId ? null : current,
            );
            setMessages([]);
            setPendingProjectId(draftProjectId);
            flash(`Upload failed: ${uploadError}. The empty draft was removed.`);
          } catch (localCleanupFailure) {
            cleanupError = errMsg(localCleanupFailure);
          }
        }
        if (cleanupError) {
          setPendingProjectId(draftProjectId);
          flash(`Upload failed: ${uploadError}. The empty draft could not be removed: ${cleanupError}`);
        }
      } else {
        flash(`Upload failed: ${uploadError}`);
      }
    } finally {
      setUploading(false);
    }
  };

  const handleSaveGoal = async () => {
    if (!activeThreadId || !goalDraft.trim()) return;
    try {
      await setGoal(activeThreadId, goalDraft.trim());
      setGoalText(goalDraft.trim());
      setGoalEditing(false);
      localThreadMetaRef.current[activeThreadId] = {
        ...(localThreadMetaRef.current[activeThreadId] || { botName: null, goal: null }),
        goal: goalDraft.trim(),
      };
      await persistLocalHistory(
        () => setThreadMeta(activeThreadId, { goal: goalDraft.trim() }),
        "The goal is saved on the server, but its local copy could not be updated.",
      );
      flash("Goal set — the agent works toward it until done.");
    } catch {
      flash("Couldn't save the goal.");
    }
  };

  const handleClearGoal = async () => {
    if (!activeThreadId) return;
    try {
      await clearGoal(activeThreadId);
      setGoalText(null);
      setGoalEditing(false);
      localThreadMetaRef.current[activeThreadId] = {
        ...(localThreadMetaRef.current[activeThreadId] || { botName: null, goal: null }),
        goal: null,
      };
      await persistLocalHistory(
        () => setThreadMeta(activeThreadId, { goal: null }),
        "The goal was cleared on the server, but its local copy could not be updated.",
      );
    } catch {
      flash("Couldn't clear the goal.");
    }
  };

  const handleCompact = async () => {
    if (!activeThreadId) return;
    if (!window.confirm("Summarize older messages to free context? Recent messages stay intact.")) return;
    try {
      const summary = await compactThread(activeThreadId);
      flash(summary.slice(0, 200));
      const history = await fetchThreadHistoryResult(activeThreadId);
      if (!history.ok) throw new Error(history.error);
      setMessages(history.value);
      await setLocalMessages(activeThreadId, history.value);
      setUsage(await fetchTokenUsage(activeThreadId));
    } catch {
      flash("Compaction isn't available right now.");
    }
  };

  const openThread = (id: string) => {
    stopVoiceForNavigation();
    // Keep the bot space in sync: opening another bot's chat switches scope to it.
    const t = threads.find((x) => x.thread_id === id);
    const owner = t ? threadOwner(t) : null;
    if (owner) {
      setActiveBot(bots.find((x) => x.name === owner) || null);
    } else {
      setActiveBot(null);
    }
    setActiveThreadId(id);
    setView("chat");
  };

  const handleViewChange = (nextView: WorkspaceView) => {
    if (nextView !== "chat") stopVoiceForNavigation();
    setView(nextView);
  };

  const handleExportHistory = async () => {
    try {
      const blob = new Blob([await exportStoreJson()], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `alpha-history-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      window.setTimeout(() => URL.revokeObjectURL(a.href), 5000);
      flash("Complete local history downloaded — keep it safe or move it to another browser.");
    } catch (error) {
      flash(`Couldn't export history. ${errMsg(error)}`);
    }
  };

  const handleImportHistory = async (f: File): Promise<string> => {
    const text = await f.text();
    const { threads: tCount, messages: mCount } = await importStoreJson(text);
    const threadResult = await fetchThreadsResult();
    if (!threadResult.ok) {
      flash(`Imported ${tCount} chats and ${mCount} messages, but server history could not be re-read. ${threadResult.error}`);
      return `Imported ${tCount} chats and ${mCount} messages. Server refresh is unavailable.`;
    }
    setServerHistoryError(null);
    const { visible, notes } = await hideConfirmedEmptyDrafts(threadResult.value);
    if (threadResult.incomplete) flash(`Chat list is incomplete — ${threadResult.incomplete}. Showing what loaded.`);
    else if (notes.length > 0) flash(notes[0]);
    const merged = await mergeThreads(visible);
    setThreads(merged);
    return `Imported ${tCount} chats and ${mCount} messages.`;
  };

  const lastAssistantId = [...messages].reverse().find((m) => m.role === "assistant")?.id;
  const selectedModelName = models.find((m) => m.id === selectedModel)?.name || null;
  // Unread is a measured total only if the roster read asked for it: a row
  // without `unread_count` means "not projected", and mixing an unknown row
  // into a sum would invent a number. `null` (unknown) hides the badge instead
  // of showing one.
  const unreadCount =
    bots.length > 0 && bots.every((bot) => typeof bot.unread_count === "number")
      ? bots.reduce((total, bot) => total + (bot.unread_count ?? 0), 0)
      : null;

  return (
    <div className="flex flex-col h-screen w-screen overflow-hidden bg-background">
      {/* ── Global Top Bar (Alpha AI Agent Workspace, Ctrl+K Search, Notifications, Settings, User) ── */}
      <WorkspaceTopBar
        onOpenSearch={() => setOmnisearchOpen(true)}
        onOpenSettings={() => setView("settings")}
        onOpenView={(v) => setView(v)}
        gatewayOk={gatewayOk}
        userInitials={operator.name ? operator.initials : null}
        userName={operator.name}
        botLabel={activeBot ? activeBot.display_name || activeBot.name : "Lead Agent"}
        projectLabel={activeProject ? activeProject.name : "Standalone"}
        threadLabel={threads.find((t) => t.thread_id === activeThreadId)?.title || null}
        unreadCount={unreadCount}
      />

      {/* ── Omnisearch Command Palette (Ctrl+K) ── */}
      <OmnisearchModal
        isOpen={omnisearchOpen}
        onClose={() => setOmnisearchOpen(false)}
        bots={bots}
        projects={projects}
        threads={threads}
        onSelectBot={rememberBot}
        onSelectProject={(id) => void handlePickProject(id)}
        onSelectThread={(id) => openThread(id)}
        onNewConversation={() => {
          handleNewChat();
        }}
        onNewProject={() => {
          setView("projects");
        }}
        onOpenSettings={() => setView("settings")}
      />

      <div className="flex flex-1 overflow-hidden min-h-0">
        {view === "chat" && (
          <ThreadSidebar
          threads={activeBot ? threads.filter((t) => threadOwner(t) === activeBot.name) : threads}
          threadsLoading={threadsLoading}
          activeThreadId={activeThreadId}
          scopeLabel={activeBot ? activeBot.display_name || activeBot.name : null}
          scopeAvatar={activeBot?.avatar || ""}
          ownerLabel={(t) => {
            const o = threadOwner(t);
            if (!o) return null;
            return bots.find((b) => b.name === o)?.display_name || o;
          }}
          onSelectThread={(id) => {
            openThread(id);
          }}
          onNewChat={handleNewChat}
          onThreadsChanged={() => {
            void reloadThreads().catch((error) => flash(`Could not refresh chat history. ${errMsg(error)}`));
          }}
          onBranchOpened={(id) => {
            stopVoiceForNavigation();
            void reloadThreads(id).catch((error) => flash(`Could not open the branched chat. ${errMsg(error)}`));
          }}
          onExportHistory={handleExportHistory}
          onImportHistory={handleImportHistory}
          serverOnline={gatewayOk === true && serverHistoryError === null}
          onOpenSettings={() => setView("settings")}
          onProjectsChanged={async () => {
            setProjects(await listProjects());
          }}
          // Bot -> Project -> Conversation. Rendered inside the sidebar, above
          // the conversation list. Nothing that was already in this sidebar was
          // removed to make room for it: the New Chat button, the scope
          // summary, the search box, the grouped conversation list, the local
          // message search and the storage footer are all untouched.
          rail={
            <ChatShell
              bots={bots}
              activeBot={activeBot}
              threads={activeBot ? threads.filter((t) => threadOwner(t) === activeBot.name) : threads}
              projects={projects}
              activeThreadId={activeThreadId}
              activeProjectId={activeProjectId}
              onSelectBot={rememberBot}
              onSelectThread={(id) => {
                openThread(id);
              }}
              onNewConversation={(projectId) => {
                handleNewChat();
                if (projectId !== null) void handlePickProject(projectId);
              }}
              onPickProject={(projectId) => {
                void handlePickProject(projectId);
              }}
              onOpenView={(target) => setView(target)}
              onProjectsChanged={async () => {
                setProjects(await listProjects());
              }}
            />
          }
        />
      )}

      <main className="flex-1 flex flex-col h-full overflow-hidden min-w-0">
        {/* Workspace navigation for non-chat views */}
        {view !== "chat" && (
          <header className="border-b border-border/60 px-3 pt-2 pb-1.5 bg-card/20 shrink-0 space-y-1.5">
            <div className="overflow-x-auto">
              {/* Each badge is that tab's own measured count. The Projects tab must
                  read the project list, not inherit the bot roster: an
                  installation with 42 bots and 2 projects would otherwise show
                  "42" next to Projects. */}
              <NavTabs
                view={view}
                onChange={handleViewChange}
                badge={{ bots: bots.length, projects: projects.length }}
              />
            </div>

            {/* Live backend vitals: connectivity, usage and subsystem readiness.
                Update state moved to `WorkspaceTopBar`, which renders in every
                view — here it was a second instance of a control the chat view
                never showed at all. */}
            <div className="flex items-start justify-between gap-3 flex-wrap">
              <WorkspaceVitals />
              <div className="flex items-center gap-1.5 ml-auto">
                <button
                  type="button"
                  onClick={refreshFreeCatalog}
                  disabled={freeRefreshing}
                  className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground px-2 py-1 rounded-lg hover:bg-muted/70 transition-colors disabled:opacity-40"
                  title={freeNote || "Free keyless models — click to refresh live catalog"}
                  aria-label={freeNote || "Free keyless models — not read yet. Click to refresh the live catalog."}
                >
                  <span className={`size-1.5 rounded-full ${FREE_TONE_DOT[freeTone]}`} aria-hidden="true" />
                  <span className="hidden lg:inline">{freeRefreshing ? "Refreshing free…" : freeNote ? freeNote.split(".")[0] : "Free models"}</span>
                  <span className="lg:hidden">Free</span>
                </button>
                <button
                  type="button"
                  onClick={() => setView("settings")}
                  className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground px-2 py-1 rounded-lg hover:bg-muted/70 transition-colors"
                  title="Open Settings"
                  aria-label="Open Settings — model selection, theme and API diagnostics"
                >
                  <Settings className="size-3.5" />
                  {/*
                    This control used to collapse to a bare 30px gear below the
                    `lg` breakpoint, because its only label was behind
                    `hidden lg:inline` and it carried no `aria-label`. A gear
                    glyph next to a model name is guesswork, so the word "Settings"
                    now stays at every width and the selected model is a separate,
                    explicitly-labelled line underneath.
                  */}
                  <span className="whitespace-nowrap">Settings</span>
                  <span className="sr-only">
                    {selectedModelName ? ` — selected model: ${selectedModelName}` : " — no model selected"}
                  </span>
                  {selectedModelName ? (
                    <span className="hidden lg:inline text-muted-foreground/80 font-normal">· {selectedModelName}</span>
                  ) : null}
                </button>
              </div>
            </div>
          </header>
        )}

        {/* Project Context Header for Chat View (matches reference design) */}
        {view === "chat" && (
          <ProjectContextHeader
            bot={activeBot}
            bots={bots}
            project={activeProject}
            projectKnown={!activeProjectId || activeProject !== null}
            thread={threads.find((thread) => thread.thread_id === activeThreadId) ?? null}
            threads={threads}
            projects={projects}
            projectThreadCount={activeProjectThreadCount}
            onSwitchProject={(id) => void handlePickProject(id)}
            onSelectBot={rememberBot}
            onSelectThread={(id) => openThread(id)}
            onNewConversation={() => {
              handleNewChat();
            }}
            onNewProject={() => setView("projects")}
            onOpenView={(target) => setView(target)}
            activeTab={activeContextTab}
            onTabChange={(tab) => setActiveContextTab(tab)}
            onToggleInspector={() => setInspectorOpen((v) => !v)}
            inspectorOpen={inspectorOpen}
          />
        )}

        {gatewayOk === false && !offlineDismissed && (
          <div className="shrink-0 px-4 pt-2">
            {/* An interruption: `role="alert"` so a screen reader is told now,
                not on the next focus move. */}
            <div role="alert" className="max-w-4xl mx-auto flex items-center gap-2.5 rounded-xl border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs">
              <span className="size-2 rounded-full bg-destructive animate-pulse shrink-0" aria-hidden="true" />
              <span className="flex-1 min-w-0">
                <strong>Backend not connected.</strong>{" "}
                <span className="text-muted-foreground">Chats stay in this browser until the Gateway runs. Start it with <code className="font-mono">.\start.ps1</code>, then refresh.</span>
              </span>
              <button type="button" onClick={() => setView("system")} className="px-2.5 py-1 rounded-lg bg-destructive text-destructive-foreground text-[11px] font-semibold shrink-0">
                Diagnose
              </button>
              <button type="button" onClick={() => setOfflineDismissed(true)} className="px-2 py-1 rounded-lg text-[11px] text-muted-foreground hover:text-foreground shrink-0" aria-label="Dismiss offline warning">
                Dismiss
              </button>
            </div>
          </div>
        )}

        {serverHistoryError && view === "chat" && (
          <div className="shrink-0 px-4 pt-2">
            <div role="alert" className="max-w-4xl mx-auto flex items-start gap-2.5 rounded-xl border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs">
              <span className="size-2 rounded-full bg-amber-500 shrink-0 mt-1" aria-hidden="true" />
              <span className="flex-1 min-w-0">
                <strong>Server conversation list unavailable.</strong>{" "}
                <span className="text-muted-foreground">
                  {serverHistoryError} — you are seeing the complete copy saved on this computer, not a confirmed empty history.
                </span>
              </span>
              <button
                type="button"
                onClick={() => void reloadThreads().catch((error) => flash(`Could not refresh chat history. ${errMsg(error)}`))}
                className="px-2.5 py-1 rounded-lg border border-amber-500/40 text-[11px] font-semibold shrink-0"
              >
                Retry
              </button>
              <button
                type="button"
                onClick={() => setServerHistoryError(null)}
                className="px-2 py-1 rounded-lg text-[11px] text-muted-foreground hover:text-foreground shrink-0"
                aria-label="Dismiss server history warning"
              >
                Dismiss
              </button>
            </div>
          </div>
        )}

        {notice && (
          <div className="shrink-0 px-4 pt-2">
            {/* Transient by design, so `status` (polite) rather than `alert`
                (interruptive): it reports a completed action, not a failure. */}
            <div
              role="status"
              aria-live="polite"
              className="max-w-4xl mx-auto rounded-xl border border-primary/30 bg-primary/5 px-3 py-2 text-xs"
            >
              {notice}
            </div>
          </div>
        )}

        <ErrorBoundary resetKey={view} label={view}>
        {view === "warroom" ? (
          <Suspense fallback={<SectionFallback />}>
            <WarRoomSection />
          </Suspense>
        ) : view === "deliberation" ? (
          <Suspense fallback={<SectionFallback />}>
            <WarRoomRunsSection />
          </Suspense>
        ) : view === "bots" ? (
          <div className="shrink-0 px-4 sm:px-6 pt-3">
            <div className="max-w-6xl mx-auto flex gap-1 rounded-xl bg-muted/60 p-1 w-fit">
              {(["profiles", "ops"] as const).map((t) => (
                <button
                  key={t}
                  type="button"
                  onClick={() => setBotsTab(t)}
                  className={`px-3 py-1.5 rounded-lg text-[11px] font-semibold ${botsTab === t ? "bg-card shadow" : "text-muted-foreground hover:text-foreground"}`}
                >
                  {t === "profiles" ? `Profiles (${bots.length})` : "Team ops"}
                </button>
              ))}
            </div>
          </div>
        ) : null}

        {view === "bots" && botsTab === "ops" ? (
          <Suspense fallback={<SectionFallback />}>
            <BotOpsSection bots={bots} onRefreshBots={refreshBots} />
          </Suspense>
        ) : view === "bots" ? (
          <BotGallery
            bots={bots}
            activeBotName={activeBot?.name || null}
            isLoading={botsLoading}
            onSelect={setInspectedBot}
            onChat={handleChatWithBot}
            onRefresh={refreshBots}
          />
        ) : view === "messages" ? (
          <Suspense fallback={<SectionFallback />}>
            <MessagesSection threadId={activeThreadId} botNames={bots.map((b) => b.display_name || b.name)} />
          </Suspense>
        ) : view === "peers" ? (
          <Suspense fallback={<SectionFallback />}>
            <PeerNetworkSection />
          </Suspense>
        ) : view === "kanban" ? (
          <Suspense fallback={<SectionFallback />}>
            <KanbanSection bots={bots.map((b) => ({ name: b.name, display_name: b.display_name || b.name, avatar: b.avatar }))} />
          </Suspense>
        ) : view === "runs" ? (
          <Suspense fallback={<SectionFallback />}>
            <RunsSection threadId={activeThreadId} />
          </Suspense>
        ) : view === "run-inspector" ? (
          <Suspense fallback={<SectionFallback />}>
            <RunInspectorSection threadId={activeThreadId} />
          </Suspense>
        ) : view === "files" ? (
          <Suspense fallback={<SectionFallback />}>
            <FilesSection threadId={activeThreadId} />
          </Suspense>
        ) : view === "scheduled" ? (
          <Suspense fallback={<SectionFallback />}>
            <ScheduledSection bots={bots} />
          </Suspense>
        ) : view === "subagents" ? (
          <Suspense fallback={<SectionFallback />}>
            <SubagentsSection threadId={activeThreadId} />
          </Suspense>
        ) : view === "skills" ? (
          <Suspense fallback={<SectionFallback />}>
            <SkillsSection />
          </Suspense>
        ) : view === "workflows" ? (
          <Suspense fallback={<SectionFallback />}>
            <WorkflowsSection />
          </Suspense>
        ) : view === "forge" ? (
          <Suspense fallback={<SectionFallback />}>
            <ForgeSection />
          </Suspense>
        ) : view === "supervisor" ? (
          <Suspense fallback={<SectionFallback />}>
            <SupervisorSection />
          </Suspense>
        ) : view === "protocols" ? (
          <Suspense fallback={<SectionFallback />}>
            <ProtocolsSection />
          </Suspense>
        ) : view === "memory" ? (
          <Suspense fallback={<SectionFallback />}>
            <MemorySection />
          </Suspense>
        ) : view === "projects" ? (
          <Suspense fallback={<SectionFallback />}>
            <ProjectsSection
              onOpenThread={openThread}
              threads={threads}
              bots={bots.map((b) => ({ name: b.name, display_name: b.display_name || b.name }))}
              onOpenLiveProject={(projectId) => {
                // The Workforce view owns its own project selection and otherwise
                // defaults to the FIRST project, so switching views alone would land
                // on a different project than the one the user just opened. Select
                // it here, then switch.
                setFocusedProjectId(projectId);
                setView("workforce");
              }}
              onThreadsChanged={() => {
                void reloadThreads().catch((error) => flash(`Could not refresh chat history. ${errMsg(error)}`));
              }}
            />
          </Suspense>
        ) : view === "dashboard" ? (
          <Suspense fallback={<SectionFallback />}>
            <DashboardSection onOpenThread={openThread} />
          </Suspense>
        ) : view === "agents" ? (
          <Suspense fallback={<SectionFallback />}>
            <AgentsSection enabled={features.agentsApi} />
          </Suspense>
        ) : view === "team" ? (
          <Suspense fallback={<SectionFallback />}>
            <TeamOpsSection threadId={activeThreadId} mcpTasksAvailable={features.mcpTasks} />
          </Suspense>
        ) : view === "channels" ? (
          <Suspense fallback={<SectionFallback />}>
            <ChannelsSection />
          </Suspense>
        ) : view === "workforce" ? (
          <Suspense fallback={<SectionFallback />}>
            <WorkforceSection
              bots={bots.map((b) => ({ name: b.name, display_name: b.display_name || b.name }))}
              focusedProjectId={focusedProjectId}
            />
          </Suspense>
        ) : view === "system" ? (
          <Suspense fallback={<SectionFallback />}>
            <SystemSection threadId={activeThreadId} browserActive={features.browserControl} />
          </Suspense>
        ) : view === "integration" ? (
          <Suspense fallback={<SectionFallback />}>
            <IntegrationSection />
          </Suspense>
        ) : view === "settings" ? (
          <Suspense fallback={<SectionFallback />}>
            <SettingsSection
              initialTab={settingsInitialTab}
              currentModel={selectedModel}
              onModelChange={(m) => {
                setSelectedModel((current) => {
                  const next = m;
                  if (next !== current) {
                    setReasoningEffort((effort) => reconcileEffortForModel(effort, models.find((x) => x.id === next) ?? null));
                  }
                  return next;
                });
                try {
                  localStorage.setItem("alpha_selected_model", m);
                } catch {}
              }}
              onModelsUpdated={async () => {
                const mList = await fetchAvailableModels();
                setModels(mList);
              }}
              onOpenView={(v) => setView(v)}
            />
          </Suspense>
        ) : (
          <div className="flex-1 flex overflow-hidden min-h-0">
            {activeContextTab === "files" ? (
              <Suspense fallback={<SectionFallback />}>
                <FilesSection threadId={activeThreadId} />
              </Suspense>
            ) : activeContextTab === "tasks" ? (
              <Suspense fallback={<SectionFallback />}>
                <KanbanSection bots={bots.map((b) => ({ name: b.name, display_name: b.display_name || b.name, avatar: b.avatar }))} />
              </Suspense>
            ) : activeContextTab === "knowledge" ? (
              <Suspense fallback={<SectionFallback />}>
                <MemorySection />
              </Suspense>
            ) : (
              <div className="flex-1 flex flex-col h-full overflow-hidden min-w-0">
            {/* Active-bot banner */}
            {activeBot && (
              <div className="shrink-0 px-4 pt-3">
                <div className="max-w-4xl mx-auto flex items-center gap-2.5 rounded-xl border border-primary/30 bg-primary/5 px-3 py-2 text-xs">
                  <div className="size-7 rounded-lg bg-primary/10 flex items-center justify-center font-bold overflow-hidden shrink-0">
                    {activeBot.avatar || (activeBot.display_name || activeBot.name).slice(0, 2).toUpperCase()}
                  </div>
                  <span className="min-w-0">
                    Chatting as <strong>{activeBot.display_name || activeBot.name}</strong>
                    <span className="text-muted-foreground"> — {activeBot.role}</span>
                  </span>
                  <button
                    type="button"
                    onClick={() => rememberBot(null)}
                    className="ml-auto text-[11px] font-medium text-muted-foreground hover:text-foreground px-2 py-1 rounded-lg hover:bg-muted shrink-0"
                  >
                    Reset to Lead Agent
                  </button>
                </div>
              </div>
            )}

            {/* Goal bar */}
            <div className="shrink-0 px-4 pt-2">
              <div className="max-w-4xl mx-auto">
                {goalEditing ? (
                  <div className="flex gap-2 rounded-xl border border-primary/30 bg-card p-2">
                    <input
                      value={goalDraft}
                      onChange={(e) => setGoalDraft(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") handleSaveGoal();
                        if (e.key === "Escape") setGoalEditing(false);
                      }}
                      placeholder="What is the goal of this conversation? e.g. Ship the landing page"
                      autoFocus
                      aria-label="Conversation goal"
                      className="flex-1 bg-transparent px-2 py-1.5 text-xs focus:outline-none"
                    />
                    <button type="button" onClick={handleSaveGoal} disabled={!goalDraft.trim()} className="px-2.5 py-1 rounded-lg bg-primary text-primary-foreground text-[11px] font-semibold disabled:opacity-40">
                      Set
                    </button>
                    <button type="button" onClick={() => setGoalEditing(false)} className="px-2.5 py-1 rounded-lg border border-border text-[11px] hover:bg-muted">
                      Cancel
                    </button>
                  </div>
                ) : goal ? (
                  <div className="flex items-center gap-2 rounded-xl border border-primary/30 bg-primary/5 px-3 py-1.5 text-xs">
                    <Target className="size-3.5 text-primary shrink-0" />
                    <span className="flex-1 truncate"><strong>Goal:</strong> {goal}</span>
                    <button type="button" onClick={() => { setGoalDraft(goal); setGoalEditing(true); }} className="text-[11px] text-muted-foreground hover:text-foreground">Edit</button>
                    <button type="button" onClick={handleClearGoal} className="text-[11px] text-muted-foreground hover:text-destructive">Clear</button>
                  </div>
                ) : activeThreadId ? (
                  <button type="button" onClick={() => { setGoalDraft(""); setGoalEditing(true); }} className="inline-flex items-center gap-1.5 text-[11px] text-muted-foreground hover:text-foreground px-1 py-0.5">
                    <Target className="size-3.5" /> Set a goal for this conversation
                  </button>
                ) : null}
              </div>
            </div>

            {/* Messages Viewport.

                The scroller is `flex-1`, so it always fills the space between
                the header and the composer. Its children were laid out from the
                top, which meant a two-line answer left a screenful of blank
                between the last message and the composer — measured at 235px of
                scroller holding 251px of content at a 700px viewport, i.e. the
                region grows with the window rather than with the conversation.

                The fix is to bottom-anchor the content instead of deleting the
                space: an inner `min-h-full flex flex-col justify-end` grows with
                the conversation, and `justify-end` has no effect once the
                content is taller than the scroller, so scrolling a long
                transcript is unchanged. `space-y-4` became the inner wrapper's
                `gap-4` because `space-y` on a flex column adds top margins that
                a bottom-anchored layout would render as leading blank. */}
            {/* `role="log"` + polite live region: without it a screen reader
                gets the transcript as inert markup and hears nothing when an
                answer arrives. It is a focusable region too, so the scroller can
                be driven from the keyboard (WCAG 2.1.1) — which also makes the
                "I scrolled up" state reachable without a pointer, and that state
                is what disarms auto-follow below. */}
            <div className="flex-1 overflow-y-auto px-4 py-6"
              ref={transcriptRef}
              onScroll={handleTranscriptScroll}
              tabIndex={0}
              role="log"
              aria-live="polite"
              aria-relevant="additions"
              aria-label="Conversation transcript"
            >
              <div className="min-h-full flex flex-col justify-end gap-4">
                {messages.length === 0 ? (
                  <div
                    className="h-full flex flex-col items-center justify-center text-center max-w-2xl mx-auto space-y-4 w-full px-2"
                    aria-label={branding.name}
                  >
                    <h2 className="text-lg font-semibold text-foreground tracking-tight">
                      <BrandLogo logoSize={44} textClassName="text-lg text-foreground" priority />
                    </h2>
                    <p className="text-xs text-muted-foreground leading-relaxed">
                      {activeBot
                        ? `Talking to ${activeBot.display_name || activeBot.name} (${activeBot.role}). Switch specialists anytime from the Bots tab.`
                        : branding.intro}
                    </p>
                    {bots.length > 0 && (
                      <div className="flex flex-wrap justify-center gap-1.5 pt-1">
                        {bots.slice(0, 5).map((b) => (
                          <button
                            key={b.name}
                            type="button"
                            onClick={() => rememberBot(b)}
                            className={`text-[11px] px-2.5 py-1.5 rounded-lg border font-medium transition-colors ${
                              activeBot?.name === b.name
                                ? "border-primary bg-primary/10 text-primary"
                                : "border-border/70 hover:border-primary/40 text-muted-foreground hover:text-foreground"
                            }`}
                          >
                            {b.avatar ? `${b.avatar} ` : ""}{b.display_name || b.name}
                          </button>
                        ))}
                        <button
                          type="button"
                          onClick={() => setView("bots")}
                          className="text-[11px] px-2.5 py-1.5 rounded-lg border border-dashed border-border/70 text-muted-foreground hover:text-foreground font-medium"
                        >
                          View all {bots.length} →
                        </button>
                      </div>
                    )}
                    {/* Added beneath the existing welcome block, not in place of
                        it: the logo, the intro sentence and the bot chips above
                        are all still here. This adds the one line that names the
                        bot and the project, and the starter actions, each of
                        which is a real request to this Gateway. */}
                    <div className="w-full pt-1">
                      <ChatShellEmptyState
                        botName={activeBot ? activeBot.display_name || activeBot.name : null}
                        botRole={activeBot?.role}
                        botAvatar={activeBot?.avatar}
                        projectId={activeProjectId}
                        projectName={
                          projects.find((p) => p.id === activeProjectId)?.name ?? null
                        }
                        userName={operator.name}
                        returning={threads.length > 0}
                        onPickStarter={(prompt) => setInput(prompt)}
                        onReviewProject={() => setInspectorOpen(true)}
                      />
                    </div>
                  </div>
                ) : (
                  messages.map((msg) => (
                    <MessageItem
                      key={msg.id}
                      message={msg}
                      onRate={handleRate}
                      onRegenerate={handleRegenerate}
                      showRegenerate={msg.id === lastAssistantId && msg.role === "assistant"}
                      regenerating={isLoading}
                      onEdit={handleEditResend}
                      streaming={isLoading && msg.role === "assistant" && msg.id === lastAssistantId}
                    />
                  ))
                )}

                {requestError && requestError.threadId === activeThreadId && (
                  <div className="max-w-4xl mx-auto space-y-2">
                    <div role="alert">
                      <ErrorBox
                        message={requestError.message}
                        onRetry={!isLoading ? () => sendMessage(input.trim() ? input : requestError.draft) : undefined}
                      />
                    </div>
                    {requestError.partial && (
                      <div className="rounded-xl border border-destructive/40 p-3 text-xs">
                        <p className="font-semibold mb-2">
                          Incomplete response — {requestError.partialArchived ? "kept in local history" : "local archive write failed"}
                        </p>
                        <pre className="whitespace-pre-wrap break-words font-sans">{requestError.partial}</pre>
                      </div>
                    )}
                  </div>
                )}

                {isLoading && activity && (
                  <div className="max-w-4xl mx-auto">
                    <ActivityStatus
                      state={activity}
                      elapsedMs={elapsedMs}
                      silence={silence}
                      actor={activeBot ? activeBot.display_name || activeBot.name : branding.assistantLabel}
                    />
                  </div>
                )}

                {/* Subagent progress survives the run that produced it, so the
                    answer lands next to the receipt of the work behind it, and
                    only the next prompt clears it. */}
                {subagentTasks.length > 0 && (
                  <div className="max-w-4xl mx-auto">
                    <SubagentList tasks={subagentTasks} />
                  </div>
                )}
                <div ref={messagesEndRef} />
              </div>
            </div>

            {/* Follow-up suggestions */}
            {suggestions.length > 0 && !isLoading && (
              <div className="shrink-0 px-3 pb-1">
                <div className="max-w-4xl mx-auto flex gap-1.5 flex-wrap">
                  {suggestions.map((s, i) => (
                    <button
                      key={i}
                      type="button"
                      onClick={() => sendMessage(s)}
                      className="text-[11px] px-2.5 py-1.5 rounded-full border border-border/70 text-muted-foreground hover:text-foreground hover:border-primary/40 transition-colors text-left"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Context toolbar */}
            {activeThreadId && (
              <div className="shrink-0 px-3 pb-1">
                <div className="max-w-4xl mx-auto flex items-center gap-2 text-[11px] text-muted-foreground">
                  {usage && (
                    <span className="font-mono" title="Tokens used in this conversation">
                      {usage.totalTokens > 0 ? `${(usage.totalTokens / 1000).toFixed(1)}k tokens` : "fresh context"}
                      {usage.contextPercent !== null ? ` • ${usage.contextPercent}% of window` : ""}
                    </span>
                  )}
                  <span className="flex-1" />
                  <button type="button" onClick={handleCompact} className="inline-flex items-center gap-1 hover:text-foreground px-1.5 py-1 rounded-lg hover:bg-muted" title="Summarize older messages to free context">
                    <Shrink className="size-3.5" /> Compact context
                  </button>
                  <button
                    type="button"
                    onClick={() => setPlanMode((v) => !v)}
                    title={planMode ? "Plan mode ON: the agent plans complex work with checklists before acting" : "Turn on plan mode for careful multi-step work"}
                    className={`inline-flex items-center gap-1 px-1.5 py-1 rounded-lg hover:bg-muted ${planMode ? "text-primary font-semibold" : ""}`}
                    aria-pressed={planMode}
                  >
                    <ClipboardList className="size-3.5" /> Plan {planMode ? "on" : "off"}
                  </button>
                  {/* This toggle is a per-session client switch, not the
                      server setting. The server owns the capability, so when it
                      reports "off" the control is disabled with the reason
                      rather than letting the operator flip a switch that cannot
                      do anything; when the read failed it says "unknown"
                      instead of implying "off". */}
                  <button
                    type="button"
                    onClick={() => setSuggestionsOn((v) => !v)}
                    disabled={suggestionsServerState !== true}
                    aria-pressed={suggestionsOn}
                    className="hover:text-foreground px-1.5 py-1 rounded-lg hover:bg-muted disabled:opacity-60 disabled:cursor-not-allowed"
                    title={
                      suggestionsServerState === false
                        ? "Follow-up suggestions are disabled by the Gateway (suggestions.enabled in config.yaml)."
                        : suggestionsServerState === null
                          ? "The Gateway did not report whether suggestions are enabled."
                          : "Toggle follow-up question suggestions for this session"
                    }
                  >
                    Suggestions{" "}
                    {suggestionsServerState === null
                      ? "unknown"
                      : suggestionsServerState === false
                        ? "off (server)"
                        : suggestionsOn
                          ? "on"
                          : "off"}
                  </button>
                </div>
              </div>
            )}

            {/* Composer */}
            {/* On an iPhone the home indicator sits over the bottom ~34px, and a
            fixed `pb-3` put the composer's send button underneath it. The
            inset is read from the viewport so the bar clears the indicator
            without padding it on devices that have none. */}
        <footer className="shrink-0 pb-[calc(0.75rem+env(safe-area-inset-bottom))]">
              <Composer
                botDisplayName={activeBot ? activeBot.display_name || activeBot.name : undefined}
                input={input}
                setInput={setInput}
                onSubmit={handleSubmit}
                onStop={handleStop}
                isLoading={isLoading}
                models={models}
                selectedModel={selectedModel}
                onSelectModel={(m) => {
                  setSelectedModel((current) => {
                    if (m !== current) {
                      setReasoningEffort((effort) => reconcileEffortForModel(effort, models.find((x) => x.id === m) ?? null));
                    }
                    return m;
                  });
                  // Persist so the choice survives a reload. The Settings path
                  // already wrote this key, but a composer-only switch used to
                  // be session-scoped and silently reverted on the next load.
                  try {
                    localStorage.setItem("alpha_selected_model", m);
                  } catch {
                    /* a full or blocked store must not fail the selection */
                  }
                }}
                effort={reasoningEffort}
                onEffortChange={(next) => {
                  setReasoningEffort(next);
                  try {
                    if (next === DEFAULT_EFFORT) localStorage.removeItem("alpha_reasoning_effort");
                    else localStorage.setItem("alpha_reasoning_effort", next);
                  } catch {}
                }}
                effortLadder={effortLadder}
                effortLabels={effortLabels}
                onPolish={handlePolish}
                polishing={polishing}
                onAttach={handleAttach}
                uploading={uploading}
                onPasteFiles={(files) => handleAttach(files)}
                onDictate={(text) => flash("Dictation inserted — review and send.")}
                onVoiceTranscript={handleVoiceTranscript}
                voiceConversationEnabled={voiceConversationEnabled}
                voiceConversationScopeKey={`${view}:${activeThreadId || "new"}`}
                voiceConversationTurnActive={voiceTurnActive}
                voiceResumeToken={voiceResumeToken}
                onVoiceConversationStateChange={(active) => {
                  voiceConversationEnabledRef.current = active;
                  setVoiceConversationEnabled(active);
                  if (!active) cancelSpeech();
                }}
                freeNote={freeNote}
                onRefreshFree={refreshFreeCatalog}
                refreshingFree={freeRefreshing}
                onOpenModelSettings={() => {
                  setSettingsInitialTab("models");
                  setView("settings");
                }}
                onModelsUpdated={async () => {
                  const mList = await fetchAvailableModels();
                  setModels(mList);
                  flash("Models updated with new API key configuration.");
                }}
                slashCommands={slashCommands}
              />
            </footer>
          </div>
        )}

        {/* Right-Hand Project Inspector Drawer */}
        {inspectorOpen && (
          <aside className="w-80 shrink-0 h-full hidden lg:block overflow-hidden">
            <ProjectDetailPanel
              project={activeProject}
              activeBot={activeBot}
              projects={projects}
              onOpenView={(target) => setView(target)}
              onOpenThread={(id) => openThread(id)}
              onNewConversationInProject={() => {
                handleNewChat();
                if (activeProjectId) void handlePickProject(activeProjectId);
              }}
              onClose={() => setInspectorOpen(false)}
              onPickProject={(id) => void handlePickProject(id)}
              onNewProject={() => setView("projects")}
            />
          </aside>
        )}
      </div>
    )}
        </ErrorBoundary>
      </main>
      </div>

      <LionPet
        state={lionState}
        message={lionMessage || undefined}
        onOpenChat={() => setView("chat")}
      />

      <BotDetailPanel
        bot={inspectedBot}
        onClose={() => setInspectedBot(null)}
        onChat={handleChatWithBot}
        onProjectCreated={async (projectId) => {
          setView("projects");
          try {
            setProjects(await listProjects());
          } catch (error) {
            flash(`Project ${projectId} was created, but the project list could not refresh. ${errMsg(error)}`);
            return;
          }
          flash(`Project created with ${inspectedBot?.display_name || inspectedBot?.name || "the bot"} as lead (${projectId}).`);
        }}
      />
    </div>
  );
}
