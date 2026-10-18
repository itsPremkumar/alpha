"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Send } from "lucide-react";

import { Btn, inputCls } from "@/components/ui";
import {
  applyMention,
  filterMentionRows,
  findMentionAtCaret,
  type MentionRow,
} from "@/lib/agent-mentions";
import { errMsg } from "@/lib/http";
import { previewMentions, type MentionPreviewResponse } from "@/lib/groups-profile";
import { fetchGroupRoster, postGroupMessage, type GroupRoster } from "@/lib/teamops";

/**
 * The group-room `@` composer (Bot Mode 2.6).
 *
 * Three things the plain input could not say, all shown BEFORE the send:
 *
 *  - **Autocomplete from the room's own roster** — the palette offers exactly
 *    the handles `GET /groups/{name}/roster` reports, because that roster is
 *    the grammar's own input. A suggestion the server cannot resolve is a
 *    button that can only fail, so none are invented here.
 *  - **A live preview of the resolution** — the server's answer (chips for
 *    each mention span, the unresolved tokens with their reasons), not a
 *    client-side guess. The client mirrors the grammar for the chat composer;
 *    here the server is the only voice, so a disagreement is a bug and the
 *    preview says so instead of smoothing over it.
 *  - **The routing decision** — `next_speakers` from the same orchestrator
 *    method the landed message goes through, so "will route to X" is a fact
 *    about the room's mode, not a hope.
 */

/** The room's routing mode beside the composer: name plus what it means. */
const MODE_SENTENCES: Record<string, string> = {
  mention: "Mention-driven — addressed bots speak next; an unaddressed human message invites the moderator.",
  moderated: "Moderated — the moderator synthesises, then dispatches.",
  quorum: "Quorum — every member except the speaker deliberates.",
  parallel: "Parallel — every member except the speaker responds at once.",
  round_robin: "Round-robin — the turn passes to the next member.",
};

type PreviewState =
  | { status: "idle" }
  | { status: "checking" }
  | { status: "ok"; data: MentionPreviewResponse }
  | { status: "error"; reason: string };

/** How the room will route this draft, in one line. */
function routingLine(data: MentionPreviewResponse): { text: string; tone: "good" | "warn" | "neutral" } {
  const speakers = data.next_speakers ?? [];
  if (speakers.length > 0) return { text: `Will route to: ${speakers.join(", ")}`, tone: "good" };
  if ((data.resolved_handles?.length ?? 0) > 0)
    return { text: "These tags resolved, but the room will not route on them.", tone: "warn" };
  if (data.mode === "mention") return { text: "No route — an unaddressed human message invites the moderator.", tone: "neutral" };
  return { text: "This mode decides the next speaker when the message lands.", tone: "neutral" };
}

/** The draft with each server-resolved mention span rendered as a chip. */
function DraftWithChips({ text, spans }: { text: string; spans: MentionPreviewResponse["spans"] }) {
  const parts: React.ReactNode[] = [];
  let cursor = 0;
  const sorted = [...spans].sort((a, b) => a.start - b.start).filter((span) => span.end > span.start);
  sorted.forEach((span, index) => {
    const start = Math.min(Math.max(span.start, 0), text.length);
    const end = Math.min(Math.max(span.end, start), text.length);
    if (start > cursor) parts.push(text.slice(cursor, start));
    parts.push(
      <span key={`${start}-${index}`} className="rounded bg-primary/20 px-1 py-0.5 font-mono text-primary">
        {text.slice(start, end)}
      </span>,
    );
    cursor = end;
  });
  if (cursor < text.length) parts.push(text.slice(cursor));
  return <span className="break-words">{parts}</span>;
}

export function MentionComposer({
  room,
  mode,
  moderator,
  onSent,
}: {
  room: string;
  /** The room's routing mode, `null` when the Gateway did not report one. */
  mode: string | null;
  moderator: string | null;
  /** Reload the room's message list after a successful send. */
  onSent: () => void | Promise<void>;
}) {
  const [draft, setDraft] = useState("");
  const [caret, setCaret] = useState(0);
  const [roster, setRoster] = useState<GroupRoster | null>(null);
  const [rosterError, setRosterError] = useState<string | null>(null);
  const [preview, setPreview] = useState<PreviewState>({ status: "idle" });
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // The roster is the grammar's input; without it the palette has nothing
  // honest to offer, and the preview still works (it is server-side).
  useEffect(() => {
    let alive = true;
    setRosterError(null);
    fetchGroupRoster(room)
      .then((result) => {
        if (alive) setRoster(result);
      })
      .catch((err: unknown) => {
        if (alive) setRosterError(errMsg(err));
      });
    return () => {
      alive = false;
    };
  }, [room]);

  // The autocomplete rows: one per effective handle, plus @everyone. Built from
  // the roster only — a handle the room does not list is not a suggestion.
  const rows = useMemo<MentionRow[]>(() => {
    if (!roster?.effective) return [];
    const direct = new Set(roster.direct ?? []);
    const handles = roster.effective;
    const built: MentionRow[] = handles.map((handle) => ({
      id: `bot:${handle}`,
      mode: "mention" as const,
      token: `@bot:${handle}`,
      title: handle,
      subtitle: direct.has(handle) ? "direct member" : "inherited or rule-matched",
      badge: `@${handle}`,
      avatar: "",
      status: null,
      resolves: [handle],
      refusal: null,
    }));
    built.push({
      id: "everyone",
      mode: "everyone" as const,
      token: "@everyone",
      title: "@everyone",
      subtitle: handles.length === 1 ? "1 agent" : `${handles.length} agents`,
      badge: "everyone",
      avatar: "",
      status: null,
      resolves: handles,
      refusal: handles.length === 0 ? "The roster is empty, so this tag would address nobody." : null,
    });
    return built;
  }, [roster]);

  const trigger = useMemo(() => findMentionAtCaret(draft, caret), [draft, caret]);
  const palette = useMemo(() => {
    if (!trigger) return null;
    const { visible, hidden } = filterMentionRows(rows, trigger);
    return { visible, hidden };
  }, [trigger, rows]);

  // The preview is the server's answer about THIS draft, debounced so every
  // keystroke does not become a request. A newer draft replaces an older
  // response only when its own request settles (the effect cleanup).
  useEffect(() => {
    if (!draft.trim()) {
      setPreview({ status: "idle" });
      return;
    }
    setPreview({ status: "checking" });
    const timer = setTimeout(() => {
      previewMentions(room, draft, "user")
        .then((data) => setPreview({ status: "ok", data }))
        .catch((err: unknown) => setPreview({ status: "error", reason: errMsg(err) }));
    }, 250);
    return () => clearTimeout(timer);
  }, [draft, room]);

  const choose = (row: MentionRow) => {
    if (!trigger) return;
    const next = applyMention(draft, trigger, row);
    setDraft(next.text);
    setCaret(next.caret);
    inputRef.current?.focus();
  };

  const send = async () => {
    if (sending || !draft.trim()) return;
    setSending(true);
    setError(null);
    setNotice(null);
    try {
      await postGroupMessage(room, draft.trim());
      setDraft("");
      setNotice("Sent — the room routed it the way the preview said it would.");
      await onSent();
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setSending(false);
    }
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    if (palette && palette.visible.length > 0) choose(palette.visible[0]);
    else void send();
  };

  const modeSentence = mode ? MODE_SENTENCES[mode] : null;

  return (
    <div className="space-y-2" data-panel="mention-composer">
      <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
        <span>
          Routing mode:{" "}
          <span className={`font-mono ${mode ? "text-foreground" : "italic"}`}>{mode || "not reported"}</span>
          {moderator ? ` · moderator: ${moderator}` : ""}
        </span>
        {modeSentence ? (
          <span className="italic opacity-80" title={modeSentence}>
            {modeSentence}
          </span>
        ) : mode ? (
          <span className="italic opacity-80">This build has not named that mode — the preview below is the room&apos;s own answer.</span>
        ) : null}
      </div>

      <div className="relative">
        <div className="flex gap-2">
          <input
            ref={inputRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
            onSelect={(e) => setCaret(e.currentTarget.selectionStart ?? 0)}
            onKeyUp={(e) => setCaret(e.currentTarget.selectionStart ?? 0)}
            onClick={(e) => setCaret(e.currentTarget.selectionStart ?? 0)}
            placeholder="Message the room… use @coder to address a bot"
            maxLength={20000}
            className={inputCls}
            aria-label={`Message ${room}`}
          />
          <Btn onClick={() => void send()} disabled={sending || !draft.trim()}>
            <Send className="size-3.5" aria-hidden="true" />
            <span className="sr-only">Send message to {room}</span>
          </Btn>
        </div>

        {palette && (
          <div className="absolute left-0 right-16 z-20 mt-1 max-h-64 overflow-y-auto rounded-xl border border-border/60 bg-card shadow-lg">
            {rosterError && (
              <p className="border-b border-border/40 px-3 py-1.5 text-[10px] text-destructive">
                The roster could not be read ({rosterError}) — the palette is incomplete, the preview is not.
              </p>
            )}
            {palette.visible.length === 0 && !rosterError && (
              <p className="px-3 py-2 text-[11px] text-muted-foreground">
                No roster entry matches — an unknown handle will not route.
              </p>
            )}
            {palette.visible.map((row) => (
              <button
                key={row.id}
                type="button"
                disabled={row.refusal !== null}
                onClick={() => choose(row)}
                title={row.refusal ?? row.subtitle}
                className="flex w-full items-center justify-between gap-2 px-3 py-1.5 text-left text-xs hover:bg-muted/40 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <span className="min-w-0">
                  <span className="font-mono">{row.title}</span>
                  <span className="ml-1.5 text-[10px] text-muted-foreground">{row.subtitle}</span>
                  {row.refusal && <span className="block text-[10px] text-destructive">{row.refusal}</span>}
                </span>
                <span className="shrink-0 font-mono text-[10px] text-muted-foreground">{row.badge}</span>
              </button>
            ))}
            {palette.hidden > 0 && (
              <p className="border-t border-border/40 px-3 py-1 text-[10px] text-muted-foreground">
                {palette.hidden} more match — keep typing to narrow.
              </p>
            )}
          </div>
        )}
      </div>

      {preview.status === "checking" && (
        <p className="text-[11px] text-muted-foreground" role="status">
          Checking with the room…
        </p>
      )}
      {preview.status === "error" && (
        <p className="text-[11px] text-destructive" role="alert">
          The room could not be asked how this routes: <span className="font-mono">{preview.reason}</span>. The send
          still goes through — the server remains the authority.
        </p>
      )}
      {preview.status === "ok" && preview.data && (
        <div className="space-y-1 rounded-lg border border-border/50 bg-muted/20 px-3 py-2">
          {preview.data.text.trim() && (
            <p className="text-[11px] text-muted-foreground">
              <DraftWithChips text={preview.data.text} spans={preview.data.spans} />
            </p>
          )}
          <p className="text-[11px] font-medium text-foreground">{routingLine(preview.data).text}</p>
          {preview.data.unresolved.length > 0 && (
            <div className="space-y-0.5">
              {preview.data.unresolved.map((u) => (
                <p key={u.raw} className="text-[11px] text-destructive">
                  <span className="font-mono">{u.raw}</span> will not route: {u.reason}
                </p>
              ))}
            </div>
          )}
          {preview.data.ok && preview.data.unresolved.length === 0 && (
            <p className="text-[11px] text-muted-foreground">Every tag in this draft resolves.</p>
          )}
        </div>
      )}

      {error && (
        <p className="text-[11px] text-destructive" role="alert">
          The message could not be sent: <span className="font-mono">{error}</span>
        </p>
      )}
      {notice && <p className="text-[11px] text-emerald-300">{notice}</p>}
    </div>
  );
}
