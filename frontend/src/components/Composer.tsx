"use client";

import React, { useRef, useEffect, useState, useMemo } from "react";
import { Send, Square, Wand2, Paperclip, Terminal, ChevronRight } from "lucide-react";
import { AIModel, SlashCommandInfo } from "@/types/chat";
import { fetchCommands } from "@/lib/api";
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
  setInput: (value: string) => void;
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
  slashCommands,
}: ComposerProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [availableCommands, setAvailableCommands] = useState<SlashCommandInfo[]>(DEFAULT_CORE_COMMANDS);
  const [selectedIndex, setSelectedIndex] = useState<number>(0);
  const [isDismissed, setIsDismissed] = useState<boolean>(false);

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

      <div className="rounded-2xl border border-border bg-card shadow-sm focus-within:ring-1 focus-within:ring-primary/40 focus-within:border-primary/50 transition-all p-2.5">
        <textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask anything or type / for Master Slash Commands..."
          rows={1}
          aria-label="Message the agent"
          className="w-full resize-none bg-transparent px-3 py-2 text-sm focus:outline-none placeholder:text-muted-foreground max-h-48 text-foreground"
        />

        <div className="flex items-center justify-between pt-2 border-t border-border/40 px-2 mt-1 gap-2">
          <div className="flex items-center gap-2 min-w-0">
            {/* Model Selector */}
            <select
              value={selectedModel}
              onChange={(e) => onSelectModel(e.target.value)}
              className="text-xs bg-muted/60 border border-border/80 rounded-lg px-2.5 py-1 text-foreground focus:outline-none focus:ring-1 focus:ring-primary/40 font-medium cursor-pointer max-w-36 truncate"
              title="Language model for this chat"
              aria-label="Language model"
            >
              {models.length === 0 ? (
                <option value="default">Default model</option>
              ) : (
                models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}
                  </option>
                ))
              )}
            </select>
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
      </div>
    </div>
  );
}
