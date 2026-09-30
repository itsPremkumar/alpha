"use client";

import React from "react";
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
import { Btn } from "@/components/ui";

export interface ChatShellLandingProps {
  botName: string | null;
  botRole?: string | null;
  botAvatar?: string | null;
  projectId: string | null;
  projectName: string | null;
  userName?: string;
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
    userName = "MK",
    onPickStarter,
    onReviewProject,
  } = props;

  const sentence = contextSentence({
    botName,
    projectName,
    conversationTitle: null,
  });

  return (
    <div className="max-w-2xl mx-auto w-full flex flex-col items-center justify-center text-center py-8 space-y-6 select-none" data-shell="landing">
      {/* ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ 1. Hero Avatar Icon with Subtle Glow ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ */}
      <div className="relative group">
        <div className="absolute -inset-1 rounded-full bg-gradient-to-r from-blue-600 via-indigo-600 to-purple-600 opacity-40 blur-lg group-hover:opacity-75 transition-opacity" />
        <div className="relative size-18 rounded-full bg-gradient-to-br from-indigo-600 to-blue-600 text-white flex items-center justify-center shadow-xl border border-white/20">
          {botAvatar ? (
            <span className="text-3xl">{botAvatar}</span>
          ) : (
            <Code2 className="size-9 text-white" />
          )}
        </div>
      </div>

      {/* ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ 2. Greeting & Context ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ */}
      <div className="space-y-1.5 max-w-lg">
        <h2 className="text-2xl sm:text-3xl font-bold tracking-tight text-foreground">
          Welcome back, {userName}!
        </h2>
        <p className="text-xs text-muted-foreground leading-relaxed">
          You are working with{" "}
          <span className="text-foreground font-semibold">{botName || "Lead Agent"}</span>
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

      {/* ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ 3. 2x2 Feature Discovery Card Grid (from Reference Mockup) ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 w-full max-w-xl text-left pt-1">
        {/* Card 1: Create a new plan */}
        <button
          type="button"
          onClick={() => onPickStarter?.("/plan ")}
          className="group p-4 rounded-2xl border border-border/70 bg-card/60 hover:bg-card hover:border-primary/50 transition-all flex items-center justify-between shadow-2xs hover:shadow-md cursor-pointer"
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
          onClick={() => onPickStarter?.("Write or improve code for ")}
          className="group p-4 rounded-2xl border border-border/70 bg-card/60 hover:bg-card hover:border-primary/50 transition-all flex items-center justify-between shadow-2xs hover:shadow-md cursor-pointer"
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
          className="group p-4 rounded-2xl border border-border/70 bg-card/60 hover:bg-card hover:border-primary/50 transition-all flex items-center justify-between shadow-2xs hover:shadow-md cursor-pointer"
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
          className="group p-4 rounded-2xl border border-border/70 bg-card/60 hover:bg-card hover:border-primary/50 transition-all flex items-center justify-between shadow-2xs hover:shadow-md cursor-pointer"
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

