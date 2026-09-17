"use client";

import React, { useState } from "react";
import { ChevronDown, ChevronRight, Terminal, CheckCircle2, Bot, Send, ShieldAlert, CheckSquare, Sparkles } from "lucide-react";
import { ToolCall } from "@/types/chat";

interface ToolPillProps {
  toolCall: ToolCall;
}

export function ToolPill({ toolCall }: ToolPillProps) {
  const [isOpen, setIsOpen] = useState(false);
  const isA2A = toolCall.name === "message_agent";
  const isApproval = toolCall.name === "request_approval";
  const isGatekeeper = toolCall.name === "verify_and_complete";
  const isAudit = toolCall.name === "audit_code";

  const targetBot = isA2A ? String(toolCall.args?.target || "teammate") : null;
  const msgContent = isA2A ? String(toolCall.args?.message || "") : null;

  return (
    <div className={`my-1.5 rounded-lg border text-xs overflow-hidden transition-colors ${
      isA2A ? "border-blue-500/40 bg-blue-500/5 dark:bg-blue-500/10" : "border-border/80 bg-muted/40"
    }`}>
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className="flex w-full items-center justify-between px-3 py-1.5 font-mono text-muted-foreground hover:bg-muted/70 transition-colors"
      >
        <div className="flex items-center gap-2">
          {isA2A ? (
            <>
              <Bot className="size-3.5 text-blue-500" />
              <span className="font-semibold text-blue-500">Agent-to-Agent DM</span>
              <span className="text-[11px] font-sans px-1.5 py-0.2 rounded bg-blue-500/20 text-blue-400 font-bold">
                ➔ @{targetBot}
              </span>
            </>
          ) : isApproval ? (
            <>
              <ShieldAlert className="size-3.5 text-amber-500" />
              <span className="font-semibold text-amber-500">Human Approval Gated</span>
            </>
          ) : isGatekeeper ? (
            <>
              <CheckSquare className="size-3.5 text-emerald-500" />
              <span className="font-semibold text-emerald-500">DoD Contract Verification</span>
            </>
          ) : isAudit ? (
            <>
              <Sparkles className="size-3.5 text-purple-500" />
              <span className="font-semibold text-purple-500">Pre-Merge Audit Council</span>
            </>
          ) : (
            <>
              <Terminal className="size-3.5 text-primary" />
              <span className="font-semibold text-foreground/90">{toolCall.name}</span>
              <span className="text-[10px] text-muted-foreground">executed</span>
            </>
          )}
        </div>
        <div className="flex items-center gap-1.5">
          <CheckCircle2 className="size-3 text-emerald-500" />
          {isOpen ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
        </div>
      </button>

      {/* Special Quick-Preview for A2A Message without opening JSON */}
      {isA2A && msgContent && !isOpen && (
        <div className="px-3 py-1 border-t border-blue-500/20 bg-blue-500/5 text-[11px] text-foreground/80 flex items-center gap-2">
          <Send className="size-3 text-blue-400 shrink-0" />
          <span className="truncate italic">"{msgContent}"</span>
        </div>
      )}

      {isOpen && (
        <div className="border-t border-border/60 bg-background/50 p-2.5 space-y-2">
          {isA2A && msgContent ? (
            <div>
              <div className="text-[10px] font-semibold text-blue-400 uppercase tracking-wider mb-1">
                Dispatched Message Body to @{targetBot}
              </div>
              <div className="p-2 rounded-lg bg-blue-500/10 border border-blue-500/20 text-xs font-sans whitespace-pre-wrap text-foreground">
                {msgContent}
              </div>
            </div>
          ) : (
            <div>
              <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider mb-1">Arguments</div>
              <pre className="p-2 rounded bg-muted/80 text-[11px] overflow-x-auto text-foreground font-mono">
                {JSON.stringify(toolCall.args, null, 2)}
              </pre>
            </div>
          )}
          {toolCall.output && (
            <div>
              <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider mb-1">Result</div>
              <pre className="p-2 rounded bg-muted/80 text-[11px] overflow-x-auto text-foreground font-mono max-h-48">
                {toolCall.output}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

