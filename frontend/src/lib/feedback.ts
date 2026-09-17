import { get, send, asList, pick } from "./http";

export async function rateMessage(
  threadId: string,
  runId: string,
  rating: 1 | -1,
  category?: string
): Promise<void> {
  const body: Record<string, unknown> = { rating };
  if (category) body.category = category;
  try {
    await send(`/threads/${encodeURIComponent(threadId)}/runs/${encodeURIComponent(runId)}/feedback`, "PUT", body);
  } catch {
    await send(`/threads/${encodeURIComponent(threadId)}/runs/${encodeURIComponent(runId)}/feedback`, "POST", body);
  }
}

export async function fetchFeedback(
  threadId: string,
  runId: string
): Promise<Array<Record<string, unknown>>> {
  try {
    const d = await get<unknown>(
      `/threads/${encodeURIComponent(threadId)}/runs/${encodeURIComponent(runId)}/feedback`
    );
    return asList(d, ["feedback", "data"]);
  } catch {
    return [];
  }
}

export interface FeedbackStats {
  positive: number;
  negative: number;
  byCategory: Record<string, { positive: number; negative: number }>;
}

export async function fetchFeedbackStats(threadId: string, runId: string): Promise<FeedbackStats | null> {
  try {
    const d = await get<Record<string, unknown>>(
      `/threads/${encodeURIComponent(threadId)}/runs/${encodeURIComponent(runId)}/feedback/stats`
    );
    return {
      positive: Number(pick(d, ["positive", "up"], 0)),
      negative: Number(pick(d, ["negative", "down"], 0)),
      byCategory: (d.by_category as FeedbackStats["byCategory"]) ?? {},
    };
  } catch {
    return null;
  }
}
