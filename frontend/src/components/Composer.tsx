"use client";

import React, { useRef, useEffect, useState, useMemo } from "react";
import { Send, Square, Wand2, Paperclip, Terminal, ChevronRight, Zap, Settings, Key, ExternalLink, Check, X } from "lucide-react";
import { AIModel, SlashCommandInfo } from "@/types/chat";
import { fetchCommands, BUILTIN_FREE_MODELS, configureProviderCredentials } from "@/lib/api";
import { VoiceControls } from "@/components/VoiceControls";
import { SlashCommand } from "@/lib/commands";
import { branding } from "@/lib/branding";

const DEFAULT_CORE_COMMANDS: SlashCommandInfo[] = [
  { command: "/goal", category: "mission", description: "Define and orchestrate autonomous goals", usage: "/goal <objective>", is_core: true, is_autonomous_trigger: true, requires_approval: false },
  { command: "/plan", category: "planning", description: "Compile 8-dimensional strategic meta-plan", usage: "/plan <prompt>", is_core: true, is_autonomous_trigger: true, requires_approval: false },
  { command: "/swarm", category: "swarm", description: "Orchestrate multi-agent specialized swarms", usage: "/swarm create <name>", is_core: true, is_autonomous_trigger: true, requires_approval: false },
  { command: "/agent", category: "agent", description: "Spawn, inspect, or manage autonomous agents", usage: "/agent spawn <role>", is_core: true, is_autonomous_trigger: true, requires_approval: false },
  { command: "/research", category: "research", description: "Deep multi-stage web and codebase research", usage: "/research <query>", is_core: true, is_autonomous_trigger: true, requires_approval: false },
  { command: "/code", category: "coding", description: "Inspect, write, and refactor code modules", usage: "/code <task>", is_core: true, is_autonomous_trigger: false, requires_approval: false },
  { command: "/memory", category: "memory", description: "Query and store episodic and semantic memory", usage: "/memory query <key>", is_core: true, is_autonomous_trigger: false, requires_approval: false },
  { command: "/context", category: "context", description: "Inspect context tokens, budget, and prune", usage: "/context inspect", is_core: true, is_autonomous_trigger: false, requires_approval: false },
  { command: "/skills", category: "skills", description: "Manage agent procedural skills and extensions", usage: "/skills list", is_core: true, is_autonomous_trigger: false, requires_approval: false },
  { command: "/model", category: "model", description: "Inspect or switch active LLM reasoning model", usage: "/model switch <model_id>", is_core: true, is_autonomous_trigger: false, requires_approval: false },
  { command: "/tools", category: "tools", description: "List and execute agentic tool calls", usage: "/tools list", is_core: true, is_autonomous_trigger: false, requires_approval: false },
  { command: "/mcp", category: "tools", description: "Model Context Protocol servers and resources", usage: "/mcp list", is_core: true, is_autonomous_trigger: false, requires_approval: false },
  { command: "/verify", category: "verification", description: "Run automated tests, linters, and invariants", usage: "/verify all", is_core: true, is_autonomous_trigger: false, requires_approval: false },
  { command: "/browser", category: "browser", description: "Launch and inspect headless browser sessions", usage: "/browser open <url>", is_core: true, is_autonomous_trigger: true, requires_approval: false },
  { command: "/learn", category: "rsi", description: "Extract and store operational learnings", usage: "/learn save", is_core: true, is_autonomous_trigger: false, requires_approval: false },
  { command: "/session", category: "session", description: "Manage thread history, checkpoints, and rollback", usage: "/session rollback", is_core: true, is_autonomous_trigger: false, requires_approval: false },
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
}

export function Composer({
  input,
  setInput,
  onSubmit,
  onStop,
  isLoading,
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
}: ComposerProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [availableCommands, setAvailableCommands] = useState<SlashCommandInfo[]>(DEFAULT_CORE_COMMANDS);
  const [selectedIndex, setSelectedIndex] = useState<number>(0);
  const [isDismissed, setIsDismissed] = useState<boolean>(false);

  // Quick API Key configuration popover state
  const [showKeyPopover, setShowKeyPopover] = useState(false);
  const [quickProvider, setQuickProvider] = useState("gemini");
  const [quickApiKey, setQuickApiKey] = useState("");
  const [savingKey, setSavingKey] = useState(false);
  const [keyStatusMsg, setKeyStatusMsg] = useState<string | null>(null);

  // Load registered backend commands on mount, then merge the prop list.
  useEffect(() => {
    async function load() {
      const cmds = await fetchCommands();
      if (cmds && cmds.length > 0) {
        setAvailableCommands(cmds);
      }
    }
    load();
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
  const suggestions = useMemo(() => {
    if (isDismissed || !input.startsWith("/") || input.includes(" ")) {
      return [];
    }
    const q = input.toLowerCase();
    return mergedCommands
      .filter((c) => c.command.toLowerCase().startsWith(q) || c.command.toLowerCase().includes(q))
      .slice(0, 8);
  }, [input, mergedCommands, isDismissed]);

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
    return { standardModels: standard, keylessModels: keyless, quotaModels: quota };
  }, [models]);

  useEffect(() => {
    if (input.startsWith("/")) {
      setIsDismissed(false);
    }
    setSelectedIndex(0);
  }, [input]);

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 200)}px`;
    }
  }, [input]);

  const selectCommand = (cmd: SlashCommandInfo) => {
    setInput(`${cmd.command} `);
    setIsDismissed(true);
    textareaRef.current?.focus();
  };

  // Paste images/files straight from the clipboard (screenshots, copied files).
  const handlePaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const files = Array.from(e.clipboardData?.files || []).filter((f) =>
      f.type.startsWith("image/") || f.size > 0
    );
    if (files.length > 0 && onPasteFiles) {
      e.preventDefault();
      onPasteFiles(files);
    }
    // Plain text + URLs paste normally into the textarea.
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (suggestions.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIndex((prev) => (prev + 1) % suggestions.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedIndex((prev) => (prev - 1 + suggestions.length) % suggestions.length);
        return;
      }
      if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) {
        e.preventDefault();
        selectCommand(suggestions[selectedIndex]);
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
      {/* Slash Command Suggestions Palette */}
      {suggestions.length > 0 && (
        <div className="absolute bottom-full mb-2 left-3 right-3 bg-popover/95 backdrop-blur-md border border-border rounded-xl shadow-xl overflow-hidden z-50 animate-in fade-in slide-in-from-bottom-2 duration-150">
          <div className="flex items-center justify-between px-3 py-1.5 border-b border-border/60 bg-muted/40 text-[11px] font-medium text-muted-foreground">
            <div className="flex items-center gap-1.5">
              <Terminal className="size-3.5 text-primary" />
              <span>Master Slash Commands</span>
            </div>
            <span>Use ↑↓ to navigate • Tab to select • Esc to dismiss</span>
          </div>
          <div className="max-h-60 overflow-y-auto p-1 divide-y divide-border/20">
            {suggestions.map((cmd, idx) => (
              <button
                key={cmd.command}
                type="button"
                onClick={() => selectCommand(cmd)}
                className={`w-full text-left px-3 py-2 rounded-lg flex items-center justify-between transition-colors ${
                  idx === selectedIndex ? "bg-accent text-accent-foreground" : "hover:bg-muted/60"
                }`}
              >
                <div className="flex items-center gap-2.5 min-w-0">
                  <span className="font-mono text-xs font-semibold text-primary">
                    {cmd.command}
                  </span>
                  <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-muted/80 text-muted-foreground font-semibold">
                    {cmd.category}
                  </span>
                  <span className="text-xs text-muted-foreground truncate max-w-md">
                    {cmd.description}
                  </span>
                </div>
                <ChevronRight className="size-3.5 text-muted-foreground shrink-0 opacity-60" />
              </button>
            ))}
          </div>
        </div>
      )}

      {showKeyPopover && (
        <div className="mb-2.5 rounded-2xl border border-primary/30 bg-card/95 backdrop-blur-md shadow-xl p-3.5 space-y-3 animate-in fade-in slide-in-from-bottom-2">
          <div className="flex items-center justify-between border-b border-border/50 pb-2">
            <div className="flex items-center gap-2">
              <Key className="size-4 text-primary" />
              <span className="text-xs font-bold text-foreground">Quick API Key & Provider Setup</span>
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
                  <Settings className="size-3" /> Full Model & Key Studio <ExternalLink className="size-2.5" />
                </button>
              )}
              <button
                type="button"
                onClick={() => setShowKeyPopover(false)}
                className="p-1 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted"
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
                  <option value="cohere">Cohere Coral (1,000 req/mo Free)</option>
                  <option value="cloudflare">Cloudflare Workers AI (Free)</option>
                </optgroup>
                <optgroup label="🌐 Free-Model Gateways">
                  <option value="openrouter">OpenRouter (:free & Union Alpha)</option>
                </optgroup>
                <optgroup label="🎟️ Free Trial Credits">
                  <option value="nvidia">NVIDIA NIM (1,000 Credits)</option>
                  <option value="cerebras">Cerebras (1M Tokens/day)</option>
                </optgroup>
                <optgroup label="🚀 Commercial Frontier">
                  <option value="openai">OpenAI (GPT-4o / o3-mini)</option>
                  <option value="anthropic">Anthropic (Claude 3.7 Sonnet)</option>
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
                      setKeyStatusMsg(`✅ Saved & unlocked ${quickProvider.toUpperCase()} models!`);
                      if (onModelsUpdated) await onModelsUpdated();
                    } catch (err: any) {
                      setKeyStatusMsg(`❌ ${err?.message || "Failed to save key"}`);
                    } finally {
                      setSavingKey(false);
                    }
                  }}
                  className="px-3 py-1.5 rounded-lg bg-primary text-primary-foreground text-xs font-semibold hover:opacity-95 disabled:opacity-40 shrink-0 flex items-center gap-1"
                >
                  <Check className="size-3.5" /> {savingKey ? "Saving…" : "Save Key"}
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

      <div className="rounded-2xl border border-border bg-card shadow-sm focus-within:ring-1 focus-within:ring-primary/40 focus-within:border-primary/50 transition-all p-2.5">
        <textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
          placeholder="Ask anything or type / for Master Slash Commands..."
          rows={1}
          aria-label="Message the agent"
          className="w-full resize-none bg-transparent px-3 py-2 text-sm focus:outline-none placeholder:text-muted-foreground max-h-48 text-foreground"
        />

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
                title={freeNote || "Select LLM reasoning model or configure API keys"}
                aria-label="Language model"
              >
                <option value="default">⚡ Default (Auto-Routed)</option>
                {keylessModels.length > 0 && (
                  <optgroup label="✨ Free Models (No API Key Needed)">
                    {keylessModels.map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.name}
                      </option>
                    ))}
                  </optgroup>
                )}
                {quotaModels.length > 0 && (
                  <optgroup label="🎁 Free Tier / Gateway (Quota)">
                    {quotaModels.map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.name}
                      </option>
                    ))}
                  </optgroup>
                )}
                {standardModels.length > 0 && (
                  <optgroup label="🚀 Standard & Custom Models">
                    {standardModels.map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.name}
                      </option>
                    ))}
                  </optgroup>
                )}
                <optgroup label="⚙️ Configure API Keys & Models">
                  <option value="__quick_api_key__">🔑 Quick Set API Key (Gemini / Groq / OpenRouter)…</option>
                  <option value="__configure_models__">⚙️ Open Model & API Key Configuration Page…</option>
                </optgroup>
              </select>
              {onRefreshFree && (
                <button
                  type="button"
                  onClick={onRefreshFree}
                  disabled={refreshingFree}
                  className="p-1 rounded-lg text-amber-500 hover:text-amber-400 hover:bg-muted transition-colors disabled:opacity-40"
                  title="⚡ Find & Probe Today's Free Models (Live health probe and catalog refresh)"
                  aria-label="Find and probe today's free models"
                >
                  <Zap className={`size-3.5 ${refreshingFree ? "animate-spin text-amber-400" : ""}`} />
                </button>
              )}
              <button
                type="button"
                onClick={() => setShowKeyPopover((v) => !v)}
                className={`p-1 rounded-lg transition-colors ${
                  showKeyPopover ? "bg-primary/15 text-primary" : "text-muted-foreground hover:text-foreground hover:bg-muted"
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
                  className="p-1 rounded-lg text-muted-foreground hover:text-primary hover:bg-muted transition-colors"
                  title="⚙️ Open Full Model & API Key Configuration Page"
                  aria-label="Open model settings"
                >
                  <Settings className="size-3.5" />
                </button>
              )}
            </div>
            {onAttach && (
              <>
                <button
                  type="button"
                  onClick={() => fileRef.current?.click()}
                  disabled={uploading}
                  className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted transition-colors disabled:opacity-40"
                  title={uploading ? "Uploading…" : "Attach files"}
                  aria-label="Attach files"
                >
                  <Paperclip className="size-4" />
                </button>
                <input
                  ref={fileRef}
                  type="file"
                  multiple
                  className="hidden"
                  onChange={(e) => {
                    if (e.target.files && e.target.files.length > 0) onAttach(e.target.files);
                    e.target.value = "";
                  }}
                />
              </>
            )}
            {onPolish && (
              <button
                type="button"
                onClick={onPolish}
                disabled={!input.trim() || polishing || isLoading}
                className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted transition-colors disabled:opacity-40"
                title={polishing ? "Polishing…" : "Improve my draft with AI"}
                aria-label="Improve draft with AI"
              >
                <Wand2 className={`size-4 ${polishing ? "animate-pulse text-primary" : ""}`} />
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
            {isLoading ? (
              <button
                type="button"
                onClick={onStop}
                className="size-8 rounded-lg bg-destructive text-destructive-foreground flex items-center justify-center hover:opacity-90 transition-opacity"
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
                className="size-8 rounded-lg bg-primary text-primary-foreground flex items-center justify-center disabled:opacity-40 hover:opacity-95 transition-opacity"
                title="Send message"
                aria-label="Send message"
              >
                <Send className="size-3.5" />
              </button>
            )}
          </div>
        </div>
      </div>
      <div className="text-[11px] text-center text-muted-foreground mt-2">
        {branding.name} • Type <kbd className="px-1 py-0.5 rounded bg-muted text-[10px] font-mono">/</kbd> for commands • <kbd className="px-1 py-0.5 rounded bg-muted text-[10px] font-mono">Enter</kbd> to send • <kbd className="px-1 py-0.5 rounded bg-muted text-[10px] font-mono">Shift + Enter</kbd> for new line
        {dictating && <span className="block text-primary mt-1">Transcribing voice…</span>}
      </div>
    </div>
  );
}
