// ChatShellLanding.tsx — the empty-chat landing.
//
// WHAT THIS SURFACE IS FOR: it is the screen you see before the first message,
// so it has one job — say hello and get out of the way. It used to also carry a
// paragraph naming the operator, the specialist and the project, a "How can I
// help you today?" line, and four starter cards. Together those took the whole
// viewport and pushed the transcript and composer below the fold, while the
// agent's name and role were printed two or three times in the same screen (the
// chat header already names the specialist, and the starter block named it
// again). The sentences also carried a setup instruction aimed at a developer
// ("Set your name in Settings to be greeted by it") and jargon ("standalone
// mode") that meant nothing to the person reading it.
//
// So the landing is now the avatar and the greeting, nothing else. Switching
// specialists lives in the chat header, where the specialist is already named.
"use client";

import React from "react";
import { DEFAULT_AGENT_AVATAR, DEFAULT_AGENT_NAME } from "@/lib/default-agent";
import { landingGreeting, operatorIdentity } from "@/lib/operator";

export interface ChatShellLandingProps {
  /**
   * The specialist in play, or `null` for the default agent.
   *
   * These props are still accepted because the caller passes them, but this
   * surface no longer narrates them: the chat header names the specialist and
   * the composer is the only thing an empty chat needs to offer.
   */
  botName: string | null;
  botRole?: string | null;
  botAvatar?: string | null;
  projectId: string | null;
  projectName: string | null;
  /**
   * The operator's own name, or `null` when none is configured.
   *
   * This used to default to a developer's initials, so a first-time visitor was
   * greeted by name as someone who had never used the product. The Gateway
   * authenticates requests but exposes no display name, so the honest default is
   * "no name" and the greeting degrades to a first-run line. See
   * `lib/operator.ts` for the single place a name is resolved.
   */
  userName?: string | null;
  /**
   * Whether a previous session actually exists.
   *
   * Gates the "Welcome back" phrasing, which the old build printed
   * unconditionally — a false claim on a first run. Callers derive this from
   * real history (a non-empty conversation list); it defaults to `false` so the
   * default can never overstate familiarity.
   */
  returning?: boolean;
  /** Accepted and ignored: starter prompts are no longer offered here. */
  onPickStarter?: (prompt: string) => void;
  /** Accepted and ignored: the project inspector is reachable from elsewhere. */
  onReviewProject?: () => void;
}

export function ChatShellLanding(props: ChatShellLandingProps) {
  const { botAvatar, userName = null, returning = false } = props;

  return (
    /* `justify-center` was the clipping cause, not `my-*`. A flex container
       taller than its scroller centres its content by overflowing *both* ends,
       and the top overflow is unreachable — the transcript scroller is
       `flex-1 overflow-y-auto`, so the greeting was sliced in half on any
       viewport where the landing block exceeded the available height. Measured:
       scroller clientH 219 vs scrollH 652.

       `my-auto` on a column flex item resolves to 0 in the cross axis (height is
       not stretched), so it centres nothing and the `py-8` already present keeps
       the spacing the design intends. `justify-start` keeps the top reachable and
       `my-auto` centres when there *is* spare room. */
    <div
      className="max-w-2xl mx-auto w-full flex flex-col items-center justify-start text-center py-8 space-y-6 select-none my-auto"
      data-shell="landing"
    >
      {/* ── Hero Avatar Icon with Subtle Glow ─────────────────────── */}
      <div className="relative group">
        <div className="absolute -inset-1 rounded-full bg-gradient-to-r from-blue-600 via-indigo-600 to-purple-600 opacity-40 blur-lg group-hover:opacity-75 transition-opacity" />
        {/* `size-18` is NOT in Tailwind's default spacing scale (it steps
            14 -> 16 -> 20) and `tailwind.config.cjs` extends only `colors` and
            `borderRadius`, so the class generated nothing at all. The circle
            silently collapsed to its content's size while the `blur-lg` glow
            sized itself around it — the largest element on the empty state
            rendering wrong with no lint, no build error and no test. An
            arbitrary value keeps the intended 4.5rem without inventing a new
            theme scale. `lib/tailwind-class-guard.test.mjs` pins this. */}
        <div className="relative size-[4.5rem] rounded-full bg-gradient-to-br from-indigo-600 to-blue-600 text-white flex items-center justify-center elev-3 border border-white/20">
          {botAvatar ? (
            <span className="text-3xl">{botAvatar}</span>
          ) : (
            /* No specialist picked: the default agent's own face, not a
               generic code glyph. */
            <img
              src={DEFAULT_AGENT_AVATAR}
              alt={DEFAULT_AGENT_NAME}
              className="size-full rounded-md object-cover"
            />
          )}
        </div>
      </div>

      {/* The greeting is the whole message. Whether it says "Welcome back" is
          decided by `landingGreeting`, which only claims a previous session when
          one is real. */}
      <h2 className="text-2xl sm:text-3xl font-bold tracking-tight text-foreground">
        {landingGreeting(operatorIdentity(userName), returning)}
      </h2>
    </div>
  );
}