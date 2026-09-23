"use client";

import React, { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Bot, User, Brain, Copy, Check, ThumbsUp, ThumbsDown, RotateCcw, Pencil, Users, ShieldCheck, FileDown } from "lucide-react";
import { ChatMessage } from "@/types/chat";
import { branding } from "@/lib/branding";
import { ToolPill } from "./ToolPill";
import { TodoBlock } from "./TodoBlock";
import { HumanApprovalCard } from "./HumanApprovalCard";
import { Volume2, Loader2, AlertCircle } from "lucide-react";
import { synthesizeSpeech, MultimodalError } from "@/lib/multimodal";

interface MessageItemProps {
  message: ChatMessage;
  onApprovalDecision?: (approved: boolean) => void;
  /** Feedback wiring (assistant messages with a runId). */
  onRate?: (messageId: string, rating: 1 | -1) => void;
  /** "Ask again" for the latest assistant answer. */
  onRegenerate?: () => void;
  showRegenerate?: boolean;
  regenerating?: boolean;
  /** Edit & resend for your messages. */
  onEdit?: (messageId: string, newContent: string) => void;
}

export function MessageItem({ message, onApprovalDecision, onRate, onRegenerate, showRegenerate, regenerating, onEdit }: MessageItemProps) {
  const isUser = message.role === "user";
  const [copied, setCopied] = useState(false);
  const [showThinking, setShowThinking] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(message.content);

  const dmMatch = message.content ? message.content.match(/^\[DM from ([^\]]+)\]\s*([\s\S]*)$/) : null;
  const groupMatch = !dmMatch && message.content ? message.content.match(/^\[(Group(?:\s+Chat)?(?::\s*([^\]]+))?)\](?:\s*@?([a-zA-Z0-9_-]+):)?\s*([\s\S]*)$/i) : null;

  const isA2A = Boolean(dmMatch);
  const a2aSender = dmMatch ? dmMatch[1] : null;
  const isGroupChat = Boolean(groupMatch);
  const groupName = groupMatch ? (groupMatch[2] || "Team Channel") : null;
  const groupSender = groupMatch ? groupMatch[3] : null;

  const displayContent = dmMatch ? dmMatch[2] : groupMatch ? groupMatch[4] : message.content;

  // Speaker playback: POST /tts → objectURL → <audio>; revoke on cleanup (plan §6).
  const [speaking, setSpeaking] = useState(false);
  const [speechLoading, setSpeechLoading] = useState(false);
  const [speakError, setSpeakError] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const urlRef = useRef<string | null>(null);

  const stopSpeech = () => {
    const audio = audioRef.current;
    if (audio) {
      audio.onended = null;
      audio.onerror = null;
      audio.pause();
      audioRef.current = null;
    }
    if (urlRef.current) {
      URL.revokeObjectURL(urlRef.current);
      urlRef.current = null;
    }
    setSpeaking(false);
  };

  const playSpeech = async () => {
    if (speechLoading) return;
    if (speaking) {
      stopSpeech();
      return;
    }
    const text = displayContent.slice(0, 4000);
    if (!text.trim()) {
      setSpeakError("Nothing to speak in this message.");
      return;
    }
    setSpeakError(null);
    setSpeechLoading(true);
    try {
      const { blob } = await synthesizeSpeech(text);
      if (blob.size === 0) throw new MultimodalError(503, "tts", "serving engine returned 0 audio bytes");
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      urlRef.current = url;
      audioRef.current = audio;
      audio.onended = () => stopSpeech();
      audio.onerror = () => {
        setSpeakError("The browser failed to play the audio.");
        stopSpeech();
      };
      setSpeaking(true);
      await audio.play();
    } catch (err) {
      stopSpeech();
      setSpeakError(
        err instanceof MultimodalError
          ? err.attempts.length > 0
            ? `${err.message} — ${err.formatAttempts()}`
            : err.message
          : err instanceof Error
            ? err.message
            : String(err),
      );
    } finally {
      setSpeechLoading(false);
    }
  };

  // Plan §6: revoke object URLs when the message unmounts mid-playback.
  useEffect(() => () => stopSpeech(), []);

  const copyToClipboard = () => {
    navigator.clipboard.writeText(displayContent);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const downloadResponse = () => {
    const blob = new Blob([displayContent], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${isUser ? "prompt" : "response"}-${message.id}.md`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  };

  return (
    <div
      className={`flex w-full gap-3 py-4 px-4 rounded-xl transition-all ${
        isUser
          ? "bg-muted/30 ml-auto max-w-3xl"
          : isA2A
            ? "bg-card border border-blue-500/40 shadow-sm shadow-blue-500/5 max-w-4xl"
            : isGroupChat
              ? "bg-card border border-purple-500/40 shadow-sm shadow-purple-500/5 max-w-4xl"
              : "bg-card border border-border/50 max-w-4xl"
      }`}
    >
      <div
        className={`size-8 rounded-lg flex items-center justify-center shrink-0 ${
          isUser
            ? "bg-primary text-primary-foreground"
            : isA2A
              ? "bg-blue-500/20 text-blue-400 border border-blue-500/30"
              : isGroupChat
                ? "bg-purple-500/20 text-purple-400 border border-purple-500/30"
                : "bg-muted text-foreground border border-border"
        }`}
      >
        {isUser ? (
          <User className="size-4" />
        ) : isGroupChat ? (
          <Users className="size-4 text-purple-400" />
        ) : (
          <Bot className={`size-4 ${isA2A ? "text-blue-400" : "text-primary"}`} />
        )}
      </div>

      <div className="flex-1 overflow-hidden space-y-2 min-w-0">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="text-xs font-semibold text-foreground">
              {isUser
                ? "You"
                : isA2A
                  ? `@${a2aSender}`
                  : isGroupChat
                    ? groupSender
                      ? `@${groupSender}`
                      : "Team Channel"
                    : branding.assistantLabel}
            </span>
            {isA2A && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/15 text-blue-400 font-medium">
                Agent-to-Agent DM
              </span>
            )}
            {isGroupChat && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-500/15 text-purple-400 font-medium">
                #{groupName}
              </span>
            )}
          </div>
          <div className="flex items-center gap-0.5">
            {/* Your rating teaches the system what good looks like */}
            {!isUser && onRate && message.runId && (
              <>
                <button
                  type="button"
                  onClick={() => onRate(message.id, 1)}
                  className={`p-1 rounded hover:bg-muted/60 transition-colors ${message.rating === 1 ? "text-emerald-500" : "text-muted-foreground hover:text-foreground"}`}
                  title="Good answer"
                  aria-label="Rate answer good"
                >
                  <ThumbsUp className="size-3.5" />
                </button>
                <button
                  type="button"
                  onClick={() => onRate(message.id, -1)}
                  className={`p-1 rounded hover:bg-muted/60 transition-colors ${message.rating === -1 ? "text-destructive" : "text-muted-foreground hover:text-foreground"}`}
                  title="Bad answer"
                  aria-label="Rate answer bad"
                >
                  <ThumbsDown className="size-3.5" />
                </button>
              </>
            )}
            {!isUser && showRegenerate && onRegenerate && (
              <button
                type="button"
                onClick={onRegenerate}
                disabled={regenerating}
                className="p-1 rounded text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors disabled:opacity-40"
                title="Ask again (regenerate)"
                aria-label="Regenerate answer"
              >
                <RotateCcw className={`size-3.5 ${regenerating ? "animate-spin" : ""}`} />
              </button>
            )}
            {isUser && onEdit && (
              <button
                type="button"
                onClick={() => {
                  setDraft(message.content);
                  setEditing((v) => !v);
                }}
                className="p-1 rounded text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors"
                title="Edit and resend"
                aria-label="Edit and resend message"
              >
                <Pencil className="size-3.5" />
              </button>
            )}
            <button
              type="button"
              onClick={copyToClipboard}
              className="p-1 rounded text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors"
              title={isUser ? "Copy prompt" : "Copy answer"}
              aria-label={isUser ? "Copy prompt" : "Copy answer"}
            >
              {copied ? <Check className="size-3.5 text-emerald-500" /> : <Copy className="size-3.5" />}
            </button>
            <button
              type="button"
              onClick={downloadResponse}
              className="p-1 rounded text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors"
              title={isUser ? "Download prompt (.md)" : "Download answer (.md)"}
              aria-label={isUser ? "Download prompt" : "Download answer"}
            >
              <FileDown className="size-3.5" />
            </button>
            {!isUser && (
              <button
                type="button"
                onClick={() => void playSpeech()}
                className="p-1 rounded text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors"
                title={
                  speakError
                    ? `Speech failed: ${speakError}`
                    : speaking
                      ? "Stop speech"
                      : speechLoading
                        ? "Generating speech…"
                        : displayContent.length > 4000
                          ? "Listen to this answer (first 4,000 characters)"
                          : "Listen to this answer"
                }
                aria-label={speaking ? "Stop speech" : "Listen to this answer"}
              >
                {speakError ? (
                  <AlertCircle className="size-3.5 text-destructive" />
                ) : speechLoading ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <Volume2 className={`size-3.5 ${speaking ? "text-primary" : ""}`} />
                )}
              </button>
            )}
          </div>
        </div>

        {/* Autonomous Slash Command Lifecycle Badge */}
        {message.autonomousDetection && message.autonomousDetection.matched && (
          <div className="rounded-lg border border-primary/30 bg-primary/5 p-2 text-xs space-y-1 my-1">
            <div className="flex items-center gap-2">
              <span className="flex items-center gap-1 font-mono font-semibold text-primary">
                ⚡ Auto-Triggered: {message.autonomousDetection.command}
              </span>
              <span className="text-[10px] uppercase font-bold tracking-wider px-1.5 py-0.5 rounded bg-primary/20 text-primary">
                {message.autonomousDetection.phase}
              </span>
              <span className="text-[10px] text-muted-foreground ml-auto">
                {Math.round(message.autonomousDetection.confidence * 100)}% confidence
              </span>
            </div>
            <p className="text-[11px] text-muted-foreground">
              {message.autonomousDetection.reason}
            </p>
          </div>
        )}

        {/* Agent-to-Agent Verified Attribution Banner */}
        {isA2A && (
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-blue-500/30 bg-blue-500/10 text-xs my-1">
            <Bot className="size-3.5 text-blue-400 shrink-0" />
            <span className="font-semibold text-blue-400">@{a2aSender}</span>
            <span className="text-muted-foreground text-[11px]">➔ autonomous dispatch to team</span>
            <span className="ml-auto text-[10px] px-2 py-0.5 rounded-full bg-blue-500/20 text-blue-300 font-mono font-medium flex items-center gap-1">
              <ShieldCheck className="size-3" /> Server-Verified A2A Attribution
            </span>
          </div>
        )}

        {/* Group Chat Channel Banner */}
        {isGroupChat && (
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-purple-500/30 bg-purple-500/10 text-xs my-1">
            <Users className="size-3.5 text-purple-400 shrink-0" />
            <span className="font-semibold text-purple-400">#{groupName}</span>
            <span className="text-muted-foreground text-[11px]">
              Multi-Agent Room Broadcast {groupSender ? `from @${groupSender}` : ""}
            </span>
            <span className="ml-auto text-[10px] px-2 py-0.5 rounded-full bg-purple-500/20 text-purple-300 font-mono font-medium">
              Group Channel
            </span>
          </div>
        )}

        {editing && isUser && onEdit ? (
          <div className="space-y-2">
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              rows={3}
              className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary/40"
              aria-label="Edit your message"
            />
            <div className="flex gap-2">
              <button
                type="button"
                disabled={!draft.trim()}
                onClick={() => {
                  onEdit(message.id, draft.trim());
                  setEditing(false);
                }}
                className="px-3 py-1.5 rounded-lg bg-primary text-primary-foreground text-xs font-semibold disabled:opacity-40"
              >
                Send edited
              </button>
              <button
                type="button"
                onClick={() => setEditing(false)}
                className="px-3 py-1.5 rounded-lg border border-border text-xs font-medium hover:bg-muted"
              >
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <>
            {message.thinking && (
              <div className="rounded-lg border border-border/60 bg-muted/20 text-xs overflow-hidden">
                <button
                  type="button"
                  onClick={() => setShowThinking(!showThinking)}
                  className="flex w-full items-center gap-1.5 px-2.5 py-1.5 font-mono text-[11px] text-muted-foreground hover:bg-muted/40 transition-colors"
                >
                  <Brain className="size-3.5 text-amber-500" />
                  <span>Thought process</span>
                  <span className="text-[10px] ml-auto">{showThinking ? "Hide" : "Show"}</span>
                </button>
                {showThinking && (
                  <div className="p-2.5 border-t border-border/40 text-[11px] text-muted-foreground font-mono whitespace-pre-wrap">
                    {message.thinking}
                  </div>
                )}
              </div>
            )}

            {message.todos && message.todos.length > 0 && (
              <TodoBlock todos={message.todos} />
            )}

            {message.approvalRequest && onApprovalDecision && (
              <HumanApprovalCard approval={message.approvalRequest} onDecision={onApprovalDecision} />
            )}

            {message.toolCalls && message.toolCalls.length > 0 && (
              <div className="space-y-1 my-2">
                {message.toolCalls.map((tc) => (
                  <ToolPill key={tc.id} toolCall={tc} />
                ))}
              </div>
            )}

            {displayContent && (
              <div className="prose prose-sm dark:prose-invert max-w-none text-sm leading-relaxed break-words">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {displayContent}
                </ReactMarkdown>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
