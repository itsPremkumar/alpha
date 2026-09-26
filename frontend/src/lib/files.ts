import { get, send, asList, pick, GATEWAY_BASE } from "./http";
import { apiFetch, ApiClientError } from "./api-client";

export interface UploadedFile {
  name: string;
  size: number;
  type: string;
}

export async function listUploads(threadId: string): Promise<UploadedFile[]> {
  const data = await get<unknown>(`/threads/${encodeURIComponent(threadId)}/uploads/list`);
  const hasEnvelopeList =
    data &&
    typeof data === "object" &&
    ["files", "uploads", "data"].some((key) => Array.isArray((data as Record<string, unknown>)[key]));
  if (!Array.isArray(data) && !hasEnvelopeList) {
    throw new Error("The server returned an unreadable upload list.");
  }
  return asList(data, ["files", "uploads", "data"]).map((file) => ({
    name: String(pick(file, ["filename", "name", "path"], "")),
    size: Number(pick(file, ["size", "size_bytes"], 0)),
    type: String(pick(file, ["content_type", "type"], "")),
  }));
}

export async function uploadFiles(threadId: string, files: FileList | File[]): Promise<UploadedFile[]> {
  const form = new FormData();
  Array.from(files).forEach((f) => form.append("files", f));
  const res = await apiFetch(`/threads/${encodeURIComponent(threadId)}/uploads`, {
    method: "POST",
    body: form,
  });
  const data = await res.json().catch(() => { throw new ApiClientError("response"); });
  return asList(data, ["files", "uploads", "data"]).map((f) => ({
    name: String(pick(f, ["filename", "name", "path"], "")),
    size: Number(pick(f, ["size", "size_bytes"], 0)),
    type: String(pick(f, ["content_type", "type"], "")),
  }));
}

export async function deleteUpload(threadId: string, filename: string): Promise<void> {
  await send(`/threads/${encodeURIComponent(threadId)}/uploads/${encodeURIComponent(filename)}`, "DELETE");
}

export async function uploadLimits(threadId: string): Promise<Record<string, unknown> | null> {
  // Thread-scoped limits endpoint — requires a real thread id.
  try {
    return await get<Record<string, unknown>>(`/threads/${encodeURIComponent(threadId)}/uploads/limits`);
  } catch {
    return null;
  }
}

function artifactPath(threadId: string, path: string): string {
  const clean = path.replace(/^\/+/, "");
  return `/threads/${encodeURIComponent(threadId)}/artifacts/${clean.split("/").map(encodeURIComponent).join("/")}`;
}

/** Download URL for an artifact (opens in a new tab; active content forces download server-side). */
export function artifactUrl(threadId: string, path: string, download = false): string {
  return `${GATEWAY_BASE}${artifactPath(threadId, path)}${download ? "?download=true" : ""}`;
}

/** Fetch a text artifact for inline preview. Throws for binary/oversized content. */
export async function previewArtifact(threadId: string, path: string): Promise<string> {
  const res = await apiFetch(artifactPath(threadId, path));
  const blob = await res.blob().catch(() => { throw new ApiClientError("response"); });
  if (blob.size > 200_000) throw new Error("File is too large to preview — use download.");
  const type = blob.type || "";
  if (!type.startsWith("text/") && !type.includes("json") && !type.includes("markdown") && type !== "") {
    // Try decoding anyway for typical code/text without content-type
    if (!/\.(txt|md|json|yaml|yml|csv|log|py|ts|tsx|js|jsx|css|html|xml|sh)$/i.test(path)) {
      throw new Error("Binary file — use download instead of preview.");
    }
  }
  return await blob.text().catch(() => { throw new ApiClientError("response"); });
}

export async function saveArtifact(threadId: string, path: string, content: string, sha256: string): Promise<void> {
  const clean = path.replace(/^\/+/, "");
  await send(
    `/threads/${encodeURIComponent(threadId)}/artifacts/${clean.split("/").map(encodeURIComponent).join("/")}`,
    "PUT",
    { content, expected_sha256: sha256 }
  );
}
