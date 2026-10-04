/**
 * Clearly-labeled SAMPLE data for visual verification of the Overview atlas.
 *
 * Every export here is prefixed with SAMPLE_ and carries `isSample: true` at
 * the point of use. It is NEVER mixed silently with live Gateway state: the
 * Overview section renders it only when `?demo=1` is present or when the user
 * explicitly toggles "Preview with sample data" after all live reads failed.
 * The banner in that mode says "Sample data — backend unavailable" in words.
 */

export interface SampleBot {
  name: string;
  display_name: string;
  role: string;
  department: string;
  status: string;
  model: string;
  toolsets: string[];
  skills: string[];
  last_active: string | null;
  reputation_score: number | null;
}

export interface SampleProject {
  id: string;
  name: string;
  status: string;
  members: number;
  threads: number;
  updated_at: string;
}

export interface SampleWorkflow {
  id: string;
  name: string;
  status: string;
  runs: number;
  last_run: string;
}

export interface SampleSkill {
  name: string;
  description: string;
  enabled: boolean;
  source: string;
}

export interface SampleMemoryFact {
  id: string;
  content: string;
  updated_at: string;
}

export interface SampleScheduledTask {
  id: string;
  title: string;
  schedule_type: string;
  status: string;
  next_run: string;
}

export interface SampleChannel {
  name: string;
  enabled: boolean;
  connected: boolean;
  status: string;
}

export interface SampleAgent {
  name: string;
  display_name: string;
  description: string;
  model: string;
}

export const SAMPLE_BOTS: SampleBot[] = [
  {
    name: "atlas-lead",
    display_name: "Atlas Lead",
    role: "Lead Agent — plans, delegates, reviews",
    department: "engineering",
    status: "active",
    model: "union-alpha",
    toolsets: ["shell", "files", "browser"],
    skills: ["planning", "code-review"],
    last_active: "2 min ago (sample)",
    reputation_score: 4.8,
  },
  {
    name: "tester-bot",
    display_name: "Tester",
    role: "QA Specialist — writes and runs tests",
    department: "quality",
    status: "active",
    model: "union-alpha",
    toolsets: ["shell", "tests"],
    skills: ["pytest", "playwright"],
    last_active: "9 min ago (sample)",
    reputation_score: 4.6,
  },
  {
    name: "docs-bot",
    display_name: "Docs",
    role: "Technical Writer — changelogs, guides",
    department: "docs",
    status: "paused",
    model: "union-alpha",
    toolsets: ["files"],
    skills: ["writing"],
    last_active: "1 h ago (sample)",
    reputation_score: null,
  },
];

export const SAMPLE_PROJECTS: SampleProject[] = [
  { id: "sample-shop", name: "Sample Shop Revamp", status: "active", members: 3, threads: 12, updated_at: "today (sample)" },
  { id: "sample-oncall", name: "On-call Triage", status: "active", members: 1, threads: 4, updated_at: "yesterday (sample)" },
];

export const SAMPLE_WORKFLOWS: SampleWorkflow[] = [
  { id: "sample-release", name: "Release checklist", status: "active", runs: 14, last_run: "2 h ago (sample)" },
  { id: "sample-triage", name: "Nightly triage", status: "paused", runs: 31, last_run: "last night (sample)" },
];

export const SAMPLE_SKILLS: SampleSkill[] = [
  { name: "planning", description: "Break a goal into checkable steps.", enabled: true, source: "built-in" },
  { name: "code-review", description: "Review a diff for risk and style.", enabled: true, source: "built-in" },
  { name: "web-search", description: "Search the public web for current facts.", enabled: false, source: "mcp" },
];

export const SAMPLE_FACTS: SampleMemoryFact[] = [
  { id: "sample-1", content: "Team prefers small, reviewable changes.", updated_at: "Mon (sample)" },
  { id: "sample-2", content: "Release window is Fridays 15:00 UTC.", updated_at: "Tue (sample)" },
  { id: "sample-3", content: "Docs live under docs/ and need an index entry.", updated_at: "Wed (sample)" },
];

export const SAMPLE_SCHEDULED: SampleScheduledTask[] = [
  { id: "sample-nightly", title: "Nightly triage", schedule_type: "cron", status: "active", next_run: "02:00 UTC (sample)" },
  { id: "sample-weekly", title: "Weekly changelog", schedule_type: "cron", status: "paused", next_run: "not scheduled (sample)" },
];

export const SAMPLE_CHANNELS: SampleChannel[] = [
  { name: "telegram", enabled: true, connected: true, status: "connected (sample)" },
  { name: "slack", enabled: true, connected: false, status: "needs token (sample)" },
  { name: "discord", enabled: false, connected: false, status: "disabled (sample)" },
];

export const SAMPLE_AGENTS: SampleAgent[] = [
  { name: "release-captain", display_name: "Release Captain", description: "Owns the release checklist end to end.", model: "union-alpha" },
  { name: "support-triage", display_name: "Support Triage", description: "Labels and routes incoming issues.", model: "union-alpha" },
];

export const SAMPLE_RUNS = [
  { run_id: "sample-run-1", thread_title: "Fix login redirect", status: "completed", model: "union-alpha", tokens: 12480 },
  { run_id: "sample-run-2", thread_title: "Draft changelog", status: "completed", model: "union-alpha", tokens: 3210 },
  { run_id: "sample-run-3", thread_title: "Investigate slow query", status: "failed", model: "union-alpha", tokens: 8820 },
];
