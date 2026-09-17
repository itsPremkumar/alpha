import { send } from "./http";

export type CouncilStrategy = "auto" | "single" | "ensemble" | "council" | "debate";

/** Ask several models/agents to settle a disagreement (debate/council). */
export async function runCouncil(
  prompt: string,
  strategy: CouncilStrategy = "debate",
  maxRounds = 3
): Promise<string> {
  const d = await send<Record<string, unknown>>("/deliberation/run", "POST", {
    prompt,
    strategy,
    max_rounds: maxRounds,
  });
  const pick = (v: unknown): string | null => (typeof v === "string" && v ? v : null);
  const out =
    pick(d.verdict) ?? pick(d.decision) ?? pick(d.result) ?? pick(d.summary) ?? pick(d.output) ?? pick(d.text);
  if (out) return out.slice(0, 6000);
  return JSON.stringify(d, null, 2).slice(0, 6000);
}

/** Quick single-pass review without launching a full council. */
export async function evaluateQuestion(prompt: string): Promise<string> {
  const d = await send<Record<string, unknown>>("/deliberation/evaluate", "POST", { prompt });
  return JSON.stringify(d, null, 2).slice(0, 4000);
}
