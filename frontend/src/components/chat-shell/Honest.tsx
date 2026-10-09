"use client";

import React from "react";
import { Bot } from "lucide-react";
import { DEFAULT_AGENT_AVATAR, DEFAULT_AGENT_HINT, DEFAULT_AGENT_NAME } from "@/lib/default-agent";
import {
  botStatusText,
  countText,
  countTitle,
  isMeasured,
  presenceDotClass,
  presenceTitle,
  type Count,
  type PresenceState,
  type PresenceView,
} from "@/lib/chat-shell";

/**
 * The only place in the chat shell where a number, a dot or a status becomes
 * markup.
 *
 * Every other component in `components/chat-shell/` renders one of these rather
 * than interpolating a value itself. That is what makes the honesty rule
 * testable: `renderToStaticMarkup` on this file proves what the unknown case
 * looks like, and a component that bypasses it would have to be caught by a
 * source pin instead.
 *
 * Two distinctions are load-bearing and are never collapsed:
 *
 *   - **not reported** and **0** are different. A count cell renders
 *     "not reported" in muted text for the first and a bold `0` for the second,
 *     because the Gateway really does send `0` for a bot or project that has
 *     never run, and turning that into "not reported" would be a different lie.
 *   - **a failed read** and **an empty list** are different. `Failed` renders
 *     the server's reason in destructive styling; an empty list renders a
 *     neutral sentence saying the server reported nothing.
 */

/** A count, or the explicit reason the Gateway did not report one. */
export function MeasuredCount(props: {
  value: Count | number | null | undefined;
  /** Which API field this is, for the tooltip. */
  source: string;
  className?: string;
}) {
  const measured = isMeasured(props.value);
  return (
    <span className={props.className} title={countTitle(props.value, props.source)} data-measured={measured ? "yes" : "no"}>
      {measured ? (
        <span className="font-semibold tabular-nums">{(props.value as number).toString()}</span>
      ) : (
        <span className="text-muted-foreground italic">{countText(props.value)}</span>
      )}
    </span>
  );
}

/** A count on its own line, with its label above it. */
export function CountTile(props: {
  label: string;
  value: Count | number | null | undefined;
  source: string;
}) {
  const measured = isMeasured(props.value);
  return (
    <div className="rounded-lg border border-border/60 bg-card px-2.5 py-1.5">
      <p className="text-[10px] text-muted-foreground">{props.label}</p>
      <p
        className={`text-sm tabular-nums ${measured ? "font-semibold" : "text-muted-foreground italic font-normal text-xs"}`}
        title={countTitle(props.value, props.source)}
        data-measured={measured ? "yes" : "no"}
      >
        {countText(props.value)}
      </p>
    </div>
  );
}

/**
 * The presence dot.
 *
 * `unrecorded` draws a hollow ring, never a filled dot in any colour. A filled
 * dot is a claim that the Gateway told us something about this bot's presence;
 * when it told us nothing, the honest glyph is the absence of a claim.
 */
export function PresenceDot(props: {
  view: PresenceView;
  windowSeconds?: number;
  className?: string;
}) {
  return (
    <span
      className={`inline-block size-2 rounded-full shrink-0 ${presenceDotClass(props.view.state)} ${props.className ?? ""}`}
      title={presenceTitle(props.view, props.windowSeconds)}
      aria-hidden="true"
      data-presence={props.view.state}
    />
  );
}

/** The dot plus the sentence that says which reading it is. */
export function PresenceLine(props: { view: PresenceView; windowSeconds?: number; className?: string }) {
  return (
    <span className={`inline-flex items-center gap-1.5 text-[10px] text-muted-foreground ${props.className ?? ""}`} title={presenceTitle(props.view, props.windowSeconds)}>
      <PresenceDot view={props.view} windowSeconds={props.windowSeconds} />
      <span className="truncate">{props.view.label}</span>
    </span>
  );
}

/**
 * A bot's status, as the server's own word.
 *
 * Deliberately uncoloured. `lib/bots.ts` substitutes the string "active" for a
 * roster row that carried no `status` key, so a green badge here would assert a
 * verdict the read did not support; the literal word is shown instead and a
 * reader can see "active" whether it came from the server or from that default.
 */
export function StatusWord(props: { status: string | null | undefined; className?: string }) {
  const text = botStatusText(props.status);
  return (
    <span className={`text-[10px] text-muted-foreground ${props.className ?? ""}`} title="The Gateway's own status string, shown verbatim. A colour here would assert a liveness verdict the read did not support.">
      {text}
    </span>
  );
}

/**
 * A read that failed. Says so, in the server's own words.
 *
 * An empty list and a failed read are opposite claims, and a panel that cannot
 * tell them apart is worse than a panel that shows nothing.
 */
export function Failed(props: { what: string; reason: string; onRetry?: () => void }) {
  return (
    <div className="rounded-lg border border-destructive/40 bg-destructive/5 px-2.5 py-2" role="alert" data-read="failed">
      <p className="text-[11px] font-semibold text-destructive">{props.what} could not be read</p>
      <p className="text-[11px] text-destructive/90 mt-0.5 break-words">{props.reason}</p>
      {props.onRetry && (
        <button type="button" onClick={props.onRetry} className="mt-1.5 px-2 py-1 rounded-lg border border-border text-[11px] font-medium hover:bg-muted">
          Try again
        </button>
      )}
    </div>
  );
}

/**
 * A source the Gateway does not expose.
 *
 * This is not an empty section and must never be drawn as one. Saying "this
 * project has no files" when the truth is "the Gateway exposes no project-scoped
 * file route" is the exact defect a permanently-empty panel creates, so the
 * shell states the absence and the reason instead.
 */
export function SourceUnavailable(props: { what: string; reason: string; route?: string }) {
  return (
    <div className="rounded-lg border border-dashed border-border bg-muted/30 px-2.5 py-2" data-read="unavailable">
      <p className="text-[11px] font-semibold text-muted-foreground">{props.what} is not available from the Gateway</p>
      <p className="text-[11px] text-muted-foreground/90 mt-0.5">
        {props.reason}
        {props.route ? (
          <>
            {" "}
            No <code className="font-mono text-[10px]">{props.route}</code> route exists, so this is not an empty list and no count is shown.
          </>
        ) : null}
      </p>
    </div>
  );
}

/** A section that is genuinely empty, worded as the server's own answer. */
export function ServerSaidNothing(props: { what: string }) {
  return (
    <p className="text-[11px] text-muted-foreground" data-read="empty">
      {props.what}
    </p>
  );
}

/** A loading section. Never rendered as an empty one. */
export function Loading(props: { what: string }) {
  return (
    <p className="text-[11px] text-muted-foreground animate-pulse" data-read="loading">
      {props.what}
    </p>
  );
}

/**
 * A bot's avatar tile, with the presence dot attached to it.
 *
 * `botInitials` derives from the display name the server sent; there is no
 * image asset to be wrong about, so no branding rule is engaged here.
 */
export function BotGlyph(props: { initials: string; avatar: string; presence: PresenceView; windowSeconds?: number; className?: string }) {
  return (
    <span className={`relative inline-flex size-6 shrink-0 items-center justify-center rounded-md bg-primary/10 text-[10px] font-bold text-primary overflow-visible ${props.className ?? ""}`}>
      <span className="truncate">{props.avatar || props.initials}</span>
      <span className="absolute -bottom-0.5 -right-0.5">
        <PresenceDot view={props.presence} windowSeconds={props.windowSeconds} className="ring-2 ring-card rounded-full" />
      </span>
    </span>
  );
}

/**
 * The default agent's avatar — so "no specialist selected" is a named state
 * with a face, not an absence: Alpha, the lion.
 */
export function LeadGlyph(props: { className?: string }) {
  return (
    <span
      className={`inline-flex size-6 shrink-0 items-center justify-center rounded-md bg-secondary overflow-hidden ${props.className ?? ""}`}
      title={DEFAULT_AGENT_HINT}
    >
      {/* Decorative: every site that renders this glyph puts the agent's name
          beside it, so `alt=""` avoids the screen reader saying the name
          twice. */}
      <img src={DEFAULT_AGENT_AVATAR} alt="" className="size-6 shrink-0 rounded-md object-cover" />
    </span>
  );
}

export type { PresenceState };
