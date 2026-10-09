"use client";

import React, { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  Bot,
  User,
  Copy,
  Check,
  ThumbsUp,
  ThumbsDown,
  RotateCcw,
  Pencil,
  Users,
  ShieldCheck,
  FileDown,
} from "lucide-react";
import { ChatMessage } from "@/types/chat";
import { branding } from "@/lib/branding";
import { absoluteStamp, clockTime } from "@/lib/time";
import { TaskList } from "./TaskList";
import { HumanApprovalCard } from "./HumanApprovalCard";
import { ThinkingBlock } from "./agent-ui/ThinkingBlock";
import { AgentToolBlocks } from "./agent-ui/AgentToolBlocks";
import { ArtifactStrip } from "./agent-ui/ArtifactStrip";
import { markdownComponents } from "./agent-ui/CodeBlock";
import { Volume2, Loader2, AlertCircle } from "lucide-react";
import { enqueueSpeech, isSpeechCancellation } from "@/lib/speech";
import { primeSpeakerPlayback, speak, speakErrorMessage } from "@/lib/voice";

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
  /**
   * This message is the one currently streaming. Drives the live affordances
   * — the pulsing thinking header, in-flight tool states, and the trailing
   * cursor — which must all be gone once the run settles.
   */
  streaming?: boolean;
  /**
   * This message is the transcript search's current hit. Draws the
   * match ring so "3 of 12" has a visible "3".
   */
  searchHit?: boolean;
}

export function MessageItem({
  message,
  onApprovalDecision,
  onRate,
  onRegenerate,
  showRegenerate,
  regenerating,
  onEdit,
  streaming = false,
  searchHit = false,
}: MessageItemProps) {
  const isUser = message.role === "user";
  const [copied, setCopied] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(message.content);
  // Long answers collapse behind "Show more" so a 200-line answer
  // does not bury the rest of the transcript. Streaming answers
  // never clamp — the text is still arriving.
  const [answerExpanded, setAnswerExpanded] = useState(false);

  const dmMatch = message.content
    ? message.content.match(/^\[DM from ([^\]]+)\]\s*([\s\S]*)$/)
    : null;
  const groupMatch =
    !dmMatch && message.content
      ? message.content.match(
          /^\[(Group(?:\s+Chat)?(?::\s*([^\]]+))?)\](?:\s*@?([a-zA-Z0-9_-]+):)?\s*([\s\S]*)$/i,
        )
      : null;

  const isA2A = Boolean(dmMatch);
  /** Server-stamped clock time, or `null` for a row the Gateway never stamped. */
  const stamp = clockTime(message.createdAt);
  const stampFull = absoluteStamp(message.createdAt);
  const a2aSender = dmMatch ? dmMatch[1] : null;
  const isGroupChat = Boolean(groupMatch);
  const groupName = groupMatch ? groupMatch[2] || "Team Channel" : null;
  const groupSender = groupMatch ? groupMatch[3] : null;

  const displayContent = dmMatch
    ? dmMatch[2]
    : groupMatch
      ? groupMatch[4]
      : message.content;

  /** Answers past this length collapse behind "Show more". */
  const LONG_ANSWER_CHARS = 4000;
  const isLongAnswer =
    !isUser && !streaming && (displayContent?.length ?? 0) > LONG_ANSWER_CHARS;
  const clampAnswer = isLongAnswer && !answerExpanded;

  // All message/voice playback shares one serial SpeechQueue.
  const [speaking, setSpeaking] = useState(false);
  const [speechLoading, setSpeechLoading] = useState(false);
  const [speakError, setSpeakError] = useState<string | null>(null);
  const speechAbortRef = useRef<AbortController | null>(null);

  const stopSpeech = () => {
    speechAbortRef.current?.abort();
    setSpeaking(false);
    setSpeechLoading(false);
  };

  const playSpeech = async () => {
    if (speaking || speechLoading) {
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
    const controller = new AbortController();
    speechAbortRef.current = controller;
    try {
      setSpeaking(true);
      await primeSpeakerPlayback();
      await enqueueSpeech(text, {
        player: (value, signal) => speak(value, { signal }),
        signal: controller.signal,
      });
      if (speechAbortRef.current === controller) setSpeaking(false);
    } catch (err) {
      if (!isSpeechCancellation(err)) setSpeakError(speakErrorMessage(err));
      if (speechAbortRef.current === controller) setSpeaking(false);
    } finally {
      if (speechAbortRef.current === controller) {
        speechAbortRef.current = null;
        setSpeechLoading(false);
      }
    }
  };

  // Cancel only this message's queued/current task when its card unmounts.
  useEffect(() => () => stopSpeech(), []);

  const copyToClipboard = () => {
    navigator.clipboard.writeText(displayContent);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const downloadResponse = () => {
    const blob = new Blob([displayContent], {
      type: "text/markdown;charset=utf-8",
    });
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
      data-message-id={message.id}
      className={`flex w-full gap-3 py-4 px-4 rounded-xl transition-all scroll-mt-4 ${
        searchHit
          ? "ring-2 ring-primary/60 ring-offset-2 ring-offset-background"
          : ""
      } ${
        isUser
          ? "bg-muted/30 ml-auto max-w-3xl"
          : isA2A
            ? "bg-card border border-blue-500/40 elev-1 shadow-blue-500/5 max-w-4xl"
            : isGroupChat
              ? "bg-card border border-purple-500/40 elev-1 shadow-purple-500/5 max-w-4xl"
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
          <Bot
            className={`size-4 ${isA2A ? "text-blue-400" : "text-primary"}`}
          />
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
            {/* The Gateway stamps every feed row; a row it never stamped shows
                no time rather than the moment this page loaded (lib/time.ts). */}
            {stamp && (
              <time
                dateTime={stampFull ?? undefined}
                title={stampFull ?? undefined}
                className="text-[10px] font-normal text-muted-foreground tabular-nums"
              >
                {stamp}
              </time>
            )}
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
                  className={`p-1.5 rounded hover:bg-muted/60 transition-colors ${message.rating === 1 ? "text-emerald-500" : "text-muted-foreground hover:text-foreground"}`}
                  title="Good answer"
                  aria-label="Rate answer good"
                >
                  <ThumbsUp className="size-3.5" />
                </button>
                <button
                  type="button"
                  onClick={() => onRate(message.id, -1)}
                  className={`p-1.5 rounded hover:bg-muted/60 transition-colors ${message.rating === -1 ? "text-destructive" : "text-muted-foreground hover:text-foreground"}`}
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
                className="p-1.5 rounded text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors disabled:opacity-40"
                title="Ask again (regenerate)"
                aria-label="Regenerate answer"
              >
                <RotateCcw
                  className={`size-3.5 ${regenerating ? "animate-spin" : ""}`}
                />
              </button>
            )}
            {isUser && onEdit && (
              <button
                type="button"
                onClick={() => {
                  setDraft(message.content);
                  setEditing((v) => !v);
                }}
                className="p-1.5 rounded text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors"
                title="Edit and resend"
                aria-label="Edit and resend message"
              >
                <Pencil className="size-3.5" />
              </button>
            )}
            <button
              type="button"
              onClick={copyToClipboard}
              className="p-1.5 rounded text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors"
              title={isUser ? "Copy prompt" : "Copy answer"}
              aria-label={isUser ? "Copy prompt" : "Copy answer"}
            >
              {copied ? (
                <Check className="size-3.5 text-emerald-500" />
              ) : (
                <Copy className="size-3.5" />
              )}
            </button>
            <button
              type="button"
              onClick={downloadResponse}
              className="p-1.5 rounded text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors"
              title={isUser ? "Download prompt (.md)" : "Download answer (.md)"}
              aria-label={isUser ? "Download prompt" : "Download answer"}
            >
              <FileDown className="size-3.5" />
            </button>
            {!isUser && (
              <button
                type="button"
                onClick={() => void playSpeech()}
                className="p-1.5 rounded text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors"
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
                  <Volume2
                    className={`size-3.5 ${speaking ? "text-primary" : ""}`}
                  />
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
                {Number.isFinite(message.autonomousDetection.confidence)
                  ? `${Math.round(message.autonomousDetection.confidence * 100)}% confidence`
                  : "—"}
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
            <span className="text-muted-foreground text-[11px]">
              ➔ autonomous dispatch to team
            </span>
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
              Multi-Agent Room Broadcast{" "}
              {groupSender ? `from @${groupSender}` : ""}
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
            {/* The reasoning trace, as a card: amber-accented,
                collapsed by default, open while the run is still
                writing the trace, with a rough token estimate and
                a markdown body (see `agent-ui/ThinkingBlock`). */}
            {message.thinking && (
              <ThinkingBlock
                thinking={message.thinking}
                streaming={streaming}
              />
            )}

            {/* `message.todos` is the plan as it stood when this message was
                persisted, not the plan right now. A run still in flight updates
                the live panel above the composer; this is the historical copy,
                so it renders collapsed rather than competing with it. */}
            {message.todos && message.todos.length > 0 && (
              <TaskList
                todos={message.todos}
                variant="inline"
                defaultOpen={false}
                title="Plan for this answer"
              />
            )}

            {message.approvalRequest && onApprovalDecision && (
              <HumanApprovalCard
                approval={message.approvalRequest}
                onDecision={onApprovalDecision}
              />
            )}

            {/* The turn's tool work, split by kind: terminal
                commands and file writes get specialised blocks
                (command + exit code, path + diff); everything
                else folds behind a receipt (see
                `agent-ui/AgentToolBlocks`). */}
            {message.toolCalls && message.toolCalls.length > 0 && (
              <AgentToolBlocks toolCalls={message.toolCalls} live={streaming} />
            )}

            {/* The assistant reply. Scoped to .response-prose, defined in
                globals.css.
                *
                * This used to carry `prose prose-sm dark:prose-invert
                * max-w-none`, and every one of those `prose*` classes was DEAD:
                * @tailwindcss/typography is neither installed nor registered in
                * tailwind.config.cjs, which lists only tailwindcss-animate. So
                * the reply had no typographic styling at all - headings, lists,
                * quotes, tables and code blocks fell back to browser defaults,
                * and `max-w-none` left the measure unbounded inside a full-width
                * transcript, which is why a long answer read as one flat wall.
                *
                * .response-prose is plain CSS rather than a new dependency: a
                * 68ch measure, a heading scale where size/weight/tracking move
                * together, real block spacing, and code and table treatments. */}
            {/* Long answers collapse behind "Show more" so a
                200-line answer does not bury the rest of the
                transcript. The clamp is visual only — copy,
                download and speech still read the whole answer. */}
            {displayContent && (
              <div>
                <div
                  className={`response-prose ${clampAnswer ? "max-h-96 overflow-hidden" : ""}`}
                  data-answer-clamped={clampAnswer || undefined}
                >
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    components={markdownComponents}
                  >
                    {displayContent}
                  </ReactMarkdown>
                </div>
                {isLongAnswer && (
                  <button
                    type="button"
                    onClick={() => setAnswerExpanded((v) => !v)}
                    aria-expanded={answerExpanded}
                    className="mt-1.5 text-[11px] font-medium text-primary hover:underline"
                  >
                    {answerExpanded
                      ? "Show less"
                      : `Show more (${displayContent.length.toLocaleString()} characters)`}
                  </button>
                )}
              </div>
            )}

            {/* Artifacts the run delivered — files, documents,
                images — as expandable chips. `ChatMessage.artifacts`
                was typed but never rendered before this. */}
            {message.artifacts && message.artifacts.length > 0 && (
              <ArtifactStrip artifacts={message.artifacts} />
            )}

            {/* A streaming turn must never look finished. Content already on
                screen followed by silence reads as a hang, so the turn keeps a
                quiet trailing indicator until the run actually settles. */}
            {streaming && (
              <div
                className="flex items-center gap-1 pt-0.5"
                aria-hidden="true"
              >
                <span className="size-1.5 animate-pulse rounded-full bg-primary/60" />
                <span className="size-1.5 animate-pulse rounded-full bg-primary/40 [animation-delay:150ms]" />
                <span className="size-1.5 animate-pulse rounded-full bg-primary/25 [animation-delay:300ms]" />
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
