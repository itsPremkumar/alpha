"use client";

import React, { useState } from "react";
import { Loader2, PlayCircle } from "lucide-react";
import { BotProfile, botDisplayName } from "@/types/bots";
import { contextSentence, starterActions, type StarterAction } from "@/lib/chat-shell";
import { get, send, errMsg } from "@/lib/http";
import { Btn } from "@/components/ui";

/**
 * The empty state for a new conversation.
 *
 * Its job is to answer two questions before the user types anything: which bot
 * am I talking to, and which project am I in. Both are answered from state the
 * Gateway supplied, and both branches are written out rather than composed from
 * optional fragments — dropping the project clause would read as "there is no
 * project", which is a claim the client cannot make on the user's behalf.
 *
 * **The starter actions issue real requests.** Each one is a genuine route with
 * a genuine body, the button shows the route it will call, and the result panel
 * shows the server's own response or the server's own error. There is no
 * "start a run" button here: the run boundary takes a real prompt, and a button
 * that invented one would be a request nobody made.
 *
 * The response is rendered verbatim rather than summarised, because a summary
 * is where a fabricated count would come from. Nothing in this panel counts
 * anything: it either ran a route and shows what came back, or it says the
 * route failed.
 */
export function ChatShellLanding(props: {
  botName: string | null;
  projectId: string | null;
  projectName: string | null;
  /** Called with the prompt the user picked, so the composer can be seeded. */
  onPickStarter?: (prompt: string) => void;
}) {
  const { botName, projectId, projectName, onPickStarter } = props;
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<{ route: string; ok: boolean; text: string } | null>(null);

  const sentence = contextSentence({
    botName,
    projectName,
    conversationTitle: null,
  });
  const actions = starterActions(projectId);

  const run = async (action: StarterAction) => {
    setBusy(action.id);
    setResult(null);
    try {
      const payload = await issue(action);
      setResult({
        route: action.route,
        ok: true,
        text: renderPayload(payload),
      });
    } catch (error) {
      setResult({ route: action.route, ok: false, text: errMsg(error) });
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="max-w-2xl mx-auto w-full space-y-3" data-shell="landing">
      <p className="text-[11px] text-muted-foreground" data-shell="landing-sentence">
        {sentence.text}
      </p>

      <div>
        <p className="text-[11px] font-semibold mb-1">Starter actions</p>
        <p className="text-[10px] text-muted-foreground mb-1.5">
          Each of these is a real request to this Gateway, not a canned response. The route is printed on each button.
        </p>
        <div className="flex flex-wrap gap-1.5">
          {actions.map((action) => (
            <button
              key={action.id}
              type="button"
              onClick={() => void run(action)}
              disabled={busy !== null}
              title={`${action.route} — ${action.hint}`}
              className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-border/70 text-[11px] font-medium hover:border-primary/50 hover:bg-muted/50 disabled:opacity-40 text-left"
            >
              {busy === action.id ? <Loader2 className="size-3 animate-spin" /> : <PlayCircle className="size-3 text-muted-foreground" />}
              <span className="flex flex-col leading-tight">
                <span>{action.label}</span>
                <code className="text-[9px] text-muted-foreground font-mono">{action.route}</code>
              </span>
            </button>
          ))}
        </div>
      </div>

      {result && (
        <div
          className={`rounded-xl border px-2.5 py-2 text-left ${result.ok ? "border-border/60 bg-card/60" : "border-destructive/40 bg-destructive/5"}`}
          role={result.ok ? undefined : "alert"}
          data-read={result.ok ? "ok" : "failed"}
        >
          <p className="text-[10px] font-semibold">
            <code className="font-mono">{result.route}</code>{" "}
            {result.ok ? (
              <span className="text-muted-foreground font-normal">returned</span>
            ) : (
              <span className="text-destructive">did not answer</span>
            )}
          </p>
          <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-words text-[10px] text-muted-foreground font-mono">
            {result.text}
          </pre>
        </div>
      )}

      {onPickStarter && (
        <div>
          <p className="text-[11px] font-semibold mb-1">Or start from a prompt</p>
          <div className="flex flex-wrap gap-1.5">
            {["What is this project for?", "What has been decided so far?"].map((prompt) => (
              <Btn
                key={prompt}
                variant="ghost"
                onClick={() => onPickStarter(prompt)}
                title="Puts this text in the composer. Nothing is sent until you press send."
              >
                {prompt}
              </Btn>
            ))}
          </div>
        </div>
      )}

      {botName && (
        <p className="text-[10px] text-muted-foreground/80">
          Talking to {botName}. Switch specialists from the bot rail on the left, or the selector in the header above.
        </p>
      )}
    </div>
  );
}

/** Issue the request a starter action declares. Nothing is invented here. */
async function issue(action: StarterAction): Promise<unknown> {
  return action.method === "POST"
    ? send<unknown>(action.path, action.method, action.body)
    : get<unknown>(action.path);
}

/**
 * Render a payload without summarising it.
 *
 * A summary is where a fabricated number would come from, so the response is
 * shown as the server sent it. An empty array is printed as `[]`, which says
 * "the server returned no rows" — an honest, small claim — rather than being
 * smoothed into prose that reads like a count.
 */
function renderPayload(payload: unknown): string {
  if (payload === null) return "null (the server sent an empty body)";
  if (typeof payload === "string") return payload;
  try {
    return JSON.stringify(payload, null, 2);
  } catch {
    return String(payload);
  }
}
