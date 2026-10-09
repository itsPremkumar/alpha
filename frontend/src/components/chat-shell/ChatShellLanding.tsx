"use client";

import React from "react";
import { DEFAULT_AGENT_AVATAR, DEFAULT_AGENT_NAME } from "@/lib/default-agent";
import {
  Code2,
  FileText,
  FolderCheck,
  HelpCircle,
  ChevronRight,
  Sparkles,
  Bot,
} from "lucide-react";
import { contextSentence } from "@/lib/chat-shell";
import { ANONYMOUS_LABEL, landingGreeting, operatorIdentity } from "@/lib/operator";
import { Btn } from "@/components/ui";

export interface ChatShellLandingProps {
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
  /** Called with the prompt the user picked, so the composer can be seeded. */
  onPickStarter?: (prompt: string) => void;
  onReviewProject?: () => void;
}

export function ChatShellLanding(props: ChatShellLandingProps) {
  const {
    botName,
    botRole,
    botAvatar,
    projectId,
    projectName,
    userName = null,
    returning = false,
    onPickStarter,
    onReviewProject,
  } = props;

  const sentence = contextSentence({
    botName,
    projectName,
    conversationTitle: null,
  });

  return (
    /* `justify-center` was the clipping cause, not `my-*`. A flex container
       taller than its scroller centres its content by overflowing *both* ends,
       and the top overflow is unreachable — the transcript scroller is
       `flex-1 overflow-y-auto`, so the greeting and "How can I help you today?"
       were sliced in half on any viewport where the landing block exceeds the
       available height. Measured: scroller clientH 219 vs scrollH 652.

       `my-auto` on a column flex item resolves to 0 in the cross axis (height is
       not stretched), so it centres nothing and the `py-8` already present keeps
       the spacing the design intends. `justify-start` keeps the top reachable and
       `my-auto` centres when there *is* spare room. */
    <div className="max-w-2xl mx-auto w-full flex flex-col items-center justify-start text-center py-8 space-y-6 select-none my-auto" data-shell="landing">
      {/* ── 1. Hero Avatar Icon with Subtle Glow ─────────────────────── */}
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
            <img src={DEFAULT_AGENT_AVATAR} alt={DEFAULT_AGENT_NAME} className="size-9 rounded-full object-cover" />
          )}
        </div>
      </div>

      {/* ── 2. Greeting & Context ────────────────────────────────────── */}
      <div className="space-y-1.5 max-w-lg">
        <h2 className="text-2xl sm:text-3xl font-bold tracking-tight text-foreground">
          {landingGreeting(operatorIdentity(userName), returning)}
        </h2>
        <p className="text-xs text-muted-foreground leading-relaxed">
          {userName ? (
            <>
              Signed in as{" "}
              <span className="text-foreground font-semibold">{userName}</span>.
            </>
          ) : (
            <>
              Running as the local {ANONYMOUS_LABEL.toLowerCase()}. Set your name in{" "}
              <span className="text-foreground font-semibold">Settings</span> to be greeted by it.
            </>
          )}{" "}
          You are working with{" "}
          <span className="text-foreground font-semibold">{botName || DEFAULT_AGENT_NAME}</span>
          {projectName ? (
            <>
              {" "}on the <span className="text-foreground font-semibold">{projectName}</span> project.
            </>
          ) : (
            " in standalone mode."
          )}
        </p>
        <p className="text-sm font-medium text-foreground/90 pt-1">
          How can I help you today?
        </p>
      </div>

      {/* ── 3. 2x2 Feature Discovery Card Grid (from Reference Mockup) ── */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 w-full max-w-xl text-left pt-1">
        {/* Card 1: Create a new plan.

            These cards used to seed the composer with raw internal command
            strings — `"/plan "` with a trailing space, and
            `"Write or improve code for "` with a dangling preposition. So a
            button labelled in friendly prose dropped a CLI invocation into the
            input box, and an unresolvable command produced a command error from
            a card that had promised a conversation. Each now seeds a complete,
            self-contained prompt the agent can answer as written, and the
            composer shows the operator the text before they send it. */}
        <button
          type="button"
          onClick={() =>
            onPickStarter?.(
              "Create a plan for this work. Break it into concrete steps, call out anything you need from me, and tell me what to start with.",
            )
          }
          className="group p-4 rounded-2xl border border-border/70 bg-card/60 hover:bg-card hover:border-primary/50 transition-all flex items-center justify-between elev-1 hover:elev-2 cursor-pointer"
        >
          <div className="flex items-center gap-3">
            <div className="p-2.5 rounded-xl bg-primary/10 text-primary border border-primary/20 group-hover:bg-primary/20 transition-colors">
              <FileText className="size-5" />
            </div>
            <div>
              <span className="block text-xs font-semibold text-foreground group-hover:text-primary transition-colors">
                Create a new plan
              </span>
              <span className="block text-[11px] text-muted-foreground">
                Plan your project step by step
              </span>
            </div>
          </div>
          <ChevronRight className="size-4 text-muted-foreground group-hover:text-primary transition-transform group-hover:translate-x-1" />
        </button>

        {/* Card 2: Write or improve code */}
        <button
          type="button"
          onClick={() =>
            onPickStarter?.(
              "Write or improve some code. Show me the code, explain what it does, and point out anything you would do differently.",
            )
          }
          className="group p-4 rounded-2xl border border-border/70 bg-card/60 hover:bg-card hover:border-primary/50 transition-all flex items-center justify-between elev-1 hover:elev-2 cursor-pointer"
        >
          <div className="flex items-center gap-3">
            <div className="p-2.5 rounded-xl bg-blue-500/10 text-blue-500 border border-blue-500/20 group-hover:bg-blue-500/20 transition-colors">
              <Code2 className="size-5" />
            </div>
            <div>
              <span className="block text-xs font-semibold text-foreground group-hover:text-primary transition-colors">
                Write or improve code
              </span>
              <span className="block text-[11px] text-muted-foreground">
                Generate, debug or refactor code
              </span>
            </div>
          </div>
          <ChevronRight className="size-4 text-muted-foreground group-hover:text-primary transition-transform group-hover:translate-x-1" />
        </button>

        {/* Card 3: Review the project */}
        <button
          type="button"
          onClick={() => {
            if (onReviewProject) {
              onReviewProject();
            } else {
              onPickStarter?.("Review the current project status, files and logic.");
            }
          }}
          className="group p-4 rounded-2xl border border-border/70 bg-card/60 hover:bg-card hover:border-primary/50 transition-all flex items-center justify-between elev-1 hover:elev-2 cursor-pointer"
        >
          <div className="flex items-center gap-3">
            <div className="p-2.5 rounded-xl bg-purple-500/10 text-purple-500 border border-purple-500/20 group-hover:bg-purple-500/20 transition-colors">
              <FolderCheck className="size-5" />
            </div>
            <div>
              <span className="block text-xs font-semibold text-foreground group-hover:text-primary transition-colors">
                Review the project
              </span>
              <span className="block text-[11px] text-muted-foreground">
                Check files, logic or progress
              </span>
            </div>
          </div>
          <ChevronRight className="size-4 text-muted-foreground group-hover:text-primary transition-transform group-hover:translate-x-1" />
        </button>

        {/* Card 4: Ask a question */}
        <button
          type="button"
          onClick={() => onPickStarter?.("")}
          className="group p-4 rounded-2xl border border-border/70 bg-card/60 hover:bg-card hover:border-primary/50 transition-all flex items-center justify-between elev-1 hover:elev-2 cursor-pointer"
        >
          <div className="flex items-center gap-3">
            <div className="p-2.5 rounded-xl bg-emerald-500/10 text-emerald-500 border border-emerald-500/20 group-hover:bg-emerald-500/20 transition-colors">
              <HelpCircle className="size-5" />
            </div>
            <div>
              <span className="block text-xs font-semibold text-foreground group-hover:text-primary transition-colors">
                Ask a question
              </span>
              <span className="block text-[11px] text-muted-foreground">
                Get help with anything
              </span>
            </div>
          </div>
          <ChevronRight className="size-4 text-muted-foreground group-hover:text-primary transition-transform group-hover:translate-x-1" />
        </button>
      </div>

    </div>
  );
}
