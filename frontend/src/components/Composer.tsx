"use client";

import React, { useRef, useEffect, useState, useMemo, useCallback } from "react";
import {
  Send,
  Square,
  Wand2,
  Paperclip,
  Terminal,
  ChevronRight,
  Zap,
  Settings,
  Key,
  ExternalLink,
  Check,
  X,
  Image as ImageIcon,
  Code2,
  Network,
  AtSign,
  Users,
  ArrowLeftRight,
} from "lucide-react";
import { AIModel, SlashCommandInfo } from "@/types/chat";
import {
  fetchCommands,
  BUILTIN_FREE_MODELS,
  configureProviderCredentials,
} from "@/lib/api";
import { errMsg } from "@/lib/http";
import { ReasoningEffortPicker } from "@/components/ReasoningEffortPicker";
import { VoiceControls } from "@/components/VoiceControls";
import { SlashCommand } from "@/lib/commands";
import { branding } from "@/lib/branding";
import {
  DEFAULT_EFFORT,
  FALLBACK_LABELS,
  FALLBACK_LADDER,
  type EffortChoice,
} from "@/lib/reasoning-effort";
import {
  formatContextWindow,
  modelCapabilities,
} from "@/lib/model-capabilities";
import {
  applyMention,
  buildMentionRows,
  countMentionTokens,
  departmentRoleIndex,
  filterMentionRows,
  findMentionAtCaret,
  parseMentions,
  rosterHandles,
  type MentionAgent,
  type MentionRow,
} from "@/lib/agent-mentions";
import {
  buildSlashCommandPalette,
  describePalette,
  type PaletteCommand,
} from "@/lib/slash-command-palette";

const DEFAULT_CORE_COMMANDS: SlashCommandInfo[] = [
  {
    command: "/goal",
    category: "mission",
    description: "Define and orchestrate autonomous goals",
    usage: "/goal <objective>",
    is_core: true,
    is_autonomous_trigger: true,
    requires_approval: false,
  },
  {
    command: "/plan",
    category: "planning",
    description: "Compile 8-dimensional strategic meta-plan",
    usage: "/plan <prompt>",
    is_core: true,
    is_autonomous_trigger: true,
    requires_approval: false,
  },
  {
    command: "/swarm",
    category: "swarm",
    description: "Orchestrate multi-agent specialized swarms",
    usage: "/swarm create <name>",
    is_core: true,
    is_autonomous_trigger: true,
    requires_approval: false,
  },
  {
    command: "/agent",
    category: "agent",
    description: "Spawn, inspect, or manage autonomous agents",
    usage: "/agent spawn <role>",
    is_core: true,
    is_autonomous_trigger: true,
    requires_approval: false,
  },
  {
    command: "/research",
    category: "research",
    description: "Deep multi-stage web and codebase research",
    usage: "/research <query>",
    is_core: true,
    is_autonomous_trigger: true,
    requires_approval: false,
  },
  {
    command: "/code",
    category: "coding",
    description: "Inspect, write, and refactor code modules",
    usage: "/code <task>",
    is_core: true,
    is_autonomous_trigger: false,
    requires_approval: false,
  },
  {
    command: "/memory",
    category: "memory",
    description: "Query and store episodic and semantic memory",
    usage: "/memory query <key>",
    is_core: true,
    is_autonomous_trigger: false,
    requires_approval: false,
  },
  {
    command: "/context",
    category: "context",
    description: "Inspect context tokens, budget, and prune",
    usage: "/context inspect",
    is_core: true,
    is_autonomous_trigger: false,
    requires_approval: false,
  },
  {
    command: "/skills",
    category: "skills",
    description: "Manage agent procedural skills and extensions",
    usage: "/skills list",
    is_core: true,
    is_autonomous_trigger: false,
    requires_approval: false,
  },
  {
    command: "/model",
    category: "model",
    description: "Inspect or switch active LLM reasoning model",
    usage: "/model switch <model_id>",
    is_core: true,
    is_autonomous_trigger: false,
    requires_approval: false,
  },
  {
    command: "/tools",
    category: "tools",
    description: "List and execute agentic tool calls",
    usage: "/tools list",
    is_core: true,
    is_autonomous_trigger: false,
    requires_approval: false,
  },
  {
    command: "/mcp",
    category: "tools",
    description: "Model Context Protocol servers and resources",
    usage: "/mcp list",
    is_core: true,
    is_autonomous_trigger: false,
    requires_approval: false,
  },
  {
    command: "/verify",
    category: "verification",
    description: "Run automated tests, linters, and invariants",
    usage: "/verify all",
    is_core: true,
    is_autonomous_trigger: false,
    requires_approval: false,
  },
  {
    command: "/browser",
    category: "browser",
    description: "Launch and inspect headless browser sessions",
    usage: "/browser open <url>",
    is_core: true,
    is_autonomous_trigger: true,
    requires_approval: false,
  },
  {
    command: "/learn",
    category: "rsi",
    description: "Extract and store operational learnings",
    usage: "/learn save",
    is_core: true,
    is_autonomous_trigger: false,
    requires_approval: false,
  },
  {
    command: "/session",
    category: "session",
    description: "Manage thread history, checkpoints, and rollback",
    usage: "/session rollback",
    is_core: true,
    is_autonomous_trigger: false,
    requires_approval: false,
  },
];

interface ComposerProps {
  input: string;
  setInput: React.Dispatch<React.SetStateAction<string>>;
  onSubmit: () => void;
  onStop?: () => void;
  isLoading: boolean;
  models: AIModel[];
  selectedModel: string;
  onSelectModel: (modelId: string) => void;
  /** Polish the draft with AI before sending. */
  onPolish?: () => void;
  polishing?: boolean;
  /** Attach files to the active conversation. */
  onAttach?: (files: FileList) => void;
  uploading?: boolean;
  /** Pasted/dropped images land here so the host can upload them. */
  onPasteFiles?: (files: File[]) => void;
  /** Mic dictation: host transcribes and inserts text at the cursor. */
  onDictate?: (text: string) => void;
  dictating?: boolean;
  /** Final hands-free transcript callback lifted to the chat host. */
  onVoiceTranscript?: (text: string) => void;
  /** Parent-owned conversation controls keep voice bound to this thread/view. */
  voiceConversationEnabled?: boolean;
  voiceConversationScopeKey?: string;
  voiceConversationTurnActive?: boolean;
  voiceResumeToken?: number;
  onVoiceConversationStateChange?: (active: boolean) => void;
  /** Honest free-model status line shown under the selector tooltip. */
  freeNote?: string | null;
  /** Live probe and refresh of free models */
  onRefreshFree?: () => Promise<void> | void;
  refreshingFree?: boolean;
  /** Redirect to the full Model & API Key Configuration Page in Settings */
  onOpenModelSettings?: () => void;
  /** Callback when models/keys are updated inline */
  onModelsUpdated?: () => Promise<void> | void;
  /** All shortcut commands (for the "/" palette). */
  slashCommands?: SlashCommand[];
  /**
   * Roster rows the `@` tag palette offers (`GET /api/bots`).
   *
   * Deliberately distinct from `slashCommands`: a command is a verb the server
   * executes, a tag is a recipient the server's mention grammar resolves. They
   * come from different surfaces and are never merged into one list.
   */
  mentionAgents?: MentionAgent[];
  /**
   * Whether the roster behind `mentionAgents` is readable.
   *
   * `unavailable` is a real state and renders as such. A failed roster read must
   * never answer "no agents available", which claims the fleet is empty — a
   * claim about the workspace that nothing measured.
   */
  mentionAgentsState?: "loading" | "ready" | "unavailable";
  /** The server's own reason when `mentionAgentsState` is `unavailable`. */
  mentionAgentsError?: string | null;
  /** Handle of the agent currently driving this chat, so it is not offered again. */
  activeAgentHandle?: string | null;
  /**
   * Make `handle` the agent this conversation runs on.
   *
   * Omit this and the "bot mode" rows disappear entirely. A switch control with
   * no handler could only ever be a no-op, and a control that can never succeed
   * is not a control.
   */
  onMentionSwitchAgent?: (handle: string) => void;
  /**
   * Selected reasoning effort. Omit `onEffortChange` to hide the picker
   * entirely — a read-only surface should not show a control it cannot drive.
   */
  effort?: EffortChoice;
  onEffortChange?: (effort: EffortChoice) => void;
  /**
   * Whether this message may delegate work to background subagents.
   *
   * Sent as the server's `autonomous` run flag, which is what applies
   * `subagent_enabled` and puts the `task` tool in the toolset. Defaults off:
   * delegation spends tokens and starts workers, so it is opt-in and the control
   * renders its own state rather than implying a capability that is not active.
   */
  delegationEnabled?: boolean;
  onDelegationChange?: (enabled: boolean) => void;
  /** Canonical ladder + labels from `GET /api/models` (weakest → strongest). */
  effortLadder?: readonly string[];
  effortLabels?: Readonly<Record<string, string>>;
  /** Name of the current active bot for the placeholder */
  botDisplayName?: string;
}

export function Composer({
  input,
  setInput,
  onSubmit,
  onStop,
  isLoading,
  botDisplayName,
  models,
  selectedModel,
  onSelectModel,
  onPolish,
  polishing,
  onAttach,
  uploading,
  onPasteFiles,
  onDictate,
  dictating,
  onVoiceTranscript,
  voiceConversationEnabled,
  voiceConversationScopeKey,
  voiceConversationTurnActive,
  voiceResumeToken,
  onVoiceConversationStateChange,
  freeNote,
  onRefreshFree,
  refreshingFree,
  onOpenModelSettings,
  onModelsUpdated,
  slashCommands,
  mentionAgents = [],
  mentionAgentsState = "ready",
  mentionAgentsError = null,
  activeAgentHandle = null,
  onMentionSwitchAgent,
  effort = DEFAULT_EFFORT,
  onEffortChange,
  delegationEnabled = false,
  onDelegationChange,
  effortLadder = FALLBACK_LADDER,
  effortLabels = FALLBACK_LABELS,
}: ComposerProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  /**
   * The scroll container holding the palette rows.
   *
   * The list is no longer eight rows, so keyboard navigation can move the
   * highlight past the visible window; without scrolling it into view the
   * selected row exists somewhere off-screen and the palette looks stuck.
   */
  const paletteListRef = useRef<HTMLDivElement>(null);
  /**
   * The command token just completed by picking a row.
   *
   * `selectCommand` writes `/<command> ` back into the draft, and the reset
   * effect below fires on every input change — so without this the palette
   * would reopen the instant a row was chosen, showing the very row that was
   * just picked. Recording what was completed lets the effect tell "the
   * operator typed more" (reopen) from "we filled it in" (stay closed).
   */
  const completedTokenRef = useRef<string | null>(null);
  const [availableCommands, setAvailableCommands] = useState<
    SlashCommandInfo[]
  >(DEFAULT_CORE_COMMANDS);
  const [registryError, setRegistryError] = useState<string | null>(null);
  const [selectedIndex, setSelectedIndex] = useState<number>(0);
  const [isDismissed, setIsDismissed] = useState<boolean>(false);
  /**
   * The `@` picker's own selection and dismissal.
   *
   * Separate from the `/` palette's `selectedIndex` because one shared index
   * would highlight the wrong row in whichever palette happened to be open. The
   * two can never be open together — `/` only matches while the value starts
   * with it and holds no space, so `/goal ship @bo` has already closed it.
   */
  const [mentionIndex, setMentionIndex] = useState<number>(0);
  const [mentionDismissed, setMentionDismissed] = useState<boolean>(false);
  /**
   * Caret offset, tracked explicitly.
   *
   * `@` opens mid-sentence, so the palette cannot be derived from the whole
   * value the way `/` is — it must know where the caret actually is. A
   * controlled `<textarea>` keeps its caret in the DOM, which a render cannot
   * read, so every event that can move it reports it here.
   */
  const [caret, setCaret] = useState<number>(0);

  // Quick API Key configuration popover state
  const [showKeyPopover, setShowKeyPopover] = useState(false);
  const [quickProvider, setQuickProvider] = useState("gemini");
  const [quickApiKey, setQuickApiKey] = useState("");
  const [savingKey, setSavingKey] = useState(false);
  const [keyStatusMsg, setKeyStatusMsg] = useState<string | null>(null);

  // Load registered backend commands on mount, then merge the prop list.
  // A failed registry read keeps the local defaults (so the palette still works)
  // but is disclosed: a backend whose command registry could not be read must
  // not look identical to one that simply has no custom commands.
  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const cmds = await fetchCommands();
        if (cancelled) return;
        if (cmds && cmds.length > 0) {
          setAvailableCommands(cmds);
          setRegistryError(null);
        }
      } catch (err) {
        if (cancelled) return;
        setRegistryError(
          `Backend command registry unavailable — ${errMsg(err)}`,
        );
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  // Unified command source: backend registry wins, prop shortcuts fill gaps.
  const mergedCommands = useMemo(() => {
    const seen = new Set(availableCommands.map((c) => c.command.toLowerCase()));
    const extra: SlashCommandInfo[] = (slashCommands || [])
      .filter((c) => !seen.has(`/${c.name}`.toLowerCase()))
      .map((c) => ({
        command: `/${c.name}`,
        category: c.category || "general",
        description: c.description || "Run this shortcut",
        usage: c.usage || `/${c.name}`,
        is_core: false,
        is_autonomous_trigger: false,
        requires_approval: false,
      }));
    return [...availableCommands, ...extra];
  }, [availableCommands, slashCommands]);

  // Filter slash commands
  //
  // Every command the registry holds for the token being typed — no cap. The
  // old `slice(0, 8)` showed eight of ~125 with nothing saying so, and closing
  // on the first space made every multi-word subcommand (`/agent ask`,
  // `/verify deep`) unreachable. The bound now lives in the renderer's scroll
  // container, which can disclose what it hid.
  const palette = useMemo(
    () => buildSlashCommandPalette(mergedCommands, input),
    [mergedCommands, input],
  );
  const suggestions = useMemo(
    () => (isDismissed ? [] : palette.rows),
    [isDismissed, palette.rows],
  );

  // ── `@` tag palette ────────────────────────────────────────────────────────
  //
  // Three separate derivations, because they answer three different questions
  // and each has its own honest failure state:
  //
  //   rosterRows — what CAN be tagged        (a failed read answers nothing)
  //   trigger    — is the caret inside `@…`  (pure caret geometry)
  //   filtered   — what should be offered    (bounded, and says what it hid)

  /** Every tag this roster supports, in pick order. */
  const rosterRows = useMemo(
    () =>
      buildMentionRows({
        agents: mentionAgents,
        activeHandle: activeAgentHandle,
        allowSwitch: typeof onMentionSwitchAgent === "function",
      }),
    [mentionAgents, activeAgentHandle, onMentionSwitchAgent],
  );

  /** The `@` token the caret is in, or null. */
  const mentionTrigger = useMemo(() => {
    if (mentionDismissed) return null;
    return findMentionAtCaret(input, caret);
  }, [input, caret, mentionDismissed]);

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
    if (countMentionTokens(input) === 0) return null;
    return parseMentions(input, rosterHandles(mentionAgents), departmentRoleIndex(mentionAgents));
  }, [input, mentionAgents]);

  /**
   * The palette header's one-line state.
   *
   * Three states, and the third is the one a picker usually gets wrong: a
   * failed roster read must say so. Rendering "no agents available" after a read
   * that failed is a claim about the fleet that nothing measured.
   */
  const mentionHeaderNote = (() => {
    if (mentionAgentsState === "loading") return "Reading the agent roster…";
    if (mentionAgentsState === "unavailable") {
      return mentionAgentsError
        ? `Agent roster unavailable — ${mentionAgentsError}. Nothing can be tagged from this list.`
        : "Agent roster unavailable — nothing can be tagged from this list.";
    }
    return null;
  })();

  /** What an empty result means, which differs by roster state and query. */
  const mentionEmptyNote = (() => {
    if (mentionHeaderNote) return null;
    if (mentionAgents.length === 0) {
      return "The agent roster is empty, so there is nothing to tag. Add a bot in the Bots view first.";
    }
    return `No agent matches ${mentionTrigger?.kind ? `@${mentionTrigger.kind}:` : ""}${mentionTrigger?.query || "—"} exactly. A tag must name a real handle; use @role: for a whole department.`;
  })();

  const { standardModels, keylessModels, quotaModels } = useMemo(() => {
    const effectiveModels = models.length > 0 ? models : BUILTIN_FREE_MODELS;
    const standard: AIModel[] = [];
    const keyless: AIModel[] = [];
    const quota: AIModel[] = [];

    for (const m of effectiveModels) {
      if (m.is_free || m.id.startsWith("free:") || m.id === "alpha-free") {
        if (
          m.free_status === "no_key_free" ||
          m.quota_type === "keyless_free" ||
          m.quota_type === "keyless" ||
          m.id.startsWith("free:ovhcloud") ||
          m.id.startsWith("free:pollinations") ||
          m.id.startsWith("free:vireonix") ||
          m.id.startsWith("free:llm7") ||
          m.id.startsWith("free:cehpoint") ||
          m.id.startsWith("free:aihorde") ||
          m.id === "alpha-free"
        ) {
          keyless.push(m);
        } else {
          quota.push(m);
        }
      } else {
        standard.push(m);
      }
    }
    return {
      standardModels: standard,
      keylessModels: keyless,
      quotaModels: quota,
    };
  }, [models]);

  /**
   * A compact capability hint appended to an option's label: image input,
   * tools, and reasoning. A native `<option>` cannot render chips, so the
   * signal has to ride in the text — and it is derived from the same tri-state
   * resolver the Settings cards use, so a model nobody measured about shows
   * nothing rather than a crossed-out "no".
   */
  const optionLabel = (m: AIModel): string => {
    const c = modelCapabilities(m, null);
    const marks: string[] = [];
    if (c.vision === true) marks.push("👁");
    if (c.tools === true) marks.push("🔧");
    if (c.reasoningEffort === true) marks.push("🧠");
    return marks.length ? `${m.name} ${marks.join("")}` : m.name;
  };

  /** Tooltip carrying the full detail, including "not reported" states. */
  const modelOptionTitle = (m: AIModel): string => {
    const c = modelCapabilities(m, null);
    const parts: string[] = [m.name];
    const label = (state: boolean | null, yes: string, no: string) =>
      state === null ? `${yes}: not reported` : `${yes}: ${state ? "yes" : no}`;
    parts.push(label(c.vision, "image input", "no"));
    parts.push(label(c.tools, "tools", "no"));
    parts.push(label(c.reasoningEffort, "reasoning effort", "no"));
    const window = formatContextWindow(c.contextWindow);
    if (window) parts.push(`context: ${window} tokens`);
    return parts.join(" · ");
  };

  useEffect(() => {
    // Reopen on a token we did not just fill in ourselves. A bare `/` clears
    // the record, because from an empty token every command is reachable again
    // and a stale completion must not silence the whole palette.
    if (input.startsWith("/") && palette.prefix !== completedTokenRef.current) {
      setIsDismissed(false);
    }
    setSelectedIndex(0);
  }, [input, palette.prefix]);

  /**
   * Re-open the `@` picker whenever the caret re-enters a token.
   *
   * A sticky dismissal would mean one `Escape` silenced `@` for the rest of the
   * draft — and an `Escape` pressed at the end of a sentence, with no palette
   * open, would disarm a token the operator had not finished typing.
   */
  useEffect(() => {
    if (findMentionAtCaret(input, caret)) {
      setMentionDismissed(false);
    }
    setMentionIndex(0);
  }, [input, caret]);

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 200)}px`;
    }
  }, [input]);

  const selectCommand = (cmd: PaletteCommand) => {
    setInput(`${cmd.command} `);
    completedTokenRef.current = cmd.command;
    setIsDismissed(true);
    textareaRef.current?.focus();
  };

  /** Put the caret where `applyMention` says it belongs, after React commits. */
  const placeCaret = (position: number) => {
    setCaret(position);
    // The value has not rendered yet inside this event, so setting
    // `selectionStart` synchronously would be overwritten by the commit.
    window.setTimeout(() => {
      const node = textareaRef.current;
      if (!node) return;
      node.focus();
      try {
        node.setSelectionRange(position, position);
      } catch {
        /* a detached textarea is not worth failing the insertion over */
      }
    }, 0);
  };

  /**
   * Commit one tag.
   *
   * The switch mode is a separate, deliberate effect rather than a silent side
   * effect of insertion: tagging an agent and re-pointing the whole conversation
   * at it are different decisions, so the picker offers them as different rows.
   * Both write the identical token, which means a switch that failed to apply
   * leaves a tag the operator can still see and correct.
   */
  const selectMention = useCallback(
    (row: MentionRow) => {
      if (row.refusal) return;
      const trigger = findMentionAtCaret(input, caret);
      if (!trigger) return;
      const applied = applyMention(input, trigger, row);
      setInput(applied.text);
      setMentionDismissed(true);
      if (row.mode === "switch" && row.switchHandle && onMentionSwitchAgent) {
        onMentionSwitchAgent(row.switchHandle);
      }
      placeCaret(applied.caret);
    },
    [input, caret, onMentionSwitchAgent],
  );

  /**
   * Keep the highlighted row inside the visible window.
   *
   * Only runs on keyboard navigation: `block: "nearest"` is a no-op when the row
   * is already on screen, so this cannot yank the list around while the
   * operator is typing a filter.
   */
  useEffect(() => {
    if (suggestions.length === 0) return;
    const node = paletteListRef.current?.querySelector<HTMLElement>(
      `[data-palette-index="${selectedIndex}"]`,
    );
    node?.scrollIntoView({ block: "nearest" });
  }, [selectedIndex, suggestions]);

  // Paste images/files straight from the clipboard (screenshots, copied files).
  const handlePaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const files = Array.from(e.clipboardData?.files || []).filter(
      (f) => f.type.startsWith("image/") || f.size > 0,
    );
    if (files.length > 0 && onPasteFiles) {
      e.preventDefault();
      onPasteFiles(files);
    }
    // Plain text + URLs paste normally into the textarea.
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    setCaret(e.currentTarget.selectionStart ?? input.length);

    // The `@` palette is checked first because it is the one that can be open
    // mid-sentence. A value holding both can only be a `/` command with a space
    // in it, which already closed the `/` palette, so this order never has to
    // choose between two open palettes.
    if (mentionRows.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setMentionIndex((prev) => (prev + 1) % mentionRows.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setMentionIndex((prev) => (prev - 1 + mentionRows.length) % mentionRows.length);
        return;
      }
      if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) {
        e.preventDefault();
        const row = mentionRows[mentionIndex];
        if (row && !row.refusal) selectMention(row);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setMentionDismissed(true);
        return;
      }
    }

    if (suggestions.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIndex((prev) => (prev + 1) % suggestions.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedIndex(
          (prev) => (prev - 1 + suggestions.length) % suggestions.length,
        );
        return;
      }
      if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) {
        e.preventDefault();
        // The list is re-derived from the draft on every keystroke, so a stale
        // index must not throw mid-draft and swallow the operator's Enter.
        const row = suggestions[selectedIndex];
        if (row) selectCommand(row);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setIsDismissed(true);
        return;
      }
    }

    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!isLoading && input.trim()) {
        onSubmit();
      }
    }
  };

  return (
    <div className="w-full max-w-4xl mx-auto p-3 relative">
      {/* `@` Agent Tag Palette.

          Opens mid-sentence, which `/` cannot, so it derives from the caret
          rather than from the start of the value. It renders its own honest
          states — loading, unreadable roster, no matches, a disclosed hidden
          count, and a refused row carrying the reason it cannot be used. A
          picker that quietly shows nothing is indistinguishable from a fleet
          with no agents. */}
      {mentionTrigger && (
        <div className="absolute bottom-full mb-2 left-3 right-3 bg-popover/95 backdrop-blur-md border border-border rounded-xl elev-3 overflow-hidden z-50 animate-in fade-in slide-in-from-bottom-2 dur-fast">
          <div className="flex items-center justify-between px-3 py-1.5 border-b border-border/60 bg-muted/40 text-[11px] font-medium text-muted-foreground">
            <div className="flex items-center gap-1.5">
              <AtSign className="size-3.5 text-primary" />
              <span>Tag an AI agent</span>
            </div>
            <span>↑↓ to navigate • Tab or Enter to tag • Esc to dismiss</span>
          </div>

          {mentionHeaderNote && (
            <div className="px-3 py-2 border-b border-border/60 bg-muted/30 text-[11px] text-muted-foreground">
              {mentionHeaderNote}
            </div>
          )}

          {mentionEmptyNote && (
            <div className="px-3 py-3 border-b border-border/60 bg-muted/30 text-[11px] text-muted-foreground">
              {mentionEmptyNote}
            </div>
          )}

          <div className="max-h-64 overflow-y-auto p-1">
            {mentionSections.map((section) => (
              <div key={section.id} className="mb-1 last:mb-0">
                <div className="flex items-center gap-1.5 px-2 pt-1.5 pb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/70">
                  {section.icon}
                  <span>{section.label}</span>
                </div>
                {section.rows.map(({ row, index }) => {
                  const disabled = row.refusal !== null;
                  return (
                    <button
                      key={row.id}
                      type="button"
                      disabled={disabled}
                      onClick={() => selectMention(row)}
                      title={row.refusal || `Insert ${row.token}`}
                      aria-selected={index === mentionIndex}
                      className={`w-full text-left px-2.5 py-1.5 rounded-lg flex items-center justify-between gap-2 transition-colors ${
                        disabled
                          ? "opacity-55 cursor-not-allowed"
                          : index === mentionIndex
                            ? "bg-accent text-accent-foreground"
                            : "hover:bg-muted/60"
                      }`}
                    >
                      <div className="flex items-center gap-2.5 min-w-0">
                        <span className="size-6 shrink-0 rounded-full bg-muted/70 text-[10px] font-semibold flex items-center justify-center">
                          {(row.avatar || row.title || "?").trim().slice(0, 1).toUpperCase() || "?"}
                        </span>
                        <div className="min-w-0">
                          <div className="flex items-center gap-1.5 min-w-0">
                            <span className="text-xs font-medium truncate">{row.title}</span>
                            {row.status && row.status !== "active" && (
                              <span className="text-[9px] uppercase tracking-wider px-1 py-px rounded bg-muted/80 text-muted-foreground font-semibold shrink-0">
                                {row.status}
                              </span>
                            )}
                          </div>
                          <div className="text-[10.5px] text-muted-foreground truncate max-w-sm">
                            {row.refusal || row.subtitle}
                          </div>
                        </div>
                      </div>
                      <span
                        className={`text-[10px] font-mono px-1.5 py-0.5 rounded shrink-0 ${
                          row.mode === "switch" ? "bg-primary/15 text-primary" : "bg-muted/80 text-muted-foreground"
                        }`}
                      >
                        {row.badge}
                      </span>
                    </button>
                  );
                })}
              </div>
            ))}
          </div>

          {mentionFilter && mentionFilter.hidden > 0 && (
            <div className="px-3 py-1.5 border-t border-border/60 bg-muted/40 text-[10.5px] text-muted-foreground">
              {mentionFilter.hidden} more tag{mentionFilter.hidden === 1 ? "" : "s"} matched — keep typing to narrow the list.
            </div>
          )}
        </div>
      )}

      {/* Slash Command Suggestions Palette */}
      {suggestions.length > 0 && (
        <div className="absolute bottom-full mb-2 left-3 right-3 bg-popover/95 backdrop-blur-md border border-border rounded-xl elev-3 overflow-hidden z-50 animate-in fade-in slide-in-from-bottom-2 dur-fast">
          <div className="flex items-center justify-between px-3 py-1.5 border-b border-border/60 bg-muted/40 text-[11px] font-medium text-muted-foreground">
            <div className="flex items-center gap-1.5">
              <Terminal className="size-3.5 text-primary" />
              <span>Master Slash Commands</span>
            </div>
            <span>Use ↑↓ to navigate • Tab to select • Esc to dismiss</span>
          </div>
          {registryError && (
            <div className="px-3 py-1.5 border-b border-border/60 bg-destructive/10 text-[11px] text-destructive">
              {registryError} — showing the built-in commands only.
            </div>
          )}
          <div
            ref={paletteListRef}
            className="max-h-80 overflow-y-auto p-1 divide-y divide-border/20"
          >
            {suggestions.map((cmd, idx) => (
              <button
                key={cmd.command}
                data-palette-index={idx}
                type="button"
                onClick={() => selectCommand(cmd)}
                className={`w-full text-left px-3 py-2 rounded-lg flex items-center justify-between transition-colors ${
                  idx === selectedIndex
                    ? "bg-accent text-accent-foreground"
                    : "hover:bg-muted/60"
                }`}
              >
                <div className="flex items-center gap-2.5 min-w-0">
                  <span className="font-mono text-xs font-semibold text-primary">
                    {cmd.command}
                  </span>
                  <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-muted/80 text-muted-foreground font-semibold">
                    {cmd.category}
                  </span>
                  {cmd.hasHandler === false && (
                    <span
                      className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-destructive/15 text-destructive font-semibold shrink-0"
                      title="The registry reports no bound handler, so this row answers 'unimplemented' instead of running"
                    >
                      no handler
                    </span>
                  )}
                  <span className="text-xs text-muted-foreground truncate max-w-md">
                    {cmd.description}
                  </span>
                </div>
                <ChevronRight className="size-3.5 text-muted-foreground shrink-0 opacity-60" />
              </button>
            ))}
          </div>
          <div className="px-3 py-1.5 border-t border-border/60 bg-muted/40 text-[11px] text-muted-foreground">
            {describePalette(palette)}
          </div>
        </div>
      )}

      {showKeyPopover && (
        <div className="mb-2.5 rounded-2xl border border-primary/30 bg-card/95 backdrop-blur-md elev-3 p-3.5 space-y-3 animate-in fade-in slide-in-from-bottom-2">
          <div className="flex items-center justify-between border-b border-border/50 pb-2">
            <div className="flex items-center gap-2">
              <Key className="size-4 text-primary" />
              <span className="text-xs font-bold text-foreground">
                Quick API Key & Provider Setup
              </span>
            </div>
            <div className="flex items-center gap-1.5">
              {onOpenModelSettings && (
                <button
                  type="button"
                  onClick={() => {
                    setShowKeyPopover(false);
                    onOpenModelSettings();
                  }}
                  className="inline-flex items-center gap-1 text-[11px] font-semibold text-primary hover:underline px-2 py-0.5 rounded-md bg-primary/10"
                >
                  <Settings className="size-3" /> Full Model & Key Studio{" "}
                  <ExternalLink className="size-2.5" />
                </button>
              )}
              <button
                type="button"
                onClick={() => setShowKeyPopover(false)}
                className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted"
                aria-label="Close quick setup"
              >
                <X className="size-3.5" />
              </button>
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
            <div>
              <label className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground block mb-1">
                Provider
              </label>
              <select
                value={quickProvider}
                onChange={(e) => setQuickProvider(e.target.value)}
                className="w-full text-xs bg-muted/60 border border-border rounded-lg px-2.5 py-1.5 text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
              >
                <optgroup label="🎁 Recurring $0 Free Tier">
                  <option value="gemini">Google Gemini (15 RPM Free)</option>
                  <option value="groq">GroqCloud (14,400 RPD Free)</option>
                  <option value="sambanova">SambaNova Cloud (Free Tier)</option>
                  <option value="mistral">Mistral La Plateforme (Free)</option>
                  <option value="cohere">
                    Cohere Coral (1,000 req/mo Free)
                  </option>
                  <option value="cloudflare">
                    Cloudflare Workers AI (Free)
                  </option>
                </optgroup>
                <optgroup label="🌐 Free-Model Gateways">
                  <option value="openrouter">
                    OpenRouter (:free & Union Alpha)
                  </option>
                </optgroup>
                <optgroup label="🎟️ Free Trial Credits">
                  <option value="nvidia">NVIDIA NIM (1,000 Credits)</option>
                  <option value="cerebras">Cerebras (1M Tokens/day)</option>
                </optgroup>
                <optgroup label="🚀 Commercial Frontier">
                  <option value="openai">OpenAI (GPT-4o / o3-mini)</option>
                  <option value="anthropic">
                    Anthropic (Claude 3.7 Sonnet)
                  </option>
                  <option value="deepseek">DeepSeek Official (V3 & R1)</option>
                </optgroup>
              </select>
            </div>
            <div className="sm:col-span-2">
              <label className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground block mb-1">
                API Key
              </label>
              <div className="flex items-center gap-2">
                <input
                  type="password"
                  value={quickApiKey}
                  onChange={(e) => setQuickApiKey(e.target.value)}
                  placeholder={`Paste your ${quickProvider.toUpperCase()} API key...`}
                  className="flex-1 text-xs bg-muted/60 border border-border rounded-lg px-2.5 py-1.5 text-foreground font-mono focus:outline-none focus:ring-1 focus:ring-primary"
                />
                <button
                  type="button"
                  disabled={!quickApiKey.trim() || savingKey}
                  onClick={async () => {
                    setSavingKey(true);
                    setKeyStatusMsg(null);
                    try {
                      await configureProviderCredentials({
                        provider: quickProvider,
                        api_key: quickApiKey.trim(),
                      });
                      setQuickApiKey("");
                      setKeyStatusMsg(
                        `✅ Saved & unlocked ${quickProvider.toUpperCase()} models!`,
                      );
                      if (onModelsUpdated) await onModelsUpdated();
                    } catch (err: any) {
                      setKeyStatusMsg(
                        `❌ ${err?.message || "Failed to save key"}`,
                      );
                    } finally {
                      setSavingKey(false);
                    }
                  }}
                  className="px-3 py-1.5 rounded-lg bg-primary text-primary-foreground text-xs font-semibold hover:opacity-95 disabled:opacity-40 shrink-0 flex items-center gap-1"
                >
                  <Check className="size-3.5" />{" "}
                  {savingKey ? "Saving…" : "Save Key"}
                </button>
              </div>
            </div>
          </div>
          {keyStatusMsg && (
            <div className="text-[11px] font-medium text-primary flex items-center justify-between pt-1">
              <span>{keyStatusMsg}</span>
              {onOpenModelSettings && (
                <button
                  type="button"
                  onClick={() => {
                    setShowKeyPopover(false);
                    onOpenModelSettings();
                  }}
                  className="underline hover:opacity-80"
                >
                  View all models in Settings →
                </button>
              )}
            </div>
          )}
        </div>
      )}

      {/* The lion companion reads this rect to stay off the composer. Without a
          marked keep-out region the pet's own hit area (`pointer-events: auto`)
          can land on the input and swallow clicks aimed at it.

          `data-dense-controls` adds the invisible 24px hit-area floor to the
          toolbar's icon buttons, which sit at `p-1.5` around a 14px glyph —
          a 22x22 target, under the WCAG 2.5.8 minimum. `elev-2` replaces the
          ad-hoc `elev-1` so the composer sits on the documented elevation
          ladder with the sticky header and the popovers. */}
      <div
        data-lion-pet-keepout=""
        data-dense-controls=""
        className="rounded-2xl border border-border bg-card elev-2 focus-within:ring-1 focus-within:ring-primary/40 focus-within:border-primary/50 transition-all p-2.5"
      >
        <textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => {
            setInput(e.target.value);
            // Read the caret off the event, not off `input`: this fires after the
            // DOM already holds the new value, so `selectionStart` is the
            // post-insertion position rather than the stale prop.
            setCaret(e.target.selectionStart ?? e.target.value.length);
          }}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
          onClick={(e) => setCaret(e.currentTarget.selectionStart ?? 0)}
          onSelect={(e) => setCaret(e.currentTarget.selectionStart ?? 0)}
          onBlur={() => setCaret(textareaRef.current?.selectionStart ?? caret)}
          placeholder={
            botDisplayName
              ? `Ask ${botDisplayName} anything…  @ to tag an agent`
              : "Ask anything • / for commands • @ to tag an agent"
          }
          rows={1}
          aria-label="Message the agent"
          aria-describedby={tagStatus ? "composer-tag-status" : undefined}
          className="w-full resize-none bg-transparent px-3 py-2 text-sm focus:outline-none placeholder:text-muted-foreground max-h-48 text-foreground"
        />

        {/* Tag status strip.

            It renders only when the draft actually holds an `@token`, so "no
            tags yet" and "a tag that addresses nobody" never look the same. The
            dead-tag case is the one that matters: the server resolves an unknown
            handle to nothing rather than to a near match, so the message would go
            out and call nobody. Naming the reason is the only point at which the
            operator can still fix it. */}
        {tagStatus && (
          <div id="composer-tag-status" className="mx-2 mb-1.5 flex items-center gap-2 flex-wrap text-[10.5px]">
            {tagStatus.unresolved.length > 0 ? (
              <>
                <span className="inline-flex items-center gap-1 text-destructive font-medium">
                  <X className="size-3" />
                  {tagStatus.unresolved.length} tag{tagStatus.unresolved.length === 1 ? "" : "s"} address nobody
                </span>
                {tagStatus.unresolved.slice(0, 3).map((bad) => (
                  <span key={bad.raw} className="text-muted-foreground">
                    <code className="font-mono text-destructive">{bad.raw}</code>{" "}
                    <span className="text-muted-foreground/80">{bad.reason}</span>
                  </span>
                ))}
                {tagStatus.unresolved.length > 3 && (
                  <span className="text-muted-foreground/70">+{tagStatus.unresolved.length - 3} more</span>
                )}
              </>
            ) : (
              <span className="inline-flex items-center gap-1 text-muted-foreground">
                <Check className="size-3 text-primary" />
                {tagStatus.resolvedHandles.length === 1
                  ? `Tagging ${tagStatus.resolvedHandles[0]}`
                  : `Tagging ${tagStatus.resolvedHandles.length} agents`}
                {tagStatus.resolvedHandles.length > 0 && (
                  <span className="text-muted-foreground/70 font-mono">
                    {tagStatus.resolvedHandles.map((h) => `@${h}`).join(" ")}
                  </span>
                )}
              </span>
            )}
          </div>
        )}

        <div className="flex items-center justify-between pt-2 border-t border-border/40 px-2 mt-1 gap-2">
          <div className="flex items-center gap-1.5 min-w-0 flex-wrap">
            {/* Model Selector */}
            <div className="flex items-center gap-1">
              <select
                value={selectedModel}
                onChange={(e) => {
                  const val = e.target.value;
                  if (val === "__configure_models__") {
                    if (onOpenModelSettings) onOpenModelSettings();
                    return;
                  }
                  if (val === "__quick_api_key__") {
                    setShowKeyPopover(true);
                    return;
                  }
                  onSelectModel(val);
                }}
                className="text-xs bg-muted/60 border border-border/80 rounded-lg px-2.5 py-1 text-foreground focus:outline-none focus:ring-1 focus:ring-primary/40 font-medium cursor-pointer max-w-60 truncate"
                title={
                  freeNote || "Select LLM reasoning model or configure API keys"
                }
                aria-label="Language model"
              >
                <option value="default">⚡ Default (Auto-Routed)</option>
                {keylessModels.length > 0 && (
                  <optgroup label="✨ Free Models (No API Key Needed)">
                    {keylessModels.map((m) => (
                      <option
                        key={m.id}
                        value={m.id}
                        title={modelOptionTitle(m)}
                      >
                        {optionLabel(m)}
                      </option>
                    ))}
                  </optgroup>
                )}
                {quotaModels.length > 0 && (
                  <optgroup label="🎁 Free Tier / Gateway (Quota)">
                    {quotaModels.map((m) => (
                      <option
                        key={m.id}
                        value={m.id}
                        title={modelOptionTitle(m)}
                      >
                        {optionLabel(m)}
                      </option>
                    ))}
                  </optgroup>
                )}
                {standardModels.length > 0 && (
                  <optgroup label="🚀 Standard & Custom Models">
                    {standardModels.map((m) => (
                      <option
                        key={m.id}
                        value={m.id}
                        title={modelOptionTitle(m)}
                      >
                        {optionLabel(m)}
                      </option>
                    ))}
                  </optgroup>
                )}
                <optgroup label="⚙️ Configure API Keys & Models">
                  <option value="__quick_api_key__">
                    🔑 Quick Set API Key (Gemini / Groq / OpenRouter)…
                  </option>
                  <option value="__configure_models__">
                    ⚙️ Open Model & API Key Configuration Page…
                  </option>
                </optgroup>
              </select>
              {onRefreshFree && (
                <button
                  type="button"
                  onClick={onRefreshFree}
                  disabled={refreshingFree}
                  className="p-1.5 rounded-lg text-amber-500 hover:text-amber-400 hover:bg-muted transition-colors disabled:opacity-40"
                  title="⚡ Find & Probe Today's Free Models (Live health probe and catalog refresh)"
                  aria-label="Find and probe today's free models"
                >
                  <Zap
                    className={`size-3.5 ${refreshingFree ? "animate-spin text-amber-400" : ""}`}
                  />
                </button>
              )}
              <button
                type="button"
                onClick={() => setShowKeyPopover((v) => !v)}
                className={`p-1.5 rounded-lg transition-colors ${
                  showKeyPopover
                    ? "bg-primary/15 text-primary"
                    : "text-muted-foreground hover:text-foreground hover:bg-muted"
                }`}
                title="🔑 Quick Configure Provider API Key"
                aria-label="Quick configure API key"
              >
                <Key className="size-3.5" />
              </button>
              {onOpenModelSettings && (
                <button
                  type="button"
                  onClick={onOpenModelSettings}
                  className="p-1.5 rounded-lg text-muted-foreground hover:text-primary hover:bg-muted transition-colors"
                  title="⚙️ Open Full Model & API Key Configuration Page"
                  aria-label="Open model settings"
                >
                  <Settings className="size-3.5" />
                </button>
              )}
            </div>

            {/* Attachments */}
            {onAttach && (
              <>
                <button
                  type="button"
                  onClick={() => fileRef.current?.click()}
                  disabled={uploading}
                  className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted transition-colors disabled:opacity-40 cursor-pointer"
                  title={uploading ? "Uploading…" : "Attach files (clip)"}
                  aria-label="Attach files"
                >
                  <Paperclip className="size-4" />
                </button>
                <button
                  type="button"
                  onClick={() => fileRef.current?.click()}
                  disabled={uploading}
                  className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted transition-colors disabled:opacity-40 cursor-pointer"
                  title="Upload images or media"
                  aria-label="Upload images or media"
                >
                  <ImageIcon className="size-4" />
                </button>
                <input
                  ref={fileRef}
                  type="file"
                  multiple
                  className="hidden"
                  onChange={(e) => {
                    if (e.target.files && e.target.files.length > 0)
                      onAttach(e.target.files);
                    e.target.value = "";
                  }}
                />
              </>
            )}

            {/* Code Block Snippet insert button */}
            <button
              type="button"
              onClick={() => {
                setInput((prev) =>
                  prev ? `${prev}\n\`\`\`\n\n\`\`\`` : "```\n\n```",
                );
                setTimeout(() => textareaRef.current?.focus(), 50);
              }}
              className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted transition-colors cursor-pointer"
              title="Insert code block (</>)"
              aria-label="Insert code block"
            >
              <Code2 className="size-4" />
            </button>

            {onPolish && (
              <button
                type="button"
                onClick={onPolish}
                disabled={!input.trim() || polishing || isLoading}
                className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted transition-colors disabled:opacity-40"
                title={polishing ? "Polishing…" : "Improve my draft with AI"}
                aria-label="Improve draft with AI"
              >
                <Wand2
                  className={`size-4 ${polishing ? "animate-pulse text-primary" : ""}`}
                />
              </button>
            )}

            {/* Delegation opt-in. This control exists because the composer could
                not delegate at all: `sendMessage` sent `model_name`,
                `is_plan_mode` and `reasoning_effort`, and never the server's
                `autonomous` flag, so `subagent_enabled` stayed false and the
                `task` tool was never in the toolset. Measured against the live
                Gateway: `autonomous=false` -> tools `alpha_capability,
                catalog_tool_search`; `autonomous=true` -> `alpha_capability,
                task`. Typing a prompt therefore could not produce a working
                subagent, and that was the wiring rather than the operator.

                It defaults OFF and says so. Delegation spends tokens and starts
                workers, so silently enabling it would change what a prompt does;
                a control that is off must look off, not idle. */}
            {onDelegationChange && (
              <button
                type="button"
                onClick={() => onDelegationChange(!delegationEnabled)}
                disabled={isLoading}
                aria-pressed={delegationEnabled}
                aria-label="Allow this message to delegate work to subagents"
                title={
                  delegationEnabled
                    ? "Subagents ON — the agent may split this into helpers that run in the background. Costs extra tokens."
                    : "Subagents OFF — the agent answers this itself. Turn on to let it delegate work to background helpers."
                }
                className={`p-1.5 rounded-lg transition-colors disabled:opacity-40 ${
                  delegationEnabled
                    ? "text-primary bg-primary/10"
                    : "text-muted-foreground hover:text-foreground hover:bg-muted"
                }`}
              >
                <Network
                  className={`size-4 ${delegationEnabled ? "" : "opacity-60"}`}
                />
              </button>
            )}
            <VoiceControls
              onTranscript={(text) => {
                setInput((current) => (current ? `${current}\n${text}` : text));
                onDictate?.(text);
                textareaRef.current?.focus();
              }}
              onVoiceTranscript={onVoiceTranscript}
              conversationEnabled={voiceConversationEnabled}
              conversationScopeKey={voiceConversationScopeKey}
              conversationTurnActive={voiceConversationTurnActive}
              resumeConversationToken={voiceResumeToken}
              onConversationStateChange={onVoiceConversationStateChange}
            />
          </div>

          <div className="flex items-center gap-2 shrink-0">
            {onEffortChange && (
              <ReasoningEffortPicker
                models={models}
                selectedModel={selectedModel}
                effort={effort}
                onEffortChange={onEffortChange}
                ladder={effortLadder}
                labels={effortLabels}
              />
            )}
            {isLoading ? (
              <button
                type="button"
                onClick={onStop}
                className="size-9 rounded-full bg-destructive text-destructive-foreground flex items-center justify-center hover:opacity-90 transition-opacity elev-1"
                title="Stop generating"
                aria-label="Stop generating"
              >
                <Square className="size-4 fill-current" />
              </button>
            ) : (
              <button
                type="button"
                disabled={!input.trim()}
                onClick={onSubmit}
                className="size-9 rounded-full bg-gradient-to-tr from-blue-600 to-indigo-600 text-white flex items-center justify-center disabled:opacity-40 hover:opacity-95 transition-all elev-2 cursor-pointer hover:shadow-blue-500/20"
                title="Send message"
                aria-label="Send message"
              >
                <Send className="size-4 -ml-0.5" />
              </button>
            )}
          </div>
        </div>
      </div>
      <div className="text-[11px] text-center text-muted-foreground mt-2">
        {branding.name} • Type{" "}
        <kbd className="px-1 py-0.5 rounded bg-muted text-[10px] font-mono">
          /
        </kbd>{" "}
        for commands •{" "}
        <kbd className="px-1 py-0.5 rounded bg-muted text-[10px] font-mono">
          @
        </kbd>{" "}
        to tag an agent •{" "}
        <kbd className="px-1 py-0.5 rounded bg-muted text-[10px] font-mono">
          Enter
        </kbd>{" "}
        to send •{" "}
        <kbd className="px-1 py-0.5 rounded bg-muted text-[10px] font-mono">
          Shift + Enter
        </kbd>{" "}
        for new line
        {dictating && (
          <span className="block text-primary mt-1">Transcribing voice…</span>
        )}
      </div>
    </div>
  );
}
