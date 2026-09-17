import { get, send, asList, pick } from "./http";

export interface Batch {
  id: string;
  status: string;
  title: string;
}

function toBatch(b: Record<string, unknown>, i: number): Batch {
  return {
    id: String(pick(b, ["id", "batch_id"], `batch-${i}`)),
    status: String(pick(b, ["status", "state"], "unknown")),
    title: String(pick(b, ["title", "objective", "name"], "")),
  };
}

export async function listBatches(threadId: string): Promise<Batch[]> {
  const d = await get<unknown>(`/threads/${encodeURIComponent(threadId)}/subagent-batches?limit=20`);
  return asList(d, ["batches", "data"]).map((b, i) => toBatch(b, i));
}

export interface BatchItem {
  id: string;
  status: string;
  label: string;
}

export async function batchItems(threadId: string, batchId: string): Promise<BatchItem[]> {
  const d = await get<unknown>(
    `/threads/${encodeURIComponent(threadId)}/subagent-batches/${encodeURIComponent(batchId)}/items?limit=100`
  );
  return asList(d, ["items", "data"]).map((it, i) => ({
    id: String(pick(it, ["id", "item_id"], `item-${i}`)),
    status: String(pick(it, ["status", "state"], "unknown")),
    label: String(pick(it, ["label", "title", "objective"], "")),
  }));
}

export async function pauseBatch(threadId: string, batchId: string): Promise<void> {
  await send(`/threads/${encodeURIComponent(threadId)}/subagent-batches/${encodeURIComponent(batchId)}/pause`, "POST", {});
}

export async function resumeBatch(threadId: string, batchId: string): Promise<void> {
  await send(`/threads/${encodeURIComponent(threadId)}/subagent-batches/${encodeURIComponent(batchId)}/resume`, "POST", {});
}

export async function cancelBatch(threadId: string, batchId: string): Promise<void> {
  await send(`/threads/${encodeURIComponent(threadId)}/subagent-batches/${encodeURIComponent(batchId)}/cancel`, "POST", {});
}

export async function retryBatchItem(threadId: string, batchId: string, itemId: string): Promise<void> {
  await send(
    `/threads/${encodeURIComponent(threadId)}/subagent-batches/${encodeURIComponent(batchId)}/items/${encodeURIComponent(itemId)}/retry`,
    "POST",
    {}
  );
}
