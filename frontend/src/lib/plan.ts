import { send } from "./http";

/** 8-dimension strategic review of a goal. Returns raw server data. */
export async function evaluatePlan(prompt: string): Promise<Record<string, unknown>> {
  return send<Record<string, unknown>>("/plan-mode/evaluate", "POST", { prompt });
}

/** Review AND immediately run the plan across subsystems (needs admin). */
export async function dispatchPlan(prompt: string): Promise<Record<string, unknown>> {
  return send<Record<string, unknown>>("/plan-mode/dispatch", "POST", { prompt });
}

/** Point the live browser tab at a URL (needs browser control enabled + a chat). */
export async function navigateBrowser(threadId: string, url: string): Promise<Record<string, unknown>> {
  return send<Record<string, unknown>>(`/threads/${encodeURIComponent(threadId)}/browser/navigate`, "POST", { url });
}
